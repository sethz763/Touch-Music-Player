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

from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import QWidget

from engine.commands import (
    PlayCueCommand,
    StopCueCommand,
    FadeCueCommand,
    SetMasterGainCommand,
    UpdateCueCommand,
    SetAutoFadeCommand,
    TransportPlay,
    TransportStop,
    TransportPause,
    TransportNext,
    TransportPrev,
    OutputSetDevice,
    OutputSetConfig,
    OutputListDevices,
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
        poll_interval_ms: int = 30,
    ) -> None:
        """
        Initialize the engine adapter.
        
        Args:
            cmd_q: multiprocessing.Queue for sending commands to AudioService.
            evt_q: multiprocessing.Queue for receiving events from AudioService.
            parent: Optional Qt parent widget (for signal/slot ownership).
            poll_interval_ms: Event polling interval in milliseconds (default 30 ≈ 33 Hz).
                            Lower = more responsive but higher CPU. 30-50 Hz recommended.
        """
        super().__init__(parent=parent)
        self._cmd_q = cmd_q
        self._evt_q = evt_q

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

    # ===========================================================================
    # COMMAND METHODS (GUI → AudioService)
    # ===========================================================================

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
            self._cmd_q.put(cmd)
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
        try:
            cmd = StopCueCommand(cue_id=cue_id, fade_out_ms=fade_out_ms)
            self._cmd_q.put(cmd)
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
        try:
            cmd = FadeCueCommand(
                cue_id=cue_id,
                target_db=target_db,
                duration_ms=duration_ms,
                curve=curve,
            )
            self._cmd_q.put(cmd)
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

    def shutdown(self) -> None:
        """Request graceful shutdown of the AudioService process."""
        try:
            cmd = TransportStop()
            self._cmd_q.put(cmd)
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
        import time
        current_time = time.time()
        
        event_count = 0
        while True:
            try:
                # Non-blocking get: raises Empty exception if no events
                event = self._evt_q.get_nowait()
                event_count += 1
            except Exception as e:
                # Queue empty or other error; stop polling for now
                break
            
            try:
                self._dispatch_event(event, current_time)
            except Exception as e:
                # Tolerate errors in event dispatch; log and continue
                pass
        
        # Emit any pending throttled telemetry
        self._emit_pending_telemetry(current_time)

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
        if isinstance(event, CueStartedEvent):
            # Lifecycle: guaranteed delivery
            self.cue_started.emit(event.cue_id, None)  # cue_info not available yet

        elif isinstance(event, CueFinishedEvent):
            # Lifecycle: guaranteed delivery
            cue_info = event.cue_info  # CueInfo snapshot
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
            # Batched telemetry: coalesce and emit in batch
            # Store latest times and emit in _emit_pending_telemetry
            for cue_id, (elapsed, remaining) in event.cue_times.items():
                # Create a synthetic event for compatibility with existing code
                self._pending_master_time = (cue_id, elapsed, remaining)

        elif isinstance(event, CueLevelsEvent):
            # Legacy single-cue levels (for backward compatibility)
            last_emit = self._last_cue_levels_emit.get(event.cue_id, 0.0)
            if current_time - last_emit >= self._cue_levels_debounce:
                self.cue_levels.emit(event.cue_id, event.rms, event.peak)
                self._last_cue_levels_emit[event.cue_id] = current_time

        elif isinstance(event, CueTimeEvent):
            # Legacy single-cue time (for backward compatibility)
            # Coalesce - emit at reduced frequency
            self._pending_master_time = (event.cue_id, event.elapsed_seconds, event.remaining_seconds)

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
                    cue_id, elapsed_seconds, remaining_seconds = event
                    self.cue_time.emit(cue_id, elapsed_seconds, remaining_seconds, None)
                else:
                    self.cue_time.emit(
                        event.cue_id, event.elapsed_seconds, event.remaining_seconds, event.total_seconds
                    )
                self._last_master_time_emit = current_time
                self._pending_master_time = None
