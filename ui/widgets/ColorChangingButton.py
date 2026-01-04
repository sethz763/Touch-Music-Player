import sys
from PySide6.QtWidgets import QApplication, QPushButton, QMenu, QVBoxLayout, QWidget, QColorDialog, QInputDialog, QFileDialog
from PySide6.QtGui import QColor, QAction, QDrag, QFont

from PySide6.QtCore import QUrl
from PySide6.QtCore import QObject, Signal

class SaveSignal(QObject):
    save_signal = Signal(str, dict, name='SaveSig') # key, dict (save, color, text, qurl for buttons)

class ColorChangingButton(QPushButton):
    def __init__(self, parent, *args, **kwargs):
        super(ColorChangingButton, self).__init__(parent, *args, **kwargs)
        self.save_signal = SaveSignal()
        self.settings = {'text': self.text(),'stylesheet':self.styleSheet()}

    def contextMenuEvent(self, event):
        menu = QMenu(self)

        # change_color_action = QAction("Change Color", self)
        # change_color_action.triggered.connect(self.change_color)
        # menu.addAction(change_color_action)

        change_text_action = QAction("Change Text", self)
        change_text_action.triggered.connect(self.change_text)
        menu.addAction(change_text_action)

        menu.exec(self.mapToGlobal(event.pos()))

    def change_color(self):
        color = QColorDialog.getColor()
        if color.isValid():
            self.setStyleSheet(f"background-color: {color.name()};")
        self.save_settings()

    def change_text(self):
        text, ok = QInputDialog.getText(self, "Change Text", "Enter new text:")
        if ok:
            text = text.split('|')
            new_text = ''
            for i, t in enumerate(text):
                new_text += t
                if(i+1 != len(text)):
                    new_text += '\n'
            self.setText(new_text)
        self.save_settings()

    def save_settings(self):
        self.settings = {'text': self.text(),'stylesheet':self.styleSheet()}
        self.save_signal.save_signal.emit(f'{self.objectName()}', self.settings)





# if __name__ == "__main__":
#     app = QApplication(sys.argv)
#     window = QWidget()
#     layout = QVBoxLayout()

#     button = ColorChangingButton("Right-click me!", window)
#     layout.addWidget(button)

#     window.setLayout(layout)
#     window.show()

#     sys.exit(app.exec())