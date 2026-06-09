from twitchio.ext import commands
import twitchio.ext.pubsub as pubsub
import logging
import markovify
import asyncio
import time
import sqlite3
from datetime import datetime, timezone
import os
import json
import threading
from tabulate import tabulate
from bot.logger import Logger
from bot.commands import mockbot_command
from bot.config import config
from bot.colors import YELLOW, RED, GREEN, PURPLE, RESET
from bot.tts import process_text, start_tts_processing
from bot.utils import LRUCache, convert_size
from bot.connection import ConnectionStateManager


logger = Logger()
logger.setup_logger()

# Create a handler for writing to the log file
file_handler = logging.FileHandler("app.log")
file_handler.setLevel(logging.DEBUG)

# Try to extract the channels - with error handling
try:
    channels = config.channels
except Exception as e:
    logger.logger.info(f"{RED}Error reading channels from config: {e}{RESET}")
    channels = []


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
        
        self.prefix = prefix
        self.my_logger = Logger()
        self.my_logger.setup_logger()
        self.owner = None
        self.channels = initial_channels
        self.last_global_message_time = datetime.now()
        self.is_sleeping = False
        self.logger = logging.getLogger("bot")
        self.logger.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
        handler = logging.FileHandler(filename="logs/mockbot.log", encoding="utf-8", mode="w")
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.message_queue = [] # Queue for async DB bulk inserts
        self.db_flush_task = None
        self.db_file = db_file
        from bot.database import Database
        self.db = Database(db_file)
        from bot import tts as _tts_mod
        _tts_mod.init_tts_db(self.db)
        from bot import overlay as _overlay_mod
        _overlay_mod.init_overlay_db(self.db)
        from bot.channel_manager import ChannelManager
        self.channel_manager = ChannelManager(self, self.db, self.logger, self.my_logger, initial_channels)
        self.channel_manager.load_channel_settings()  # Populate channel settings

        self.rebuild_cache = rebuild_cache
        from bot.brain import MarkovBrain
        self.brain = MarkovBrain(self.db, self.logger, self.my_logger, nick, rebuild_cache)
        self.brain.cache_build_times = self.brain.load_last_cache_build_times()
        self.brain.load_text_and_build_model()
        if self.rebuild_cache:
            self.brain.update_model_periodically()
        
        self.enable_tts = enable_tts
        if self.enable_tts:
            from bot import tts
            tts.initialize_tts()

        self.verbose_heartbeat_log = config.getboolean('settings', 'verbose_heartbeat_log', fallback=False)
        self.start_time = time.time()

        self.connection_manager = ConnectionStateManager(self)
        self.pubsub_pool = pubsub.PubSubPool(self)
        self._channel_ids = {}
        self.socketio_emitter = None

        self.message_request_check = None
        self.live_streamers = set()

        from bot.custom_commands import CustomCommandHandler
        self.custom_cmd_handler = CustomCommandHandler(self.db, self.logger, self.my_logger, self)

    # ---------------------------------------------------------------------------
    # Proxy properties so existing callers continue to work via self.*
    # ---------------------------------------------------------------------------
    @property
    def general_model(self):
        return self.brain.general_model

    @general_model.setter
    def general_model(self, value):
        self.brain.general_model = value

    @property
    def models(self):
        return self.brain.models

    @property
    def cache_build_times(self):
        return self.brain.cache_build_times

    @cache_build_times.setter
    def cache_build_times(self, value):
        self.brain.cache_build_times = value

    # ---------------------------------------------------------------------------

    # ── ChannelManager proxies ────────────────────────────────────────────────

    @property
    def _joined_channels(self):
        return self.channel_manager._joined_channels

    @_joined_channels.setter
    def _joined_channels(self, value):
        self.channel_manager._joined_channels = value

    @property
    def channel_settings(self):
        return self.channel_manager.channel_settings

    @property
    def channel_chat_line_count(self):
        return self.channel_manager.channel_chat_line_count

    @property
    def channel_last_message_time(self):
        return self.channel_manager.channel_last_message_time

    async def join_channel(self, channel_name):
        return await self.channel_manager.join_channel(channel_name)

    async def leave_channel(self, channel_name):
        return await self.channel_manager.leave_channel(channel_name)

    async def send_message_to_channel(self, channel_name, message, log_to_tui=False):
        return await self.channel_manager.send_message_to_channel(channel_name, message, log_to_tui)

    async def print_channel_status(self, channel_filter=None, out_func=None):
        return await self.channel_manager.print_channel_status(channel_filter, out_func)

    async def fetch_channel_settings(self, channel_name):
        return await self.channel_manager.fetch_channel_settings(channel_name)

    async def add_trusted_user(self, channel_name, username):
        return await self.channel_manager.add_trusted_user(channel_name, username)

    def ensure_channel_configs(self):
        return self.channel_manager.ensure_channel_configs()

    async def check_and_join_channels(self, silent=False):
        return await self.channel_manager.check_and_join_channels(silent)

    async def setup_periodic_channel_check(self, interval=300):
        return await self.channel_manager.setup_periodic_channel_check(interval)

    def load_channel_settings(self):
        return self.channel_manager.load_channel_settings()

    # ── End ChannelManager proxies ────────────────────────────────────────────

    async def print_brain_status(self, channel_filter=None, out_func=None):
        """Delegate to MarkovBrain.print_brain_status."""
        return await self.brain.print_brain_status(channel_filter=channel_filter, out_func=out_func)

    def generate_message(self, channel_name):
        """Delegate to MarkovBrain.generate_message."""
        return self.brain.generate_message(channel_name)

    def load_text_and_build_model(self, create_individual_caches=False, target_channel=None):
        """Delegate to MarkovBrain.load_text_and_build_model (called from tui.py compile).
        Syncs Bot.rebuild_cache to the brain before delegating so tui.py's flag override works."""
        self.brain.rebuild_cache = self.rebuild_cache
        return self.brain.load_text_and_build_model(create_individual_caches=create_individual_caches, target_channel=target_channel)

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
            
        elif setting == "var":
            from bot.commands import mockbot_var
            if len(args) < 2:
                await ctx.send("Usage: !var <set|add|get> <var_name> [value]")
                return
            await mockbot_var(self, ctx, args[0], args[1], value=" ".join(args[2:]) if len(args) > 2 else "")
            
        else:
            # Call the original mockbot_command for other settings
            await mockbot_command(self, ctx, setting, args[0] if args else None, enable_tts=self.enable_tts)

    def get_channel_voice_preset(self, channel_name):
        """Fetch the voice_preset for a given channel from the database."""
        clean = channel_name.lstrip('#')
        cfg = self.db.get_tts_config_sync(clean)
        preset = cfg.get("voice_preset")
        if preset:
            self.logger.debug(f"Voice preset for channel {clean}: {preset}")
        else:
            self.logger.debug(f"No specific voice preset found for channel {clean}, using default.")
        return preset or None

    def get_tts_delay_setting(self, channel_name):
        """Get TTS delay setting for a channel"""
        try:
            clean_channel_name = channel_name.lstrip('#')
            cfg = self.db.get_tts_config_sync(clean_channel_name)
            result = [cfg.get("tts_delay_enabled", False)]
            enabled = bool(result[0]) if result else False
            if enabled:
                self.logger.debug(f"TTS delay enabled for channel {clean_channel_name}")
            else:
                self.logger.debug(f"TTS delay disabled or not set for channel {clean_channel_name}")
            return enabled
        except Exception as e:
            self.logger.error(f"Error in get_tts_delay_setting for {channel_name}: {e}")
            return False

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
                    from bot.tts import process_text_thread
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

    async def event_raw_data(self, data: str):
        """Intercept raw IRC data to catch Twitch dropping messages or sending notices."""
        if "NOTICE" in data:
            self.logger.info(f"{RED}Twitch IRC NOTICE: {data.strip()}{RESET}")
            try:
                # Format: @msg-id=... :tmi.twitch.tv NOTICE #channel :Message
                parts = data.split(" :", 1)
                if len(parts) > 1:
                    notice_msg = parts[1].strip()
                    # Extract channel if present
                    channel = "global"
                    if " NOTICE #" in data:
                        channel = data.split(" NOTICE #")[1].split(" :")[0].strip()
                    
                    # Format user-friendly actionable notices
                    display_msg = f"[bold red]NOTICE: {notice_msg}[/bold red]"
                    
                    if "follower-only" in notice_msg.lower():
                        display_msg = f"[bold yellow]⚠️ Cannot Send Message[/bold yellow]: #{channel} is in Follower-Only mode!\n[cyan]Action Required:[/cyan] Log into the bot's Twitch account and go to https://twitch.tv/{channel} to follow them, or type '/mod {self.nick}' from the broadcaster account."
                    elif "verified phone number" in notice_msg.lower():
                        display_msg = f"[bold yellow]⚠️ Cannot Send Message[/bold yellow]: #{channel} requires a verified phone number!\n[cyan]Action Required:[/cyan] Go to https://www.twitch.tv/settings/security to verify the bot's phone number, or type '/mod {self.nick}' from the broadcaster account."
                    elif "verified email" in notice_msg.lower():
                        display_msg = f"[bold yellow]⚠️ Cannot Send Message[/bold yellow]: #{channel} requires a verified email address!\n[cyan]Action Required:[/cyan] Go to https://www.twitch.tv/settings/security to verify the bot's email, or type '/mod {self.nick}' from the broadcaster account."
                    elif "subscriber" in notice_msg.lower():
                        display_msg = f"[bold yellow]⚠️ Cannot Send Message[/bold yellow]: #{channel} is in Subscriber-Only mode!\n[cyan]Action Required:[/cyan] You must subscribe to the channel, or type '/mod {self.nick}' from the broadcaster account."
                    elif "banned" in notice_msg.lower():
                        display_msg = f"[bold red]🚫 BANNED[/bold red]: The bot is banned from talking in #{channel}."

                    self.my_logger.log_message(channel, "TwitchSystem", display_msg, is_bot_message=True)
            except Exception:
                pass

    async def event_ready(self):
        """Handle the bot ready event."""
        # Use verbose flag for detailed output        
        # Use verbose flag for detailed output
        verbose = os.environ.get('VERBOSE', '').lower() in ('true', '1', 'yes')
        
        # Step 1: Initialize channel configs in the database
        try:
            if verbose:
                self.logger.info(f"{YELLOW}Step 1: Initializing channel configurations...{RESET}")
            self.ensure_channel_configs()
            if verbose:
                self.logger.info(f"{GREEN}✅ Channel configs initialized{RESET}")
        except Exception as e:
            self.logger.info(f"{RED}❌ Error initializing channel configs: {e}{RESET}")
        
        # Step 2: Set start time for uptime tracking
        self._start_time = time.time()
        
        # Step 2.5: Cache Bot's Twitch User ID for API calls (like Timeout)
        try:
            bot_users = await self.fetch_users(names=[self.nick])
            if bot_users:
                self.bot_user_id = bot_users[0].id
                if verbose:
                    self.logger.info(f"{GREEN}✅ Bot User ID Cached: {self.bot_user_id}{RESET}")
        except Exception as e:
            self.logger.info(f"{RED}❌ Failed to cache Bot User ID: {e}{RESET}")
        
        # Step 3: Process channels from config file
        try:
            if verbose:
                self.logger.info(f"{YELLOW}Step 3: Processing channels from config file...{RESET}")
            if config.channels:
                config_channels = [ch.strip() for ch in config.channels if ch.strip()]
                
                if verbose:
                    self.logger.info(f"{YELLOW}Found {len(config_channels)} channels in config file{RESET}")
                
                # Make sure each config channel has a database entry
                for channel in config_channels:
                    clean_name = channel.lstrip('#')
                    # Update channel config to ensure it's set to be joined
                    try:
                        with self.db.connect_sync() as conn:
                            c = conn.cursor()
                            c.execute("SELECT 1 FROM channel_configs WHERE channel_name = ?", (clean_name,))

                            if not c.fetchone():
                                # Create new entry
                                if verbose:
                                    self.logger.info(f"{YELLOW}Creating config for config file channel: {clean_name}{RESET}")
                                c.execute('''
                                    INSERT INTO channel_configs
                                    (channel_name, tts_enabled, voice_enabled, join_channel, owner,
                                    trusted_users, ignored_users, use_general_model, lines_between_messages, time_between_messages, currently_connected, tts_delay_enabled, pubsub_bits, pubsub_points, tts_reward)
                                    VALUES (?, 0, 1, 1, ?, '', '', 1, 50, 15, 0, 0, 0, 0, '')
                                ''', (clean_name, clean_name))
                            else:
                                # Update existing entry to make sure join_channel is enabled
                                c.execute("UPDATE channel_configs SET join_channel = 1 WHERE channel_name = ?", (clean_name,))

                            conn.commit()
                    except Exception as db_error:
                        self.logger.info(f"{RED}Error updating channel config for {clean_name}: {db_error}{RESET}")
            elif verbose:
                self.logger.info(f"{YELLOW}No channels found in config file{RESET}")
                
            if verbose:
                self.logger.info(f"{GREEN}✅ Config file channels processed{RESET}")
        except Exception as e:
            self.logger.info(f"{RED}❌ Error processing config file channels: {e}{RESET}")
        
        # Step 4: Join all configured channels from database
        try:
            if verbose:
                self.logger.info(f"{YELLOW}Step 4: Joining all configured channels...{RESET}")
            await self.check_and_join_channels(silent=False)  # Initial join, show full output
            if verbose:
                self.logger.info(f"{GREEN}✅ Channel joining completed{RESET}")
        except Exception as e:
            self.logger.info(f"{RED}❌ Error joining channels: {e}{RESET}")
        
        # Step 5: Start periodic channel checking
        try:
            if verbose:
                self.logger.info(f"{YELLOW}Step 5: Setting up periodic channel check...{RESET}")
            await self.setup_periodic_channel_check()
            if verbose:
                self.logger.info(f"{GREEN}✅ Periodic checking started{RESET}")
        except Exception as e:
            self.logger.info(f"{RED}❌ Error setting up periodic channel check: {e}{RESET}")
        
        # Step 6: Print status table
        try:
            if verbose:
                self.logger.info(f"{YELLOW}Step 6: Printing status table...{RESET}")
            await self.print_channel_status()
            if verbose:
                self.logger.info(f"{GREEN}✅ Status printed{RESET}")
        except Exception as e:
            self.logger.info(f"{RED}❌ Error printing channel status: {e}{RESET}")
        
        # Step 7: Create PID file
        try:
            with open("bot.pid", "w") as f:
                f.write(str(os.getpid()))
            if verbose:
                self.logger.info(f"{GREEN}✅ Created PID file with PID: {os.getpid()}{RESET}")
        except Exception as e:
            self.logger.info(f"{RED}❌ Error creating PID file: {e}{RESET}")
        
        # Step 8: Setup heartbeat
        try:
            if verbose:
                self.logger.info(f"{YELLOW}Step 8: Setting up heartbeat...{RESET}")
            from bot.tasks import heartbeat as _heartbeat_mod
            _heartbeat_mod.update_heartbeat_file(self)
            self.loop.create_task(_heartbeat_mod.heartbeat_loop(self))
            if verbose:
                self.logger.info(f"{GREEN}✅ Heartbeat task started{RESET}")
        except Exception as e:
            self.logger.info(f"{RED}❌ Error setting up heartbeat: {e}{RESET}")

        # Step 9: Start background DB writer & Timed Message Loop
        try:
            if verbose:
                self.logger.info(f"{YELLOW}Step 9: Starting background DB writer, Timed Message Loop, and Sleep Monitor...{RESET}")
            from bot.tasks import db_writer, timed_messages, sleep_monitor, message_requests, live_stream_monitor
            self.db_flush_task = self.loop.create_task(db_writer.background_db_writer(self))
            self.timed_msg_task = self.loop.create_task(timed_messages.timed_message_loop(self))
            self.sleep_monitor_task = self.loop.create_task(sleep_monitor.sleep_monitor_loop(self))
            self.message_request_check = self.loop.create_task(message_requests.message_request_checker(self))
            self.live_stream_monitor_task = self.loop.create_task(live_stream_monitor.live_stream_monitor_loop(self))
            if verbose:
                self.logger.info(f"{GREEN}✅ Background loops started{RESET}")
        except Exception as e:
            self.logger.info(f"{RED}❌ Error starting background loops: {e}{RESET}")
            
        # Step 10: Setup PubSub
        try:
            if verbose:
                self.logger.info(f"{YELLOW}Step 10: Setting up PubSub for Bits & Channel Points...{RESET}")
            
            tmi_token = config.get("auth", "tmi_token")
            if tmi_token.startswith("oauth:"):
                tmi_token = tmi_token[6:]
                
            clean_channels = [c.lstrip('#') for c in self._joined_channels]
            users = await self.fetch_users(names=clean_channels)
            
            topics = []
            try:
                async with self.db.connect_async() as conn:
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
                self.logger.info(f"Failed to load pubsub configs: {e}")
                
            if topics:
                await self.pubsub_pool.subscribe_topics(topics)
                if verbose:
                    self.logger.info(f"{GREEN}✅ Subscribed to PubSub topics for {len(users)} channels{RESET}")
        except Exception as e:
            self.logger.info(f"{RED}❌ Error setting up PubSub: {e}{RESET}")

        # Final verification
        if verbose:
            self.logger.info(f"{GREEN}Bot initialization complete!{RESET}")
        
        # Extra verification for channels of interest
        for channel in self.channels:
            clean_channel = channel.lstrip('#')
            # Create the properly formatted channel name for _joined_channels check
            formatted_channel = f"#{clean_channel}"
            
            if formatted_channel in self._joined_channels:
                # Update database to mark channel as connected
                try:
                    with self.db.connect_sync() as conn:
                        c = conn.cursor()
                        c.execute(
                            "UPDATE channel_configs SET currently_connected = 1 WHERE channel_name = ?",
                            (clean_channel,)
                        )
                        conn.commit()
                except Exception as e:
                    if verbose:
                        self.logger.info(f"Error updating channel connection status in DB: {e}")
            else:
                # Make sure database shows it's not connected
                try:
                    with self.db.connect_sync() as conn:
                        c = conn.cursor()
                        c.execute(
                            "UPDATE channel_configs SET currently_connected = 0 WHERE channel_name = ?",
                            (clean_channel,)
                        )
                        conn.commit()
                except Exception as e:
                    if verbose:
                        self.logger.info(f"Error updating channel connection status in DB: {e}")

        # Mark connection as successful for reconnection manager
        self.connection_manager.mark_connected()

    async def event_pubsub_bits(self, event: pubsub.PubSubBitsMessage):
        """Handle incoming Bits/Cheers via PubSub."""
        channel_name = self._channel_ids.get(event.channel_id, "unknown")
        user_name = event.user.name if event.user else "Anonymous"
        
        try:
            async with self.db.connect_async() as conn:
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
            async with self.db.connect_async() as conn:
                c = await conn.cursor()
                await c.execute("SELECT pubsub_points, tts_reward, voice_preset FROM channel_configs WHERE channel_name = ?", (channel_name.lstrip('#'),))
                row = await c.fetchone()
                if not row or not row[0]:
                    return  # Points tracking is disabled
                
                tts_reward = row[1]
                voice_preset = row[2]
                
        except Exception as e:
            self.logger.error(f"Failed to check pubsub_points config: {e}")
            return
            
        self.logger.info(f"Channel point redemption: {reward_title} by {user_name} in {channel_name}")
        
        # 1. Check if this is a TTS Reward redemption!
        if tts_reward and tts_reward.lower() == reward_title.lower() and event.input:
            self.logger.info(f"TTS Channel Point Reward triggered by {user_name}: {event.input}")
            import uuid
            fake_msg_id = f"cp_tts_{uuid.uuid4().hex[:8]}"
            timestamp_str = datetime.now().isoformat()
            
            from bot.tts import start_tts_processing
            start_tts_processing(
                input_text=event.input,
                channel_name=channel_name.lstrip('#'),
                db_file=self.db_file,
                message_id=fake_msg_id,
                timestamp_str=timestamp_str,
                voice_preset_override=voice_preset
            )
        
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
            async with self.db.connect_async() as conn:
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
            with self.db.connect_sync() as conn:
                conn.execute("UPDATE channel_configs SET currently_connected = 0")
                conn.commit()
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
            with self.db.connect_sync() as conn:
                conn.execute(
                    "INSERT INTO error_log (timestamp, level, message, source, stack_trace) VALUES (?, ?, ?, ?, ?)",
                    (datetime.now().isoformat(), level, message, 'bot',
                     json.dumps(extra_data) if extra_data else None),
                )
                conn.commit()
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
            self.my_logger.print_message("[bold yellow]Waking up from Smart Sleep Mode![/bold yellow]")
            
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
        if await self.custom_cmd_handler.handle(message, channel_name):
            return

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
            self.logger.info(f"Error queuing user message for {channel_name}: {e}")

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
                self.my_logger.print_message(
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
            with self.db.connect_sync() as conn:
                c = conn.cursor()
                # Check if the general model should be used for the current channel.
                c.execute("SELECT use_general_model FROM channel_configs WHERE channel_name = ?", (channel_name,))
                row = c.fetchone()

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
                                
                                await self.send_message_to_channel(channel_name, response, log_to_tui=True)

                            except Exception as e:
                                self.logger.error(f"Error in TTS delay mode for {channel_name}: {e}")
                                # Fallback: queue message even if TTS failed
                                await self.send_message_to_channel(channel_name, response, log_to_tui=True)

                        # NORMAL MODE: Queue message immediately, then generate TTS
                        else:
                            # Queue the response immediately
                            await self.send_message_to_channel(channel_name, response, log_to_tui=True)

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
                        self.logger.info(f"Error sending message in {channel_name}: {str(e)}")

    async def stop(self):
        try:
            # Disconnect the bot from all channels
            await self.close()
            
            # Remove status files
            for file in ["bot.pid", "bot_heartbeat.json"]:
                if os.path.exists(file):
                    os.remove(file)
                
            # Perform any additional cleanup tasks, such as closing database connections or saving data
            self.logger.info("Bot stopped successfully.")
        except Exception as e:
            self.logger.info(f"Error stopping bot: {e}")

    def update_heartbeat_file(self):
        """Delegation: write current bot status to heartbeat file and database."""
        from bot.tasks.heartbeat import update_heartbeat_file
        update_heartbeat_file(self)

    def is_tts_enabled(self, channel_name):
        """Check if TTS is enabled for a channel"""
        try:
            clean_channel = channel_name.lstrip('#')
            return self.db.get_tts_config_sync(clean_channel).get("tts_enabled", False)
        except Exception as e:
            self.logger.error(f"Error checking TTS status for {channel_name}: {e}")
            return False
            
    async def handle_speak_command(self, ctx):
        """Handle the !speak command with improved TTS processing"""
        channel = ctx.channel.name
        
        try:
            # Get the last message from this channel that wasn't a command
            with self.db.connect_sync() as conn:
                c = conn.cursor()
                c.execute("""
                    SELECT message FROM messages
                    WHERE channel = ? AND NOT message LIKE '!%'
                    ORDER BY timestamp DESC LIMIT 1
                """, (channel,))

                result = c.fetchone()
            
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
                from bot.tts import process_text
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
                
                import asyncio
                def _speak_blocking_wrapper():
                    from bot.tts import process_text_thread
                    import uuid, os
                    from datetime import datetime
                    msg_id = "speak_" + str(uuid.uuid4())[:8]
                    out_dir = f"static/outputs/{channel}"
                    os.makedirs(out_dir, exist_ok=True)
                    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                    f_path = f"{out_dir}/{channel}_{msg_id}_{ts}.wav"
                    audio_file, tts_id = process_text_thread(message_to_speak, channel, full_path=f_path, message_id=msg_id, voice_preset=voice_preset_for_speak, author_name=author_name)
                    return audio_file is not None, audio_file
                
                success, audio_file = await asyncio.to_thread(_speak_blocking_wrapper)
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
                
                if hasattr(self, 'socketio_emitter') and self.socketio_emitter:
                    try:
                        self.socketio_emitter({
                            'event': 'new_tts_entry',
                            'channel': channel,
                            'message_id': getattr(getattr(ctx, 'message', None), 'id', 'unknown'),
                            'tts_url': web_path,
                            'voice': voice_preset_for_speak,
                            'text': message_to_speak
                        })
                    except Exception as e:
                        self.logger.error(f"Failed to emit new_tts_entry event: {e}")
                
                self.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] Sending message to Twitch: Speaking: {message_to_speak[:50]}... (Audio: {web_path})")
                await ctx.send(f"Speaking: {message_to_speak[:50]}... (Audio: {web_path})")
                self.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] Message sent to Twitch.")
                
                # Log the TTS usage in the database for tracking
                try:
                    self.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] Attempting to log !speak TTS to DB. audio_file from process_text: {audio_file}")
                    with self.db.connect_sync() as conn:
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

    @commands.command(name="mystats", aliases=["stats", "mystat"])
    async def my_stats_command(self, ctx):
        """Displays fun stats about the user's contributions to the markov chain corpus."""
        try:
            author = ctx.author.name.lower()
            
            cache_file = os.path.join("cache", "user_model_stats.json")
            if not os.path.exists(cache_file):
                await ctx.send("The model cache hasn't compiled user stats yet! Please wait for the next brain rebuild.")
                return
                
            with open(cache_file, 'r') as f:
                import json
                user_stats = json.load(f)
                
            stats = user_stats.get(author)
            if not stats:
                await ctx.send(f"@{ctx.author.name}, I haven't seen any messages from you in my training data yet! Speak up to feed the brain.")
                return
                
            total_messages = stats.get('messages', 0)
            total_chars = stats.get('chars', 0)
            total_words = stats.get('words', 0)
            
            avg_len = total_chars // total_messages if total_messages > 0 else 0
                
            stats_msg = f"@{ctx.author.name}'s Brain Node Stats 🧠: You've seeded the Markov Chain with {total_messages:,} messages, {total_words:,} words, and {total_chars:,} characters! Avg message length: {avg_len} chars."
            await ctx.send(stats_msg)
        except Exception as e:
            self.logger.error(f"Error in mystats command: {e}")
            await ctx.send("Oops, something went wrong fetching your stats from the model cache.")





