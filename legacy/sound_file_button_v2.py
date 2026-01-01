# =========================
# SOUND FILE BUTTON (UI-LOCKED)
# =========================
# The following code defines the visual appearance, geometry,
# and timing display of the button.
#
# DO NOT rewrite paintEvent(), resizeEvent(), or drawing logic.
# Engine coupling must be refactored WITHOUT altering UI behavior.


from PySide6.QtWidgets import (QPushButton, 
                             QMenu, 
                             QColorDialog, 
                             QInputDialog, 
                             QFileDialog, 
                             QLineEdit, 
                             QMessageBox, 
                             QWidget, 
                             QHBoxLayout, 
                             QVBoxLayout, 
                             QApplication, 
                             QSizePolicy)
from PySide6.QtGui import (QColor, 
                         QAction,
                         QFont, 
                         QMouseEvent, 
                         QPalette, 
                         QPainter, 
                         QImage, 
                         QPainterPath,  
                         QPainterPathStroker, 
                         QPen, 
                         QTextOption, 
                         QPolygon,
                         QRadialGradient,
                         QBrush)
from PySide6.QtCore import QObject, Signal, QTimer, QTime, Qt, Slot, QRectF, QRect, QPoint, QPointF
from PySide6.QtMultimedia import QMediaPlayer

import textwrap

# Import CueInfo for type hints only
from typing import TYPE_CHECKING, Optional
if TYPE_CHECKING:
    from engine.cue import CueInfo

from pathlib import Path

import fleep

from PySide6.QtCore import QUrl

import sys

import validators
import statistics

class Signals(QObject):
    # Intent signals to be routed through EngineAdapter to AudioService
    request_play = Signal(str, dict, name='request_play')  # file_path, params_dict
    request_stop = Signal(str, int, name='request_stop')   # cue_id, fade_ms
    request_fade = Signal(str, float, int, name='request_fade')  # cue_id, target_db, duration_ms
    
    # Legacy signals (for compatibility if needed)
    try:
        play_signal = Signal(QUrl, int, int, bool, int, object, name='play_signal')
    except Exception as e:
        print(e)

class SaveSignal(QObject):
    save_signal = Signal(str, dict, name='SaveSig') # key, dict (save, color, text, qurl for buttons)

class InOutSignal(QObject):
    set_in_point_signal = Signal(int, name='set_in_point_signal')
    set_out_point_signal = Signal(int, name='set_out_point_signal')

