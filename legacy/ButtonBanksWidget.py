#BUTTON BANKS WIDGET
from PySide6.QtWidgets import (QMainWindow,
                             QWidget,
                             QGridLayout, 
                             QSizePolicy, 
                             QApplication)
from PySide6.QtGui import QFont, QColor, QResizeEvent
from PySide6.QtCore import QObject, Signal, QUrl, Slot, QTimer, QPoint, QRect
# from PySide6 import QtWidgets

from widgets.ColorChangingButton import ColorChangingButton
from widgets.SoundFileButton import SoundFileButton
from persistence.SaveSettings import SaveSettings
from DragSelectWidget import DragSelectWidget

from streamdeck_connector import StreamDeckConnector

# import DragSelectWidget
from BankWidget import BankWidget
import sys

from typing import List

class Signals(QObject):
    #args : qurl, in point, out point, loop, object name
    play_signal = Signal(QUrl, int, int, bool, int, object,name='play_signal')

class SaveSignal(QObject):
    save_signal = Signal(str, dict, name='SaveSig') # key, dict (save, color, text, qurl for buttons)
     
class ButtonBanksWidget(QWidget):
    enable_buttons_signal = Signal(bool)
    def __init__(self, parent, banks=10, rows=5, columns=10, audio_engine=None):
        super().__init__(parent)

        self.parent = parent
        
        #new for refractor
        self.engine = audio_engine
        
        self.stream_deck = StreamDeckConnector()
        self.stream_deck.key_pressed.connect(self.streamdeck_button_handler)

        self.settings = SaveSettings("Default_Button_File.json")
        self.button_settings = self.settings.get_settings()
        self.emitter = Signals()
        self.save_signal = SaveSignal()
        self.save_signal.save_signal.connect(self.save_button_settings)
        
        self.outer_layout = QGridLayout()
        self.banks_layout = QGridLayout()
        self.buttons_layout = QGridLayout()
        self.buttons = []
        self.current_bank_widget = BankWidget()
        
        # self.buttons_layout.setVerticalSpacing(30)
        # self.buttons_layout.setHorizontalSpacing(30)

        self.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.MinimumExpanding
        )

        self.setMinimumSize(parent.width()-20, (int(round(parent.height())*.7)))

        self.setLayout(self.outer_layout)
        self.outer_layout.addLayout(self.banks_layout, 1,0)
        self.outer_layout.addLayout(self.buttons_layout,0,0)

        self.button_clipboard = SoundFileButton()
        self.button_clipboard_list:List[SoundFileButton] = []

        self.num_of_banks = banks
        self.rows = rows
        self.columns = columns
        
        self.row_pad = int(round((self.width()/(self.rows))*.05))
        self.column_pad = int(round((self.height()/(self.columns))*.02)) 
        
        
        self.set_up_buttons()
        
        self.drag_select_widget = DragSelectWidget(self)
        self.installEventFilter(self.drag_select_widget)
        self.enable_buttons_signal.connect(self.drag_select_widget.set_buttons_enabled)
        # self.set_up_buttons()
        
        self.re_enable_timer = QTimer()
        self.re_enable_timer.timeout.connect(self.enable_buttons)
        self.re_enable_timer.setSingleShot(True)
        self.re_enable_timeout = 10

        
        #add buttons to bank select row
        for bank_num in range(10):
            bank_button = ColorChangingButton(f'BANK \n {bank_num}', self)
            bank_button.setObjectName(f'_bank_button{bank_num}')
            # bank_button.setFixedSize(100, 50)
            bank_button.setMinimumSize(90, 50)
            font = QFont("Arial", 14)
            bank_button.setFont(font)
            bank_button.setStyleSheet(f"background-color: {'grey'};"
                                      "border: 3px solid black;")
            bank_button.pressed.connect(lambda bank_num=bank_num+1: self.switch_bank(bank_num))
            self.buttons_layout.addWidget(bank_button, 1, bank_num)
            bank_button.save_signal.save_signal.connect(self.save_button_settings)

            if bank_num == 0:
                bank_button.setStyleSheet(f"background-color: {'green'};"
                                          "border: 3px solid black;")
                
            if bank_button.objectName() in self.button_settings:
                settings = self.button_settings[bank_button.objectName()]
                bank_button.setStyleSheet(settings['stylesheet'])
                bank_button.setText(settings['text'])

        self.show()
        
        
        
        
        
    def set_up_buttons(self):
        button_number = 0
        #add banks - banks will be named like 'bank1'
        # buttons will be named like 'button016' = button at bank 0 row 1 column 6
        for bank_num in range(self.num_of_banks):
            bank_layout = QGridLayout()
            bank_widget = BankWidget()
            bank_widget.setLayout(bank_layout)
            bank_widget.setObjectName(f'bank{bank_num}')
            bank_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.banks_layout.addWidget(bank_widget,0,0)
     
            button_width = int(self.width()/self.columns) - self.row_pad
            button_height = int(self.height()/(self.rows)) - self.column_pad

            self.current_bank = 1

            #add buttons to each bank
            for row in range(self.rows):
                for column in range(self.columns):
                    button = SoundFileButton(self, audio_engine=self.engine)
                    button.setObjectName(f'button{bank_num}{row}{column}')
                    # button.installEventFilter(self.drag_select_widget)
                    button_number += 1
                    button.button_num = f"{bank_num}{row}{column}" #str(button_number)
                    button.setFixedSize(button_width,button_height)
                    font = QFont("Arial", 14)
                    button.setFont(font)
                    bank_layout.addWidget(button, row, column)
                    
                    color = QColor(200,200,200)
                    button.setStyleSheet("padding: 5px;"
                                       f"background-color: {color.name()};"
                                       "color: white;"
                                       "border: 3px solid black;")
                    button.emitter.play_signal.connect(self.emit_play_signal)
                    button.save_signal.save_signal.connect(self.save_button_settings)
                    button.released.connect(self.disable_buttons)
                    self.parent.mark_played_tracks_signal.connect(button.enable_mark_played)

                    settings = {'QUrl': '', 'text': button.text(),'stylesheet':button.styleSheet()}

                    
                    if button.objectName() in self.button_settings:
                        settings = self.button_settings[button.objectName()]
                        button.setText(settings['text'])
                        button.setStyleSheet(settings['stylesheet'])
                        button.qurl = QUrl.fromLocalFile(settings['QUrl'])
                        try:
                            if 'in_point' in settings:
                                button.in_point = settings['in_point']
                            if 'out_point' in settings:
                                button.out_point = settings['out_point']
                                button.button_time = button.duration()
                            if 'gain' in settings:
                                button.gain = settings['gain']
                            if 'loop' in settings:
                                button.loop = settings['loop']
                            else:
                                button.loop = False
                            
                        except:
                            pass
                        if button.qurl.path() != '':
                            button.released.connect(button.emit_play_signal)
                            button.track_set = True
                            
                        else:
                            button.clear_connections()
                        button.background_color1 = button.get_background_color()
                        button.text_color = button.get_text_color()
                        
                    else:
                        self.button_settings[f'{button.objectName()}'] = settings
                    
                    button.geometry_offset = self.buttons_layout.geometry().bottomLeft()
                    self.buttons.append(button)
            
            if bank_num != 0:
                bank_widget.hide()

            else:
                bank_widget.show()
                self.current_bank_widget = bank_widget
                
                
            
                
            
                
    def reload_buttons(self):
        for bank in range(self.num_of_banks):
            for row in range(self.rows):
                for column in range(self.columns):
                    button = self.findChild(SoundFileButton, f'button{bank}{row}{column}')
                    
                    if button.objectName() in self.button_settings:
                        settings = self.button_settings[button.objectName()]
                        button.set_played(False)
                        button.setText(settings['text'])
                        button.setStyleSheet(settings['stylesheet'])
                        button.qurl = QUrl.fromLocalFile(settings['QUrl'])
                        try:
                            if 'in_point' in settings:
                                button.in_point = settings['in_point']
                            if 'out_point' in settings:
                                button.out_point = settings['out_point']
                            if 'gain' in settings:
                                button.gain = settings['gain']
                            if 'loop' in settings:
                                button.loop = settings['loop']
                            else:
                                button.loop = False
                                
                        except:
                            pass
                        if button.qurl.path() != '':
                            connections = button.receivers(button.released)
                            try:
                                if connections > 0:
                                    button.released.disconnect()
                            except:
                                pass
                            button.released.connect(button.emit_play_signal)
                        
                        else:
                            button.clear_connections()
                            
                        button.background_color1 = button.get_background_color()
                        button.text_color = button.get_text_color()
                        
                        
                        
                    else:
                        button.clear()
                        
                    self.drag_select_widget.set_buttons()

    def paintEvent(self, e):
        self.setMinimumSize(self.parent.width()-20, (int((self.parent.height())*.72)))
        buttons = self.findChildren(SoundFileButton)
        self.row_pad = int(round((self.width()/(self.rows))*.1))
        self.column_pad = int(round((self.height()/(self.columns))*.1)) 
        button_width = int(round(self.width()/self.columns)) - self.column_pad
        button_height = int(round(self.height()/self.rows)) - self.row_pad
        for button in buttons:
            button.setFixedSize(button_width, button_height)     

    def switch_bank(self, bank_number):
        #switch the bank/layout to the 
        buttons = self.findChildren(ColorChangingButton)

        for b in buttons:
            # if b.objectName()[0:11]=='_bank_button':
            b.setStyleSheet(f"background-color: {'grey'};"
                            "border: 3px solid black;")
                
            if b.objectName()[12] == str(bank_number-1):
                b.setStyleSheet(f"background-color: {'green'};"
                                    "border: 3px solid black;")

        #get each bank - hide each bank - except for the one 
        banks = self.findChildren(BankWidget)
        
        for bank in banks:
            if bank.objectName() != f'bank{bank_number-1}':
                bank.hide()

            if bank.objectName() == f'bank{bank_number-1}':
                bank.show()
                self.current_bank_widget = bank      
                      
        self.current_bank = bank_number
        self.drag_select_widget.set_buttons()
        self.streamdeck_update()
        


    @Slot(QUrl, int, int, bool, int, object)
    def emit_play_signal(self, qurl, in_point, out_point, loop, gain, button):
        try:
            self.emitter.play_signal.emit(qurl, in_point, out_point, loop, gain, button)
            # pass
        except Exception as e:
            print(f'button banks emit signal{e}')

    def change_button_rows_and_columns(self, rows, columns):
        self.num_of_banks = 10
        self.rows = rows
        self.columns = columns

        #get all the buttons in a list
        buttons = self.findChildren(SoundFileButton)
        self.row_pad = int(round((self.width()/(self.rows))*.05))
        self.column_pad = int(round((self.height()/(self.columns))*.02)) 
        button_width = int(round(self.width()/self.columns)) - self.column_pad
        button_height = int(round(self.height()/(self.rows))) - self.row_pad

        #hide buttons that won't be visiable and resize
        for button in buttons:
            button.hide()

        for bank_num in range(self.num_of_banks):
            for row in range(self.rows):
                for column in range(self.columns):
                    name = f'button{bank_num}{row}{column}'
                    button = self.findChild(SoundFileButton, name)
                    button.setFixedSize(button_width, button_height)
                    button.show()

        #add buttons to bank select row
        for bank_num in range(10):
            bank_button = ColorChangingButton(f'BANK \n {bank_num}', self)
            bank_button.setObjectName(f'_bank_button{bank_num}')
            # bank_button.setFixedSize(100, 50)
            bank_button.setMinimumSize(90, 50)
            font = QFont("Arial", 14)
            bank_button.setFont(font)
            bank_button.setStyleSheet(f"background-color: {'grey'};"
                                      "border: 3px solid black;")
            bank_button.pressed.connect(lambda bank_num=bank_num+1: self.switch_bank(bank_num))
            self.buttons_layout.addWidget(bank_button, 1, bank_num)
            bank_button.save_signal.save_signal.connect(self.save_button_settings)
            button.global_offset_topLeft = self.banks_layout.geometry().adjust
            

            if bank_num == 0:
                bank_button.setStyleSheet(f"background-color: {'green'};"
                                          "border: 3px solid black;")
                
            if bank_button.objectName() in self.button_settings:
                settings = self.button_settings[bank_button.objectName()]
                bank_button.setStyleSheet(settings['stylesheet'])
                bank_button.setText(settings['text'])

        self.show()


    def save_button_settings(self, key, value):
        print('trying to save...')
        self.settings.set_setting(key,value)
        self.settings.save_settings()
        
    def save_new_buttons_file(self, file_path):
        self.settings = SaveSettings(file_path)
        self.button_settings = self.settings.get_settings()
        
        for bank_num in range(self.num_of_banks):
            for row in range(self.rows):
                for column in range(self.columns):
                    name = f'button{bank_num}{row}{column}'
                    button = self.findChild(SoundFileButton, name)
                    button.clear()
                    button.save_settings()
                    
    def save_as_buttons_file(self, file_path):
        self.settings = SaveSettings(file_path)
        self.button_settings = self.settings.get_settings()
        
        for bank_num in range(self.num_of_banks):
            for row in range(self.rows):
                for column in range(self.columns):
                    name = f'button{bank_num}{row}{column}'
                    button = self.findChild(SoundFileButton, name)
                    button.save_settings()
                    
    def change_buttons_file(self, file_path):
        self.settings = SaveSettings(file_path)
        self.button_settings = self.settings.get_settings()

    def stop_flashing_all(self):
        
        try:
            buttons = self.current_bank_widget.findChildren(SoundFileButton)
            for button in buttons:
                button.stop_flashing()
            # pass

        except Exception as e:
            print(f'stop_flashing_all(): {e}')
            
    def disable_buttons(self):
        try:
            self.enable_buttons_signal.emit(False)
            self.re_enable_timer.start(self.re_enable_timeout)
                
        except Exception as e:
            print(f'disable buttons error {e}')
            
    def enable_buttons(self):
        try:
            self.enable_buttons_signal.emit(True)
                
        except Exception as e:
            print(f'enable buttons error {e}')
            
    def resizeEvent(self, a0: QResizeEvent | None) -> None:
        banks_rect = self.banks_layout.geometry()
        self.drag_select_widget.move(banks_rect.topLeft())
        self.drag_select_widget.resize(banks_rect.width(), banks_rect.height())
        for button in self.buttons:
            button.geometry_offset = self.buttons_layout.geometry().bottomLeft()
        return super().resizeEvent(a0)
    
    def streamdeck_update(self):
        rows = 3
        columns = 8
        buttons = self.current_bank_widget.findChildren(SoundFileButton)
        
        # button{bank_num}{row}{column}
        for button in buttons:
            number_str = button.objectName()
            
            btn_num = int(number_str[7:9])
            print(number_str[7])
            if int(number_str[7]) < 3:
                if number_str[7] == '0':
                    key = btn_num
                    print(key)
                if number_str[7] == '1':
                    key = btn_num - 2
                    print(key)
                if number_str[7] == '2':
                    key = btn_num - 4
                    print(key)
                if number_str[7] == '3':
                    key = btn_num - 6
                    print(key)
                 
                self.stream_deck.sound_file_button_to_key(button, self.stream_deck.deck, key)
                
    @Slot(int)
    def streamdeck_button_handler(self, key:int):
        button_num = self.key_to_index(key)
        btns = self.current_bank_widget.findChildren(SoundFileButton)
        btns[button_num].emit_play_signal()
        
        
    def key_to_button(self, key:int)->str:
        bank = self.current_bank
        if key < 8:
            return f"button{bank}{0}{key}"
        if key >=8 and key < 16:
            return f"button{bank}{key+2}"
        if key >= 16 and key < 24:
            return f"button{bank}{key+4}"
        if key >= 16 and key < 24:
            return f"button{bank}{key+6}"
        
    def key_to_index(self, key:int)->int:
        if key < 8:
            return key
        if key >=8 and key < 16:
            return key+2
        if key >= 16 and key < 24:
            return key+4
        if key >= 16 and key < 24:
            return key+6
        
        
            
            
            
        
        
        
          
        
        
        
        

if __name__ == '__main__':
    app = QApplication(sys.argv)
    main = QMainWindow()
    main.setFixedSize(2000,1200)
    main.show()

    window = ButtonBanksWidget(main)
    window.show()

    sys.exit(app.exec())



        