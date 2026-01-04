from __future__ import annotations

import multiprocessing as mp
import uuid
import time
import threading
import queue
import json
import os
from pathlib import Path
from dataclasses import replace
from datetime import datetime
from typing import Dict, Optional, List

from engine import cue
from engine.cue import Cue, CueInfo
from engine.track import Track
from engine.commands import (
    # Cue playback commands
    PlayCueCommand,
    StopCueCommand,
    FadeCueCommand,
    
    # Gain & fade commands
    SetMasterGainCommand,
    UpdateCueCommand,
    SetAutoFadeCommand,
    SetGlobalLoopEnabledCommand,
    SetLoopOverrideCommand,
    
    # Transport commands
    TransportPlay,
    TransportStop,
    TransportPause,
    TransportNext,
    TransportPrev,
    
    # Configuration commands
    OutputSetDevice,
    OutputSetConfig,
    OutputFadeTo,
    OutputListDevices,
    SetTransitionFadeDurations,
)
from engine.messages.events import (
    CueStartedEvent,
    CueFinishedEvent,
    DecodeErrorEvent,
    CueLevelsEvent,
    CueTimeEvent,
    MasterLevelsEvent,
    BatchCueLevelsEvent,
    BatchCueTimeEvent,
)
from engine.processes.decode_process_pooled import decode_process_main, DecodeStart, DecodeStop, DecodedChunk, DecodeError
from engine.processes.output_process import output_process_main, OutputConfig, OutputStartCue, OutputStopCue
import sounddevice as sd
from log.log_manager import LogManager

