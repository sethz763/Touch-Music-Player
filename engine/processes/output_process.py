from __future__ import annotations

import multiprocessing as mp
from dataclasses import dataclass
from typing import Dict
from collections import deque
import time

import numpy as np
import math
from engine.processes.decode_process_pooled import DecodedChunk
from engine.processes.decode_process_pooled import BufferRequest, DecodeError, DecodeStop
from engine.commands import OutputFadeTo, OutputSetDevice, OutputSetConfig, OutputListDevices, UpdateCueCommand


#these events need to bu used to send events back to audio_service 
from engine.messages.events import (
    CueStartedEvent,
    CueFinishedEvent,
    CueLevelsEvent,
    CueTimeEvent,
    MasterLevelsEvent,
    BatchCueLevelsEvent,
    BatchCueTimeEvent,
    DecodeErrorEvent,
    TransportStateEvent,
)

LOW_WATER_MULT = 4
BLOCK_MULT = 8

@dataclass(frozen=True, slots=True)
class OutputStartCue:
    cue_id: str
    track_id: str
    gain_db: float
    fade_in_duration_ms: int = 0
    fade_in_curve: str = "equal_power"
    target_gain_db: float = 0.0
    loop_enabled: bool = False
    is_loop_restart: bool = False  # True if this is a loop restart (skip fade-in, don't emit finish event)
    
class _FadeEnv:
    def __init__(self, start, target, frames, curve):
        self.start = start
        self.target = target
        self.frames_left = max(1, frames)
        self.total = self.frames_left
        self.curve = curve

    def next_gain(self):
        t = 1.0 - (self.frames_left / self.total)
        # interpolation factor in [0,1]
        if self.curve == "equal_power":
            s = math.sin(t * math.pi / 2)
        else:
            s = t

        # map from start..target using factor s
        g = self.start + s * (self.target - self.start)

        self.frames_left -= 1
        return g
    
    def compute_batch_gains(self, num_frames):
        """Pre-compute all gains for a chunk at once (vectorized with NumPy)"""
        if num_frames == 0:
            return np.array([], dtype=np.float32)
        
        # Compute all frame indices efficiently
        frame_indices = np.arange(num_frames, dtype=np.float32)
        t = 1.0 - ((self.frames_left - frame_indices) / self.total)
        t = np.clip(t, 0.0, 1.0)
        
        # Apply curve
        if self.curve == "equal_power":
            s = np.sin(t * np.pi / 2)
        else:
            s = t
        
        # Linear interpolation: start + s * (target - start)
        gains = self.start + s * (self.target - self.start)
        
        # Update frames_left
        self.frames_left -= num_frames
        
        return gains.astype(np.float32)


@dataclass(frozen=True, slots=True)
class OutputStopCue:
    cue_id: str

@dataclass(frozen=True, slots=True)
class OutputConfig:
    sample_rate: int
    channels: int
    block_frames: int

def _db_to_lin(db: float) -> float:
    return float(10.0 ** (db / 20.0))

class _Ring:
    def __init__(self):
        self.q = deque()
        self.frames = 0
        self.eof = False
        self.request_pending = False
        self.request_started_at = None  # timestamp when current buffer request was made
        self.last_pcm_time = None  # timestamp when last PCM was pushed to this ring
        self.finished_pending = False  # set by callback when cue is done; main loop emits event

    def push(self, a: np.ndarray, eof: bool):
        if a.size:
            self.q.append(a)
            self.frames += a.shape[0]
        if eof:
            self.eof = True

    def pull(self, n: int, channels: int):
        out = np.zeros((n, channels), dtype=np.float32)
        filled = 0
        while filled < n and self.q:
            a = self.q[0]
            take = min(n - filled, a.shape[0])
            out[filled:filled+take] = a[:take]
            if take == a.shape[0]:
                self.q.popleft()
            else:
                self.q[0] = a[take:]
            self.frames -= take
            filled += take
        done = (filled == 0 and self.eof and self.frames == 0 and not self.q)

        return out, done, filled

