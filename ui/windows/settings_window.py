#settings
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget, QHBoxLayout, QSpacerItem, QRadioButton, QSlider, QLabel, QComboBox, QMainWindow, QLineEdit, QSpinBox, QMessageBox, QCheckBox
from PySide6.QtGui import QFont
from PySide6 import QtCore
from PySide6 import QtWidgets
from PySide6.QtCore import QThread

from PySide6.QtMultimedia import QMediaDevices

from PySide6.QtCore import QObject, Signal, Qt

from persistence.SaveSettings import SaveSettings
from legacy.CheckSoundDevices import CheckSoundDevices

from gui.engine_adapter import EngineAdapter

import sounddevice as sd

from inspect import currentframe, getframeinfo


class SettingSignals(QObject):
    change_rows_and_columns_signal = Signal(int, int)
    main_output_signal = Signal(int, float)
    editor_output_signal = Signal(int, float)

class SettingsWindow(QWidget):
    restart_output_signal = Signal()
    refresh_sound_devices_signal = Signal()
    
    save_output_settings_signal = Signal(dict)
    
    def __init__(
        self,
        parent: QWidget,
        height: int = 500,
        width: int = 450,
        pause: int = 1000,
        play: int = 100,
        *args,
        **kwargs,
    ):
        engine_adapter: Optional[EngineAdapter] = kwargs.pop("engine_adapter", None)
        super().__init__(*args, **kwargs)
        try:
            self.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Fixed,
                QtWidgets.QSizePolicy.Policy.Fixed
            )
            self.parent = parent
            self.setWindowFlags(QtCore.Qt.WindowType.WindowStaysOnTopHint)
            self.setWindowTitle('Settings')

            self.setFixedSize(width, height)
            
            self.sample_rates = (48000.0, 44100.0, 32000.0, 22040.0, 16000.0, 11025.0, 8000.0)
            self.acceptable_apis = ['MME', 'Windows DirectSound', 'ASIO', 'Windows WASAPI']

            self.settings = SaveSettings("Settings.json")
            self.app_settings = self.settings.get_settings()
            self.settings_signals = SettingSignals()

            # Boundary layer to audio service (queue-based). Optional for now.
            self.engine_adapter: Optional[EngineAdapter] = engine_adapter
  
            self.fade_out_dur = int(pause)
            self.fade_in_dur = int(play)
            
            self.sample_rate = 48000
            
            self.usable_devices:dict = {}
            self.show_all_devices: bool = False  # User preference: show all devices vs. filtered
            try:
                self.devices = sd.query_devices()
            except Exception:
                self.devices = []
            try:
                self.apis = sd.query_hostapis()
            except Exception:
                self.apis = ()

            # Guard to prevent signal handlers from firing during initial population/restore.
            self._initializing = True

            self.main_layout = QVBoxLayout()
            self.setLayout(self.main_layout)
            self.slider_layout = QHBoxLayout()
            self.v_layoutL = QVBoxLayout()
            self.v_layoutM = QVBoxLayout()
            self.v_layoutR = QVBoxLayout()
            crossfade_label = QLabel('Crossfade Duration Settings')
            self.main_layout.addWidget(crossfade_label)

            self.main_layout.addLayout(self.slider_layout)
            
            self.slider_layout.addLayout(self.v_layoutL)
            self.slider_layout.addLayout(self.v_layoutM)
            self.slider_layout.addLayout(self.v_layoutR)

            self.spacer = QSpacerItem(100, 300, QtWidgets.QSizePolicy.Policy.Fixed)

            slider_height = 20

            self.fade_out_label = QLabel('FADE\nOUT')
            self.fade_out_label.setFont(QFont('arial', 6))
            self.fade_out_line_edit = QLineEdit('0')
            self.fade_out_line_edit.setFixedSize(30,25)
            self.fade_out_line_edit.setStyleSheet('border: 1px solid black;')
            self.fade_out_line_edit.returnPressed.connect(self.fade_out_line_edit_handler)
            self.fade_out_slider = QSlider(QtCore.Qt.Orientation.Horizontal)
            self.fade_out_slider.setFixedHeight(slider_height)
            self.fade_out_slider.setRange(1,2000)
            self.fade_out_slider.setValue(self.fade_out_dur)
            self.fade_out_slider.valueChanged.connect(self.update_fade_out_dur)
            self.fade_out_slider.sliderReleased.connect(self._send_transition_fade_settings)

        
            self.fade_in_label = QLabel('FADE\nIN')
            self.fade_in_label.setFont(QFont('arial', 6))
            self.fade_in_line_edit = QLineEdit('0')
            self.fade_in_line_edit.setFixedSize(30,25)
            self.fade_in_line_edit.setStyleSheet('border: 1px solid black;')
            self.fade_in_line_edit.returnPressed.connect(self.fade_in_line_edit_handler)
            self.fade_in_slider = QSlider(QtCore.Qt.Orientation.Horizontal)
            self.fade_in_slider.setFixedHeight(slider_height)
            self.fade_in_slider.setRange(1,2000)
            self.fade_in_slider.valueChanged.connect(self.update_fade_in_dur)
            self.fade_in_slider.setValue(self.fade_in_dur)
            self.fade_in_slider.sliderReleased.connect(self._send_transition_fade_settings)

            self.v_layoutL.addWidget(self.fade_in_label)
            self.v_layoutM.addWidget(self.fade_in_line_edit)
            self.v_layoutR.addWidget(self.fade_in_slider)
            
            self.v_layoutL.addWidget(self.fade_out_label)
            self.v_layoutM.addWidget(self.fade_out_line_edit)
            self.v_layoutR.addWidget(self.fade_out_slider)


            spacer = QSpacerItem(20,70)

            self.main_layout.addItem(spacer)

            self.refresh_layout = QHBoxLayout()
            self.refresh_outputs_button = QPushButton('REFRESH OUTPUT COMBO BOXES')
            self.refresh_outs_label = QLabel('Note: Output change or refresh stops output')
            self.refresh_outs_label.setFont(QFont('Arial', 8))
            self.refresh_layout.addWidget(self.refresh_outputs_button)
            self.refresh_layout.addWidget(self.refresh_outs_label)
            self.main_layout.addLayout(self.refresh_layout)
            self.refresh_outputs_button.clicked.connect(self.refresh_devices)
            
            # Lazily initialized on refresh to avoid extra threads and PortAudio churn on open.
            self.check_sound_devices_thread = None
            self.check_sound_devices = None

            # Checkbox to show all devices (including virtual/Dante)
            device_filter_layout = QHBoxLayout()
            self.show_all_devices_checkbox = QCheckBox('Show All Devices (including Virtual)')
            self.show_all_devices_checkbox.setChecked(False)
            self.show_all_devices_checkbox.stateChanged.connect(self._on_show_all_devices_changed)
            device_filter_label = QLabel('Device Filter')
            device_filter_layout.addWidget(device_filter_label)
            device_filter_layout.addWidget(self.show_all_devices_checkbox)
            self.main_layout.addLayout(device_filter_layout)

            main_output_label = QLabel('Main Output')
            self.main_layout.addWidget(main_output_label)

            self.audio_output_combo = QComboBox()
            self.audio_output_combo.setFixedWidth(400)
            self.audio_output_combo.addItem('MAIN AUDIO OUTPUT')
            self.main_layout.addWidget(self.audio_output_combo, alignment=QtCore.Qt.AlignmentFlag.AlignHCenter)

            editor_label = QLabel('Editor Output')
            self.main_layout.addWidget(editor_label, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)
            self.editor_audio_output_combo = QComboBox()
            self.editor_audio_output_combo.setFixedWidth(400)
            self.editor_audio_output_combo.addItem('EDITOR AUDIO OUTPUT')
            self.main_layout.addWidget(self.editor_audio_output_combo, alignment=QtCore.Qt.AlignmentFlag.AlignHCenter)
            self.populate_audio_combo_box()
            self.audio_output_combo.currentIndexChanged.connect(self.main_output_changed)
            self.editor_audio_output_combo.currentIndexChanged.connect(self.editor_output_changed)
            self.main_output_device = {}
            self.editor_output_device = {}

            self.button_banks_settings_layout = QHBoxLayout()
            self.button_banks_setting_label = QLabel('Adjust Number of Rows and Columns')
            self.rows_label = QLabel('Rows:')
            self.columns_label = QLabel('Columns:')
            self.rows_spinbox = QSpinBox()
            self.rows_spinbox.setFixedSize(60, 30)
            font = QFont('Arial', 12)
            self.rows_spinbox.setFont(font)
            self.rows_spinbox.setRange(1, 5)
            self.columns_spinbox = QSpinBox()
            self.columns_spinbox.setFixedSize(60, 30)
            self.columns_spinbox.setFont(font)
            self.columns_spinbox.setRange(1,10)
            # New UI uses MainWindow.bank (ButtonBankWidget). Legacy uses buttonBanksWidget.
            try:
                self.rows_spinbox.setValue(int(getattr(getattr(self.parent, "bank", None), "rows", 3)))
            except Exception:
                try:
                    self.rows_spinbox.setValue(int(getattr(getattr(self.parent, "buttonBanksWidget", None), "rows", 3)))
                except Exception:
                    self.rows_spinbox.setValue(3)

            try:
                self.columns_spinbox.setValue(int(getattr(getattr(self.parent, "bank", None), "cols", 8)))
            except Exception:
                try:
                    self.columns_spinbox.setValue(int(getattr(getattr(self.parent, "buttonBanksWidget", None), "columns", 8)))
                except Exception:
                    self.columns_spinbox.setValue(8)
            self.rows_spinbox.valueChanged.connect(self.change_row_columns)
            self.columns_spinbox.valueChanged.connect(self.change_row_columns)
            self.main_layout.addWidget(self.button_banks_setting_label)
            row_columns_spacer = QSpacerItem(300, 10)
            # self.button_banks_settings_layout.addItem(row_columns_spacer)
            self.button_banks_settings_layout.addWidget(self.rows_label)
            self.button_banks_settings_layout.addWidget(self.rows_spinbox)
            self.button_banks_settings_layout.addWidget(self.columns_label)
            self.button_banks_settings_layout.addWidget(self.columns_spinbox)
            self.button_banks_settings_layout.addItem(row_columns_spacer)
            self.main_layout.addLayout(self.button_banks_settings_layout)

            self.restore_saved_settings()

            self.update_fade_out_dur()
            self.update_fade_in_dur()

            # Ensure engine receives the currently loaded transition fades.
            self._send_transition_fade_settings()

            self._initializing = False
            
        except Exception as e:
            print(e)

    def restore_saved_settings(self):
        try:
            #recall settings
            if 'pause_fade_dur' in self.app_settings:
                self.fade_out_dur = int(self.app_settings['pause_fade_dur'])
                self.fade_out_slider.setValue(self.fade_out_dur)
                self.fade_out_line_edit.setText(str(self.fade_out_dur))
            elif 'fade_out_duration' in self.app_settings:
                self.fade_out_dur = int(self.app_settings['fade_out_duration'])
                self.fade_out_slider.setValue(self.fade_out_dur)
                self.fade_out_line_edit.setText(str(self.fade_out_dur))

            if 'play_fade_dur' in self.app_settings:
                self.fade_in_dur = int(self.app_settings['play_fade_dur'])
                self.fade_in_slider.setValue(self.fade_in_dur)
                self.fade_in_line_edit.setText(str(self.fade_in_dur))
            elif 'fade_in_duration' in self.app_settings:
                self.fade_in_dur = int(self.app_settings['fade_in_duration'])
                self.fade_in_slider.setValue(self.fade_in_dur)
                self.fade_in_line_edit.setText(str(self.fade_in_dur))

            # ------------------------
            # Output restore w/ fallback
            # ------------------------

            def _select_output(combo: QComboBox, saved: object, label: str) -> dict:
                """Select saved output if available; else fall back and warn."""
                saved_name = None
                saved_hostapi = None
                saved_index = None

                if isinstance(saved, (list, tuple)) and saved:
                    # Backward compat:
                    # - old: [name, hostapi_name, sample_rate]
                    # - new: [index, name, hostapi_name, sample_rate]
                    if len(saved) >= 4:
                        saved_index = saved[0]
                        saved_name = saved[1]
                        saved_hostapi = saved[2]
                    elif len(saved) >= 2:
                        saved_name = saved[0]
                        saved_hostapi = saved[1]

                def _find_by_index(idx: int | None) -> int:
                    if idx is None:
                        return -1
                    try:
                        idx = int(idx)
                    except Exception:
                        return -1
                    for i in range(1, combo.count()):
                        try:
                            d = combo.itemData(i)
                            if isinstance(d, dict) and int(d.get('index')) == idx:
                                return i
                        except Exception:
                            continue
                    return -1

                def _find_by_text(name: str | None, hostapi: str | None) -> int:
                    if not name or not hostapi:
                        return -1
                    search = f"{name}, {hostapi}"
                    try:
                        return combo.findText(search, flags=Qt.MatchFlag.MatchContains)
                    except Exception:
                        return -1

                # Try to find the saved device
                selected = _find_by_index(saved_index)
                if selected < 1:
                    selected = _find_by_text(saved_name, saved_hostapi)

                missing_saved = False
                if selected < 1:
                    missing_saved = bool(saved_name)
                    # Fall back to system default output
                    try:
                        default_idx = sd.default.device[1]
                        default_dev = sd.query_devices(default_idx)
                        api = self.apis[default_dev['hostapi']] if self.apis else {"name": ""}
                        selected = combo.findText(default_dev['name'] + ', ' + api['name'], flags=Qt.MatchFlag.MatchContains)
                    except Exception:
                        selected = -1

                # Last resort: first available device
                if selected < 1 and combo.count() > 1:
                    selected = 1

                try:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(selected)
                finally:
                    combo.blockSignals(False)

                chosen = combo.itemData(selected) if selected >= 1 else {}
                if not isinstance(chosen, dict):
                    chosen = {}

                if missing_saved:
                    try:
                        QMessageBox.warning(
                            self,
                            "Output device unavailable",
                            f"The previously saved {label} output device is not available.\n\n"
                            f"Saved: {saved_name}, {saved_hostapi}\n"
                            f"Using: {chosen.get('name', 'Unknown')}, {chosen.get('hostapi_name', 'Unknown')}",
                        )
                    except Exception:
                        pass

                    # Persist fallback so next launch is consistent.
                    try:
                        if chosen.get('index') is not None:
                            self.settings.set_setting(label, [chosen.get('index'), chosen.get('name'), chosen.get('hostapi_name'), chosen.get('sample_rate')])
                            self.settings.save_settings()
                    except Exception:
                        pass

                return chosen

            # Restore selections (no engine restart on open)
            saved_main = self.app_settings.get('Main_Output')
            saved_editor = self.app_settings.get('Editor_Output')

            self.main_output_device = _select_output(self.audio_output_combo, saved_main, 'Main_Output')
            self.editor_output_device = _select_output(self.editor_audio_output_combo, saved_editor, 'Editor_Output')

            try:
                self.parent.main_output_device = self.main_output_device
            except Exception:
                pass
            try:
                self.parent.editor_output_device = self.editor_output_device
            except Exception:
                pass

            # (Editor output handled by _select_output above)

            if 'rows' in self.app_settings:
                rows = self.app_settings['rows']
                self.rows_spinbox.setValue(rows)

            if 'columns' in self.app_settings:
                columns = self.app_settings['columns']
                self.columns_spinbox.setValue(columns)
        except Exception as e:
            info = getframeinfo(currentframe())
            print(f'{e}{info.filename}:{info.lineno}')
            
    def restore_outputs(self):
        if 'Main_Output' in self.app_settings:
            device = self.app_settings['Main_Output']
            device_name = device[0]
            device_hostapi = device[1] #MME, WINDOWS DIRECT SOUND, ASIO etc only the index number is saved
            
            device_search_text = device_name + ', ' + device_hostapi
            
            selected_index = self.audio_output_combo.findText(device_search_text , flags=Qt.MatchFlag.MatchContains)
            self.audio_output_combo.setCurrentIndex(selected_index)
            self.main_output_device = device
            
        else:
            index = sd.default.device[1]
            device = sd.query_devices(index)
            api = self.apis[device['hostapi']]
            
            device_search_text = device['name'] + ', ' + api['name']
            
            selected_index = self.audio_output_combo.findText(device_search_text , flags=Qt.MatchFlag.MatchContains)
            self.audio_output_combo.setCurrentIndex(selected_index)
            self.main_output_device = device

        if 'Editor_Output' in self.app_settings:
            device = self.app_settings['Editor_Output']
            device_name = device[0]
            device_hostapi = device[1]  #MME, WINDOWS DIRECT SOUND, ASIO etc
            
            device_search_text = device_name + ', ' + device_hostapi
            selected_index = self.editor_audio_output_combo.findText(device_search_text , flags=Qt.MatchFlag.MatchContains)
            self.editor_audio_output_combo.setCurrentIndex(selected_index)
            self.editor_output_device = device
                    
        else:
            index = sd.default.device[1]
            device = sd.query_devices(index)
            api = self.apis[device['hostapi']]
            device_search_text = device['name'] + ', ' + api['name']
            selected_index = self.editor_audio_output_combo.findText(device_search_text , flags=Qt.MatchFlag.MatchContains)
            self.editor_audio_output_combo.setCurrentIndex(selected_index)
            self.editor_output_device = device

    def _is_real_device(self, device: dict, name: str) -> bool:
        """
        Filter out virtual devices and loopback endpoints.
        
        If show_all_devices is True, allow all devices with output.
        Otherwise, looks for common virtual/fake device patterns and whitelists pro audio.
        
        Returns True if device should be shown.
        """
        name_lower = name.lower()
        
        # Whitelist professional virtual audio devices (must come before exclusion patterns)
        whitelist_patterns = [
            'dante',           # Dante virtual audio
            'network audio',   # Generic network audio
            'madi',            # Multichannel Audio Digital Interface
            'aes67',           # AES67 audio networking
        ]
        for pattern in whitelist_patterns:
            if pattern in name_lower:
                return True  # Whitelist: allow pro audio networking
        
        # If show_all_devices is enabled, allow any device with output channels
        if self.show_all_devices:
            return device.get('max_output_channels', 0) > 0
        
        # Virtual/loopback patterns to exclude
        virtual_patterns = [
            'virtual',
            'loopback',
            'stereo mix',
            'what u hear',
            'wave out mix',
            'microphone',  # Exclude input devices
            'input',
            'mono',  # Prefer stereo
            'dummy',
            'none',
            'disabled',
            'cable',  # VB-Cable, VB-Audio, etc.
        ]
        
        for pattern in virtual_patterns:
            if pattern in name_lower:
                return False
        
        # Must have at least 2 output channels to be useful
        if device.get('max_output_channels', 0) < 2:
            return False
        
        # Device must not be marked as an input-only device
        if device.get('max_output_channels', 0) == 0:
            return False
        
        return True

    def populate_audio_combo_box(self):     
        try:
            self.audio_output_combo.clear()
            self.editor_audio_output_combo.clear()

            self.audio_output_combo.addItem('Main Audio Output')
            self.editor_audio_output_combo.addItem('Editor Audio Output')

            self.usable_devices.clear()
            
            for api in self.apis:
                if api['name'] in self.acceptable_apis:
                    for device_idx in api['devices']:
                        device = self.devices[device_idx]
                        device_name = device.get('name', '')
                        
                        # Filter: must be output device with 2+ channels and be "real"
                        if (device.get('max_output_channels', 0) > 0 and 
                            self._is_real_device(device, device_name)):
                            
                            # Test sample rate compatibility
                            for sample_rate in self.sample_rates:
                                try:
                                    sd.check_output_settings(device_idx, channels=2, samplerate=sample_rate)
                                    usable_device = self.devices[device_idx].copy()
                                    usable_device['sample_rate'] = sample_rate
                                    usable_device['hostapi_name'] = api['name']
                                    usable_device['index'] = device_idx
                                    self.usable_devices[device_idx] = usable_device
                                    break
                                
                                except:
                                    pass
            
            
            for device_idx in self.usable_devices:
                device = self.usable_devices[device_idx]
                api_idx = device.get('hostapi', 0)
                api = self.apis[api_idx]
                self.audio_output_combo.addItem(device['name'] +', ' + api['name'], userData=device)
                self.editor_audio_output_combo.addItem(device['name'] + ', ' + api['name'], userData=device)

        except Exception as e:
            info = getframeinfo(currentframe())
            print(f'{e}{info.filename}:{info.lineno}')
            
    def refresh_devices(self):
        self.refresh_outputs_button.setEnabled(False)
        # Kick off a safe refresh in a background thread (legacy helper does PortAudio re-init).
        try:
            if self.check_sound_devices_thread is None:
                self.check_sound_devices_thread = QThread(self)
                self.check_sound_devices = CheckSoundDevices()
                self.check_sound_devices.moveToThread(self.check_sound_devices_thread)
                self.check_sound_devices.device_list_signal.connect(self.re_populate_audio_combo_box)
                self.check_sound_devices_thread.start()
            self.check_sound_devices.get_devices()
        except Exception:
            self.refresh_outputs_button.setEnabled(True)
        
    def re_populate_audio_combo_box(self, device_list:list, api_dict:tuple):
        
        try:
            self.devices = device_list
            self.apis = api_dict 
            
            self.audio_output_combo.currentIndexChanged.disconnect(self.main_output_changed)
            self.editor_audio_output_combo.currentIndexChanged.disconnect(self.editor_output_changed)
            self.audio_output_combo.setEnabled(False)
            self.editor_audio_output_combo.setEnabled(False)
            
            self.populate_audio_combo_box()
            
            self.audio_output_combo.currentIndexChanged.connect(self.main_output_changed)
            self.editor_audio_output_combo.currentIndexChanged.connect(self.editor_output_changed)
            
            self.restore_outputs()
            
            self.audio_output_combo.setEnabled(True)
            self.editor_audio_output_combo.setEnabled(True)
            
            # self.restart_output_signal.emit()
            self.refresh_outputs_button.setEnabled(True)
            
        except Exception as e:
            info = getframeinfo(currentframe())
            print(f'{e}{info.filename}:{info.lineno}')
            
    def check_device_capabilites(self, device=sd):
        sample_rates = [8000.0, 11025.0, 16000.0, 22050.0, 32000.0, 44100.0, 48000.0] #96000.0
        supports_all_sample_rates = False
        for sample_rate in sample_rates:
            try:
                sd.check_output_settings(device=device['index'], samplerate=sample_rate)
                supports_all_sample_rates = True
            except:
                supports_all_sample_rates = False
                return supports_all_sample_rates
                
        return supports_all_sample_rates
            
    def main_output_changed(self):
        try:
            if getattr(self, "_initializing", False):
                return
            index = self.audio_output_combo.currentIndex()
            if index <= 0:
                return
            device = self.audio_output_combo.itemData(index)
            if not device:
                return
            
            self.main_output_device = device
            print(f'main output: {device}')
            self.sample_rate = device['sample_rate']

            # Persist new schema that includes index for reliable engine routing.
            self.settings.set_setting('Main_Output', [device.get('index'), device['name'], device['hostapi_name'], device['sample_rate']])
            self.settings_signals.main_output_signal.emit(self.main_output_device['index'], self.main_output_device['sample_rate'])
            self.parent.main_output_device = self.audio_output_combo.currentData()
            self.settings.save_settings()

            # Send to audio engine via queue boundary.
            if self.engine_adapter is not None:
                try:
                    self.engine_adapter.set_output_device(device.get('index'))
                except Exception:
                    pass
                try:
                    # Re-open stream with sample rate known-good for this device.
                    self.engine_adapter.set_output_config(
                        sample_rate=int(device['sample_rate']),
                        channels=2,
                        block_frames=2048,
                    )
                except Exception:
                    pass
        except Exception as e:
            info = getframeinfo(currentframe())
            print(f'{e}{info.filename}:{info.lineno}')
            
    def editor_output_changed(self):
        try:
            if getattr(self, "_initializing", False):
                return
            index = self.editor_audio_output_combo.currentIndex()
            if index <= 0:
                return
            device = self.editor_audio_output_combo.itemData(index)
            if not device:
                return
            
            self.editor_output_device = device

            # Persist new schema including index.
            self.settings.set_setting('Editor_Output', [device.get('index'), device['name'], device['hostapi_name'], device['sample_rate']])
            self.settings_signals.editor_output_signal.emit(device['index'], device['sample_rate'])
            self.parent.editor_output_device = self.editor_audio_output_combo.currentData()
            self.settings.save_settings()
        except Exception as e:
            info = getframeinfo(currentframe())
            print(f'{e}{info.filename}:{info.lineno}')

        
