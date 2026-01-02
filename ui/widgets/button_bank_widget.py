from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional
from PySide6.QtWidgets import QWidget, QGridLayout
from PySide6.QtCore import Signal, QTimer
import time

from engine.commands import PlayCueCommand, StopCueCommand, FadeCueCommand

if TYPE_CHECKING:
    from ui.widgets.sound_file_button import SoundFileButton
    from gui.engine_adapter import EngineAdapter


class ButtonBankWidget(QWidget):
    """
    Grid of audio clip buttons with intelligent event routing and command batching.
    
    Each button manages a single audio cue and emits playback requests to the engine adapter.
    This widget implements signal batching to reduce queue overhead when many cues are
    controlled simultaneously. Commands are accumulated for a short time window (~5ms)
    and then sent as a batch to reduce context switching and improve responsiveness.
    
    Benefits:
    - Reduces queue.put() calls from N to 1 for N simultaneous button clicks
    - Reduces Qt signal processing overhead
    - Improves GUI responsiveness with many concurrent cues
    """

    def __init__(self, rows: int = 3, cols: int = 8, engine_adapter: EngineAdapter | None = None) -> None:
        """
        Initialize button bank with grid of sound file buttons and batching support.
        
        Args:
            rows (int): Number of rows in button grid
            cols (int): Number of columns in button grid
            engine_adapter (EngineAdapter or None): Reference to engine adapter for connecting button signals
        """
        super().__init__()
        self.engine_adapter = engine_adapter
        self.buttons = []
        self.setFixedHeight(500)
        
        # Command batching state
        self._pending_commands: list = []
        self._batch_timer = QTimer()
        self._batch_timer.setSingleShot(True)
        self._batch_timer.timeout.connect(self._flush_batch)
        self._batch_window_ms = 15  # 15ms batching window (66 commands/sec max)

        # Defer button cleanup work so multiple cue-finished events collapse into one repaint
        self._dirty_buttons: set = set()
        self._cleanup_timer = QTimer()
        self._cleanup_timer.setSingleShot(True)
        self._cleanup_timer.timeout.connect(self._run_batched_button_cleanup)

        # Timing instrumentation
        self._slow_threshold_ms = 2.0
        self._event_times = {}  # Track timing per event type
        
        # Import here to avoid module-level import in subprocess (avoids pickling issues)
        from ui.widgets.sound_file_button import SoundFileButton
        
        layout = QGridLayout(self)
        layout.setSpacing(6)
        for r in range(rows):
            for c in range(cols):
                btn: SoundFileButton = SoundFileButton()
                # Connect button's playback request signals to ButtonBankWidget for routing
                btn.request_play.connect(self._on_button_request_play)
                btn.request_stop.connect(self._on_button_request_stop)
                btn.request_fade.connect(self._on_button_request_fade)
                self.buttons.append(btn)
                layout.addWidget(btn, r, c)
        
        # Connect engine adapter signals to button bank's routing methods
        if engine_adapter:
            self.set_engine_adapter(engine_adapter)
    
    def _queue_command(self, cmd: object) -> None:
        """
        Queue a command for batching.
        
        Commands are accumulated in _pending_commands and flushed after a short
        time window. This allows rapid button clicks to be batched together.
        
        Args:
            cmd: A command object (PlayCueCommand, StopCueCommand, FadeCueCommand, etc.)
        """
        self._pending_commands.append(cmd)
        
        # If timer is not already running, start it
        if not self._batch_timer.isActive():
            self._batch_timer.start(self._batch_window_ms)
    
    def _flush_batch(self) -> None:
        """
        Send all pending commands as a batch to the engine adapter.
        
        If only one command is pending, send it directly (more efficient).
        If multiple commands are pending, use batch_commands() for atomicity.
        """
        if not self._pending_commands or not self.engine_adapter:
            return
        
        if len(self._pending_commands) == 1:
            # Single command: send directly without batching overhead
            cmd = self._pending_commands[0]
            if isinstance(cmd, PlayCueCommand):
                self.engine_adapter.play_cue(
                    file_path=cmd.file_path,
                    cue_id=cmd.cue_id,
                    track_id=cmd.track_id,
                    gain_db=cmd.gain_db,
                    in_frame=cmd.in_frame,
                    out_frame=cmd.out_frame,
                    fade_in_ms=cmd.fade_in_ms,
                    loop_enabled=cmd.loop_enabled,
                    layered=cmd.layered,
                    total_seconds=cmd.total_seconds,
                )
            elif isinstance(cmd, StopCueCommand):
                self.engine_adapter.stop_cue(cmd.cue_id, cmd.fade_out_ms)
            elif isinstance(cmd, FadeCueCommand):
                self.engine_adapter.fade_cue(cmd.cue_id, cmd.target_db, cmd.duration_ms, cmd.curve)
        else:
            # Multiple commands: send as batch for efficiency
            self.engine_adapter.batch_commands(self._pending_commands)
        
        self._pending_commands.clear()
    
    def _on_button_request_play(self, file_path: str, params: dict) -> None:
        """
        Handle play request from a button by queuing it for batching.
        
        Args:
            file_path (str): Path to audio file
            params (dict): Playback parameters including cue_id
        """
        cmd = PlayCueCommand(
            cue_id=params.get('cue_id', ''),
            file_path=file_path,
            track_id=params.get('track_id'),
            gain_db=params.get('gain_db', 0.0),
            in_frame=params.get('in_frame', 0),
            out_frame=params.get('out_frame'),
            fade_in_ms=params.get('fade_in_ms', 0),
            loop_enabled=params.get('loop_enabled', False),
            layered=params.get('layered', False),
            total_seconds=params.get('total_seconds'),
        )
        self._queue_command(cmd)
    
    def _on_button_request_stop(self, cue_id: str, fade_out_ms: int) -> None:
        """
        Handle stop request from a button by queuing it for batching.
        
        Args:
            cue_id (str): Identifier of cue to stop
            fade_out_ms (int): Fade-out duration
        """
        cmd = StopCueCommand(cue_id=cue_id, fade_out_ms=fade_out_ms)
        self._queue_command(cmd)
    
    def _on_button_request_fade(self, cue_id: str, target_db: float, duration_ms: int) -> None:
        """
        Handle fade request from a button by queuing it for batching.
        
        Args:
            cue_id (str): Identifier of cue to fade
            target_db (float): Target gain in dB
            duration_ms (int): Fade duration
        """
        cmd = FadeCueCommand(cue_id=cue_id, target_db=target_db, duration_ms=duration_ms)
        self._queue_command(cmd)
    
    def set_engine_adapter(self, engine_adapter: EngineAdapter) -> None:
        """
        Set or update engine adapter reference and route events centrally.
        
        OPTIMIZATION: Instead of subscribing all 24 buttons to adapter signals,
        only ButtonBankWidget subscribes. This reduces signal overhead from
        24*N to just N when N cues are active.
        
        ButtonBankWidget routes events to the appropriate button based on cue_id.
        
        Args:
            engine_adapter (EngineAdapter): The engine adapter instance
        """
        self.engine_adapter = engine_adapter
        
        # Subscribe ButtonBankWidget (not individual buttons) to adapter signals
        # This centralized routing reduces signal overhead by 24x
        engine_adapter.cue_started.connect(self._on_adapter_cue_started)
        engine_adapter.cue_finished.connect(self._on_adapter_cue_finished)
        engine_adapter.cue_time.connect(self._on_adapter_cue_time)
        engine_adapter.cue_levels.connect(self._on_adapter_cue_levels)

        # IMPORTANT: Buttons still need a path to send per-cue updates (gain slider, loop toggle,
        # in/out points, etc.) into the engine. We keep event routing centralized, but connect
        # each button's outbound update signal to the adapter.
        for btn in self.buttons:
            try:
                btn.update_cue_settings.disconnect()
            except Exception:
                pass
            btn.update_cue_settings.connect(engine_adapter.update_cue)
    
    def _on_adapter_cue_started(self, cue_id: str, cue_info: object) -> None:
        """
        Route cue_started event to the button that owns this cue.
        
        Called once per cue_started event (much more efficient than
        calling this on all 24 buttons).
        """
        start = time.perf_counter()
        # Find button with matching cue_id in _active_cue_ids
        for btn in self.buttons:
            if cue_id in btn._active_cue_ids:
                btn._on_cue_started(cue_id, cue_info)
                break
        elapsed = (time.perf_counter() - start) * 1000
        if elapsed > self._slow_threshold_ms:
            print(f"[PERF] ButtonBankWidget._on_adapter_cue_started: {elapsed:.2f}ms cue_id={cue_id}")
    
    def _on_adapter_cue_finished(self, cue_id: str, cue_info: object, reason: str) -> None:
        """
        Route cue_finished event to the button that owns this cue.
        
        Critical optimization: Only the owning button processes this event,
        not all 24 buttons. Reduces signal processing by 24x.
        """
        start = time.perf_counter()
        # Find button with matching cue_id in _active_cue_ids
        for btn in self.buttons:
            if cue_id in btn._active_cue_ids:
                btn._on_cue_finished(cue_id, cue_info, reason)
                self._mark_button_dirty(btn)
                break
        elapsed = (time.perf_counter() - start) * 1000
        if elapsed > self._slow_threshold_ms:
            print(f"[PERF] ButtonBankWidget._on_adapter_cue_finished: {elapsed:.2f}ms cue_id={cue_id} reason={reason}")
    
    def _on_adapter_cue_time(self, cue_id: str, elapsed: float, remaining: float, total: object) -> None:
        """
        Route cue_time event to the button that owns this cue.
        """
        start = time.perf_counter()
        # Find button with matching cue_id in _active_cue_ids
        for btn in self.buttons:
            if cue_id in btn._active_cue_ids:
                btn._on_cue_time(cue_id, elapsed, remaining, total)
                break
        elapsed_ms = (time.perf_counter() - start) * 1000
        if elapsed_ms > self._slow_threshold_ms:
            print(f"[PERF] ButtonBankWidget._on_adapter_cue_time: {elapsed_ms:.2f}ms cue_id={cue_id}")
    
    def _on_adapter_cue_levels(self, cue_id: str, rms, peak) -> None:
        """
        Route cue_levels event to the button that owns this cue.
        """
        start = time.perf_counter()
        # Find button with matching cue_id in _active_cue_ids
        for btn in self.buttons:
            if cue_id in btn._active_cue_ids:
                btn._on_cue_levels(cue_id, rms, peak)
                break
        elapsed = (time.perf_counter() - start) * 1000
        if elapsed > self._slow_threshold_ms:
            print(f"[PERF] ButtonBankWidget._on_adapter_cue_levels: {elapsed:.2f}ms cue_id={cue_id}")

    # ------------------------------------------------------------------
    # Batched cleanup helpers
    # ------------------------------------------------------------------

    def _mark_button_dirty(self, btn: object) -> None:
        """Track buttons needing cleanup and schedule a single-pass flush."""
        self._dirty_buttons.add(btn)
        if len(self._dirty_buttons) >= 3:
            print(f"[PERF] ButtonBankWidget._mark_button_dirty pending={len(self._dirty_buttons)}")
        if not self._cleanup_timer.isActive():
            # Run after current event loop turn so multiple cues batch together.
            self._cleanup_timer.start(0)

    def _run_batched_button_cleanup(self) -> None:
        """Run deferred cleanup for any buttons marked dirty."""
        if not self._dirty_buttons:
            return
        dirty = list(self._dirty_buttons)
        self._dirty_buttons.clear()
        start = time.perf_counter()
        for btn in dirty:
            btn._finish_cleanup()
        elapsed = (time.perf_counter() - start) * 1000
        if elapsed > self._slow_threshold_ms or len(dirty) >= 5:
            print(
                f"[PERF] ButtonBankWidget._run_batched_button_cleanup: {elapsed:.2f}ms"
                f" count={len(dirty)} avg={elapsed/len(dirty):.2f}ms"
            )
