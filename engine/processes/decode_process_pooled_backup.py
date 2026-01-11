"""
Audio decoder using a fixed worker pool for efficient concurrent decoding.
Instead of spawning one process per cue, maintains a pool of N workers
that process decode jobs sequentially. This is more efficient for high
concurrency (many cues across few CPU cores).
"""
from __future__ import annotations

import multiprocessing as mp
from dataclasses import dataclass
from typing import Dict, Optional
import queue as queue_module
import time
import os

import numpy as np
import av

@dataclass(slots=True)
class DecodeStart:
    cue_id: str
    track_id: str
    file_path: str
    in_frame: int
    out_frame: Optional[int]
    gain_db: float
    loop_enabled: bool
    target_sample_rate: int
    target_channels: int
    block_frames: int

@dataclass(frozen=True, slots=True)
class DecodeStop:
    cue_id: str

@dataclass(frozen=True, slots=True)
class DecodedChunk:
    cue_id: str
    track_id: str
    pcm: np.ndarray
    eof: bool
    is_loop_restart: bool = False

@dataclass(frozen=True, slots=True)
class DecodeError:
    cue_id: str
    track_id: str
    file_path: str
    error: str
    
@dataclass(frozen=True, slots=True)
class BufferRequest:
    cue_id: str
    frames_needed: int

def _normalize_audio(arr: np.ndarray) -> np.ndarray:
    if arr.ndim == 1:
        arr = arr[None, :]
    if arr.shape[0] <= 8 and arr.shape[0] < arr.shape[-1]:
        arr = arr.T
    if arr.dtype == np.float32:
        out = arr
    elif np.issubdtype(arr.dtype, np.floating):
        out = arr.astype(np.float32, copy=False)
    elif np.issubdtype(arr.dtype, np.signedinteger):
        info = np.iinfo(arr.dtype)
        out = (arr.astype(np.float32) / max(abs(info.min), info.max)).astype(np.float32, copy=False)
    else:
        out = arr.astype(np.float32)
    return out

def _ensure_channels(pcm: np.ndarray, target_channels: int) -> np.ndarray:
    frames, ch = pcm.shape
    if ch == target_channels:
        return pcm
    if ch > target_channels:
        return pcm[:, :target_channels]
    pad = np.zeros((frames, target_channels - ch), dtype=np.float32)
    return np.concatenate([pcm, pad], axis=1)

@dataclass
class _JobState:
    """State for a single decode job within a worker"""
    cmd: DecodeStart
    container: Optional[object] = None
    stream: Optional[object] = None
    resampler: Optional[object] = None
    packet_iter: Optional[object] = None
    frame_iter: Optional[object] = None
    
    decoded_frames: int = 0
    credit_frames: int = 0
    eof: bool = False
    loop_count: int = 0
    discard_frames: int = 0
    is_loop_restart: bool = False

