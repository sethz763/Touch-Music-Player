import sys
from PySide6.QtWidgets import (QApplication, 
                             QWidget, 
                             QPushButton, 
                             QVBoxLayout,
                             QHBoxLayout,
                             QGesture, 
                             QGestureEvent, 
                             QSwipeGesture, 
                             QPanGesture,
                             QPinchGesture, 
                             QSizePolicy, 
                             QMenu, 
                             QColorDialog,
                             QDialog, 
                             QLineEdit, 
                             QLabel, 
                             QInputDialog)
from PySide6.QtCore import Qt, QRect, QEvent, QObject, QPoint, Signal, Slot
from PySide6.QtGui import QPainter, QColor, QMouseEvent, QAction, QPaintDevice
from SoundFileButton import SoundFileButton
from AddTracksWindow import AddTracksWindow
from BankWidget import BankWidget

from typing import List

import time

class DragSelectWidget(QWidget):
    def __init__(self, parent:QWidget):
        super().__init__()
        self.setParent(parent)
        parent.installEventFilter(self)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # A list to store the buttons
        self.buttons:List[SoundFileButton] = parent.buttons
        
        self.selected_buttons:List[SoundFileButton] = []
        
        self.current_buttons:List[SoundFileButton] = []
        
        self.set_buttons()

        # Initialize variables for selection
        self.dragging = False
        self.selection_rect = QRect()
        self.drag_num = 0
        # Create some buttons   
        self.loop = False
        self.num = 0
        
        self.buttons_enabled = True
        self.open_context_menu = False
        
    # def event(self, event:QEvent):
       
            
    #     return super().event(event)
    
    def set_buttons(self):
        self.buttons:List[SoundFileButton] = self.parent().buttons
        self.current_buttons = self.parent().current_bank_widget.findChildren(SoundFileButton)
        self.buttons_geometry_list:List[QRect] = []
        for button in self.current_buttons:
            self.buttons_geometry_list.append(button.geometry())
    
    #old event filter - causes audio to glitch
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Gesture:
            self.handle_gesture(event)
        
        if event.type() == QEvent.Type.MouseButtonRelease:
            #map mouse position to global
            global_pos = event.globalPosition().toPoint()
            local_pos = self.mapFromGlobal(global_pos)
            pos = event.pos()
            for button in self.parent().current_bank_widget.findChildren(SoundFileButton):
                if button.geometry().contains(local_pos):
                    # button.start_flashing()
                    pos = button.mapFromGlobal(global_pos)
                    if self.dragging == False and self.buttons_enabled == True:
                        if button.fade_button.isVisible() and button.fade_button.geometry().contains(pos):
                            button.fade_button.released.emit()
                            break
                        elif event.button() == Qt.MouseButton.LeftButton:
                            button.released.emit()
                            break
                        elif event.button() == Qt.MouseButton.RightButton:
                            button.contextMenuEvent(event)     
                    
            if event.button() == Qt.MouseButton.LeftButton and self.dragging:
            # End the drag selection
                self.drag_num = 0
                self.dragging = False
                self.selected_buttons.clear()
                # Perform selection
                
                bank = self.parent().current_bank_widget
                buttons = bank.findChildren(SoundFileButton)
                for button in buttons: 
                    if self.selection_rect.intersects(button.geometry()):
                        text_color = button.get_text_color()
                        button.setStyleSheet(f"background-color: gray; color: {text_color.name()};", True)  # Highlight selected button
                        self.selected_buttons.append(button)
                        

                # Reset the selection rectangle
                self.selection_rect = QRect()
                self.update()  # Repaint to clear the selection rectangle
                self.contextMenuEvent(event)
                
            if event.button() == Qt.MouseButton.LeftButton:
                bank = self.parent().current_bank_widget
                buttons = bank.findChildren(SoundFileButton)
                for button in buttons:
                    button.clear_selection()
                    
        else:
            self.event(event)
                
        return super().eventFilter(obj, event)
    
    # def eventFilter(self, obj, event):
    #     if event.type() == QEvent.Type.MouseButtonRelease:
    #         #map mouse position to global
    #         t1 = time.time()
    #         global_pos = event.globalPosition().toPoint()
    #         local_pos = self.mapFromGlobal(global_pos)
    #         pos = event.pos()
    #         for i, button_geometry in enumerate(self.buttons_geometry_list):
    #             if button_geometry.contains(local_pos):
    #                 button = self.current_buttons[i]
    #                 pos = button.mapFromGlobal(global_pos)
    #                 if button.fade_button.isVisible() and button.fade_button.geometry().contains(pos):
    #                     button.fade_button.released.emit()
    #                     break
    #                 else:
    #                     button.released.emit()
    #                     break
    #         dur = time.time()-t1
    #         print(f'EVENT FILTER -  DURATION: {dur}')
                    
    #     else:
    #         self.event(event)
                
    #     return super().eventFilter(obj, event)
    
   
    
    def mousePressEvent(self, event:QMouseEvent):
        

        if event.button() == Qt.MouseButton.LeftButton:
            # if len(self.selected_buttons) > 0:
            self.selected_buttons.clear()
            pos = self.parent().current_bank_widget.mapFromGlobal(event.globalPosition().toPoint())
            # Start the drag selection
            self.selection_rect = QRect(pos, pos)
            # for button in self.parent().current_bank_widget.findChildren(SoundFileButton):
            #     if not button.flashing_timer.isActive():
            #         button.stop_flashing()
        
        return super().mousePressEvent(event)
        
        
    def mouseMoveEvent(self, event:QMouseEvent):
        self.drag_num += 1
        if self.drag_num > 3:
            self.dragging = True
            self.open_context_menu = True
        if self.dragging:
            # Update the selection rectangle as the mouse moves
            pos = self.parent().current_bank_widget.mapFromGlobal(event.globalPosition().toPoint())
            self.selection_rect.setBottomRight(pos)
            self.update()  # Repaint to update the selection rectangle
            
        return super().mouseMoveEvent(event)

    # def mouseReleaseEvent(self, event):
    #     if event.button() == Qt.MouseButton.LeftButton and self.dragging:
    #         # End the drag selection
    #         self.drag_num = 0
    #         self.dragging = False
    #         self.selected_buttons.clear()
    #         # Perform selection
            
    #         parent = self.parent()
    #         bank = parent.current_bank_widget
    #         buttons = bank.findChildren(SoundFileButton)
    #         for button in buttons: 
    #             if self.selection_rect.intersects(button.geometry()):
    #                 text_color = button.get_text_color()
    #                 button.setStyleSheet(f"background-color: gray; color: {text_color.name()};", True)  # Highlight selected button
    #                 self.selected_buttons.append(button)
                    

    #         # Reset the selection rectangle
    #         self.selection_rect = QRect()
    #         self.update()  # Repaint to clear the selection rectangle
    #         self.contextMenuEvent(event)
            
    
    def contextMenuEvent(self, event):
        if self.open_context_menu:
            self.open_context_menu = False
            menu = QMenu(self)
            color = QColor(200,200,200)

            menu.setStyleSheet(f"background-color: {color.name()};"
                            "color: black;")
            
            change_track_action = QAction("Select Track", self)
            change_track_action.triggered.connect(self.change_tracks)
            menu.addAction(change_track_action)
            
            change_color_action = QAction("Background Color", self)
            change_color_action.triggered.connect(self.change_color)
            menu.addAction(change_color_action)

            change_text_color_action = QAction("Text Color", self)
            change_text_color_action.triggered.connect(self.change_text_color)
            menu.addAction(change_text_color_action)

            change_text_action = QAction("Change Text", self)
            change_text_action.triggered.connect(self.change_text)
            menu.addAction(change_text_action)

            gain_action = QAction('Set Gain', self)
            gain_action.triggered.connect(self.open_gain_dialog)
            menu.addAction(gain_action)

            copy_button_action = QAction('Copy', self)
            copy_button_action.triggered.connect(self.copy_buttons)
            menu.addAction(copy_button_action)

            paste_button_action = QAction('Paste', self)
            paste_button_action.triggered.connect(self.paste_buttons)
            menu.addAction(paste_button_action)
            
            self.loop_action = QAction('Enable Loop', menu)
            self.loop_action.triggered.connect(self.set_loop_enabled)
            menu.addAction(self.loop_action)
            
            self.loop_action = QAction('Disable Loop', menu)
            self.loop_action.triggered.connect(self.set_loop_disabled)
            menu.addAction(self.loop_action)
            
            reset_played_action = QAction('Reset Played Check', self)
            reset_played_action.triggered.connect(self.reset_played)
            menu.addAction(reset_played_action)

            menu.addSeparator()

            clear_action = QAction("Clear", self)
            clear_action.triggered.connect(self.clear)
            menu.addAction(clear_action)

            menu.exec(self.mapToGlobal(event.pos()))
        
    def change_tracks(self):
        tracks, ok = AddTracksWindow.select_files(AddTracksWindow(self.parent().parent))
        
        if ok:
            for i, button in enumerate(self.selected_buttons):
                button.clear()
                if i < len(tracks):
                    AddTracksWindow.set_track(AddTracksWindow(self.parent().parent), tracks[i], button)
                    
        button.save_settings()
    
    def change_text(self):
        pass
    
    def copy_buttons(self):
        self.parent().button_clipboard_list = self.selected_buttons.copy()
        # for button in self.parent().button_clipboard_list:
        #     print(button.qurl)
        print(self.selected_buttons)
    
    def paste_buttons(self):
        try:
            for copy_btn, button in zip(self.parent().button_clipboard_list, self.selected_buttons):
            # for i, button in enumerate(self.selected_buttons):
                # copy_btn = self.parent().button_clipboard_list[i]
                copy_btn.clear_selection()
                button.qurl = copy_btn.qurl
                button.filename = copy_btn.filename
                button.in_point = copy_btn.in_point
                button.out_point = copy_btn.out_point
                button.setText(copy_btn.text())
                button.background_color1 = copy_btn.get_background_color()
                button.text_color = copy_btn.get_text_color()
                button.setStyleSheet(f"color: {button.text_color.name()};"
                                    f"background-color: {copy_btn.background_color1.name()};"
                                    "border:3px solid black;")
                connections = button.receivers(button.released)
                print(f'conneciton: {connections}')
                # if connections == 0:
                button.released.connect(button.emit_play_signal)
                button.setEnabled(True)
                button.save_settings()
            
            self.set_buttons()
            
                

        except Exception as e:
            print(e)
    
    def set_loop_enabled(self):
        for button in self.selected_buttons:
            button.loop = True
            button.update()
            button.save_settings()
        
    def set_loop_disabled(self):
        for button in self.selected_buttons:
            button.loop = False
            button.update()
            button.save_settings()
        
    def reset_played(self):
        for button in self.selected_buttons:
            button.set_played(False)
            button.update()
            
    def change_color(self):
        color = QColorDialog.getColor(title='Change Background Color')
        for button in self.selected_buttons:
            button.text_color = button.get_text_color()
            if color.isValid():
                button.setStyleSheet(f"background-color: {color.name()};"
                                    f"color: {button.text_color.name()};"
                                    "border:3px solid black;", False)
                button.background_color1 = color
                
            button.background_color1 = color
            button.save_settings()
        
    def change_text_color(self):
        color = QColorDialog.getColor(title='Change Text Color')
        
        if color.isValid():
            for button in self.selected_buttons:
                button.setStyleSheet(f"color: {color.name()};"
                                f"background-color: {button.background_color1.name()};"
                                "border:3px solid black;")
            
                button.text_color = button.get_text_color()
                button.save_settings()

    def paintEvent(self, event):
        
        if self.dragging:
            # Draw the selection rectangle
            painter = QPainter(self)
            painter.setPen(Qt.PenStyle.DashLine)
            painter.setBrush(QColor(100, 100, 100, 30))  # Transparent blue color
            painter.drawRect(self.selection_rect)
            painter.end()
            
        return super().paintEvent(event)
            
    
        
    def open_gain_dialog(self):
        input = QInputDialog()
        text, ok = input.getText(self, "Change Text", "Enter new text:", QLineEdit.EchoMode.Normal, '0')

        if ok:
            try:
                gain = int(text)
                self.set_gain(gain)
            except ValueError:
                error = "not a valid gain value"
        
    def set_gain(self, gain:int):
        for button in self.selected_buttons:
            button.set_gain(gain)
            
    def clear(self):
        for button in self.selected_buttons:
            button.clear()
        
        self.set_buttons()
            
    @Slot(bool)
    def set_buttons_enabled(self, enabled:bool):
        self.buttons_enabled = enabled
            

class TestWidget(QWidget):
    def __init__(self):
        super().__init__()        
        
        self.buttons:List[QPushButton] = []
        
        layout = QVBoxLayout(self)
        for i in range(5):
            button = QPushButton(f"Button {i+1}", self)
            layout.addWidget(button)
            self.buttons.append(button)
            button.pressed.connect(self.pressed)
            button.released.connect(self.released)
            
        self.drag_select_widget = DragSelectWidget(self)
        
                    
    def pressed(self):
        button:QPushButton = self.sender()
        button.setStyleSheet("background-color: green;")
        
    def released(self):       
        button:QPushButton = self.sender()
        button.setStyleSheet("background-color: white;")
        

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = TestWidget()

    window.show()
    sys.exit(app.exec())