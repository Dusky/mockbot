import logging
from colorama import Fore
from datetime import datetime
import re
import os
from .color_control import ColorManager 
from logging.handlers import RotatingFileHandler

# ANSI color codes for consistent styling
YELLOW = "\x1b[33m"
RESET = "\x1b[0m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
PURPLE = "\x1b[35m"
CYAN = "\x1b[36m"
BLUE = "\x1b[34m"
BRIGHT_GREEN = "\x1b[92m"
BRIGHT_RED = "\x1b[91m"
BRIGHT_YELLOW = "\x1b[93m"
BRIGHT_BLUE = "\x1b[94m"

APP_LOG_FILE = 'app.log'

# Define month_colors at the module level
MONTH_COLORS = {
    "JAN": "196", "FEB": "201", "MAR": "46", "APR": "226",
    "MAY": "201", "JUN": "214", "JUL": "196", "AUG": "46",
    "SEP": "214", "OCT": "201", "NOV": "226", "DEC": "46"
}

class Logger:
    def __init__(self):
        self.color_manager = ColorManager()  # Initialize the ColorManager
        self.logger = logging.getLogger('bot') # Initialize logger instance attribute
        self.bad_words = self.load_bad_patterns()
        self.active_channel_filter = None
        self.setup_logger() # Call setup_logger in constructor
        
    def setup_logger(self):
        # self.logger is already initialized in __init__
        # Configure the existing logger instance
        self.logger.setLevel(logging.INFO)

        # Prevent propagation to root logger to avoid duplicate console output
        self.logger.propagate = False

        # Prevent adding multiple handlers to the same logger
        if not self.logger.handlers:
            # Define log format for file handler (for app.log)
            file_formatter = logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s')

            # Set up a rotating file handler for app.log
            file_handler = RotatingFileHandler(
                APP_LOG_FILE, 
                maxBytes=1048576,  # 1MB
                backupCount=5  # Keep at most 5 log files
            )
            file_handler.setFormatter(file_formatter)
            file_handler.setLevel(logging.INFO) # File handler logs INFO and above
            self.logger.addHandler(file_handler)

            # Add the custom handler for colorized terminal output
            # CustomHandler will handle its own formatting/coloring for console
            # So, a basic formatter or even no formatter can be used here if CustomHandler overrides formatMessage
            console_formatter = logging.Formatter('%(message)s') 
            custom_handler = CustomHandler()
            custom_handler.setFormatter(console_formatter) 
            custom_handler.setLevel(logging.INFO) # Console handler also logs INFO and above
            self.logger.addHandler(custom_handler)

    def load_bad_patterns(self):
        patterns = []
        filepath = 'badwords.txt'
        
        # Ensure the file exists, create if not
        if not os.path.exists(filepath):
            print(f"'{filepath}' not found. Creating an empty file.")
            try:
                open(filepath, 'a').close()  # Create an empty file
            except Exception as e:
                print(f"Error creating '{filepath}': {e}")
                return patterns # Return empty patterns if file creation fails
        
        try:
            with open(filepath, 'r', encoding='utf-8') as file:
                for line in file:
                    pattern = line.strip()
                    if pattern:
                        # Compile each pattern to a regex object for faster matching
                        patterns.append(re.compile(pattern, re.IGNORECASE))
        # Removed redundant FileNotFoundError, as we attempt to create the file above.
        # A general Exception catch can be useful for other I/O errors.
        except Exception as e:
            print(f"Error loading bad words from '{filepath}': {e}")
        
        return patterns

    def message_contains_badword(self, message):
        # Check if the message matches any compiled regex patterns
        return any(pattern.search(message) for pattern in self.bad_words)


    def log_message(self, channel, username, message_content, is_bot_message=False):
        """
        Log a message to console and optionally to corpus file.

        Args:
            channel: Channel name
            username: Username of the message author
            message_content: The message text
            is_bot_message: If True, skip writing to corpus file (prevents bot from learning its own responses)
        """
        if self.message_contains_badword(message_content):
            # Log to console that message was not processed due to bad word
            # This specific format will be printed directly.
            print(f"{RED}Message from {username} in #{channel} not logged due to bad word usage.{RESET}")
            # Optionally, log a sanitized version to app.log if needed for audit, e.g.,
            # self.logger.info(f"Blocked message from {username} in #{channel} due to bad word.")
            return False

        timestamp_dt = datetime.now()
        day_year_time = timestamp_dt.strftime('%d %y %H:%M:%S')
        month_str = timestamp_dt.strftime("%b").upper()

        # Use the module-level MONTH_COLORS
        colored_month = f"\x1b[38;5;{MONTH_COLORS[month_str]}m{month_str}\x1b[0m"

        timestamp_color_index = 14  # Cyan
        colored_timestamp_str = f"\x1b[38;5;{timestamp_color_index}m{day_year_time}\x1b[0m"

        user_color_idx = self.color_manager.get_user_color(username)
        channel_color_idx = self.color_manager.get_channel_color(channel)
        colored_username_str = f"\x1b[38;5;{user_color_idx}m{username}\x1b[0m"
        colored_channel_str = f"\x1b[38;5;{channel_color_idx}m{channel}\x1b[0m"

        # Construct the colorized message for direct console output
        console_log_msg = f"{colored_month} {colored_timestamp_str} - #{colored_channel_str} | <{colored_username_str}>: {message_content}"

        # Print the rich, colorized message directly to the console
        if not self.active_channel_filter or self.active_channel_filter.lower() == channel.lower():
            print(console_log_msg)


        # Prepare a sanitized, uncolored message for app.log
        # The FileHandler will add its own timestamp, level, etc.
        # Basic HTML tag removal for sanitization, can be expanded
        sanitized_message_text = re.sub(r'<[^>]*>', '', message_content) 
        file_log_msg_content = f"#{channel} | <{username}>: {sanitized_message_text}"
        
        # Log the uncolored, sanitized message. This will go to app.log (via FileHandler)
        # Mark the record to skip console output since we already printed it above
        # Use a custom level or extra parameter to signal CustomHandler to skip it
        self.logger.info(file_log_msg_content, extra={'skip_console': True})
        return True
    
    def log_warning(self, message):
        """Log a warning with consistent yellow styling"""
        parts = message.split(':', 1) if ':' in message else [message, '']
        if len(parts) > 1:
            formatted_msg = f"{YELLOW}{parts[0]}:{BRIGHT_YELLOW}{parts[1]}{RESET}"
        else:
            formatted_msg = f"{YELLOW}{message}{RESET}"
        self.logger.warning(formatted_msg) # This will be handled by CustomHandler for console
    
    def log_error(self, message): # This was the method named log_error
        """Log an error with consistent red styling"""
        parts = message.split(':', 1) if ':' in message else [message, '']
        if len(parts) > 1:
            formatted_msg = f"{RED}{parts[0]}:{BRIGHT_RED}{parts[1]}{RESET}"
        else:
            formatted_msg = f"{RED}{message}{RESET}"
        self.logger.error(formatted_msg) # This will be handled by CustomHandler for console
    
    def log_success(self, message):
        """Log a success message with green styling"""
        parts = message.split(':', 1) if ':' in message else [message, '']
        if len(parts) > 1:
            formatted_msg = f"{GREEN}{parts[0]}:{BRIGHT_GREEN}{parts[1]}{RESET}"
        else:
            formatted_msg = f"{GREEN}{message}{RESET}"
        self.logger.info(formatted_msg) # This will be handled by CustomHandler for console
    
    def log_event(self, event_type, details):
        """Log a bot event with consistent styling"""
        event_msg = f"{PURPLE}{event_type}{RESET}: {CYAN}{details}{RESET}"
        self.logger.info(event_msg) # This will be handled by CustomHandler for console
        
    def log_command(self, user, command, result=None):
        """Log a command execution with consistent styling"""
        user_colored = f"\x1b[38;5;{self.color_manager.get_user_color(user)}m{user}\x1b[0m"
        cmd_msg = f"{BLUE}Command{RESET}: {user_colored} used {BRIGHT_BLUE}{command}{RESET}"
        if result:
            cmd_msg += f" → {result}"
        self.logger.info(cmd_msg) # This will be handled by CustomHandler for console

    def log_info(self, message, color=None): # This is the general log_info
        """Log an info message with optional color"""
        if color is None:
            parts = message.split(':', 1) if ':' in message else [message, '']
            if len(parts) > 1:
                message_formatted = f"{GREEN}{parts[0]}:{CYAN}{parts[1]}{RESET}"
            else:
                message_formatted = f"{GREEN}{message}{RESET}"
        else:
            message_formatted = f"{color}{message}{RESET}"
        self.logger.info(message_formatted) # This will be handled by CustomHandler for console

    def print_message(self, message): # This seems like a direct print, maybe for specific unlogged messages
        print(message)

    def log_settings(self, time_between_messages, lines_between_messages):
        # Using log_info which now correctly passes to self.logger.info
        self.log_info(f"time_between_messages: {time_between_messages}", Fore.YELLOW)
        self.log_info(f"lines_between_messages: {lines_between_messages}",Fore.YELLOW)

    def error(self, message): # This was the second method named error, logging as info
        # This method should log errors. It was previously self.logger.info(message)
        # It should use the standard error logging mechanism.
        # For colored error output, it can call self.log_error or directly use self.logger.error
        # If self.log_error is preferred for consistent styling:
        self.log_error(message)
        # Or, if direct logging is preferred (CustomHandler will color it based on level):
        # self.logger.error(message) 

    # The second, redundant setup_logger method has been removed.
    # The setup_logger call is now in __init__.
    
    
