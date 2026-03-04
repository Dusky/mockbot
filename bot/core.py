from twitchio.ext import commands
import twitchio.ext.pubsub as pubsub
import logging
import markovify
import asyncio
import configparser
import time
import sqlite3
import aiosqlite
from datetime import datetime, timezone
import os
import math
import json
from functools import lru_cache
from collections import OrderedDict

from colorama import init
from datetime import datetime
import threading
from tabulate import tabulate
from bot.logger import Logger
from bot.color_control import ColorManager
from bot.commands import mockbot_command
from bot.db import ensure_db_setup
from bot.tts import process_text, start_tts_processing # Added start_tts_processing

config = configparser.ConfigParser()
config.read("settings.conf")

db_file = "messages.db"  # Replace with your actual database file path
ensure_db_setup(db_file)


logger = Logger()
logger.setup_logger()

# init()  # init termcolor - disabled to avoid conflict with prompt_toolkit

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


class ConnectionStateManager:
    """Manages WebSocket connection state and reconnection logic with exponential backoff."""

    def __init__(self, bot_instance):
        self.bot = bot_instance
        self.state = "disconnected"  # disconnected, connecting, connected, reconnecting
        self.reconnect_attempts = 0
        self.last_attempt_time = None
        self.current_delay = 5  # Start at 5 seconds
        self.max_delay = 300  # Cap at 5 minutes
        self.base_delay = 5
        self.reconnect_task = None
        self.logger = logging.getLogger("connection_manager")

    def calculate_backoff_delay(self):
        """Exponential backoff: 5s, 10s, 20s, 40s, 80s, 160s, 300s (max)."""
        delay = min(self.base_delay * (2 ** self.reconnect_attempts), self.max_delay)
        return delay

    async def attempt_reconnect(self):
        """Attempt to reconnect with exponential backoff."""
        self.state = "reconnecting"
        self.reconnect_attempts += 1
        self.last_attempt_time = time.time()
        self.current_delay = self.calculate_backoff_delay()

        self.logger.warning(
            f"{YELLOW}Reconnection attempt #{self.reconnect_attempts}, "
            f"waiting {self.current_delay}s before retry...{RESET}"
        )

        # Log to database
        self._log_connection_event("reconnect_attempt", {
            "attempt": self.reconnect_attempts,
            "delay": self.current_delay
        })

        # Emit to admin dashboard
        if hasattr(self.bot, 'socketio_emitter') and self.bot.socketio_emitter:
            try:
                self.bot.socketio_emitter({
                    'event': 'connection_state_changed',
                    'state': 'reconnecting',
                    'attempts': self.reconnect_attempts,
                    'next_delay': self.current_delay
                })
            except Exception as e:
                self.logger.error(f"Failed to emit reconnection state: {e}")

        try:
            # Wait for backoff delay
            await asyncio.sleep(self.current_delay)

            # Close existing connection if still open
            if hasattr(self.bot, '_ws') and self.bot._ws and not self.bot._ws.is_closed:
                await self.bot._ws.close()

            # Attempt reconnection
            self.logger.info(f"{GREEN}Attempting to reconnect to Twitch...{RESET}")
            await self.bot.connect()

            # Success!
            self.state = "connected"
            total_attempts = self.reconnect_attempts
            self.reconnect_attempts = 0
            self.current_delay = self.base_delay

            self.logger.info(
                f"{GREEN}Successfully reconnected after {total_attempts} attempt(s)!{RESET}"
            )

            self._log_connection_event("reconnect_success", {
                "total_attempts": total_attempts
            })

            if hasattr(self.bot, 'socketio_emitter') and self.bot.socketio_emitter:
                try:
                    self.bot.socketio_emitter({
                        'event': 'connection_state_changed',
                        'state': 'connected',
                        'attempts': 0
                    })
                except Exception as e:
                    self.logger.error(f"Failed to emit connected state: {e}")

        except Exception as e:
            self.logger.error(
                f"{RED}Reconnection attempt #{self.reconnect_attempts} failed: {e}{RESET}"
            )

            self._log_connection_event("reconnect_failed", {
                "error": str(e),
                "attempt": self.reconnect_attempts
            })

            # Schedule next attempt (recursive)
            self.reconnect_task = asyncio.create_task(self.attempt_reconnect())

    def _log_connection_event(self, event_type, details):
        """Log connection events to database."""
        try:
            conn = sqlite3.connect(self.bot.db_file)
            c = conn.cursor()
            c.execute("""
                INSERT INTO connection_history (timestamp, event_type, details, attempt_number)
                VALUES (?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                event_type,
                json.dumps(details),
                self.reconnect_attempts
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            self.logger.error(f"Failed to log connection event to database: {e}")

    def mark_connected(self):
        """Mark connection as successful and reset counters."""
        self.state = "connected"
        self.reconnect_attempts = 0
        self.current_delay = self.base_delay
        self._log_connection_event("connected", {"channels": list(self.bot._joined_channels)})


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
        # print("Initializing Bot class...")
        self.prefix = prefix
        self.my_logger = Logger()
        self.my_logger.setup_logger()
        self.owner = None
        self.channels = initial_channels
        self.trusted_users = []
        self.ignored_users = []
        self.chat_line_count = 0
        self.last_message_time = time.time()
        self.last_global_message_time = datetime.now()
        self.is_sleeping = False
        
        # PERFORMANCE: Use memory-efficient caches with size limits to prevent memory leaks
        self.user_colors = LRUCache(maxsize=1000)  # Limit to 1000 users
        self.channel_colors = LRUCache(maxsize=100)  # Limit to 100 channels
        self.logger = logging.getLogger("bot")
        self.logger.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
        handler = logging.FileHandler(filename="logs/mockbot.log", encoding="utf-8", mode="w")
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.color_manager = ColorManager()
        self.channel_chat_line_count = {channel: 0 for channel in self.channels}
        self.channel_last_message_time = {
            channel: time.time() for channel in self.channels
        }
        self.channel_settings = {}  # Initialize the channel settings dictionary
        self.message_queue = [] # Queue for async DB bulk inserts
        self.db_flush_task = None
        self.db_file = db_file
        self.load_channel_settings()  # Populate channel settings

        self.rebuild_cache = rebuild_cache
        
        # Add cache update threshold setting
        self.cache_update_threshold = 3600 * 24  # 24 hours by default
        self.cache_build_times = {}  # Initialize as empty dict
        
        # Load cache build times before attempting to build model
        self.cache_build_times = self.load_last_cache_build_times()
        # print(f"Loaded cache build times: {self.cache_build_times}")
        
        self.load_text_and_build_model()
        self.first_model_update = True
        
        # Conditional call to update_model_periodically based on rebuild_cache flag
        if self.rebuild_cache:
            self.update_model_periodically()
        
        self.enable_tts = enable_tts
        if self.enable_tts:
            from bot import tts
            tts.initialize_tts()
        
        # Read verbose_heartbeat_log setting
        try:
            self.verbose_heartbeat_log = config.getboolean('settings', 'verbose_heartbeat_log')
        except (configparser.NoSectionError, configparser.NoOptionError):
            self.verbose_heartbeat_log = False # Default to False if not found
            # print(f"{YELLOW}Warning: 'verbose_heartbeat_log' not found in settings.conf, defaulting to False.{RESET}")

        self._joined_channels = set()

        self.start_time = time.time()

        # Initialize connection state manager for automatic reconnection
        self.connection_manager = ConnectionStateManager(self)
        self.pubsub_pool = pubsub.PubSubPool(self)
        self._channel_ids = {}
        self.socketio_emitter = None  # Will be set by webapp for real-time updates

        self.message_request_check = None

        self.message_request_check = self.loop.create_task(self.message_request_checker())
        
        self.live_streamers = set()
        self.live_stream_monitor_task = self.loop.create_task(self.live_stream_monitor_loop())
        
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
                async with aiosqlite.connect(self.db_file) as conn:
                    c = await conn.cursor()
                    await c.execute(
                        "UPDATE channel_configs SET currently_connected = 0 WHERE channel_name = ?",
                        (clean_name,)
                    )
                    await conn.commit()
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
            
            # print(f"{YELLOW}Attempting to join channel: {channel_name} (clean: {clean_name}){RESET}")
            
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
                    
                # Initialize timers for new channels so they don't instant-fire
                if channel_name not in self.channel_last_message_time:
                    self.channel_last_message_time[channel_name] = time.time()
                
                # 3. Update database to mark channel as connected
                try:
                    async with aiosqlite.connect(self.db_file) as conn:
                        c = await conn.cursor()
                        
                        # First check if channel exists in channel_configs
                        await c.execute("SELECT 1 FROM channel_configs WHERE channel_name = ?", (clean_name,))
                        if not await c.fetchone():
                            # Create entry if it doesn't exist
                            print(f"{YELLOW}Creating new channel config for {clean_name}{RESET}")
                            await c.execute('''
                                INSERT INTO channel_configs 
                                (channel_name, tts_enabled, voice_enabled, join_channel, owner, 
                                trusted_users, ignored_users, use_general_model, lines_between_messages, time_between_messages, currently_connected, tts_delay_enabled)
                                VALUES (?, 0, 1, 1, ?, '', '', 1, 50, 15, 1, 0)
                            ''', (clean_name, clean_name))
                        else:
                            # Update existing entry - mark as joined
                            await c.execute(
                                "UPDATE channel_configs SET join_channel = 1, currently_connected = 1 WHERE channel_name = ?",
                                (clean_name,)
                            )
                            
                        await conn.commit()
                    
                    # Force an immediate heartbeat update to sync the joined channel status
                    self.update_heartbeat_file()
                    
                except Exception as db_error:
                    print(f"{RED}Database update error for channel {clean_name}: {db_error}{RESET}")
                
                # print(f"{GREEN}✅ Successfully joined channel: {channel_name}{RESET}")
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
            async with aiosqlite.connect(self.db_file) as conn:
                c = await conn.cursor()
                await c.execute("SELECT channel_name FROM channel_configs WHERE join_channel = 1")
                channels_to_join = [row[0] for row in await c.fetchall()]
            
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
                    # print(f"{YELLOW}Attempting to join channel: {channel_name}{RESET}")
                    success = await self.join_channel(channel_name)
                    
                    if success:
                        join_success += 1
                        new_joins += 1
                        print(f"{GREEN}✓ Joined {channel_name}{RESET}")
                    else:
                        join_failure += 1
                        print(f"{RED}✗ Failed {channel_name}{RESET}")
                        
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
        # print(f"Started periodic channel check (every {interval} seconds)")
    
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
                     trusted_users, ignored_users, use_general_model, lines_between_messages, time_between_messages, currently_connected, tts_delay_enabled)
                    VALUES (?, 0, 1, 1, ?, '', '', 1, 50, 15, 0, 0)
                ''', (clean_channel, clean_channel))
        
        conn.commit()
        conn.close()
        
        # Reload channel settings after updating configs
        self.load_channel_settings()
    
    async def print_channel_status(self, channel_filter=None, out_func=None):
        """Print a status table showing all channels (or a specific channel) and their configurations."""
        out = out_func or self.my_logger.print_message
        try:
            async with aiosqlite.connect(self.db_file) as conn:
                c = await conn.cursor()
                
                table_data = []
                
                if channel_filter:
                    await c.execute('''
                        SELECT channel_name, owner, trusted_users, ignored_users, voice_enabled, tts_enabled, 
                               join_channel, time_between_messages, lines_between_messages, use_general_model, random_chance, log_dice
                        FROM channel_configs
                        WHERE channel_name = ?
                    ''', (channel_filter,))
                else:
                    await c.execute('''
                        SELECT channel_name, owner, trusted_users, ignored_users, voice_enabled, tts_enabled, 
                               join_channel, time_between_messages, lines_between_messages, use_general_model, random_chance, log_dice
                        FROM channel_configs
                    ''')
                
                rows = await c.fetchall()
                if not rows and channel_filter:
                    self.my_logger.print_message(f"No configuration found for #{channel_filter}")
                    return

                for row in rows:
                    channel, owner, trusted, ignored, voice, tts, join_enabled, time_between, lines_between, use_general, random_chance, log_dice = row
                    
                    # Format owner with color
                    owner_display = f"[color({self.get_user_color(owner)})]{owner}[/]" if owner else "None"
                    
                    # Format trusted users with colors
                    if trusted and trusted.strip():
                        trusted_display = ", ".join(
                            f"[color({self.get_user_color(user.strip())})]{user.strip()}[/]"
                            for user in trusted.split(",") if user.strip()
                        )
                    else:
                        trusted_display = ""
                        
                    # Format settings
                    voice_status = "[green]enabled[/green]" if voice else "[red]disabled[/red]"
                    tts_status = "[green]enabled[/green]" if tts else "[red]disabled[/red]"
                    model_status = "[green]general[/green]" if use_general else "[magenta]individual[/magenta]"
                    
                    # Check if channel is actually joined
                    is_joined = f"#{channel}" in self._joined_channels
                    join_status = "[green]joined[/green]" if is_joined else "[red]not joined[/red]"
                    
                    # Format time and lines settings
                    time_status = f"[green]{time_between}[/green]" if time_between > 0 else "[red]0[/red]"
                    lines_status = f"[green]{lines_between}[/green]" if lines_between > 0 else "[red]0[/red]"
                    
                    # Format chance
                    chance_status = f"[cyan]{random_chance}%[/cyan]" if random_chance > 0 else "[yellow]0.0%[/yellow]"
                    
                    # Format log dice
                    log_dice_status = "[green]on[/green]" if log_dice else "[red]off[/red]"
                    
                    # Formatting channel color properly via int/hex check
                    color_idx = self.get_channel_color(channel)
                    if isinstance(color_idx, str) and color_idx.isdigit():
                        chan_style = f"bold color({color_idx})"
                    else:
                        chan_style = f"bold {color_idx}"
                    
                    channel_display = f"[{chan_style}]#{channel}[/]"
                    
                    # Add to table
                    table_data.append([
                        channel_display, 
                        owner_display,
                        trusted_display,
                        voice_status,
                        tts_status,
                        join_status,
                        model_status,
                        time_status,
                        lines_status,
                        chance_status,
                        log_dice_status
                    ])
            
            from rich.table import Table
            from rich import box
            table = Table(
                title="Channel Configurations",
                title_style="bold cyan",
                box=box.ROUNDED,
                border_style="dim",
                header_style="bold white",
                padding=(0, 1),
            )
            headers = [
                ("Channel", "left"),
                ("Owner", "left"),
                ("Trusted Users", "left"),
                ("Voice", "center"),
                ("TTS", "center"),
                ("Autojoin", "center"),
                ("Model", "center"),
                ("Time", "right"),
                ("Lines", "right"),
                ("Chance", "right"),
                ("Log Dice", "center"),
            ]
            for h, j in headers:
                table.add_column(h, justify=j)
            for row in table_data:
                table.add_row(*row)
            out(table)
        
        except Exception as e:
            out(f"Error printing channel status: {e}")

    async def print_brain_status(self, channel_filter=None, out_func=None):
        """Print a status table showing the number of lines loaded for each channel's Markov brain and cache metadata."""
        out = out_func or out
        try:
            async with aiosqlite.connect(self.db_file) as conn:
                c = await conn.cursor()
                
                # Get use_general_model for channels
                await c.execute('SELECT channel_name, use_general_model FROM channel_configs')
                channel_models = {row[0]: row[1] for row in await c.fetchall()}
                
                table_data = []
                import os
                import datetime

                if channel_filter:
                    clean_channel = channel_filter.lstrip('#')
                    use_general = channel_models.get(clean_channel, 1) # Default to 1 (general) if not found
                    model_type = "General" if use_general else "Individual"
                    
                    if use_general:
                        cache_file_path = os.path.join("cache", "general_markov_model.json")
                        model_name = "general_markov_model"
                    else:
                        cache_file_path = os.path.join("cache", f"{clean_channel}_model.json")
                        model_name = f"{clean_channel}_model"
                    
                    await c.execute('SELECT COUNT(*) FROM messages WHERE is_bot_response = 0 AND channel = ?', (clean_channel,))
                    row = await c.fetchone()
                    msg_count = row[0] if row else 0
                    color_idx = self.get_channel_color(clean_channel)
                    if isinstance(color_idx, str) and color_idx.isdigit():
                        chan_style = f"color({color_idx})"
                    else:
                        chan_style = f"{color_idx}"
                    
                    out(f"\n🧠 [bold]Detailed Brain Stats for [{chan_style}]#{clean_channel}[/]:[/bold]")
                    out(f"  • Raw Messages in DB: {msg_count:,}")
                    out(f"  • Source Model:       {model_type} ({model_name})")
                    
                    if os.path.exists(cache_file_path):
                        size_bytes = os.path.getsize(cache_file_path)
                        cache_size_str = f"{size_bytes / 1024:.1f} KB"
                        mtime = os.path.getmtime(cache_file_path)
                        dt = datetime.datetime.fromtimestamp(mtime)
                        
                        out(f"  • Cache File Size:    {cache_size_str}")
                        out(f"  • Last Compiled:      {dt.strftime('%Y-%m-%d %H:%M:%S')}")
                        
                        try:
                            with open(cache_file_path, 'r', encoding='utf-8') as f:
                                json_str = f.read()
                            import markovify
                            import json
                            model = markovify.Text.from_json(json_str)
                            
                            state_size = model.state_size
                            num_parsed_sentences = len(model.parsed_sentences) if model.parsed_sentences else 0
                            
                            # Dictionary representation of the chain
                            chain_model = model.chain.model
                            num_states = len(chain_model) if isinstance(chain_model, dict) else "Unknown"
                            
                            # Top start words
                            try:
                                starts = chain_model.get((markovify.chain.BEGIN,) * state_size, {})
                                top_starts = sorted(starts.items(), key=lambda x: x[1], reverse=True)[:5]
                                top_starts_str = ", ".join([f"'{w[0]}': {c}" if isinstance(w, tuple) else f"'{w}': {c}" for w, c in top_starts])
                            except Exception:
                                top_starts_str = "Unavailable"
                                
                            out(f"  • State Size:         {state_size}")
                            out(f"  • Sentences Parsed:   {num_parsed_sentences:,}")
                            out(f"  • Unique States:      {num_states:,}" if isinstance(num_states, int) else f"  • Unique States:      {num_states}")
                            out(f"  • Top Start Words:    {top_starts_str}")

                        except Exception as e:
                            out(f"  • Error parsing cache: {str(e)}")
                    else:
                        out(f"  • Cache Status:       Not generated yet")
                    
                    out("")
                    return
                
                # Get total counts per channel from DB
                await c.execute('''
                    SELECT channel, COUNT(*) as count 
                    FROM messages 
                    WHERE is_bot_response = 0 
                    GROUP BY channel 
                    ORDER BY channel
                ''')
                
                total_lines = 0
                
                # Pre-fetch general model stats
                gen_cache_size_str = "N/A"
                gen_last_compiled_str = "None"
                gen_cache_file_path = os.path.join("cache", "general_markov_model.json")
                if os.path.exists(gen_cache_file_path):
                    size_bytes = os.path.getsize(gen_cache_file_path)
                    gen_cache_size_str = f"{size_bytes / 1024:.1f} KB"
                    mtime = os.path.getmtime(gen_cache_file_path)
                    dt = datetime.datetime.fromtimestamp(mtime)
                    gen_last_compiled_str = dt.strftime('%Y-%m-%d %H:%M:%S')

                for row in await c.fetchall():
                    channel, count = row
                    if channel:
                        clean_channel = channel.lstrip('#')
                        
                        use_general = channel_models.get(clean_channel, 1) # Default to 1 (general) if not found
                        model_type = "General" if use_general else "Individual"
                        
                        if use_general:
                            cache_size_str = gen_cache_size_str
                            last_compiled_str = gen_last_compiled_str
                        else:
                            cache_size_str = "N/A"
                            last_compiled_str = "None"
                            cache_file_path = os.path.join("cache", f"{clean_channel}_model.json")
                            if os.path.exists(cache_file_path):
                                size_bytes = os.path.getsize(cache_file_path)
                                cache_size_str = f"{size_bytes / 1024:.1f} KB"
                                
                                mtime = os.path.getmtime(cache_file_path)
                                dt = datetime.datetime.fromtimestamp(mtime)
                                last_compiled_str = dt.strftime('%Y-%m-%d %H:%M:%S')
                                
                        color_idx = self.get_channel_color(clean_channel)
                        if isinstance(color_idx, str) and color_idx.isdigit():
                            chan_style = f"color({color_idx})"
                        else:
                            chan_style = f"{color_idx}"

                        # Add to table
                        table_data.append([
                            f"[{chan_style}]#{clean_channel}[/]", 
                            f"{count:,}",
                            model_type,
                            cache_size_str,
                            last_compiled_str
                        ])
                        total_lines += count
                 
                total_label = "[bold yellow]Total[/bold yellow]"
                table_data.append([total_label, f"{total_lines:,}", "", "", ""])
                
                from rich.table import Table
                from rich import box
                table = Table(
                    title="Brain Statistics",
                    title_style="bold cyan",
                    box=box.ROUNDED,
                    border_style="dim",
                    header_style="bold white",
                    padding=(0, 1),
                )
                headers = [
                    ("Channel", "left"),
                    ("Lines in Brain", "right"),
                    ("Model Type", "center"),
                    ("Cache Size", "right"),
                    ("Last Compiled", "center"),
                ]
                for h, j in headers:
                    table.add_column(h, justify=j)
                for row in table_data:
                    table.add_row(*row)
                out(table)
                
                
                # Check for cached general model (standalone print below the table)
                if gen_cache_size_str != "N/A":
                    out(f"\nGeneral Model Cache Size: {gen_cache_size_str}")
                    out(f"General Model Last Compiled: {gen_last_compiled_str}")
                else:
                    out("\nGeneral Model Cache: Not generated yet")
                    
        except Exception as e:
            out(f"Error printing brain status: {e}")

    def load_text_and_build_model(self, create_individual_caches=False, target_channel=None):
        cache_directory = "cache/"
        if not os.path.exists(cache_directory):
            os.makedirs(cache_directory)
        self.text = ""  # Text for the general model
        self.models = {}  # Dictionary for channel-specific models
        total_lines = 0
        files_data = []

        self.cache_build_times = self.load_last_cache_build_times()
        line_threshold = 50

        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        
        # ONE-TIME MIGRATION: Migrating old logs/*.txt to DB if they exist
        directory = "logs/"
        if os.path.exists(directory):
            import datetime
            for filename in os.listdir(directory):
                if filename.endswith(".txt"):
                    file_path = os.path.join(directory, filename)
                    channel_name = filename[:-4]
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        lines = [line.strip() for line in f if line.strip()]
                    if lines:
                        out(f"Migrating {len(lines)} legacy log entries for #{channel_name} to database...")
                        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        c.executemany(
                            "INSERT INTO messages (message, timestamp, channel, author_name, is_bot_response) VALUES (?, ?, ?, ?, 0)",
                            [(line, timestamp, channel_name, "legacy_user") for line in lines]
                        )
                    os.rename(file_path, file_path + ".imported")
            conn.commit()
            
        # Get active channels
        c.execute("SELECT channel_name FROM channel_configs")
        valid_channels = set(row[0] for row in c.fetchall())

        # Grab all non-bot messages ordered by channel
        c.execute("SELECT channel, message FROM messages WHERE is_bot_response = 0 ORDER BY channel")
        rows = c.fetchall()

        # Group messages by channel
        channel_messages = {}
        for row in rows:
            channel, msg = row
            if channel:
                channel_name = channel.lstrip('#')
                if channel_name not in channel_messages:
                    channel_messages[channel_name] = []
                channel_messages[channel_name].append(msg)
                
        for channel_name, msgs in channel_messages.items():
            if not msgs: continue
            file_text = "\n".join(msgs) + "\n"
            line_count = len(msgs)
            total_lines += line_count
            self.text += file_text

            cache_status = f"{RED}Unchanged{RESET}"
            
            should_compile_individual = create_individual_caches and channel_name in valid_channels and line_count >= line_threshold
            if target_channel and target_channel != "Global" and channel_name != target_channel.lstrip('#'):
                should_compile_individual = False
                
            if should_compile_individual:
                cache_file_path = os.path.join(cache_directory, f"{channel_name}_model.json")
                cache_status = self.create_channel_model(channel_name, file_text, cache_file_path)

            files_data.append([
                channel_name, 
                f"{line_count:,}", 
                cache_status, 
                "General Model" if cache_status == f"{RED}Unchanged{RESET}" else f"{channel_name}_model.json"
            ])

        if self.text:
            self.general_model = markovify.Text(self.text)
            
            should_compile_general = True
            if target_channel and target_channel != "Global":
                should_compile_general = False
                
            general_cache_file_path = os.path.join(cache_directory, "general_markov_model.json")
            last_build_time = self.cache_build_times.get("general_markov_model.json")
            general_cache_status = f"{RED}Unchanged{RESET}"
            
            if should_compile_general and (self.rebuild_cache or last_build_time is None):
                self.save_general_model_to_cache(general_cache_file_path)
                general_cache_status = f"{GREEN}Updated{RESET}"
                # Update build time
                self.cache_build_times["general_markov_model.json"] = time.time()
                self.save_cache_build_times()

        conn.close()

        # Add total and general model status to table
        total_label = f"{YELLOW}Total{RESET}"
        files_data.append([total_label, f"{total_lines:,}", general_cache_status, "general_markov_model.json"])

        # Print the table outside the loop, after processing all files
        headers = ["Channel", "Brain Size", "Brain Status", "Brain"]
        # print(tabulate(files_data, headers=headers, tablefmt="pretty", numalign="right"))
        out(f"Brain loaded: {total_lines:,} lines active.")

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
            return f"[green]Updated[/]"
        except Exception as e:
            out(f"Error creating model for {channel_name}: {e}")
            return f"[red]Error[/]"

    def save_general_model_to_cache(self, cache_file_path):
        """Save the general model to the cache."""
        try:
            with open(cache_file_path, 'w') as cache_file:
                cache_file.write(self.general_model.to_json())
            return True
        except Exception as e:
            out(f"Error saving general model to cache: {e}")
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
                datetime.now(timezone.utc).isoformat(), # Store timestamp as ISO string in UTC
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

    @commands.command(name="mockbot", aliases=["mb"])
    async def mockbot_wrapper(self, ctx, setting=None, *args):
        """Enhanced command handler for Mockbot commands"""
        channel_name = ctx.channel.name

        # If no setting provided, show description
        if not setting:
            await ctx.send("Mockbot utilizes Markov chain modeling to generate text. It learns from the channel's conversation history, mapping how words connect to each other, then creates new messages using those probability distributions.")
            return

        # Convert setting to lowercase for easier comparison
        setting = setting.lower()

        # Show settings help
        if setting == "settings":
            await ctx.send("Usage: !mockbot [setting] [value]. Available settings: trusted, voice, tts, lines, time, timer, addc, editc, delc, grammar, poll")
            return

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
                    await ctx.send("Usage: !mockbot trusted add/remove [username]")
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
        elif setting == "addc":
            from bot.commands import mockbot_addc
            if len(args) < 2:
                await ctx.send("Usage: !addc <cmd> <response>")
                return
            await mockbot_addc(self, ctx, args[0], response_template=" ".join(args[1:]))
            
        elif setting == "editc":
            from bot.commands import mockbot_editc
            if len(args) < 2:
                await ctx.send("Usage: !editc <cmd> <response>")
                return
            await mockbot_editc(self, ctx, args[0], response_template=" ".join(args[1:]))
            
        elif setting == "delc":
            from bot.commands import mockbot_delc
            if len(args) < 1:
                await ctx.send("Usage: !delc <cmd>")
                return
            await mockbot_delc(self, ctx, args[0])
            
        elif setting == "grammar":
            from bot.commands import mockbot_grammar
            if len(args) < 2:
                await ctx.send("Usage: !grammar <add|list|clear> <rule> [text]")
                return
            await mockbot_grammar(self, ctx, args[0], args[1], text=" ".join(args[2:]) if len(args) > 2 else "")
            
        elif setting == "poll":
            from bot.commands import mockbot_poll
            await mockbot_poll(self, ctx, *args)
            
        elif setting == "timer":
            from bot.commands import mockbot_timer
            await mockbot_timer(self, ctx, *args)
            
        else:
            # Call the original mockbot_command for other settings
            await mockbot_command(self, ctx, setting, args[0] if args else None, enable_tts=self.enable_tts)

    @staticmethod
    def convert_size(size_bytes):
        if size_bytes == 0:
            return "0B"
        size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_name[i]}"

    async def fetch_channel_settings(self, channel_name):
        try:
            async with aiosqlite.connect(self.db_file) as conn:
                c = await conn.cursor()
                await c.execute(
                    "SELECT lines_between_messages, time_between_messages, tts_enabled, voice_enabled, random_chance, log_dice FROM channel_configs WHERE channel_name = ?",
                    (channel_name,)
                )
                row = await c.fetchone()
                if row:
                    random_chance = float(row[4]) if len(row) > 4 and row[4] is not None else 0.0
                    log_dice = bool(row[5]) if len(row) > 5 and row[5] is not None else False
                    return row[0], row[1], row[2], row[3], random_chance, log_dice  # lines, time, tts, voice, chance, log_dice
                else:
                    return 0, 0, False, False, 0.0, False  # Default values
        except Exception as e:
            print(f"SQLite error in fetch_channel_settings: {e}")
            return 0, 0, False, False, 0.0, False

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

    def get_tts_delay_setting(self, channel_name):
        """Get TTS delay setting for a channel"""
        try:
            clean_channel_name = channel_name.lstrip('#')
            conn = sqlite3.connect(self.db_file)
            c = conn.cursor()
            c.execute("SELECT tts_delay_enabled FROM channel_configs WHERE channel_name = ?", (clean_channel_name,))
            result = c.fetchone()
            conn.close()
            if result and result[0]:
                self.logger.debug(f"TTS delay enabled for channel {clean_channel_name}: {result[0]}")
                return bool(result[0])
            else:
                self.logger.debug(f"TTS delay disabled or not set for channel {clean_channel_name}")
                return False
        except sqlite3.Error as e:
            self.logger.error(f"SQLite error in get_tts_delay_setting for {channel_name}: {e}")
            return False # Fallback to disabled on error

    async def generate_tts_sync(self, text, channel_name, voice_preset, message_id, timestamp_str):
        """Generate TTS synchronously and return success status"""
        try:
            import asyncio
            from concurrent.futures import ThreadPoolExecutor
            
            self.logger.info(f"Starting synchronous TTS generation for {channel_name}: '{text[:30]}...'")
            
            # Create a result container
            result = {'success': False, 'file_path': None, 'tts_id': None}
            
            def tts_worker():
                """Worker function to run TTS generation in thread"""
                try:
                    from utils.tts import process_text_thread
                    import os
                    from datetime import datetime
                    
                    # Generate filename
                    filename_timestamp = datetime.now().strftime("%Y%m%d-%H%M%S") 
                    clean_channel_name = channel_name.lstrip('#')
                    output_dir = f"static/outputs/{clean_channel_name}"
                    os.makedirs(output_dir, exist_ok=True)
                    generated_full_path = f"{output_dir}/{clean_channel_name}-{filename_timestamp}.wav"
                    
                    # Call process_text_thread synchronously
                    file_path, tts_id = process_text_thread(
                        input_text=text,
                        channel_name=channel_name,
                        db_file=self.db_file,
                        full_path=generated_full_path,
                        timestamp=timestamp_str,
                        message_id=message_id,
                        voice_preset=voice_preset
                    )
                    
                    if file_path and tts_id:
                        result['success'] = True
                        result['file_path'] = file_path
                        result['tts_id'] = tts_id
                        self.logger.info(f"Synchronous TTS generation completed: {file_path}")
                    else:
                        self.logger.error(f"TTS generation failed for {channel_name}")
                        
                except Exception as e:
                    self.logger.error(f"Error in TTS worker thread: {e}")
                    
            # Run TTS generation in thread pool
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor(max_workers=1) as executor:
                # Wait for TTS generation to complete with timeout
                await loop.run_in_executor(executor, tts_worker)
                
            return result['success']
            
        except Exception as e:
            self.logger.error(f"Error in generate_tts_sync for {channel_name}: {e}")
            return False

    async def event_ready(self):
        """Handle the bot ready event."""
        # Use verbose flag for detailed output        
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
        
        # Step 2.5: Cache Bot's Twitch User ID for API calls (like Timeout)
        try:
            bot_users = await self.fetch_users(names=[self.nick])
            if bot_users:
                self.bot_user_id = bot_users[0].id
                if verbose:
                    print(f"{GREEN}✅ Bot User ID Cached: {self.bot_user_id}{RESET}")
        except Exception as e:
            print(f"{RED}❌ Failed to cache Bot User ID: {e}{RESET}")
        
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
                                trusted_users, ignored_users, use_general_model, lines_between_messages, time_between_messages, currently_connected, tts_delay_enabled, pubsub_bits, pubsub_points)
                                VALUES (?, 0, 1, 1, ?, '', '', 1, 50, 15, 0, 0, 0, 0)
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
            
        # Step 9: Start background DB writer & Timed Message Loop
        try:
            if verbose:
                print(f"{YELLOW}Step 9: Starting background DB writer, Timed Message Loop, and Sleep Monitor...{RESET}")
            self.db_flush_task = self.loop.create_task(self.background_db_writer())
            self.timed_msg_task = self.loop.create_task(self.timed_message_loop())
            self.sleep_monitor_task = self.loop.create_task(self.sleep_monitor_loop())
            if verbose:
                print(f"{GREEN}✅ Background loops started{RESET}")
        except Exception as e:
            print(f"{RED}❌ Error starting background loops: {e}{RESET}")
            
        # Step 10: Setup PubSub
        try:
            if verbose:
                print(f"{YELLOW}Step 10: Setting up PubSub for Bits & Channel Points...{RESET}")
            
            tmi_token = config.get("auth", "tmi_token")
            if tmi_token.startswith("oauth:"):
                tmi_token = tmi_token[6:]
                
            clean_channels = [c.lstrip('#') for c in self._joined_channels]
            users = await self.fetch_users(names=clean_channels)
            
            topics = []
            try:
                async with aiosqlite.connect(self.db_file) as conn:
                    c = await conn.cursor()
                    for user in users:
                        self._channel_ids[user.id] = f"#{user.name}"
                        await c.execute("SELECT pubsub_bits, pubsub_points FROM channel_configs WHERE channel_name = ?", (user.name,))
                        row = await c.fetchone()
                        bits_enabled, points_enabled = row if row else (0, 0)
                        
                        if bits_enabled:
                            topics.append(pubsub.bits(tmi_token)[user.id])
                        if points_enabled:
                            topics.append(pubsub.channel_points(tmi_token)[user.id])
            except Exception as e:
                print(f"Failed to load pubsub configs: {e}")
                
            if topics:
                await self.pubsub_pool.subscribe_topics(topics)
                if verbose:
                    print(f"{GREEN}✅ Subscribed to PubSub topics for {len(users)} channels{RESET}")
        except Exception as e:
            print(f"{RED}❌ Error setting up PubSub: {e}{RESET}")

        # Final verification
        if verbose:
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
                if verbose or channel in self.channels:
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

        # Mark connection as successful for reconnection manager
        self.connection_manager.mark_connected()

    async def sleep_monitor_loop(self):
        """Monitors global chat activity and suspends heavy background tasks during long quiet periods (15m)."""
        self.logger.info("Smart Sleep Monitor started.")
        while True:
            try:
                await asyncio.sleep(60.0)  # Check every minute
                delta = (datetime.now() - self.last_global_message_time).total_seconds()
                
                # If total silence > 15 minutes and we aren't asleep yet
                if delta > 900 and not self.is_sleeping:
                    self.is_sleeping = True
                    self.logger.info("Global chat has been silent for 15+ minutes. Entering Smart Sleep Mode.")
                    out("[dim italic]Entering Smart Sleep Mode due to inactivity...[/dim italic]")
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in sleep monitor: {e}")

    async def timed_message_loop(self):
        """Background asynchronous task that periodically evaluates and sends Timed Messages."""
        self.logger.info("Timed message loop started.")
        while True:
            try:
                await asyncio.sleep(60.0)  # Check every 60 seconds
                if self.is_sleeping:
                    continue  # Do not dispatch timed messages or query DB if the bot is globally sleeping
                
                try:
                    import aiosqlite
                    import random
                    async with aiosqlite.connect(self.db_file) as conn:
                        c = await conn.cursor()
                        
                        # Find pools where the interval has elapsed since last_sent_time
                        # and verify the bot is currently in the channel
                        await c.execute("""
                            SELECT pool_name, channel_name 
                            FROM timed_message_pools 
                            WHERE (julianday(CURRENT_TIMESTAMP) - julianday(last_sent_time)) * 1440 >= interval_minutes
                        """)
                        ready_pools = await c.fetchall()
                        
                        for pool_name, channel_name in ready_pools:
                            # Verify bot is in channel
                            if f"#{channel_name}" not in self._joined_channels:
                                continue
                                
                            # Retrieve all messages for this pool
                            await c.execute(
                                "SELECT message_text FROM timed_messages WHERE pool_name = ? AND channel_name = ?",
                                (pool_name, channel_name)
                            )
                            messages = await c.fetchall()
                            
                            if messages:
                                # Pick a random message from the pool
                                msg_text = random.choice(messages)[0]
                                
                                # Process Tracery if it contains '#'
                                if '#' in msg_text:
                                    import tracery
                                    from tracery.modifiers import base_english
                                    
                                    # Fetch grammar rules for this channel + global rules
                                    await c.execute(
                                        "SELECT rule_name, options_json FROM custom_grammar WHERE channel_name = ? OR channel_name = 'global'",
                                        (channel_name,)
                                    )
                                    grammar_rows = await c.fetchall()
                                    
                                    rules = {}
                                    import json
                                    for rule_name, options_json in grammar_rows:
                                        try:
                                            rules[rule_name] = json.loads(options_json)
                                        except:
                                            pass
                                            
                                    rules["origin"] = [msg_text]
                                    rules["streamer"] = [channel_name]
                                    grammar = tracery.Grammar(rules)
                                    grammar.add_modifiers(base_english)
                                    msg_text = grammar.flatten("#origin#")

                                # Send the timed message
                                channel_obj = self.get_channel(channel_name)
                                if channel_obj and msg_text:
                                    await channel_obj.send(msg_text)
                                    self.my_logger.log_message(channel_name, self.nick, msg_text, is_bot_message=True)
                                    
                                    # Update the last_sent_time so the interval resets
                                    await c.execute(
                                        "UPDATE timed_message_pools SET last_sent_time = CURRENT_TIMESTAMP WHERE pool_name = ? AND channel_name = ?",
                                        (pool_name, channel_name)
                                    )
                                    await conn.commit()
                except Exception as loop_db_error:
                    self.logger.error(f"Database error in timed messages loop: {loop_db_error}")

            except asyncio.CancelledError:
                self.logger.info("Timed message loop cancelled.")
                break
            except Exception as e:
                self.logger.error(f"Unexpected error in timed message loop: {e}")

    async def flush_db_queue(self):
        """Force flush remaining messages in the queue to the database."""
        if not self.message_queue:
            return
            
        messages_to_insert = list(self.message_queue)
        self.message_queue.clear()
        
        try:
            async with aiosqlite.connect(self.db_file) as conn:
                c = await conn.cursor()
                await c.executemany(
                    """INSERT INTO messages (twitch_message_id, message, author_name, timestamp, channel, is_bot_response, message_length, tts_processed)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    messages_to_insert
                )
                await conn.commit()
            self.logger.info(f"Forcibly flushed {len(messages_to_insert)} messages to DB during shutdown.")
        except Exception as e:
            self.logger.error(f"Failed to cleanly flush message queue: {e}")

    async def background_db_writer(self):
        """Background asynchronous task that periodically commits queued messages to the database in bulk."""
        self.logger.info("Background DB writer started.")
        while True:
            try:
                await asyncio.sleep(2.0)  # Flush every 2 seconds
                
                if not self.message_queue:
                    continue
                    
                # Take a shallow copy and clear the main list lock-free
                messages_to_insert = list(self.message_queue)
                self.message_queue.clear()

                try:
                    async with aiosqlite.connect(self.db_file) as conn:
                        c = await conn.cursor()
                        await c.executemany(
                            """INSERT INTO messages (twitch_message_id, message, author_name, timestamp, channel, is_bot_response, message_length, tts_processed)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            messages_to_insert
                        )
                        await conn.commit()
                except Exception as db_err:
                    self.logger.error(f"Failed bulk inserting messages to DB: {db_err}")
                    # Re-queue the messages so they aren't lost if the DB momentarily locked
                    self.message_queue.extend(messages_to_insert)
            except asyncio.CancelledError:
                self.logger.info("Background DB writer cancelled. Flushing remaining messages...")
                await self.flush_db_queue()
                break
            except Exception as e:
                self.logger.error(f"Unexpected error in background DB writer: {e}")
                await asyncio.sleep(2.0)

    async def live_stream_monitor_loop(self):
        """Periodically check which of our joined channels are currently live on Twitch."""
        # Wait a bit before first check so bot can init fully
        await asyncio.sleep(15)
        while True:
            try:
                if self._joined_channels:
                    check_channels = [c.lstrip('#') for c in self._joined_channels]
                    live_set = set()
                    
                    # fetch_streams takes a max of 100 user logins at a time
                    for i in range(0, len(check_channels), 100):
                        chunk = check_channels[i:i+100]
                        try:
                            streams = await self.fetch_streams(user_logins=chunk)
                            for stream in streams:
                                live_set.add(stream.user.name.lower())
                        except Exception as chunk_err:
                            self.logger.error(f"Failed to fetch stream chunk: {chunk_err}")
                            
                    self.live_streamers = live_set
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Unexpected error in live stream monitor: {e}")
                
            # Check every 3 minutes to avoid API spam
            await asyncio.sleep(180)

    async def event_pubsub_bits(self, event: pubsub.PubSubBitsMessage):
        """Handle incoming Bits/Cheers via PubSub."""
        channel_name = self._channel_ids.get(event.channel_id, "unknown")
        user_name = event.user.name if event.user else "Anonymous"
        
        try:
            async with aiosqlite.connect(self.db_file) as conn:
                c = await conn.cursor()
                await c.execute("SELECT pubsub_bits FROM channel_configs WHERE channel_name = ?", (channel_name.lstrip('#'),))
                row = await c.fetchone()
                if not row or not row[0]:
                    return  # Bits tracking is disabled
        except Exception as e:
            self.logger.error(f"Failed to check pubsub_bits config: {e}")
            return
            
        self.logger.info(f"Received {event.bits_used} bits from {user_name} in {channel_name}!")
        
        # We can implement a fun random response or customized cheer logic here!
        channel = self.get_channel(channel_name.lstrip('#'))
        if channel:
            await channel.send(f"Thank you {user_name} for the {event.bits_used} bits! bloodTrail")

    async def event_pubsub_channel_points(self, event: pubsub.PubSubChannelPointsMessage):
        """Handle channel point redemptions via PubSub."""
        channel_name = self._channel_ids.get(event.channel_id, "unknown")
        user_name = event.user.name if event.user else "Anonymous"
        reward_title = event.reward.title
        
        try:
            async with aiosqlite.connect(self.db_file) as conn:
                c = await conn.cursor()
                await c.execute("SELECT pubsub_points FROM channel_configs WHERE channel_name = ?", (channel_name.lstrip('#'),))
                row = await c.fetchone()
                if not row or not row[0]:
                    return  # Points tracking is disabled
        except Exception as e:
            self.logger.error(f"Failed to check pubsub_points config: {e}")
            return
            
        self.logger.info(f"Channel point redemption: {reward_title} by {user_name} in {channel_name}")
        
        # Forward this to the custom command logic if the reward title matches a command!
        # We simulate a Twitch message object since our custom command logic requires one.
        class DummyMessage:
            def __init__(self, author_name, content, ch):
                self.author = type('DummyAuthor', (), {'name': author_name})()
                self.content = content
                self.channel = type('DummyChannel', (), {'name': ch.lstrip('#')})()
        
        # If the reward title matches a custom command, execute it!
        # We prefix it with '!' just in case it's defined that way in DB.
        cmd_trigger = reward_title if reward_title.startswith('!') else f"!{reward_title}"
        dummy_msg = DummyMessage(user_name, f"{cmd_trigger} {event.input or ''}", channel_name)
        
        # Check custom commands first (simulating what event_message does)
        try:
            async with aiosqlite.connect(self.db_file) as conn:
                c = await conn.cursor()
                await c.execute(
                    "SELECT response_template FROM custom_commands WHERE (channel_name = ? OR channel_name = 'global') AND command_name = ? ORDER BY channel_name = 'global' ASC LIMIT 1",
                    (channel_name.lstrip('#'), cmd_trigger)
                )
                row = await c.fetchone()
                if row:
                    response_template = row[0]
                    # Fetch grammar
                    await c.execute("SELECT rule_name, options_json FROM custom_grammar WHERE channel_name = ? OR channel_name = 'global'", (channel_name.lstrip('#'),))
                    db_rules = await c.fetchall()
                    
                    import tracery
                    import json
                    from tracery.modifiers import base_english
                    
                    rules = {}
                    for r_name, o_json in db_rules:
                        rules[r_name] = json.loads(o_json)
                        
                    rules["sender"] = [user_name]
                    rules["streamer"] = [channel_name.lstrip('#')]
                    rules["input"] = [event.input or ""]
                    
                    grammar = tracery.Grammar(rules)
                    grammar.add_modifiers(base_english)
                    
                    # Pre-replace the exact tags
                    formatted_template = response_template.replace("<{sender}>", "#sender#").replace("<{streamer}>", "#streamer#").replace("<{input}>", "#input#")
                    
                    final_response = grammar.flatten(formatted_template)
                    channel = self.get_channel(channel_name.lstrip('#'))
                    if channel:
                        await channel.send(final_response)
                        
                    # Also log it
                    self.logger.info(f"Custom command triggered by channel points: {cmd_trigger} -> {final_response}")
                    
        except Exception as e:
            self.logger.error(f"Error evaluating custom command from channel points: {e}")

    async def event_error(self, error, data=None):
        """Handle TwitchIO errors and initiate reconnection if needed."""
        error_msg = str(error)
        self.logger.error(f"{RED}TwitchIO Error: {error_msg}{RESET}")

        # Log to error_log table
        self._log_error("twitchio_error", error_msg, data)

        # Check if it's a connection error
        if any(keyword in error_msg.lower() for keyword in ["websocket", "connection", "disconnect", "network"]):
            self.logger.warning(f"{YELLOW}Connection error detected, initiating reconnection...{RESET}")
            self.connection_manager.state = "disconnected"

            # Start reconnection if not already reconnecting
            if not self.connection_manager.reconnect_task or self.connection_manager.reconnect_task.done():
                self.connection_manager.reconnect_task = asyncio.create_task(
                    self.connection_manager.attempt_reconnect()
                )

    async def event_disconnect(self):
        """Handle WebSocket disconnection and initiate automatic reconnection."""
        self.logger.warning(f"{YELLOW}WebSocket disconnected!{RESET}")
        self.connection_manager.state = "disconnected"

        # Log disconnection
        self.connection_manager._log_connection_event("disconnected", {
            "channels": list(self._joined_channels)
        })

        # Emit to admin dashboard
        if self.socketio_emitter:
            try:
                self.socketio_emitter({
                    'event': 'connection_state_changed',
                    'state': 'disconnected'
                })
            except Exception as e:
                self.logger.error(f"Failed to emit disconnection state: {e}")

        # Update database to mark channels as disconnected
        try:
            conn = sqlite3.connect(self.db_file)
            c = conn.cursor()
            c.execute("UPDATE channel_configs SET currently_connected = 0")
            conn.commit()
            conn.close()
        except Exception as e:
            self.logger.error(f"Failed to update channel connection status: {e}")

        # Start reconnection
        if not self.connection_manager.reconnect_task or self.connection_manager.reconnect_task.done():
            self.logger.info(f"{GREEN}Starting automatic reconnection...{RESET}")
            self.connection_manager.reconnect_task = asyncio.create_task(
                self.connection_manager.attempt_reconnect()
            )

    def _log_error(self, level, message, extra_data=None):
        """Log errors to error_log table and emit to admin dashboard."""
        try:
            conn = sqlite3.connect(self.db_file)
            c = conn.cursor()
            c.execute("""
                INSERT INTO error_log (timestamp, level, message, source, stack_trace)
                VALUES (?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                level,
                message,
                'bot',
                json.dumps(extra_data) if extra_data else None
            ))
            conn.commit()
            conn.close()

            # Emit to admin dashboard
            if self.socketio_emitter:
                try:
                    self.socketio_emitter({
                        'event': 'error_logged',
                        'level': level,
                        'message': message,
                        'timestamp': datetime.now().isoformat()
                    })
                except Exception as e:
                    self.logger.error(f"Failed to emit error to dashboard: {e}")
        except Exception as e:
            self.logger.error(f"Failed to log error to database: {e}")

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
            channel = ctx.channel.name if ctx.channel else None
            self.my_logger.error(f"Error in command: {ctx.command.name}, {error}", channel=channel)


    def log_message(self, message):
        msg = f"{message.author.name}: {message.content}"
        return self.my_logger.info(msg)

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
        # Update the global tracker to stave off Sleep Mode
        self.last_global_message_time = datetime.now()
        if self.is_sleeping:
            self.is_sleeping = False
            self.logger.info("Chat activity detected! Waking up from Smart Sleep Mode.")
            out("[bold yellow]Waking up from Smart Sleep Mode![/bold yellow]")
            
        # Ignore messages from the bot itself or messages with no author.
        if message.author is None or message.author.name.lower() == self.nick.lower():
            return

        channel_name = message.channel.name.lower()
        author_name = message.author.name.lower()
        
        # Check ignored users early, before logging
        ignored_users = [user.lower() for user in self.channel_settings[channel_name]['ignored_users']] if channel_name in self.channel_settings else []
        if author_name in ignored_users:
            return
            
        # Log the message and check for bad words.
        message_clean = self.my_logger.log_message(
            channel_name, 
            message.author.name, 
            message.content, 
            color_hex=message.tags.get('color') if getattr(message, 'tags', None) else None
        )
        if not message_clean:
            return

        # Fetch the channel settings for the current channel.
        lines_between, time_between, tts_enabled, voice_enabled, random_chance, log_dice = await self.fetch_channel_settings(channel_name)

        # --- CUSTOM COMMANDS & GRAMMAR (Funtoon Style) ---
        # Any message's first word could technically be a custom command trigger, no "!" required.
        command_parts = message.content.split(maxsplit=1)
        if command_parts:
            cmd_name = command_parts[0].lower()
            cmd_input = command_parts[1] if len(command_parts) > 1 else ""
            
            # Check db for custom command, prioritizing channel-specific, then global
            try:
                import aiosqlite
                async with aiosqlite.connect(self.db_file) as conn:
                    c = await conn.cursor()
                    await c.execute(
                        "SELECT response_template FROM custom_commands WHERE (channel_name = ? OR channel_name = 'global') AND command_name = ? ORDER BY channel_name DESC LIMIT 1",
                        (channel_name, cmd_name)
                    )
                    cmd_row = await c.fetchone()
                    
                    if cmd_row:
                        response_template = cmd_row[0]
                        
                        # Fetch grammar rules for this channel + global rules
                        await c.execute(
                            "SELECT rule_name, options_json FROM custom_grammar WHERE channel_name = ? OR channel_name = 'global'",
                            (channel_name,)
                        )
                        grammar_rows = await c.fetchall()
                        
                        rules = {}
                        import json
                        for rule_name, options_json in grammar_rows:
                            try:
                                # Prioritize channel rules over global rules if they have the same name (by overwriting)
                                rules[rule_name] = json.loads(options_json)
                            except:
                                pass
                                
                        # Inject built-in variables as static rules
                        rules["sender"] = [message.author.name]
                        rules["streamer"] = [channel_name]
                        rules["input"] = [cmd_input]
                        
                        # Generate response using Tracery
                        import tracery
                        from tracery.modifiers import base_english
                        grammar = tracery.Grammar(rules)
                        grammar.add_modifiers(base_english)
                        
                        # Replace <{...}> syntax with Tracery #...# syntax for variables
                        formatted_template = response_template.replace("<{sender}>", "#sender#")
                        formatted_template = formatted_template.replace("<{streamer}>", "#streamer#")
                        formatted_template = formatted_template.replace("<{input}>", "#input#")
                        
                        # Unescape \< and << that funtoon uses to prevent parsing
                        formatted_template = formatted_template.replace("\\<", "<").replace("<<", "<")
                        
                        generated_response = grammar.flatten(formatted_template)
                        
                        # --- MODERATION ACTION INTERCEPTOR ---
                        import re
                        timeout_match = re.search(r'\{timeout:(.+?):(\d+)\}', generated_response)
                        if timeout_match:
                            target_user = timeout_match.group(1).strip()
                            duration = int(timeout_match.group(2))
                            
                            # Clean the generated response so the tag doesn't show in chat
                            generated_response = re.sub(r'\{timeout:(.+?):(\d+)\}', '', generated_response).strip()
                            
                            # Security Check
                            # The sender MUST be a moderator, the broadcaster, the global bot owner, OR they are targeting themselves.
                            config.read("settings.conf")
                            bot_owner = config.get("auth", "owner", fallback="").lower()
                            
                            is_mod = message.author.is_mod
                            is_broadcaster = message.author.is_broadcaster
                            is_owner = message.author.name.lower() == bot_owner
                            is_self_target = target_user.lower() == message.author.name.lower()
                            
                            if is_mod or is_broadcaster or is_owner or is_self_target:
                                # Fetch Broadcaster (to act as the channel context) and Target User
                                try:
                                    channel_users = await self.fetch_users(names=[channel_name])
                                    target_users = await self.fetch_users(names=[target_user])
                                    if channel_users and target_users and hasattr(self, 'bot_user_id'):
                                        channel_user_obj = channel_users[0]
                                        target_user_obj = target_users[0]
                                        
                                        tmi_token = config.get("auth", "tmi_token")
                                        if tmi_token.startswith("oauth:"):
                                            tmi_token = tmi_token[6:]
                                            
                                        await channel_user_obj.timeout_user(
                                            token=tmi_token,
                                            moderator_id=self.bot_user_id,
                                            user_id=target_user_obj.id,
                                            duration=duration,
                                            reason="MockBot Custom Command"
                                        )
                                        self.logger.info(f"Successfully timed out {target_user} for {duration}s via Custom Command!")
                                except Exception as e:
                                    self.logger.error(f"Failed to execute moderation action: {e}")
                            else:
                                self.logger.warning(f"{message.author.name} attempted to use a moderation custom command without permissions.")

                        # Send the custom command response
                        channel_obj = self.get_channel(channel_name)
                        if channel_obj and generated_response:
                            await channel_obj.send(generated_response)
                            self.my_logger.log_message(channel_name, self.nick, generated_response, is_bot_message=True)
                            
                        return # Exit early! Don't process it as a regular message or core command
            except Exception as e:
                self.logger.error(f"Error processing custom command {cmd_name} in {channel_name}: {e}")

        # Handle any core bot commands in the message.
        await self.handle_commands(message)

        # Add user's message to the queue for background bulk-insertion
        try:
            self.message_queue.append((
                message.id,
                message.content,
                message.author.name,
                message.timestamp.isoformat(), # Store timestamp as ISO string
                channel_name,
                0, # Not a bot response
                len(message.content),
                0 # Not processed for TTS by default
            ))
        except Exception as e:
            self.my_logger.error(f"Failed to queue user message for DB: {e}", channel=channel_name)
            print(f"Error queuing user message for {channel_name}: {e}")

        # Make sure the channel is in our dictionaries
        if channel_name not in self.channel_chat_line_count:
            self.channel_chat_line_count[channel_name] = 0
        self.channel_chat_line_count[channel_name] += 1
        
        # Calculate the elapsed time since the last message in the current channel.
        elapsed_time = time.time() - self.channel_last_message_time.get(channel_name, 0)

        # Determine if a message should be sent based on the chat_mode, lines_between and time_between
        should_send_message = False
        
        # Check independent random chance first
        if random_chance > 0.0:
            import random
            roll = random.uniform(0.0, 100.0)
            if log_dice:
                result_str = '[bright_yellow]Triggered![/]' if roll <= random_chance else '[dim]Miss[/]'
                out(
                    f"[cyan]\\[{channel_name}][/] Dice roll: {roll:.3f}% [dim]vs {random_chance}%[/] → {result_str}",
                    channel=channel_name
                )
            if roll <= random_chance:
                should_send_message = True
                
        # Fallback to lines/time checks if random didn't trigger
        if not should_send_message:
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
                response = self.generate_message(channel_name)

                # If a response was generated.
                if response:
                    try:
                        channel_obj = self.get_channel(channel_name)
                        if not channel_obj:
                            self.logger.error(f"Could not find channel object for {channel_name}")
                            return

                        # Prepare TTS-related variables if TTS is enabled
                        original_message_id = message.id # ID of the original message that triggered this response
                        voice_preset_for_tts = None
                        original_timestamp_str = None
                        tts_delay_enabled = False

                        if self.enable_tts and tts_enabled:
                            # Check if TTS delay mode is enabled for this channel
                            tts_delay_enabled = self.get_tts_delay_setting(channel_name)
                            
                            # Format the timestamp of the original message in ISO format
                            if isinstance(message.timestamp, datetime):
                                original_timestamp_str = message.timestamp.isoformat()
                            elif isinstance(message.timestamp, str):
                                original_timestamp_str = message.timestamp
                            else: # Fallback
                                self.logger.warning(f"Unexpected timestamp type for original message {message.id}: {type(message.timestamp)}. Using current time (ISO) for TTS log.")
                                original_timestamp_str = datetime.now().isoformat()

                            # Get voice preset for the channel
                            voice_preset_for_tts = self.get_channel_voice_preset(channel_name)
                            if not voice_preset_for_tts:
                                voice_preset_for_tts = 'v2/en_speaker_5' 
                                self.logger.info(f"Using default voice preset '{voice_preset_for_tts}' for channel {channel_name} as none was set or found.")
                            else:
                                self.logger.info(f"Using voice preset '{voice_preset_for_tts}' for channel {channel_name}.")

                        # TTS DELAY MODE: Generate TTS first, then send message
                        if self.enable_tts and tts_enabled and tts_delay_enabled:
                            self.logger.info(f"TTS Delay Mode enabled for {channel_name}. Generating TTS before sending message.")
                            
                            try:
                                # Generate TTS synchronously
                                tts_success = await self.generate_tts_sync(
                                    response, channel_name, voice_preset_for_tts, 
                                    original_message_id, original_timestamp_str
                                )
                                
                                if tts_success:
                                    self.logger.info(f"TTS generation successful for {channel_name}. Queuing message now.")
                                else:
                                    self.logger.warning(f"TTS generation failed for {channel_name}. Queuing message anyway.")
                                
                                await self.handle_message_request(channel_name, response)

                            except Exception as e:
                                self.logger.error(f"Error in TTS delay mode for {channel_name}: {e}")
                                # Fallback: queue message even if TTS failed
                                await self.handle_message_request(channel_name, response)

                        # NORMAL MODE: Queue message immediately, then generate TTS
                        else:
                            # Queue the response immediately
                            await self.handle_message_request(channel_name, response)

                            # Generate TTS asynchronously after message is sent
                            if self.enable_tts and tts_enabled:
                                self.logger.debug(f"Calling start_tts_processing for bot response to msg_id: {original_message_id}, channel: {channel_name}, text: {response[:30]}..., timestamp: {original_timestamp_str}, voice: {voice_preset_for_tts}")
                                
                                self.logger.info(f"Starting async TTS processing for bot auto-response. MsgID: {original_message_id}, Channel: {channel_name}, Text: '{response[:30]}...'")
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
                        # Do this for both NORMAL MODE and TTS DELAY MODE
                        self.channel_chat_line_count[channel_name] = 0
                        self.channel_last_message_time[channel_name] = time.time()
                    except Exception as e:
                        # Log any errors that occur when sending the message.
                        self.my_logger.error(f"Failed to send message in {channel_name}: {str(e)}", channel=channel_name)
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
                            channel_clean = channel.lstrip('#')
                            self.my_logger.log_message(channel_clean, self.nick, message, is_bot_message=True)
                            
                            # Generate TTS if enabled for this channel
                            try:
                                # Fetch channel settings to check if TTS is enabled
                                lines_between, time_between, tts_enabled, voice_enabled, _, _ = await self.fetch_channel_settings(channel_clean)
                                
                                if self.enable_tts and tts_enabled:
                                    # Generate a unique message ID for TTS processing
                                    import time
                                    message_id = int(time.time() * 1000)  # Use timestamp as message ID
                                    timestamp_str = datetime.now().isoformat()
                                    
                                    # Get voice preset for this channel
                                    voice_preset_for_tts = self.channel_settings.get(channel_clean, {}).get('voice_preset', 'v2/en_speaker_0')
                                    
                                    self.logger.info(f"Starting TTS processing for generated message. Channel: {channel_clean}, Text: '{message[:30]}...'")
                                    start_tts_processing(
                                        input_text=message,
                                        channel_name=channel_clean,
                                        message_id=message_id,
                                        timestamp_str=timestamp_str,
                                        voice_preset_override=voice_preset_for_tts,
                                        db_file=self.db_file
                                    )
                                    self.logger.info("TTS processing initiated for generated message.")
                                    
                            except Exception as tts_error:
                                self.logger.error(f"Error starting TTS for generated message in {channel_clean}: {tts_error}")
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
            INSERT INTO channel_configs (channel_name, tts_enabled, voice_enabled, join_channel, owner, trusted_users, ignored_users, use_general_model, lines_between_messages, time_between_messages, currently_connected, tts_delay_enabled)
            SELECT ?, 0, 0, 1, ?, '', '', 1, 100, 0, 0, 0
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
    
    # Get channels to join from database
    channels_str_list = fetch_initial_channels(db_file)
    if not channels_str_list:
        print("⚠️ No auto-join channels found in database.")
        channels_str = ""
    else:
        channels_str = ",".join(channels_str_list)
    
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
