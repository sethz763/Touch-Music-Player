#streamdeck_connector

from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.ImageHelpers import PILHelper

import PIL
from PIL import ImageDraw, Image

from PySide6.QtWidgets import QApplication, QLabel, QWidget
from PySide6.QtGui import QImage, QPainter, QFont, QColor
from PySide6.QtCore import QSize, QPoint, Qt, QObject, Signal, Slot

from ui.widgets.sound_file_button import SoundFileButton

class StreamDeckConnector(QObject):
    key_pressed = Signal(int)
    
    def __init__(self, deck=None):
        super().__init__()
        if deck == None:
            streamdecks = DeviceManager().enumerate()
            self.deck = streamdecks[0]
            self.deck.open()
        key_size = self.deck.key_image_format()['size']
        self.label = QLabel()
        self.label.setFixedSize(key_size[0], key_size[1])
        self.font = QFont('Arial', 40)
        self.label.setStyleSheet("background-color: blue; color: white;")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setFont(self.font)
        
        self.widgets = []
        self.deck.set_key_callback(self.key_change_callback)
        
    def set_widget(self, widget:QWidget, key):
        self.widgets[key] = widget
        
    def button_to_label(self, label, button:SoundFileButton):
        text = SoundFileButton.text()
        label.setText(text)

    def qlabel_to_streamdeck_image(self, label: QLabel, deck, key: int):
        """
        Capture QLabel content and send it to a Stream Deck button.
        """
        
        # 1. Grab QLabel as QPixmap
        pixmap = label.grab()  # Works even if QLabel is not visible on screen

        # 2. Convert QPixmap to QImage
        qimage = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB888)

        # 3. Convert QImage to bytes
        width = qimage.width()
        height = qimage.height()
        ptr = qimage.bits()
        img_bytes = bytes(ptr)

        # 4. Create a PIL Image from raw RGBA data
        pil_img = Image.frombytes("RGB", (width, height), qimage.bits().tobytes())

        # 5. Resize to Stream Deck key size
        key_image_format = deck.key_image_format()
        pil_img = pil_img.resize((key_image_format['size'][0], key_image_format['size'][1]))

        # 6. Convert to Stream Deck native format
        native_img = PILHelper.to_native_format(deck, pil_img)

        # # 7. Send to Stream Deck
        deck.set_key_image(key, native_img)
            
    def sound_file_button_to_key(self, btn:SoundFileButton, deck, key): 
        # converts sound file button to streamdeck key image by rendering text appropriately
    
        if btn.track_set:
            self.label.setText(btn.text())
            self.label.setWordWrap(True)
            # self.label.setText("TALIA AND JESSICA")
            text = self.label.text()
            words = text.split()
            word_count = len(words)
            #get longest word
            largest_word_length = 1
            size = 54
            new_words = []
            for word in words:
                if len(word) > largest_word_length:
                    largest_word_length = len(word)
                    if largest_word_length > 7:
                        largest_word_length = 8
                        break
                    
            for word in words:
                if len(word) > 8:
                    split = self.split_word(word,8)
                    for w in split:
                        new_words.append(w)
                        largest_word_length = 8
                                
                else:
                    new_words.append(word)
            
            new_text = " ".join(new_words)        
        
            self.label.setText(new_text)
            largest_word_length = 7
                
            if largest_word_length > 8:
                    size = 17
            
            elif largest_word_length < 9:
                    size = 54 - ((int(largest_word_length)*5) + 1)
                
            
            self.font = QFont('Arial', size)    
            self.label.setFont(self.font)
            self.qlabel_to_streamdeck_image(self.label, deck, key)
        
        else:
            blank_label = QLabel()
            blank_label.setStyleSheet("background-color: black;")
            self.qlabel_to_streamdeck_image(blank_label, deck, key)
            print(f'assigning blank to {key} ')
            
    
    def split_word(self, word:str, size:int)->str:
        chunks = [word[i:i+size] for i in range(0, len(word), size)]
        return chunks
        
        
    def key_change_callback(self, deck, key, state):
        if state:  # True = key pressed
            self.key_pressed.emit(key)
            print(f"Button {key} pressed!")
        else:
            print(f"Button {key} released!")


if __name__ == "__main__":
    import sys
    import time
    
    
    app = QApplication(sys.argv) 
     # Get connected StreamDeck devices
     
    streamdeck_widget_renderer = StreamDeckConnector()
    

    


    clr = 0
    
    while True:

        for key in range(0,32,1):
            button = SoundFileButton()
            button.setTrack("C:/Users/Seth Zwiebel/Music/All I Need - Radiohead.mp3")
            
            # streamdeck_widget_renderer.widget_to_streamdeck(label, deck,keyi)
            streamdeck_widget_renderer.sound_file_button_to_key(button, streamdeck_widget_renderer.deck, key)
            time.sleep(1)

    app.exec()
    
   
        
    
    
    
    

# for deck in streamdecks:
#     deck.open()
#     deck.reset()

#     # # Set brightness to 30%
#     # deck.set_brightness(100)

#     # Set an image on the first button
#     with open("example.png", "rb") as image_file:
#         image = image_file.read()
#         for key in range(32):
#             deck.set_key_image(key, image)

#     deck.reset()
#     # Close the device
#     deck.close()
    
    