def _decode_worker_pool(worker_id: int, cmd_q: mp.Queue, out_q: mp.Queue) -> None:
    """
    Fixed worker pool process. Handles multiple decode jobs concurrently.
    Each job has its own decoder state and is interleaved during decoding.
    """
    import queue as queue_module
    
    # DEBUG: print disabled to prevent I/O blocking
    # print(f"[POOL-WORKER-{worker_id}] Started")
    
    # Active decode jobs: cue_id -> JobState
    active_jobs: Dict[str, _JobState] = {}
    
    def cleanup_job(cue_id: str) -> None:
        """Clean up a specific job"""
        if cue_id not in active_jobs:
            return
        job = active_jobs[cue_id]
        if job.container:
            try:
                job.container.close()
            except Exception:
                pass
        active_jobs.pop(cue_id, None)
    
    try:
        last_job_idx = 0  # For round-robin through active jobs
        
        while True:
            # Get command (non-blocking, check often)
            try:
                cmd = cmd_q.get_nowait()
            except queue_module.Empty:
                cmd = None
            
            # Handle command
            if isinstance(cmd, DecodeStart):
                # Add new job without killing existing ones
                cue_id = cmd.cue_id
                print(f"[POOL-WORKER-{worker_id}] Starting job for cue {cue_id[:8]} in_frame={cmd.in_frame} out_frame={cmd.out_frame} (total active jobs: {len(active_jobs) + 1})")
                
                try:
                    container = av.open(cmd.file_path)
                    stream = next((s for s in container.streams if s.type == "audio"), None)
                    if not stream:
                        out_q.put(DecodeError(cmd.cue_id, cmd.track_id, cmd.file_path, "No audio stream"))
                        continue
                    
                    # Seek if needed
                    discard_frames = 0
                    if cmd.in_frame > 0:
                        try:
                            seek_seconds = cmd.in_frame / cmd.target_sample_rate
                            container.seek(int(seek_seconds / stream.time_base), stream=stream, any_frame=False, backward=True)
                            discard_frames = cmd.target_sample_rate // 100
                        except Exception:
                            pass
                    
                    resampler = av.AudioResampler(format="fltp", rate=cmd.target_sample_rate)
                    packet_iter = container.demux(stream)
                    
                    # Create job state
                    job = _JobState(
                        cmd=cmd,
                        container=container,
                        stream=stream,
                        resampler=resampler,
                        packet_iter=packet_iter,
                        frame_iter=None,
                        decoded_frames=0,
                        credit_frames=0,
                        eof=False,
                        loop_count=0,
                        discard_frames=discard_frames,
                        is_loop_restart=False
                    )
                    active_jobs[cue_id] = job
                    print(f"[POOL-WORKER-{worker_id}] Job {cue_id[:8]} opened, ready for decode")
                
                except Exception as e:
                    print(f"[POOL-WORKER-{worker_id}] Error opening {cue_id[:8]}: {e}")
                    out_q.put(DecodeError(cmd.cue_id, cmd.track_id, cmd.file_path, str(e)))
            
            elif isinstance(cmd, BufferRequest):
                # Credit the specific job
                if cmd.cue_id in active_jobs:
                    job = active_jobs[cmd.cue_id]
                    job.credit_frames += cmd.frames_needed
                    # DEBUG: Uncomment for buffer requests: print(f"[POOL-WORKER-{worker_id}] BufferRequest for {cmd.cue_id[:8]}: +{cmd.frames_needed} frames, total={job.credit_frames}")
            
            elif isinstance(cmd, DecodeStop):
                # Remove the specific job
                if cmd.cue_id in active_jobs:
                    print(f"[POOL-WORKER-{worker_id}] Stopping job {cmd.cue_id[:8]}")
                    cleanup_job(cmd.cue_id)
            
            elif cmd is None:
                # No command: decode from active jobs in round-robin fashion
                if active_jobs:
                    # Get list of jobs with available credit and not EOF
                    jobs_to_decode = [
                        (cid, job) for cid, job in active_jobs.items()
                        if job.credit_frames > 0 and not job.eof
                    ]
                    
                    if jobs_to_decode:
                        # Pick next job in round-robin
                        last_job_idx = last_job_idx % len(jobs_to_decode)
                        cue_id, job = jobs_to_decode[last_job_idx]
                        last_job_idx += 1
                        
                        # Decode a chunk for this job
                        try:
                            frames_out = 0
                            try:
                                slice_max_frames = int(os.environ.get("STEPD_DECODE_SLICE_MAX_FRAMES", "4096").strip() or "4096")
                            except Exception:
                                slice_max_frames = 4096
                            slice_max_frames = max(256, int(slice_max_frames))
                            decode_target = min(job.credit_frames, slice_max_frames)
                            pcm_chunks = []
                            
                            while frames_out < decode_target and job.credit_frames > 0:
                                if job.frame_iter is None:
                                    packet = next(job.packet_iter, None)
                                    if packet is None:
                                        if job.cmd.loop_enabled:
                                            try:
                                                seek_ts = 0 if job.cmd.in_frame == 0 else int((job.cmd.in_frame / job.cmd.target_sample_rate) / job.stream.time_base)
                                                job.container.seek(seek_ts, stream=job.stream, any_frame=False, backward=True)
                                                job.packet_iter = job.container.demux(job.stream)
                                                job.loop_count += 1
                                                job.is_loop_restart = True
                                                job.discard_frames = job.cmd.target_sample_rate // 100 if job.cmd.in_frame > 0 else 0
                                                packet = next(job.packet_iter, None)
                                                if packet is None:
                                                    job.eof = True
                                                    break
                                            except Exception:
                                                job.eof = True
                                                break
                                        else:
                                            job.eof = True
                                            break
                                    job.frame_iter = iter(packet.decode())
                                
                                frame = next(job.frame_iter, None)
                                if frame is None:
                                    job.frame_iter = None
                                    continue
                                
                                frame.pts = None
                                resampled = job.resampler.resample(frame)
                                if resampled:
                                    pcm = _normalize_audio(resampled[0].to_ndarray())
                                    pcm = _ensure_channels(pcm, job.cmd.target_channels)
                                    
                                    if job.discard_frames > 0:
                                        discard = min(job.discard_frames, pcm.shape[0])
                                        pcm = pcm[discard:, :]
                                        job.discard_frames -= discard
                                    
                                    if pcm.size == 0:
                                        continue
                                    
                                    if job.cmd.out_frame is not None:
                                        remaining = job.cmd.out_frame - job.decoded_frames
                                        if remaining <= 0:
                                            print(f"[POOL-WORKER-{worker_id}] Reached out_frame boundary for cue {cue_id[:8]}: decoded_frames={job.decoded_frames} out_frame={job.cmd.out_frame}")
                                            job.eof = True
                                            break
                                        if pcm.shape[0] > remaining:
                                            print(f"[POOL-WORKER-{worker_id}] Trimming PCM at out_frame for cue {cue_id[:8]}: {pcm.shape[0]} -> {remaining} frames")
                                            pcm = pcm[:remaining, :]
                                    
                                    # Cap PCM to not exceed remaining credit
                                    if pcm.shape[0] > job.credit_frames:
                                        pcm = pcm[:job.credit_frames, :]
                                    
                                    if pcm.size == 0:
                                        continue
                                    
                                    job.decoded_frames += pcm.shape[0]
                                    frames_out += pcm.shape[0]
                                    job.credit_frames -= pcm.shape[0]
                                    pcm_chunks.append(pcm)
                                    
                                    # Send immediately when we reach decode_target
                                    if frames_out >= decode_target:
                                        break
                            
                            if pcm_chunks:
                                chunk_data = np.concatenate(pcm_chunks, axis=0).astype(np.float32)
                                if job.eof:
                                    # DEBUG: Uncomment for EOF events: print(f"[POOL-WORKER-{worker_id}] Cue {cue_id[:8]} FINAL: {chunk_data.shape[0]} frames, EOF=True")
                                    cleanup_job(cue_id)
                                # Removed chunk logging - causes stuttering with frequent small chunks
                                out_q.put(DecodedChunk(
                                    cue_id=cue_id,
                                    track_id=job.cmd.track_id,
                                    pcm=chunk_data,
                                    eof=job.eof,
                                    is_loop_restart=job.is_loop_restart
                                ))
                                job.is_loop_restart = False
                        
                        except Exception as e:
                            print(f"[POOL-WORKER-{worker_id}] Decode error for {cue_id[:8]}: {e}")
                            out_q.put(DecodeError(cue_id, job.cmd.track_id, job.cmd.file_path, str(e)))
                            cleanup_job(cue_id)
                    else:
                        # All active jobs are EOF or no credit - sleep briefly
                        time.sleep(0.001)
                        
                        # Clean up any jobs that reached EOF
                        to_remove = [cid for cid, job in active_jobs.items() if job.eof and job.credit_frames == 0]
                        for cid in to_remove:
                            print(f"[POOL-WORKER-{worker_id}] Cleaning up finished job {cid[:8]}")
                            cleanup_job(cid)
                else:
                    # No active jobs - sleep briefly
                    time.sleep(0.001)
    
    except Exception as e:
        print(f"[POOL-WORKER-{worker_id}] Worker crash: {e}")
    finally:
        # Clean up all jobs
        for cue_id in list(active_jobs.keys()):
            cleanup_job(cue_id)
        print(f"[POOL-WORKER-{worker_id}] Exiting")



