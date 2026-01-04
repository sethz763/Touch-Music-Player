from __future__ import annotations

import multiprocessing as mp
from dataclasses import dataclass, replace
from typing import Dict
from collections import deque
import time
import os

import numpy as np
import math
from engine.processes.decode_process_pooled import DecodedChunk
from engine.processes.decode_process_pooled import BufferRequest, DecodeError, DecodeStop
from engine.commands import (
    OutputFadeTo,
    OutputSetDevice,
    OutputSetConfig,
    OutputListDevices,
    UpdateCueCommand,
    TransportPause,
    TransportPlay,
)


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
        # Store (pcm, is_loop_restart) so we can trim already-buffered loop iterations
        # at the exact loop boundary when looping is disabled.
        self.q: deque[tuple[np.ndarray, bool]] = deque()
        self.frames = 0
        self.eof = False
        self.request_pending = False
        self.request_started_at = None  # timestamp when current buffer request was made
        self.last_pcm_time = None  # timestamp when last PCM was pushed to this ring
        self.finished_pending = False  # set by callback when cue is done; main loop emits event
        # If True, treat the next loop-restart boundary as end-of-cue.
        # This lets us stop cleanly at the boundary without yanking already-buffered audio mid-stream.
        self.stop_on_restart_boundary = False
        # Diagnostics (keep callback RT-safe: counters only, no I/O)
        self.started = False  # set True on first PCM arrival
        self.underflow_count = 0
        self.underflow_missing_frames_total = 0
        self.last_underflow_missing_frames = 0
        self.partial_fill_count = 0
        self.partial_padded_frames_total = 0
        self.last_partial_padded_frames = 0

    def push(self, a: np.ndarray, eof: bool, *, is_loop_restart: bool = False):
        if a.size:
            self.q.append((a, bool(is_loop_restart)))
            self.frames += a.shape[0]
            self.started = True
        if eof:
            self.eof = True

    def drop_buffered_loop_restart_audio(self) -> bool:
        """Drop any already-buffered audio that belongs to a future loop iteration.

        Returns True if any audio was dropped.
        """
        if not self.q:
            return False

        dropped = False
        new_q: deque[tuple[np.ndarray, bool]] = deque()
        new_frames = 0
        saw_restart = False

        for pcm, is_restart in self.q:
            if saw_restart:
                dropped = True
                continue
            if is_restart:
                saw_restart = True
                dropped = True
                continue
            new_q.append((pcm, False))
            new_frames += int(pcm.shape[0])

        if dropped:
            self.q = new_q
            self.frames = new_frames
        return dropped

    def pull(self, n: int, channels: int):
        out = np.zeros((n, channels), dtype=np.float32)
        filled = 0
        while filled < n and self.q:
            # If loop disable was requested, stop cleanly at the loop boundary.
            # The first chunk of the next loop iteration is tagged is_restart=True.
            if self.stop_on_restart_boundary:
                try:
                    _, is_restart = self.q[0]
                    if is_restart:
                        # Do not play any samples from the next loop iteration.
                        self.q.clear()
                        self.frames = 0
                        self.eof = True
                        break
                except Exception:
                    pass
            a, is_restart = self.q[0]
            take = min(n - filled, a.shape[0])
            out[filled:filled+take] = a[:take]
            if take == a.shape[0]:
                self.q.popleft()
            else:
                # If this chunk marks a loop restart boundary, consuming any samples from it
                # means we've crossed the boundary. Clear the marker on the remainder so
                # stop_on_restart_boundary won't incorrectly stop mid-chunk.
                self.q[0] = (a[take:], False)
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
    # Cues that have had looping disabled mid-playback.
    # We use this to prevent any additional loop restarts from being enqueued/played.
    loop_stop_requested: set[str] = set()
    # Track removal reasons for debug logging: {cue_id: reason_str}
    removal_reasons: Dict[str, str] = {}
    telemetry_probe = {
        "status_sent": 0,
        "status_dropped": 0,
        "levels_sent": 0,
        "levels_dropped": 0,
        "times_sent": 0,
        "times_dropped": 0,
        "master_sent": 0,
        "master_dropped": 0,
    }
    lifecycle_probe = {
        "finished_sent": 0,
        "finished_failed": 0,
    }
    _probe_emit_interval = 0.5
    _last_probe_emit = time.time()
    _pending_probe_payload: str | None = None
    _last_starvation_report = time.time()
    _starvation_report_interval = 0.5
    _starvation_reported: Dict[str, tuple[int, int]] = {}  # cue_id -> (underflow_count, partial_fill_count)
    _last_heartbeat_report: Dict[str, float] = {}
    _last_chunk_produced_mono: Dict[str, float] = {}
    _heartbeat_report_interval = 0.5
    _last_hb_snapshot: Dict[str, dict] = {}

    def _format_hb_snapshot(cue_id: str) -> str:
        """Return a compact, single-line snapshot of latest heartbeat timings for this cue."""
        try:
            snap = _last_hb_snapshot.get(cue_id)
            if not snap:
                return ""
            parts: list[str] = []
            wid = snap.get("worker_id")
            if wid is not None:
                parts.append(f"wid={wid}")
            for key in (
                "decode_work_ms",
                "total_ms",
                "engine_hold_ms",
                "decode_to_engine_ms",
                "engine_internal_ms",
                "engine_to_output_ms",
            ):
                val = snap.get(key)
                if val is None:
                    continue
                parts.append(f"{key}={val}")
            return (" hb{" + " ".join(parts) + "}") if parts else ""
        except Exception:
            return ""

    def _maybe_log_decode_heartbeat(pcm: DecodedChunk, ring: _Ring) -> None:
        """Log per-cue decode/IPC timing breakdown (non-RT; called from _drain_pcm)."""
        try:
            now_mono = time.monotonic()
            cue_id = pcm.cue_id

            last_report = _last_heartbeat_report.get(cue_id, 0.0)
            if (now_mono - last_report) < _heartbeat_report_interval:
                return

            produced_mono = getattr(pcm, "decoder_produced_mono", None)
            received_mono = getattr(pcm, "engine_received_mono", None)
            forwarded_mono = getattr(pcm, "engine_forwarded_mono", None)
            decode_work_ms = getattr(pcm, "decode_work_ms", None)
            worker_id = getattr(pcm, "worker_id", None)

            # If we don't have heartbeat metadata, nothing to do.
            if produced_mono is None and forwarded_mono is None and decode_work_ms is None:
                return

            total_ms = (now_mono - produced_mono) * 1000.0 if produced_mono is not None else None
            engine_hold_ms = (
                (forwarded_mono - produced_mono) * 1000.0
                if (produced_mono is not None and forwarded_mono is not None)
                else None
            )
            decode_to_engine_ms = (
                (received_mono - produced_mono) * 1000.0
                if (produced_mono is not None and received_mono is not None)
                else None
            )
            engine_internal_ms = (
                (forwarded_mono - received_mono) * 1000.0
                if (received_mono is not None and forwarded_mono is not None)
                else None
            )
            engine_to_output_ms = (now_mono - forwarded_mono) * 1000.0 if forwarded_mono is not None else None

            # Keep a best-effort snapshot for correlation with starvation/tick logs.
            try:
                _last_hb_snapshot[cue_id] = {
                    "worker_id": worker_id,
                    "decode_work_ms": decode_work_ms,
                    "total_ms": total_ms,
                    "engine_hold_ms": engine_hold_ms,
                    "decode_to_engine_ms": decode_to_engine_ms,
                    "engine_internal_ms": engine_internal_ms,
                    "engine_to_output_ms": engine_to_output_ms,
                }
            except Exception:
                pass

            produced_gap_ms = None
            if produced_mono is not None:
                prev = _last_chunk_produced_mono.get(cue_id)
                _last_chunk_produced_mono[cue_id] = float(produced_mono)
                if prev is not None:
                    produced_gap_ms = (produced_mono - prev) * 1000.0

            # Only log when something looks suspicious, to avoid log spam.
            suspicious = False
            if decode_work_ms is not None and decode_work_ms > 50.0:
                suspicious = True
            if total_ms is not None and total_ms > 100.0:
                suspicious = True
            if engine_hold_ms is not None and engine_hold_ms > 50.0:
                suspicious = True
            if decode_to_engine_ms is not None and decode_to_engine_ms > 50.0:
                suspicious = True
            if engine_internal_ms is not None and engine_internal_ms > 20.0:
                suspicious = True
            if engine_to_output_ms is not None and engine_to_output_ms > 50.0:
                suspicious = True
            if produced_gap_ms is not None and produced_gap_ms > 200.0:
                suspicious = True
            if ring.frames < 2048:
                suspicious = True

            if not suspicious:
                return

            frames_in_chunk = 0
            try:
                frames_in_chunk = int(pcm.pcm.shape[0])
            except Exception:
                frames_in_chunk = 0

            _log(
                "[DECODE-HB] cue="
                f"{cue_id[:8]} wid={worker_id} frames={frames_in_chunk} ring_frames={ring.frames} "
                f"decode_work_ms={decode_work_ms if decode_work_ms is not None else 'NA'} "
                f"total_ms={total_ms if total_ms is not None else 'NA'} "
                f"engine_hold_ms={engine_hold_ms if engine_hold_ms is not None else 'NA'} "
                f"decode_to_engine_ms={decode_to_engine_ms if decode_to_engine_ms is not None else 'NA'} "
                f"engine_internal_ms={engine_internal_ms if engine_internal_ms is not None else 'NA'} "
                f"engine_to_output_ms={engine_to_output_ms if engine_to_output_ms is not None else 'NA'} "
                f"produced_gap_ms={produced_gap_ms if produced_gap_ms is not None else 'NA'}"
            )

            _last_heartbeat_report[cue_id] = now_mono
        except Exception:
            pass
    
    # Open file logger for ticking diagnosis
    debug_log_file = None
    try:
        debug_log_file = open("output_process_debug.log", "w", buffering=1)
    except Exception:
        pass

    def _send_debug_payload(payload: str) -> bool:
        """Best-effort enqueue of a preformatted debug payload to the engine."""
        try:
            event_q.put_nowait(("debug", payload))
            return True
        except Exception:
            return False

    def _write_debug_file(payload: str) -> None:
        try:
            if debug_log_file:
                debug_log_file.write(f"{payload}\n")
        except Exception:
            pass

    def _log(msg: str) -> None:
        # Debug logging: send via non-blocking queue AND to file
        # This keeps _log safe for use outside callback while never blocking
        import time as time_module
        ts = time_module.time()
        payload = f"[{ts:.3f}] {msg}"
        _send_debug_payload(payload)
        _write_debug_file(payload)

    def _flush_probe_logs(force: bool = False) -> None:
        """Emit probe summaries without losing them when the event queue is full.

        If enqueue fails, we keep a single pending payload and retry later.
        Probe counters are snapshotted into that pending payload and reset
        so we don't double-count or spam the file on retries.
        """
        nonlocal _last_probe_emit, _pending_probe_payload
        now = time.time()

        # First, try to flush any pending probe payload.
        if _pending_probe_payload is not None:
            if _send_debug_payload(_pending_probe_payload):
                _write_debug_file(_pending_probe_payload)
                _pending_probe_payload = None
            else:
                return

        if not force and (now - _last_probe_emit) < _probe_emit_interval:
            return

        if any(telemetry_probe.values()) or any(lifecycle_probe.values()):
            ts = now
            msg = (
                "[OUTPUT-PROBE] status="
                f"{telemetry_probe['status_sent']}/{telemetry_probe['status_dropped']} "
                f"levels={telemetry_probe['levels_sent']}/{telemetry_probe['levels_dropped']} "
                f"times={telemetry_probe['times_sent']}/{telemetry_probe['times_dropped']} "
                f"master={telemetry_probe['master_sent']}/{telemetry_probe['master_dropped']} "
                f"finished={lifecycle_probe['finished_sent']}/{lifecycle_probe['finished_failed']}"
            )
            payload = f"[{ts:.3f}] {msg}"

            # Snapshot + reset counters immediately to avoid double counting.
            for bucket in (telemetry_probe, lifecycle_probe):
                for key in bucket:
                    bucket[key] = 0

            if _send_debug_payload(payload):
                _write_debug_file(payload)
            else:
                _pending_probe_payload = payload

        _last_probe_emit = now

    def _report_starvation() -> None:
        """Best-effort starvation diagnostics OUTSIDE the RT callback."""
        nonlocal _last_starvation_report
        now = time.time()
        if (now - _last_starvation_report) < _starvation_report_interval:
            return

        any_reported = False
        for cue_id, ring in list(rings.items()):
            prev_under, prev_partial = _starvation_reported.get(cue_id, (0, 0))
            cur_under = getattr(ring, "underflow_count", 0)
            cur_partial = getattr(ring, "partial_fill_count", 0)
            if cur_under != prev_under or cur_partial != prev_partial:
                any_reported = True
                _starvation_reported[cue_id] = (cur_under, cur_partial)
                _log(
                    "[STARVATION] cue="
                    f"{cue_id[:8]} underflows={cur_under}"
                    f" missing_total={getattr(ring, 'underflow_missing_frames_total', 0)}"
                    f" last_missing={getattr(ring, 'last_underflow_missing_frames', 0)}"
                    f" partials={cur_partial}"
                    f" padded_total={getattr(ring, 'partial_padded_frames_total', 0)}"
                    f" last_padded={getattr(ring, 'last_partial_padded_frames', 0)}"
                    f" ring_frames={ring.frames} eof={ring.eof}"
                    f"{_format_hb_snapshot(cue_id)}"
                )

        if any_reported:
            _flush_probe_logs(force=True)

        _last_starvation_report = now

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
                    if pending.loop_enabled and pcm.cue_id not in loop_stop_requested:
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
                ring = rings.get(pcm.cue_id)
                if ring is None:
                    ring = _Ring()
                    rings[pcm.cue_id] = ring
                    _log(f"[DRAIN-CREATE-RING] cue={pcm.cue_id[:8]} created new ring")

                # When looping is disabled mid-playback, we rely on the ring's
                # stop_on_restart_boundary flag to stop cleanly at the loop boundary.
                # Do NOT mark EOF early here: under prebuffering, doing so can stop
                # buffer requests and cause the cue to end immediately due to underflow.

                # Reset elapsed time on loop restart (only if we will actually play it).
                if pcm.is_loop_restart:
                    cue_samples_consumed[pcm.cue_id] = 0
                frames_in_chunk = pcm.pcm.shape[0]
                _log(f"[DECODE-CHUNK] cue={pcm.cue_id[:8]} received {frames_in_chunk} frames, eof={pcm.eof}")
                ring.push(pcm.pcm, pcm.eof, is_loop_restart=bool(pcm.is_loop_restart))
                # PCM received, buffered in ring
                # arrival of PCM clears any outstanding request state
                ring.request_pending = False
                ring.request_started_at = None
                ring.last_pcm_time = time.time()  # record when PCM arrived
                if frames_in_chunk > 0:
                    old_frames = ring.frames - frames_in_chunk  # what it was before push
                    # CRITICAL: Log if buffer had dropped dangerously low before this chunk arrived
                    if old_frames < 2048:  # Less than ~42ms of audio at 48kHz
                        _log(
                            f"[BUFFER-STARVING] cue={pcm.cue_id[:8]} CRITICAL: buffer was at {old_frames}fr before receiving {frames_in_chunk}fr chunk! This may cause ticking."
                            f"{_format_hb_snapshot(pcm.cue_id)}"
                        )
                    _log(f"[DRAIN-PCM-PUSH] cue={pcm.cue_id[:8]} frames={frames_in_chunk} total={ring.frames} eof={pcm.eof} ring.eof={ring.eof}")

                # Decode heartbeat timing breakdown (non-RT)
                _maybe_log_decode_heartbeat(pcm, ring)
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
            # Telemetry/status: cache in-process; main loop emits at a steady rate.
            if status:
                try:
                    callback._latest_status = str(status)
                except Exception:
                    pass
            
            # Transport pause: output silence without consuming cue buffers.
            # This keeps cue position stable so TransportPlay resumes instantly.
            if transport_paused:
                # Still mark EOF rings as finished_pending so Stop works while paused.
                for cue_id, ring in list(rings.items()):
                    try:
                        if ring.frames <= 0 and ring.eof:
                            ring.finished_pending = True
                    except Exception:
                        pass
                outdata[:] = 0
                try:
                    callback._latest_batch_levels = None
                    callback._latest_batch_levels_per_ch = None
                    callback._latest_batch_times = None
                    callback._latest_master_event = None
                except Exception:
                    pass
                return

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
                    # If we have never received PCM for this cue yet, don't pull/mix at all.
                    # This avoids injecting silence mid-buffer and reduces chance of a start click.
                    if ring.frames <= 0:
                        if ring.eof:
                            ring.finished_pending = True
                        elif ring.started:
                            # Underflow: we previously had PCM but now have none while not EOF.
                            missing = frames
                            ring.underflow_count += 1
                            ring.underflow_missing_frames_total += int(missing)
                            ring.last_underflow_missing_frames = int(missing)
                        continue

                    chunk, done, filled = ring.pull(frames, cfg.channels)

                    # Track partial fills (padding happens inside ring.pull via zero-filled remainder)
                    if filled < frames and not done:
                        padded = int(frames - filled)
                        ring.partial_fill_count += 1
                        ring.partial_padded_frames_total += padded
                        ring.last_partial_padded_frames = padded
                    
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
                        ring.finished_pending = True
                        # Clean up envelope and gains when cue finishes
                        envelopes.pop(cue_id, None)
                        gains.pop(cue_id, None)
                except Exception:
                    pass
            
            # Cache batched telemetry (main loop emits at ~60Hz).
            try:
                callback._latest_batch_levels = batch_levels if batch_levels else None
                batch_levels_per_ch = getattr(callback, '_batch_levels_per_channel', {})
                callback._latest_batch_levels_per_ch = batch_levels_per_ch if batch_levels_per_ch else None
                callback._latest_batch_times = batch_times if batch_times else None
            except Exception:
                pass
            # Clear per-channel cache for next cycle.
            callback._batch_levels_per_channel = {}
            
            np.clip(mix, -1.0, 1.0, out=mix)
            outdata[:] = mix
            
            # Calculate and emit master output levels (per-channel RMS and peak)
            master_event = None
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

                master_event = MasterLevelsEvent(rms=master_rms_db, peak=master_peak_db)
            except Exception:
                master_event = None
            if master_event is not None:
                try:
                    callback._latest_master_event = master_event
                except Exception:
                    pass
        except Exception:
            pass

    # -------------------------------------------------
    # Telemetry emission pacing
    # -------------------------------------------------
    # Emit telemetry at a stable rate (default ~60Hz) regardless of audio callback block size.
    # This keeps UI updates smooth even if PCM chunk sizes grow.
    try:
        telemetry_hz = float(os.environ.get("STEPD_TELEMETRY_HZ", "60").strip() or "60")
    except Exception:
        telemetry_hz = 60.0
    telemetry_hz = max(1.0, min(240.0, telemetry_hz))
    telemetry_interval = 1.0 / telemetry_hz
    telemetry_next_mono = time.monotonic()

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
        transport_paused = False
        while True:
            try:
                msg = cmd_q.get(timeout=0.01)
            except Exception:
                msg = None

            # IMPORTANT: Apply loop enable/disable immediately (before draining PCM).
            # Otherwise, a loop-restart PCM chunk can be drained and played before we
            # observe the loop toggle, causing an audible jump.
            if isinstance(msg, UpdateCueCommand) and getattr(msg, "loop_enabled", None) is not None:
                try:
                    if bool(msg.loop_enabled):
                        looping_cues.add(msg.cue_id)
                        loop_stop_requested.discard(msg.cue_id)
                        ring = rings.get(msg.cue_id)
                        if ring is not None and not ring.finished_pending:
                            # Decoder may have hit EOF early due to prebuffering.
                            # If loop is enabled mid-playback, allow refilling.
                            ring.eof = False
                    else:
                        looping_cues.discard(msg.cue_id)
                        loop_stop_requested.add(msg.cue_id)

                        # Stop cleanly at the next loop boundary.
                        ring = rings.get(msg.cue_id)
                        if ring is not None:
                            try:
                                ring.stop_on_restart_boundary = True
                            except Exception:
                                pass

                    # If cue hasn't started yet, keep pending metadata in sync.
                    pending = pending_starts.get(msg.cue_id)
                    if pending is not None:
                        try:
                            pending_starts[msg.cue_id] = replace(pending, loop_enabled=bool(msg.loop_enabled))
                        except Exception:
                            pass
                except Exception:
                    pass
            _drain_pcm()
            _report_starvation()

            # -------------------------------------------------
            # Emit cached telemetry at ~60Hz (outside RT callback)
            # -------------------------------------------------
            try:
                now_mono = time.monotonic()
                if now_mono >= telemetry_next_mono:
                    telemetry_next_mono = now_mono + telemetry_interval

                    latest_status = getattr(callback, "_latest_status", None)
                    if latest_status:
                        try:
                            event_q.put_nowait(("status", latest_status))
                            telemetry_probe["status_sent"] += 1
                        except Exception:
                            telemetry_probe["status_dropped"] += 1
                        try:
                            callback._latest_status = None
                        except Exception:
                            pass

                    latest_levels = getattr(callback, "_latest_batch_levels", None)
                    latest_levels_per_ch = getattr(callback, "_latest_batch_levels_per_ch", None)
                    if latest_levels:
                        event = BatchCueLevelsEvent(
                            cue_levels=latest_levels,
                            cue_levels_per_channel=latest_levels_per_ch,
                        )
                        try:
                            event_q.put_nowait(event)
                            telemetry_probe["levels_sent"] += 1
                        except Exception:
                            telemetry_probe["levels_dropped"] += 1

                    latest_times = getattr(callback, "_latest_batch_times", None)
                    if latest_times:
                        event = BatchCueTimeEvent(cue_times=latest_times)
                        try:
                            event_q.put_nowait(event)
                            telemetry_probe["times_sent"] += 1
                        except Exception:
                            telemetry_probe["times_dropped"] += 1

                    latest_master = getattr(callback, "_latest_master_event", None)
                    if latest_master is not None:
                        try:
                            event_q.put_nowait(latest_master)
                            telemetry_probe["master_sent"] += 1
                        except Exception:
                            telemetry_probe["master_dropped"] += 1
            except Exception:
                pass
            
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
                        lifecycle_probe["finished_sent"] += 1
                        rings.pop(cue_id, None)
                        gains.pop(cue_id, None)
                        cue_samples_consumed.pop(cue_id, None)
                        looping_cues.discard(cue_id)
                    except Exception:
                        lifecycle_probe["finished_failed"] += 1
            
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
            request_retry_secs = 0.5  # resend credit if request seems stuck (no PCM arriving)
            
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

                    should_retry = (
                        ring.request_pending
                        and ring.request_started_at is not None
                        and (current_time - ring.request_started_at) > request_retry_secs
                        and ring.frames < low_water
                    )

                    if ring.frames < low_water and (not ring.request_pending or should_retry):
                        # Request more frames: refill toward a target buffer size.
                        # Under high concurrency, request *more* (not less) to reduce request churn
                        # and decoder scheduling/IPC pressure.
                        target_frames = block_frames
                        if active_rings > 8:
                            # Default target is ~2000ms. Increase to ~4000ms when many cues are active.
                            target_frames = cfg.block_frames * 192
                        needed = target_frames - ring.frames
                        if needed > 0:
                            try:
                                # If this is a retry, don't re-credit the full amount again.
                                # Keep it bounded to avoid runaway credit during slow decodes.
                                retry_cap = cfg.block_frames * (192 if active_rings > 8 else 96)
                                credit = needed if not should_retry else min(needed, retry_cap)
                                decode_cmd_q.put_nowait(BufferRequest(cue_id, int(credit)))
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
                    ring.push(pcm.pcm, pcm.eof, is_loop_restart=bool(getattr(pcm, "is_loop_restart", False)))
                _flush_probe_logs()
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

            elif isinstance(msg, TransportPause):
                transport_paused = True

            elif isinstance(msg, TransportPlay):
                transport_paused = False
                    
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
                    # Keep output-side loop tracking in sync so it can't get stale when
                    # global loop / override toggles issue UpdateCueCommand(loop_enabled=...).
                    if msg.loop_enabled is not None:
                        if bool(msg.loop_enabled):
                            looping_cues.add(msg.cue_id)
                            loop_stop_requested.discard(msg.cue_id)
                            ring = rings.get(msg.cue_id)
                            if ring is not None:
                                try:
                                    ring.stop_on_restart_boundary = False
                                    if not ring.finished_pending:
                                        ring.eof = False
                                except Exception:
                                    pass
                        else:
                            looping_cues.discard(msg.cue_id)
                            loop_stop_requested.add(msg.cue_id)
                            ring = rings.get(msg.cue_id)
                            if ring is not None:
                                try:
                                    ring.stop_on_restart_boundary = True
                                except Exception:
                                    pass

                        # If the cue hasn't started yet (waiting for first PCM), update
                        # the pending start metadata so activation uses the latest loop flag.
                        pending = pending_starts.get(msg.cue_id)
                        if pending is not None:
                            pending_starts[msg.cue_id] = replace(pending, loop_enabled=bool(msg.loop_enabled))

                    # Note: in_frame/out_frame/loop_enabled application for decoding is handled
                    # in the decoder process; output only tracks loop for lifecycle bookkeeping.
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

            _flush_probe_logs()
    finally:
        _flush_probe_logs(force=True)
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass
