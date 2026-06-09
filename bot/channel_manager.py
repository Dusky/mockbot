import asyncio
import time
import logging

from bot.colors import YELLOW, RED, GREEN, RESET


class ChannelManager:
    def __init__(self, bot, db, logger, my_logger, initial_channels: list):
        self._bot = bot           # twitchio Bot instance (for join_channels, part_channels, get_channel, nick)
        self.db = db
        self.logger = logger      # logging.Logger
        self.my_logger = my_logger  # Logger (TUI)
        self._joined_channels: set = set()
        self.channel_settings: dict = {}
        self.channel_chat_line_count: dict = {ch: 0 for ch in initial_channels}
        self.channel_last_message_time: dict = {ch: time.time() for ch in initial_channels}

    # ── Channel settings ──────────────────────────────────────────────────────

    def load_channel_settings(self):
        self.channel_settings = self.db.load_channel_settings_sync()

    def ensure_channel_configs(self):
        """Make sure all channels have config entries in the database with proper defaults."""
        with self.db.connect_sync() as conn:
            c = conn.cursor()
            for channel in self._bot.channels:
                clean_channel = channel.lstrip('#')
                c.execute("SELECT 1 FROM channel_configs WHERE channel_name = ?", (clean_channel,))
                if not c.fetchone():
                    self.logger.info(f"Creating config for channel: {clean_channel}")
                    c.execute(
                        "INSERT INTO channel_configs "
                        "(channel_name, tts_enabled, voice_enabled, join_channel, owner, "
                        "trusted_users, ignored_users, use_general_model, lines_between_messages, "
                        "time_between_messages, currently_connected, tts_delay_enabled, tts_reward) "
                        "VALUES (?, 0, 1, 1, ?, '', '', 1, 50, 15, 0, 0, '')",
                        (clean_channel, clean_channel),
                    )
            conn.commit()
        self.load_channel_settings()

    async def fetch_channel_settings(self, channel_name):
        try:
            async with self.db.connect_async() as conn:
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
            self.logger.info(f"SQLite error in fetch_channel_settings: {e}")
            return 0, 0, False, False, 0.0, False

    async def add_trusted_user(self, channel_name, username):
        """Add a user to the trusted users list for a channel."""
        try:
            # Remove # prefix for database storage
            clean_channel = channel_name.lstrip('#')

            with self.db.connect_sync() as conn:
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

                    self.logger.info(f"Added {username} to trusted users for {channel_name}")
                    return True
                else:
                    self.logger.info(f"Channel {channel_name} not found in database")
                    return False
        except Exception as e:
            self.logger.info(f"Error adding trusted user: {e}")
            return False

    # ── Join / leave ──────────────────────────────────────────────────────────

    async def join_channel(self, channel_name):
        """Join a channel with proper formatting and error handling."""
        try:
            # Ensure the channel name is properly formatted with # prefix for our tracking
            if not channel_name.startswith('#'):
                channel_name = f'#{channel_name}'

            # TwitchIO join_channels expects channel names WITHOUT # prefix
            # The library will strip # internally if present
            clean_name = channel_name.lstrip('#')

            # Join the channel
            try:
                # The actual join operation
                await self._bot.join_channels([clean_name])
                join_success = True

                # Verify that the join was successful by checking connection
                channel_obj = self._bot.get_channel(clean_name)
                if not channel_obj:
                    self.logger.info(f"{YELLOW}Warning: Could not verify channel object for {clean_name} after joining{RESET}")

            except Exception as join_error:
                join_success = False
                self.logger.info(f"{RED}Error in join_channels operation: {join_error}{RESET}")
                raise

            if join_success:
                # Update tracking in multiple places to ensure consistency

                # 1. Add to our tracking set with # prefix
                self._joined_channels.add(channel_name)

                # 2. Make sure it's in self._bot.channels list (also with # prefix)
                if channel_name not in self._bot.channels:
                    self._bot.channels.append(channel_name)

                # Initialize timers for new channels so they don't instant-fire
                if channel_name not in self.channel_last_message_time:
                    self.channel_last_message_time[channel_name] = time.time()

                # 3. Update database to mark channel as connected
                try:
                    async with self.db.connect_async() as conn:
                        c = await conn.cursor()

                        # First check if channel exists in channel_configs
                        await c.execute("SELECT 1 FROM channel_configs WHERE channel_name = ?", (clean_name,))
                        if not await c.fetchone():
                            # Create entry if it doesn't exist
                            self.logger.info(f"{YELLOW}Creating new channel config for {clean_name}{RESET}")
                            await c.execute('''
                                INSERT INTO channel_configs
                                (channel_name, tts_enabled, voice_enabled, join_channel, owner,
                                trusted_users, ignored_users, use_general_model, lines_between_messages, time_between_messages, currently_connected, tts_delay_enabled, tts_reward)
                                VALUES (?, 0, 1, 1, ?, '', '', 1, 50, 15, 1, 0, '')
                            ''', (clean_name, clean_name))
                        else:
                            # Update existing entry - mark as joined
                            await c.execute(
                                "UPDATE channel_configs SET join_channel = 1, currently_connected = 1 WHERE channel_name = ?",
                                (clean_name,)
                            )

                        await conn.commit()

                    # Force an immediate heartbeat update to sync the joined channel status
                    self._bot.update_heartbeat_file()

                except Exception as db_error:
                    self.logger.info(f"{RED}Database update error for channel {clean_name}: {db_error}{RESET}")

                return True
            else:
                self.logger.info(f"{RED}❌ Failed to join channel: {channel_name}{RESET}")
                return False

        except Exception as e:
            self.logger.info(f"{RED}❌ Failed to join channel {channel_name}: {e}{RESET}")
            return False

    async def leave_channel(self, channel_name):
        """Leave a channel with proper cleanup."""
        try:
            # Ensure the channel name is properly formatted with # prefix for our tracking
            if not channel_name.startswith('#'):
                channel_name = f'#{channel_name}'

            # TwitchIO part_channels expects channel names WITHOUT # prefix
            clean_name = channel_name.lstrip('#')

            self.logger.info(f"{YELLOW}Attempting to leave channel: {channel_name} (clean: {clean_name}){RESET}")

            # Mark as disconnected in the database first
            try:
                async with self.db.connect_async() as conn:
                    c = await conn.cursor()
                    await c.execute(
                        "UPDATE channel_configs SET currently_connected = 0 WHERE channel_name = ?",
                        (clean_name,)
                    )
                    await conn.commit()
                self.logger.info(f"{YELLOW}Marked {clean_name} as disconnected in database{RESET}")
            except Exception as db_error:
                self.logger.info(f"{RED}Database error when leaving {clean_name}: {db_error}{RESET}")

            # Actually leave the channel
            try:
                # This is the TwitchIO API call to leave a channel
                await self._bot.part_channels([clean_name])

                # Remove from joined channel tracking
                if channel_name in self._joined_channels:
                    self._joined_channels.remove(channel_name)

                # Force an immediate heartbeat update to sync the joined channel status
                self._bot.update_heartbeat_file()

                self.logger.info(f"{GREEN}✅ Successfully left channel: {channel_name}{RESET}")
                return True
            except Exception as e:
                self.logger.info(f"{RED}Failed to leave channel {channel_name}: {e}{RESET}")
                return False

        except Exception as e:
            self.logger.info(f"{RED}Exception when leaving channel {channel_name}: {e}{RESET}")
            return False

    # ── Bulk join / periodic check ────────────────────────────────────────────

    async def check_and_join_channels(self, silent=False):
        """Join all channels marked for joining in the database.

        Args:
            silent (bool): If True, suppresses routine logging for periodic checks
        """
        try:
            from bot.config import config as _config

            # Get channels from database
            channels_to_join = await self.db.get_all_join_channels()

            if not silent:
                self.logger.info(f"{YELLOW}Found {len(channels_to_join)} channels to join from database{RESET}")

            # If no channels found in database, check config file
            if not channels_to_join and _config.channels:
                config_channels = _config.channels
                channels_to_join = [ch.strip() for ch in config_channels if ch.strip()]
                if not silent:
                    self.logger.info(f"{YELLOW}No channels in database, using {len(channels_to_join)} from config file{RESET}")

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
                            self.logger.info(f"{GREEN}Already joined channel: {channel_name}{RESET}")
                        join_success += 1
                        continue

                    # Attempt to join
                    success = await self.join_channel(channel_name)

                    if success:
                        join_success += 1
                        new_joins += 1
                        self.logger.info(f"{GREEN}✓ Joined {channel_name}{RESET}")
                    else:
                        join_failure += 1
                        self.logger.info(f"{RED}✗ Failed {channel_name}{RESET}")

                except Exception as e:
                    join_failure += 1
                    self.logger.info(f"{RED}Error joining channel {channel}: {str(e)}{RESET}")

            # Summary - only show if there's activity or not silent
            if not silent or new_joins > 0 or join_failure > 0:
                self.logger.info(f"{GREEN}Channel joining complete: {join_success} succeeded, {join_failure} failed{RESET}")

        except Exception as e:
            self.logger.info(f"{RED}Error in check_and_join_channels: {str(e)}{RESET}")

    async def setup_periodic_channel_check(self, interval=300):  # 5 minutes
        """Set up a periodic task to check for new channels."""
        async def check_periodically():
            while True:
                await asyncio.sleep(interval)
                await self.check_and_join_channels(silent=True)  # Periodic check, silent mode

        # Start the periodic task
        self._bot.loop.create_task(check_periodically())

    # ── Messaging ─────────────────────────────────────────────────────────────

    async def send_message_to_channel(self, channel_name, message, log_to_tui=False):
        """Send a message to a specific channel."""
        channel_name = channel_name.lower()
        # Check if channel starts with # (required for Twitch)
        if not channel_name.startswith('#'):
            channel_name = f'#{channel_name}'

        # Make sure we're in the channel
        if channel_name not in self._joined_channels:
            self.logger.info(f"Joining channel {channel_name} before sending message...")
            await self.join_channel(channel_name)

        # Send the message
        channel = self._bot.get_channel(channel_name.lstrip('#'))  # TwitchIO gets channels without #
        if channel:
            await channel.send(message)
            self.logger.info(f"Message sent to {channel_name}: {message}")
            if log_to_tui:
                try:
                    self.my_logger.log_message(channel.name, self._bot.nick, message, is_bot_message=True)
                except Exception:
                    pass
            return True
        else:
            self.logger.info(f"Failed to find channel {channel_name}")
            return False

    # ── Status display ────────────────────────────────────────────────────────

    async def print_channel_status(self, channel_filter=None, out_func=None):
        """Print a status table showing all channels (or a specific channel) and their configurations."""
        out = out_func or self.my_logger.print_message
        try:
            async with self.db.connect_async() as conn:
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
                    owner_display = f"[{self.my_logger.color_manager.get_user_color(owner)}]{owner}[/]" if owner else "None"

                    # Format trusted users with colors
                    if trusted and trusted.strip():
                        trusted_display = ", ".join(
                            f"[{self.my_logger.color_manager.get_user_color(user.strip())}]{user.strip()}[/]"
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

                    channel_display = f"[{self.my_logger.color_manager.get_channel_color(channel)}]#{channel}[/]"

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