class SoundFileButton(QPushButton):
    fade_out_signal = Signal(int)
    
    def __init__(self, parent=None, *args, **kwargs):
        super(SoundFileButton, self).__init__(parent, *args, **kwargs)

        self.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.MinimumExpanding
        )
        self.setAcceptDrops(True)
        self.connections = []
        self.main_text = ""
        self.qurl = QUrl()
        self.filename = ''
        
        # State variables for this button (UI display only, NOT for engine)
        self.cue_id = None  # Set when we emit request_play
        self.emitter = Signals()
        self.save_signal = SaveSignal()
        self.in_point = 0
        self.out_point = 0
        self.button_time = 0
        self.gain = 0
        self.loop = False
        self.settings = {'QUrl': self.qurl.url(), 'text': self.text(),'stylesheet':self.styleSheet(), 'in_point':self.in_point, 'out_point':self.out_point, 'gain':self.gain, 'loop':self.loop}
        self.mark_played_enabled = True
        self.style_sheet = self.styleSheet()
        self.track_set = False

        # track if the track has played (for display checkmark)
        self.track_played = False
        
        self.fade_out_duration = 0

        #timer for flashing buttons
        self.flashing_timer = QTimer()
        self.flashing_timer.timeout.connect(self.start_flashing)
        con = self.clicked.connect(self.flash)
        self.connections.append(con)
        self.background_color1 = QColor()
        self.background_color1 = self.get_background_color()
        self.text_color = self.get_text_color()
        self.current_color = self.background_color1

        #preload checkmark image and create a variable for the drop shadow so the shadow only gets created once
        self.shadow_created = False
        self.check = QImage('assets/check-mark-200_shadow.png')
        self.shadow = None

        self.button_num = 0

        self.recv_signal = InOutSignal()
        self.recv_signal.set_in_point_signal.connect(self.set_in_point)
        self.recv_signal.set_out_point_signal.connect(self.set_out_point)
        self.geometry_offset = QPoint(0,0)
        
        self.fade_button = FadeButton()
        self.fade_button.setFixedSize(int(self.width()/2), self.height())
        self.h_layout = QHBoxLayout()
        self.setLayout(self.h_layout)
        self.h_layout.addWidget(self.fade_button, alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        self.fade_button.setDisabled(True)
        self.fade_button.hide()
        self.fade_button.released.connect(self.fade_out)  
        
        self.current_frame = 0
        self.previous_frame = -1  
        
        self.playing = False
        self.light_level = 1.0

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)

        height = painter.device().height()
        width = painter.device().width()
        
        
        rect = QRect(0,0,15, 15)
        rect.moveTo(QPoint(int(width/2)-7,4))
        
        
        ctr = QPointF((width/2), 12)
        
        if self.playing == True:
            gradient = QRadialGradient(ctr,12,ctr) 
            gradient.setColorAt(0, QColor(255, 255, 255)) 
            gradient.setColorAt(1, QColor(0, int(self.light_level), 0))
        else:
            gradient = QRadialGradient(ctr,4,ctr) 
            gradient.setColorAt(0, QColor(180, 180, 180)) 
            gradient.setColorAt(1, QColor(0, 0, 0))
              
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(rect)

        if self.track_played:
            painter.setOpacity(.3)
            painter.drawImage(int(width/2)-int(self.check.width()/2),int(height/2)-int(self.check.height()/2),self.check)

        painter.setOpacity(1.0)

        font = QFont("Arial", 14)
        painter.setFont(font)
        painter.setPen(self.palette().color(self.foregroundRole()))

        # Draw the text on the button
        painter.drawText(4,18, self.button_num)
        font = QFont("Arial", 12)
        painter.setFont(font)

        pos_w = self.width()-45
        pos_h = 16 #self.height()-3
        painter.drawText(pos_w, pos_h, self.format_time(self.button_time))

        if self.loop:
            width = painter.device().width()
            painter.drawText((width-40),height-5, 'LOOP')
            font = QFont('Arial', 6)
            painter.setFont(font)
            
        textOption = QTextOption()

        textOption.setWrapMode(QTextOption.WrapMode.WordWrap)
        # textOption.setAlignment(Qt.AlignRight)
        # textOption.setFlags(QTextOption.IncludeTrailingSpaces)
        
        pos_h = 20
        pos_w = 5
        font = QFont("Arial", 14)
        rect = QRectF(pos_w, pos_h, self.width()*.95, self.height())
        painter.setFont(font)
        painter.drawText(rect, self.main_text, textOption)
        #drawText(self, rectangle: QRectF, text: Optional[str], option: QTextOption = QTextOption()):
        painter.end()
        
    @Slot(int, int)
    def update_time(self, time, frame):
        self.button_time = self.out_point - time
        self.current_frame = frame
        self.update()



    def create_alpha_stroke(self, image, stroke_color=QColor(0, 0, 0), stroke_width=2):
        # Load the image and convert it to QImage
      
        # Create a QPainterPath based on the alpha channel of the image
        path = QPainterPath()
        for y in range(image.height()):
            for x in range(image.width()):
                if QColor(image.pixel(x, y)).green() > 0:
                    path.addRect(x+stroke_width, y+stroke_width, 1, 1)

        # Create a stroke around the QPainterPath
        stroker = QPainterPathStroker()
        stroker.setWidth(stroke_width)
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap) 
        stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        stroked_path = stroker.createStroke(path)
        stroked_path = stroked_path.simplified()

        # Create a new QImage to draw the stroke
        stroke_image = QImage(image.width()+(stroke_width*2), image.height()+(stroke_width*2), QImage.Format.Format_ARGB32)
        transparent = QColor(0,0,0,0)
        stroke_image.fill(transparent)
        painter = QPainter(stroke_image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(stroke_color, stroke_width))
        painter.drawPath(stroked_path)

        self.shadow_created = True
        return stroke_image

    def format_time(self, time):
        player_time_qtime = QTime(0, 0, 0, 0).addMSecs(time)
        # time_string = player_time_qtime.toString('hh:mm:ss')
        time_string = player_time_qtime.toString('mm:ss')

        return time_string

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        color = QColor(200,200,200)

        menu.setStyleSheet(f"background-color: {color.name()};"
                           "color: black;")
        
        change_track_action = QAction("Select Track", self)
        change_track_action.triggered.connect(self.change_track)
        menu.addAction(change_track_action)
        
        # set_url_action = QAction("Set Stream", self)
        # set_url_action.triggered.connect(self.change_stream)
        # menu.addAction(set_url_action)
        
        change_color_action = QAction("Background Color", self)
        change_color_action.triggered.connect(self.change_color)
        menu.addAction(change_color_action)

        change_text_color_action = QAction("Text Color", self)
        change_text_color_action.triggered.connect(self.change_text_color)
        menu.addAction(change_text_color_action)

        change_text_action = QAction("Change Text", self)
        change_text_action.triggered.connect(self.change_text)
        menu.addAction(change_text_action)

        edit_track_action = QAction('Edit track', self)
        edit_track_action.triggered.connect(self.open_audio_editor)
        menu.addAction(edit_track_action)

        copy_button_action = QAction('Copy', self)
        copy_button_action.triggered.connect(self.copy_button)
        menu.addAction(copy_button_action)

        paste_button_action = QAction('Paste', self)
        paste_button_action.triggered.connect(self.paste_button)
        menu.addAction(paste_button_action)
        
        loop_action = QAction('Loop', menu, checkable=True, checked=self.loop)
        loop_action.triggered.connect(self.set_loop)
        menu.addAction(loop_action)
        
        reset_played_action = QAction('Reset Played Check', self)
        reset_played_action.triggered.connect(self.reset_played)
        menu.addAction(reset_played_action)

        menu.addSeparator()

        clear_action = QAction("Clear", self)
        clear_action.triggered.connect(self.clear)
        menu.addAction(clear_action)

        menu.exec(self.geometry().bottomLeft()+self.geometry_offset)

    def set_loop(self):
        if self.loop == True:
            self.loop = False
        else:
            self.loop = True
        
        self.save_settings()
        self.update()

    def duration(self):
        return self.out_point - self.in_point
    
    def copy_button(self):
        """Copy button state to global clipboard (functionality depends on parent)"""
        # Note: button_clipboard management moved to parent (ButtonBankWidget)
        pass

    def paste_button(self):
        """Paste button state from global clipboard (functionality depends on parent)"""
        # Note: button_clipboard management moved to parent (ButtonBankWidget)
        try:
            # This method requires button_clipboard to be set by parent
            # Implementation removed - use parent's paste mechanism instead
            return
            self.setStyleSheet(f"color: {self.text_color.name()};"
                                f"background-color: {self.background_color1.name()};"
                                "border:3px solid black;")
            con = self.released.connect(self.emit_play_signal)
            self.connections.append(con)
            self.track_set = True
            self.setEnabled(True)
            print(f'this button is enabled : {self.isEnabled()}')
            self.save_settings()

        except Exception as e:
            print(e)


    def change_color(self):
        self.text_color = self.get_text_color()
        color = QColorDialog.getColor(title='Change Background Color')
        if color.isValid():
            self.setStyleSheet(f"background-color: {color.name()};"
                               f"color: {self.text_color.name()};"
                               "border:3px solid black;")
            self.background_color1 = color
            
        self.background_color1 = color
        self.save_settings()
    
    def change_text_color(self):
        color = QColorDialog.getColor(title='Change Text Color')
        self.background_color1 = self.get_background_color()
        if color.isValid():
            self.setStyleSheet(f"color: {color.name()};"
                               f"background-color: {self.background_color1.name()};"
                               "border:3px solid black;")
            
        self.text_color = self.get_text_color()
        self.save_settings()

    def change_text(self):
        t = self.text()
        color = QColor(200,200,200)
        input = QInputDialog()
        input.setStyleSheet(f"background-color: {color.name()};")

        text, ok = input.getText(self, "Change Text", "Enter new text:", QLineEdit.EchoMode.Normal, t)

        if ok:
            self.setText(text)
            
        self.save_settings()

    # def set_text(self, input_text): 
    #     output_text = ''
    #     wrapper = textwrap.TextWrapper(width=15)
    #     lines = wrapper.wrap(text=input_text)
    #     for i,line in enumerate(lines):
    #         # if i > 0:
    #         output_text += '\n\n'
    #         output_text += line + ' '

    #     self.setText(output_text)
      
    def wrap_text(self, text):
        output_text = ''
        wrapper = textwrap.TextWrapper(width=10)
        lines = wrapper.wrap(text=text)
        for i,line in enumerate(lines):
            print(i, line)
            if i > 0:
                output_text += '\n'

        # self.setText(output_text)
        return output_text
        
    def setText(self, text):
        self.main_text = text #self.wrap_text(text)
        
    def text(self):
        return self.main_text
        
    def change_track(self):
        try:
            audio_file_filter = 'sound files (*.aif *.aiff, *.asf *.bwf *.flac *.mp3 *.ogg *.wav *.wm *.wma *.wmv *.ac3 *.alac *.aac)'
            file, ok = QFileDialog.getOpenFileName(self, 'Select Audio File', None, audio_file_filter)
            if ok:
                self.clear()
                url = file[0]
                self.setTrack(url)
                con = self.released.connect(self.emit_play_signal)
                self.connections.append(con)
                self.setEnabled(True)
           
        except Exception as e:
            print(f'change track error: {e}')
            
    def change_stream(self):
        t = self.text()
        color = QColor(200,200,200)
        input = QInputDialog()
        input.setStyleSheet(f"background-color: {color.name()};")

        text, ok = input.getText(self, "Enter Url for Streaming", "URL:", QLineEdit.EchoMode.Normal, t)

        if ok:
            if validators.url(text):
                self.setStream(text)

    def setStream(self, url):
        try:
            self.qurl = QUrl.fromLocalFile(url)
            self.filename = self.qurl.fileName()
            player = QMediaPlayer()
            try:
                player.setSource(self.qurl)
                self.in_point = 0
                self.out_point = player.duration()
            except:
                pass
            text = url
            text = text.split('/')
            text = text[len(text)-1]
            self.filename = text
            text = text.split('.')
            text = text[0]

            self.setText(text)

            color = QColor(50,65,220)
            self.setStyleSheet("padding: 5px;"
                                f"background-color: {color.name()};"
                                "color: white;"
                                "border: 3px solid black;")
            self.background_color1 = color

            con = self.clicked.connect(self.emit_play_signal)
            self.connections.append(con)
            self.save_settings()
        
        except Exception as e:
            print(f'change track error: {e}')


    def setTrack(self, url):
        try:
            if self.isSoundFile(url):
                self.qurl = QUrl.fromLocalFile(url)
                self.filename = self.qurl.fileName()
                player = QMediaPlayer()
                try:
                    player.setSource(self.qurl)
                    self.in_point = 0
                    self.out_point = player.duration()
                except:
                    pass
                text = url
                text = text.split('/')
                text = text[len(text)-1]
                self.filename = text
                text = text.split('.')
                text = text[0]

                self.setText(text)

                color = QColor(50,65,220)
                self.setStyleSheet("padding: 5px;"
                                    f"background-color: {color.name()};"
                                    "color: white;"
                                    "border: 3px solid black;")
                self.background_color1 = color

                con = self.clicked.connect(self.emit_play_signal)
                self.connections.append(con)
                self.track_set = True
                self.save_settings()
            else:
                self.message_box = QMessageBox(QMessageBox.Icon.Warning, 'Sound File Error', "Sorry.  This file didn't pass the sound file test.  It's not recognized as a supported sound file and won't be added." )
                self.message_box.show()
        except Exception as e:
            print(f'change track error: {e}')

    def isSoundFile(self, file):
        try:
            with open(file, 'rb') as check_file:
                    info = fleep.get(check_file.read(128))
                    print(f'type:{info.type}')
                    print(f'mime:{info.mime}')
                    if 'audio' in info.type:
                        return True
                    else:
                        return False
        except Exception as e:
            print(e)
            return False
        
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        
    def dropEvent(self, event):
        urls = event.mimeData().urls()
        print(urls)
        
    def mouseReleaseEvent(self, e: QMouseEvent | None) -> None:
        print(e.position())
        return super().mouseReleaseEvent(e)

    def open_audio_editor(self):
        """Open audio editor for this track (requires parent MainWindow)"""
        try:
            # AudioEditor functionality removed - requires parent window context
            # This method needs to be called from parent (ButtonBankWidget) with proper context
            print("Audio editor not available in refactored button")
            return
            self.audio_editor.set_gain(self.gain)
            self.audio_editor.cue_in()
        
        except Exception as e:
            print(e)

    @Slot(int)
    def set_in_point(self, in_point):
        self.in_point = in_point
        self.save_settings()
        print(f'setting in point: {in_point}, for the button!')

    @Slot(int)
    def set_out_point(self, out_point):
        self.out_point = out_point
        self.save_settings()
        print(f'setting out point: {out_point} for the button')

    @Slot(int)
    def set_gain(self, gain:int = 0):
        self.gain = gain
        self.save_settings()
        print(f'setting gain: {gain} for the button')


    #play
    def emit_play_signal(self):
        """Emit request_play signal instead of calling engine directly"""
        from uuid import uuid4
        
        # Generate unique cue_id for this play request
        self.cue_id = str(uuid4())
        
        # Build params dict with all playback configuration
        file_path = self.qurl.toLocalFile() if self.qurl.isValid() else ""
        params = {
            'in_point': self.in_point,
            'out_point': self.out_point if self.out_point > 0 else None,
            'gain_db': self.gain,
            'loop': self.loop,
            'fade_in_ms': self.fade_in_ms,
            'fade_out_ms': self.fade_out_ms,
            'auto_fade_enabled': self.auto_fade_enabled
        }
        
        # Emit request_play signal (will be routed through EngineAdapter)
        self.emitter.request_play.emit(file_path, params)

    
    def fade_out(self):
        self.fade_out_signal.emit(self.fade_out_duration)
            
    def set_played(self, played=bool):
        # mark as played to keep track of which tracks have been used
        self.track_played = played
        self.update()

    def save_settings(self):
        url = self.qurl.path()[1:]
        self.settings = {'QUrl': url, 'text': self.text(),'stylesheet':self.style_sheet, 'in_point':self.in_point, 'out_point':self.out_point, 'gain': self.gain, 'loop':self.loop}
        button_name = f'{self.objectName()}'
        self.save_signal.save_signal.emit(button_name, self.settings)

    def clear(self):
        try:
            #reset button settings to default
            self.in_point = 0
            self.out_point = 0
            color = QColor(200,200,200)
            self.background_color1 = color
            self.setStyleSheet("padding: 5px;"
                                f"background-color: {color.name()};"
                                "color: white;"
                                "border: 3px solid black;")
            self.qurl = QUrl()
            self.filename = ''
            self.track_played = False
            try:
                for con in self.connections:
                    self.disconnect(con)
                    self.connections.pop(self.connections.index(con))
            except:
                pass
            self.setText('')
            self.update_time(0,0)
            self.track_set = False
            self.save_settings()

        except Exception as e:
            print(e)
            
    def clear_connections(self):
        try:
            if self.connections:
                for con in self.connections:
                    self.disconnect(con)
                    self.connections.pop(self.connections.index(con))
                
        except:
            pass

    def get_stylesheet(self):
        try:
            stylesheet = self.styleSheet()
            stylesheet_list=stylesheet.split(';')
            stylesheet_dict={}
            for property in stylesheet_list:
                if ":" in property:
                    key, value = property.split(":")
                    stylesheet_dict[key.strip()] = value.strip()

            return stylesheet_dict
        except Exception as e:
            print('get_stylesheet error:', e)
    
    def get_background_color(self):
        stylesheet_dict = self.get_stylesheet()
        if 'background-color' in stylesheet_dict:
            color_str =  stylesheet_dict['background-color']
            return QColor().fromString(color_str)
        
        
    def get_text_color(self):
        palette = self.palette()
        return palette.color(QPalette.ColorRole.ButtonText)

    def flash(self):
        try:
            # self.text_color = self.get_text_color()
            # self.current_color = self.background_color1
            # color1_rgb = self.background_color1.getRgb()
            # self.color2 = [0,0,0,0]
            # for i, color in enumerate(color1_rgb):
            #     if color < 200:
            #         self.color2[i] = color + 50
            #     else:
            #         self.color2[i] = color - 50
            # self.color2 = tuple(self.color2)

            # if self.qurl.fileName() != '':
            #     self.flashing_timer.start(250)
            self.playing = True
            
        except Exception as e:
            print(e)

    def start_flashing(self):
        try:
            self.playing = True
            self.update()
            
            # if self.current_color == self.color2:
            #     self.setStyleSheet(f'background-color: {self.background_color1.name()};'
            #                     f'color:{self.text_color.name()};')
            #     self.current_color = self.background_color1
            # else:
            #     color = QColor().fromRgb(self.color2[0], self.color2[1], self.color2[3])
            #     self.setStyleSheet(f'background-color: {color.name()};'
            #                     f'color:{self.text_color.name()};')
            #     self.current_color = self.color2
        except Exception as e:
            print(f'start flashing error: {e}')

    @Slot()
    def stop_flashing(self):
        try:
            # self.flashing_timer.stop()
            # text_color = self.get_text_color()
            # self.setStyleSheet(f"background-color: {self.background_color1.name()};"
            #                     "border:3px solid black;"
            #                     f"color:{text_color.name()};"
            #                     "padding: 5px;")
            self.playing = False
            self.reset_time()
            self.update()
            self.fade_button.setVisible(False)
            self.fade_button.setEnabled(False)
        except Exception as e:
            print(f'stop flashing error: {e}')
            
    def clear_selection(self):
        try:
            palette = self.palette()
            self.setStyleSheet(f"""background-color: {self.background_color1.name()};
                                    color:{palette.color(QPalette.ColorRole.ButtonText).name()};
                                    border:3px solid black;
                                    padding: 5px;
                """
            )
        except Exception as e:
            print(f'stop flashing error: {e}')
        
        
    def reset_time(self):
        self.button_time = self.duration()
        # print(self.format_time(self.button_time))
        self.update()
        
    def reset_played(self):
        self.track_played = False
        self.update()
        
    @Slot(bool)
    def enable_mark_played(self, enabled:bool = False):
        self.mark_played_enabled = enabled
        
    def setStyleSheet(self, style_sheet:str, temp:bool=False):
        if not temp:
            self.style_sheet = style_sheet
        return super().setStyleSheet(style_sheet)
    
    @Slot(list)
    def level_to_light(self, levels:list):
        min = 0.1
        max = 0.7
        level = statistics.mean(levels)
        level = 1.0 - abs((level-min)/(max-min))
        self.light_level = 255 * level
        if self.light_level > 255:
            self.light_level = 255
        # elif self.light_level < 150:
        #     self.light_level = 150
    
    # ========== SLOTS FOR ENGINE EVENTS ==========
    # These slots are connected by EngineAdapter when button is added to ButtonBankWidget
    
    @Slot(str, object)
    def on_cue_started(self, cue_id: str, cue_info) -> None:
        """Handle cue started event from engine via EngineAdapter"""
        if cue_id == self.cue_id:
            self.playing = True
            self.track_played = True
            self.update()
    
    @Slot(str, str)
    def on_cue_finished(self, cue_id: str, reason: str) -> None:
        """Handle cue finished event from engine via EngineAdapter"""
        if cue_id == self.cue_id:
            self.playing = False
            self.button_time = 0
            self.current_frame = 0
            self.update()
    
    @Slot(str, float)
    def on_cue_time(self, cue_id: str, seconds_remaining: float) -> None:
        """Handle cue time update from engine via EngineAdapter"""
        if cue_id == self.cue_id:
            self.button_time = int(seconds_remaining)
            self.update()
    
    @Slot(str, float, float)
    def on_cue_levels(self, cue_id: str, rms_l: float, rms_r: float) -> None:
        """Handle audio levels update from engine via EngineAdapter"""
        if cue_id == self.cue_id:
            # Update light_level for playing indicator gradient
            self.light_level = max(rms_l, rms_r) * 255  # Scale to 0-255 range for color
            self.update()
        
