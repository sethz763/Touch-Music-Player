import sys
import os
import typing
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QGridLayout, QWidget, QApplication, QHBoxLayout, QSpacerItem, QRadioButton, QSlider, QLabel, QComboBox, QMainWindow, QLineEdit, QSpinBox, QFileDialog
from PySide6.QtGui import QColor, QAction, QDrag, QFont, QIcon
from PySide6 import QtCore, QtGui, QtPdfWidgets
from PySide6 import QtWidgets

from PySide6.QtMultimedia import QAudioDevice, QAudioOutput, QMediaDevices, QMediaPlayer

from PySide6.QtCore import QUrl, QSize
from PySide6.QtCore import QObject, Signal

import fleep
from fleep import *

from SoundFileButton import SoundFileButton


class HelpWindow(QWidget):
    def __init__(self, height=300, width=500, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Fixed
        )
        
        self.setWindowFlags(QtCore.Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle('Help')

        self.setFixedSize(width, height)

        self.folder = ''
        self.files = []

        self.label = QLabel('Hot Keys')
        font = QFont('Arlia', 20)
        self.label.setFont(font)
        self.main_layout = QGridLayout()
        self.setLayout(self.main_layout)
        self.main_layout.addWidget(self.label, 0, 0, 1,6, QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignCenter)

if __name__=='__main__':
    app = QtWidgets.QApplication([])
    help_window = HelpWindow()
    help_window.show()