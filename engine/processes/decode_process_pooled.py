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
import queue
import threading
import time
import os

import numpy as np
import av

from engine.commands import UpdateCueCommand


def _out_send(out_chan: object, msg: object, lock: threading.Lock | None) -> None:
    """Send a message to the engine.

    Supports either an mp.Queue-like object with .put(...) or a Pipe Connection
    with .send(...). If using a Connection and multiple decode threads are
    active, a lock must be provided to prevent interleaved writes.
    """
    if hasattr(out_chan, "put"):
        out_chan.put(msg)  # type: ignore[attr-defined]
        return
    # Assume multiprocessing.connection.Connection
    if lock is not None:
        with lock:
            out_chan.send(msg)  # type: ignore[attr-defined]
    else:
        out_chan.send(msg)  # type: ignore[attr-defined]

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
    # --- Decode heartbeat diagnostics (optional) ---
    # Monotonic timestamp (seconds) captured in decoder worker right before enqueue.
    decoder_produced_mono: float | None = None
    # Approx time spent decoding/resampling/building this chunk in the worker.
    decode_work_ms: float | None = None
    # Worker id that produced this chunk (pooled decoder only).
    worker_id: int | None = None
    # Monotonic timestamp (seconds) captured in audio_engine when the chunk is dequeued from decode_out_q.
    engine_received_mono: float | None = None
    # Monotonic timestamp (seconds) captured in audio_engine right before forwarding to output.
    engine_forwarded_mono: float | None = None

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
    pending_pcm: Optional[np.ndarray] = None
    
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
    
    # NOTE: Avoid any stdout/stderr I/O in decoder workers; it can block and stall decoding.
    
    # Active decode jobs: cue_id -> JobState
    active_jobs: Dict[str, _JobState] = {}

    # Import here to keep module import cost low in worker processes.
    try:
        from engine.commands import UpdateCueCommand  # type: ignore
    except Exception:
        UpdateCueCommand = None  # type: ignore
    
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
                # (no prints)
                
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
                    # (no prints)
                
                except Exception as e:
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
                    cleanup_job(cmd.cue_id)

            elif UpdateCueCommand is not None and isinstance(cmd, UpdateCueCommand):
                # Apply parameter updates to an active decode job.
                # IMPORTANT: loop_enabled is handled here (decode-side), not in output.
                cue_id = getattr(cmd, "cue_id", None)
                if cue_id in active_jobs:
                    job = active_jobs[cue_id]
                    try:
                        if getattr(cmd, "loop_enabled", None) is not None:
                            job.cmd.loop_enabled = bool(cmd.loop_enabled)
                        if getattr(cmd, "in_frame", None) is not None:
                            job.cmd.in_frame = int(cmd.in_frame)
                        # Allow clearing out_frame by passing None.
                        if hasattr(cmd, "out_frame"):
                            job.cmd.out_frame = cmd.out_frame
                    except Exception:
                        pass
            
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
                            decode_target = min(job.credit_frames, 4096)
                            pcm_chunks = []

                            work_start = time.monotonic()
                            
                            while frames_out < decode_target and job.credit_frames > 0:
                                # Drain any pending remainder first (from a previous slice) so we
                                # never drop samples when slicing to decode_target/credit.
                                if job.pending_pcm is not None and job.pending_pcm.size:
                                    remaining_needed = min(job.credit_frames, decode_target - frames_out)
                                    if remaining_needed <= 0:
                                        break

                                    if job.pending_pcm.shape[0] > remaining_needed:
                                        pcm = job.pending_pcm[:remaining_needed, :]
                                        job.pending_pcm = job.pending_pcm[remaining_needed:, :]
                                    else:
                                        pcm = job.pending_pcm
                                        job.pending_pcm = None

                                    job.decoded_frames += pcm.shape[0]
                                    frames_out += pcm.shape[0]
                                    job.credit_frames -= pcm.shape[0]
                                    pcm_chunks.append(pcm)
                                    continue

                                # Apply any pending per-cue updates promptly so loop toggles
                                # take effect before we decide to restart at EOF.
                                while True:
                                    try:
                                        pending_cmd = cmd_q.get_nowait()
                                    except queue.Empty:
                                        break
                                    try:
                                        if isinstance(pending_cmd, DecodeStop):
                                            cleanup_job(cue_id)
                                            job = None  # type: ignore
                                            break
                                        if UpdateCueCommand is not None and isinstance(pending_cmd, UpdateCueCommand):
                                            if getattr(pending_cmd, "cue_id", None) == job.cue_id:
                                                if getattr(pending_cmd, "loop_enabled", None) is not None:
                                                    job.cmd.loop_enabled = bool(pending_cmd.loop_enabled)
                                                if getattr(pending_cmd, "in_frame", None) is not None:
                                                    job.cmd.in_frame = int(pending_cmd.in_frame)
                                                if hasattr(pending_cmd, "out_frame"):
                                                    job.cmd.out_frame = pending_cmd.out_frame
                                            else:
                                                # Not for this job; push back into the queue for the main loop.
                                                cmd_q.put_nowait(pending_cmd)
                                                break
                                        else:
                                            # Not an update we handle here; push back.
                                            cmd_q.put_nowait(pending_cmd)
                                            break
                                    except Exception:
                                        pass
                                if job is None:
                                    break

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
                                                # Reset per-iteration position so out_frame trimming behaves correctly.
                                                job.decoded_frames = 0
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
                                if not resampled:
                                    continue

                                reached_target = False
                                for out_frame in resampled:
                                    pcm = _normalize_audio(out_frame.to_ndarray())
                                    pcm = _ensure_channels(pcm, job.cmd.target_channels)

                                    if job.discard_frames > 0:
                                        discard = min(job.discard_frames, pcm.shape[0])
                                        pcm = pcm[discard:, :]
                                        job.discard_frames -= discard

                                    if pcm.size == 0:
                                        continue

                                    if job.cmd.out_frame is not None:
                                        # out_frame is treated as an absolute frame index in the source.
                                        # decoded_frames tracks frames produced since in_frame.
                                        remaining = int(job.cmd.out_frame) - (int(job.cmd.in_frame) + int(job.decoded_frames))
                                        if remaining <= 0:
                                            job.eof = True
                                            reached_target = True
                                            break
                                        if pcm.shape[0] > remaining:
                                            pcm = pcm[:remaining, :]

                                    if pcm.size == 0:
                                        continue

                                    # Emit up to what's needed for this slice; carry remainder.
                                    remaining_needed = min(job.credit_frames, decode_target - frames_out)
                                    if remaining_needed <= 0:
                                        job.pending_pcm = pcm if pcm.size else job.pending_pcm
                                        reached_target = True
                                        break

                                    if pcm.shape[0] > remaining_needed:
                                        job.pending_pcm = pcm[remaining_needed:, :]
                                        pcm = pcm[:remaining_needed, :]

                                    if pcm.size == 0:
                                        continue

                                    job.decoded_frames += pcm.shape[0]
                                    frames_out += pcm.shape[0]
                                    job.credit_frames -= pcm.shape[0]
                                    pcm_chunks.append(pcm)

                                    # Send immediately when we reach decode_target
                                    if frames_out >= decode_target or (frames_out >= 4096 and not job.eof):
                                        reached_target = True
                                        break

                                if reached_target:
                                    break
                            
                            if pcm_chunks:
                                work_end = time.monotonic()
                                decode_work_ms = (work_end - work_start) * 1000.0
                                chunk_data = np.concatenate(pcm_chunks, axis=0).astype(np.float32)
                                if job.eof:
                                    # DEBUG: Uncomment for EOF events: print(f"[POOL-WORKER-{worker_id}] Cue {cue_id[:8]} FINAL: {chunk_data.shape[0]} frames, EOF=True")
                                    cleanup_job(cue_id)
                                # Removed chunk logging - causes stuttering with frequent small chunks
                                produced_mono = time.monotonic()
                                out_q.put(DecodedChunk(
                                    cue_id=cue_id,
                                    track_id=job.cmd.track_id,
                                    pcm=chunk_data,
                                    eof=job.eof,
                                    is_loop_restart=job.is_loop_restart,
                                    decoder_produced_mono=produced_mono,
                                    decode_work_ms=decode_work_ms,
                                    worker_id=worker_id,
                                ))
                                job.is_loop_restart = False
                        
                        except Exception as e:
                            out_q.put(DecodeError(cue_id, job.cmd.track_id, job.cmd.file_path, str(e)))
                            cleanup_job(cue_id)
                    else:
                        # All active jobs are EOF or no credit - sleep briefly
                        time.sleep(0.001)
                        
                        # Clean up any jobs that reached EOF
                        to_remove = [cid for cid, job in active_jobs.items() if job.eof and job.credit_frames == 0]
                        for cid in to_remove:
                            cleanup_job(cid)
                else:
                    # No active jobs - sleep briefly
                    time.sleep(0.001)
    
    except Exception as e:
        # Don't print from workers; report via out_q.
        try:
            out_q.put(DecodeError("", "", "", f"decode_worker_crash: {type(e).__name__}: {e}"))
        except Exception:
            pass
    finally:
        # Clean up all jobs
        for cue_id in list(active_jobs.keys()):
            cleanup_job(cue_id)
        pass


