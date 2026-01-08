# from scipy.ndimage cimport zoom

from PySide6.QtCore import QLine, QPointF, QPointF, QPoint
from PySide6.QtGui import QPainterPath, QPainter, QPen, QColor

cimport numpy as np
import numpy as np

import time

#scales
#1 - origianl scale - 48000 samples per second  (scale = 100/100)
#2 - scaled audio - scale = (scale/100 ) * 2 for extra detail
#3 - rendered widget inside scroll area - scale = (scale/100 )
#4 - scroll area - based on scroll max
#4 - scroll_max / duration - scale factor to convert scroll to ocriginal position
#4 - scroll_max / rendered widget width - to convert to widget scale

ctypedef np.float32_t DTYPE_t

cdef class plot:

    cdef DTYPE_t[:,:] audio_mv

    def __init__(self, np.ndarray[DTYPE_t, ndim=2] audio):
        
        self.audio_mv = audio

    @property
    def audio(self):
        return np.asarray(self.audio_mv)

    def plot_waveform(self, 
                        painter = QPainter(), 
                        pen  = QPen(),
                        int scroll_pos = 0, 
                        int height = 150, 
                        int width = 200,
                        int position = 0,
                        double thickness = 1.0,
                        int duration = 1000, 
                        double scale = 100, 
                        int in_point = 0,
                        int out_point = 0
                        ):
        t1 = time.time()


        cdef int playhead_pos = 0
        cdef int in_point_pos = int(in_point * scale)
        cdef int out_point_pos = int(out_point * scale)

        
        
        cdef int start = int(position * scale)
        
        cdef int channel_height = int(height/2)
        cdef int channel1_middle = int(channel_height/2)
        cdef int channel2_middle = channel1_middle + channel_height

        cdef double level1
        cdef double level2

        cdef int i

        cdef int pad = 10
        
        # scaled_time = int(round(self.position/self.scale))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path1 = QPainterPath()
        path2 = QPainterPath()

        path1.moveTo(0, channel1_middle)
        path2.moveTo(0, channel2_middle)

        offset_middle = int(width/2)
        if start > offset_middle and start < self.audio_mv.shape[1] - offset_middle:
            start = start - offset_middle
            playhead_pos = offset_middle

        elif start < offset_middle:
            playhead_pos = start
            start = 0

        elif start > offset_middle:
            playhead_pos = start - (self.audio_mv.shape[1] - width)
            start = self.audio_mv.shape[1] - width
            

        # print('start:',start,'playhead:', playhead_pos, 'width:', width, 'scale:', scale)

        for i in range(0, width):   
            x = i+(start)
            level1 = self.audio_mv[0,x]
            level2 = self.audio_mv[1,x]
            point1 = QPointF(i, channel1_middle+level1*(channel_height/2))
            point2 = QPointF(i, channel2_middle+level2*(channel_height/2))

            path1.lineTo(point1)
            path2.lineTo(point2)

            if in_point_pos >= x and x >= in_point_pos:
                pen.setColor(QColor(0,255,0,100))
                in_mark = QLine(i, 0, i, height)
                pen.setWidthF(3.0)
                painter.setPen(pen)
                painter.drawLine(in_mark)

            if out_point_pos >= x and x >= out_point_pos:
                pen.setColor(QColor(255,0,0,100))
                out_mark = QLine(i, 0, i, height)
                pen.setWidthF(3.0)
                painter.setPen(pen)
                painter.drawLine(out_mark)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen.setWidthF(thickness)
        pen.setColor(QColor(50,50,50,100))
        painter.setPen(pen)
        painter.drawPath(path1)
        painter.drawPath(path2)

        pen.setColor(QColor(0,0,255,100))
        playhead = QLine(playhead_pos, 0, playhead_pos, height)
        pen.setWidthF(3.0)
        painter.setPen(pen)
        painter.drawLine(playhead)

        

        t2 = time.time()
        print(f'plot duration: {t2 - t1}')

    # painter.end()


        
    
