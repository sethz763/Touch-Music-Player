import json

class SaveSettings:
    def __init__(self, file_path):
        self.file_path = file_path
        self.settings = {}
        self.load_settings()

    def load_settings(self):
        try:
            file = open(self.file_path, 'r')
            self.settings = json.load(file)
                
        except FileNotFoundError:
            #if file doesn't exist make a blank dictionary
            print("settings file doesn't exist...creating one")
            self.settings = {}
            self.save_settings()

    def save_settings(self):
        try:
            with open(self.file_path, 'w') as file:
                json.dump(self.settings, file, indent=4)
        except:
            pass

    def get_setting(self, key):
        try:
            this_setting = self.settings.get(key)
        
        except:
            pass

        return this_setting
    
    def set_setting(self, key, value):
        self.settings[key] = value

    def get_settings(self):
        return self.settings

    def delete_settings(self, key):
        del self.settings[key]