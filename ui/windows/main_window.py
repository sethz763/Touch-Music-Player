from __future__ import annotations

import multiprocessing as mp
import os
import time
from typing import Optional, TYPE_CHECKING
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QLabel, QCheckBox, QHBoxLayout, QFileDialog, QMessageBox
from PySide6.QtCore import QTimer, QThread, Signal, QEvent, Qt
from PySide6.QtGui import QAction, QFont

if TYPE_CHECKING:
    from engine.commands import PlayCueCommand


from ui.windows.log_dialogue import Log_Settings_Window
from ui.widgets.bank_selector_widget import BankSelectorWidget
from ui.widgets.AudioLevelMeterHorizontal_LR import AudioLevelMeterHorizontal
from ui.widgets.PlayControls import PlayControls
from engine.audio_service import audio_service_main, AudioServiceConfig
from gui.engine_adapter import EngineAdapter

from log.cue_logger import CueLogger
from log.async_csv_excel_logger import AsyncCsvExcelLogger
from log.log_manager import LogManager
from engine.messages.events import CueFinishedEvent
from log.log import Log
import datetime
from persistence.SaveSettings import SaveSettings

class MainWindow(QMainWindow):
    log_signal = Signal(dict, str, datetime)
    
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Touch Music Player — Step D")

        # Create queues for communication with audio service process
        ctx = mp.get_context("spawn")
        self._audio_cmd_q = ctx.Queue()
        self._audio_evt_q = ctx.Queue()
        
        # Load persisted application settings.
        app_settings = {}
        try:
            app_settings = SaveSettings("Settings.json").get_settings() or {}
        except Exception:
            app_settings = {}

        # Audio service configuration
        # Fade defaults can be overridden by persisted Settings.json (legacy settings window).
        fade_in_ms = 100
        fade_out_ms = 1000
        try:
            if "play_fade_dur" in app_settings:
                fade_in_ms = int(app_settings["play_fade_dur"])
            elif "fade_in_duration" in app_settings:
                fade_in_ms = int(app_settings["fade_in_duration"])
            if "pause_fade_dur" in app_settings:
                fade_out_ms = int(app_settings["pause_fade_dur"])
            elif "fade_out_duration" in app_settings:
                fade_out_ms = int(app_settings["fade_out_duration"])
        except Exception:
            pass
        
        audio_config = AudioServiceConfig(
            sample_rate=48000,
            channels=2,
            block_frames=2048,
            fade_in_ms=fade_in_ms,
            fade_out_ms=fade_out_ms,
            fade_curve="equal_power",
            auto_fade_on_new=True,
            parent_pid=os.getpid(),
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

        # Apply persisted main output device (if present) after adapter is ready.
        try:
            main_out = app_settings.get("Main_Output")
            if isinstance(main_out, (list, tuple)):
                # new schema: [index, name, hostapi, sample_rate]
                if len(main_out) >= 4 and main_out[0] is not None:
                    device_index = main_out[0]
                    sample_rate = int(main_out[3])
                    self.engine_adapter.set_output_device(device_index)
                    self.engine_adapter.set_output_config(sample_rate=sample_rate, channels=2, block_frames=2048)
                # old schema: [name, hostapi, sample_rate]
                elif len(main_out) >= 3:
                    device_name = main_out[0]
                    sample_rate = int(main_out[2])
                    self.engine_adapter.set_output_device(device_name)
                    self.engine_adapter.set_output_config(sample_rate=sample_rate, channels=2, block_frames=2048)
        except Exception:
            pass
        
        # Track auto-fade state in GUI for toggle
        self._auto_fade_enabled = audio_config.auto_fade_on_new
        
        # Create LogManager (central logging hub)
        self.log_manager = LogManager()

        # Apply logging settings from log_settings.json
        settings = {}
        try:
            settings = SaveSettings('log_settings.json').get_settings() or {}
        except Exception:
            settings = {}

        xlsx_path = settings.get("filename") or "cue_log.xlsx"
        title = settings.get("show_name") or "Cue Log"
        enabled = bool(settings.get("logging_enabled", True))
        try:
            base, _ext = os.path.splitext(xlsx_path)
            csv_path = base + ".csv"
        except Exception:
            csv_path = "cue_log.csv"

        # Create async CSV+Excel logger (writes CSV immediately; batches XLSX saves off the GUI thread)
        self.save_to_excel = AsyncCsvExcelLogger(
            csv_path=csv_path,
            xlsx_path=xlsx_path,
            title=title,
            enabled=enabled,
            parent=self,
        )
        
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

        # Row directly under meters:
        # - Left: labels + checkboxes
        # - Right: play controls
        under_meters_row = QHBoxLayout()
        under_meters_row.setContentsMargins(5, 0, 5, 5)
        under_meters_row.setSpacing(10)

        labels_and_toggles = QVBoxLayout()
        labels_and_toggles.setContentsMargins(0, 0, 0, 0)
        labels_and_toggles.setSpacing(4)

        self.status = QLabel("Ready")
        # labels_and_toggles.addWidget(self.status)

        self.master_time_display = QLabel("00:00.000")
        self.master_time_display.setFont(QFont('Courier', 20))
        # Click to toggle elapsed vs remaining.
        self.master_time_display.installEventFilter(self)
        labels_and_toggles.addWidget(self.master_time_display)
        self.view_elapsed_time = True  # Toggle for elapsed vs remaining time display
        self._last_master_time: Optional[tuple[str, float, float, Optional[float]]] = None

        # Drag and drop toggle (start with gestures enabled, dragging disabled)
        self.drag_enabled_chk = QCheckBox("Enable button dragging (gestures disabled)")
        self.drag_enabled_chk.setChecked(False)
        self.drag_enabled_chk.toggled.connect(self._on_toggle_drag)
        labels_and_toggles.addWidget(self.drag_enabled_chk)

        under_meters_row.addLayout(labels_and_toggles, 1)

        # Restore persisted grid size (rows/cols).
        try:
            persisted_rows = int(app_settings.get("rows", 3))
        except Exception:
            persisted_rows = 3
        try:
            persisted_cols = int(app_settings.get("columns", 8))
        except Exception:
            persisted_cols = 8

        # Create the bank selector early so PlayControls can wire Next/Loop handlers
        # (layout placement still happens below)
        self.bank = BankSelectorWidget(banks=10, rows=persisted_rows, cols=persisted_cols, engine_adapter=self.engine_adapter)

        self.play_controls = PlayControls(50, 400)
        self.play_controls.transport_play.connect(self.engine_adapter.transport_play)
        self.play_controls.transport_pause.connect(self.engine_adapter.transport_pause)
        self.play_controls.transport_stop.connect(self.engine_adapter.transport_stop)
        # Next is a GUI concern (button grid context), not an engine concern
        self.play_controls.transport_next.connect(self.bank.transport_next)
        # Loop + override: global loop state always goes to engine; per-cue updates only when override is OFF
        self.play_controls.loop_enabled_toggled.connect(self._on_loop_button_toggled)
        self.play_controls.loop_override_toggled.connect(self._on_loop_override_toggled)
        # Auto-fade toggle (replaces checkbox)
        self.play_controls.auto_fade_toggled.connect(self._on_toggle_auto_fade)
        try:
            self.play_controls.cue_mode_button.setChecked(self._auto_fade_enabled)
        except Exception:
            pass
        under_meters_row.addWidget(self.play_controls, 0)

        layout.addLayout(under_meters_row)
        
        # Initialize the drag/gesture states (False = dragging disabled, gestures enabled)
        self._on_toggle_drag(False)
        layout.addWidget(self.bank)

        # Optional Stream Deck XL integration (hardware I/O stays off the Qt thread).
        self._streamdeck = None
        try:
            from gui.streamdeck_xl import StreamDeckXLBridge

            self._streamdeck = StreamDeckXLBridge(
                bank_selector=self.bank,
                engine_adapter=self.engine_adapter,
                play_controls=self.play_controls,
                mode=StreamDeckXLBridge.BankMode.SYNC,
                parent=self,
            )
            self._streamdeck._show_corner_label = True
            self._streamdeck.start()
        except Exception:
            self._streamdeck = None

        # Connect to engine adapter signals instead of polling queue directly
        # (EngineAdapter handles all event routing via Qt signals)
        self.engine_adapter.cue_finished.connect(self._on_cue_finished)
        self.engine_adapter.cue_time.connect(self._on_master_time_update)
        self.engine_adapter.master_levels.connect(self._on_master_levels_update)
        
        # Menus
        file_menu = self.menuBar().addMenu("File")
        save_project_action = QAction("Save Project...", self)
        save_project_action.triggered.connect(self.save_project)
        file_menu.addAction(save_project_action)

        load_project_action = QAction("Load Project...", self)
        load_project_action.triggered.connect(self.load_project)
        file_menu.addAction(load_project_action)

        log_action = QAction("Logging Settings", self)
        log_action.triggered.connect(self.open_logging_dialog)
        self.menuBar().addAction(log_action)

        setting_action = QAction("Settings", self)
        setting_action.triggered.connect(self.open_settings_dialog)
        self.menuBar().addAction(setting_action)

    def _collect_project_dict(self) -> dict:
        """Collect a user-saveable project snapshot."""
        try:
            if getattr(self, "bank", None) is not None:
                self.bank.flush_persistence()
        except Exception:
            pass

        try:
            app_settings = SaveSettings("Settings.json").get_settings() or {}
        except Exception:
            app_settings = {}

        try:
            button_settings = SaveSettings("ButtonSettings.json").get_settings() or {}
        except Exception:
            button_settings = {}

        try:
            log_settings = SaveSettings("log_settings.json").get_settings() or {}
        except Exception:
            log_settings = {}

        return {
            "schema": 1,
            "settings": app_settings,
            "button_settings": button_settings,
            "log_settings": log_settings,
        }

    def save_project(self) -> None:
        """Save a portable project JSON containing app/button/log settings."""
        filename, _filter = QFileDialog.getSaveFileName(
            self,
            "Save Project",
            "",
            "Project Files (*.json)",
        )
        if not filename:
            return

        project_dict = self._collect_project_dict()
        try:
            store = SaveSettings(filename)
            store.replace_settings(project_dict, save=True)
            QMessageBox.information(self, "Project Saved", f"Saved project to:\n{filename}")
        except Exception as e:
            QMessageBox.warning(self, "Save Failed", f"Failed to save project:\n{e}")

    def load_project(self) -> None:
        """Load a project JSON and apply settings immediately where possible."""
        filename, _filter = QFileDialog.getOpenFileName(
            self,
            "Load Project",
            "",
            "Project Files (*.json)",
        )
        if not filename:
            return

        try:
            project = SaveSettings(filename).get_settings() or {}
        except Exception as e:
            QMessageBox.warning(self, "Load Failed", f"Failed to read project file:\n{e}")
            return

        app_settings = project.get("settings") if isinstance(project, dict) else None
        button_settings = project.get("button_settings") if isinstance(project, dict) else None
        log_settings = project.get("log_settings") if isinstance(project, dict) else None

        if not isinstance(app_settings, dict) or not isinstance(button_settings, dict) or not isinstance(log_settings, dict):
            QMessageBox.warning(self, "Load Failed", "Invalid project file (missing settings sections).")
            return

        # Persist into the app's normal storage locations.
        try:
            SaveSettings("Settings.json").replace_settings(app_settings, save=True)
        except Exception:
            pass
        try:
            SaveSettings("ButtonSettings.json").replace_settings(button_settings, save=True)
        except Exception:
            pass
        try:
            SaveSettings("log_settings.json").replace_settings(log_settings, save=True)
        except Exception:
            pass

        # Apply button settings immediately.
        try:
            if getattr(self, "bank", None) is not None:
                self.bank.load_button_settings(button_settings)
        except Exception:
            pass

        # Apply logging settings immediately.
        try:
            xlsx_path = log_settings.get("filename") or "cue_log.xlsx"
            title = log_settings.get("show_name") or "Cue Log"
            enabled = bool(log_settings.get("logging_enabled", True))
            if getattr(self, "save_to_excel", None) is not None:
                self.save_to_excel.set_logging_enabled(enabled)
                try:
                    self.save_to_excel.set_title(title)
                except Exception:
                    pass
                # Switch to the project's current log files (do not clear).
                self.save_to_excel.load(xlsx_path)
        except Exception:
            pass

        # If the logging dialog is open, update its fields.
        try:
            if getattr(self, "_logging_dialog", None) is not None and self._logging_dialog.isVisible():
                try:
                    self._logging_dialog.load_from_settings()
                    self._logging_dialog.refresh()
                except Exception:
                    pass
        except Exception:
            pass

        QMessageBox.information(self, "Project Loaded", f"Loaded project from:\n{filename}")

    def _on_loop_button_toggled(self, enabled: bool) -> None:
        """Update engine global loop state; optionally apply per-cue loop when not overriding."""
        try:
            self.engine_adapter.set_global_loop_enabled(bool(enabled))
        except Exception:
            pass

        # If override is enabled, do NOT mutate per-cue loop settings.
        try:
            override_on = bool(self.play_controls.loop_overide_checkbox.isChecked())
        except Exception:
            override_on = False

        if not override_on:
            try:
                self.bank.transport_set_loop_for_active(bool(enabled))
            except Exception:
                pass

    def _on_loop_override_toggled(self, enabled: bool) -> None:
        """Enable/disable engine loop override; keep engine global state in sync with UI button."""
        try:
            self.engine_adapter.set_loop_override(bool(enabled))
        except Exception:
            pass

        # Re-send current loop button state so override immediately applies correct on/off.
        try:
            self.engine_adapter.set_global_loop_enabled(bool(self.play_controls.loop_button.isChecked()))
        except Exception:
            pass

    def resizeEvent(self, event):
        """Handle window resize - layout will manage meter sizing automatically."""
        super().resizeEvent(event)
        # Just trigger an update on the meters to redraw at new size
        try:
            self.left_meter.update()
            self.right_meter.update()
        except Exception as e:
            print(f"[MainWindow.resizeEvent] Error: {e}")

    def eventFilter(self, watched, event):
        try:
            if watched is self.master_time_display and event.type() == QEvent.MouseButtonRelease:
                if getattr(event, "button", None) is not None and event.button() == Qt.LeftButton:
                    self.view_elapsed_time = not self.view_elapsed_time
                    # Refresh immediately using the most recent update.
                    if self._last_master_time is not None:
                        cue_id, elapsed, remaining, total = self._last_master_time
                        self._on_master_time_update(cue_id, elapsed, remaining, total)
                    return True
        except Exception:
            pass
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        """Clean shutdown of audio service when window closes."""
        try:
            if getattr(self, "_streamdeck", None) is not None:
                self._streamdeck.stop()
        except Exception:
            pass
        try:
            # Flush any pending debounced button persistence.
            if getattr(self, "bank", None) is not None:
                self.bank.flush_persistence()
        except Exception:
            pass
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

        try:
            # Flush/stop async logger process
            if getattr(self, "save_to_excel", None) is not None:
                self.save_to_excel.close(timeout_s=2.0)
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
        self._last_master_time = (cue_id, elapsed, remaining, total)
        if self.view_elapsed_time:
            minutes = int(elapsed // 60)
            seconds = int(elapsed % 60)
            milliseconds = int((elapsed - int(elapsed)) * 1000)
            self.master_time_display.setText(f"{minutes:02}:{seconds:02}.{milliseconds:03}")
        else:   
            # Prefer duration-based remaining when available.
            rem = remaining
            try:
                if isinstance(total, (int, float)):
                    rem = max(0.0, float(total) - float(elapsed))
            except Exception:
                rem = remaining
            minutes = int(rem // 60)
            seconds = int(rem % 60)
            milliseconds = int((rem - int(rem)) * 1000)
            self.master_time_display.setText(f"{minutes:02}:{seconds:02}.{milliseconds:03}")
    
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
        start = time.perf_counter()
        try:
            t0 = time.perf_counter()
            self.status.setText(f"Finished cue {cue_id[:8]} ({reason})")
            status_ms = (time.perf_counter() - t0) * 1000

            cue_logger_ms = 0.0
            # Log to Excel if we have cue_info
            if cue_info is not None:
                try:
                    from engine.messages.events import CueFinishedEvent
                    evt = CueFinishedEvent(cue_info=cue_info, reason=reason)
                    t1 = time.perf_counter()
                    self.cue_logger.on_cue_finished(evt)
                    cue_logger_ms = (time.perf_counter() - t1) * 1000
                except Exception:
                    pass

            total_ms = (time.perf_counter() - start) * 1000
            if total_ms > 2.0 or cue_logger_ms > 2.0 or status_ms > 2.0:
                print(
                    f"[PERF] MainWindow._on_cue_finished: {total_ms:.2f}ms cue={cue_id[:8]}"
                    f" status={status_ms:.2f}ms cue_logger={cue_logger_ms:.2f}ms reason={reason}"
                )
        except Exception:
            pass
                
    def open_logging_dialog(self):
        self._logging_dialog = Log_Settings_Window(650, 360, excel_logger=self.save_to_excel)
        # Wire dialog actions to the logger proxy
        try:
            self._logging_dialog.create_sheet_signal.connect(self.save_to_excel.start_new_log)
            self._logging_dialog.load_sheet_signal.connect(self.save_to_excel.load)
            self._logging_dialog.clear_sheet_signal.connect(self.save_to_excel.clear_sheet)
            self._logging_dialog.enable_disable_logging_signal.connect(self.save_to_excel.set_logging_enabled)
        except Exception:
            pass
        self._logging_dialog.show()
        
    def open_settings_dialog(self):
        from ui.windows.settings_window import SettingsWindow
        try:
            if getattr(self, "_settings_dialog", None) is None:
                self._settings_dialog = SettingsWindow(
                    self,
                    height=500,
                    width=400,
                    pause=1000,
                    play=100,
                    engine_adapter=self.engine_adapter,
                )
            self._settings_dialog.show()
            try:
                self._settings_dialog.raise_()
                self._settings_dialog.activateWindow()
            except Exception:
                pass
        except Exception:
            pass
    
    def _on_log_entry_added(self, log_data: dict) -> None:
        """Refresh the logging dialog when a new entry is logged to Excel."""
        if self._logging_dialog and self._logging_dialog.isVisible():
            self._logging_dialog.refresh()