def fetch_users(db_file):
    # This function now fetches trusted and ignored users for a specific channel.
    def fetch_users_for_channel(channel_name):
        from bot.database import Database
        trusted_users = []
        ignored_users = []
        try:
            with Database(db_file).connect_sync() as conn:
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
        return trusted_users, ignored_users

    return fetch_users_for_channel


def fetch_initial_channels(db_file):
    from bot.database import Database
    channels = []
    try:
        with Database(db_file).connect_sync() as conn:
            c = conn.cursor()
            c.execute("SELECT channel_name FROM channel_configs WHERE join_channel = 1")
            for row in c.fetchall():
                channels.append(row[0])
    except Exception as e:
        print(f"Error fetching initial channels: {e}")
    return channels

def insert_initial_channels_to_db(db_file, channels):
    """Insert initial channels with default values into the database if not already present,
    setting the owner name to the name of the channel."""
    from bot.database import Database
    with Database(db_file).connect_sync() as conn:
        c = conn.cursor()

        for channel in channels:
            c.execute('''
                INSERT INTO channel_configs (channel_name, tts_enabled, voice_enabled, join_channel, owner, trusted_users, ignored_users, use_general_model, lines_between_messages, time_between_messages, currently_connected, tts_delay_enabled, tts_reward)
                SELECT ?, 0, 0, 1, ?, '', '', 1, 100, 0, 0, 0, ''
                WHERE NOT EXISTS(SELECT 1 FROM channel_configs WHERE channel_name = ?)
            ''', (channel, channel, channel))

        conn.commit()





def setup_bot(db_file, rebuild_cache=False, enable_tts=False):
    token = config.tmi_token
    client_id = config.client_id
    nick = config.nickname
    
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
