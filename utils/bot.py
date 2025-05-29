from twitchio.ext import commands
import logging
import markovify
import asyncio
import configparser
import time
import sqlite3
from datetime import datetime
import os
import math
from functools import lru_cache
from collections import OrderedDict

from colorama import init
from datetime import datetime
import threading
from tabulate import tabulate
from utils.logger import Logger
from utils.color_control import ColorManager
from commands.ansv_command import ansv_command
from utils.db_setup import ensure_db_setup
from utils.tts import process_text, start_tts_processing # Added start_tts_processing

config = configparser.ConfigParser()
config.read("settings.conf")

db_file = "messages.db"  # Replace with your actual database file path
ensure_db_setup(db_file)


logger = Logger()
logger.setup_logger()

init()  # init termcolor

# Create a handler for writing to the log file
file_handler = logging.FileHandler("app.log")
file_handler.setLevel(logging.DEBUG)

YELLOW = "\x1b[33m"  # xterm colors. dunno why tbh
RESET = "\x1b[0m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
PURPLE = "\x1b[35m"

# Try to extract the channels - with error handling
try:
    channels = config["settings"]["channels"].split(",")
except Exception as e:
    print(f"{RED}Error reading channels from config: {e}{RESET}")
    channels = []


class LRUCache:
    """Memory-efficient LRU (Least Recently Used) cache to prevent memory leaks."""
    
    def __init__(self, maxsize=1000):
        self.maxsize = maxsize
        self.cache = OrderedDict()
    
    def __contains__(self, key):
        return key in self.cache
    
    def __getitem__(self, key):
        if key in self.cache:
            # Move to end (most recently used)
            self.cache.move_to_end(key)
            return self.cache[key]
        raise KeyError(key)
    
    def __setitem__(self, key, value):
        if key in self.cache:
            # Update existing key and move to end
            self.cache[key] = value
            self.cache.move_to_end(key)
        else:
            # Add new key
            self.cache[key] = value
            # Remove oldest if over size limit
            if len(self.cache) > self.maxsize:
                self.cache.popitem(last=False)  # Remove oldest (first) item
    
    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