#####################################################
    # This section is for modifying the
    # "successfully logged onto twitch ws" message
    # that is provided by twitchio
#####################################################
class CustomHandler(logging.StreamHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def emit(self, record):
        # Skip console output if the record was marked to skip (already printed directly)
        if hasattr(record, 'skip_console') and record.skip_console:
            return

        # Get the message that would be output by the formatter
        # If the logger call already provided a pre-formatted/colored string,
        # record.getMessage() might just return that.
        # If the logger call provided a raw string, and this handler has a formatter,
        # record.getMessage() will return the formatted string.
        msg_str = self.format(record) # Use self.format to apply the handler's formatter

        # Skip further custom colorizing if the message (after formatter) already contains ANSI color codes.
        # This happens if a method like log_warning, log_error, etc., was called, which pre-colors.
        # Or if log_message printed its own colorized version and then logged an uncolored one.
        # Use the module-level MONTH_COLORS here
        if '\x1b[' in msg_str and not msg_str.startswith(tuple(MONTH_COLORS.keys())): # Avoid re-coloring our direct prints from log_message
             # Check if it ends with RESET, if not, add it for safety.
            if not msg_str.endswith(RESET):
                msg_str += RESET
            print(msg_str) # Print the already colored message
            return

        # Colorize specific known TwitchIO messages or other generic log messages
        # that were not pre-colored by our specific log_type methods.
        
        # Use record.message for matching against original uncolored message content
        # if the formatter added timestamps/levels that would interfere with matching.
        original_uncolored_msg = record.message 
        
        colored_output = msg_str # Start with the (potentially) formatter-applied message

        if 'Successfully logged onto Twitch WS' in original_uncolored_msg:
            colored_output = f"{GREEN}Successfully logged onto Twitch WS. {PURPLE}Bot is now ready.{RESET}"
        elif 'Connected to' in original_uncolored_msg and 'channel' in original_uncolored_msg:
            # Example: "Connected to #somechannel"
            # This simple replace might color "Connected to" even if it's part of a username.
            # More robust regex could be used if this becomes an issue.
            colored_output = msg_str.replace('Connected to', f"{GREEN}Connected to{RESET}")
            colored_output = colored_output.replace('channel', f"{PURPLE}channel{RESET}") # Be careful with "channel" in messages
        elif 'Disconnected from' in original_uncolored_msg:
            colored_output = msg_str.replace('Disconnected from', f"{RED}Disconnected from{RESET}")
        # Removed 'Received message from' as log_message handles chat console output directly.
        # elif 'Received message from' in original_uncolored_msg:
        #     parts = msg_str.split(':', 1)
        #     if len(parts) > 1:
        #         colored_output = f"{BLUE}{parts[0]}:{CYAN}{parts[1]}{RESET}"
        elif 'Sending message to' in original_uncolored_msg:
            # Example: "Sending message to #somechannel: Hello there"
            # This is a common pattern for bot's own messages if logged through self.logger.info
            parts = msg_str.split(':', 1) 
            if len(parts) > 1: 
                # Color "Sending message to #channel" part
                # Regex might be better here to accurately capture channel name for coloring
                sending_part = parts[0] # "Sending message to #channel"
                message_part = parts[1] # " message content"
                
                # Attempt to color channel name specifically if possible
                match = re.match(r"(Sending message to )(#?\w+)", sending_part)
                if match:
                    channel_name_for_color = match.group(2).lstrip('#')
                    # Note: self.color_manager is not directly accessible here unless passed to CustomHandler
                    # For simplicity, using a generic color for channel part here.
                    colored_sending_part = f"{GREEN}{match.group(1)}{PURPLE}{match.group(2)}{RESET}"
                    colored_output = f"{colored_sending_part}:{message_part}"
                else:
                    colored_output = f"{GREEN}{sending_part}{RESET}:{message_part}"
            else: 
                colored_output = f"{GREEN}{msg_str}{RESET}"
        elif record.levelno >= logging.ERROR: # Color any ERROR level message red if not already colored
            colored_output = f"{RED}{msg_str}{RESET}"
        elif record.levelno >= logging.WARNING: # Color any WARNING level message yellow
            colored_output = f"{YELLOW}{msg_str}{RESET}"
        # INFO messages that are not chat messages (handled by log_message direct print)
        # and not special TwitchIO messages will be printed as is (or as formatted by console_formatter).
        # If default green is desired for other INFO logs, add:
        # elif record.levelno >= logging.INFO:
        #     colored_output = f"{GREEN}{msg_str}{RESET}"

        if not colored_output.endswith(RESET) and '\x1b[' in colored_output:
            colored_output += RESET
            
        print(colored_output)
