
from PySide6.QtCore import QLine
from PySide6.QtGui import QPainter, QPixmap
import numpy as np
cimport numpy as cnp

def plot_waveform(painter = QPainter(),
                  const cnp.npy_double[:,:] audio_level_array = None, 
                  int left_pos=0, 
                  int waveform_height=100, 
                  int scale=100, 
                  int pad=10, 
                  int height=100):

    cdef int channel_height = int(height/2)-pad
    cdef int channel1_middle = int(channel_height/2)
    cdef int channel2_middle = channel1_middle + channel_height
    cdef int scaled_time = 0 

    cdef int level1 = 0
    cdef int level2 = 0

    cdef int level_scale = int(waveform_height/2)

    cdef int ch1_ytop = 0
    cdef int ch1_ybottom = 0
        
    cdef int ch2_ytop = 0
    cdef int ch2_ybottom = 0
        

    cdef int time = 0
    for time in range(left_pos, audio_level_array.shape[1], scale):
        scaled_time = int(round(time/scale))
        level1 = int(round(audio_level_array[0][time]*level_scale))
        level2 = int(round(audio_level_array[1][time]*level_scale))

        ch1_ytop = channel1_middle - int(level1/2)
        ch1_ybottom = channel1_middle + int(level1/2)
        
        ch2_ytop = channel2_middle - int(level2/2)
        ch2_ybottom = channel2_middle + int(level2/2)

        line1 = QLine(scaled_time,  ch1_ytop, scaled_time, ch1_ybottom)
        line2 = QLine(scaled_time, ch2_ytop, scaled_time, ch2_ybottom)
     
        painter.drawLine(line1)
        painter.drawLine(line2)