def _decode_worker_thread(
    worker_id: int,
    start_cmd: DecodeStart,
    cmd_q: "queue.Queue[object]",
    out_q: mp.Queue,
    event_q: mp.Queue,
    decode_sema: threading.Semaphore,
    out_lock: threading.Lock | None,
) -> None:
    """Decode a single cue on a dedicated thread.

    PyAV is not generally thread-safe across shared objects; this design keeps one
    container/stream/resampler confined to a single thread.
    """
    cue_id = start_cmd.cue_id

    container = None
    try:
        container = av.open(start_cmd.file_path)
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if not stream:
            _out_send(out_q, DecodeError(cue_id, start_cmd.track_id, start_cmd.file_path, "No audio stream"), out_lock)
            return

        discard_frames = 0
        if start_cmd.in_frame > 0:
            try:
                seek_seconds = start_cmd.in_frame / start_cmd.target_sample_rate
                container.seek(
                    int(seek_seconds / stream.time_base),
                    stream=stream,
                    any_frame=False,
                    backward=True,
                )
                discard_frames = start_cmd.target_sample_rate // 100
            except Exception:
                pass

        resampler = av.AudioResampler(format="fltp", rate=start_cmd.target_sample_rate)
        packet_iter = container.demux(stream)
        frame_iter = None

        decoded_frames = 0
        credit_frames = 0
        eof = False
        is_loop_restart = False
        pending_pcm: np.ndarray | None = None

        # Larger chunks reduce IPC/message overhead under high concurrency.
        # Keep configurable so we can tune fairness vs overhead.
        default_chunk_frames = max(4096, int(start_cmd.block_frames) * 16)  # e.g. 1024*16=16384
        try:
            chunk_frames = int(os.environ.get("STEPD_DECODE_CHUNK_FRAMES", str(default_chunk_frames)).strip())
        except Exception:
            chunk_frames = default_chunk_frames
        chunk_frames = max(1024, int(chunk_frames))
        stopping = False

        try:
            event_q.put(("started", cue_id, start_cmd.track_id, start_cmd.file_path, None))
        except Exception:
            pass

        while not stopping:
            # Drain any pending commands quickly (non-blocking).
            while True:
                try:
                    msg = cmd_q.get_nowait()
                except queue.Empty:
                    break

                if isinstance(msg, DecodeStop):
                    stopping = True
                    break
                if isinstance(msg, UpdateCueCommand) and msg.cue_id == cue_id:
                    try:
                        if msg.loop_enabled is not None:
                            start_cmd.loop_enabled = bool(msg.loop_enabled)
                        if msg.in_frame is not None:
                            start_cmd.in_frame = int(msg.in_frame)
                        # Allow clearing out_frame by passing None.
                        start_cmd.out_frame = msg.out_frame
                    except Exception:
                        pass
                if isinstance(msg, BufferRequest) and msg.cue_id == cue_id:
                    credit_frames += int(msg.frames_needed)

            # If we previously hit EOF (often due to prebuffering the whole cue while looping was OFF)
            # and looping is turned ON while the cue is still active, resume by seeking and clearing EOF.
            if eof and start_cmd.loop_enabled:
                try:
                    seek_ts = 0
                    if start_cmd.in_frame != 0:
                        seek_ts = int(
                            (start_cmd.in_frame / start_cmd.target_sample_rate) / stream.time_base
                        )
                    container.seek(seek_ts, stream=stream, any_frame=False, backward=True)
                    packet_iter = container.demux(stream)
                    frame_iter = None
                    discard_frames = (
                        start_cmd.target_sample_rate // 100 if start_cmd.in_frame > 0 else 0
                    )
                    decoded_frames = 0
                    eof = False
                    is_loop_restart = True
                except Exception:
                    # If we can't restart, stay EOF.
                    pass

            if stopping:
                break

            if credit_frames <= 0 or eof:
                time.sleep(0.001)
                continue

            # Avoid having dozens of cue threads decode at once.
            # This keeps per-cue container/thread isolation, but limits active decode work.
            acquired = decode_sema.acquire(timeout=0.01)
            if not acquired:
                time.sleep(0.0005)
                continue

            frames_out = 0
            decode_target = min(credit_frames, chunk_frames)
            pcm_chunks: list[np.ndarray] = []
            work_start = time.monotonic()

            try:
                while frames_out < decode_target and credit_frames > 0:
                    # Drain any pending remainder first so we never drop samples when slicing.
                    if pending_pcm is not None and pending_pcm.size:
                        remaining_needed = min(credit_frames, decode_target - frames_out)
                        if remaining_needed <= 0:
                            break

                        if pending_pcm.shape[0] > remaining_needed:
                            pcm = pending_pcm[:remaining_needed, :]
                            pending_pcm = pending_pcm[remaining_needed:, :]
                        else:
                            pcm = pending_pcm
                            pending_pcm = None

                        decoded_frames += pcm.shape[0]
                        frames_out += pcm.shape[0]
                        credit_frames -= pcm.shape[0]
                        pcm_chunks.append(pcm)
                        continue

                    # Apply pending updates promptly so loop toggles take effect before EOF handling.
                    while True:
                        try:
                            pending = cmd_q.get_nowait()
                        except queue.Empty:
                            break
                        try:
                            if isinstance(pending, DecodeStop):
                                stopping = True
                                break
                            if isinstance(pending, UpdateCueCommand) and pending.cue_id == cue_id:
                                if pending.loop_enabled is not None:
                                    start_cmd.loop_enabled = bool(pending.loop_enabled)
                                if pending.in_frame is not None:
                                    start_cmd.in_frame = int(pending.in_frame)
                                start_cmd.out_frame = pending.out_frame
                            elif isinstance(pending, BufferRequest) and pending.cue_id == cue_id:
                                credit_frames += int(pending.frames_needed)
                            else:
                                # Not ours; re-queue and stop draining to avoid starving others.
                                cmd_q.put_nowait(pending)
                                break
                        except Exception:
                            pass
                    if stopping:
                        break

                    if frame_iter is None:
                        packet = next(packet_iter, None)
                        if packet is None:
                            if start_cmd.loop_enabled:
                                try:
                                    seek_ts = 0
                                    if start_cmd.in_frame != 0:
                                        seek_ts = int(
                                            (start_cmd.in_frame / start_cmd.target_sample_rate)
                                            / stream.time_base
                                        )
                                    container.seek(
                                        seek_ts,
                                        stream=stream,
                                        any_frame=False,
                                        backward=True,
                                    )
                                    packet_iter = container.demux(stream)
                                    is_loop_restart = True
                                    # Reset per-iteration position so out_frame trimming behaves correctly.
                                    decoded_frames = 0
                                    discard_frames = (
                                        start_cmd.target_sample_rate // 100
                                        if start_cmd.in_frame > 0
                                        else 0
                                    )
                                    packet = next(packet_iter, None)
                                    if packet is None:
                                        eof = True
                                        break
                                except Exception:
                                    eof = True
                                    break
                            else:
                                eof = True
                                break
                        frame_iter = iter(packet.decode())

                    frame = next(frame_iter, None)
                    if frame is None:
                        frame_iter = None
                        continue

                    frame.pts = None
                    resampled = resampler.resample(frame)
                    if not resampled:
                        continue

                    reached_target = False
                    for out_frame in resampled:
                        pcm = _normalize_audio(out_frame.to_ndarray())
                        pcm = _ensure_channels(pcm, start_cmd.target_channels)

                        if discard_frames > 0:
                            discard = min(discard_frames, pcm.shape[0])
                            pcm = pcm[discard:, :]
                            discard_frames -= discard

                        if pcm.size == 0:
                            continue

                        if start_cmd.out_frame is not None:
                            # out_frame is treated as an absolute frame index in the source.
                            # decoded_frames tracks frames produced since in_frame.
                            remaining = int(start_cmd.out_frame) - (int(start_cmd.in_frame) + int(decoded_frames))
                            if remaining <= 0:
                                eof = True
                                reached_target = True
                                break
                            if pcm.shape[0] > remaining:
                                pcm = pcm[:remaining, :]

                        if pcm.size == 0:
                            continue

                        remaining_needed = min(credit_frames, decode_target - frames_out)
                        if remaining_needed <= 0:
                            pending_pcm = pcm
                            reached_target = True
                            break

                        if pcm.shape[0] > remaining_needed:
                            pending_pcm = pcm[remaining_needed:, :]
                            pcm = pcm[:remaining_needed, :]

                        if pcm.size == 0:
                            continue

                        decoded_frames += pcm.shape[0]
                        frames_out += pcm.shape[0]
                        credit_frames -= pcm.shape[0]
                        pcm_chunks.append(pcm)

                        # Send one chunk per slice.
                        if frames_out >= decode_target:
                            reached_target = True
                            break

                    if reached_target:
                        break

                if pcm_chunks:
                    work_end = time.monotonic()
                    decode_work_ms = (work_end - work_start) * 1000.0
                    chunk_data = np.concatenate(pcm_chunks, axis=0).astype(np.float32, copy=False)
                    produced_mono = time.monotonic()
                    _out_send(
                        out_q,
                        DecodedChunk(
                            cue_id=cue_id,
                            track_id=start_cmd.track_id,
                            pcm=chunk_data,
                            eof=eof,
                            is_loop_restart=is_loop_restart,
                            decoder_produced_mono=produced_mono,
                            decode_work_ms=decode_work_ms,
                            worker_id=worker_id,
                        ),
                        out_lock,
                    )
                    is_loop_restart = False
            except Exception as e:
                _out_send(out_q, DecodeError(cue_id, start_cmd.track_id, start_cmd.file_path, f"Decode error: {e}"), out_lock)
                break
            finally:
                try:
                    decode_sema.release()
                except Exception:
                    pass

    except Exception as e:
        try:
            _out_send(out_q, DecodeError(cue_id, start_cmd.track_id, start_cmd.file_path, f"Worker crash: {e}"), out_lock)
        except Exception:
            pass
    finally:
        if container is not None:
            try:
                container.close()
            except Exception:
                pass



