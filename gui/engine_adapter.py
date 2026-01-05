"""
Engine Adapter: Strict Boundary Layer Between Qt GUI and AudioService

This module is the ONLY bridge between the GUI (Qt) and the AudioService process.
It provides:
- Conversion of engine events (from engine/messages/events.py) into Qt signals
- Methods to send engine commands (from engine/messages/commands.py) via IPC queues
- Non-blocking polling of the event queue using QTimer
- Resilience to dropped telemetry and queue backlog

CRITICAL DESIGN PRINCIPLE:
- The engine/audio_service.py code MUST NEVER import Qt (PySide6/PyQt)
- This adapter MUST NEVER contain audio logic or UI widgets
- This adapter MUST NEVER block the Qt thread
- All interaction with the engine is strictly via multiprocessing queues

Architecture:
```
┌─────────────────────────────────────────────────────────────────┐
│ MainWindow / UI Widgets                                         │
│  (PySide6 / Qt)                                                 │
└────────────────────┬──────────────────────────────────────────┘
                     │
     ┌───────────────┴────────────────┐
     │  EngineAdapter (this module)   │
     │  - Translates events → signals │
     │  - Translates commands → IPC   │
     │  - Non-blocking via QTimer     │
     └───────┬──────────────────┬─────┘
             │                  │
      cmd_q (Queue)      evt_q (Queue)
    (GUI → Service)    (Service → GUI)
             │                  │
     ┌───────┴──────────────────┴──────┐
     │ AudioService Process             │
     │  (engine/audio_service.py)       │
     │  - No Qt imports                 │
     │  - Pure audio logic              │
     │  - Multiprocessing only          │
     └──────────────────────────────────┘
```

Benefits:
- Qt UI remains responsive even if audio process is busy
- Audio continues uninterrupted even if Qt blocks (file dialogs, etc.)
- Clear separation of concerns (no audio logic in GUI, no Qt in audio)
- Events are non-blocking and telemetry is dropped gracefully
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING
import multiprocessing as mp
import traceback
import uuid
import time
from collections import deque

from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import QWidget

from engine.commands import (
    PlayCueCommand,
    StopCueCommand,
    FadeCueCommand,
    SetMasterGainCommand,
    UpdateCueCommand,
    SetAutoFadeCommand,
    SetGlobalLoopEnabledCommand,
    SetLoopOverrideCommand,
    TransportPlay,
    TransportStop,
    TransportPause,
    TransportNext,
    TransportPrev,
    OutputSetDevice,
    OutputSetConfig,
    OutputListDevices,
    SetTransitionFadeDurations,
    BatchCommandsCommand,
)
from engine.cue import Cue
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

if TYPE_CHECKING:
    from engine.cue import CueInfo


class EngineAdapter(QObject):
    """
    Qt-to-AudioService Bridge.
    
    This QObject translates between:
    - Engine events (immutable dataclasses) → Qt signals
    - Qt method calls → Engine commands via multiprocessing queues
    
    Responsibilities:
    1. Poll the event queue (evt_q) periodically using QTimer.
    2. Drain received events and emit corresponding Qt signals.
    3. Provide methods for the GUI to send commands to the engine.
    4. Handle errors gracefully (missing files, dropped events, queue full).
    5. Never block the Qt thread.
    
    Usage:
        # Create adapter with queues from MainWindow
        adapter = EngineAdapter(cmd_q, evt_q, parent=main_window)
        
        # Connect signals
        adapter.cue_started.connect(on_cue_started)
        adapter.cue_finished.connect(on_cue_finished)
        
        # Send commands
        adapter.play_cue(file_path="/path/to/audio.wav")
        adapter.stop_cue(cue_id="12345678")
    """

    # ==========================================================================
    # LIFECYCLE SIGNALS (guaranteed delivery, exactly once per cue)
    # ===========================================================================

    cue_started = Signal(str, object)  # cue_id: str, cue_info: CueInfo
    """
    Emitted when a cue begins playback.
    
    Args:
        cue_id (str): Unique identifier for this playback session.
        cue_info: CueInfo object with metadata (file_path, duration, etc.).
    """

    cue_finished = Signal(str, object, str)  # cue_id: str, cue_info: CueInfo, reason: str
    """
    Emitted when a cue stops playing (for any reason: EOF, stop, error).
    
    Args:
        cue_id (str): Unique identifier for this playback session.
        cue_info: CueInfo snapshot with timing and metadata.
        reason (str): Why playback finished ("eof", "stopped", "error").
    """

    # ===========================================================================
    # TELEMETRY SIGNALS (best-effort, may be dropped if queue full)
    # ===========================================================================

    cue_levels = Signal(str, object, object)  # cue_id: str, rms: float|list, peak: float|list
    """
    Per-cue audio level snapshot (high frequency, ~30-50 Hz).
    
    Supports both formats:
    - Mixed levels: rms and peak as floats (0.0 = silence, 1.0+ = clipping)
    - Per-channel levels: rms and peak as lists (one value per audio channel)
    
    Args:
        cue_id (str): Identifier of the cue being metered.
        rms (float or list): RMS level (float for mixed, list for per-channel).
        peak (float or list): Peak absolute amplitude (float for mixed, list for per-channel).
    """

    cue_time = Signal(str, float, float, object)  # cue_id: str, elapsed: float, remaining: float, total: Optional[float]
    """
    Time reporting for a playing cue (high frequency, ~30-50 Hz).
    
    Args:
        cue_id (str): Identifier of the cue.
        elapsed (float): Playback time in seconds since start.
        remaining (float): Estimated time until cue finishes.
        total (float or None): Total cue duration if known.
    """

    master_levels = Signal(list, list)  # rms: list[float], peak: list[float]
    """
    Master output audio levels per channel in dB (high frequency, ~30-50 Hz).
    
    Args:
        rms (list[float]): Per-channel RMS levels in dB (e.g., [-6.5, -7.2] for stereo).
        peak (list[float]): Per-channel peak levels in dB (e.g., [-3.0, -4.1] for stereo).
    """

    # ===========================================================================
    # DIAGNOSTIC SIGNALS (status, best-effort)
    # ===========================================================================

    decode_error = Signal(str, str, str, str)  # cue_id: str, track_id: str, file_path: str, error: str
    """
    Emitted when the decode process encounters an error.
    
    Args:
        cue_id (str): Identifier of the cue that failed.
        track_id (str): Application-provided track identifier.
        file_path (str): Path to the file that failed to decode.
        error (str): Human-readable error message.
    """

    transport_state_changed = Signal(str)  # state: str
    """
    Emitted when the transport state changes (future use).
    
    Args:
        state (str): New transport state ("playing", "paused", "stopped").
    """

    # ===========================================================================
    # CONSTRUCTOR
    # ===========================================================================

    def __init__(
        self,
        cmd_q: mp.Queue,
        evt_q: mp.Queue,
        parent: Optional[QWidget] = None,
        poll_interval_ms: int = 16,
    ) -> None:
        """
        Initialize the engine adapter.
        
        Args:
            cmd_q: multiprocessing.Queue for sending commands to AudioService.
            evt_q: multiprocessing.Queue for receiving events from AudioService.
            parent: Optional Qt parent widget (for signal/slot ownership).
            poll_interval_ms: Event polling interval in milliseconds (default 16 ≈ 60 Hz).
                            Lower = more responsive but higher CPU. 16-33 Hz recommended.
        """
        super().__init__(parent=parent)
        self._cmd_q = cmd_q
        self._evt_q = evt_q
        # Duration tracking for accurate remaining-time UI.
        # Populated from CueStartedEvent.total_seconds and CueFinishedEvent.cue_info.duration_seconds.
        self._cue_total_seconds: dict[str, Optional[float]] = {}
        self._last_started_cue_id: Optional[str] = None

        # Best-effort local transport state tracking.
        # The engine currently does not emit TransportStateEvent reliably.
        self.transport_state: str = "playing"  # "playing" | "paused" | "stopped"
        
        # Timing instrumentation
        self._slow_threshold_ms = 5.0
        self._poll_event_times = []
        self._dispatch_event_times = []
        self._poll_debug_logging = True  # Temporary verbose logging for queue analysis

        # Poll jitter / backlog diagnostics
        self._poll_interval_ms = int(poll_interval_ms)
        self._poll_seq = 0
        self._last_poll_perf = time.perf_counter()
        self._last_poll_wall = time.time()

        # Set up polling timer
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(poll_interval_ms)
        self._poll_timer.timeout.connect(self._poll_events)
        self._poll_timer.start()
        
        # Telemetry throttling: track last emission time per event type
        # to reduce UI update frequency
        self._last_master_levels_emit = 0.0  # seconds
        self._last_master_time_emit = 0.0    # seconds
        self._last_cue_levels_emit = {}      # cue_id -> time
        self._master_levels_debounce = 0.05  # Emit master levels at ~20 Hz max
        self._master_time_debounce = 0.05    # Emit master time at ~20 Hz max
        self._cue_levels_debounce = 0.1      # Emit per-cue levels at ~10 Hz max
        
        # Pending telemetry to emit (coalesce multiple events)
        self._pending_master_levels = None
        self._pending_master_time = None

        # Lifecycle backlog: preserve ordering and prevent CueStarted/CueFinished starvation
        # under heavy telemetry load. We keep overflow lifecycle events here rather than
        # re-queueing them behind telemetry in the multiprocessing queue.
        self._lifecycle_backlog = deque()

    # ===========================================================================
    # COMMAND METHODS (GUI → AudioService)
    # ===========================================================================

    def transport_play(self) -> None:
        """Request transport play (global)."""
        try:
            self._cmd_q.put(TransportPlay())
            self.transport_state = "playing"
        except Exception as e:
            print(f"[EngineAdapter.transport_play] Error: {e}")

    def transport_pause(self) -> None:
        """Request transport pause (global)."""
        try:
            self._cmd_q.put(TransportPause())
            self.transport_state = "paused"
        except Exception as e:
            print(f"[EngineAdapter.transport_pause] Error: {e}")

    def transport_stop(self) -> None:
        """Request transport stop (global)."""
        try:
            # Stop all cues immediately.
            self._cmd_q.put(TransportStop())
            # IMPORTANT: Stop should also reset pause state so future plays are audible.
            # (Pause is implemented at output stage.) This does not start playback.
            self._cmd_q.put(TransportPlay())
            self.transport_state = "stopped"
        except Exception as e:
            print(f"[EngineAdapter.transport_stop] Error: {e}")

    def transport_next(self) -> None:
        """Request transport next (future use)."""
        try:
            self._cmd_q.put(TransportNext())
        except Exception as e:
            print(f"[EngineAdapter.transport_next] Error: {e}")

    def transport_prev(self) -> None:
        """Request transport prev (future use)."""
        try:
            self._cmd_q.put(TransportPrev())
        except Exception as e:
            print(f"[EngineAdapter.transport_prev] Error: {e}")

    def play_cue(
        self,
        file_path: str,
        cue_id: Optional[str] = None,
        track_id: Optional[str] = None,
        gain_db: float = 0.0,
        in_frame: int = 0,
        out_frame: Optional[int] = None,
        fade_in_ms: int = 0,
        loop_enabled: bool = False,
        layered: bool = False,
        total_seconds: Optional[float] = None,
    ) -> None:
        """
        Request playback of an audio file (cue).
        
        Args:
            file_path (str): Absolute path to audio file (must exist).
            cue_id (str, optional): Unique identifier generated by caller (GUI).
                                    If not provided, a cue_id will be generated internally.
            track_id (str, optional): Application-specific track ID for logging.
            gain_db (float): Initial gain in dB (default 0.0 = unity).
            in_frame (int): Start at this frame (default 0 = beginning).
            out_frame (int or None): Stop at this frame (None = end of file).
            fade_in_ms (int): Fade-in duration in milliseconds (default 0 = no fade).
            loop_enabled (bool): Enable looping from out_frame to in_frame (default False).
            layered (bool): If True, don't auto-fade existing cues (stack playback).
            total_seconds (float or None): Pre-computed duration in seconds (optional).
        """
        start = time.perf_counter()
        try:
            cmd = PlayCueCommand(
                cue_id=cue_id or uuid.uuid4().hex,
                file_path=file_path,
                track_id=track_id,
                gain_db=gain_db,
                in_frame=in_frame,
                out_frame=out_frame,
                fade_in_ms=fade_in_ms,
                loop_enabled=loop_enabled,
                layered=layered,
                total_seconds=total_seconds,
            )
            q_start = time.perf_counter()
            self._cmd_q.put(cmd)
            q_time = (time.perf_counter() - q_start) * 1000
            
            elapsed = (time.perf_counter() - start) * 1000
            if elapsed > self._slow_threshold_ms:
                print(f"[PERF] play_cue took {elapsed:.2f}ms (queue.put: {q_time:.2f}ms) cue_id={cmd.cue_id}")
        except Exception as e:
            print(f"[EngineAdapter.play_cue] Error: {e}")
            traceback.print_exc()

    def stop_cue(self, cue_id: str, fade_out_ms: int = 0) -> None:
        """
        Request stop of a playing cue.
        
        Args:
            cue_id (str): Unique identifier of the cue to stop.
            fade_out_ms (int): Fade-out duration in milliseconds (default 0 = immediate).
        """
        start = time.perf_counter()
        try:
            cmd = StopCueCommand(cue_id=cue_id, fade_out_ms=fade_out_ms)
            q_start = time.perf_counter()
            self._cmd_q.put(cmd)
            q_time = (time.perf_counter() - q_start) * 1000
            
            elapsed = (time.perf_counter() - start) * 1000
            if elapsed > self._slow_threshold_ms:
                print(f"[PERF] stop_cue took {elapsed:.2f}ms (queue.put: {q_time:.2f}ms) cue_id={cue_id}")
        except Exception as e:
            print(f"[EngineAdapter.stop_cue] Error: {e}")

    def fade_cue(
        self,
        cue_id: str,
        target_db: float,
        duration_ms: int,
        curve: str = "equal_power",
    ) -> None:
        """
        Request a dynamic gain fade on a specific cue.
        
        Args:
            cue_id (str): Unique identifier of the cue to fade.
            target_db (float): Target gain in dB (e.g., 0.0 = unity, -6.0 = half).
            duration_ms (int): Fade duration in milliseconds (must be > 0).
            curve (str): Curve shape ("equal_power" or "linear", default "equal_power").
        """
        start = time.perf_counter()
        try:
            cmd = FadeCueCommand(
                cue_id=cue_id,
                target_db=target_db,
                duration_ms=duration_ms,
                curve=curve,
            )
            q_start = time.perf_counter()
            self._cmd_q.put(cmd)
            q_time = (time.perf_counter() - q_start) * 1000
            
            elapsed = (time.perf_counter() - start) * 1000
            if elapsed > self._slow_threshold_ms:
                print(f"[PERF] fade_cue took {elapsed:.2f}ms (queue.put: {q_time:.2f}ms) cue_id={cue_id} target={target_db}dB")
        except Exception as e:
            print(f"[EngineAdapter.fade_cue] Error: {e}")

    def update_cue(self, cue_id: str, cue: CueInfo | None = None, **kwargs) -> None:
        """
        Update properties of a playing cue.
        
        Can be called in two ways:
        1. With a Cue object: update_cue(cue_id, cue)
           - Updates all modified properties from the Cue object
        2. With keyword arguments: update_cue(cue_id, in_frame=0, gain_db=-6.0, loop_enabled=True)
           - Updates only the specified properties
        
        Args:
            cue_id (str): Unique identifier of the cue to update.
            cue (Cue, optional): Cue object with updated properties.
            **kwargs: Individual property updates (in_frame, out_frame, gain_db, loop_enabled).
        """
        print(f"[EngineAdapter.update_cue] CALLED with cue_id={cue_id}, cue={cue}, kwargs={kwargs}")
        try:
            if cue is not None:
                # Extract properties from Cue object
                print(f"[EngineAdapter.update_cue] Using CueInfo object. Extracting: in_frame={cue.in_frame}, out_frame={cue.out_frame}, gain_db={cue.gain_db}, loop_enabled={cue.loop_enabled}")
                cmd = UpdateCueCommand(
                    cue_id=cue_id,
                    in_frame=cue.in_frame if hasattr(cue, 'in_frame') else None,
                    out_frame=cue.out_frame if hasattr(cue, 'out_frame') else None,
                    gain_db=cue.gain_db if hasattr(cue, 'gain_db') else None,
                    loop_enabled=cue.loop_enabled if hasattr(cue, 'loop_enabled') else None,
                )
            else:
                # Use keyword arguments
                cmd = UpdateCueCommand(
                    cue_id=cue_id,
                    in_frame=kwargs.get('in_frame'),
                    out_frame=kwargs.get('out_frame'),
                    gain_db=kwargs.get('gain_db'),
                    loop_enabled=kwargs.get('loop_enabled'),
                )
            print(f"[EngineAdapter.update_cue] Created UpdateCueCommand: cue_id={cmd.cue_id}, gain_db={cmd.gain_db}")
            self._cmd_q.put(cmd)
            print(f"[EngineAdapter.update_cue] Command queued successfully")
        except Exception as e:
            print(f"[EngineAdapter.update_cue] Error: {e}")
            import traceback
            traceback.print_exc()

    def set_auto_fade(self, enabled: bool) -> None:
        """
        Request enable/disable of auto-fade-on-new behavior.
        
        When enabled, starting a new cue will fade out existing cues.
        
        Args:
            enabled (bool): If True, enable auto-fade. If False, allow layered playback.
        """
        try:
            cmd = SetAutoFadeCommand(enabled=enabled)
            self._cmd_q.put(cmd)
        except Exception as e:
            print(f"[EngineAdapter.set_auto_fade] Error: {e}")

    def set_loop_override(self, enabled: bool) -> None:
        """Enable/disable global loop override in the engine."""
        try:
            self._cmd_q.put(SetLoopOverrideCommand(enabled=bool(enabled)))
        except Exception as e:
            print(f"[EngineAdapter.set_loop_override] Error: {e}")

    def set_global_loop_enabled(self, enabled: bool) -> None:
        """Set global loop enabled state (used only when override is enabled)."""
        try:
            self._cmd_q.put(SetGlobalLoopEnabledCommand(enabled=bool(enabled)))
        except Exception as e:
            print(f"[EngineAdapter.set_global_loop_enabled] Error: {e}")

    def set_master_gain(self, gain_db: float) -> None:
        """
        Request change to master output gain (future use).
        
        Args:
            gain_db (float): Master gain in dB (0.0 = unity).
        """
        try:
            cmd = SetMasterGainCommand(gain_db=gain_db)
            self._cmd_q.put(cmd)
        except Exception as e:
            print(f"[EngineAdapter.set_master_gain] Error: {e}")

    def set_output_device(self, device: object) -> None:
        """
        Request switch to a different audio output device.
        
        Args:
            device (int or str): Device index or friendly name.
        """
        try:
            cmd = OutputSetDevice(device=device)
            self._cmd_q.put(cmd)
        except Exception as e:
            print(f"[EngineAdapter.set_output_device] Error: {e}")

    def set_output_config(
        self, sample_rate: int, channels: int, block_frames: int
    ) -> None:
        """
        Request change to audio output configuration.
        
        Args:
            sample_rate (int): Output sample rate in Hz (e.g., 48000).
            channels (int): Number of output channels (1 or 2).
            block_frames (int): Audio block size in frames (e.g., 2048).
        """
        try:
            cmd = OutputSetConfig(
                sample_rate=sample_rate, channels=channels, block_frames=block_frames
            )
            self._cmd_q.put(cmd)
        except Exception as e:
            print(f"[EngineAdapter.set_output_config] Error: {e}")

    def list_output_devices(self) -> None:
        """Request list of available audio output devices."""
        try:
            cmd = OutputListDevices()
            self._cmd_q.put(cmd)
        except Exception as e:
            print(f"[EngineAdapter.list_output_devices] Error: {e}")

    def set_transition_fade_durations(self, *, fade_in_ms: int, fade_out_ms: int) -> None:
        """Set engine-wide default transition fade durations (ms)."""
        try:
            cmd = SetTransitionFadeDurations(fade_in_ms=int(fade_in_ms), fade_out_ms=int(fade_out_ms))
            self._cmd_q.put(cmd)
        except Exception as e:
            print(f"[EngineAdapter.set_transition_fade_durations] Error: {e}")

    def batch_commands(self, commands: list) -> None:
        """
        Send multiple cue commands in a single atomic operation.
        
        This method batches multiple command objects (PlayCueCommand, StopCueCommand,
        FadeCueCommand, UpdateCueCommand) into a single BatchCommandsCommand and
        sends them as a single queue operation. This significantly reduces overhead
        when controlling many cues simultaneously.
        
        Usage:
            commands = [
                PlayCueCommand(...),
                FadeCueCommand(...),
                StopCueCommand(...),
            ]
            adapter.batch_commands(commands)
        
        Benefits:
        - Single queue.put() instead of N puts
        - Single Qt signal poll instead of N events
        - Engine processes all commands in one atomic operation
        - Improved responsiveness with many concurrent cues
        
        Args:
            commands (list): List of command objects to batch.
                            Valid types: PlayCueCommand, StopCueCommand, FadeCueCommand, UpdateCueCommand
        
        Raises:
            ValueError: If commands list is empty
        """
        if not commands:
            return  # Silently ignore empty batches
        
        try:
            from engine.commands import BatchCommandsCommand
            cmd = BatchCommandsCommand(commands=commands)
            self._cmd_q.put(cmd)
        except Exception as e:
            print(f"[EngineAdapter.batch_commands] Error: {e}")
            traceback.print_exc()

    def shutdown(self) -> None:
        """Request graceful shutdown of the AudioService process."""
        try:
            # AudioService shuts down on sentinel None.
            # TransportStop is reserved for "stop all cues".
            self._cmd_q.put(None)
        except Exception as e:
            print(f"[EngineAdapter.shutdown] Error: {e}")

    # ===========================================================================
    # INTERNAL METHODS
    # ===========================================================================

    def _poll_events(self) -> None:
        """
        Poll the event queue (called by QTimer).
        
        This method is called at regular intervals (default ~30 Hz) to drain
        the event queue and emit corresponding Qt signals. It is non-blocking
        and resilient to queue underflow, unknown events, and signal emission
        failures.

        Telemetry events may be dropped if the queue is full, which is acceptable.
        Lifecycle events (started, finished) will always be delivered.
        
        Telemetry throttling: Multiple rapid telemetry events are coalesced and
        emitted at reduced frequency to prevent UI thrashing when many cues are active.
        """
        poll_start = time.perf_counter()
        current_time = time.time()

        # Timer jitter (how late this poll fired). Large values indicate GUI thread blockage.
        now_perf = poll_start
        dt_ms = (now_perf - self._last_poll_perf) * 1000.0
        slip_ms = dt_ms - float(self._poll_interval_ms)
        self._last_poll_perf = now_perf
        self._last_poll_wall = current_time
        self._poll_seq += 1
        
        # First pass: drain pending events from queue (bounded to avoid long UI stalls)
        pending_events = []
        drain_start = time.perf_counter()
        max_drain_per_poll = 2000
        while True:
            try:
                pending_events.append(self._evt_q.get_nowait())
            except Exception:
                break
            if len(pending_events) >= max_drain_per_poll:
                break
        drain_time = (time.perf_counter() - drain_start) * 1000
        stage_times = {
            "drain": drain_time,
            "lifecycle": 0.0,
            "telemetry": 0.0,
            "diag": 0.0,
            "emit_pending": 0.0,
        }
        
        # Second pass: separate lifecycle from telemetry
        # CRITICAL: Never drop lifecycle events (CueStartedEvent, CueFinishedEvent)
        # They control button state - dropping them breaks UI
        lifecycle_events = []
        telemetry_events = []
        other_events = []
        
        for event in pending_events:
            is_lifecycle = isinstance(event, (CueStartedEvent, CueFinishedEvent))
            is_telemetry = isinstance(event, (BatchCueLevelsEvent, BatchCueTimeEvent, CueLevelsEvent, CueTimeEvent, MasterLevelsEvent))
            
            if is_lifecycle:
                lifecycle_events.append(event)
            elif is_telemetry:
                telemetry_events.append(event)
            else:
                other_events.append(event)

        if self._poll_debug_logging and pending_events:
            print(
                f"[POLL-DEBUG] drained={len(pending_events)} drain={drain_time:.2f}ms"
                f" lifecycle={len(lifecycle_events)} telemetry={len(telemetry_events)} diag={len(other_events)}"
            )
        
        # Process lifecycle events with limit (critical events)
        # IMPORTANT: Do NOT re-queue overflow lifecycle events back into the mp queue,
        # because that can put CueFinishedEvent behind a flood of telemetry and delay it indefinitely.
        # Instead, keep overflow lifecycle events in an in-memory backlog.
        lifecycle_count = 0
        max_event_time = 0.0
        max_lifecycle_per_poll = 50

        # Prepend any backlog lifecycle events (preserve order)
        if self._lifecycle_backlog:
            try:
                lifecycle_events = list(self._lifecycle_backlog) + lifecycle_events
            finally:
                self._lifecycle_backlog.clear()

        # Per-poll event timing aggregation (captures slot execution time too)
        # {event_type: {"count": int, "total_ms": float, "max_ms": float, "max_detail": str}}
        per_poll_stats = {}

        def _detail_for_event(evt: object) -> str:
            try:
                if isinstance(evt, CueFinishedEvent):
                    cue_id = evt.cue_info.cue_id[:8] if getattr(evt, "cue_info", None) else "unknown"
                    return f"cue={cue_id} reason={getattr(evt, 'reason', '')}"
                if isinstance(evt, CueStartedEvent):
                    return f"cue={evt.cue_id[:8]}"
                if isinstance(evt, BatchCueLevelsEvent):
                    n = len(evt.cue_levels) if getattr(evt, "cue_levels", None) else 0
                    return f"levels={n} per_ch={bool(getattr(evt, 'cue_levels_per_channel', None))}"
                if isinstance(evt, BatchCueTimeEvent):
                    n = len(evt.cue_times) if getattr(evt, "cue_times", None) else 0
                    return f"cues={n}"
                if isinstance(evt, MasterLevelsEvent):
                    n = len(evt.rms) if getattr(evt, "rms", None) else 0
                    return f"channels={n}"
            except Exception:
                pass
            return ""

        def _accum(evt_type: str, elapsed_ms: float, detail: str) -> None:
            rec = per_poll_stats.get(evt_type)
            if rec is None:
                per_poll_stats[evt_type] = {"count": 1, "total_ms": float(elapsed_ms), "max_ms": float(elapsed_ms), "max_detail": detail}
                return
            rec["count"] += 1
            rec["total_ms"] += float(elapsed_ms)
            if elapsed_ms > rec["max_ms"]:
                rec["max_ms"] = float(elapsed_ms)
                rec["max_detail"] = detail
        
        for event in lifecycle_events[:max_lifecycle_per_poll]:
            try:
                event_type = type(event).__name__
                detail = _detail_for_event(event)
                dispatch_start = time.perf_counter()
                self._dispatch_event(event, current_time)
                dispatch_time = (time.perf_counter() - dispatch_start) * 1000
                max_event_time = max(max_event_time, dispatch_time)
                stage_times["lifecycle"] += dispatch_time
                _accum(event_type, dispatch_time, detail)
            except Exception:
                pass
            lifecycle_count += 1

        # Keep remaining lifecycle events for next poll (preserve order, ensure delivery)
        lifecycle_dropped = len(lifecycle_events) - lifecycle_count
        if lifecycle_dropped > 0:
            self._lifecycle_backlog.extend(lifecycle_events[max_lifecycle_per_poll:])
        
        max_telemetry_per_poll = 40  # Drain more telemetry per tick to avoid backlog

        if self._poll_debug_logging and lifecycle_dropped > 0:
            print(
                f"[POLL-DEBUG] lifecycle backlog total={len(lifecycle_events)} processed={lifecycle_count}"
                f" deferred={lifecycle_dropped}"
            )
        if self._poll_debug_logging and len(telemetry_events) > max_telemetry_per_poll:
            print(
                f"[POLL-DEBUG] telemetry backlog total={len(telemetry_events)} limit={max_telemetry_per_poll}"
            )
        
        # Process telemetry events up to limit (can be dropped, best-effort)
        telemetry_count = 0
        
        for event in telemetry_events[:max_telemetry_per_poll]:
            try:
                event_type = type(event).__name__
                detail = _detail_for_event(event)
                dispatch_start = time.perf_counter()
                self._dispatch_event(event, current_time)
                dispatch_time = (time.perf_counter() - dispatch_start) * 1000
                max_event_time = max(max_event_time, dispatch_time)
                stage_times["telemetry"] += dispatch_time
                _accum(event_type, dispatch_time, detail)
            except Exception:
                pass
            telemetry_count += 1
        
        # Process all diagnostic events
        for event in other_events:
            try:
                event_type = type(event).__name__
                detail = _detail_for_event(event)
                dispatch_start = time.perf_counter()
                self._dispatch_event(event, current_time)
                dispatch_time = (time.perf_counter() - dispatch_start) * 1000
                max_event_time = max(max_event_time, dispatch_time)
                stage_times["diag"] += dispatch_time
                _accum(event_type, dispatch_time, detail)
            except Exception:
                pass
        
        # Emit any pending throttled telemetry
        telemetry_start = time.perf_counter()
        self._emit_pending_telemetry(current_time)
        telemetry_time = (time.perf_counter() - telemetry_start) * 1000
        stage_times["emit_pending"] = telemetry_time
        
        # Record and report timing
        total_time = (time.perf_counter() - poll_start) * 1000
        self._poll_event_times.append(total_time)
        if len(self._poll_event_times) > 100:
            self._poll_event_times.pop(0)
        
        total_events = len(pending_events)
        telemetry_dropped = max(0, len(telemetry_events) - telemetry_count)
        
        if self._poll_debug_logging and pending_events:
            print(
                f"[POLL-DEBUG] total={total_time:.2f}ms lifecycle={lifecycle_count}/{len(lifecycle_events)}"
                f" telemetry={telemetry_count}/{len(telemetry_events)} other={len(other_events)}"
                f" emit_pending={telemetry_time:.2f}ms max_event={max_event_time:.2f}ms"
            )
            print(
                f"[POLL-DEBUG] stages drain={stage_times['drain']:.2f}ms lifecycle={stage_times['lifecycle']:.2f}ms"
                f" telemetry={stage_times['telemetry']:.2f}ms diag={stage_times['diag']:.2f}ms"
                f" emit_pending={stage_times['emit_pending']:.2f}ms"
            )
        if self._poll_debug_logging and telemetry_dropped > 0:
            print(
                f"[POLL-DEBUG] telemetry dropped={telemetry_dropped} processed={telemetry_count}"
            )
        
        if total_events > 0 and total_time > self._slow_threshold_ms:
            avg_time = sum(self._poll_event_times[-10:]) / min(10, len(self._poll_event_times))
            stage_msg = (
                f"drain={stage_times['drain']:.2f}ms lifecycle={stage_times['lifecycle']:.2f}ms "
                f"telemetry={stage_times['telemetry']:.2f}ms diag={stage_times['diag']:.2f}ms "
                f"emit={stage_times['emit_pending']:.2f}ms"
            )
            # Summarize where time went inside this poll (top event types by total_ms)
            try:
                top = sorted(
                    ((k, v) for k, v in per_poll_stats.items()),
                    key=lambda kv: kv[1]["total_ms"],
                    reverse=True,
                )[:4]
                top_msg = "; ".join(
                    f"{k}={v['total_ms']:.1f}ms/{v['count']} max={v['max_ms']:.1f}ms {v['max_detail']}".strip()
                    for k, v in top
                    if v["total_ms"] >= 0.5
                )
            except Exception:
                top_msg = ""

            jitter_msg = f"dt={dt_ms:.1f}ms slip={slip_ms:+.1f}ms"
            if lifecycle_dropped > 0:
                print(f"[PERF] _poll_events: {total_time:.2f}ms ({lifecycle_count}/{lifecycle_count+lifecycle_dropped} lifecycle, {telemetry_count}/{telemetry_count+telemetry_dropped} telemetry, max event {max_event_time:.2f}ms) {stage_msg} {jitter_msg} avg10={avg_time:.2f}ms")
            elif telemetry_dropped > 0:
                print(f"[PERF] _poll_events: {total_time:.2f}ms ({lifecycle_count} lifecycle, {telemetry_count}/{telemetry_count+telemetry_dropped} telemetry, max event {max_event_time:.2f}ms) {stage_msg} {jitter_msg} avg10={avg_time:.2f}ms")
            elif total_time > 10.0:  # Only show really slow polls
                print(f"[PERF] _poll_events: {total_time:.2f}ms ({lifecycle_count} lifecycle, {telemetry_count} telemetry, max event {max_event_time:.2f}ms) {stage_msg} {jitter_msg} avg10={avg_time:.2f}ms")

            if top_msg:
                print(f"[PERF] _poll_events breakdown: {top_msg}")

    def _dispatch_event(self, event: object, current_time: float) -> None:
        """
        Dispatch a single event to the appropriate Qt signal.
        
        Converts engine events (from engine/messages/events.py) into Qt signals.
        Implements throttling for high-frequency telemetry events.
        Tolerates unknown event types (ignores them).
        
        Args:
            event: An event object from the AudioService.
            current_time: Current time in seconds (from time.time()).
        """
        dispatch_start = time.perf_counter()
        event_type = type(event).__name__
        
        if isinstance(event, CueStartedEvent):
            # Lifecycle: guaranteed delivery
            try:
                self._last_started_cue_id = event.cue_id
                self._cue_total_seconds[event.cue_id] = getattr(event, "total_seconds", None)
            except Exception:
                pass
            self.cue_started.emit(event.cue_id, None)  # cue_info not available yet

        elif isinstance(event, CueFinishedEvent):
            # Lifecycle: guaranteed delivery
            cue_info = event.cue_info  # CueInfo snapshot
            try:
                # CueInfo includes duration_seconds; keep in sync for any late time events.
                dur = getattr(cue_info, "duration_seconds", None)
                self._cue_total_seconds[getattr(cue_info, "cue_id", event.cue_info.cue_id)] = dur
            except Exception:
                pass
            self.cue_finished.emit(event.cue_info.cue_id, cue_info, event.reason)

        elif isinstance(event, BatchCueLevelsEvent):
            # Batched telemetry: throttled to ~10 Hz per cue
            # Prefer per-channel levels if available
            levels_to_emit = event.cue_levels_per_channel if event.cue_levels_per_channel else event.cue_levels
            
            if event.cue_levels_per_channel:
                # Per-channel format: {cue_id: (rms_list, peak_list), ...}
                for cue_id, (rms_list, peak_list) in levels_to_emit.items():
                    last_emit = self._last_cue_levels_emit.get(cue_id, 0.0)
                    if current_time - last_emit >= self._cue_levels_debounce:
                        # Emit per-channel levels (emit as lists, not single values)
                        self.cue_levels.emit(cue_id, rms_list, peak_list)
                        self._last_cue_levels_emit[cue_id] = current_time
            else:
                # Legacy mixed format: {cue_id: (rms, peak), ...}
                for cue_id, (rms, peak) in event.cue_levels.items():
                    last_emit = self._last_cue_levels_emit.get(cue_id, 0.0)
                    if current_time - last_emit >= self._cue_levels_debounce:
                        self.cue_levels.emit(cue_id, rms, peak)
                        self._last_cue_levels_emit[cue_id] = current_time

        elif isinstance(event, BatchCueTimeEvent):
            # Batched telemetry: pick a stable "master" cue (most recently started if active).
            try:
                cue_times = event.cue_times or {}
            except Exception:
                cue_times = {}
            if cue_times:
                master_id = None
                try:
                    if self._last_started_cue_id in cue_times:
                        master_id = self._last_started_cue_id
                except Exception:
                    master_id = None
                if master_id is None:
                    try:
                        master_id = next(iter(cue_times.keys()))
                    except Exception:
                        master_id = None

                if master_id is not None:
                    try:
                        elapsed, remaining = cue_times[master_id]
                    except Exception:
                        elapsed, remaining = 0.0, 0.0

                    total = self._cue_total_seconds.get(master_id)
                    if isinstance(total, (int, float)):
                        remaining = max(0.0, float(total) - float(elapsed))
                    # Store for throttled emission
                    self._pending_master_time = (master_id, float(elapsed), float(remaining), total)

        elif isinstance(event, CueLevelsEvent):
            # Legacy single-cue levels (for backward compatibility)
            last_emit = self._last_cue_levels_emit.get(event.cue_id, 0.0)
            if current_time - last_emit >= self._cue_levels_debounce:
                self.cue_levels.emit(event.cue_id, event.rms, event.peak)
                self._last_cue_levels_emit[event.cue_id] = current_time

        elif isinstance(event, CueTimeEvent):
            # Legacy single-cue time (for backward compatibility)
            # Coalesce - emit at reduced frequency
            total = getattr(event, "total_seconds", None)
            remaining = event.remaining_seconds
            if isinstance(total, (int, float)):
                try:
                    remaining = max(0.0, float(total) - float(event.elapsed_seconds))
                except Exception:
                    remaining = event.remaining_seconds
            self._pending_master_time = (event.cue_id, event.elapsed_seconds, remaining, total)

        elif isinstance(event, MasterLevelsEvent):
            # Telemetry: coalesce - emit at reduced frequency
            # Store the latest event and emit in batch
            self._pending_master_levels = event

        elif isinstance(event, DecodeErrorEvent):
            # Diagnostic: best-effort
            self.decode_error.emit(event.cue_id, event.track_id, event.file_path, event.error)

        elif isinstance(event, TransportStateEvent):
            # Diagnostic: best-effort
            self.transport_state_changed.emit(event.state)

        elif isinstance(event, tuple):
            # Legacy internal events (may be converted elsewhere)
            pass

        else:
            # Unknown event type; silently ignore
            pass
        
        dispatch_time = (time.perf_counter() - dispatch_start) * 1000
        self._dispatch_event_times.append((event_type, dispatch_time))
        if len(self._dispatch_event_times) > 100:
            self._dispatch_event_times.pop(0)
        
        if dispatch_time > self._slow_threshold_ms:
            detail = ""
            if isinstance(event, BatchCueLevelsEvent):
                level_count = len(event.cue_levels) if event.cue_levels else 0
                detail = f" levels={level_count} per_ch={bool(event.cue_levels_per_channel)}"
            elif isinstance(event, BatchCueTimeEvent):
                detail = f" cues={len(event.cue_times)}"
            elif isinstance(event, MasterLevelsEvent):
                detail = f" channels={len(event.rms)}"
            elif isinstance(event, CueFinishedEvent):
                cue_id = event.cue_info.cue_id[:8] if getattr(event, "cue_info", None) else "unknown"
                detail = f" cue={cue_id}"
            elif isinstance(event, CueStartedEvent):
                detail = f" cue={event.cue_id[:8]}"
            print(f"[PERF] _dispatch_event {event_type}: {dispatch_time:.2f}ms{detail}")

    def _emit_pending_telemetry(self, current_time: float) -> None:
        """
        Emit any pending throttled telemetry events that are ready.
        
        Args:
            current_time: Current time in seconds (from time.time()).
        """
        # Emit pending master levels at throttled rate
        if self._pending_master_levels is not None:
            if current_time - self._last_master_levels_emit >= self._master_levels_debounce:
                event = self._pending_master_levels
                self.master_levels.emit(event.rms, event.peak)
                self._last_master_levels_emit = current_time
                self._pending_master_levels = None
        
        # Emit pending master time at throttled rate
        if self._pending_master_time is not None:
            if current_time - self._last_master_time_emit >= self._master_time_debounce:
                event = self._pending_master_time
                # Handle both tuple format (cue_id, elapsed, remaining) and event object
                if isinstance(event, tuple):
                    cue_id = event[0]
                    elapsed_seconds = event[1] if len(event) > 1 else 0.0
                    remaining_seconds = event[2] if len(event) > 2 else 0.0
                    total_seconds = event[3] if len(event) > 3 else None
                    self.cue_time.emit(cue_id, float(elapsed_seconds), float(remaining_seconds), total_seconds)
                else:
                    self.cue_time.emit(
                        event.cue_id, event.elapsed_seconds, event.remaining_seconds, event.total_seconds
                    )
                self._last_master_time_emit = current_time
                self._pending_master_time = None
