from __future__ import annotations

import multiprocessing as mp
from typing import Optional
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QLabel, QCheckBox, QHBoxLayout
from PySide6.QtCore import QTimer, QThread, Signal
from PySide6.QtGui import QAction


from ui.windows.log_dialogue import Log_Settings_Window
from ui.widgets.button_bank_widget import ButtonBankWidget
from ui.widgets.AudioLevelMeterHorizontal_LR import AudioLevelMeterHorizontal
from engine.audio_service import audio_service_main, AudioServiceConfig
from gui.engine_adapter import EngineAdapter

from log.cue_logger import CueLogger
from log.Save_To_Excel import Save_To_Excel
from log.log_manager import LogManager
from engine.messages.events import CueFinishedEvent
from log.log import Log
import datetime

class MainWindow(QMainWindow):
    log_signal = Signal(dict, str, datetime)
    
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Touch Music Player — Step D")

        # Create queues for communication with audio service process
        ctx = mp.get_context("spawn")
        self._audio_cmd_q = ctx.Queue()
        self._audio_evt_q = ctx.Queue()
        
        # Audio service configuration
        audio_config = AudioServiceConfig(
            sample_rate=48000,
            channels=2,
            block_frames=2048,
            fade_in_ms=100,
            fade_out_ms=1000,
            fade_curve="equal_power",
            auto_fade_on_new=True,
        )
        
        # Spawn audio service process (daemon=False ensures clean shutdown)
        self._audio_service = mp.Process(
            target=audio_service_main,
            args=(self._audio_cmd_q, self._audio_evt_q, audio_config),
            daemon=False,
        )
        self._audio_service.start()
        self.engine_adapter = EngineAdapter(
            cmd_q=self._audio_cmd_q,
            evt_q=self._audio_evt_q,
            parent=self,
        )
        
        # Track auto-fade state in GUI for toggle
        self._auto_fade_enabled = audio_config.auto_fade_on_new
        
        # Create LogManager (central logging hub)
        self.log_manager = LogManager()
        
        # Create Excel logger
        self.save_to_excel = Save_To_Excel(filename="cue_log.xlsx", title="Cue Log")
        
        # Create CueLogger that sends to LogManager and Excel
        self.cue_logger = CueLogger(self.log_manager, save_to_excel=self.save_to_excel)
        
        # Connect Excel logger signal to refresh dialog when entries are added
        self.save_to_excel.log_entry_added.connect(self._on_log_entry_added)
        
        # Keep reference to dialogs so they don't get garbage collected
        self._logging_dialog = None

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        # Add master output level meter at the top (horizontal layout with two channels)
        meter_layout = QHBoxLayout()
        meter_layout.setContentsMargins(5, 5, 5, 5)
        meter_layout.setSpacing(5)
        
        # Create stereo meters (one per channel) - don't set fixed width yet, let layout handle it
        self.left_meter = AudioLevelMeterHorizontal(vmin=-64, vmax=0, height=60, width=400)
        self.right_meter = AudioLevelMeterHorizontal(vmin=-64, vmax=0, height=60, width=400)
        meter_layout.addWidget(QLabel("L:"), 0)
        meter_layout.addWidget(self.left_meter, 1)  # Add stretch factor so it grows with window
        meter_layout.addWidget(QLabel("R:"), 0)
        meter_layout.addWidget(self.right_meter, 1)  # Add stretch factor so it grows with window
        layout.addLayout(meter_layout)

        self.status = QLabel("Ready")
        layout.addWidget(self.status)
        
        self.master_time_display = QLabel("Master Time: 00:00.000")
        layout.addWidget(self.master_time_display)
        self.view_elapsed_time = True  # Toggle for elapsed vs remaining time display

        # Auto-fade toggle
        self.auto_fade_chk = QCheckBox("Auto-fade previous cues on new cue")
        self.auto_fade_chk.setChecked(self._auto_fade_enabled)
        self.auto_fade_chk.toggled.connect(self._on_toggle_auto_fade)
        layout.addWidget(self.auto_fade_chk)
        
        # Drag and drop toggle (start with gestures enabled, dragging disabled)
        self.drag_enabled_chk = QCheckBox("Enable button dragging (gestures disabled)")
        self.drag_enabled_chk.setChecked(False)
        self.drag_enabled_chk.toggled.connect(self._on_toggle_drag)
        layout.addWidget(self.drag_enabled_chk)
        
        # Initialize the drag/gesture states (False = dragging disabled, gestures enabled)
        self._on_toggle_drag(False)

        self.bank = ButtonBankWidget(rows=3, cols=8, engine_adapter=self.engine_adapter)
        layout.addWidget(self.bank)

        # Connect to engine adapter signals instead of polling queue directly
        # (EngineAdapter handles all event routing via Qt signals)
        self.engine_adapter.cue_finished.connect(self._on_cue_finished)
        self.engine_adapter.cue_time.connect(self._on_master_time_update)
        self.engine_adapter.master_levels.connect(self._on_master_levels_update)
        
        log_action = QAction("Logging Settings", self)
        log_action.triggered.connect(self.open_logging_dialog)
        self.menuBar().addAction(log_action)

    def resizeEvent(self, event):
        """Handle window resize - layout will manage meter sizing automatically."""
        super().resizeEvent(event)
        # Just trigger an update on the meters to redraw at new size
        try:
            self.left_meter.update()
            self.right_meter.update()
        except Exception as e:
            print(f"[MainWindow.resizeEvent] Error: {e}")

    def closeEvent(self, event):
        """Clean shutdown of audio service when window closes."""
        try:
            # Send None to signal audio service to stop
            self._audio_cmd_q.put(None)
            # Wait for service to finish
            self._audio_service.join(timeout=2.0)
            # Terminate if still running
            if self._audio_service.is_alive():
                self._audio_service.terminate()
                self._audio_service.join(timeout=1.0)
        except Exception:
            pass
        event.accept()


    def _on_play_cmd(self, cmd: PlayCueCommand) -> None:
        """Route play command to audio service."""
        try:
            self._audio_cmd_q.put(cmd)
            self.status.setText(f"Playing: {cmd.file_path}")
        except Exception:
            self.status.setText("Error: Could not send play command")

    def _on_toggle_auto_fade(self, enabled: bool) -> None:
        """Send auto-fade toggle to audio service via engine adapter."""
        try:
            self._auto_fade_enabled = enabled
            # Send SetAutoFadeCommand through engine adapter (proper boundary layer)
            self.engine_adapter.set_auto_fade(enabled)
            if enabled:
                self.status.setText("Mode: auto-fade on new cue")
            else:
                self.status.setText("Mode: layered (no auto-fade)")
        except Exception as e:
            self.status.setText(f"Error setting auto-fade: {e}")
            print(f"[MainWindow._on_toggle_auto_fade] Error: {e}")
    
    def _on_toggle_drag(self, enabled: bool) -> None:
        """Toggle drag/drop and gestures - they are mutually exclusive."""
        from ui.widgets.sound_file_button import SoundFileButton
        SoundFileButton.drag_enabled = enabled
        SoundFileButton.gesture_enabled = not enabled  # Inverse: if drag enabled, gesture disabled
        if enabled:
            self.status.setText("Mode: Button dragging ENABLED (gestures disabled)")
        else:
            self.status.setText("Mode: Swipe gestures ENABLED (dragging disabled)")
        
    def _on_master_time_update(self, cue_id: str, elapsed: float, remaining: float, total: Optional[float]) -> None:
        """Update master time display with remaining and elapsed time optional"""
        if self.view_elapsed_time:
            minutes = int(elapsed // 60)
            seconds = int(elapsed % 60)
            milliseconds = int((elapsed - int(elapsed)) * 1000)
            self.master_time_display.setText(f"Master Time: {minutes:02}:{seconds:02}.{milliseconds:03}")
        else:   
            minutes = int(remaining // 60)
            seconds = int(remaining % 60)
            milliseconds = int((remaining - int(remaining)) * 1000)
            self.master_time_display.setText(f"Master Time: -{minutes:02}:{seconds:02}.{milliseconds:03}")
    
    def _on_master_levels_update(self, rms_db: list, peak_db: list) -> None:
        """Update master output level meters with per-channel levels in dB."""
        try:
            # rms_db and peak_db are lists with one entry per channel
            # For stereo: rms_db = [-6.5, -7.2], peak_db = [-3.0, -4.1]
            if len(rms_db) >= 1:
                self.left_meter.setValue(rms_db[0], peak_db[0])
            if len(rms_db) >= 2:
                self.right_meter.setValue(rms_db[1], peak_db[1])
        except Exception as e:
            print(f"[MainWindow._on_master_levels_update] Error: {e}")

    def _on_cue_finished(self, cue_id: str, cue_info: object, reason: str) -> None:
        """Handle CueFinishedEvent from engine adapter."""
        self.status.setText(f"Finished cue {cue_id[:8]} ({reason})")
        
        # Log to Excel if we have cue_info
        if cue_info is not None:
            try:
                from engine.messages.events import CueFinishedEvent
                evt = CueFinishedEvent(cue_info=cue_info, reason=reason)
                self.cue_logger.on_cue_finished(evt)
            except Exception:
                pass
                
    def open_logging_dialog(self):
        self._logging_dialog = Log_Settings_Window(650, 360, excel_logger=self.save_to_excel)
        self._logging_dialog.show()
    
    def _on_log_entry_added(self, log_data: dict) -> None:
        """Refresh the logging dialog when a new entry is logged to Excel."""
        if self._logging_dialog and self._logging_dialog.isVisible():
            self._logging_dialog.refresh()
