from PySide6 import QtWidgets
from PySide6.QtWidgets import*
from PySide6.QtCore import QTimer, Qt, QUrl, QTime, QObject, Signal, QSize, Slot, QThread
from PySide6.QtMultimedia import QMediaMetaData, QAudioFormat

from typing import overload, List

from inspect import currentframe, getframeinfo

import sounddevice as sd
import time

class CheckSoundDevices(QObject):
    device_list_signal = Signal(list, tuple)
    
    def __init__(self):
        super().__init__()
        self.devices = {}
        self.apis = {}
        
    def get_devices(self):
        try:
            print('starting sounddevice re-initialization')
            sd._terminate()
            sd._initialize()
        
       
            QThread.msleep(100)

            self.devices = sd.query_devices()
            self.apis = sd.query_hostapis()
            
            self.device_list_signal.emit(self.devices, self.apis)
            
        except sd.PortAudioError as e:
            info = getframeinfo(currentframe())
            print(f'{e}{info.filename}:{info.lineno}')
