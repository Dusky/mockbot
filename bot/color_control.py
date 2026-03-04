import random
import json

class ColorManager:
    def __init__(self, color_file='colors.json'):
        self.color_file = color_file
        self.load_colors()

    def load_colors(self):
        try:
            with open(self.color_file, 'r') as file:
                data = json.load(file)
                self.user_colors = data.get('user_colors', {})
                self.channel_colors = data.get('channel_colors', {})
                #print("Loaded colors:", self.user_colors, self.channel_colors)  # Debug print
        except FileNotFoundError:
            #print(f"Could not find {self.color_file}, initializing with empty colors.")  # Debug print
            self.user_colors = {}
            self.channel_colors = {}
        except json.JSONDecodeError:
            #print(f"Could not decode JSON from {self.color_file}, check if it's correctly formatted.")  # Debug print
            self.user_colors = {}
            self.channel_colors = {}

    def save_colors(self):
        data = {
            'user_colors': self.user_colors,
            'channel_colors': self.channel_colors
        }
        with open(self.color_file, 'w') as file:
            json.dump(data, file)
            #print("Saved colors:", data)  # Debug print

    def _generate_bright_hex(self):
        # Generate vivid, legible hex colors for dark backgrounds natively
        return f"#{random.randint(100, 255):02x}{random.randint(100, 255):02x}{random.randint(100, 255):02x}"

    def get_color(self, name, color_dict):
        needs_save = False
        
        if name not in color_dict:
            color_dict[name] = self._generate_bright_hex()
            needs_save = True
            
        # Seamlessly migrate old 8-bit ANSI integers to rich Hex codes
        elif isinstance(color_dict[name], int):
            color_dict[name] = self._generate_bright_hex()
            needs_save = True
            
        if needs_save:
            self.save_colors()
            
        return color_dict[name]

    def get_user_color(self, username):
        return self.get_color(username, self.user_colors)

    def get_channel_color(self, channel_name):
        return self.get_color(channel_name, self.channel_colors)
