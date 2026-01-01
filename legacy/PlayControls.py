import sys
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget, QApplication, QHBoxLayout, QSpacerItem, QCheckBox, QLabel
from PySide6.QtGui import QColor, QAction, QDrag, QFont, QIcon
from PySide6 import QtCore, QtGui, QtWidgets

from PySide6.QtCore import QUrl, QSize
from PySide6.QtCore import QObject, Signal

class PlayControls(QWidget):
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
        self.cue_mode_button = QPushButton('INSTANT \n MODE')
        self.font = QFont('Arial', 18, QFont.Weight.Bold)
        self.cue_mode_button.setFont(self.font)
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
        


if __name__=='__main__':
    app = QtWidgets.QApplication([])
    play_controls = PlayControls(50, 400)
    play_controls.show()
    app.exec()

