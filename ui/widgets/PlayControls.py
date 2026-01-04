import sys
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget, QApplication, QHBoxLayout, QSpacerItem, QCheckBox, QLabel
from PySide6.QtGui import QColor, QAction, QDrag, QFont, QIcon
from PySide6 import QtCore, QtGui, QtWidgets

from PySide6.QtCore import QUrl, QSize
from PySide6.QtCore import QObject, Signal

class PlayControls(QWidget):
    transport_play = Signal()
    transport_pause = Signal()
    transport_stop = Signal()
    transport_next = Signal()

    loop_enabled_toggled = Signal(bool)
    loop_override_toggled = Signal(bool)
    auto_fade_toggled = Signal(bool)

    def __init__(self, height=50, width=400, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Fixed
        )

        self.layout = QHBoxLayout()
        self.setLayout(self.layout)
        self.spacer = QSpacerItem(10, 50, QtWidgets.QSizePolicy.Policy.Fixed)

        # self.play_img = QPixmap('assets\play_icon.png')
        # self.pause_img = QPixmap('assets\pause_icon.png')
        # self.loop_img = QPixmap('assets\stop_icon.png')

        self.stop_button = QPushButton()
        self.play_button = QPushButton()
        self.pause_button = QPushButton()
        self.loop_button = QPushButton()
        self.cue_mode_button = QPushButton('AUTO\nFADE')
        self.font = QFont('Arial', 18, QFont.Weight.Bold)
        self.cue_mode_button.setFont(self.font)
        self.cue_mode_button.setCheckable(True)
        self.next_button = QPushButton('NEXT')
        self.next_button.setFont(self.font)
        self.next_button.setFixedSize(120,85)
        

        self.play_icon = QIcon('assets\play_icon.png')
        self.pause_icon = QIcon('assets\pause_icon.png')
        self.loop_icon = QIcon('assets\loop_icon.png')
        self.stop_icon = QIcon('assets\stop_icon.png')

        self.icon_size = QSize(90, 90)

        self.stop_button.setFixedSize(120,85)
        self.stop_button.setIconSize(self.icon_size)

        self.play_button.setFixedSize(120,85)
        self.play_button.setIconSize(self.icon_size)

        self.pause_button.setFixedSize(120,85)
        self.pause_button.setIconSize(self.icon_size)

        self.loop_button.setFixedSize(120,85)
        self.loop_button.setIconSize(self.icon_size)
        self.loop_button.setCheckable(True)

        self.loop_overide_checkbox = QCheckBox()
        self.loop_overide_checkbox.setStyleSheet("""QCheckBox::indicator {width: 30;
                                                                        height: 30;}""")
        self.loop_overide_checkbox.setChecked(False)
        self.loop_overide_label = QLabel('Overide\nClip\nLoop')

        self.cue_mode_button.setFixedSize(120,85)

        self.stop_button.setIcon(self.stop_icon)
        self.play_button.setIcon(self.play_icon)
        self.pause_button.setIcon(self.pause_icon)
        self.loop_button.setIcon(self.loop_icon)

        self.layout.addWidget(self.stop_button)
        self.layout.addSpacerItem(self.spacer)
        self.layout.addWidget(self.cue_mode_button)
        self.layout.addSpacerItem(self.spacer)
        self.layout.addWidget(self.play_button)
        self.layout.addSpacerItem(self.spacer)
        self.layout.addWidget(self.pause_button)
        self.layout.addSpacerItem(self.spacer)
        self.layout.addWidget(self.loop_button)
        self.layout.addSpacerItem(self.spacer)
        self.layout.addWidget(self.next_button)
        self.layout.addSpacerItem(self.spacer)
        self.layout.addWidget(self.loop_overide_checkbox)
        self.layout.addWidget(self.loop_overide_label)

        # Transport wiring (emit signals; MainWindow routes to EngineAdapter)
        self.play_button.clicked.connect(self.transport_play.emit)
        self.pause_button.clicked.connect(self.transport_pause.emit)
        self.stop_button.clicked.connect(self.transport_stop.emit)
        self.next_button.clicked.connect(self.transport_next.emit)

        # Loop + Auto Fade
        self.loop_button.toggled.connect(self.loop_enabled_toggled.emit)
        self.loop_overide_checkbox.toggled.connect(self.loop_override_toggled.emit)
        self.cue_mode_button.toggled.connect(self.auto_fade_toggled.emit)
        


if __name__=='__main__':
    app = QtWidgets.QApplication([])
    play_controls = PlayControls(50, 400)
    play_controls.show()
    app.exec()