class FadeButton(QPushButton):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setStyleSheet('border:3px solid black;')
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        bkgd = QColor(127,127,127,127)
        color = QColor(0,0,127,127)

        painter.fillRect(self.rect(), bkgd)
        
        bars = 4
        
        for i in range(bars):
            h_step = int(self.height()/bars)
            w_bar = int(self.width()/(bars*2))
            h = self.height()
            angle = int(self.height()/bars)
            
            # left,
            # top,
            # width,
            # height
            ii = i*2
            tl = QPoint(0+(ii*w_bar),0+i*h_step)
            tr = QPoint(w_bar + (ii*w_bar), angle+i*h_step)
            br = QPoint(w_bar+(ii*w_bar), h)
            bl = QPoint(0+(ii*w_bar),h) 
            
            bar_1 = QPolygon([
                tl,
                tr,
                br,
                bl
            ])
        
            painter.setBrush(color) 
            painter.drawPolygon(bar_1)
        
        
    






button_clipboard = None


# if __name__ == "__main__":
#     app = QApplication([])
    
#     window = QWidget()
#     layout = QVBoxLayout()
#     button = SoundFileButton(window)
#     button.button_num = "102"
    
#     layout.addWidget(button)
#     window.setLayout(layout)
#     window.show()
#     button.change_color()
#     button.change_track()
#     app.exec()
    
#     window.show()
    
    

    # sys.exit(app.exec())