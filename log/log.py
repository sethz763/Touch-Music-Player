import logging
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QObject, Signal, QUrl, Slot, QThread
from PySide6.QtMultimedia import QMediaPlayer, QMediaMetaData

from inspect import currentframe, getframeinfo
from log.Save_To_Excel import Save_To_Excel
from persistence.SaveSettings import SaveSettings


class Log(QObject):
    
    get_info_signal = Signal(str, str, int) #filename, title, num_entries
    get_sheet_signal = Signal(list, list)
    
    def __init__(self):
        super().__init__()
        self.settings_obj = SaveSettings('log_settings.json')
        self.settings = self.settings_obj.get_settings()
        if 'logging_enabled' in self.settings:
            self.logging_enabled = self.settings['logging_enabled']
        else:
            self.logging_enabled = False
        self.log_file = Save_To_Excel('','',self)
        
    @Slot(str)
    def load(self, filename:str):
        self.log_file.load(filename)
        self.log_file.set_filename(filename)
        self.log_file.set_show_name(self.log_file.title)
        self.get_info_signal.emit(self.log_file.filename, self.log_file.title, self.log_file.get_num_entries())
        self.get_sheet_signal.emit(self.log_file.music_log_sheet.values, self.log_file.merged_cells)
        
    @Slot(dict, str, datetime)
    def log(self, metadata:dict={}, source:str='', tod_start:datetime=None):
        if self.logging_enabled == True:
            artist = metadata['Artist']
            song = metadata['Title']
        
            path = source
            path_split = path.split('/')
            end = len(path_split)-1
            filename = path_split[end]
            
            tod_end = datetime.now()
            duration_played = tod_end - tod_start
            tod_start = tod_start.strftime('%H:%M:%S')
            tod_end = tod_end.strftime('%H:%M:%S')
            
          
            total_seconds = duration_played.total_seconds()
            
            
            hours = int(total_seconds // 3600) 
            minutes = int((total_seconds % 3600) // 60) 
            seconds = int(total_seconds % 60) 
            hundredths = int((total_seconds % 1) * 100) 
            # Format the string 
            duration_played = f"{hours:02}:{minutes:02}:{seconds:02}.{hundredths:02}"
            
            
            #convert time to string format  (old method displays nanoseconds)
            # duration_played = str(duration_played)
            duration_played = f"{hours:02}:{minutes:02}:{seconds:02}.{hundredths:02}"
            
            try:
                #if there is no metadata just use the filenmame
                if metadata['Title'] == '':
                    
                    data = {'ARTIST': '',
                            'SONG': '',
                            'FILENAME': filename, 
                            'TIME_START':tod_start, 
                            'TIME_END':tod_end, 
                            'DURATION_PLAYED':duration_played}
                
                if metadata['Title'] != '':
                    data = {'ARTIST': artist,
                            'SONG': song,
                            'FILENAME': filename, 
                            'TIME_START':tod_start, 
                            'TIME_END':tod_end, 
                            'DURATION_PLAYED':duration_played}
                    
                self.log_file.update_log(data)
                self.get_sheet_info()
                # self.get_sheet_signal.emit(self.log_file.music_log_sheet.values, self.log_file.merged_cells)
                    
            except Exception as e:
                info = getframeinfo(currentframe()) 
                logging.info(f'{e}{info.filename}:{info.lineno}')
                
    @Slot(bool)
    def enable_disable_logging(self, enabled):
        self.logging_enabled = enabled
    
    @Slot(str, str)
    def create_sheet(self, filename:str, title:str):
        self.log_file.filename = filename
        self.log_file.title = title
        self.log_file.start_new_log(filename, title)
        self.get_sheet_info()
     
    @Slot()    
    def get_sheet_info(self):
        self.get_info_signal.emit(self.log_file.filename, self.log_file.title, self.log_file.get_num_entries())
        self.get_sheet_signal.emit(list(self.log_file.music_log_sheet.values), self.log_file.merged_cells)
        
    @Slot()
    def clear_sheet(self):
        self.log_file.clear()
        self.get_sheet_signal.emit(list(self.log_file.music_log_sheet.values), self.log_file.merged_cells)