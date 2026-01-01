#settings
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget, QHBoxLayout, QSpacerItem, QRadioButton, QSlider, QLabel, QComboBox, QMainWindow, QLineEdit, QSpinBox
from PySide6.QtGui import QFont
from PySide6 import QtCore
from PySide6 import QtWidgets
from PySide6.QtCore import QThread

from PySide6.QtMultimedia import QMediaDevices

from PySide6.QtCore import QObject, Signal, Qt

from SaveSettings import SaveSettings
from CheckSoundDevices import CheckSoundDevices

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
    
    def __init__(self, parent, height=500, width=450, pause=10, play=10, *args, **kwargs):
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
  
            self.fade_out_dur = pause
            self.fade_in_dur = play
            
            self.sample_rate = 48000
            
            self.usable_devices:dict = {}
            self.devices = sd.query_devices()
            self.apis = sd.query_hostapis()

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
            
            self.check_sound_devices_thread = QThread()
            self.check_sound_devices = CheckSoundDevices()
            self.check_sound_devices.moveToThread(self.check_sound_devices_thread)
            
            self.check_sound_devices.device_list_signal.connect(self.re_populate_audio_combo_box)
            self.refresh_outputs_button.clicked.connect(self.check_sound_devices.get_devices)
            self.check_sound_devices_thread.start()

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
            self.rows_spinbox.setValue(self.parent.buttonBanksWidget.rows)
            self.columns_spinbox.setValue(self.parent.buttonBanksWidget.columns)
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
            
        except Exception as e:
            print(e)

    def restore_saved_settings(self):
        try:
            #recall settings
            if 'pause_fade_dur' in self.app_settings:
                self.fade_out_dur = self.app_settings['pause_fade_dur']
                self.fade_out_slider.setValue(self.fade_out_dur)
                self.fade_out_line_edit.setText(str(self.fade_out_dur))

            if 'play_fade_dur' in self.app_settings:
                self.fade_in_dur = self.app_settings['play_fade_dur']
                self.fade_in_slider.setValue(self.fade_in_dur)
                self.fade_in_line_edit.setText(str(self.fade_in_dur))

            if 'Main_Output' in self.app_settings:
                device = self.app_settings['Main_Output']
                device_name = device[0]
                device_hostapi = device[1] #MME, WINDOWS DIRECT SOUND, ASIO etc only the index number is saved
                
                device_search_text = device_name + ', ' + device_hostapi
                
                selected_index = self.audio_output_combo.findText(device_search_text , flags=Qt.MatchFlag.MatchContains)
                self.audio_output_combo.setCurrentIndex(selected_index)
                # self.audio_output_combo.currentIndexChanged.emit(selected_index)
                self.main_output_device = device
                self.main_output_changed()
            
            else:
                index = sd.default.device[1]
                device = sd.query_devices(index)
                api = self.apis[device['hostapi']]
                
                device_search_text = device['name'] + ', ' + api['name']
                
                selected_index = self.audio_output_combo.findText(device_search_text , flags=Qt.MatchFlag.MatchContains)
                self.audio_output_combo.setCurrentIndex(selected_index)
                self.audio_output_combo.currentIndexChanged.emit(selected_index)
                self.main_output_device = device

            if 'Editor_Output' in self.app_settings:
                device = self.app_settings['Editor_Output']
                device_name = device[0]
                device_hostapi = device[1]  #MME, WINDOWS DIRECT SOUND, ASIO etc
                
                device_search_text = device_name + ', ' + device_hostapi
                selected_index = self.editor_audio_output_combo.findText(device_search_text , flags=Qt.MatchFlag.MatchContains)
                self.editor_audio_output_combo.setCurrentIndex(selected_index)
                # self.editor_audio_output_combo.currentIndexChanged.emit(selected_index)
                self.editor_output_device = device
                self.editor_output_changed()
                        
            else:
                index = sd.default.device[1]
                device = sd.query_devices(index)
                api = self.apis[device['hostapi']]
                device_search_text = device['name'] + ', ' + api['name']
                selected_index = self.editor_audio_output_combo.findText(device_search_text , flags=Qt.MatchFlag.MatchContains)
                self.editor_audio_output_combo.setCurrentIndex(selected_index)
                self.editor_audio_output_combo.currentIndexChanged.emit(selected_index)
                self.editor_output_device = device

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

    def populate_audio_combo_box(self):     
        try:
            self.audio_output_combo.clear()
            self.editor_audio_output_combo.clear()

            self.audio_output_combo.addItem('Main Audio Output')
            self.editor_audio_output_combo.addItem('Editor Audio Output')

            self.usable_devices.clear()
            
            for api in self.apis:
                if api['name'] in self.acceptable_apis:
                    for device in api['devices']:
                        if self.devices[device]['max_output_channels'] > 0:
                            self.usable_devices[device] = api['name']
                            settings = ''
                            for sample_rate in self.sample_rates:
                                try:
                                    sd.check_output_settings(device, channels=2, samplerate=sample_rate)
                                    usable_device = self.devices[device]
                                    usable_device['sample_rate'] = sample_rate
                                    usable_device['hostapi_name'] = api['name']
                                    self.usable_devices[device] = usable_device
                                    break
                                
                                except:
                                    pass
            
            
            for device in self.usable_devices:
                device = self.usable_devices[device]
                i = device['hostapi']
                api = self.apis[i]
                self.audio_output_combo.addItem(device['name'] +', ' + api['name'], userData=device)
                self.editor_audio_output_combo.addItem(device['name'] + ', ' + api['name'], userData=device)

        except Exception as e:
            info = getframeinfo(currentframe())
            print(f'{e}{info.filename}:{info.lineno}')
            
    def refresh_devices(self):
        self.refresh_outputs_button.setEnabled(False)
        self.refresh_sound_devices_signal.emit()
        
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
            index = self.audio_output_combo.currentIndex()
            device = self.audio_output_combo.itemData(index)
            
            self.main_output_device = device
            print(f'main output: {device}')
            self.sample_rate = device['sample_rate']
            self.settings.set_setting('Main_Output', [device['name'], device['hostapi_name'], device['sample_rate']])
            self.settings_signals.main_output_signal.emit(self.main_output_device['index'], self.main_output_device['sample_rate'])
            self.parent.main_output_device = self.audio_output_combo.currentData()
            self.settings.save_settings()
        except Exception as e:
            info = getframeinfo(currentframe())
            print(f'{e}{info.filename}:{info.lineno}')
            
    def editor_output_changed(self):
        try:
            index = self.editor_audio_output_combo.currentIndex()
            device = self.editor_audio_output_combo.itemData(index)
            
            self.editor_output_device = device
            self.settings.set_setting('Editor_Output', [device['name'], device['hostapi_name'], device['sample_rate']])
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

    def fade_in_line_edit_handler(self):
        text = self.fade_in_line_edit.text()
        try:
            t = int(text)
            self.fade_in_slider.setValue(t)
            self.fade_in_dur = t

        except:
            self.fade_in_line_edit.setText('10')
            self.fade_in_slider.setValue(10)
    
    def fade_out_line_edit_handler(self):
        text = self.fade_out_line_edit.text()
        try:
            t = int(text)
            self.fade_out_slider.setValue(t)
            self.fade_out_dur=t

        except:
            self.fade_out_line_edit.setText('10')
            self.fade_out_slider.setValue(10)

    def change_row_columns(self):
        banks=10
        rows=self.rows_spinbox.value()
        columns=self.columns_spinbox.value()
        self.settings_signals.change_rows_and_columns_signal.emit(rows, columns)

    def closeEvent(self,event): 
        try:
            #save settings on closing the settings window
            self.settings.set_setting('fade_in_duration', self.fade_in_dur)
            self.settings.set_setting('fade_out_duration', self.fade_out_dur)
            self.settings.set_setting('Editor_Output', [self.editor_output_device['name'], self.editor_output_device['hostapi_name'], self.editor_output_device['sample_rate']])
            self.settings.set_setting('Main_Output', [self.main_output_device['name'], self.main_output_device['hostapi_name'], self.main_output_device['sample_rate']])
            self.settings.set_setting('rows', self.rows_spinbox.value())
            self.settings.set_setting('columns', self.columns_spinbox.value())
            self.settings.save_settings()
        except Exception as e:
            info = getframeinfo(currentframe())
            print(f'{e}{info.filename}:{info.lineno}')
        

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.btn = QPushButton('Popup')
        self.btn.clicked.connect(self.popup)
        self.setCentralWidget(self.btn)

    def popup(self):
        self.settings_window = SettingsWindow(self)
        self.settings_window.show()

if __name__=='__main__':
    app = QtWidgets.QApplication([])
    main = MainWindow()
    setting_win = SettingsWindow(main)
    setting_win.show()
    
    main.show()
    app.exec()