def output_process_main(cfg: OutputConfig, cmd_q: mp.Queue, pcm_q: mp.Queue, event_q: mp.Queue, decode_cmd_q:mp.Queue) -> None:
    import sounddevice as sd

    rings: Dict[str, _Ring] = {}
    gains: Dict[str, float] = {}
    envelopes: Dict[str, _FadeEnv] = {}
    pending_starts: Dict[str, OutputStartCue] = {}
    # tracking per-cue consumed sample counts for elapsed time reporting
    cue_samples_consumed: Dict[str, int] = {}
    # Track which cues are looping to suppress finish event on loop restart
    looping_cues: set[str] = set()
    # Track removal reasons for debug logging: {cue_id: reason_str}
    removal_reasons: Dict[str, str] = {}
    
    # Open file logger for ticking diagnosis
    debug_log_file = None
    try:
        debug_log_file = open("output_process_debug.log", "w", buffering=1)
    except Exception:
        pass

    def _log(msg: str) -> None:
        # Debug logging: send via non-blocking queue AND to file
        # This keeps _log safe for use outside callback while never blocking
        import time as time_module
        ts = time_module.time()
        try:
            event_q.put_nowait(("debug", f"[{ts:.3f}] {msg}"))
        except Exception:
            pass
        # Also log to file for analysis
        try:
            if debug_log_file:
                debug_log_file.write(f"[{ts:.3f}] {msg}\n")
        except Exception:
            pass

    def _drain_pcm(max_items: int = 256) -> None:
        """Drain decoded PCM chunks from pcm_q into per-cue rings.
        We drain every loop to avoid backpressure that can stall decode."""
        drained = 0
        while drained < max_items:
            try:
                pcm = pcm_q.get_nowait()
            except Exception:
                break
            # count every pulled item so we don't spin if errors are frequent
            drained += 1
            if isinstance(pcm, DecodeError):
                ring = rings.get(pcm.cue_id)
                _log(f"DecodeError for cue={pcm.cue_id} error={pcm.error}")
                removal_reasons[pcm.cue_id] = f"decode_error: {pcm.error}"
                if ring:
                    try:
                        ring.eof = True
                        ring.q.clear()
                        ring.frames = 0
                        ring.request_pending = False
                        ring.request_started_at = None
                    except Exception as ex:
                        _log(f"EXCEPTION clearing ring for error cue={pcm.cue_id}: {type(ex).__name__}: {ex}")
                continue
            try:
                # If there's a pending OutputStartCue for this cue, activate it now
                pending = pending_starts.pop(pcm.cue_id, None)
                if pending:
                    _log(f"[DRAIN-ACTIVATE] cue={pcm.cue_id[:8]} first PCM, gain={pending.gain_db}")
                    ring = rings.setdefault(pcm.cue_id, _Ring())
                    gains[pcm.cue_id] = _db_to_lin(pending.gain_db)
                    
                    # Track looping cues
                    if pending.loop_enabled:
                        looping_cues.add(pcm.cue_id)
                    
                    # Apply bundled fade-in atomically on first PCM (but NOT on loop restart)
                    if pending.fade_in_duration_ms > 0 and not pending.is_loop_restart:
                        try:
                            cur = _db_to_lin(pending.gain_db)
                            target = _db_to_lin(pending.target_gain_db)
                            fade_frames = int(cfg.sample_rate * pending.fade_in_duration_ms / 1000)
                            envelopes[pcm.cue_id] = _FadeEnv(cur, target, fade_frames, pending.fade_in_curve)
                            _log(f"[DRAIN-FADE-IN] cue={pcm.cue_id[:8]} fade_in={fade_frames}fr")
                        except Exception as ex:
                            _log(f"[DRAIN-ACTIVATE-ERROR] cue={pcm.cue_id[:8]}: {type(ex).__name__}")
                # Reset elapsed time on loop restart
                if pcm.is_loop_restart:
                    cue_samples_consumed[pcm.cue_id] = 0
                ring = rings.get(pcm.cue_id)
                if ring is None:
                    ring = _Ring()
                    rings[pcm.cue_id] = ring
                    _log(f"[DRAIN-CREATE-RING] cue={pcm.cue_id[:8]} created new ring")
                frames_in_chunk = pcm.pcm.shape[0]
                _log(f"[DECODE-CHUNK] cue={pcm.cue_id[:8]} received {frames_in_chunk} frames, eof={pcm.eof}")
                ring.push(pcm.pcm, pcm.eof)
                # PCM received, buffered in ring
                # arrival of PCM clears any outstanding request state
                ring.request_pending = False
                ring.request_started_at = None
                ring.last_pcm_time = time.time()  # record when PCM arrived
                if frames_in_chunk > 0:
                    old_frames = ring.frames - frames_in_chunk  # what it was before push
                    # CRITICAL: Log if buffer had dropped dangerously low before this chunk arrived
                    if old_frames < 2048:  # Less than ~42ms of audio at 48kHz
                        _log(f"[BUFFER-STARVING] cue={pcm.cue_id[:8]} CRITICAL: buffer was at {old_frames}fr before receiving {frames_in_chunk}fr chunk! This may cause ticking.")
                    _log(f"[DRAIN-PCM-PUSH] cue={pcm.cue_id[:8]} frames={frames_in_chunk} total={ring.frames} eof={pcm.eof} ring.eof={ring.eof}")
            except Exception as ex:
                _log(f"[DRAIN-EXCEPTION] cue={pcm.cue_id[:8]}: {type(ex).__name__}")

    def callback(outdata, frames, time, status):
        # STRICTLY REAL-TIME SAFE:
        # - No blocking IPC (only put_nowait for telemetry, silently drop on full)
        # - No logging or prints
        # - No exception handling with side effects
        # - Only audio mixing and state updates
        # - Set finished_pending flag; main loop handles event emission
        
        try:
            # Telemetry status: non-blocking, may be dropped silently
            if status:
                try:
                    event_q.put_nowait(("status", str(status)))
                except Exception:
                    pass
            
            mix = np.zeros((frames, cfg.channels), dtype=np.float32)
            active_envelopes = len(envelopes)  # Check concurrency level
            skip_telemetry = active_envelopes > 6  # Skip telemetry during bulk fades to reduce CPU load
            
            # Accumulate telemetry for batching - send once per callback instead of per-cue
            batch_levels = {}  # {cue_id: (rms, peak), ...}
            batch_times = {}   # {cue_id: (elapsed, remaining), ...}
            
            # Initialize per-channel cache if not present
            if not hasattr(callback, '_batch_levels_per_channel'):
                callback._batch_levels_per_channel = {}
            
            for cue_id, ring in list(rings.items()):
                try:
                    chunk, done, filled = ring.pull(frames, cfg.channels)
                    if done:
                        _log(f"[CALLBACK-DONE] cue={cue_id[:8]} done=True filled={filled} eof={ring.eof} frames={ring.frames}")
                    
                    # CRITICAL: Log if we had to return a partial buffer (which causes silence padding)
                    if filled < frames and not done:
                        _log(f"[CALLBACK-PARTIAL] cue={cue_id[:8]} CRITICAL: requested {frames}fr but only got {filled}fr! Padding with {frames-filled}fr of silence - THIS CAUSES TICKING")
                    
                    env = envelopes.get(cue_id)
                    if env:
                        # Use batch gain computation when multiple envelopes are active (>=3)
                        # Batch mode is much faster for multiple concurrent fades
                        if active_envelopes >= 3:
                            # Batch mode: compute all gains at once (vectorized)
                            batch_gains = env.compute_batch_gains(chunk.shape[0])
                            # Vectorized gain application: chunk *= gains (per-channel broadcast)
                            chunk *= batch_gains[:, None]
                            gains[cue_id] = batch_gains[-1] if len(batch_gains) > 0 else env.target
                            if env.frames_left <= 0:
                                gains[cue_id] = env.target
                                envelopes.pop(cue_id, None)
                                if env.target == 0.0:
                                    _log(f"[ENVELOPE-SILENCE] cue={cue_id[:8]} envelope finished to silence - batch mode")
                                    ring.finished_pending = True
                                    ring.request_pending = False
                                    # Request decoder stop; let EOF naturally propagate when all buffered frames consumed
                                    try:
                                        decode_cmd_q.put_nowait(DecodeStop(cue_id=cue_id))
                                    except Exception:
                                        pass
                        else:
                            # Per-sample mode: original behavior for low concurrency
                            for i in range(chunk.shape[0]):
                                g = env.next_gain()
                                chunk[i] *= g
                                gains[cue_id] = g
                                if env.frames_left <= 1000:
                                    gains[cue_id] = env.target
                                    envelopes.pop(cue_id, None)
                                    if env.target == 0.0:
                                        _log(f"[ENVELOPE-SILENCE] cue={cue_id[:8]} envelope finished to silence - per sample mode")
                                        ring.finished_pending = True
                                        ring.request_pending = False
                                        # Request decoder stop; let EOF naturally propagate when all buffered frames consumed
                                        try:
                                            decode_cmd_q.put_nowait(DecodeStop(cue_id=cue_id))
                                        except Exception:
                                            pass
                                    for j in range(i+1, chunk.shape[0]):
                                        chunk[j] *= env.target
                                    break
                    else:
                        gain_val = gains.get(cue_id, 1.0)
                        chunk *= gain_val
                    
                    mix += chunk

                    # Telemetry: accumulate for batching - send one batch message per callback
                    # Skip entirely during bulk fades (>6 concurrent envelopes) to prevent event queue congestion
                    if filled > 0:
                        cue_samples_consumed[cue_id] = cue_samples_consumed.get(cue_id, 0) + filled
                        elapsed_seconds = cue_samples_consumed[cue_id] / float(cfg.sample_rate)
                        remaining_seconds = ring.frames / float(cfg.sample_rate) if cfg.sample_rate > 0 else 0.0
                        
                        # Accumulate time for this cue
                        batch_times[cue_id] = (elapsed_seconds, remaining_seconds)
                        
                        if not skip_telemetry:
                            # Normal case: accumulate level data for this cue (both overall and per-channel)
                            segment = chunk[:filled]
                            try:
                                rms = float(np.sqrt(np.mean(np.square(segment))))
                            except Exception:
                                rms = 0.0
                            try:
                                peak = float(np.max(np.abs(segment)))
                            except Exception:
                                peak = 0.0
                            batch_levels[cue_id] = (rms, peak)
                            
                            # Calculate per-channel levels for each audio channel
                            rms_per_channel = []
                            peak_per_channel = []
                            for ch in range(cfg.channels):
                                try:
                                    ch_data = segment[:, ch]
                                    ch_rms = float(np.sqrt(np.mean(np.square(ch_data))))
                                    ch_peak = float(np.max(np.abs(ch_data)))
                                    rms_per_channel.append(ch_rms)
                                    peak_per_channel.append(ch_peak)
                                except Exception:
                                    rms_per_channel.append(0.0)
                                    peak_per_channel.append(0.0)
                            
                            # Store per-channel levels for batching
                            callback._batch_levels_per_channel[cue_id] = (rms_per_channel, peak_per_channel)
                        else:
                            # During bulk fade: send zero levels to indicate we're not computing meters
                            batch_levels[cue_id] = (-64.0, -64.0)
                    
                    # Mark cue finished pending; main loop will emit event reliably
                    if done:
                        _log(f"[CALLBACK-DONE] cue={cue_id[:8]} done=True (filled={filled} eof={ring.eof} frames={ring.frames})")
                        ring.finished_pending = True
                        # Clean up envelope and gains when cue finishes
                        envelopes.pop(cue_id, None)
                        gains.pop(cue_id, None)
                except Exception:
                    pass
            
            # Send batched telemetry events (one per callback cycle instead of N per cue)
            try:
                if batch_levels:
                    # Include per-channel levels if available
                    batch_levels_per_ch = getattr(callback, '_batch_levels_per_channel', {})
                    event_q.put_nowait(BatchCueLevelsEvent(
                        cue_levels=batch_levels,
                        cue_levels_per_channel=batch_levels_per_ch if batch_levels_per_ch else None
                    ))
                    # Clear per-channel cache for next cycle
                    callback._batch_levels_per_channel = {}
            except Exception:
                pass
            try:
                if batch_times:
                    event_q.put_nowait(BatchCueTimeEvent(cue_times=batch_times))
            except Exception:
                pass
            
            np.clip(mix, -1.0, 1.0, out=mix)
            outdata[:] = mix
            
            # Calculate and emit master output levels (per-channel RMS and peak)
            try:
                master_rms_per_channel = []
                master_peak_per_channel = []
                for ch in range(cfg.channels):
                    channel_data = mix[:, ch]
                    rms = float(np.sqrt(np.mean(np.square(channel_data))))
                    peak = float(np.max(np.abs(channel_data)))
                    master_rms_per_channel.append(rms)
                    master_peak_per_channel.append(peak)
                
                # Convert linear RMS/peak to dB (avoid log(0))
                master_rms_db = []
                master_peak_db = []
                for rms, peak in zip(master_rms_per_channel, master_peak_per_channel):
                    rms_db = 20 * np.log10(rms) if rms > 0 else -120.0
                    peak_db = 20 * np.log10(peak) if peak > 0 else -120.0
                    master_rms_db.append(float(rms_db))
                    master_peak_db.append(float(peak_db))
                
                # Send per-channel master levels
                event_q.put_nowait(MasterLevelsEvent(rms=master_rms_db, peak=master_peak_db))
            except Exception:
                pass
        except Exception:
            pass

    # Create/open output stream with ability to re-open on device/config change
    stream = None

    def open_stream(device=None):
        nonlocal stream, cfg
        try:
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
        except NameError:
            pass
        try:
            stream = sd.OutputStream(
                samplerate=cfg.sample_rate,
                channels=cfg.channels,
                dtype="float32",
                blocksize=cfg.block_frames,
                callback=callback,
                device=device,
            )
            stream.start()
            _log(f"Opened output stream device={device} sr={cfg.sample_rate} ch={cfg.channels} block={cfg.block_frames}")
        except Exception as ex:
            _log(f"EXCEPTION opening output stream device={device}: {type(ex).__name__}: {ex}")

    # Open initial stream with default device
    open_stream()

    try:
        while True:
            try:
                msg = cmd_q.get(timeout=0.01)
            except Exception:
                msg = None
            _drain_pcm()
            
            # Emit finished events reliably (outside RT callback)
            # Detect finished_pending flag set by callback and emit event with blocking put
            for cue_id, ring in list(rings.items()):
                if ring.finished_pending:
                    # Ring has truly finished (eof set and all frames consumed)
                    try:
                        # Note: CueFinishedEvent requires cue_info which we don't have in output process.
                        # Send as tuple; audio_service will convert to proper CueFinishedEvent
                        removal_reason = removal_reasons.pop(cue_id, "eof_natural")
                        _log(f"[FINISHED] cue={cue_id[:8]} removal_reason={removal_reason}")
                        event_q.put(("finished", cue_id, removal_reason))
                        rings.pop(cue_id, None)
                        gains.pop(cue_id, None)
                        cue_samples_consumed.pop(cue_id, None)
                        looping_cues.discard(cue_id)
                    except Exception:
                        pass
            
            # -------------------------------------------------
            # Buffer threshold check (OUTSIDE callback)
            # -------------------------------------------------
            # Use fixed, generous buffers for all cues to prevent starvation
            # Simpler and more stable than dynamic sizing
            active_rings = len([r for r in rings.values() if not r.eof and r.frames >= 0])
            
            # Fixed buffer sizing for stable concurrent playback
            # CRITICAL: PyAV file decoding is slow and I/O bound, with gaps up to 6+ seconds
            # between chunks. We need MASSIVE buffers to hide these decode delays.
            low_water = cfg.block_frames * 48       # ~1000ms (was 12)
            block_frames = cfg.block_frames * 96    # Request ~2000ms per refill (was 24)
            
            current_time = time.time()
            stuck_timeout_secs = 30.0  # 30s timeout for stuck pending cues (very long to handle high concurrency without prematurely timing out active playback)

            for cue_id, ring in list(rings.items()):
                try:
                    assert not ring.eof or ring.frames >= 0
                    
                    # Check if any fade envelopes have completed to silence
                    # (this must happen even if the cue has 0 frames and isn't being processed by callback)
                    env = envelopes.get(cue_id)
                    if env and env.frames_left <= 0 and env.target == 0.0:
                        # Envelope completed to silence - mark finished immediately
                        _log(f"[MAIN-LOOP-ENVELOPE-COMPLETE] cue={cue_id} fade complete, marking finished_pending")
                        removal_reasons[cue_id] = "fade_complete"  # Track fade completion as removal reason
                        ring.finished_pending = True
                        ring.request_pending = False
                        envelopes.pop(cue_id, None)
                        # Tell decoder to stop processing this cue (non-blocking, may fail silently)
                        try:
                            decode_cmd_q.put_nowait(DecodeStop(cue_id=cue_id))
                        except Exception:
                            pass
                    
                    # Timeout-based cleanup: if a cue has been pending for >2s WITHOUT receiving any PCM,
                    # mark it EOF. This prevents truly stuck cues from blocking playback forever.
                    # CRITICAL: Only timeout if we've ALREADY received PCM (last_pcm_time is not None).
                    # If we haven't received ANY PCM yet, it means the decoder is still working on it -
                    # don't timeout immediately as it may take time for the first chunk to arrive.
                    if ring.request_pending and ring.frames == 0 and ring.last_pcm_time is not None:
                        if ring.request_started_at is not None:
                            time_pending = current_time - ring.request_started_at
                            pcm_age = current_time - ring.last_pcm_time
                            if time_pending > stuck_timeout_secs and pcm_age > stuck_timeout_secs:
                                _log(f"[TIMEOUT-CLEANUP] cue={cue_id[:8]} timeout: pending {time_pending:.3f}s, last_pcm {pcm_age:.3f}s ago")
                                removal_reasons[cue_id] = "timeout_stuck_decode"  # Track timeout as removal reason
                                ring.eof = True
                                ring.request_pending = False
                                ring.request_started_at = None
                                envelopes.pop(cue_id, None)
                                continue
                    
                    if ring.eof:
                        continue

                    if ring.frames < low_water and not ring.request_pending:
                        # Request more frames: either up to block_frames, or higher for high concurrency
                        target_frames = block_frames
                        if active_rings > 8:
                            # High concurrency: request larger chunks (12 blocks instead of 4)
                            # This reduces decoder scheduling pressure
                            target_frames = cfg.block_frames * 12
                        needed = target_frames - ring.frames
                        if needed > 0:
                            try:
                                decode_cmd_q.put_nowait(BufferRequest(cue_id, needed))
                                ring.request_pending = True
                                ring.request_started_at = current_time
                            except Exception as ex:
                                pass
                except AssertionError as ex:
                    pass
                except Exception as ex:
                    pass


            if msg is False:
                break

            if msg is None:
                try:
                    pcm = pcm_q.get_nowait()
                except Exception:
                    pcm = None
                if isinstance(pcm, DecodedChunk):
                    ring = rings.get(pcm.cue_id)
                    if ring is None:
                        ring = _Ring()
                        rings[pcm.cue_id] = ring
                    ring.push(pcm.pcm, pcm.eof)
                continue

            # Log all non-None messages
            if msg is not None:
                _log(f"[OUTPUT-PROCESS-MSG] Received message type={type(msg).__name__}")
            
            if isinstance(msg, OutputStartCue):
                try:
                    existing_ring = rings.get(msg.cue_id)
                    if existing_ring:
                        _log(f"[START-CUE-REUSE] Ring exists for cue={msg.cue_id[:8]} eof={existing_ring.eof} frames={existing_ring.frames} finished={existing_ring.finished_pending}")
                    
                    ring = rings.setdefault(msg.cue_id, _Ring())
                    _log(f"[START-CUE] cue={msg.cue_id[:8]} is_new={not existing_ring} fade_in={msg.fade_in_duration_ms}")
                    
                    # If this is a loop restart, clear the old envelope and ring EOF flag
                    if msg.is_loop_restart:
                        _log(f"[START-CUE-LOOP-RESTART] cue={msg.cue_id[:8]}")
                        envelopes.pop(msg.cue_id, None)
                        ring.eof = False
                        ring.frames = 0
                        ring.q.clear()
                        ring.last_pcm_time = None
                        ring.request_pending = False
                        ring.request_started_at = None
                        ring.finished_pending = False
                    else:
                        # For a NEW cue (not loop restart), the ring should be brand new
                        if ring.eof or ring.frames > 0 or ring.finished_pending:
                            _log(f"[START-CUE-STALE] cue={msg.cue_id[:8]} WARNING: eof={ring.eof} frames={ring.frames} finished={ring.finished_pending}")
                            ring.eof = False
                            ring.frames = 0
                            ring.q.clear()
                            ring.finished_pending = False
                            ring.last_pcm_time = None
                            ring.request_pending = False
                            ring.request_started_at = None
                    
                    try:
                        # Request MASSIVE initial buffer to handle slow I/O from file decoding
                        # Initial request: 96 blocks (~2000ms) to give decoder time to warm up
                        # This is critical because PyAV file I/O can have 6+ second gaps
                        initial_needed = cfg.block_frames * 96
                        decode_cmd_q.put_nowait(BufferRequest(msg.cue_id, initial_needed))
                        ring.request_pending = True
                        ring.request_started_at = current_time
                        pending_starts[msg.cue_id] = msg
                        _log(f"[START-CUE-BUFFER] cue={msg.cue_id[:8]} BufferRequest sent for {initial_needed} frames (~2000ms)")
                    except Exception as ex:
                        _log(f"[START-CUE-ERROR] cue={msg.cue_id[:8]}: {type(ex).__name__}")
                except Exception as ex:
                    _log(f"[START-CUE-EXCEPTION] cue={msg.cue_id[:8]}: {type(ex).__name__}: {ex}")
            elif isinstance(msg, OutputStopCue):
                try:
                    ring = rings.get(msg.cue_id)
                    if ring:
                        _log(f"[STOP-CUE] cue={msg.cue_id[:8]} BEFORE: eof={ring.eof} frames={ring.frames} finished_pending={ring.finished_pending}")
                        ring.eof = True
                        ring.q.clear()
                        ring.frames = 0
                        ring.request_pending = False  # clear pending flag when eof is set
                        ring.request_started_at = None
                        _log(f"[STOP-CUE] cue={msg.cue_id[:8]} AFTER: eof={ring.eof} cleared and marked EOF")
                    else:
                        _log(f"[STOP-CUE] cue={msg.cue_id[:8]} RING_NOT_FOUND (ring doesn't exist yet)")
                    # Remove from looping cues so finish event will be emitted (not suppressed)
                    looping_cues.discard(msg.cue_id)
                except Exception as ex:
                    _log(f"[STOP-CUE-EXCEPTION] cue={msg.cue_id[:8]}: {type(ex).__name__}: {ex}")
                    
            elif isinstance(msg, OutputFadeTo):
                try:
                    ring = rings.get(msg.cue_id)
                    
                    # If ring doesn't exist, the cue was already removed/finished
                    if ring is None:
                        _log(f"[OUTPUT-FADE-NO-RING] cue={msg.cue_id[:8]} ring not found (already finished?)")
                    # If ring is already marked as finished, don't interfere
                    elif ring.finished_pending:
                        _log(f"[OUTPUT-FADE-FINISHED] cue={msg.cue_id[:8]} ring marked finished_pending, ignoring fade")
                    # If ring is at EOF, check if it can complete a fade
                    elif ring.eof:
                        # Ring is EOF from decoder. Mark finished immediately without a hanging fade.
                        # This prevents creating fade envelopes that can never complete naturally.
                        _log(f"[OUTPUT-FADE-EOF] cue={msg.cue_id[:8]} ring is EOF, marking finished_pending")
                        ring.finished_pending = True
                        envelopes.pop(msg.cue_id, None)
                    else:
                        # Normal case: ring is active, create fade envelope
                        cur = gains.get(msg.cue_id, 1.0)
                        # Reduce verbose logging for batch operations - only log at info level
                        # print(f"[OUTPUT-FADE-START] cue={msg.cue_id[:8]} cur={cur:.3f} target_db={msg.target_db} duration_ms={msg.duration_ms} ring_frames={ring.frames}")
                        _log(f"[OUTPUT-FADE-START] cue={msg.cue_id[:8]} target_db={msg.target_db} duration_ms={msg.duration_ms} current_gain={cur}")
                        
                        # treat very-small target_db (e.g. -120dB) as silence
                        if msg.target_db <= -120.0:
                            target = 0.0
                        else:
                            target = _db_to_lin(msg.target_db)
                        fade_frames = int(cfg.sample_rate * msg.duration_ms / 1000)
                        envelopes[msg.cue_id] = _FadeEnv(cur, target, fade_frames, msg.curve)
                        _log(f"FadeTo created: cue={msg.cue_id} cur={cur} target={target} frames={fade_frames} curve={msg.curve}")
                except Exception as ex:
                    _log(f"EXCEPTION in OutputFadeTo handler for cue={msg.cue_id}: {type(ex).__name__}: {ex}")
            
            elif isinstance(msg, UpdateCueCommand):
                try:
                    # Update cue properties while playing
                    if msg.gain_db is not None:
                        # Update gain immediately
                        target = _db_to_lin(msg.gain_db)
                        old_gain = gains.get(msg.cue_id, 1.0)
                        gains[msg.cue_id] = target
                        # Remove any active envelope so static gain takes effect
                        removed_env = envelopes.pop(msg.cue_id, None)
                        _log(f"[OUTPUT-UPDATE-CUE] cue={msg.cue_id} NEW_gain_db={msg.gain_db} linear={target:.6f} (from {old_gain:.6f}), removed_envelope={removed_env is not None}")
                    # Note: in_frame, out_frame, loop_enabled are handled in decode_process
                except Exception as ex:
                    _log(f"EXCEPTION in UpdateCueCommand handler for cue={msg.cue_id}: {type(ex).__name__}: {ex}")
            
            elif isinstance(msg, OutputSetDevice):
                try:
                    _log(f"OutputSetDevice received: {msg.device}")
                    open_stream(device=msg.device)
                    try:
                        event_q.put_nowait(("device_changed", msg.device))
                    except Exception:
                        pass
                except Exception as ex:
                    _log(f"EXCEPTION in OutputSetDevice handler: {type(ex).__name__}: {ex}")
            elif isinstance(msg, OutputSetConfig):
                try:
                    _log(f"OutputSetConfig received: sr={msg.sample_rate} ch={msg.channels} block={msg.block_frames}")
                    # update cfg and reopen stream
                    cfg = OutputConfig(sample_rate=msg.sample_rate, channels=msg.channels, block_frames=msg.block_frames)
                    open_stream()
                    try:
                        event_q.put_nowait(("config_changed", {"sample_rate": msg.sample_rate, "channels": msg.channels, "block_frames": msg.block_frames}))
                    except Exception:
                        pass
                except Exception as ex:
                    _log(f"EXCEPTION in OutputSetConfig handler: {type(ex).__name__}: {ex}")
            elif isinstance(msg, OutputListDevices):
                try:
                    _log("OutputListDevices received")
                    try:
                        devs = sd.query_devices()
                    except Exception:
                        devs = []
                    try:
                        event_q.put_nowait(("devices", devs))
                    except Exception:
                        pass
                except Exception as ex:
                    _log(f"EXCEPTION in OutputListDevices handler: {type(ex).__name__}: {ex}")
    finally:
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass
