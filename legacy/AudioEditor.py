#audio file editor

import typing
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt,QUrl, QTimer, Signal, QObject, QMutex, QThread, QReadWriteLock, QTime, Slot
from PySide6.QtWidgets import QWidget, QLabel, QScrollArea, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QSlider, QSlider, QLineEdit, QDial, QWidget
from PySide6.QtGui import QPixmap, QColor, QPainter, QKeyEvent, QFont, qRgb, QPalette

import numpy as np

from EditorOutput import EditorOutput
from AudioPlayerProcessor import AudioPlayerProcessor
from AudioLevelMeter import AudioLevelMeter
import sounddevice as sd

from multiprocessing import (connection,
                             shared_memory, 
                             Lock)

from statistics import mean

import logging
import time
from math import log, log10, log1p, log2

#global variables
audio_level_array = np.zeros((2, 1000), dtype=np.float32)  #create global placeholder for audio levels
mutex = QMutex()
lock = QReadWriteLock()

class Signals(QObject):
    mark_in_signal = Signal(int, name='mark_in_signal')
    mark_out_signal =  Signal(int, name='mark_out_signal')
    close_signal = Signal(name='close_signal')
    gain_signal = Signal(int, name='gain_signal')


class AudioEditor(QWidget):
    ignore_out_signal = Signal(bool)
    set_loop_signal = Signal(bool)
    change_editor_output_signal = Signal(int, float) #device index, sample rate
    
    close_signal = Signal()

    def __init__(self, parent=QObject(), qurl=QUrl(),
                  vmin:int=-64, vmax:int=0, 
                  height:int=200, width:int=500,
                  in_point:int=0, out_point:int=1000,
                  gain:int = 0,
                  loop:bool=False,
                  output_device:int=8,
                  conn:connection.Connection=None,
                  *args, **kwargs):
        super().__init__()

        self.parent = parent
        
        try:
            self.setWindowTitle(f'Audio Editor - BUTTON {self.parent.button_num} - {self.parent.text()}')
        except:
            print('no parent - cant set window title')
        self.setMinimumSize(width, height)
        self.setWindowFlags(QtCore.Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet("""
                           background-color: gray;
                           """)

        self.decoder_conn = conn
        self.emitter = Signals()
        self.scale = 10
        self.qurl = qurl
        self.vmin = vmin
        self.vmax = vmax
        self.vheight = height
        self.vwidth = width
        self.in_point = in_point
        self.out_point = out_point
        self.audio_data = []
        self.qurl = qurl
        self.sample_rate = 48000
        self.channels = 2
        self.device = sd.default.device[1]
        
        peaks = []
        peak_decay = 40 #samples
        for i in range(40):
            peaks.append([-64, -64])
            
        self.peaks = np.array(peaks)
        
        # devices = sd.query_devices(kind='output')
        print(self.device)
        print(f'DEVICE: {self.device}')
        
        self.loop = loop
        self.gain = gain
        self.lock = Lock()
        self.track = AudioPlayerProcessor(source=self.qurl, 
                                          in_point=self.in_point, 
                                          out_point=self.out_point,
                                          loop=self.loop,
                                          gain=self.gain, 
                                          editor=True, 
                                          lock=self.lock)
        self.waveform = WaveformDisplay(self, track=self.track)
        self.output = EditorOutput(sample_rate=self.sample_rate,
                                   channels=self.channels,
                                   device=self.device)
        
        self.waveform.in_point = in_point
        self.waveform.out_point = out_point
        
        self.create_ui()
        
        self.viewport_height = self.scroll_area.height()# - self.scroll_area.horizontalScrollBar().height()
        self.viewport_width = self.scroll_area.width()
        
        self.check_status_timer = QTimer()
        self.check_status_timer.timeout.connect(self.check_status)
        self.check_status_timer.start(2000)
        
        self.output.position_signal.connect(self.update_time)
        self.output.position_signal.connect(self.update_slider_position)
        self.output.emit_levels_signal.connect(self.level_meter_update)
        self.change_editor_output_signal.connect(self.output.change_output)
        self.set_loop_signal.connect(self.output.loop)
        self.emitter.mark_in_signal.connect(self.output.set_in_point)
        self.emitter.mark_out_signal.connect(self.output.set_out_point)
        self.emitter.gain_signal.connect(self.output.set_gain)
        self.close_signal.connect(self.output.close)
        
        self.decoder_conn.send(self.track.track.decoder_info)
        
        
        self.track.set_position(self.in_point)
        self.position_ms = self.in_point
        self.position_frame = self.ms_to_frames(self.in_point)
        self.duration = self.track.duration_frames
        
        if self.out_point == 0:
            self.out_point = self.track.duration_milliseconds

        self.value = 0.0
        self.i = 0
        self.rewind_bool = False
        
        #not sure what to do with this timer yet - disconnected from a rewind callback function for now
        self.rewind_timer = QTimer()  
        self.rewind_speed = 0
        self.fast_forward_speed = 2.0
        self.playback_rate = 0

        self.fast_forwarding = False
        self.rewinding = False

        if self.track.duration_milliseconds > 60000:
            self.set_scale(self.scale)
        else:
            self.set_scale(5)
        self.set_in_point(self.in_point)
        self.set_out_point(self.out_point)
        self.track.set_position(self.in_point)
        self.waveform.position = self.track.ms_to_frames(self.in_point)
        
        self.output.add_track(self.track)
        
        self.track = None
        
        self.jog_single_shot = QTimer()
        self.jog_single_shot.setSingleShot(True)
        self.jog_single_shot.timeout.connect(self._jog_dial_pause)

        self.x = 0
        
    def check_status(self):
        if self.waveform.track.track.ready:
            self.viewport_height = self.scroll_area.viewport().height()
            self.viewport_width = self.scroll_area.viewport().width()
            self.waveform.render(self.viewport_width, self.viewport_height)
            self.move_play_head_to_player(0)
            self.set_scale(self.scale)
        
        if self.waveform.track.track.write_position.value > self.duration-5000: 
            self.check_status_timer.stop()
            
        else:
            print(self.waveform.track.track.write_position.value, self.duration, self.duration-self.waveform.track.track.write_position.value)
        
        

    def create_ui(self):
        
        # Create a QScrollArea and set the QLabel as its widget
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidget(self.waveform)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        print('height', self.scroll_area.viewport().height())
        self.waveform.setFixedHeight(self.scroll_area.viewport().height())
        
        scrollarea_viewport_height = self.scroll_area.height() - self.scroll_area.horizontalScrollBar().height()
        self.waveform.setFixedHeight(scrollarea_viewport_height)
        
        self.waveform_slider = QSlider(Qt.Orientation.Horizontal)
        self.waveform_slider.setRange(0,int(self.track.duration_frames/self.scale))
        print(f'track.duration_frames: {self.track.duration_frames}')
        self.waveform_slider.setFixedWidth(300)
        
        self.waveform_slider.valueChanged.connect(self.scroll_slider_handler)
        
        # Add the QScrollArea to the layout
        self.main_layout = QVBoxLayout()
        self.setLayout(self.main_layout)
        # self.setLayout(self.layout)
        self.waveform_and_levels_layout = QHBoxLayout()
        self.waveform_and_levels_layout.addWidget(self.scroll_area)
        self.levels_layout = QHBoxLayout()
        self.meter1 = AudioLevelMeter(height=170, width=40)
        self.meter2 = AudioLevelMeter(height=170, width=40)
        self.gain_slider = QSlider()
        self.gain_slider.setRange(-64,30)
        self.gain_slider.setValue(0)
        self.gain_line_edit = QLineEdit()
        self.gain_line_edit.setFixedWidth(30)
        self.gain_label = QLabel('GAIN')
        self.gain_layout = QVBoxLayout()
        self.gain_layout.addWidget(self.gain_slider)
        self.gain_layout.addWidget(self.gain_line_edit)
        self.gain_layout.addWidget(self.gain_label)
        self.levels_layout.addWidget(self.meter1)
        self.levels_layout.addWidget(self.meter2)
        self.meter1.setValue(-64, -64)
        self.meter2.setValue(-64, -64)
        self.waveform_and_levels_layout.addLayout(self.levels_layout)
        self.waveform_and_levels_layout.addLayout(self.gain_layout)
        self.main_layout.addLayout(self.waveform_and_levels_layout)

        self.gain_slider.valueChanged.connect(self.gain_slider_handler)
        self.gain_line_edit.textChanged.connect(self.gain_line_edit_handler)
        self.gain_line_edit.editingFinished.connect(self.save_gain)
        self.gain_slider.sliderReleased.connect(self.save_gain)
        self.gain_line_edit.setText(str(0))
        
        self.main_layout.addWidget(self.waveform_slider)

        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.MinimumExpanding,
            QtWidgets.QSizePolicy.Policy.MinimumExpanding
        )

        #editor controls
        self.cue_in_button = QPushButton('GOT TO \n IN (U)')
        self.mark_in_button = QPushButton('MARK IN \n (i)')
        self.rewind_button = QPushButton('REW \n (j)')
        self.play_pause_button = QPushButton('PLAY/PAUSE \n (k)')
        self.fast_forward_button = QPushButton('FFWD \n (l)')
        self.mark_out_button = QPushButton('MARK OUT \n (o)')
        self.cue_out_button = QPushButton('GOT TO \n OUT (P)')
        self.loop_button = QPushButton('LOOP')
        self.jog_dial = QDial()
        
        self.zoom_in_button = QPushButton('+')
        self.zoom_out_button = QPushButton('-')
        self.scale_labelA = QLabel('SCALE')
        self.scale_labelB = QLabel('100')
        
        self.cue_in_button.setFixedSize(80,50)
        self.mark_in_button.setFixedSize(80,50)
        self.rewind_button.setFixedSize(80,50)
        self.fast_forward_button.setFixedSize(80,50)
        self.play_pause_button.setFixedSize(80,50)
        self.mark_out_button.setFixedSize(80,50)
        self.cue_out_button.setFixedSize(80,50)
        self.loop_button.setFixedSize(80,50)
        
        self.zoom_in_button.setFixedSize(50,50)
        self.zoom_out_button.setFixedSize(50, 50)
        
        default_button_color = QColor(qRgb(230,230,230))
        
        button_stylesheet = f"""
                            background-color: {default_button_color.name()};
                            color: black;
                            """
        
        self.cue_in_button.setStyleSheet(button_stylesheet)
        self.cue_out_button.setStyleSheet(button_stylesheet)
        self.mark_in_button.setStyleSheet(button_stylesheet)
        self.rewind_button.setStyleSheet(button_stylesheet)
        self.fast_forward_button.setStyleSheet(button_stylesheet)
        self.play_pause_button.setStyleSheet(button_stylesheet)
        self.mark_out_button.setStyleSheet(button_stylesheet)
        self.cue_out_button.setStyleSheet(button_stylesheet)
        self.loop_button.setStyleSheet(button_stylesheet)
        
        self.zoom_in_button.setStyleSheet(button_stylesheet)
        self.zoom_out_button.setStyleSheet(button_stylesheet)
        
        self.jog_dial.setFixedSize(200,200)
        self.jog_dial.setWrapping(True)
        self.jog_speed_list = []
        for i in range(3):
            self.jog_speed_list.append(1.0)
        self.jog_dial_value_prev = 0

        #add controls to layout
        self.master_controls_layout = QGridLayout()
        self.controls_layout_lower = QHBoxLayout()
        self.controls_layout_upper = QHBoxLayout()
        self.controls_layout_zoom = QHBoxLayout()
        self.master_controls_layout.addLayout(self.controls_layout_upper, 0,0)
        self.master_controls_layout.addLayout(self.controls_layout_lower, 1,0)
        self.controls_layout_lower.addWidget(self.cue_in_button)
        self.controls_layout_lower.addWidget(self.mark_in_button)
        self.controls_layout_lower.addWidget(self.rewind_button)
        self.controls_layout_lower.addWidget(self.play_pause_button)
        self.controls_layout_lower.addWidget(self.fast_forward_button)
        self.controls_layout_lower.addWidget(self.mark_out_button)
        self.controls_layout_lower.addWidget(self.cue_out_button)
        self.master_controls_layout.addWidget(self.jog_dial, 0,1,2,1)
        self.controls_layout_upper.addWidget(self.loop_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.controls_layout_upper.addLayout(self.controls_layout_zoom)
        self.controls_layout_zoom.addWidget(self.zoom_in_button)
        self.controls_layout_zoom.addWidget(self.zoom_out_button)
        self.controls_layout_zoom.addWidget(self.scale_labelA, alignment=Qt.AlignmentFlag.AlignRight)
        self.controls_layout_zoom.addWidget(self.scale_labelB, alignment=Qt.AlignmentFlag.AlignLeft)
        
        

        #connect controls
        self.cue_in_button.clicked.connect(self.cue_in)
        self.mark_in_button.clicked.connect(self.mark_in)
        self.rewind_button.clicked.connect(self.rewind)
        self.fast_forward_button.clicked.connect(self.fast_forward)
        self.play_pause_button.clicked.connect(self.play_pause)
        self.mark_out_button.clicked.connect(self.mark_out)
        self.cue_out_button.clicked.connect(self.cue_out)
        self.loop_button.clicked.connect(self.set_loop)
        self.zoom_in_button.clicked.connect(self.set_scale_minus)
        self.zoom_out_button.clicked.connect(self.set_scale_plus)
        self.jog_dial.valueChanged.connect(self.jog_dial_handler)
        self.jog_dial.sliderReleased.connect(self.jog_pause)
        self.jog_position = 0
        self.jog_dial_value_prev = None
        self.jog_interval_time = 1
        self.jog_dial_timer = QTimer()
        self.jog_dial_timer.setInterval(200)
        self.jog_dial.setNotchesVisible(True)
        self.jog_dial.setNotchTarget(10)
        self.jog_dial_settings = {
            'range_min':0,
            'range_max':50
        }
        self.jog_dial.setRange(self.jog_dial_settings['range_min'], 
                               self.jog_dial_settings['range_max'])
        self.jog_dial_timer.timeout.connect(self.jog_pause)
        self.ignore_out_signal.connect(self.output.ignore_out_point)

        self.in_display = QLabel()
        self.in_label = QLabel('IN POINT')
        self.out_display = QLabel()
        self.out_label = QLabel('OUT POINT')
        self.current_pos_display = QLabel()
        self.current_pos_label = QLabel('PLAY POSITION')

        self.display_stylesheet = """background-color: 'black';
                                    color: 'white';
                                    border: 1px 'white';
        """
        font = QFont("Arial", 14)
        self.in_display.setStyleSheet(self.display_stylesheet)
        self.out_display.setStyleSheet(self.display_stylesheet)
        self.current_pos_display.setStyleSheet(self.display_stylesheet)
        self.in_display.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.out_display.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.current_pos_display.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.in_display.setFont(font)
        self.out_display.setFont(font)
        self.current_pos_display.setFont(font)

        self.in_display.setText(self.millisec_to_strtime(self.in_point))
        self.out_display.setText(self.millisec_to_strtime(self.out_point))
        self.current_pos_display.setText(self.millisec_to_strtime(self.scroll_area.horizontalScrollBar().value()))
        self.scroll_area.horizontalScrollBar().valueChanged.connect(self.move_play_head_to_scrollbar)
        # self.scroll_area.horizontalScrollBar().sliderPressed.connect(self._scroll_pressed)
        # self.scroll_area.horizontalScrollBar().sliderReleased.connect(self._scroll_released)
        
        #connect to player
        self.output.position_signal.connect(self.move_play_head_to_player)

        self.time_layout = QGridLayout()
        self.time_layout.addWidget(self.in_display, 0,0)
        self.time_layout.addWidget(self.out_display, 0,1)
        self.time_layout.addWidget(self.current_pos_display, 0,2)
        self.time_layout.addWidget(self.in_label, 1,0)
        self.time_layout.addWidget(self.out_label, 1,1)
        self.time_layout.addWidget(self.current_pos_label, 1,2)

        self.main_layout.addLayout(self.time_layout)
        self.main_layout.addLayout(self.master_controls_layout)
        
        def sizeHint(self):
            return QtCore.QSize(200,800)
        
        self.scale_labelB.setText(str(self.scale))

    def closeEvent(self, event):
        self.close_signal.emit()
            
    def paintEvent(self,e):
        try:
            pass
            # painter = QtGui.QPainter(self)
            # brush = QtGui.QBrush()
            # brush.setColor(QtGui.QColor('white'))
            # brush.setStyle(Qt.BrushStyle.SolidPattern)
            # rect = QtCore.QRect(0,0,painter.device().width(), painter.device().height())
            # painter.fillRect(rect, brush)
            # painter.end()
            
        except Exception as err:
            print('editor error: ' + str(err))

        self.i = 0
       
    def _trigger_refresh(self):
        self.update()

    def millisec_to_strtime(self, time):
        player_time_qtime = QTime(0, 0, 0, 0).addMSecs(time)
        time_string = player_time_qtime.toString('hh:mm:ss')
        msec_string = player_time_qtime.toString('zz')
        if len(msec_string) < 2:
            msec_string = msec_string + "0"
        time_string = time_string + ':' + msec_string[0:2]

        return time_string

    # def _scroll_pressed(self):
    #     self.move_play_head_to_scrollbar()

    # def _scroll_released(self):
    #     self.move_play_head_to_scrollbar()

    def setValue(self, level):
        self.value = level
        self._trigger_refresh()

    def setVmin(self, vmin):
        self.vmin = vmin
    
    def setVmax(self, vmax):
        self.vmax = vmax
        
    @Slot(int, int)
    def update_time(self, position_ms, position_frame):
        self.position_ms = position_ms
        self.position_frame = position_frame
        self.current_pos_display.setText(self.millisec_to_strtime(self.position_ms))
        self.waveform.set_position(self.position_frame)
        
    @Slot(int)
    def update_slider_position(self, position_ms, position_frame):
        self.waveform_slider.valueChanged.disconnect(self.scroll_slider_handler)
        self.waveform.scroll_pos = int(position_frame / self.scale)
        self.waveform_slider.setSliderPosition(self.waveform.scroll_pos)
        self.waveform.update()
        self.waveform_slider.valueChanged.connect(self.scroll_slider_handler)

    def jog_dial_handler(self):
        min, max = self.jog_dial_settings['range_min'], self.jog_dial_settings['range_max']
        notch_time = time.time() - self.jog_interval_time
        #.01 = 1
        #.1 = .1
        factor = round(1-((notch_time)*5)+.03, 2)
        
        if self.jog_position < max and self.jog_position > min:
            #forward
            if self.jog_dial.value() > self.jog_position:
                self.output.jog_to(factor)
            #reverse    
            elif self.jog_dial.value() < self.jog_position:
                self.output.jog_to(-factor)
                
        #reverse
        else:
            #forward
            if self.jog_position == max and self.jog_dial.value() == min:
                self.output.jog_to(factor)
            #reverse
            elif self.jog_position == 0 and self.jog_dial.value() == 99:
                self.output.jog_to(-factor)
        
        self.jog_interval_time = time.time()
        self.jog_position = self.jog_dial.value()  
        
        
        # self.position_frame = self.position_frame + 1000
        # self.output.jog_to_signal.emit(self.position_frame)
        # if self.jog_single_shot.remainingTime() > 0:
        #     self.jog_single_shot.stop()
        # else:
        #     self.jog_single_shot.start(100)
        
    def _jog_dial_pause(self):
        self.output.set_playback_rate(0.01)
        print('stop')

    def jog_dial_handler_old(self):
        self.ignore_out_signal.emit(True)
        now = time.time()
        speed = 0
        scaler = 1.0
        interval = 0
        self.jog_dial_timer.stop()
        self.jog_dial_timer.start(200)
    
        if self.jog_interval_time != 0:
            interval = now - self.jog_interval_time
            
        else:
            self.jog_interval_time = now

        interval = int(round(interval*1000))
        
        self.jog_speed_list.append(interval)
        self.jog_speed_list.pop(0)
        
        interval = mean(self.jog_speed_list)
        
        if interval >= 100:
            # speed = (1-(interval*5)) * 0.5
            speed = 0.25
            # print('SLOW', interval)
        elif interval < 100 and interval > 30:
            # speed = (1-(interval*3))
            speed = .50
            # print('NORMAL', interval)
        elif interval <= 30 and interval < 20:
            
            # speed = (1 + interval*50)
            speed = 1.0
            # print('FAST', interval)
            
        elif interval <= 20:
            speed = 2.0

        current_speed = self.playback_rate
        min, max = self.jog_dial_settings['range_min'], self.jog_dial_settings['range_max']
        
        if abs(current_speed-speed) != 0:
            if self.jog_position < max and self.jog_position > min:
                #forward
                if self.jog_dial.value() > self.jog_position:
                    self.output.set_playback_rate(speed)
                    self.playback_rate = speed
                #reverse    
                elif self.jog_dial.value() < self.jog_position:
                    self.output.set_playback_rate(-speed)
                    self.playback_rate = -speed
            else:
                #forward
                if self.jog_position == max and self.jog_dial.value() == min:
                    self.output.set_playback_rate(speed)
                    self.playback_rate = speed
                #reverse
                elif self.jog_position == 0 and self.jog_dial.value() == 99:
                    # self.player.setPosition(pos-1)
                    self.output.set_playback_rate(-speed)
                    self.playback_rate = -speed

        self.jog_interval_time = time.time()
        self.jog_position = self.jog_dial.value()   
        
        if self.jog_single_shot.remainingTime() > 0:
            self.jog_single_shot.stop()
        self.jog_single_shot.start(100) 

    def jog_pause(self):
        self.output.stop()
        self.jog_dial_timer.stop()
        self.ignore_out_signal.emit(False)
       
    def cue_in(self):
        self.output.set_position(self.ms_to_frames(self.in_point))
        self.position_ms = self.in_point
        self.position_frame = self.ms_to_frames(self.in_point)
        self.move_play_head_to_player(self.in_point)
        self.ignore_out_signal.emit(False)
        
      
    def mark_in(self):
        self.in_point = self.output.position_ms
        self.waveform.in_point = self.output.position_frame
        self.in_display.setText(self.millisec_to_strtime(self.in_point))
        self.emitter.mark_in_signal.emit(self.in_point)
        self.waveform.update()
        
    def rewind(self):
        self.ignore_out_signal.emit(True)
        self.rewinding = True
        self.fast_forwarding = False
        speed = self.playback_rate
        if speed > -1.0:
            self.output.set_playback_rate(-1.0)
            self.playback_rate = -1.0
        elif speed <= -1.0 and speed >= -10.0:
            speed = speed - 1.0
            self.output.set_playback_rate(speed)
            self.playback_rate = speed
        
    def play_pause(self):        
        self.reset_vars()
        if self.playback_rate == 0.0:
            self.output.play()
            self.playback_rate = 1.0
            
        else:
            self.output.pause()
            self.playback_rate = 0.0
            
    def ms_to_frames(self, ms=int)->int:
        frames = int(ms * (self.sample_rate/1000))
        return frames
    
    def frames_to_ms(self, frames=int)->int:
        ms = int(frames/(self.sample_rate/1000))
        return ms
        

    def fast_forward(self):
        self.fast_forwarding = True
        self.rewinding = False
        speed = self.playback_rate
        
        if speed < 1.0:
            self.output.set_playback_rate(1.0)
            self.ignore_out_signal.emit(True)
            self.playback_rate = 1.0

        elif speed >= 1.0 and speed <= 10.0:
            speed += 1.0
            self.output.set_playback_rate(speed)
            self.playback_rate = speed
            self.ignore_out_signal.emit(True)

    def mark_out(self):
        self.out_point = self.output.position_ms
        self.waveform.out_point = self.output.position_frame
        self.out_display.setText(self.millisec_to_strtime(self.out_point))
        self.emitter.mark_out_signal.emit(self.out_point)
        self.waveform.update()
        # self.player.out_point = self.out_point

    def cue_out(self):
        self.output.set_position(self.ms_to_frames(self.out_point))
        self.position_ms = self.out_point
        self.position_frame = self.ms_to_frames(self.out_point)
        self.move_play_head_to_player(self.out_point)
        self.ignore_out_signal.emit(False)

    def set_gain(self, gain:float = 0.0):
        self.gain = gain
        self.gain_slider.setValue(gain)
        self.gain_line_edit.setText(str(gain))
        self.emitter.gain_signal.emit(gain)
        
    def set_loop(self):
        if self.loop == False:
            self.loop = True
            self.set_loop_signal.emit(True)
            color = QColor('grey')
            self.loop_button.setStyleSheet(f'background-color: {color.name()};')
        else:
            self.loop = False
            self.set_loop_signal.emit(False)
            color = QColor(qRgb(230,230,230))
            self.loop_button.setStyleSheet(f'background-color: {color.name()};')
            
    def scroll_slider_handler(self):
        self.output.position_signal.disconnect(self.update_slider_position)
        scaler = self.waveform.duration/self.waveform.audio_level_array.shape[1]
        self.position_frame = int(self.waveform_slider.value()*scaler)
        slider_pos = self.position_frame * (self.waveform.audio_level_array.shape[1]/self.waveform.duration)
        self.position_ms = self.frames_to_ms(self.position_frame)
        
        self.output.set_position(self.position_frame)
        
        self.waveform.set_position(self.position_frame)
        self.waveform.set_visible(0, self.scroll_max, self.viewport_width)
        self.waveform.scroll_pos = self.waveform_slider.value()
        
        # self.waveform.play_head_position = pos
        self.current_pos_display.setText(self.millisec_to_strtime(self.position_ms))
        self.waveform.update()
        
        self.output.position_signal.connect(self.update_slider_position)



    def move_play_head_to_player(self, position:int):
        # self.scroll_area.horizontalScrollBar().valueChanged.disconnect(self.move_play_head_to_scrollbar)
        # # scale = self.scroll_area.horizontalScrollBar().maximum() / self.duration
        # # inv_scale = self.duration/self.scroll_area.horizontalScrollBar().maximum()
        
        # pos = self.position_frame
        # # self.output.set_position(self.ms_to_frames(pos))
        
        # # print('SCROLL POS:', self.scroll_area.horizontalScrollBar().value() )
        # scroll_pos = self.scroll_area.horizontalScrollBar().value()
        
        
        # s = self.scroll_max/self.duration
        # self.scroll_area.horizontalScrollBar().setValue(int(pos * s))
        
        # self.waveform.set_position(self.position_frame)
        # self.waveform.set_visible(scroll_pos, self.scroll_max, self.viewport_width)

        # # self.waveform.play_head_position = pos
        # self.current_pos_display.setText(self.millisec_to_strtime(pos))
 
        # self.scroll_area.horizontalScrollBar().valueChanged.connect(self.move_play_head_to_scrollbar)
        pass
    
    def move_play_head_to_scrollbar(self):
        
        # self.output.position_signal.disconnect(self.move_play_head_to_player)
        
        # # scale = self.scroll_area.horizontalScrollBar().maximum() / self.duration
        # scale = self.duration/self.scroll_area.horizontalScrollBar().maximum()
        
        # pos = int(self.scroll_area.horizontalScrollBar().value() * scale)
        # self.position_ms = self.frames_to_ms(pos)
        # self.position_frame = pos
        # self.output.set_position(pos)
        
        
        # scroll_pos = self.scroll_area.horizontalScrollBar().value()
        # # scroll_max = self.scroll_area.horizontalScrollBar().maximum()
        
        # # self.waveform.set_position(pos)
        # self.waveform.set_visible(scroll_pos, self.scroll_max, self.viewport_width)
        
        # # self.waveform.play_head_position = pos
        # self.current_pos_display.setText(self.millisec_to_strtime(self.position_ms))
        # self.output.position_signal.connect(self.move_play_head_to_player)
        pass

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_J:
            self.rewind()

        if event.key() == Qt.Key.Key_K:
            self.play_pause()
            self.rewind_speed = 0

        if event.key() == Qt.Key.Key_L:
            self.fast_forward()

        if event.key() == Qt.Key.Key_I:
            self.mark_in()

        if event.key() == Qt.Key.Key_O:
            self.mark_out()

        if event.key()== Qt.Key.Key_U:
            self.cue_in()
        
        if event.key() == Qt.Key.Key_P:
            self.cue_out()

        if event.key() == Qt.Key.Key_Equal:
            self.set_scale_minus()
  
        if event.key() == Qt.Key.Key_Minus:
            self.set_scale_plus()

        # have to figure out scrubbing - this doesn't work right yet
        # if event.key() == Qt.Key.Key_Comma:
        #     self.scrub_backward()

        # if event.key() == Qt.Key.Key_Period:
        #     self.scrub_forward()

    def reset_vars(self):
        self.rewind_speed = 0
        self.fast_forward_speed = 1.0
        self.fast_forwarding = False
        self.rewinding = False

    def is_fast_forwarding(self):
        return self.fast_forwarding
    
    def is_rewinding(self):
        return self.rewinding
    
    def set_in_point(self, value):
        self.in_point = value
        self.waveform.in_point = value #int(value/self.scale)
        self.in_display.setText(self.millisec_to_strtime(self.in_point))

    def set_out_point(self, value):
        self.out_point = value
        self.waveform.out_point = value #int(value/self.scale)
        self.out_display.setText(self.millisec_to_strtime(self.out_point))
        
    def set_scale_plus(self):
        if self.scale < 5:
            scale = self.scale + 1
            self.set_scale(scale)
            self.scale_labelB.setText(str(scale))
            
        elif self.scale < 1000:
            scale = self.scale + 5
            self.set_scale(scale)
            self.scale_labelB.setText(str(scale))
    
    def set_scale_minus(self):
        if self.scale > 5:
            scale = self.scale - 5
            self.set_scale(scale)
            self.scale_labelB.setText(str(scale))
        else:
            if self.scale > 1:
                scale = self.scale - 1
                self.set_scale(scale)
                self.scale_labelB.setText(str(scale))
                

            

    def set_scale(self, scale):
        self.scale = scale
        self.waveform.set_scale(scale)
        self.scroll_max = self.scroll_area.horizontalScrollBar().maximum()
        self.viewport_width = self.scroll_area.viewport().width()
        self.viewport_height = self.scroll_area.viewport().height()
        self.waveform.render(self.viewport_width, self.viewport_height)
        self.waveform_slider.setFixedWidth(self.viewport_width)
        self.waveform_slider.setRange(0, self.waveform.audio_level_array.shape[1])
        self.waveform.scaler = self.waveform.audio_level_array.shape[1]/self.duration

    def gain_line_edit_handler(self):
        try:
            value = int(self.gain_line_edit.text())
            if value < 30 and value >= -64:
                self.gain_slider.setValue(value)
        except:
            pass

    def gain_slider_handler(self):
        self.gain = self.gain_slider.value()
        self.gain_line_edit.setText(str(self.gain))
        self.emitter.gain_signal.emit(self.gain)

    def save_gain(self):
        self.emitter.gain_signal.emit(self.gain)

    def log_gain(self, value)->float:
        if value == 0:
            return value
        
        if value > 0:
            value = log1p(value)

        if value < 0:
            value = log1p(-value)

        return value

    def level_meter_update(self, levels, peaks):
        self.peaks[1:] = self.peaks[:-1]
        self.peaks[0] = peaks
        peaks_max = np.max(self.peaks, axis=0)
        
        self.meter1.setValue(levels[0], peaks_max[0])
        self.meter2.setValue(levels[1], peaks_max[1])
        
    @Slot(int, float)
    def change_editor_output(self, index, samplerate):
        self.change_editor_output_signal.emit(index, samplerate)
        
    def closeEvent(self, event):
        self.close_signal.emit()
        print('CLOSING NOW')
        event.accept()
        
    def resizeEvent(self, event):
        scrollarea_viewport_height = self.scroll_area.height() - self.scroll_area.horizontalScrollBar().height()
        self.waveform.setFixedHeight(scrollarea_viewport_height)
        self.waveform.update()


logging.basicConfig(format="%(message)s", level=logging.INFO)

from Track import Track
from threading import Lock
from pedalboard.io import AudioFile, AudioStream
import numpy as np
# from resampy import resample

from plot_waveform_new import plot #import cython function to draw waveform faster
from PySide6.QtCore import QLine, QPointF, QPoint
from PySide6.QtGui import QPainterPath

from scipy.ndimage import zoom
# from scale_audio import scale_audio
import soxr
from normalize_numpy import normalize


class WaveformDisplay(QLabel):
    def __init__(self, parent, track:AudioPlayerProcessor=None, height=150, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scale = 50
        self.track = track
        self.duration = track.duration_frames
        print(f'duration: {round(self.duration/track.sample_rate,2)}')
        
        self.scroll_pos = 0
        self.scroll_max = 0
        self.step = 0
        self.scaler = 1.0
        
        self.audio_level_array = self.track.track.audio_buffer.audio.astype(np.float32).T
        self.setFixedHeight(height)
        # self.render()
        self.setStyleSheet(f"background-color: {QColor(0,200,250).name()};")
        
        self.timer = QTimer()
        self.timer.timeout.connect(self.update())
        self.timer.start(1000)
        self.position = 0
        self.visible_width = 200

        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.check_status)
        
        self.visibile_start = 0
        self.visible_width = 500
        
        self.in_point = 0
        self.out_point = 0
        
        self.previous_frame = -1
        
        # self.plotter = plot(audio=self.audio_level_array)
        self.plotter = plot(audio=self.audio_level_array)
        
    def render(self, viewport_width, viewport_height):
        self.duration = self.track.duration_frames
            
        self.audio_level_array = self.track.track.audio_buffer.audio.T
        
        resampled_audio = self.audio_level_array[:,::self.scale]
        self.scaler = 1.0 #self.audio_level_array.shape[0]/resampled_audio.shape[0]
        peak = np.max(resampled_audio, axis=1)
        peak = np.max(peak)
        
        if peak > 1:
            factor = (1/peak)
            resampled_audio = resampled_audio * factor
        self.audio_level_array = resampled_audio
    
        self.plotter = plot(audio=self.audio_level_array)
        self.setFixedSize(viewport_width, viewport_height)
            
        self.update()
        
    def check_status(self):
        print(self.track.track.write_position.value, self.duration)
        if self.track.track.write_position.value < self.duration:
            self.render() 
            
        if self.track.track.write_position.value == self.duration:
            self.status_timer.stop()
            
        # else:
        #     print('not ready')
    
    def set_scale(self, scale):
        self.scale = scale
        
    def set_position(self, pos):
        #position in frames
        self.position = pos
        self.update()
            
    def set_visible(self, scroll_pos, scroll_max, viewport_width):
        self.scroll_pos = scroll_pos
        self.scroll_max = scroll_max
        self.visible_width = viewport_width
        # print('set visible:', scroll_pos, viewport_width)
        
    def paintEvent(self, event):

        thickness = 1.0
        
 
        zoom_scale = self.audio_level_array.shape[1]/self.duration
     
        # if int(self.position*zoom_scale) - int(self.previous_frame*zoom_scale) > 10:
            # print('',int(self.position*zoom_scale) - int(self.previous_frame*zoom_scale))
        painter = QPainter(self)
        pen = QtGui.QPen()
        
        plot.plot_waveform(painter=painter,
                    pen=pen, 
                    scroll_pos=self.scroll_pos,
                    height=self.height(),
                    width=self.visible_width,
                    position=self.position,
                    thickness=thickness, 
                    duration=self.duration, 
                    scale=zoom_scale, 
                    in_point=self.in_point,
                    out_point=self.out_point)
        self.previous_frame = self.position
        
        painter.end()
        

class WaveformDisplay_old(QWidget):
    def __init__(self, parent, track:AudioPlayerProcessor=None, height=150, *args, **kwargs):
        super().__init__(*args, **kwargs)
        logging.info('hello')

        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.MinimumExpanding,
            QtWidgets.QSizePolicy.Policy.MinimumExpanding
        )
        self.update_needed = True

        self.vmin = -64
        self.vmax = 0
        self.scale = 50

        # global audio_level_array
        self.track = track
        
        step = int(self.track.sample_rate/1000) 
        self.audio_level_array = self.track.track.audio_buffer.audio.astype(np.float32)[::step,:].T
        
        
        self.duration = track.duration_milliseconds
        self.update_audio_data()

        self.label = QLabel(self)
        self.layout = QVBoxLayout()
        self.setLayout(self.layout)
        self.layout.addWidget(self.label)

        self.p = parent

        self.pad = 20
        self.waveform_scale = self.scale
        self.waveform_width = int(self.duration/self.scale)+self.pad
        self.waveform_height = 150

        self.setMinimumHeight(height)
        self.setMinimumWidth(self.waveform_width)
        self.in_point = 0
        self.out_point = 0
        self.play_head_position = 0
        self.mark_in_position = 0
        self.mark_out_position = 2000

        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self._update_timer)
        self.update_delay = 100
        self.update_count = 0
        self.update_timer.start(500)

        self.new_update_count = 0
        self.update_once = True
        self.plotter = plot(self.audio_level_array)
        
    def update_audio_data(self):
        step = int(self.track.sample_rate/1000)
        self.audio_level_array = self.track.track.audio_buffer.audio.astype(np.float32)[::step,:].T
        if self.track.track.write_position.value >= self.track.track.frames:
            self.update_timer.stop()
            print('decoder finished - stopping update in audio editor')

    def paintEvent(self, e):
        
        
        left_pos = 0


        width = 1
        self.waveform.fill(QColor('3 blue'))
        painter = QPainter(self.waveform)
        pen = QtGui.QPen()
        pen.setWidth(width)
        pen.setColor(QtGui.QColor('gray'))
        painter.setPen(pen)
        pad = 10
    
        plot.plot_waveform(painter,
                    self.audio_level_array, 
                    left_pos, 
                    self.waveform.height(),
                    self.scale,
                    pad,
                    self.waveform.height(),
                    self.in_point,
                    self.out_point)
    
        painter.end()

    def _update_timer(self):
        self.update_audio_data()
        self.update()

    def trigger_update(self):
        self.update_audio_data()
        self.update_needed = True
        self.update_once = True
        self.update()

    def set_scale(self, scale):
        self.scale = scale
        self.pad = 20
        self.waveform_width = int(self.duration/self.scale)+self.pad
        self.setFixedWidth(self.waveform_width)
        self.update_needed = True
        self.update_once = True
        self.update()


if __name__ == "__main__":
    from DecodingManager import DecodingManager
    from multiprocessing import Pipe, Lock
    app = QtWidgets.QApplication([])
    # qurl = QUrl().fromLocalFile("C:/Users/sethz/OneDrive/Documents/GPI on air light/music/Bodysnatchers.mp3")
    # file = "C:/Users/Seth Zwiebel/OneDrive/Documents/GPI on air light/sample_sound_formats/stereo-test.mp3"
    # file = "C:/Users/Seth Zwiebel/OneDrive/Documents/GPI on air light/music/That's Not True.mp3"
    file = "C:/Users/Seth Zwiebel/Music/Easy Rider.mp3"
    # file = "C:/Users/Seth Zwiebel/Music/In-A-Gadda-Da-Vida (2006 Remaster Full-Length) - Iron Butterfly.mp3"
    qurl = QUrl().fromLocalFile(file)
    
    conn_a, conn_b = Pipe()
    
    lock = Lock()
    decoder = DecodingManager(conn_b, lock)
    
    
    volume = AudioEditor(parent=QObject(), 
                         qurl=qurl,
                         vmin=-64,
                         vmax = 0,
                         height=200,
                         width=500, 
                         in_point=1000, 
                         out_point=1000*120, 
                         gain=0,
                         loop=False,
                         output_device=7,
                         conn=conn_a)
    volume.show()
    app.exec()