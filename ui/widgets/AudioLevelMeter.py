#audio level meter

import typing
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
import numpy as np

class AudioLevelMeter(QtWidgets.QWidget):

    def __init__(self, vmin=-64, vmax=0, height=300, width=50,  *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.vmin = vmin
        self.vmax = vmax
        self.vheight = height
        self.vwidth = width

        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Fixed
        )
        def sizeHint(self):
            return QtCore.QSize(200,500)
        
        self.value = 0.0
        self.peak = 0.0
        self.setFixedHeight(self.vheight)
        self.setFixedWidth(self.vwidth)
        
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

            
            number_of_bars = 32
            
            level = abs(self.value - self.vmin) / (self.vmax - self.vmin)
            n_steps_to_draw = abs(int(round(level * number_of_bars)))

            d_height = painter.device().height()
            d_width = painter.device().width()
            
            brush.setColor(QtGui.QColor('red'))
                                  
            left_padding = int(round(d_width*.3))
            right_padding = int(round(d_width*.1))  
            
            step_size = d_height / number_of_bars
            bar_height = step_size*.7
            bar_spacer = step_size - bar_height
            
            for n in range(n_steps_to_draw):
                rect = QtCore.QRect(
                    left_padding,
                    d_height - int(round((n+1)*(step_size))),
                    int(d_width-round(right_padding+left_padding)),
                    int(round(bar_height))
                )
                color_factor = (n+1)/number_of_bars
                color = min(int(round(color_factor * self.colors_len)), self.colors_len - 1)
                brush.setColor(QtGui.QColor(self.colors[color]))
                painter.fillRect(rect,brush)

            peak = abs(self.peak - self.vmin) / (self.vmin - self.vmax)
            peak_bar = d_height - abs(round(peak * d_height))
            
            #draw peak
            rect2 = QtCore.QRect(
                left_padding,
                int(round(peak_bar)),
                int(d_width-round(right_padding+left_padding)),
                int(round(bar_height))
            )
            color_factor = peak_bar/d_height
            color = min(int(round(color_factor * self.colors_len)), self.colors_len - 1)
            brush.setColor(QtGui.QColor(self.colors[(self.colors_len-1) - color]))
            painter.fillRect(rect2,brush)

            font = painter.font()
            font.setFamily('Times')
            font.setPointSize(7)
            painter.setFont(font)

            painter.drawText(0,4, "-")
            
            
            num_markings = 10
            remainder = 1
            remainder_scaler = remainder/64
            remainder_px = d_height * remainder_scaler
            
            spacing = (d_height-(remainder_px*3))/num_markings
            offset = -1
            x = 0
            for i in range(num_markings):
                x -= 6
                position = int(round(spacing * (i+1)))+offset
                number = x# 0 - (60-abs(i))
                font.setPointSize(5)
                painter.setFont(font)
                # painter.drawText(10,position-5, "-")
                font.setPointSize(8)
                painter.setFont(font)
                if number != 0:
                    painter.drawText(3, position , "{}".format(number)+"_")

            # painter.drawText(0,d_height+10, "{}-".format(self.vmin))
            painter.end()
            
            
            
        except Exception as err:
            print('meter error' + str(err))

       

    def _trigger_refresh(self):
        self.update()


    def setValue(self, level, peak):
        self.value = level
        self.peak = peak
        
        if level < -64:
            self.value = -64
        if peak < -64:
            self.peak = -64
            
        if level > 0:
            self.value = 0
            
        if peak > 0:
            self.peak = 0
        
        self._trigger_refresh()

    def setVmin(self, vmin):
        self.vmin = vmin
    
    def setVmax(self, vmax):
        self.vmax = vmax

    

if __name__ == "__main__":
    app = QtWidgets.QApplication([])
    volume = AudioLevelMeter(-64,0,260,60)
    volume.setValue(-6, -2)
    volume.show()
    app.exec()