class AudioEngine:
    def __init__(self, *, sample_rate: int = 48000, channels: int = 2, block_frames: int = 1024, fade_in_ms: int = 100, fade_out_ms: int = 1000, fade_curve: str = "equal_power", auto_fade_on_new: bool = True) -> None:
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.block_frames = int(block_frames)
        self.fade_in_ms = int(fade_in_ms)
        self.fade_out_ms = int(fade_out_ms)
        self.fade_curve = str(fade_curve)

        self.log = LogManager()
        # Dedicated debug log for high-signal engine diagnostics (separate from the user XLSX cue log)
        self._engine_debug_log_path = Path.cwd() / "engine_debug.log"
        self._ctx = mp.get_context("spawn")

        self._decode_cmd_q = self._ctx.Queue()
        # Decode output transport can be switched to Pipe to reduce mp.Queue contention.
        # Enable by setting env var: STEPD_DECODE_TRANSPORT=pipe
        self._decode_transport = os.environ.get("STEPD_DECODE_TRANSPORT", "queue").strip().lower()
        self._decode_out_send = None
        if self._decode_transport == "pipe":
            recv_conn, send_conn = self._ctx.Pipe(duplex=False)
            self._decode_out_q = recv_conn
            self._decode_out_send = send_conn
        else:
            self._decode_out_q = self._ctx.Queue()
            self._decode_out_send = self._decode_out_q
        self._decode_evt_q = self._ctx.Queue()

        self._out_cmd_q = self._ctx.Queue()
        self._out_pcm_q = self._ctx.Queue()
        self._out_evt_q = self._ctx.Queue()

        self._decode_proc: Optional[mp.Process] = None
        self._out_proc: Optional[mp.Process] = None

        self.active_cues: Dict[str, Cue] = {}
        # Store immutable CueInfo snapshots for logging/export when cues finish
        self.cue_info_map: Dict[str, CueInfo] = {}
        # Track removal reasons for each cue: {cue_id: removal_reason_str}
        self._removal_reasons: Dict[str, str] = {}
        self.primary_cue_id: Optional[str] = None
        self._output_started: set[str] = set()
        # If True, starting a new cue will automatically fade existing active cues
        # unless the caller requests layered playback via the `layered` flag on play_cue().
        self.auto_fade_on_new = bool(auto_fade_on_new)

        # -------------------------------------------------
        # Global loop override (driven by PlayControls)
        # -------------------------------------------------
        # When enabled, ignore per-cue loop_enabled and use _global_loop_enabled.
        self._loop_override_enabled: bool = False
        self._global_loop_enabled: bool = False
        # track cues we've requested fades for to avoid duplicate commands
        self._fade_requested: set[str] = set()
        # track cues pending force-stop: {cue_id: stop_time_unix}
        self._pending_stops: Dict[str, float] = {}

        # rate-limit refade checks to avoid spamming every pump call
        self._last_refade_check: float = time.time()
        self._refade_check_interval: float = 0.05  # Check every 50ms max
        # track events generated in play_cue() to be returned by pump()
        self._pending_events: List[object] = []

        # -------------------------------------------------
        # Engine-loop heartbeat (diagnostics)
        # -------------------------------------------------
        self._hb_last_pump_mono: float | None = None
        self._hb_last_emit_mono: float = time.monotonic()
        self._hb_emit_interval: float = 0.5  # seconds
        self._hb_max_pump_dt_ms: float = 0.0
        self._hb_max_engine_hold_ms: float = 0.0
        self._hb_max_decoder_age_ms: float = 0.0
        self._hb_max_decode_to_engine_ms: float = 0.0
        self._hb_max_engine_internal_ms: float = 0.0
        self._hb_decode_chunks_drained: int = 0
        self._hb_out_events_drained: int = 0
        self._hb_decode_events_drained: int = 0

    def _append_engine_debug(self, *, level: str, message: str, metadata: dict) -> None:
        try:
            # Format: one record per line for easy grep/correlation with output_process_debug.log
            record = {
                "ts_unix": time.time(),
                "pid": mp.current_process().pid,
                "level": level,
                "message": message,
                "metadata": metadata,
            }
            line = "[ENGINE-DEBUG] " + json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            self._engine_debug_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._engine_debug_log_path.open("a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            # Best-effort only; never break audio engine on logging failures.
            pass
    def start(self) -> None:
        if self._decode_proc or self._out_proc:
            return

        # Record the active decode transport (queue vs pipe) for run-to-run comparisons.
        self._append_engine_debug(
            level="info",
            message="engine_startup",
            metadata={
                "decode_transport": self._decode_transport,
                "max_active_decoders_env": os.environ.get("STEPD_MAX_ACTIVE_DECODERS"),
            },
        )
        self._decode_proc = self._ctx.Process(
            target=decode_process_main,
            args=(self._decode_cmd_q, self._decode_out_send, self._decode_evt_q),
            daemon=False,
        )
        self._decode_proc.start()

        # Parent no longer needs the send end of the pipe.
        try:
            if self._decode_transport == "pipe" and self._decode_out_send is not None:
                self._decode_out_send.close()
        except Exception:
            pass

        cfg = OutputConfig(sample_rate=self.sample_rate, channels=self.channels, block_frames=self.block_frames)
        self._out_proc = self._ctx.Process(target=output_process_main, args=(cfg, self._out_cmd_q, self._out_pcm_q, self._out_evt_q, self._decode_cmd_q), daemon=True)
        self._out_proc.start()

    def get_output_event_queue(self) -> mp.Queue:
        """Return the output process event queue for direct access."""
        return self._out_evt_q

    def stop(self) -> None:
        try: self._decode_cmd_q.put(None)
        except Exception: pass
        try: self._out_cmd_q.put(False)
        except Exception: pass
        if self._decode_proc: self._decode_proc.join(timeout=1.0)
        if self._out_proc: self._out_proc.join(timeout=1.0)
        self._decode_proc = None
        self._out_proc = None
        self.active_cues.clear()
        self.cue_info_map.clear()
        self._removal_reasons.clear()

    def set_auto_fade_on_new(self, enabled: bool) -> None:
        """Enable or disable automatic fading of existing cues when a new cue starts."""
        try:
            self.auto_fade_on_new = bool(enabled)
            self.log.info(source="engine", message="auto_fade_on_new_set", metadata={"enabled": self.auto_fade_on_new})
        except Exception:
            pass

    def get_auto_fade_on_new(self) -> bool:
        """Return current auto-fade-on-new setting."""
        return bool(self.auto_fade_on_new)

    def _effective_loop_enabled(self, requested_loop_enabled: bool) -> bool:
        if self._loop_override_enabled:
            return bool(self._global_loop_enabled)
        return bool(requested_loop_enabled)

    def _send_loop_enabled_to_processes(self, cue_id: str, *, loop_enabled: bool) -> None:
        cmd = UpdateCueCommand(cue_id=cue_id, loop_enabled=bool(loop_enabled))
        try:
            self._out_cmd_q.put(cmd)
        except Exception:
            pass
        try:
            self._decode_cmd_q.put(cmd)
        except Exception:
            pass

    def _apply_effective_loop_to_all_active(self) -> None:
        # Do not mutate per-cue stored loop_enabled; only update decoder/output.
        for cue_id, cue_obj in list(self.active_cues.items()):
            self._send_loop_enabled_to_processes(
                cue_id,
                loop_enabled=self._effective_loop_enabled(bool(getattr(cue_obj, "loop_enabled", False))),
            )

    def handle_command(self, cmd: object) -> None:
        """Route a public Engine command (from `engine.commands`) to engine actions.

        This method only routes intent. Handlers either call existing methods
        or emit lower-level messages to decode/output processes. Unimplemented
        commands are logged as TODOs.
        """
        try:
            # Transport commands
            if isinstance(cmd, TransportPause):
                self.log.info(source="engine", message="transport_pause_requested", metadata={})
                try:
                    # Pause is implemented at output stage: mute without consuming buffers.
                    self._out_cmd_q.put(cmd)
                except Exception:
                    pass
                return

            if isinstance(cmd, TransportPlay):
                self.log.info(source="engine", message="transport_play_requested", metadata={})
                try:
                    self._out_cmd_q.put(cmd)
                except Exception:
                    pass
                return

            if isinstance(cmd, TransportStop):
                # Stop all active cues immediately and emit CueFinishedEvent for each
                # once output process reports "finished".
                self.log.info(source="engine", message="transport_stop_requested", metadata={"active_cues": len(self.active_cues)})
                for cue_id in list(self.active_cues.keys()):
                    try:
                        self._removal_reasons[cue_id] = "transport_stop"
                        self._fade_requested.discard(cue_id)
                        self._pending_stops.pop(cue_id, None)
                        self._decode_cmd_q.put(DecodeStop(cue_id=cue_id))
                        self._out_cmd_q.put(OutputStopCue(cue_id=cue_id))
                    except Exception:
                        pass
                return
            if isinstance(cmd, TransportNext):
                self.log.info(source="engine", message="transport_next_requested", metadata={})
                return
            if isinstance(cmd, TransportPrev):
                self.log.info(source="engine", message="transport_prev_requested", metadata={})
                return

            # Cue playback commands
            if isinstance(cmd, PlayCueCommand):
                # Route to play_cue directly
                try:
                    self.play_cue(cmd)
                except Exception:
                    pass
                return

            if isinstance(cmd, StopCueCommand):
                try:
                    # If caller requested a fade-out, issue OutputFadeTo and schedule cleanup
                    if getattr(cmd, "fade_out_ms", 0) and cmd.fade_out_ms > 0:
                        try:
                            self._out_cmd_q.put(OutputFadeTo(cue_id=cmd.cue_id, target_db=-120.0, duration_ms=int(cmd.fade_out_ms), curve=getattr(cmd, "fade_curve", "linear")))
                            # schedule pending stop after fade duration
                            self._pending_stops[cmd.cue_id] = time.time() + (int(cmd.fade_out_ms) / 1000.0)
                            self._fade_requested.add(cmd.cue_id)
                            self.log.info(cue_id=cmd.cue_id, source="engine", message="stop_with_fade_requested", metadata={"fade_out_ms": cmd.fade_out_ms})
                        except Exception:
                            pass
                        return
                    # Otherwise call existing stop_cue path
                    self.stop_cue(StopCueCommand(cue_id=cmd.cue_id))
                except Exception:
                    pass
                return

            if isinstance(cmd, FadeCueCommand):
                try:
                    self._out_cmd_q.put(OutputFadeTo(cue_id=cmd.cue_id, target_db=float(cmd.target_db), duration_ms=int(cmd.duration_ms), curve=str(cmd.curve)))
                    self.log.info(cue_id=cmd.cue_id, source="engine", message="fade_requested", metadata={"target_db": cmd.target_db, "duration_ms": cmd.duration_ms})
                except Exception:
                    pass
                return

            # Gain commands
            if isinstance(cmd, SetMasterGainCommand):
                # TODO: implement master gain handling in output process
                self.log.info(source="engine", message="set_master_gain_todo", metadata={"gain_db": cmd.gain_db})
                return

            if isinstance(cmd, UpdateCueCommand):
                try:
                    print(f"[AUDIO-ENGINE] Updating cue {cmd.cue_id}: in_frame={cmd.in_frame} out_frame={cmd.out_frame} gain_db={cmd.gain_db} loop_enabled={cmd.loop_enabled}")
                    self.update_cue(
                        cue_id=cmd.cue_id,
                        in_frame=cmd.in_frame,
                        out_frame=cmd.out_frame,
                        gain_db=cmd.gain_db,
                        loop_enabled=cmd.loop_enabled,
                    )
                    self.log.info(cue_id=cmd.cue_id, source="engine", message="cue_update_requested", metadata={"in_frame": cmd.in_frame, "out_frame": cmd.out_frame, "gain_db": cmd.gain_db, "loop_enabled": cmd.loop_enabled})
                except Exception as e:
                    print(f"[AUDIO-ENGINE] Error updating cue {cmd.cue_id}: {e}")
                    pass
                return

            if isinstance(cmd, SetAutoFadeCommand):
                try:
                    self.set_auto_fade_on_new(cmd.enabled)
                except Exception:
                    pass
                return

            if isinstance(cmd, SetLoopOverrideCommand):
                try:
                    self._loop_override_enabled = bool(cmd.enabled)
                    self.log.info(source="engine", message="loop_override_set", metadata={"enabled": self._loop_override_enabled})
                    # Loop override means "use global loop state for all cues".
                    # Apply immediately to all currently-active cues.
                    self._apply_effective_loop_to_all_active()
                except Exception:
                    pass
                return

            if isinstance(cmd, SetGlobalLoopEnabledCommand):
                try:
                    self._global_loop_enabled = bool(cmd.enabled)
                    self.log.info(source="engine", message="global_loop_set", metadata={"enabled": self._global_loop_enabled})
                    # Transport loop button should affect currently active cues too.
                    # If override is enabled, this becomes authoritative for all cues.
                    for cue_id in list(self.active_cues.keys()):
                        self._send_loop_enabled_to_processes(cue_id, loop_enabled=bool(self._global_loop_enabled))
                except Exception:
                    pass
                return

            # Configuration commands
            if isinstance(cmd, OutputSetDevice):
                try:
                    self.set_output_device(cmd.device)
                except Exception:
                    pass
                return

            if isinstance(cmd, OutputSetConfig):
                try:
                    self.set_output_config(cmd.sample_rate, cmd.channels, cmd.block_frames)
                except Exception:
                    pass
                return

            if isinstance(cmd, OutputListDevices):
                try:
                    # Forward directly to output process; response comes back via output events.
                    self._out_cmd_q.put(cmd)
                except Exception:
                    pass
                return

            if isinstance(cmd, SetTransitionFadeDurations):
                try:
                    self.fade_in_ms = int(cmd.fade_in_ms)
                    self.fade_out_ms = int(cmd.fade_out_ms)
                    self.log.info(
                        source="engine",
                        message="transition_fade_durations_set",
                        metadata={"fade_in_ms": self.fade_in_ms, "fade_out_ms": self.fade_out_ms},
                    )
                except Exception:
                    pass
                return

            # Unknown command
            self.log.info(source="engine", message="unknown_command", metadata={"cmd_type": str(type(cmd))})
        except Exception:
            try:
                self.log.info(source="engine", message="handle_command_exception", metadata={})
            except Exception:
                pass

    def list_output_devices(self):
        """Return a list of available output devices as returned by sounddevice."""
        try:
            return sd.query_devices()
        except Exception:
            return []

    def set_output_device(self, device: object) -> None:
        """Request the output process switch to the specified device (index or name)."""
        try:
            self._out_cmd_q.put(OutputSetDevice(device=device))
            self.log.info(source="engine", message="set_output_device_requested", metadata={"device": str(device)})
        except Exception:
            pass

    def set_output_config(self, sample_rate: int, channels: int, block_frames: int) -> None:
        """Request the output process change its config (stream reopened)."""
        try:
            self._out_cmd_q.put(OutputSetConfig(sample_rate=int(sample_rate), channels=int(channels), block_frames=int(block_frames)))
            self.log.info(source="engine", message="set_output_config_requested", metadata={"sample_rate": sample_rate, "channels": channels, "block_frames": block_frames})
        except Exception:
            pass

    def play_cue(
        self,
        cmd: PlayCueCommand,
        *,
        layered: bool = False,
    ) -> CueStartedEvent:
        """Play a cue with automatic fade-in/fade-out using engine-wide settings."""
        # (fading of other cues happens below after the new cue is registered)

        # Use cue_id from command (generated by caller/GUI); never regenerate internally
        cue_id = cmd.cue_id
        tod_start = datetime.now()

        # Determine out_frame (end frame) for the cue. Prefer explicit request,
        # then any provided total_seconds, then try to probe the file for duration.
        out_frame_val = getattr(cmd, "out_frame", None)
        total_seconds = getattr(cmd, "total_seconds", None)
        file_metadata = {}  # Will hold Artist, Title, etc. from the file
        
        # Always try to probe the file for metadata (independent of duration/out_frame)
        # Done synchronously since audio_engine runs in a separate process (won't block GUI)
        try:
            import av
            try:
                container = av.open(cmd.file_path)
                
                # Extract metadata from container level
                if container.metadata:
                    try:
                        file_metadata.update(dict(container.metadata))
                    except Exception:
                        pass
                
                # Find audio stream and extract its metadata too
                stream = next((s for s in container.streams if s.type == "audio"), None)
                
                if stream is not None:
                    # Try stream-level metadata
                    if stream.metadata:
                        try:
                            stream_meta = dict(stream.metadata)
                            file_metadata.update(stream_meta)
                        except Exception:
                            pass
                    
                    # Extract duration from stream if we need it
                    if (out_frame_val is None or out_frame_val <= 0) and total_seconds is None:
                        if getattr(stream, "duration", None) is not None and getattr(stream, "time_base", None) is not None:
                            dur = float(stream.duration * stream.time_base)
                            total_seconds = dur
                            out_frame_val = int(dur * float(self.sample_rate))
                
                try:
                    container.close()
                except Exception:
                    pass
            except Exception:
                pass
            
        except Exception:
            pass
        
        # Now handle duration/out_frame_val if still needed
        if out_frame_val is None or out_frame_val <= 0:
            if total_seconds is not None:
                try:
                    out_frame_val = int(float(total_seconds) * float(self.sample_rate))
                except Exception:
                    out_frame_val = None

        print(f"[AUDIO-ENGINE] cue={cue_id[:8]} FINAL: out_frame={out_frame_val} (from total_seconds={total_seconds} sample_rate={self.sample_rate})")
        
        track = Track(track_id=str(uuid.uuid4()), file_path=cmd.file_path, channels=self.channels, sample_rate=self.sample_rate, duration_frames=(out_frame_val if out_frame_val is not None else None))
        cue = Cue(
            cue_id=cue_id,
            track=track,
            in_frame=cmd.in_frame,
            out_frame=out_frame_val,
            gain_db=cmd.gain_db,
            loop_enabled=cmd.loop_enabled,
            has_played=True,
            tod_start=tod_start,
            total_seconds=(total_seconds if total_seconds is not None else None),
        )
        self.active_cues[cue_id] = cue

        # Create immutable CueInfo snapshot at cue start for logging and export
        cue_info = CueInfo(
            cue_id=cue.cue_id,
            track_id=cue.track.track_id,
            file_path=cmd.file_path,
            duration_seconds=total_seconds,
            in_frame=cmd.in_frame,
            out_frame=out_frame_val,
            gain_db=cmd.gain_db,
            fade_in_ms=self.fade_in_ms,
            fade_out_ms=self.fade_out_ms,
            metadata=file_metadata if file_metadata else None,
            started_at=tod_start,
        )
        self.cue_info_map[cue_id] = cue_info

        self.log.info(cue_id=cue.cue_id, track_id=cue.track.track_id, tod_start=cue.tod_start, source="engine", message="cue_start_requested",
                      metadata={"file_path": cmd.file_path, "gain_db": cmd.gain_db, "loop": cmd.loop_enabled, "fade_in_ms": self.fade_in_ms})

        # Determine whether to fade existing cues when this new cue starts.
        # Per-call `layered=True` overrides the engine-level `auto_fade_on_new`.
        fade_others = (not layered) and self.auto_fade_on_new
        if fade_others:
            stop_time = time.time() + (self.fade_out_ms / 1000.0)  # schedule stop after fade duration
            # Fade ALL other active cues (don't filter by _fade_requested)
            # The output process will handle duplicate fade commands gracefully
            old_cues = [c for c in list(self.active_cues.keys()) if c != cue_id]
            
            print(f"[AUTO-FADE-INIT] new_cue={cue_id[:8]} fade_others={fade_others} old_cues_to_fade={len(old_cues)} auto_fade_on_new={self.auto_fade_on_new}")
            
            # Send fade commands for all active cues (with timeout, non-blocking)
            # Use put() with timeout=0.1s to prevent indefinite blocking
            # while ensuring commands are queued immediately.
            fade_sent_count = 0
            fade_failed_count = 0
            for old_cid in old_cues:
                try:
                    self._removal_reasons[old_cid] = "auto_fade"  # Track as auto-fade removal
                    self._out_cmd_q.put(OutputFadeTo(
                        cue_id=old_cid,
                        target_db=-120.0,
                        duration_ms=self.fade_out_ms,
                        curve=self.fade_curve,
                    ), timeout=0.1)  # 100ms timeout to prevent stalling
                    self._fade_requested.add(old_cid)
                    self._pending_stops[old_cid] = stop_time
                    fade_sent_count += 1
                    self.log.info(cue_id=old_cid, source="engine", message="fade_requested_on_new_cue", metadata={"new_cue": cue_id, "removal_reason": "auto_fade"})
                    print(f"[FADE-QUEUED] cue={old_cid[:8]} -> output queue (sent={fade_sent_count})")
                except queue.Full:
                    # Queue full - still mark as pending so refade will retry
                    fade_failed_count += 1
                    self._fade_requested.add(old_cid)
                    self._pending_stops[old_cid] = stop_time
                    self.log.warning(cue_id=old_cid, source="engine", message="fade_queue_timeout_will_retry", metadata={"new_cue": cue_id})
                    print(f"[FADE-QUEUE-TIMEOUT] cue={old_cid[:8]} queue full, will retry (failures={fade_failed_count})")
                except Exception as e:
                    fade_failed_count += 1
                    self.log.error(cue_id=old_cid, source="engine", message="fade_queue_error", metadata={"error": str(e)})
                    print(f"[FADE-QUEUE-ERROR] cue={old_cid[:8]} {type(e).__name__}: {e}")
            
            print(f"[AUTO-FADE-COMPLETE] new_cue={cue_id[:8]} sent={fade_sent_count} failed={fade_failed_count}")

        # If fading in, start silent and specify fade-in duration in the start command (atomic)
        start_gain_db = cue.gain_db
        if self.fade_in_ms > 0:
            start_gain_db = -120.0

        # CRITICAL: Send DecodeStart FIRST so decoder is ready before output process sends BufferRequest
        print(f"[ENGINE-PLAY-CUE] cue={cue.cue_id[:8]} sending DecodeStart")
        self._decode_cmd_q.put(DecodeStart(
            cue_id=cue.cue_id,
            track_id=cue.track.track_id,
            file_path=cmd.file_path,
            in_frame=cmd.in_frame,
            out_frame=cmd.out_frame,
            gain_db=cmd.gain_db,
            loop_enabled=self._effective_loop_enabled(bool(cmd.loop_enabled)),
            target_sample_rate=self.sample_rate,
            target_channels=self.channels,
            block_frames=self.block_frames * 4,
        ))
        
        # CRITICAL FIX: Send OutputStartCue immediately instead of waiting for first DecodedChunk.
        # During high concurrency (12+ cues), the decoder may be starved and never produce
        # the first chunk, causing the cue to be marked as finished before output even starts.
        # By sending OutputStartCue immediately, we ensure the ring buffer is created and
        # requests start flowing to the decoder promptly.
        self._out_cmd_q.put(OutputStartCue(
            cue_id=cue.cue_id,
            track_id=cue.track.track_id,
            gain_db=start_gain_db,
            fade_in_duration_ms=self.fade_in_ms,
            fade_in_curve=self.fade_curve,
            target_gain_db=cue.gain_db,
            loop_enabled=self._effective_loop_enabled(bool(getattr(cue, "loop_enabled", False))),
        ))
        self._output_started.add(cue.cue_id)

        if not layered:
            self.primary_cue_id = cue.cue_id

        evt = CueStartedEvent(cue_id=cue.cue_id, track_id=cue.track.track_id, tod_start_iso=tod_start.isoformat(), file_path=cmd.file_path)
        # Queue event to be returned by pump()
        self._pending_events.append(evt)
        return evt

    def stop_cue(self, cmd: StopCueCommand) -> None:
        """Request stop of a single cue.

        Important: Do NOT remove the cue from active_cues here.
        We wait for the output process to emit ("finished", cue_id, reason)
        so we can emit a proper CueFinishedEvent to the GUI.
        """
        cue = self.active_cues.get(cmd.cue_id)
        if not cue:
            return
        self._removal_reasons[cmd.cue_id] = "manual_stop"
        try:
            self._fade_requested.discard(cmd.cue_id)
            self._pending_stops.pop(cmd.cue_id, None)
        except Exception:
            pass
        try:
            self._decode_cmd_q.put(DecodeStop(cue_id=cmd.cue_id))
        except Exception:
            pass
        try:
            self._out_cmd_q.put(OutputStopCue(cue_id=cmd.cue_id))
        except Exception:
            pass
        try:
            self.log.info(cue_id=cue.cue_id, track_id=cue.track.track_id, tod_start=cue.tod_start or datetime.now(), source="engine", message="cue_stop_requested", metadata={"removal_reason": "manual_stop"})
        except Exception:
            pass

    def update_cue(
        self,
        cue_id: str,
        *,
        in_frame: Optional[int] = None,
        out_frame: Optional[int] = None,
        gain_db: Optional[float] = None,
        loop_enabled: Optional[bool] = None,
    ) -> None:
        """Update properties of a playing cue (in_frame, out_frame, gain_db, loop_enabled).
        
        Only provided parameters will be updated; others remain unchanged.
        Changes are applied immediately in the decoder and output processes.
        """
        print(f"[AudioEngine.update_cue] CALLED with cue_id={cue_id}, in_frame={in_frame}, out_frame={out_frame}, gain_db={gain_db}, loop_enabled={loop_enabled}")
        cue = self.active_cues.get(cue_id)
        if not cue:
            print(f"[AudioEngine.update_cue] Cue {cue_id} not found in active_cues")
            return
        
        print(f"[AudioEngine.update_cue] Found cue {cue_id}, updating properties")
        
        # Update cue object for tracking
        if in_frame is not None:
            cue.in_frame = in_frame
        if out_frame is not None:
            cue.out_frame = out_frame
        if gain_db is not None:
            cue.gain_db = gain_db
        if loop_enabled is not None:
            cue.loop_enabled = loop_enabled
        
        # Send update command to decoder and output processes
        effective_loop_enabled = loop_enabled
        if loop_enabled is not None:
            effective_loop_enabled = self._effective_loop_enabled(bool(loop_enabled))
        cmd = UpdateCueCommand(
            cue_id=cue_id,
            in_frame=in_frame,
            out_frame=out_frame,
            gain_db=gain_db,
            loop_enabled=effective_loop_enabled,
        )
        print(f"[AudioEngine.update_cue] Sending UpdateCueCommand to both queues: {cmd}")
        # Send to output first so loop toggles take effect before any queued PCM is drained.
        self._out_cmd_q.put(cmd)
        self._decode_cmd_q.put(cmd)
        print(f"[AudioEngine.update_cue] Commands queued")

    def pump(self) -> List[object]:
        evts: List[object] = []

        # Heartbeat timing: cadence of pump calls (this is the "engine loop")
        pump_start_perf = time.perf_counter()
        now_mono = time.monotonic()
        pump_dt_ms: float | None = None
        if self._hb_last_pump_mono is not None:
            pump_dt_ms = (now_mono - self._hb_last_pump_mono) * 1000.0
            if pump_dt_ms > self._hb_max_pump_dt_ms:
                self._hb_max_pump_dt_ms = pump_dt_ms
        self._hb_last_pump_mono = now_mono
        
        # First, add any pending events from play_cue() or other methods
        for evt in self._pending_events:
            pass
        evts.extend(self._pending_events)
        self._pending_events.clear()
        
        # IMPORTANT: Process all finished events FIRST before checking refade timeouts
        # This prevents race conditions where a cue finishes naturally but a refade
        # timeout is triggered before the finished event clears the pending_stops.
        # We must collect ALL finished events first, regardless of other events in the queue.
        finished_events = []
        other_events = []
        
        # Drain all events from output queue, separating finished from other events
        out_events_drained_this_pump = 0
        while True:
            try:
                m = self._out_evt_q.get_nowait()
            except Exception:
                break
            out_events_drained_this_pump += 1
            if isinstance(m, tuple) and m and m[0] == "finished":
                finished_events.append(m)
            else:
                other_events.append(m)
        self._hb_out_events_drained += out_events_drained_this_pump
        
        # Process all finished events first
        for m in finished_events:
            cue_id = m[1]
            output_removal_reason = m[2] if len(m) > 2 else "eof_natural"  # Get reason from output process
            cue = self.active_cues.pop(cue_id, None)
            cue_info = self.cue_info_map.pop(cue_id, None)
            try:
                self._output_started.discard(cue_id)
                self._fade_requested.discard(cue_id)
                self._pending_stops.pop(cue_id, None)
            except Exception:
                pass
            if self.primary_cue_id == cue_id:
                self.primary_cue_id = None

            assert cue_id not in self.active_cues, f"cue_finished but cue still active: {cue_id}"

            # ALWAYS emit CueFinishedEvent if we have enough information to do so.
            # This prevents the GUI from getting stuck "playing" (e.g. button still flashing)
            # in cases like DecodeError where the Cue may already have been removed.
            if cue_info is None and cue is not None:
                # Reconstruct a minimal CueInfo snapshot from the Cue.
                cue_info = CueInfo(
                    cue_id=cue.cue_id,
                    track_id=cue.track.track_id,
                    file_path=cue.track.file_path,
                    duration_seconds=getattr(cue, "total_seconds", None),
                    in_frame=cue.in_frame,
                    out_frame=cue.out_frame,
                    gain_db=cue.gain_db,
                    fade_in_ms=self.fade_in_ms,
                    fade_out_ms=self.fade_out_ms,
                    started_at=cue.tod_start,
                    loop_enabled=bool(getattr(cue, "loop_enabled", False)),
                )

            if cue_info is not None:
                # Use removal reason from audio_engine if set (manual_fade, auto_fade, manual_stop, decode_error:..., etc.)
                # otherwise use reason from output process (eof_natural, decode_error, etc.)
                removal_reason = self._removal_reasons.pop(cue_id, output_removal_reason)

                # Best-effort logging: prefer Cue if present, else fall back to CueInfo.
                try:
                    track_id = cue.track.track_id if cue is not None else cue_info.track_id
                    tod_start = cue.tod_start if cue is not None else (cue_info.started_at or datetime.now())
                    self.log.info(
                        cue_id=cue_id,
                        track_id=track_id,
                        tod_start=tod_start,
                        source="engine",
                        message="cue_finished",
                        metadata={"removal_reason": removal_reason},
                    )
                except Exception:
                    pass

                stopped_at = datetime.now()
                final_cue_info = replace(cue_info, stopped_at=stopped_at, removal_reason=removal_reason)
                evts.append(CueFinishedEvent(cue_info=final_cue_info, reason=removal_reason))
        
        # Safety check: if a cue is in pending_stops for too long (>5 seconds), force-remove it
        # This should be a rare safety valve - fades should complete naturally and emit finished events
        current_time = time.time()
        stuck_timeout = 5.0  # 5 second safety timeout for stuck cues
        pending_to_check = list(self._pending_stops.keys())
        for cue_id in pending_to_check:
            stop_time = self._pending_stops[cue_id]
            
            # Skip if cue is no longer active
            if cue_id not in self.active_cues:
                self._pending_stops.pop(cue_id, None)
                continue
            
            # If we've been waiting way too long, force-remove as emergency measure
            if current_time - stop_time > stuck_timeout:
                cue = self.active_cues.pop(cue_id, None)
                cue_info = self.cue_info_map.pop(cue_id, None)
                self._output_started.discard(cue_id)
                self._fade_requested.discard(cue_id)
                self._pending_stops.pop(cue_id, None)
                
                if self.primary_cue_id == cue_id:
                    self.primary_cue_id = None
                
                # Send explicit stop commands to clean up
                try:
                    self._decode_cmd_q.put(DecodeStop(cue_id=cue_id))
                    self._out_cmd_q.put(OutputStopCue(cue_id=cue_id))
                except Exception:
                    pass
                
                self.log.warning(cue_id=cue_id, source="engine", message="force_removed_stuck_cue", metadata={"reason": "emergency_timeout_exceeded", "timeout_seconds": stuck_timeout})
                
                # Generate cue_finished event for GUI
                if cue and cue_info:
                    stopped_at = datetime.now()
                    final_cue_info = replace(cue_info, stopped_at=stopped_at, removal_reason="emergency_stop")
                    evts.append(CueFinishedEvent(cue_info=final_cue_info, reason="forced"))
        
        decode_chunks_drained_this_pump = 0
        max_engine_hold_ms_this_pump = 0.0
        max_decoder_age_ms_this_pump = 0.0
        max_decode_to_engine_ms_this_pump = 0.0
        max_engine_internal_ms_this_pump = 0.0
        while True:
            try:
                if hasattr(self._decode_out_q, "get_nowait"):
                    msg = self._decode_out_q.get_nowait()
                else:
                    # Pipe transport: non-blocking poll
                    if not self._decode_out_q.poll(0):
                        break
                    msg = self._decode_out_q.recv()
            except Exception:
                break
            if isinstance(msg, DecodedChunk):
                decode_chunks_drained_this_pump += 1
                # Decode heartbeat: stamp engine dequeue + forward timestamps.
                # This lets output_process split: decode->engine dequeue, engine internal work, engine->output.
                try:
                    recv_mono = time.monotonic()
                    if getattr(msg, "engine_received_mono", None) is None:
                        msg = replace(msg, engine_received_mono=recv_mono)
                except Exception:
                    pass

                # Heartbeat: compute how long this chunk waited in the engine before forwarding.
                try:
                    produced = getattr(msg, "decoder_produced_mono", None)
                    received = getattr(msg, "engine_received_mono", None)
                    forwarded = getattr(msg, "engine_forwarded_mono", None)
                    if produced is not None:
                        basis = float(received) if received is not None else now_mono
                        age_ms = (basis - float(produced)) * 1000.0
                        if age_ms > max_decoder_age_ms_this_pump:
                            max_decoder_age_ms_this_pump = age_ms
                    if produced is not None and received is not None:
                        q_ms = (float(received) - float(produced)) * 1000.0
                        if q_ms > max_decode_to_engine_ms_this_pump:
                            max_decode_to_engine_ms_this_pump = q_ms
                    if produced is not None and forwarded is not None:
                        hold_ms = (float(forwarded) - float(produced)) * 1000.0
                        if hold_ms > max_engine_hold_ms_this_pump:
                            max_engine_hold_ms_this_pump = hold_ms
                    if received is not None and forwarded is not None:
                        internal_ms = (float(forwarded) - float(received)) * 1000.0
                        if internal_ms > max_engine_internal_ms_this_pump:
                            max_engine_internal_ms_this_pump = internal_ms
                except Exception:
                    pass
                # If this is the first decoded chunk for the cue, notify output to start
                if msg.cue_id in self.active_cues and msg.cue_id not in self._output_started:
                    cue = self.active_cues.get(msg.cue_id)
                    # If fading in, start silent
                    start_gain_db = cue.gain_db
                    if self.fade_in_ms > 0:
                        start_gain_db = -120.0
                    self._out_cmd_q.put(OutputStartCue(
                        cue_id=msg.cue_id,
                        track_id=msg.track_id,
                        gain_db=start_gain_db,
                        fade_in_duration_ms=self.fade_in_ms,
                        fade_in_curve=self.fade_curve,
                        target_gain_db=cue.gain_db,
                        loop_enabled=self._effective_loop_enabled(bool(getattr(cue, "loop_enabled", False))),
                    ))
                    self._output_started.add(msg.cue_id)

                # Stamp forwarded time as close as possible to the actual enqueue to output.
                try:
                    if getattr(msg, "engine_forwarded_mono", None) is None:
                        msg = replace(msg, engine_forwarded_mono=time.monotonic())
                except Exception:
                    pass
                self._out_pcm_q.put(msg)
            elif isinstance(msg, DecodeError):
                cue = self.active_cues.pop(msg.cue_id, None)
                if cue:
                    print(f"[ENGINE-DECODE-ERROR] cue={msg.cue_id[:8]} DecodeError: {msg.error}, sending OutputStopCue")
                    self._removal_reasons[msg.cue_id] = f"decode_error: {msg.error}"  # Track error as removal reason
                    self._out_cmd_q.put(OutputStopCue(cue_id=msg.cue_id))
                    try:
                        self._output_started.discard(msg.cue_id)
                    except Exception:
                        pass
                    evts.append(DecodeErrorEvent(cue_id=msg.cue_id, track_id=msg.track_id, file_path=msg.file_path, error=msg.error))

        self._hb_decode_chunks_drained += decode_chunks_drained_this_pump
        if max_engine_hold_ms_this_pump > self._hb_max_engine_hold_ms:
            self._hb_max_engine_hold_ms = max_engine_hold_ms_this_pump
        if max_decoder_age_ms_this_pump > self._hb_max_decoder_age_ms:
            self._hb_max_decoder_age_ms = max_decoder_age_ms_this_pump
        # Optional finer-grain maxima (used only for debugging)
        try:
            if max_decode_to_engine_ms_this_pump > getattr(self, "_hb_max_decode_to_engine_ms", 0.0):
                self._hb_max_decode_to_engine_ms = max_decode_to_engine_ms_this_pump
            if max_engine_internal_ms_this_pump > getattr(self, "_hb_max_engine_internal_ms", 0.0):
                self._hb_max_engine_internal_ms = max_engine_internal_ms_this_pump
        except Exception:
            pass

        # Drain decoder events and emit OutputStartCue on "started" event
        decode_events_drained_this_pump = 0
        while True:
            try:
                m = self._decode_evt_q.get_nowait()
            except Exception:
                break
            decode_events_drained_this_pump += 1
            if isinstance(m, tuple) and m and m[0] == "started":
                cue_id = m[1] if len(m) > 1 else None
                track_id = m[2] if len(m) > 2 else None
                file_path = m[3] if len(m) > 3 else None
                total_seconds = m[4] if len(m) > 4 else None
                
                # Send OutputStartCue if we haven't already for this cue
                if cue_id and cue_id in self.active_cues and cue_id not in self._output_started:
                    cue = self.active_cues.get(cue_id)
                    # Store decoder-reported total duration if available
                    try:
                        if total_seconds is not None:
                            cue.total_seconds = float(total_seconds)
                            # Also mirror into track.duration_frames if sample rate known
                            try:
                                if cue.track.sample_rate and cue.total_seconds is not None:
                                    cue.track.duration_frames = int(cue.total_seconds * cue.track.sample_rate)
                            except Exception:
                                pass
                    except Exception:
                        pass
                    # If fading in, start silent (but NOT if this is a loop restart)
                    is_loop_restart = cue_id in self._output_started  # If already started, this is a loop restart
                    start_gain_db = cue.gain_db
                    if self.fade_in_ms > 0 and not is_loop_restart:
                        start_gain_db = -120.0
                    self._out_cmd_q.put(OutputStartCue(
                        cue_id=cue_id,
                        track_id=track_id,
                        gain_db=start_gain_db,
                        fade_in_duration_ms=self.fade_in_ms,
                        fade_in_curve=self.fade_curve,
                        target_gain_db=cue.gain_db,
                        loop_enabled=self._effective_loop_enabled(bool(getattr(cue, "loop_enabled", False))),
                        is_loop_restart=is_loop_restart,
                    ))
                    self._output_started.add(cue_id)
                    self.log.info(cue_id=cue_id, track_id=track_id, source="engine", message="sent_start_on_decoder_ready", metadata={"file_path": file_path})

            # Decoder diagnostics (best-effort)
            elif isinstance(m, tuple) and m and m[0] == "diag":
                try:
                    payload = m[1] if len(m) > 1 else None
                    self.log.warning(source="engine", message="decoder_diag", metadata=payload)
                except Exception:
                    pass
            
            # Handle looped event from decoder - send OutputStartCue with is_loop_restart=True
            elif isinstance(m, tuple) and m and m[0] == "looped":
                cue_id = m[1] if len(m) > 1 else None
                track_id = m[2] if len(m) > 2 else None
                file_path = m[3] if len(m) > 3 else None
                print(f"[DEBUG-LOOP-ENGINE] Received looped event for cue {cue_id}")
                
                if cue_id and cue_id in self.active_cues:
                    cue = self.active_cues.get(cue_id)
                    print(f"[DEBUG-LOOP-ENGINE] Sending OutputStartCue with is_loop_restart=True for cue {cue_id}, gain={cue.gain_db}dB")                    
                    self._out_cmd_q.put(OutputStartCue(
                        cue_id=cue_id,
                        track_id=track_id,
                        gain_db=cue.gain_db,
                        fade_in_duration_ms=0,  # No fade on loop restart
                        fade_in_curve="linear",
                        target_gain_db=cue.gain_db,
                        loop_enabled=self._effective_loop_enabled(bool(getattr(cue, "loop_enabled", False))),
                        is_loop_restart=True,
                    ))
                else:
                    print(f"[DEBUG-LOOP-ENGINE] Looped event for cue {cue_id}: cue not in active_cues")

        self._hb_decode_events_drained += decode_events_drained_this_pump

        # Emit heartbeat (rate-limited). Only emit when engine is doing work or cues are active.
        try:
            hb_now = time.monotonic()
            if (hb_now - self._hb_last_emit_mono) >= self._hb_emit_interval:
                pump_work_ms = (time.perf_counter() - pump_start_perf) * 1000.0
                active = len(self.active_cues)
                should_emit = active > 0 or decode_chunks_drained_this_pump > 0 or out_events_drained_this_pump > 0
                if should_emit:
                    suspicious = False
                    if pump_dt_ms is not None and pump_dt_ms > 50.0:
                        suspicious = True
                    if max_engine_hold_ms_this_pump > 50.0 or max_decoder_age_ms_this_pump > 100.0:
                        suspicious = True
                    if pump_work_ms > 20.0:
                        suspicious = True

                    level = "warning" if suspicious else "info"
                    meta = {
                        "active_cues": active,
                        "pump_dt_ms": pump_dt_ms,
                        "pump_work_ms": pump_work_ms,
                        "out_events_drained": out_events_drained_this_pump,
                        "decode_chunks_drained": decode_chunks_drained_this_pump,
                        "decode_events_drained": decode_events_drained_this_pump,
                        "max_engine_hold_ms": max_engine_hold_ms_this_pump,
                        "max_decoder_age_ms": max_decoder_age_ms_this_pump,
                        "max_decode_to_engine_ms": max_decode_to_engine_ms_this_pump,
                        "max_engine_internal_ms": max_engine_internal_ms_this_pump,
                        # running maxima since engine start (useful for quick triage)
                        "max_pump_dt_ms_run": self._hb_max_pump_dt_ms,
                        "max_engine_hold_ms_run": self._hb_max_engine_hold_ms,
                        "max_decoder_age_ms_run": self._hb_max_decoder_age_ms,
                        "max_decode_to_engine_ms_run": self._hb_max_decode_to_engine_ms,
                        "max_engine_internal_ms_run": self._hb_max_engine_internal_ms,
                    }
                    if level == "warning":
                        self.log.warning(source="engine", message="engine_heartbeat", metadata=meta)
                    else:
                        self.log.info(source="engine", message="engine_heartbeat", metadata=meta)

                    # Always mirror heartbeat to a dedicated debug file for correlation.
                    self._append_engine_debug(level=level, message="engine_heartbeat", metadata=meta)

                # Reset running counters each emit window (keep maxima)
                self._hb_decode_chunks_drained = 0
                self._hb_out_events_drained = 0
                self._hb_decode_events_drained = 0
                self._hb_last_emit_mono = hb_now
        except Exception:
            pass

        # Now process all other output events (not finished)
        for m in other_events:
            # Pass through non-tuple event objects (telemetry) directly.
            if isinstance(m, (BatchCueLevelsEvent, BatchCueTimeEvent, MasterLevelsEvent)):
                evts.append(m)
                continue

            if isinstance(m, tuple) and m:
                tag = m[0]
                if tag == "started":
                    # decoder reported it started for a cue
                    try:
                        _cid = m[1]
                        _tid = m[2] if len(m) > 2 else None
                        _fp = m[3] if len(m) > 3 else None
                        self.log.info(cue_id=_cid, track_id=_tid, source="engine", message="decoder_started", metadata={"file_path": _fp})
                    except Exception:
                        pass
                elif tag == "status":
                    try:
                        self.log.info(source="engine", message="output_status", metadata={"status": m[1]})
                    except Exception:
                        pass
                elif tag == "device_changed":
                    try:
                        self.log.info(source="engine", message="output_device_changed", metadata={"device": m[1]})
                    except Exception:
                        pass
                elif tag == "config_changed":
                    try:
                        self.log.info(source="engine", message="output_config_changed", metadata=m[1] if len(m) > 1 else {})
                    except Exception:
                        pass
                elif tag == "devices":
                    try:
                        # forward device list as a simple event tuple for UI consumption
                        evts.append(("devices", m[1]))
                    except Exception:
                        pass
                elif tag == "cue_levels":
                    try:
                        _cid = m[1]
                        _rms = float(m[2]) if len(m) > 2 else 0.0
                        _peak = float(m[3]) if len(m) > 3 else 0.0
                        evts.append(CueLevelsEvent(cue_id=_cid, rms=_rms, peak=_peak))
                    except Exception:
                        pass
                elif tag == "cue_time":
                    try:
                        _cid = m[1]
                        _elapsed = float(m[2]) if len(m) > 2 else 0.0
                        _remaining = float(m[3]) if len(m) > 3 else 0.0
                        # Attach known total_seconds from cue metadata when available
                        _total = None
                        try:
                            cue = self.active_cues.get(_cid)
                            if cue:
                                _total = getattr(cue, "total_seconds", None)
                        except Exception:
                            _total = None
                        evts.append(CueTimeEvent(cue_id=_cid, elapsed_seconds=_elapsed, remaining_seconds=_remaining, total_seconds=_total))
                    except Exception:
                        pass
                elif tag == "debug":
                    try:
                        self.log.info(source="engine", message="output_debug", metadata={"msg": m[1]})
                    except Exception:
                        pass

            for cid, cue in self.active_cues.items():
                assert cue.has_played, f"active cue never started: {cid}"
        return evts