#
    def update_fade_out_dur(self):
        dur = self.fade_out_slider.value()
        self.fade_out_dur = dur
        self.fade_out_line_edit.setText(str(dur))

    def update_fade_in_dur(self):
        dur = self.fade_in_slider.value()
        self.fade_in_dur = dur
        self.fade_in_line_edit.setText(str(dur))

    def _send_transition_fade_settings(self):
        """Send current fade durations to the audio engine via queued command."""
        if self.engine_adapter is None:
            return
        try:
            self.engine_adapter.set_transition_fade_durations(
                fade_in_ms=int(self.fade_in_dur),
                fade_out_ms=int(self.fade_out_dur),
            )
        except Exception:
            pass

    def fade_in_line_edit_handler(self):
        text = self.fade_in_line_edit.text()
        try:
            t = int(text)
            self.fade_in_slider.setValue(t)
            self.fade_in_dur = t
            self._send_transition_fade_settings()

        except:
            self.fade_in_line_edit.setText('10')
            self.fade_in_slider.setValue(10)
    
    def fade_out_line_edit_handler(self):
        text = self.fade_out_line_edit.text()
        try:
            t = int(text)
            self.fade_out_slider.setValue(t)
            self.fade_out_dur=t
            self._send_transition_fade_settings()

        except:
            self.fade_out_line_edit.setText('10')
            self.fade_out_slider.setValue(10)

    def change_row_columns(self):
        banks=10
        rows=self.rows_spinbox.value()
        columns=self.columns_spinbox.value()
        self.settings_signals.change_rows_and_columns_signal.emit(rows, columns)

    def _on_show_all_devices_changed(self, state: int) -> None:
        """Handler for show_all_devices checkbox state change."""
        self.show_all_devices = bool(state)
        # Refresh device lists immediately
        self.refresh_devices()

    def closeEvent(self,event): 
        try:
            #save settings on closing the settings window
            # Maintain backward compatibility keys + the newer keys used by this window.
            self.settings.set_setting('fade_in_duration', int(self.fade_in_dur))
            self.settings.set_setting('fade_out_duration', int(self.fade_out_dur))
            self.settings.set_setting('play_fade_dur', int(self.fade_in_dur))
            self.settings.set_setting('pause_fade_dur', int(self.fade_out_dur))

            if isinstance(self.editor_output_device, dict) and self.editor_output_device:
                self.settings.set_setting('Editor_Output', [self.editor_output_device.get('index'), self.editor_output_device.get('name'), self.editor_output_device.get('hostapi_name'), self.editor_output_device.get('sample_rate')])
            if isinstance(self.main_output_device, dict) and self.main_output_device:
                self.settings.set_setting('Main_Output', [self.main_output_device.get('index'), self.main_output_device.get('name'), self.main_output_device.get('hostapi_name'), self.main_output_device.get('sample_rate')])

            self.settings.set_setting('rows', self.rows_spinbox.value())
            self.settings.set_setting('columns', self.columns_spinbox.value())
            self.settings.save_settings()

            # Ensure engine has final values (in case the user typed values but didn't release slider).
            self._send_transition_fade_settings()
        except Exception as e:
            info = getframeinfo(currentframe())
            print(f'{e}{info.filename}:{info.lineno}')

        # Best-effort cleanup of refresh thread.
        try:
            if self.check_sound_devices_thread is not None:
                self.check_sound_devices_thread.quit()
                self.check_sound_devices_thread.wait(250)
        except Exception:
            pass

        event.accept()


