"""
EXPERIMENTAL: Dedicated decoder worker process
Each cue gets its own decoder worker process running in parallel.
This enables true multicore decoding instead of single-threaded round-robin.
"""

import multiprocessing as mp
from dataclasses import dataclass
from typing import Optional
import queue
import time

import numpy as np
import av

# Reuse message types from main decoder
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

def decode_worker(cue_id: str, cmd_q: mp.Queue, out_q: mp.Queue):
    """
    Single-purpose decoder worker. Runs in its own process.
    Decodes one cue file in a tight loop, sending frames as they become available.
    """
    try:
        msg: Optional[DecodeStart] = None
        container = None
        stream = None
        packet_iter = None
        frame_iter = None
        decoded_frames = 0
        credit_frames = 0
        eof = False
        stopping = False
        loop_count = 0
        loop_seeked = False
        
        running = True
        while running:
            # Check for commands
            try:
                cmd = cmd_q.get_nowait()
                if isinstance(cmd, DecodeStart):
                    msg = cmd
                    # Open file
                    try:
                        container = av.open(msg.file_path)
                        stream = next((s for s in container.streams if s.type == "audio"), None)
                        if not stream:
                            out_q.put(DecodeError(cue_id, msg.track_id, msg.file_path, "No audio stream"))
                            running = False
                            continue
                        
                        # Seek if needed
                        if msg.in_frame > 0:
                            seek_seconds = msg.in_frame / msg.target_sample_rate
                            container.seek(int(seek_seconds / stream.time_base), stream=stream, any_frame=False, backward=True)
                        
                        packet_iter = container.demux(stream)
                        decoded_frames = 0
                        credit_frames = 0
                        eof = False
                        loop_seeked = False
                        loop_count = 0
                    except Exception as e:
                        out_q.put(DecodeError(cue_id, msg.track_id, msg.file_path, str(e)))
                        running = False
                
                elif isinstance(cmd, DecodeStop):
                    stopping = True
                    running = False
                elif isinstance(cmd, tuple) and cmd[0] == "credit":
                    # BufferRequest
                    credit_frames += cmd[1]
            except queue.Empty:
                pass
            
            # Decode if we have credit and aren't at EOF
            if msg and credit_frames > 0 and not eof and not stopping:
                try:
                    # Decode up to TARGET_CHUNK_SIZE before sending
                    TARGET_CHUNK_SIZE = msg.block_frames * 16
                    chunks = []
                    frames_out = 0
                    
                    while frames_out < TARGET_CHUNK_SIZE and credit_frames > 0:
                        # Get next frame
                        if frame_iter is None:
                            # Get next packet
                            packet = next(packet_iter, None)
                            if packet is None:
                                # EOF on this iteration
                                if msg.loop_enabled:
                                    # Loop: seek back to start
                                    try:
                                        container.seek(0 if msg.in_frame == 0 else int((msg.in_frame / msg.target_sample_rate) / stream.time_base), 
                                                      stream=stream, any_frame=False, backward=True)
                                        packet_iter = container.demux(stream)
                                        loop_count += 1
                                        loop_seeked = True
                                        packet = next(packet_iter, None)
                                        if packet is None:
                                            eof = True
                                            break
                                    except:
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
                        
                        # Convert frame to PCM
                        pcm = frame.to_ndarray()
                        if pcm.ndim == 1:
                            pcm = pcm[np.newaxis, :]
                        pcm = pcm.T.astype(np.float32)  # shape: (frames, channels)
                        
                        # Check boundary
                        if msg.out_frame is not None:
                            remaining = msg.out_frame - decoded_frames
                            if remaining <= 0:
                                eof = True
                                break
                            if pcm.shape[0] > remaining:
                                pcm = pcm[:remaining, :]
                        
                        decoded_frames += pcm.shape[0]
                        frames_out += pcm.shape[0]
                        credit_frames -= pcm.shape[0]
                        chunks.append(pcm)
                    
                    # Send accumulated chunk
                    if chunks:
                        out_q.put(DecodedChunk(
                            cue_id=cue_id,
                            track_id=msg.track_id,
                            pcm=np.concatenate(chunks, axis=0).astype(np.float32),
                            eof=eof,
                            is_loop_restart=loop_seeked
                        ))
                        loop_seeked = False
                
                except Exception as e:
                    out_q.put(DecodeError(cue_id, msg.track_id, msg.file_path, str(e)))
                    eof = True
            
            elif not msg:
                # Waiting for DecodeStart
                time.sleep(0.001)
        
        # Cleanup
        if container:
            container.close()
    
    except Exception as e:
        if msg:
            out_q.put(DecodeError(cue_id, msg.track_id, msg.file_path, f"Worker crash: {e}"))
        else:
            print(f"[WORKER-{cue_id}] Unexpected error: {e}")