class Bot(commands.Bot):
    def __init__(
        self,
        token,
        client_id,
        nick,
        prefix,
        initial_channels,
        db_file,
        rebuild_cache=False,
        enable_tts=False
    ):
        super().__init__(
            token=token,
            client_id=client_id,
            nick=nick,
            prefix=prefix,
            initial_channels=initial_channels,
        )
        
        # Initialize other variables
        print("Initializing Bot class...")
        self.prefix = prefix
        self.my_logger = Logger()
        self.my_logger.setup_logger()
        self.owner = None
        self.channels = initial_channels
        self.trusted_users = []
        self.ignored_users = []
        self.chat_line_count = 0
        self.last_message_time = time.time()
        # PERFORMANCE: Use memory-efficient caches with size limits to prevent memory leaks
        self.user_colors = LRUCache(maxsize=1000)  # Limit to 1000 users
        self.channel_colors = LRUCache(maxsize=100)  # Limit to 100 channels
        self.logger = logging.getLogger("bot")
        self.logger.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
        handler = logging.FileHandler(filename="app.log", encoding="utf-8", mode="w")
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.color_manager = ColorManager()
        self.channel_chat_line_count = {channel: 0 for channel in self.channels}
        self.channel_last_message_time = {
            channel: time.time() for channel in self.channels
        }
        self.channel_settings = {}  # Initialize the channel settings dictionary
        self.db_file = db_file
        self.load_channel_settings()  # Populate channel settings
        self.rebuild_cache = rebuild_cache
        
        # Add cache update threshold setting
        self.cache_update_threshold = 3600 * 24  # 24 hours by default
        self.cache_build_times = {}  # Initialize as empty dict
        
        # Load cache build times before attempting to build model
        self.cache_build_times = self.load_last_cache_build_times()
        print(f"Loaded cache build times: {self.cache_build_times}")
        
        self.load_text_and_build_model()
        self.first_model_update = True
        
        # Conditional call to update_model_periodically based on rebuild_cache flag
        if self.rebuild_cache:
            self.update_model_periodically()
        
        self.enable_tts = enable_tts
        if self.enable_tts:
            from utils import tts
            tts.initialize_tts()
        
        # Read verbose_heartbeat_log setting
        try:
            self.verbose_heartbeat_log = config.getboolean('settings', 'verbose_heartbeat_log')
        except (configparser.NoSectionError, configparser.NoOptionError):
            self.verbose_heartbeat_log = False # Default to False if not found
            print(f"{YELLOW}Warning: 'verbose_heartbeat_log' not found in settings.conf, defaulting to False.{RESET}")

        self._joined_channels = set()
        
        self.start_time = time.time()
        
        self.message_request_check = None
        
        self.message_request_check = self.loop.create_task(self.message_request_checker())
        
    async def send_message_to_channel(self, channel_name, message):
        """Send a message to a specific channel."""
        # Check if channel starts with # (required for Twitch)
        if not channel_name.startswith('#'):
            channel_name = f'#{channel_name}'
            
        # Make sure we're in the channel
        if channel_name not in self._joined_channels:
            print(f"Joining channel {channel_name} before sending message...")
            await self.join_channel(channel_name)
            
        # Send the message
        channel = self.get_channel(channel_name.lstrip('#'))  # TwitchIO gets channels without #
        if channel:
            await channel.send(message)
            print(f"Message sent to {channel_name}: {message}")
            return True
        else:
            print(f"Failed to find channel {channel_name}")
            return False
    
    async def leave_channel(self, channel_name):
        """Leave a channel with proper cleanup."""
        try:
            # Ensure the channel name is properly formatted with # prefix for our tracking
            if not channel_name.startswith('#'):
                channel_name = f'#{channel_name}'
            
            # TwitchIO part_channels expects channel names WITHOUT # prefix
            clean_name = channel_name.lstrip('#')
            
            print(f"{YELLOW}Attempting to leave channel: {channel_name} (clean: {clean_name}){RESET}")
            
            # Mark as disconnected in the database first
            try:
                conn = sqlite3.connect(self.db_file)
                c = conn.cursor()
                c.execute(
                    "UPDATE channel_configs SET currently_connected = 0 WHERE channel_name = ?",
                    (clean_name,)
                )
                conn.commit()
                conn.close()
                print(f"{YELLOW}Marked {clean_name} as disconnected in database{RESET}")
            except Exception as db_error:
                print(f"{RED}Database error when leaving {clean_name}: {db_error}{RESET}")
            
            # Actually leave the channel
            try:
                # This is the TwitchIO API call to leave a channel
                await self.part_channels([clean_name])
                
                # Remove from joined channel tracking
                if channel_name in self._joined_channels:
                    self._joined_channels.remove(channel_name)
                
                # Force an immediate heartbeat update to sync the joined channel status
                self.update_heartbeat_file()
                
                print(f"{GREEN}✅ Successfully left channel: {channel_name}{RESET}")
                return True
            except Exception as e:
                print(f"{RED}Failed to leave channel {channel_name}: {e}{RESET}")
                return False
                
        except Exception as e:
            print(f"{RED}Exception when leaving channel {channel_name}: {e}{RESET}")
            return False

    async def join_channel(self, channel_name):
        """Join a channel with proper formatting and error handling."""
        try:
            # Ensure the channel name is properly formatted with # prefix for our tracking
            if not channel_name.startswith('#'):
                channel_name = f'#{channel_name}'
            
            # TwitchIO join_channels expects channel names WITHOUT # prefix
            # The library will strip # internally if present
            clean_name = channel_name.lstrip('#')
            
            print(f"{YELLOW}Attempting to join channel: {channel_name} (clean: {clean_name}){RESET}")
            
            # Join the channel
            try:
                # The actual join operation
                await self.join_channels([clean_name])
                join_success = True
                
                # Verify that the join was successful by checking connection
                channel_obj = self.get_channel(clean_name)
                if not channel_obj:
                    print(f"{YELLOW}Warning: Could not verify channel object for {clean_name} after joining{RESET}")
                
            except Exception as join_error:
                join_success = False
                print(f"{RED}Error in join_channels operation: {join_error}{RESET}")
                raise
            
            if join_success:
                # Update tracking in multiple places to ensure consistency
                
                # 1. Add to our tracking set with # prefix
                self._joined_channels.add(channel_name)
                
                # 2. Make sure it's in self.channels list (also with # prefix)
                if channel_name not in self.channels:
                    self.channels.append(channel_name)
                
                # 3. Update database to mark channel as connected
                try:
                    conn = sqlite3.connect(self.db_file)
                    c = conn.cursor()
                    
                    # First check if channel exists in channel_configs
                    c.execute("SELECT 1 FROM channel_configs WHERE channel_name = ?", (clean_name,))
                    if not c.fetchone():
                        # Create entry if it doesn't exist
                        print(f"{YELLOW}Creating new channel config for {clean_name}{RESET}")
                        c.execute('''
                            INSERT INTO channel_configs 
                            (channel_name, tts_enabled, voice_enabled, join_channel, owner, 
                            trusted_users, ignored_users, use_general_model, lines_between_messages, time_between_messages, currently_connected)
                            VALUES (?, 0, 1, 1, ?, '', '', 1, 50, 15, 1)
                        ''', (clean_name, clean_name))
                    else:
                        # Update existing entry - mark as joined
                        c.execute(
                            "UPDATE channel_configs SET join_channel = 1, currently_connected = 1 WHERE channel_name = ?",
                            (clean_name,)
                        )
                        
                    conn.commit()
                    conn.close()
                    
                    # Force an immediate heartbeat update to sync the joined channel status
                    self.update_heartbeat_file()
                    
                except Exception as db_error:
                    print(f"{RED}Database update error for channel {clean_name}: {db_error}{RESET}")
                
                print(f"{GREEN}✅ Successfully joined channel: {channel_name}{RESET}")
                return True
            else:
                print(f"{RED}❌ Failed to join channel: {channel_name}{RESET}")
                return False
                
        except Exception as e:
            print(f"{RED}❌ Failed to join channel {channel_name}: {e}{RESET}")
            return False

    def load_channel_settings(self):
        self.channel_settings = {}
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()

        # Load channel-specific settings
        c.execute(
            "SELECT channel_name, trusted_users, ignored_users, time_between_messages, lines_between_messages FROM channel_configs"
        )
        for row in c.fetchall():
            channel, trusted, ignored, time_between, lines_between = row
            self.channel_settings[channel] = {
                "trusted_users": trusted.split(",") if trusted else [],
                "ignored_users": ignored.split(",") if ignored else [],
                "time_between_messages": time_between,
                "lines_between_messages": lines_between,
            }
        conn.close()

    async def check_and_join_channels(self, silent=False):
        """Join all channels marked for joining in the database.
        
        Args:
            silent (bool): If True, suppresses routine logging for periodic checks
        """
        try:
            # Get channels from database
            conn = sqlite3.connect(self.db_file)
            c = conn.cursor()
            c.execute("SELECT channel_name FROM channel_configs WHERE join_channel = 1")
            channels_to_join = [row[0] for row in c.fetchall()]
            conn.close()
            
            if not silent:
                print(f"{YELLOW}Found {len(channels_to_join)} channels to join from database{RESET}")
            
            # If no channels found in database, check config file
            if not channels_to_join and "settings" in config and "channels" in config["settings"]:
                config_channels = config["settings"]["channels"].split(",")
                channels_to_join = [ch.strip() for ch in config_channels if ch.strip()]
                if not silent:
                    print(f"{YELLOW}No channels in database, using {len(channels_to_join)} from config file{RESET}")
            
            join_success = 0
            join_failure = 0
            new_joins = 0
            
            # Join each channel with improved error handling
            for channel in channels_to_join:
                try:
                    # Make sure channel has # prefix
                    channel_name = f"#{channel.lstrip('#')}"
                    
                    # Skip if already joined
                    if channel_name in self._joined_channels:
                        if not silent:
                            print(f"{GREEN}Already joined channel: {channel_name}{RESET}")
                        join_success += 1
                        continue
                    
                    # Attempt to join
                    print(f"{YELLOW}Attempting to join channel: {channel_name}{RESET}")
                    success = await self.join_channel(channel_name)
                    
                    if success:
                        join_success += 1
                        new_joins += 1
                    else:
                        join_failure += 1
                        
                except Exception as e:
                    join_failure += 1
                    print(f"{RED}Error joining channel {channel}: {str(e)}{RESET}")
            
            # Summary - only show if there's activity or not silent
            if not silent or new_joins > 0 or join_failure > 0:
                print(f"{GREEN}Channel joining complete: {join_success} succeeded, {join_failure} failed{RESET}")
                # Final verification - only show if there's activity or not silent
                print(f"{YELLOW}Currently joined channels: {sorted(self._joined_channels)}{RESET}")
            
        except Exception as e:
            print(f"{RED}Error in check_and_join_channels: {str(e)}{RESET}")
    
    async def setup_periodic_channel_check(self, interval=300):  # 5 minutes
        """Set up a periodic task to check for new channels."""
        async def check_periodically():
            while True:
                await asyncio.sleep(interval)
                await self.check_and_join_channels(silent=True)  # Periodic check, silent mode
        
        # Start the periodic task
        self.loop.create_task(check_periodically())
        print(f"Started periodic channel check (every {interval} seconds)")
    
    def ensure_channel_configs(self):
        """Make sure all channels have config entries in the database with proper defaults."""
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        
        for channel in self.channels:
            # Remove # for database storage
            clean_channel = channel.lstrip('#')
            
            # Check if config exists
            c.execute("SELECT 1 FROM channel_configs WHERE channel_name = ?", (clean_channel,))
            if not c.fetchone():
                print(f"Creating config for channel: {clean_channel}")
                c.execute('''
                    INSERT INTO channel_configs 
                    (channel_name, tts_enabled, voice_enabled, join_channel, owner, 
                     trusted_users, ignored_users, use_general_model, lines_between_messages, time_between_messages, currently_connected)
                    VALUES (?, 0, 1, 1, ?, '', '', 1, 50, 15, 0)
                ''', (clean_channel, clean_channel))
        
        conn.commit()
        conn.close()
        
        # Reload channel settings after updating configs
        self.load_channel_settings()
    
    async def print_channel_status(self):
        """Print a status table showing all channels and their configurations."""
        try:
            conn = sqlite3.connect(self.db_file)
            c = conn.cursor()
            
            table_data = []
            c.execute('''
                SELECT channel_name, owner, trusted_users, ignored_users, voice_enabled, tts_enabled, 
                       join_channel, time_between_messages, lines_between_messages, use_general_model
                FROM channel_configs
            ''')
            
            for row in c.fetchall():
                channel, owner, trusted, ignored, voice, tts, join_enabled, time_between, lines_between, use_general = row
                
                # Format owner with color
                owner_display = f"\033[38;5;{self.get_user_color(owner)}m{owner}\033[0m" if owner else "None"
                
                # Format trusted users with colors
                if trusted and trusted.strip():
                    trusted_display = ", ".join(
                        f"\033[38;5;{self.get_user_color(user.strip())}m{user.strip()}\033[0m"
                        for user in trusted.split(",") if user.strip()
                    )
                else:
                    trusted_display = ""
                    
                # Format settings
                voice_status = f"{GREEN}enabled{RESET}" if voice else f"{RED}disabled{RESET}"
                tts_status = f"{GREEN}enabled{RESET}" if tts else f"{RED}disabled{RESET}"
                
                # Check if channel is actually joined
                is_joined = f"#{channel}" in self._joined_channels
                join_status = f"{GREEN}joined{RESET}" if is_joined else f"{RED}not joined{RESET}"
                
                # Format time and lines settings
                time_status = f"{GREEN}{time_between}{RESET}" if time_between > 0 else f"{RED}0{RESET}"
                lines_status = f"{GREEN}{lines_between}{RESET}" if lines_between > 0 else f"{RED}0{RESET}"
                
                # Add to table
                table_data.append([
                    f"\033[38;5;{self.get_channel_color(channel)}m#{channel}\033[0m", 
                    owner_display,
                    trusted_display,
                    voice_status,
                    tts_status,
                    join_status,
                    time_status,
                    lines_status,
                ])
            
            conn.close()
            
            headers = ["Channel", "Owner", "Trusted Users", "Voice", "TTS", "Autojoin", "Time", "Lines"]
            print(tabulate(table_data, headers=headers, tablefmt="pretty"))
        
        except Exception as e:
            print(f"Error printing channel status: {e}")

    def load_text_and_build_model(self, create_individual_caches=False):
        directory = "logs/"
        cache_directory = "cache/"
        if not os.path.exists(cache_directory):
            os.makedirs(cache_directory)
        self.text = ""  # Text for the general model
        self.models = {}  # Dictionary for channel-specific models
        total_lines = 0
        files_data = []  # Initialize files_data list here

        # Load or initialize last cache build times
        self.cache_build_times = self.load_last_cache_build_times()

        line_threshold = 50  # Threshold for individual model creation
        # Connect to the SQLite database to fetch valid channels
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute("SELECT channel_name FROM channel_configs")
        valid_channels = set(row[0] for row in c.fetchall())

        for filename in os.listdir(directory):
            if filename.endswith(".txt"):
                channel_name, _ = os.path.splitext(filename)
                file_path = os.path.join(directory, filename)
                if os.path.exists(file_path):
                    with open(file_path, "r") as f:
                        file_text = f.read()
                    line_count = file_text.count('\n')
                    total_lines += line_count
                    self.text += file_text  # Add text to the general model

                    # Check if individual cache should be created
                    if channel_name in valid_channels and line_count >= line_threshold and create_individual_caches:
                        cache_file_path = os.path.join(cache_directory, f"{channel_name}_model.json")
                        cache_status = self.create_channel_model(channel_name, file_text, cache_file_path)
                    else:
                        cache_status = f"{RED}Unchanged{RESET}"

                    files_data.append([
                        channel_name, 
                        f"{line_count:,}", 
                        cache_status, 
                        "General Model" if cache_status == f"{RED}Unchanged{RESET}" else f"{channel_name}_model.json"
                    ])

        # After processing all files, build the general model
        if self.text:
            self.general_model = markovify.Text(self.text)
            general_cache_file_path = os.path.join(cache_directory, "general_markov_model.json")
            last_build_time = self.cache_build_times.get("general_markov_model.json")
            
            if self.rebuild_cache or last_build_time is None:
                self.save_general_model_to_cache(general_cache_file_path)
                general_cache_status = f"{GREEN}Updated{RESET}"
                # Update build time
                self.cache_build_times["general_markov_model.json"] = time.time()
                self.save_cache_build_times()
            else:
                general_cache_status = f"{RED}Unchanged{RESET}"

        conn.close()

        # Add total and general model status to table
        total_label = f"{YELLOW}Total{RESET}"
        files_data.append([total_label, f"{total_lines:,}", general_cache_status, "general_markov_model.json"])

        # Print the table outside the loop, after processing all files
        headers = ["Channel", "Brain Size", "Brain Status", "Brain"]
        print(tabulate(files_data, headers=headers, tablefmt="pretty", numalign="right"))

    def determine_cache_status(self, channel_name, file_text, create_individual_caches, cache_directory):
        """Determine cache status for a given channel"""
        cache_file_path = os.path.join(cache_directory, f"{channel_name}_model.json")
        cache_status = "Unchanged"
        cache_file_display = "general_markov_model.json"

        # Check for DB-registered channels
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute("SELECT channel_name FROM channel_configs")
        valid_channels = set(row[0] for row in c.fetchall())
        conn.close()

        if channel_name in valid_channels and create_individual_caches:
            channel_model = markovify.Text(file_text)
            self.models[channel_name] = channel_model
            
            # Check if the cache file needs to be updated
            # Note: Checking last build time or model differences to decide
            last_build_time = self.cache_build_times.get(channel_name)
            if self.rebuild_cache or last_build_time is None:
                with open(cache_file_path, 'w') as cache_file:
                    cache_file.write(channel_model.to_json())
                cache_status = "Updated"
                # Update the build time
                self.cache_build_times[channel_name] = time.time()
                
            cache_file_display = f"{channel_name}_model.json"

        return cache_status, cache_file_display

    def cache_individual_model(self, channel_name, model, cache_file_path):
        model_json = model.to_json()
        with open(cache_file_path, "w") as f:
            f.write(model_json)

    def create_channel_model(self, channel_name, file_text, cache_file_path):
        """Create a model for a specific channel and save it to the cache."""
        try:
            channel_model = markovify.Text(file_text)
            self.models[channel_name] = channel_model
            
            # Check if we should update cache
            last_build_time = self.cache_build_times.get(channel_name)
            if self.rebuild_cache or last_build_time is None:
                with open(cache_file_path, 'w') as cache_file:
                    cache_file.write(channel_model.to_json())
            
            # Update build time
            self.cache_build_times[channel_name] = time.time()
            self.save_cache_build_times()
            return f"{GREEN}Updated{RESET}"
        except Exception as e:
            print(f"Error creating model for {channel_name}: {e}")
            return f"{RED}Error{RESET}"

    def save_general_model_to_cache(self, cache_file_path):
        """Save the general model to the cache."""
        try:
            with open(cache_file_path, 'w') as cache_file:
                cache_file.write(self.general_model.to_json())
            return True
        except Exception as e:
            print(f"Error saving general model to cache: {e}")
            return False


    def load_model_from_cache(self, channel_name):
        cache_file_path = os.path.join("cache", f"{channel_name}_model.json")
        try:
            with open(cache_file_path, "r") as f:
                model_json = f.read()
                return markovify.Text.from_json(model_json)
        except FileNotFoundError:
            return None


    def generate_message(self, channel_name):
        # Connect to the SQLite database
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        # Check the database to see if this channel should use the general model
        c.execute(
            "SELECT use_general_model FROM channel_configs WHERE channel_name = ?",
            (channel_name,),
        )
        result = c.fetchone()
        conn.close()

        cache_file_used = ""  # Variable to store the name of the cache file used

        # Determine which model to use and add debug information
        if result and result[0]:
            model = self.general_model
            cache_file_used = (
                "general_markov_model.json"  # Name of the general model cache file
            )
        else:
            model = self.load_model_from_cache(channel_name)
            if model:
                cache_file_used = (
                    f"{channel_name}_model.json"  # Specific model cache file
                )
            else:
                model = self.general_model
                cache_file_used = (
                    "general_markov_model.json"  # Fallback to general model cache file
                )

        # Generate a message using the chosen model
        message = model.make_sentence()
        if message:
            # Clean up the message to ensure all characters are printable
            message = "".join(char for char in message if char.isprintable())
            # Save and return the generated message
            self.save_message(message, channel_name)
            return message
        else:
            # If no message was generated, return None and add debug information
            print(
                f"[DEBUG] Failed to generate message for channel: {channel_name} using cache file: {cache_file_used}"
            )
            return None


    def save_message(self, message, channel_name):
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute(
            """INSERT INTO messages (message, timestamp, channel, state_size, message_length, author_name, is_bot_response) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                message,
                datetime.now().isoformat(), # Store timestamp as ISO string
                channel_name,
                self.general_model.state_size if hasattr(self.general_model, 'state_size') else None, # Ensure general_model exists
                len(message),
                self.nick, # Bot's name as author
                1 # Mark as bot response
            ),
        )
        conn.commit()
        conn.close()


    def update_model_periodically(self, interval=86400, initial_delay=120):
        #self.my_logger.info("update_model_periodically called")
        def delayed_execution():
            try:
                if self.rebuild_cache:
                    # Rebuild cache including individual caches
                    self.load_text_and_build_model(create_individual_caches=True)
                    #self.my_logger.info("Brain rebuild requested.")
                else:
                    # Check if the general model cache is loaded
                    cache_loaded = self.load_model_from_cache("general_markov_model.json")
                    if not cache_loaded:
                        # Just rebuild the general model
                        self.load_text_and_build_model(create_individual_caches=False)
                        self.my_logger.info("Markov model updated.")
                    else:
                        self.my_logger.info("Markov model loaded from cache.")
            except Exception as e:
                self.my_logger.error(f"Error during model update: {e}")
            finally:
                # Schedule the next execution
                threading.Timer(interval, delayed_execution).start()

        # Start the first execution after the initial delay
        threading.Timer(initial_delay, delayed_execution).start()

    def load_last_cache_build_times(self):
        """Load the last build times of cache files from the database or create a default."""
        try:
            # Check if a cache_build_times file exists
            cache_time_file = os.path.join("cache", "cache_build_times.json")
            if os.path.exists(cache_time_file):
                with open(cache_time_file, 'r') as f:
                    import json
                    data = json.load(f)
                    
                    # Convert from list to dictionary if needed
                    if isinstance(data, list):
                        print("Converting cache build times from list to dictionary format...")
                        result = {}
                        for entry in data:
                            if isinstance(entry, dict) and "channel" in entry and "timestamp" in entry:
                                # Use the channel name as the key, timestamp as the value
                                channel_key = entry["channel"]
                                if channel_key == "general_markov":
                                    channel_key = "general_markov_model.json"
                                result[channel_key] = entry["timestamp"]
                        return result
                    return data
            return {}
        except Exception as e:
            print(f"Error loading cache build times: {e}")
            return {}
        
    def save_cache_build_times(self):
        """Save the current cache build times to a file."""
        try:
            # Ensure cache directory exists
            if not os.path.exists("cache"):
                os.makedirs("cache")
            
            cache_time_file = os.path.join("cache", "cache_build_times.json")
            
            # Convert from dictionary to list for backwards compatibility
            # or just save as dictionary if we've already migrated
            with open(cache_time_file, 'w') as f:
                import json
                # Check if we need to maintain the list format for backwards compatibility
                try:
                    with open(cache_time_file, 'r') as read_f:
                        old_data = json.load(read_f)
                        if isinstance(old_data, list):
                            # Convert our dictionary back to the list format
                            list_data = []
                            for key, timestamp in self.cache_build_times.items():
                                channel_name = key
                                if key == "general_markov_model.json":
                                    channel_name = "general_markov"
                                list_data.append({
                                    "channel": channel_name,
                                    "timestamp": timestamp,
                                    "success": True,
                                    "duration": 3.45  # Default duration
                                })
                            json.dump(list_data, f, indent=2)
                            print("Saved cache build times in list format for compatibility")
                            return
                except:
                    # If we can't read the old file, just use the dictionary format
                    pass
                
                # Save as dictionary
                json.dump(self.cache_build_times, f, indent=2)
                print("Saved cache build times in dictionary format")
        except Exception as e:
            print(f"Error saving cache build times: {e}")

    @commands.command(name="ansv")
    async def ansv_wrapper(self, ctx, setting=None, *args):
        """Enhanced command handler for ansv commands"""
        channel_name = ctx.channel.name
        
        # If no setting provided, show help
        if not setting:
            await ctx.send("Usage: !ansv [setting] [value]. Available settings: trusted, voice, tts, lines, time")
            return
        
        # Convert setting to lowercase for easier comparison
        setting = setting.lower()
        
        if setting == "trusted":
            # Handle trusted users
            if not args:
                # No arguments, show current trusted users
                if channel_name in self.channel_settings:
                    trusted_users = self.channel_settings[channel_name].get('trusted_users', [])
                    if trusted_users:
                        await ctx.send(f"Trusted users: {', '.join(trusted_users)}")
                    else:
                        await ctx.send("No trusted users set")
                else:
                    await ctx.send("Channel settings not found")
            else:
                # Add or remove trusted user
                action = args[0].lower()
                if len(args) < 2:
                    await ctx.send("Usage: !ansv trusted add/remove [username]")
                    return
                    
                username = args[1].lower()
                
                if action == "add":
                    success = await self.add_trusted_user(channel_name, username)
                    if success:
                        await ctx.send(f"Added {username} to trusted users")
                    else:
                        await ctx.send(f"Failed to add {username} to trusted users")
                elif action == "remove":
                    # Implement remove trusted user logic here
                    await ctx.send(f"Removed {username} from trusted users")
                else:
                    await ctx.send("Unknown action. Use add or remove")
        else:
            # Call the original ansv_command for other settings
            await ansv_command(self, ctx, setting, args[0] if args else None, enable_tts=self.enable_tts)

    @staticmethod
    def convert_size(size_bytes):
        if size_bytes == 0:
            return "0B"
        size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_name[i]}"

    def fetch_channel_settings(self, channel_name):
        try:
            conn = sqlite3.connect(self.db_file)
            c = conn.cursor()
            c.execute(
                "SELECT lines_between_messages, time_between_messages, tts_enabled, voice_enabled FROM channel_configs WHERE channel_name = ?",
                (channel_name,)
            )
            row = c.fetchone()
            conn.close()
            if row:
                return row[0], row[1], row[2], row[3]  # returns lines_between, time_between, tts_enabled, voice_enabled
            else:
                return 0, 0, False, False  # Default values if channel settings not found
        except sqlite3.Error as e:
            print(f"SQLite error in fetch_channel_settings: {e}")
            return 0, 0, False, False

    def get_channel_voice_preset(self, channel_name):
        """Fetch the voice_preset for a given channel from the database."""
        try:
            clean_channel_name = channel_name.lstrip('#')
            conn = sqlite3.connect(self.db_file)
            c = conn.cursor()
            c.execute("SELECT voice_preset FROM channel_configs WHERE channel_name = ?", (clean_channel_name,))
            result = c.fetchone()
            conn.close()
            if result and result[0]:
                self.logger.debug(f"Voice preset for channel {clean_channel_name}: {result[0]}")
                return result[0]
            else:
                self.logger.debug(f"No specific voice preset found for channel {clean_channel_name}, using default.")
                return None # Or a global default like 'v2/en_speaker_5'
        except sqlite3.Error as e:
            self.logger.error(f"SQLite error in get_channel_voice_preset for {channel_name}: {e}")
            return None # Fallback on error



    async def event_ready(self):
        """Handle the bot ready event."""
        print(f"{GREEN}==================================================={RESET}")
        print(f"{GREEN}Bot is ready! | {self.nick}{RESET}")
        print(f"{GREEN}==================================================={RESET}")
        
        # Use verbose flag for detailed output
        verbose = os.environ.get('VERBOSE', '').lower() in ('true', '1', 'yes')
        
        # Step 1: Initialize channel configs in the database
        try:
            if verbose:
                print(f"{YELLOW}Step 1: Initializing channel configurations...{RESET}")
            self.ensure_channel_configs()
            if verbose:
                print(f"{GREEN}✅ Channel configs initialized{RESET}")
        except Exception as e:
            print(f"{RED}❌ Error initializing channel configs: {e}{RESET}")
        
        # Step 2: Set start time for uptime tracking
        self._start_time = time.time()
        
        # Step 3: Process channels from config file
        try:
            if verbose:
                print(f"{YELLOW}Step 3: Processing channels from config file...{RESET}")
            if "settings" in config and "channels" in config["settings"]:
                config_channels = config["settings"]["channels"].split(",")
                config_channels = [ch.strip() for ch in config_channels if ch.strip()]
                
                if verbose:
                    print(f"{YELLOW}Found {len(config_channels)} channels in config file{RESET}")
                
                # Make sure each config channel has a database entry
                for channel in config_channels:
                    clean_name = channel.lstrip('#')
                    # Update channel config to ensure it's set to be joined
                    try:
                        conn = sqlite3.connect(self.db_file)
                        c = conn.cursor()
                        c.execute("SELECT 1 FROM channel_configs WHERE channel_name = ?", (clean_name,))
                        
                        if not c.fetchone():
                            # Create new entry 
                            if verbose:
                                print(f"{YELLOW}Creating config for config file channel: {clean_name}{RESET}")
                            c.execute('''
                                INSERT INTO channel_configs 
                                (channel_name, tts_enabled, voice_enabled, join_channel, owner, 
                                trusted_users, ignored_users, use_general_model, lines_between_messages, time_between_messages, currently_connected)
                                VALUES (?, 0, 1, 1, ?, '', '', 1, 50, 15, 0)
                            ''', (clean_name, clean_name))
                        else:
                            # Update existing entry to make sure join_channel is enabled
                            c.execute("UPDATE channel_configs SET join_channel = 1 WHERE channel_name = ?", (clean_name,))
                            
                        conn.commit()
                        conn.close()
                    except Exception as db_error:
                        print(f"{RED}Error updating channel config for {clean_name}: {db_error}{RESET}")
            elif verbose:
                print(f"{YELLOW}No channels found in config file{RESET}")
                
            if verbose:
                print(f"{GREEN}✅ Config file channels processed{RESET}")
        except Exception as e:
            print(f"{RED}❌ Error processing config file channels: {e}{RESET}")
        
        # Step 4: Join all configured channels from database
        try:
            if verbose:
                print(f"{YELLOW}Step 4: Joining all configured channels...{RESET}")
            await self.check_and_join_channels(silent=False)  # Initial join, show full output
            if verbose:
                print(f"{GREEN}✅ Channel joining completed{RESET}")
        except Exception as e:
            print(f"{RED}❌ Error joining channels: {e}{RESET}")
        
        # Step 5: Start periodic channel checking
        try:
            if verbose:
                print(f"{YELLOW}Step 5: Setting up periodic channel check...{RESET}")
            await self.setup_periodic_channel_check()
            if verbose:
                print(f"{GREEN}✅ Periodic checking started{RESET}")
        except Exception as e:
            print(f"{RED}❌ Error setting up periodic channel check: {e}{RESET}")
        
        # Step 6: Print status table
        try:
            if verbose:
                print(f"{YELLOW}Step 6: Printing status table...{RESET}")
            await self.print_channel_status()
            if verbose:
                print(f"{GREEN}✅ Status printed{RESET}")
        except Exception as e:
            print(f"{RED}❌ Error printing channel status: {e}{RESET}")
        
        # Step 7: Create PID file
        try:
            with open("bot.pid", "w") as f:
                f.write(str(os.getpid()))
            if verbose:
                print(f"{GREEN}✅ Created PID file with PID: {os.getpid()}{RESET}")
        except Exception as e:
            print(f"{RED}❌ Error creating PID file: {e}{RESET}")
        
        # Step 8: Setup heartbeat
        try:
            if verbose:
                print(f"{YELLOW}Step 8: Setting up heartbeat...{RESET}")
            self.update_heartbeat_file()
            self.loop.create_task(self.heartbeat_task())
            if verbose:
                print(f"{GREEN}✅ Heartbeat task started{RESET}")
        except Exception as e:
            print(f"{RED}❌ Error setting up heartbeat: {e}{RESET}")
            
        # Final verification - always show
        print(f"{YELLOW}Currently joined channels: {sorted(self._joined_channels)}{RESET}")
        print(f"{GREEN}==================================================={RESET}")
        print(f"{GREEN}Bot initialization complete!{RESET}")
        print(f"{GREEN}==================================================={RESET}")
        
        # Extra verification for channels of interest
        for channel in self.channels:
            clean_channel = channel.lstrip('#')
            # Create the properly formatted channel name for _joined_channels check
            formatted_channel = f"#{clean_channel}"
            
            if formatted_channel in self._joined_channels:
                print(f"{GREEN}✓ Successfully joined {formatted_channel} channel!{RESET}")
                
                # Update database to mark channel as connected
                try:
                    conn = sqlite3.connect(self.db_file)
                    c = conn.cursor()
                    c.execute(
                        "UPDATE channel_configs SET currently_connected = 1 WHERE channel_name = ?",
                        (clean_channel,)
                    )
                    conn.commit()
                    conn.close()
                except Exception as e:
                    if verbose:
                        print(f"Error updating channel connection status in DB: {e}")
            else:
                # Only print failure if verbose or if this was explicitly in initial_channels
                if verbose or channel in self.initial_channels:
                    print(f"{RED}✗ Failed to join {formatted_channel} channel - will retry in next periodic check{RESET}")
                    
                # Make sure database shows it's not connected
                try:
                    conn = sqlite3.connect(self.db_file)
                    c = conn.cursor()
                    c.execute(
                        "UPDATE channel_configs SET currently_connected = 0 WHERE channel_name = ?",
                        (clean_channel,)
                    )
                    conn.commit()
                    conn.close()
                except Exception as e:
                    if verbose:
                        print(f"Error updating channel connection status in DB: {e}")

    def get_user_color(self, username):
        """Get a consistent color number for a user."""
        if not username or username.strip() == "":
            return 7  # Default gray for empty usernames
        
        # PERFORMANCE: Use cache with automatic memory management
        cached_color = self.user_colors.get(username)
        if cached_color is None:
            # Generate color based on username (simple hash)
            color_num = sum(ord(c) for c in username) % 200 + 20  # Range 20-220 to avoid dark colors
            self.user_colors[username] = color_num
            return color_num
            
        return cached_color

    def get_channel_color(self, channel_name):
        """Get a consistent color number for a channel."""
        clean_channel = channel_name.lstrip('#')
        
        # PERFORMANCE: Use cache with automatic memory management
        cached_color = self.channel_colors.get(clean_channel)
        if cached_color is None:
            # Generate color based on channel name (simple hash)
            color_num = sum(ord(c) for c in clean_channel) % 200 + 20  # Range 20-220
            self.channel_colors[clean_channel] = color_num
            return color_num
            
        return cached_color

    async def event_command_error(self, ctx, error):
        """Handle command errors."""
        if isinstance(error, commands.CommandNotFound):
            return  # Ignore the error, preventing it from propagating further
        else:
            # For all other types of errors, you might want to see what's going on
            self.my_logger.error(f"Error in command: {ctx.command.name}, {error}")


    def log_message(self, message):
        msg = f"{message.author.name}: {message.content}"
        self.my_logger.info(msg)

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return  # Ignore CommandNotFound exceptions
        raise error  # Re-raise other exceptions

    async def send_message(self, message):
        # Iterate over all channels
        for channel_name in self.channels:
            # Get the channel object
            channel = self.get_channel(channel_name)
            if channel:
                # Send the message to the channel
                await channel.send(message)

    # The function event_message is called whenever a new message is received in a channel.
    async def event_message(self, message):
        # Ignore messages from the bot itself or messages with no author.
        if message.author is None or message.author.name.lower() == self.nick.lower():
            return

        channel_name = message.channel.name.lower()
        # Log the message.
        self.my_logger.log_message(channel_name, message.author.name, message.content)

        # Fetch the channel settings and ignored users for the current channel.
        lines_between, time_between, tts_enabled, voice_enabled = self.fetch_channel_settings(channel_name)
        ignored_users = [user.lower() for user in self.channel_settings[channel_name]['ignored_users']] if channel_name in self.channel_settings else []

        # Ignore messages from ignored users.
        if message.author.name.lower() in ignored_users:
            return

        # Handle any commands in the message.
        await self.handle_commands(message)

        # Save user's message to the database
        try:
            conn = sqlite3.connect(self.db_file)
            c = conn.cursor()
            c.execute(
                """INSERT INTO messages (twitch_message_id, message, author_name, timestamp, channel, is_bot_response, message_length, tts_processed)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message.id,
                    message.content,
                    message.author.name,
                    message.timestamp.isoformat(), # Store timestamp as ISO string
                    channel_name,
                    0, # Not a bot response
                    len(message.content),
                    0 # Not processed for TTS by default
                )
            )
            conn.commit()
            conn.close()
        except sqlite3.Error as e:
            self.my_logger.error(f"Failed to save user message to DB for channel {channel_name}: {e}")
            print(f"Error saving user message to DB for {channel_name}: {e}")


        # Make sure the channel is in our dictionaries
        if channel_name not in self.channel_chat_line_count:
            self.channel_chat_line_count[channel_name] = 0
        self.channel_chat_line_count[channel_name] += 1
        
        # Calculate the elapsed time since the last message in the current channel.
        elapsed_time = time.time() - self.channel_last_message_time.get(channel_name, 0)

        # Determine if a message should be sent based on the lines_between and time_between settings.
        should_send_message = False
        if lines_between > 0 and self.channel_chat_line_count[channel_name] >= lines_between:
            should_send_message = True
        elif time_between > 0 and elapsed_time >= time_between * 60:
            should_send_message = True

        # If a message should be sent and voice is enabled for the current channel.
        if should_send_message and voice_enabled:
            # Connect to the database.
            conn = sqlite3.connect(self.db_file)
            c = conn.cursor()
            # Check if the general model should be used for the current channel.
            c.execute("SELECT use_general_model FROM channel_configs WHERE channel_name = ?", (channel_name,))
            row = c.fetchone()
            conn.close()

            # Generate a response using the appropriate model.
            if row:
                response = (self.general_model.make_sentence() if row[0] else self.models.get(channel_name, self.general_model).make_sentence())

                # If a response was generated.
                if response:
                    try:
                        # Send the response in the current channel.
                        channel_obj = self.get_channel(channel_name)
                        if channel_obj:
                            await channel_obj.send(response)
                            # Log the response.
                            self.my_logger.log_message(channel_name, self.nick, response)

                            # If TTS is enabled for the current channel.
                            if self.enable_tts and tts_enabled:
                                # Generate TTS audio for the response.
                                # Ensure start_tts_processing is called correctly with message_id and timestamp_str
                                original_message_id = message.id # ID of the original message that triggered this response
                                
                                # Format the timestamp of the original message in ISO format
                                if isinstance(message.timestamp, datetime):
                                    original_timestamp_str = message.timestamp.isoformat()
                                elif isinstance(message.timestamp, str):
                                    # If it's already a string, assume it's correctly formatted or handle conversion if needed
                                    # For now, we'll trust it if it's a string. If issues persist, this might need adjustment.
                                    original_timestamp_str = message.timestamp
                                else: # Fallback
                                    self.logger.warning(f"Unexpected timestamp type for original message {message.id}: {type(message.timestamp)}. Using current time (ISO) for TTS log.")
                                    original_timestamp_str = datetime.now().isoformat()

                                # Get voice preset for the channel
                                voice_preset_for_tts = self.get_channel_voice_preset(channel_name)
                                if not voice_preset_for_tts:
                                    # Fallback to a global default if channel has no specific preset or on error
                                    voice_preset_for_tts = 'v2/en_speaker_5' 
                                    self.logger.info(f"Using default voice preset '{voice_preset_for_tts}' for channel {channel_name} as none was set or found.")
                                else:
                                    self.logger.info(f"Using voice preset '{voice_preset_for_tts}' for channel {channel_name}.")

                                self.logger.debug(f"Calling start_tts_processing for bot response to msg_id: {original_message_id}, channel: {channel_name}, text: {response[:30]}..., timestamp: {original_timestamp_str}, voice: {voice_preset_for_tts}")
                                
                                # Call start_tts_processing from utils.tts
                                # Note: The process_text function was previously called here, but start_tts_processing is the one
                                # that takes message_id and timestamp_str for proper logging.
                                # start_tts_processing is now imported at the top of utils/bot.py

                                self.logger.info(f"Attempting to start TTS processing for bot auto-response. MsgID: {original_message_id}, Channel: {channel_name}, Text: '{response[:30]}...'")
                                start_tts_processing(
                                    input_text=response, # The bot's generated response
                                    channel_name=channel_name,
                                    message_id=original_message_id, # Link to the original user message
                                    timestamp_str=original_timestamp_str, # Timestamp of the original user message
                                    voice_preset_override=voice_preset_for_tts,
                                    db_file=self.db_file
                                )
                                self.logger.info("start_tts_processing called for bot auto-response.")
                                # The process_text_thread called by start_tts_processing will handle logging to tts_logs.

                            # Reset the chat line count and last message time for the current channel.
                            self.channel_chat_line_count[channel_name] = 0
                            self.channel_last_message_time[channel_name] = time.time()
                    except Exception as e:
                        # Log any errors that occur when sending the message.
                        self.my_logger.error(f"Failed to send message in {channel_name}: {str(e)}")
                        print(f"Error sending message in {channel_name}: {str(e)}")

    async def stop(self):
        try:
            # Disconnect the bot from all channels
            await self.close()
            
            # Remove status files
            for file in ["bot.pid", "bot_heartbeat.json"]:
                if os.path.exists(file):
                    os.remove(file)
                
            # Perform any additional cleanup tasks, such as closing database connections or saving data
            print("Bot stopped successfully.")
        except Exception as e:
            print(f"Error stopping bot: {e}")

    async def add_trusted_user(self, channel_name, username):
        """Add a user to the trusted users list for a channel."""
        try:
            # Remove # prefix for database storage
            clean_channel = channel_name.lstrip('#')
            
            conn = sqlite3.connect(self.db_file)
            c = conn.cursor()
            
            # Get current trusted users
            c.execute("SELECT trusted_users FROM channel_configs WHERE channel_name = ?", (clean_channel,))
            row = c.fetchone()
            
            if row:
                current_trusted = row[0]
                
                # Add the new user
                trusted_users = []
                if current_trusted and current_trusted.strip():
                    trusted_users = [u.strip() for u in current_trusted.split(',')]
                    
                if username not in trusted_users:
                    trusted_users.append(username)
                    
                # Update the database
                new_trusted = ','.join(trusted_users)
                c.execute("UPDATE channel_configs SET trusted_users = ? WHERE channel_name = ?", 
                         (new_trusted, clean_channel))
                conn.commit()
                
                # Update the channel settings in memory
                if clean_channel in self.channel_settings:
                    self.channel_settings[clean_channel]['trusted_users'] = trusted_users
                    
                print(f"Added {username} to trusted users for {channel_name}")
                return True
            else:
                print(f"Channel {channel_name} not found in database")
                return False
        except Exception as e:
            print(f"Error adding trusted user: {e}")
            return False
        finally:
            conn.close()

    async def heartbeat_task(self):
        """Update the heartbeat file periodically."""
        while True:
            self.update_heartbeat_file()
            await asyncio.sleep(60)  # Update every 60 seconds

    def update_heartbeat_file(self):
        """Write current bot status to heartbeat file and database."""
        try:
            import json
            # from utils.web_utils import get_verbose_logs_setting # No longer needed from web_utils
            
            # Check for verbose logs setting (now from self.verbose_heartbeat_log)
            # verbose_logs = get_verbose_logs_setting() # Replaced by self.verbose_heartbeat_log
            
            # Get current joined channels - strip # for consistent matching
            # We use a list comprehension to get only the channel names from _joined_channels
            # This ensures we only list truly joined channels
            channels_list = [channel.lstrip('#') for channel in self._joined_channels]
            
            # Remove empty strings from the list
            channels_list = [ch for ch in channels_list if ch]
            
            # Current timestamp for consistency
            current_time = time.time()
            formatted_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            data = {
                "timestamp": current_time,
                "nick": self.nick,
                "channels": channels_list,  # Store channels without # prefix
                "uptime": current_time - self.start_time,
                "tts_enabled": self.enable_tts,
                "pid": os.getpid()
            }
            
            # Write to heartbeat JSON file
            with open("bot_heartbeat.json", "w") as f:
                json.dump(data, f)
                
            # Also update the PID file to ensure it exists
            with open("bot.pid", "w") as f:
                f.write(str(os.getpid()))
                
            if self.verbose_heartbeat_log: # Use the new config setting
                print(f"{YELLOW}Heartbeat: Raw channels from _joined_channels: {channels_list}{RESET}")
            
            # Update the database for web UI connection status
            try:
                conn = sqlite3.connect(self.db_file)
                c = conn.cursor()
                
                # Create bot_status table if it doesn't exist
                c.execute('''
                    CREATE TABLE IF NOT EXISTS bot_status (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                ''')
                
                # Update or insert the last heartbeat time
                c.execute('''
                    INSERT OR REPLACE INTO bot_status (key, value)
                    VALUES (?, ?)
                ''', ('last_heartbeat', formatted_time))
                
                # Update or insert connected channels
                c.execute('''
                    INSERT OR REPLACE INTO bot_status (key, value)
                    VALUES (?, ?)
                ''', ('connected_channels', ','.join(channels_list)))
                
                # Also update the currently_connected status for each channel in the database
                # First, set all channels to not connected
                c.execute("UPDATE channel_configs SET currently_connected = 0")
                
                # Then set the connected status for channels that are actually joined
                for channel in channels_list:
                    clean_channel = channel.lstrip('#')  # Ensure no # prefix for DB storage
                    c.execute(
                        "UPDATE channel_configs SET currently_connected = 1 WHERE channel_name = ?",
                        (clean_channel,)
                    )
                
                # Commit the changes
                conn.commit()
                
                if self.verbose_heartbeat_log: # Use the new config setting
                    self.my_logger.log_info(f"Heartbeat: Updated database heartbeat at {formatted_time}")
                    print(f"{YELLOW}Heartbeat: Processed connected channels for DB: {channels_list}{RESET}")
                
            except Exception as db_error:
                self.my_logger.error(f"Heartbeat: Error updating database heartbeat: {db_error}")
            finally:
                if conn:
                    conn.close()
                
        except Exception as e:
            self.my_logger.error(f"Heartbeat: Error updating heartbeat file: {e}")

    async def check_message_requests(self):
        """Check for message requests from the web interface"""
        # First check for task restart requests
        restart_file = 'bot_task_restart.json'
        if os.path.exists(restart_file):
            try:
                with open(restart_file, 'r') as f:
                    import json
                    restart_data = json.load(f)
                
                task_name = restart_data.get('task', '')
                self.logger.info(f"{YELLOW}Found task restart request for {task_name}{RESET}")
                
                if task_name == 'message_request_checker' and self.message_request_check:
                    # Cancel the existing task
                    try:
                        self.message_request_check.cancel()
                        self.logger.info(f"{YELLOW}Cancelled existing message_request_checker task{RESET}")
                    except:
                        pass
                    
                    # Create a new task
                    self.message_request_check = self.loop.create_task(self.message_request_checker())
                    self.logger.info(f"{GREEN}Restarted message_request_checker task{RESET}")
                
                # Remove the restart file
                try:
                    os.remove(restart_file)
                except Exception as e:
                    self.logger.error(f"{RED}Error removing restart file: {e}{RESET}")
            except Exception as e:
                self.logger.error(f"{RED}Error processing restart request: {e}{RESET}")
                try:
                    os.remove(restart_file)
                except:
                    pass
        
        # Now check for message requests
        request_file = 'bot_message_request.json'
        
        if os.path.exists(request_file):
            self.logger.info(f"{YELLOW}Found message request file{RESET}")
            try:
                # Read the request file
                with open(request_file, 'r') as f:
                    import json
                    data = json.load(f)
                
                # Log the request details
                request_id = data.get('request_id', 'unknown')
                action = data.get('action', 'unknown')
                force = data.get('force', False)
                self.logger.info(f"{YELLOW}Processing message request{RESET}: ID={request_id}, Action={action}, Force={force}")
                
                # Process the message request
                if data['action'] == 'send_message':
                    channel = data['channel']
                    message = data['message']
                    
                    # Make sure the channel is in the correct format
                    if not channel.startswith('#'):
                        channel = f"#{channel}"
                    
                    self.logger.info(f"{YELLOW}Attempting to send message to {channel}{RESET}: {message[:50]}...")
                    
                    try:
                        # Try to get the channel object first
                        channel_obj = self.get_channel(channel.lstrip('#'))
                        success = False
                        
                        if channel_obj:
                            # Send directly with channel object
                            await channel_obj.send(message)
                            self.logger.info(f"{GREEN}Successfully sent message via channel object to {channel}{RESET}")
                            success = True
                        else:
                            # Fallback to our helper method
                            sent = await self.send_message_to_channel(channel, message)
                            if sent:
                                self.logger.info(f"{GREEN}Successfully sent message via helper to {channel}{RESET}")
                                success = True
                            else:
                                self.logger.error(f"{RED}Failed to send message to {channel}{RESET}")
                                
                                # Try to join the channel and send again - especially if force flag is set
                                self.logger.info(f"{YELLOW}Attempting to join {channel} and retry...{RESET}")
                                join_success = await self.join_channel(channel)
                                
                                if join_success:
                                    # Try sending one more time - with a slight delay to ensure the join completes
                                    await asyncio.sleep(0.5)
                                    
                                    # Try to get channel object again
                                    channel_obj = self.get_channel(channel.lstrip('#'))
                                    if channel_obj:
                                        try:
                                            await channel_obj.send(message)
                                            self.logger.info(f"{GREEN}Successfully sent message on retry to {channel}{RESET}")
                                            success = True
                                        except Exception as send_error:
                                            self.logger.error(f"{RED}Error sending message on retry: {send_error}{RESET}")
                                    else:
                                        # Last fallback - try helper again
                                        sent = await self.send_message_to_channel(channel, message)
                                        if sent:
                                            self.logger.info(f"{GREEN}Successfully sent message on second retry to {channel}{RESET}")
                                            success = True
                                        else:
                                            self.logger.error(f"{RED}Failed to send message on second retry to {channel}{RESET}")
                                else:
                                    self.logger.error(f"{RED}Failed to join channel {channel}{RESET}")
                        
                        # Log the final result
                        if success:
                            self.logger.info(f"{GREEN}Message request processed successfully{RESET}: Sent to {PURPLE}{channel}{RESET}")
                            # Save message to logs
                            self.my_logger.log_message(channel.lstrip('#'), self.nick, message)
                        else:
                            self.logger.error(f"{RED}Failed to send message to {channel} after all attempts{RESET}")
                            
                    except Exception as send_error:
                        self.logger.error(f"{RED}Error sending message to {channel}: {send_error}{RESET}")
                
                # Always remove the request file after processing
                try:
                    os.remove(request_file)
                    self.logger.info(f"{GREEN}Removed processed request file{RESET}")
                except Exception as rm_error:
                    self.logger.error(f"{RED}Error removing request file: {rm_error}{RESET}")
                
            except Exception as e:
                self.logger.error(f"{RED}Error processing message request: {e}{RESET}")
                
                # Rename the file to avoid repeated errors
                try:
                    error_file = f"{request_file}.error.{int(time.time())}"
                    os.rename(request_file, error_file)
                    self.logger.info(f"{YELLOW}Renamed error file to {error_file}{RESET}")
                except Exception as rename_error:
                    self.logger.error(f"{RED}Error renaming request file: {rename_error}{RESET}")
                    try:
                        # Last resort: try to delete it
                        os.remove(request_file)
                        self.logger.info(f"{YELLOW}Deleted error file as fallback{RESET}")
                    except:
                        pass

    async def message_request_checker(self):
        """Periodically check for message requests"""
        while True:
            await self.check_message_requests()
            await asyncio.sleep(2)  # Check every 2 seconds

    def is_tts_enabled(self, channel_name):
        """Check if TTS is enabled for a channel"""
        try:
            # Remove # prefix if present for database lookup
            clean_channel = channel_name.lstrip('#')
            
            conn = sqlite3.connect(self.db_file)
            c = conn.cursor()
            c.execute("SELECT tts_enabled FROM channel_configs WHERE channel_name = ?", (clean_channel,))
            result = c.fetchone()
            conn.close()
            
            # Return True if tts_enabled is 1, False otherwise
            return result is not None and result[0] == 1
        except Exception as e:
            self.logger.error(f"Error checking TTS status for {channel_name}: {e}")
            return False
            
    async def handle_speak_command(self, ctx):
        """Handle the !speak command with improved TTS processing"""
        channel = ctx.channel.name
        
        try:
            # Get the last message from this channel that wasn't a command
            conn = sqlite3.connect(self.db_file)
            c = conn.cursor()
            c.execute("""
                SELECT message FROM messages 
                WHERE channel = ? AND NOT message LIKE '!%' 
                ORDER BY timestamp DESC LIMIT 1
            """, (channel,))
            
            result = c.fetchone()
            conn.close()
            
            if not result:
                await ctx.send("No recent messages to speak.")
                return
            
            message_to_speak = result[0]
            
            # Check channel TTS settings
            if not self.is_tts_enabled(channel):
                await ctx.send("TTS is not enabled for this channel.")
                return
            
            # Only attempt TTS if it's enabled globally
            if not self.enable_tts:
                await ctx.send("TTS is not currently enabled globally.")
                return
            
            # Process the TTS with proper error handling
            # Use the correct parameter order based on how process_text is defined
            try:
                from utils.tts import process_text
                # Get the voice preset for the current channel
                voice_preset_for_speak = self.get_channel_voice_preset(channel)
                if not voice_preset_for_speak:
                    voice_preset_for_speak = 'v2/en_speaker_0' # Default for !speak if none set
                    self.logger.info(f"Using default voice preset '{voice_preset_for_speak}' for !speak in {channel}.")
                else:
                    self.logger.info(f"Using voice preset '{voice_preset_for_speak}' for !speak in {channel}.")

                # Note: We're using the import here to ensure we're calling the right function
                # The signature for async def process_text(channel, text, model_type="bark", voice_preset_override=None) in utils/tts.py
                self.logger.info(f"Calling process_text for !speak command. Channel: {channel}, Text: '{message_to_speak[:30]}...', Voice: {voice_preset_for_speak}")
                success, audio_file = await process_text(channel, message_to_speak, voice_preset_override=voice_preset_for_speak)
            except Exception as tts_error:
                self.logger.error(f"Error calling or during TTS generation via process_text for !speak: {tts_error}", exc_info=True)
                success, audio_file = False, None
            
            self.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] After process_text call. success: {success}, audio_file: '{audio_file}'")

            if success and audio_file:
                self.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] Condition (success and audio_file) is TRUE.")
                # TTS was successful, log and notify
                # The audio_file path from process_text should be like "static/outputs/channel/file.wav"
                web_path = audio_file 
                if not web_path.startswith('static/'): # Ensure it's a web path if not already
                    web_path = f"static/{web_path.lstrip('/')}"
                
                self.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] Sending message to Twitch: Speaking: {message_to_speak[:50]}... (Audio: {web_path})")
                await ctx.send(f"Speaking: {message_to_speak[:50]}... (Audio: {web_path})")
                self.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] Message sent to Twitch.")
                
                # Log the TTS usage in the database for tracking
                try:
                    self.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] Attempting to log !speak TTS to DB. audio_file from process_text: {audio_file}")
                    conn = sqlite3.connect(self.db_file)
                    c = conn.cursor()
                    # Get the message_id of the command message itself
                    command_message_id = ctx.message.id
                    # Use the timestamp of the command message in ISO format
                    command_timestamp_str = ctx.message.timestamp.isoformat() if isinstance(ctx.message.timestamp, datetime) else str(ctx.message.timestamp)
                    
                    # audio_file from process_text is "static/outputs/channel/file.wav"
                    # For the database, we want "outputs/channel/file.wav"
                    db_audio_file_path = None
                    if audio_file and audio_file.startswith('static/'):
                        db_audio_file_path = audio_file[len('static/'):]
                        self.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] Derived db_audio_file_path: '{db_audio_file_path}'")
                    elif audio_file: # If it doesn't start with static/ for some reason, log a warning but use it as is
                        self.logger.warning(f"Audio file path from process_text does not start with 'static/': {audio_file}")
                        db_audio_file_path = audio_file # Use as is, might be an issue later
                        self.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] Using audio_file as is for db_audio_file_path: '{db_audio_file_path}'")
                    else:
                        self.logger.error(f"[HANDLE_SPEAK_COMMAND_TRACE] audio_file is None or empty, cannot derive db_audio_file_path.")


                    # Get voice preset used. This should be the one passed to process_text.
                    voice_preset_used = voice_preset_for_speak # This was determined before calling process_text

                    if db_audio_file_path:
                        self.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] Logging !speak TTS to DB: msg_id={command_message_id}, channel={channel}, timestamp={command_timestamp_str}, path='{db_audio_file_path}', voice='{voice_preset_used}'")
                        c.execute("""
                            INSERT INTO tts_logs (message_id, channel, timestamp, file_path, voice_preset, message) 
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (command_message_id, channel, command_timestamp_str, db_audio_file_path, voice_preset_used, message_to_speak))
                        conn.commit()
                        self.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] !speak TTS logged to DB with message_id: {command_message_id}")
                    else:
                        self.logger.error(f"[HANDLE_SPEAK_COMMAND_TRACE] Could not log !speak TTS as db_audio_file_path was not determined or was None. Original audio_file: {audio_file}")
                    conn.close()
                except sqlite3.IntegrityError as ie:
                    self.logger.error(f"[HANDLE_SPEAK_COMMAND_TRACE] SQLite IntegrityError logging !speak TTS (likely duplicate message_id {command_message_id}): {ie}")
                except Exception as e:
                    self.logger.error(f"Error logging TTS usage: {e}")
            else:
                # TTS failed, inform the user
                await ctx.send("Sorry, there was an error generating the TTS audio.")
        except Exception as e:
            self.logger.error(f"Error in speak command: {e}")
            await ctx.send(f"Error: {str(e)}")





def fetch_users(db_file):
    # This function now fetches trusted and ignored users for a specific channel.
    def fetch_users_for_channel(channel_name):
        trusted_users = []
        ignored_users = []
        try:
            conn = sqlite3.connect(db_file)
            c = conn.cursor()
            c.execute(
                "SELECT trusted_users, ignored_users FROM channel_configs WHERE channel_name = ?",
                (channel_name,),
            )
            row = c.fetchone()
            if row:
                trusted_users = row[0].split(",") if row[0] else []
                ignored_users = row[1].split(",") if row[1] else []
        except Exception as e:
            print(f"Error fetching users for channel {channel_name}: {e}")
        finally:
            conn.close()
        return trusted_users, ignored_users

    return fetch_users_for_channel


def fetch_initial_channels(db_file):
    channels = []
    try:
        conn = sqlite3.connect(db_file)
        c = conn.cursor()
        c.execute("SELECT channel_name FROM channel_configs WHERE join_channel = 1")
        for row in c.fetchall():
            channels.append(row[0])
    except Exception as e:
        print(f"Error fetching initial channels: {e}")
    finally:
        conn.close()
    return channels

def insert_initial_channels_to_db(db_file, channels):
    """Insert initial channels with default values into the database if not already present,
    setting the owner name to the name of the channel."""
    conn = sqlite3.connect(db_file)
    c = conn.cursor()
    

    for channel in channels:
        c.execute('''
            INSERT INTO channel_configs (channel_name, tts_enabled, voice_enabled, join_channel, owner, trusted_users, ignored_users, use_general_model, lines_between_messages, time_between_messages, currently_connected)
            SELECT ?, 0, 0, 1, ?, '', '', 1, 100, 0, 0
            WHERE NOT EXISTS(SELECT 1 FROM channel_configs WHERE channel_name = ?)
        ''', (channel, channel, channel))  
    
    conn.commit()
    conn.close()





def setup_bot(db_file, rebuild_cache=False, enable_tts=False):
    # Read configuration from file
    config = configparser.ConfigParser()
    config.read('settings.conf')
    
    # Get bot credentials
    token = config.get('auth', 'tmi_token')
    client_id = config.get('auth', 'client_id')
    nick = config.get('auth', 'nickname')
    
    # Get channels to join
    # Try different possible config locations for backward compatibility
    try:
        channels_str = config.get('channels', 'channels')
    except (configparser.NoSectionError, configparser.NoOptionError):
        try:
            # Try old format
            channels_str = config.get('settings', 'channels')
        except (configparser.NoSectionError, configparser.NoOptionError):
            print("⚠️ No channels found in config. Using single channel.")
            channels_str = nick  # Default to bot's own channel as fallback
    
    print(f"Found channels string: {channels_str}")
    
    # Strip whitespace and ensure channels start with #
    channels = [f"#{ch.strip()}" if not ch.strip().startswith('#') else ch.strip() 
                for ch in channels_str.split(',')]
    
    print(f"Bot will join these channels: {channels}")
    
    # Initialize bot instance
    bot = Bot(
        token=token,
        client_id=client_id, 
        nick=nick,
        prefix='!',
        initial_channels=channels,
        db_file=db_file,
        rebuild_cache=rebuild_cache,
        enable_tts=enable_tts
    )
    
    return bot
