from twitchio.ext import commands
import twitchio.ext.eventsub as eventsub
import logging
import asyncio
import time
from datetime import datetime
import os
import json
from bot.logger import Logger
from bot.commands import mockbot_command
from bot.config import config
from bot.colors import YELLOW, RED, GREEN, RESET
from bot.tts import start_tts_processing
from bot.connection import ConnectionStateManager
from bot.events import EventBus, ConnectionStateChanged, ErrorLogged, TtsGenerated, SendMessageCommand, to_legacy_dict
from bot.trigger_policy import evaluate_trigger
from bot.handlers import startup, eventsub as eventsub_handler, tts as tts_handler, raw_data


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
        self.eventsub_ws = eventsub.EventSubWSClient(self)
        self._channel_ids = {}
        self.socketio_emitter = None

        # Internal event bus — the single integration seam (loop bound at ready).
        self.event_bus = EventBus()
        self.event_bus.subscribe(ConnectionStateChanged, self._forward_to_socketio)
        self.event_bus.subscribe(ErrorLogged, self._forward_to_socketio)
        self.event_bus.subscribe(TtsGenerated, self._forward_to_socketio)
        _tts_mod.init_tts_events(self.event_bus)
        from bot.commands_intake import handle_send_message
        self.event_bus.subscribe(SendMessageCommand, lambda cmd: handle_send_message(self, cmd))

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

    def generate_message(self, channel_name, seed=None):
        """Delegate to MarkovBrain.generate_message."""
        return self.brain.generate_message(channel_name, seed=seed)

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

    async def create_poll_via_api(self, channel_name, title, choices, duration_minutes):
        """Create a Twitch poll via the Helix API.

        Shared by the !poll command and the TUI poll action. The TUI fires this as
        a background task, so outcomes are surfaced through my_logger (TUI-visible)
        rather than only via the return value. Returns True on success, False otherwise.
        """
        clean_channel = channel_name.lstrip('#')
        choices = [c for c in choices if c]
        if not (2 <= len(choices) <= 5):
            self.my_logger.error(f"Poll needs between 2 and 5 choices (got {len(choices)}).", channel=clean_channel)
            return False
        # Twitch requires poll duration to be between 15 and 1800 seconds.
        duration_seconds = max(15, min(1800, int(duration_minutes * 60)))
        try:
            users = await self.fetch_users(names=[clean_channel])
            if not users:
                self.my_logger.error(f"Could not fetch #{clean_channel} from the Twitch API.", channel=clean_channel)
                return False

            token = config.tmi_token
            if token.startswith("oauth:"):
                token = token[6:]

            await users[0].create_poll(
                token=token,
                title=title,
                choices=choices,
                duration=duration_seconds,
                channel_points_voting_enabled=False,
            )
            self.logger.info(f"Poll created in {clean_channel}: '{title}' {choices} ({duration_seconds}s)")
            return True
        except Exception as e:
            self.my_logger.error(f"Failed to create poll in #{clean_channel}: {e}", channel=clean_channel)
            self.logger.error(f"create_poll_via_api error for {clean_channel}: {e}")
            return False

    def get_channel_voice_preset(self, channel_name):
        return tts_handler.get_channel_voice_preset(self, channel_name)

    def get_tts_delay_setting(self, channel_name):
        return tts_handler.get_tts_delay_setting(self, channel_name)

    async def generate_tts_sync(self, text, channel_name, voice_preset, message_id, timestamp_str):
        return await tts_handler.generate_tts_sync(self, text, channel_name, voice_preset, message_id, timestamp_str)

    async def event_raw_data(self, data: str):
        await raw_data.handle_raw_data(self, data)

    async def event_ready(self):
        await startup.run(self)

    async def event_eventsub_notification_cheer(self, event: eventsub.NotificationEvent):
        await eventsub_handler.handle_bits(self, event)

    async def event_eventsub_notification_channel_reward_redeem(self, event: eventsub.NotificationEvent):
        await eventsub_handler.handle_channel_points(self, event)

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

        # Publish to clients via the event bus (compat layer re-emits to socketio).
        self.event_bus.publish(ConnectionStateChanged("disconnected"))

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

    def _forward_to_socketio(self, event):
        """Compat bridge: re-emit bus events to a socketio emitter if one is set."""
        emitter = self.socketio_emitter
        if not emitter:
            return
        payload = to_legacy_dict(event)
        if payload:
            try:
                emitter(payload)
            except Exception as e:
                self.logger.error(f"Failed to forward event to socketio: {e}")

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
            self.event_bus.publish(ErrorLogged(level, message, datetime.now().isoformat()))
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

        # Determine if a message should be sent (random chance, then lines, then time).
        decision = evaluate_trigger(
            random_chance,
            self.channel_chat_line_count[channel_name],
            lines_between,
            elapsed_time,
            time_between,
        )
        if log_dice and decision.roll is not None:
            result_str = '[bright_yellow]Triggered![/]' if decision.reason == 'random' else '[dim]Miss[/]'
            self.my_logger.print_message(
                f"[cyan]\\[{channel_name}][/] Dice roll: {decision.roll:.3f}% [dim]vs {random_chance}%[/] → {result_str}",
                channel=channel_name
            )
        should_send_message = decision.should_respond

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
        return tts_handler.is_tts_enabled(self, channel_name)

    async def handle_speak_command(self, ctx):
        return await tts_handler.handle_speak_command(self, ctx)

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





def setup_bot(db_file, rebuild_cache=False, enable_tts=False):
    token = config.tmi_token
    client_id = config.client_id
    nick = config.nickname
    
    # Get channels to join from database
    from bot.database import Database
    channels_str_list = Database(db_file).get_all_join_channels_sync()
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
