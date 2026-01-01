#audio level meter

import typing
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
import numpy as np

class AudioLevelMeterHorizontal(QtWidgets.QWidget):

    def __init__(self, vmin=0, vmax=0, height=50, width=200,  *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.vmin = vmin
        self.vmax = vmax
        self.vheight = height
        self.vwidth = width

        # Allow the widget to expand horizontally while keeping fixed height
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed
        )
        def sizeHint(self):
            return QtCore.QSize(50,200)
        
        self.value = 0.0
        self.level = -64.0  # Initialize level to silence (dB)
        self.peak = -64.0  # Initialize peak to silence (dB)
        self.setFixedHeight(self.vheight)
        self.setMinimumWidth(self.vwidth)  # Use minimum instead of fixed to allow stretching
        
        colors = ['green', 'yellow','orange','red']
        self.colors = []
        for i in range(64):
            l = 64-i
            
            if l > 12:
                self.colors.append(colors[0]) 
            
            if l <= 12 and l >= 6:
                self.colors.append(colors[1])
                
            if l < 6 and l > 1:
                self.colors.append(colors[2])
                
            if l <= 1:
                self.colors.append(colors[3])
            
        
            
            
        self.colors_len = len(self.colors)
        print(f'length: {self.colors_len}')
        
    def paintEvent(self,e):

        try:
            painter = QtGui.QPainter(self)
            brush = QtGui.QBrush()
            brush.setColor(QtGui.QColor('black'))
            brush.setStyle(Qt.BrushStyle.SolidPattern)
            rect = QtCore.QRect(0,0,painter.device().width(), painter.device().height())
            painter.fillRect(rect, brush)

            pen = painter.pen()
            pen.setColor(QtGui.QColor('red'))
            painter.setPen(pen)

            d_height = painter.device().height()
            d_width = painter.device().width()
        
            number_of_bars = int(d_width/8)
            
            level = (self.level - self.vmin) / (self.vmin - self.vmax)
            peak = (self.peak - self.vmin) / (self.vmin - self.vmax)
            n_steps_to_draw = abs(int(round(level * number_of_bars)))
            
            peak_bar = abs(round(peak * number_of_bars))

            factor = d_width/number_of_bars

            step_size = d_width / number_of_bars
            bar_width = step_size*.6
            bar_spacer = step_size - bar_width

            brush.setColor(QtGui.QColor('red'))
                 
            # main levels                      
            top_padding = int(round(d_height*.2))
            bottom_padding = int(round(d_height*.3))  
            for n in range(n_steps_to_draw):
                rect = QtCore.QRect(
                    int(round((n)*(bar_width+bar_spacer))),
                    top_padding,
                    int(round(bar_width)),
                    int(d_height-(round(top_padding+bottom_padding)))
                )
                color_factor = n/number_of_bars
                color = min(int(round(color_factor * self.colors_len)), self.colors_len - 1)
                
                brush.setColor(QtGui.QColor(self.colors[color]))
                painter.fillRect(rect,brush)
            
            # peak bar    
            rect = QtCore.QRect(
                    int(round((peak_bar-.6)*(bar_width+bar_spacer))),
                    top_padding,
                    int(round(bar_width)),
                    int(d_height-(round(top_padding+bottom_padding)))
                )
            color_factor = (peak_bar-.6)/number_of_bars
            color = min(int(round(color_factor * self.colors_len)), self.colors_len - 1)
            brush.setColor(QtGui.QColor(self.colors[color]))
            painter.fillRect(rect,brush)

            font = painter.font()
            font.setFamily('Times')
            font.setPointSize(5)
            painter.setFont(font)

            painter.drawText(1,d_height-12, "|")
            painter.drawText(d_width-4,d_height-12, "|")
            font.setPointSize(10)
            # painter.setFont(font)
            # painter.drawText(2,d_height, "{}".format(self.vmin))
            
            # - scale steps base 10
            # scale_width = abs(self.vmax - self.vmin)
            # remainder = int(scale_width%10.0)
            # scale_steps = abs(int((scale_width-remainder)/10))
            # factor = (scale_width-remainder)/scale_width
            # scale_step_size = (d_width*factor)/scale_steps
            # for i in range((scale_steps+1)):
            #     position = d_width-int(round(scale_step_size * (i)))
            #     number = (i) * 10
            #     font.setPointSize(5)
            #     painter.setFont(font)
            #     painter.drawText(position,d_height-12, "|")
            #     font.setPointSize(10)
            #     painter.setFont(font)
            #     painter.drawText(position-5,d_height, "{}".format(number))
                
            #scale steps base 3
            
            remainder = 1
            start = int(round(d_width * (remainder/64)))
            for i in range(-66, 1, 6):
                factor = (remainder + abs(i))/64
                position = start + start + 2 + int(round(d_width * factor))
                number = 0 - (60-abs(i))
                font.setPointSize(5)
                painter.setFont(font)
                painter.drawText(position,d_height-12, "|")
                font.setPointSize(10)
                painter.setFont(font)
                painter.drawText(position-5,d_height, "{}".format(number))

            painter.end()
            
        except Exception as err:
            print('meter error' + str(err))

       

    def _trigger_refresh(self):
        self.update()


    def setValue(self, level:float = -64, peak:float = -64):
        
        
        self.level = round(level,3)
        self.peak =round(peak, 3)
        if level < -64:
            self.level = -64
        if peak < -64:
            self.peak = -64
        self._trigger_refresh()

    def setVmin(self, vmin):
        self.vmin = vmin
    
    def setVmax(self, vmax):
        self.vmax = vmax

    



# app = QtWidgets.QApplication([])
# volume = AudioLevelMeterHorizontal(-64,0,70,350)
# volume.setValue(-54, -12)
# volume.show()
# app.exec()