def decode_process_main(cmd_q: mp.Queue, out_q: mp.Queue, event_q: mp.Queue) -> None:
    """
    Coordinator: Manages a fixed pool of decoder workers.
    Routes DecodeStart/BufferRequest/DecodeStop to appropriate workers.
    """
    # Determine pool size: 1-4 workers based on CPU cores, never more than cores
    cpu_count = os.cpu_count() or 4
    num_workers = min(4, max(1, cpu_count))
    
    print(f"[DECODE-COORD] Starting with {num_workers} worker pool (CPU cores: {cpu_count})")
    
    # Create worker queues and processes
    worker_queues: list[mp.Queue] = []
    workers: list[mp.Process] = []
    worker_cue_map: Dict[str, int] = {}  # Maps cue_id -> worker_index
    
    for i in range(num_workers):
        wq = mp.Queue()
        worker_queues.append(wq)
        
        worker = mp.Process(
            target=_decode_worker_pool,
            args=(i, wq, out_q),
            name=f"decode-pool-worker-{i}"
        )
        worker.start()
        workers.append(worker)
    
    # Main coordinator loop
    next_worker_idx = 0  # Round-robin assignment
    running = True
    
    while running:
        # Drain all pending commands
        first_cmd = True
        while True:
            try:
                msg = cmd_q.get(timeout=0.005) if first_cmd else cmd_q.get_nowait()
                first_cmd = False
            except queue_module.Empty:
                break
            
            if msg is None:
                running = False
                break
            
            if isinstance(msg, DecodeStart):
                # Assign to next worker (round-robin)
                worker_idx = next_worker_idx
                next_worker_idx = (next_worker_idx + 1) % num_workers
                
                print(f"[DECODE-COORD] DecodeStart cue {msg.cue_id[:8]} -> worker {worker_idx}")
                
                # Send job to worker
                worker_queues[worker_idx].put(msg)
                worker_cue_map[msg.cue_id] = worker_idx
                
                # Notify audio_service that decode started
                event_q.put(("started", msg.cue_id, msg.track_id, msg.file_path, None))
            
            elif isinstance(msg, BufferRequest):
                # Route to worker handling this cue
                worker_idx = worker_cue_map.get(msg.cue_id)
                if worker_idx is not None:
                    worker_queues[worker_idx].put(msg)
            
            elif isinstance(msg, DecodeStop):
                # Route to worker handling this cue
                worker_idx = worker_cue_map.get(msg.cue_id)
                if worker_idx is not None:
                    worker_queues[worker_idx].put(msg)
                    worker_cue_map.pop(msg.cue_id, None)
        
        # Check worker health
        for i, worker in enumerate(workers):
            if not worker.is_alive():
                print(f"[DECODE-COORD] Worker {i} died! Restarting...")
                # Restart the worker
                wq = mp.Queue()
                worker_queues[i] = wq
                
                new_worker = mp.Process(
                    target=_decode_worker_pool,
                    args=(i, wq, out_q),
                    name=f"decode-pool-worker-{i}"
                )
                new_worker.start()
                workers[i] = new_worker
        
        # Small sleep to prevent busy-wait
        time.sleep(0.001)
    
    # Shutdown: send None to all workers
    print("[DECODE-COORD] Shutting down worker pool...")
    for wq in worker_queues:
        try:
            wq.put(None)
        except Exception:
            pass
    
    # Wait for workers to exit
    for worker in workers:
        try:
            worker.join(timeout=2)
            if worker.is_alive():
                worker.terminate()
        except Exception:
            pass
    
    print("[DECODE-COORD] Coordinator exiting")