def decode_process_main(cmd_q: mp.Queue, out_q: mp.Queue, event_q: mp.Queue) -> None:
    """Decoder coordinator (thread-per-container).

    Spawns one dedicated decode thread per cue; each thread owns its own PyAV
    container/stream/resampler, which avoids unsafe sharing and eliminates
    multi-cue interleaving inside a single worker.
    """
    threads: Dict[str, threading.Thread] = {}
    thread_queues: Dict[str, "queue.Queue[object]"] = {}
    cue_cmd_map: Dict[str, DecodeStart] = {}
    cue_worker_id: Dict[str, int] = {}
    next_worker_id = 0

    cpu_count = os.cpu_count() or 4
    # Conservative cap to avoid Python/GIL thrash while still allowing parallelism.
    # Can be overridden for A/B testing via env var STEPD_MAX_ACTIVE_DECODERS.
    max_active_decoders = max(1, min(4, cpu_count))
    try:
        override = int(os.environ.get("STEPD_MAX_ACTIVE_DECODERS", "").strip() or "0")
        if override > 0:
            max_active_decoders = override
    except Exception:
        pass
    decode_sema = threading.Semaphore(max_active_decoders)

    # If out_q is a Pipe Connection shared by multiple threads, protect sends.
    out_lock: threading.Lock | None = None
    if not hasattr(out_q, "put") and hasattr(out_q, "send"):
        out_lock = threading.Lock()

    running = True

    def _start_or_restart_thread(cmd: DecodeStart) -> None:
        nonlocal next_worker_id
        cue_id = cmd.cue_id

        # Stop existing thread if present.
        prev_q = thread_queues.get(cue_id)
        prev_t = threads.get(cue_id)
        if prev_q is not None:
            try:
                prev_q.put_nowait(DecodeStop(cue_id=cue_id))
            except Exception:
                pass
        if prev_t is not None and prev_t.is_alive():
            prev_t.join(timeout=0.25)

        q: "queue.Queue[object]" = queue.Queue()
        worker_id = next_worker_id
        next_worker_id += 1
        cue_worker_id[cue_id] = worker_id
        cue_cmd_map[cue_id] = cmd

        t = threading.Thread(
            target=_decode_worker_thread,
            args=(worker_id, cmd, q, out_q, event_q, decode_sema, out_lock),
            name=f"decode-thread-{cue_id[:8]}",
            daemon=True,
        )
        thread_queues[cue_id] = q
        threads[cue_id] = t
        t.start()

    while running:
        # Drain commands from cmd_q.
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
                _start_or_restart_thread(msg)

            elif isinstance(msg, BufferRequest):
                q = thread_queues.get(msg.cue_id)
                if q is not None:
                    try:
                        q.put_nowait(msg)
                    except Exception:
                        # If the per-cue queue is wedged, surface as diag.
                        try:
                            event_q.put((
                                "diag",
                                {
                                    "type": "decode_cmd_queue_full",
                                    "cue": msg.cue_id[:8],
                                    "ts": time.time(),
                                },
                            ))
                        except Exception:
                            pass

            elif isinstance(msg, DecodeStop):
                cue_id = msg.cue_id
                q = thread_queues.get(cue_id)
                t = threads.get(cue_id)
                if q is not None:
                    try:
                        q.put_nowait(msg)
                    except Exception:
                        pass
                if t is not None and t.is_alive():
                    t.join(timeout=0.25)
                thread_queues.pop(cue_id, None)
                threads.pop(cue_id, None)
                cue_cmd_map.pop(cue_id, None)
                cue_worker_id.pop(cue_id, None)

            elif isinstance(msg, UpdateCueCommand):
                # Forward updates to the per-cue decode thread so it can change loop behavior
                # immediately (critical for disabling looping mid-playback).
                cue_id = msg.cue_id
                q = thread_queues.get(cue_id)
                if q is not None:
                    try:
                        q.put_nowait(msg)
                    except Exception:
                        pass
                # Keep the last-known parameters so if the thread restarts, it restarts with
                # the updated loop/in/out values.
                cmd = cue_cmd_map.get(cue_id)
                if cmd is not None:
                    try:
                        if msg.loop_enabled is not None:
                            cmd.loop_enabled = bool(msg.loop_enabled)
                        if msg.in_frame is not None:
                            cmd.in_frame = int(msg.in_frame)
                        # Allow clearing out_frame.
                        cmd.out_frame = msg.out_frame
                    except Exception:
                        pass

        # Health check / restart if a decode thread died unexpectedly.
        for cue_id, t in list(threads.items()):
            if t.is_alive():
                continue
            cmd = cue_cmd_map.get(cue_id)
            if cmd is None:
                threads.pop(cue_id, None)
                thread_queues.pop(cue_id, None)
                cue_worker_id.pop(cue_id, None)
                continue
            try:
                event_q.put((
                    "diag",
                    {
                        "type": "decode_thread_restart",
                        "cue": cue_id[:8],
                        "worker_id": cue_worker_id.get(cue_id),
                        "ts": time.time(),
                    },
                ))
            except Exception:
                pass
            _start_or_restart_thread(cmd)

        time.sleep(0.001)

    # Shutdown: ask all threads to stop.
    for cue_id, q in list(thread_queues.items()):
        try:
            q.put_nowait(DecodeStop(cue_id=cue_id))
        except Exception:
            pass
    for t in list(threads.values()):
        try:
            if t.is_alive():
                t.join(timeout=1.0)
        except Exception:
            pass
