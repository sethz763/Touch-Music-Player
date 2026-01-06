"""
Audio Decoder Process - Multiprocessing Version
Each cue gets its own independent decoder worker process.
This enables true parallel decoding on multiple CPU cores.
"""

from __future__ import annotations

import multiprocessing as mp
from dataclasses import dataclass
from typing import Dict, Optional
import queue
import time

import numpy as np
import av

from engine.commands import UpdateCueCommand

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
        arr = arr[np.newaxis, :]
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

def _decode_worker(cue_id: str, msg: DecodeStart, worker_q: mp.Queue, out_q: mp.Queue) -> None:
    """
    Independent decoder worker process. 
    Each worker handles ONE cue file, decoding in a tight loop.
    This allows multiple files to be decoded in parallel on different CPU cores.
    """
    try:
        # Open the file
        try:
            container = av.open(msg.file_path)
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if not stream:
                out_q.put(DecodeError(cue_id, msg.track_id, msg.file_path, "No audio stream"))
                return
        except Exception as e:
            out_q.put(DecodeError(cue_id, msg.track_id, msg.file_path, f"Failed to open: {e}"))
            return
        
        # Seek if needed
        discard_frames = 0
        if msg.in_frame > 0:
            try:
                seek_seconds = msg.in_frame / msg.target_sample_rate
                container.seek(int(seek_seconds / stream.time_base), stream=stream, any_frame=False, backward=True)
                discard_frames = msg.target_sample_rate // 100
            except Exception:
                pass
        
        # Main decode loop
        resampler = av.AudioResampler(format="fltp", rate=msg.target_sample_rate)
        packet_iter = container.demux(stream)
        decoded_frames = 0
        credit_frames = 0
        eof = False
        loop_count = 0
        stopping = False
        frame_iter = None
        is_loop_restart = False
        
        TARGET_CHUNK_SIZE = msg.block_frames * 32
        
        while not stopping:
            # Check for commands
            try:
                cmd = worker_q.get_nowait()
                if isinstance(cmd, DecodeStop):
                    stopping = True
                    break
                elif isinstance(cmd, BufferRequest) and cmd.cue_id == cue_id:
                    credit_frames += cmd.frames_needed
            except queue.Empty:
                pass
            
            # Decode if we have credit
            if credit_frames > 0 and not eof:
                chunks = []
                frames_out = 0
                
                try:
                    while frames_out < TARGET_CHUNK_SIZE and credit_frames > 0:
                        # Get next frame
                        if frame_iter is None:
                            packet = next(packet_iter, None)
                            if packet is None:
                                # EOF - loop or finish
                                if msg.loop_enabled:
                                    try:
                                        seek_ts = 0 if msg.in_frame == 0 else int((msg.in_frame / msg.target_sample_rate) / stream.time_base)
                                        container.seek(seek_ts, stream=stream, any_frame=False, backward=True)
                                        packet_iter = container.demux(stream)
                                        loop_count += 1
                                        is_loop_restart = True
                                        discard_frames = msg.target_sample_rate // 100 if msg.in_frame > 0 else 0
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
                        
                        # Resample
                        frame.pts = None
                        resampled = resampler.resample(frame)
                        if not resampled:
                            continue

                        reached_target = False
                        for out_frame in resampled:
                            pcm = _normalize_audio(out_frame.to_ndarray())
                            pcm = _ensure_channels(pcm, msg.target_channels)

                            # Discard frames after seek
                            if discard_frames > 0:
                                discard = min(discard_frames, pcm.shape[0])
                                pcm = pcm[discard:, :]
                                discard_frames -= discard

                            if pcm.size == 0:
                                continue

                            # Check boundary
                            if msg.out_frame is not None:
                                remaining = msg.out_frame - decoded_frames
                                if remaining <= 0:
                                    eof = True
                                    reached_target = True
                                    break
                                if pcm.shape[0] > remaining:
                                    pcm = pcm[:remaining, :]

                            if pcm.size == 0:
                                continue

                            decoded_frames += pcm.shape[0]
                            frames_out += pcm.shape[0]
                            credit_frames -= pcm.shape[0]
                            chunks.append(pcm)

                            if frames_out >= TARGET_CHUNK_SIZE or credit_frames <= 0:
                                reached_target = True
                                break

                        if reached_target:
                            break
                    
                    # Send chunk
                    if chunks:
                        out_q.put(DecodedChunk(
                            cue_id=cue_id,
                            track_id=msg.track_id,
                            pcm=np.concatenate(chunks, axis=0).astype(np.float32),
                            eof=eof,
                            is_loop_restart=is_loop_restart
                        ))
                        is_loop_restart = False
                
                except Exception as e:
                    out_q.put(DecodeError(cue_id, msg.track_id, msg.file_path, f"Decode error: {e}"))
                    break
            else:
                # No credit - small sleep
                time.sleep(0.001)
        
        # Cleanup
        try:
            container.close()
        except Exception:
            pass
    
    except Exception as e:
        out_q.put(DecodeError(cue_id, msg.track_id, msg.file_path, f"Worker crash: {e}"))

def decode_process_main(cmd_q: mp.Queue, out_q: mp.Queue, event_q: mp.Queue) -> None:
    """
    Main decoder coordinator process.
    Dispatches DecodeStart messages to spawn independent worker processes.
    Each worker handles one cue file independently.
    """
    # Track active worker processes
    workers: Dict[str, mp.Process] = {}
    worker_cmd_queues: Dict[str, mp.Queue] = {}
    
    running = True
    
    while running:
        # Drain commands
        first_cmd = True
        
        while True:
            try:
                msg = cmd_q.get(timeout=0.005) if first_cmd else cmd_q.get_nowait()
                first_cmd = False
            except queue.Empty:
                break
            
            if msg is None:
                running = False
                break
            
            if isinstance(msg, DecodeStart):
                # Spawn worker for this cue if not already running
                if msg.cue_id not in workers:
                    worker_q = mp.Queue()
                    worker_cmd_queues[msg.cue_id] = worker_q
                    
                    worker = mp.Process(
                        target=_decode_worker,
                        args=(msg.cue_id, msg, worker_q, out_q),
                        name=f"decode-{msg.cue_id[:8]}"
                    )
                    worker.daemon = True
                    worker.start()
                    workers[msg.cue_id] = worker
                    
                    # Send started event
                    event_q.put(("started", msg.cue_id, msg.track_id, msg.file_path, None))
            
            elif isinstance(msg, DecodeStop):
                if msg.cue_id in worker_cmd_queues:
                    try:
                        worker_cmd_queues[msg.cue_id].put_nowait(msg)
                    except Exception:
                        pass
            
            elif isinstance(msg, BufferRequest):
                if msg.cue_id in worker_cmd_queues:
                    try:
                        worker_cmd_queues[msg.cue_id].put_nowait(msg)
                    except Exception:
                        pass
        
        # Clean up finished workers
        finished = [cid for cid, w in workers.items() if not w.is_alive()]
        for cid in finished:
            workers.pop(cid, None)
            worker_cmd_queues.pop(cid, None)
        
        # Sleep to avoid busy-wait
        time.sleep(0.001 if workers else 0.01)
