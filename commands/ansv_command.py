import configparser
from datetime import datetime

from twitchio.ext import commands
import sqlite3
from tabulate import tabulate
from utils.tts import start_tts_processing

YELLOW = "\x1b[33m" #xterm colors. dunno why tbh
RESET = "\x1b[0m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
PURPLE = "\x1b[35m"

async def ansv_command(self, ctx, setting=None, new_value=None, **kwargs):
    if setting is None:
        await ctx.send("ANSV Bot - A Not So Vanilla Twitch Bot")
        return

    # Fetch channel-specific trusted users and owner
    conn = sqlite3.connect(self.db_file)
    c = conn.cursor()
    c.execute("SELECT voice_enabled, owner, trusted_users FROM channel_configs WHERE channel_name = ?", (ctx.channel.name,))
    channel_config = c.fetchone()
    conn.close()

    if channel_config is None:
        await ctx.send("This channel is not configured.")
        return

    voice_enabled, channel_owner, channel_trusted_users = channel_config
    channel_trusted_users = channel_trusted_users.split(",") if channel_trusted_users else []

    # Check if the user is allowed to use the command
    config = configparser.ConfigParser()
    config.read("settings.conf")
    bot_owner = config.get("auth", "owner")

    if ctx.author.name not in channel_trusted_users and ctx.author.name != channel_owner and ctx.author.name != bot_owner:
        self.my_logger.log_warning(f"Unauthorized attempt to use ansv command by {ctx.author.name}")
        await ctx.send("You do not have permission to use this command.")
        return
    if setting == "speak":
        if not voice_enabled:
            await ctx.send("Voice is not enabled for this channel.")
            return

        # Generate a Markov chain message
        response = self.generate_message(ctx.channel.name)
        if response:
            try:
                await ctx.send(response)
                self.my_logger.log_message(ctx.channel.name, self.nick, response, is_bot_message=True)

                # Trigger TTS processing with database logging (matches web UI behavior)
                conn = sqlite3.connect(self.db_file)
                c = conn.cursor()
                c.execute("SELECT tts_enabled, voice_preset FROM channel_configs WHERE channel_name = ?", (ctx.channel.name,))
                tts_config = c.fetchone()
                conn.close()

                if tts_config and tts_config[0]:
                    voice_preset = tts_config[1] or 'v2/en_speaker_5'
                    synthetic_message_id = f"speak_{ctx.channel.name}_{int(datetime.now().timestamp())}"
                    timestamp_str = datetime.now().isoformat()

                    start_tts_processing(
                        input_text=response,
                        channel_name=ctx.channel.name,
                        db_file=self.db_file,
                        message_id=synthetic_message_id,
                        timestamp_str=timestamp_str,
                        voice_preset_override=voice_preset
                    )
                    print(f"TTS processing started for speak command in {ctx.channel.name}")
                else:
                    print(f"TTS not enabled for channel {ctx.channel.name}")

                # Reset counters to prevent automatic response from triggering immediately after !ansv speak
                import time
                self.channel_chat_line_count[ctx.channel.name] = 0
                self.channel_last_message_time[ctx.channel.name] = time.time()

            except Exception as e:
                self.my_logger.log_error(f"Failed to send message due to: {e}")
        else:
            await ctx.send("Unable to generate a message at this time.")

    elif setting in ["start", "stop"]:
        # Get the bot owner's name from the configuration file
        config = configparser.ConfigParser()
        config.read("settings.conf")
        bot_owner = config.get("auth", "owner")

        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()

        # Determine the target channel
        target_channel = new_value if ctx.author.name == bot_owner and new_value else ctx.channel.name

        # Retrieve the owner of the target channel from the database
        c.execute("SELECT owner FROM channel_configs WHERE channel_name = ?", (target_channel,))
        channel_config = c.fetchone()

        if channel_config is None:
            await ctx.send(f"Channel {target_channel} not found in database.")
            conn.close()
            return

        channel_owner = channel_config[0]

        # Check if the user is the bot owner or the channel owner
        if ctx.author.name != bot_owner and ctx.author.name != channel_owner:
            await ctx.send("You do not have permission to use this command for this channel.")
            conn.close()
            return

        # Update the voice_enabled field based on the command
        voice_enabled_status = 1 if setting == "start" else 0
        c.execute("UPDATE channel_configs SET voice_enabled = ? WHERE channel_name = ?", (voice_enabled_status, target_channel))
        conn.commit()
        conn.close()

        action = "enabled" if setting == "start" else "disabled"
        await ctx.send(f"Voice {action} in {target_channel}.")

    elif setting in ["time", "lines"]:
        # Convert new_value to an integer
        new_value_int = int(new_value)

        # Get the channel name from the context
        channel_name = ctx.channel.name

        # Get the bot owner's name from the configuration file
        config = configparser.ConfigParser()
        config.read("settings.conf")
        bot_owner = config.get("auth", "owner")

        # Update the database with the new value
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()

        # Retrieve the owner and trusted users of the channel from the database
        c.execute("SELECT owner, trusted_users FROM channel_configs WHERE channel_name = ?", (channel_name,))
        channel_config = c.fetchone()

        if channel_config is None:
            await ctx.send(f"Channel {channel_name} not found in database.")
            conn.close()
            return

        channel_owner = channel_config[0]
        trusted_users = channel_config[1].split(",") if channel_config[1] else []

        # Check if the user is the bot owner, channel owner, or a trusted user
        if ctx.author.name != bot_owner and ctx.author.name != channel_owner and ctx.author.name not in trusted_users:
            await ctx.send("You do not have permission to use this command in this channel.")
            conn.close()
            return

        # Check if the channel is in the database
        c.execute("SELECT COUNT(*) FROM channel_configs WHERE channel_name = ?", (channel_name,))
        if c.fetchone()[0] == 0:
            await ctx.send(f"Channel {channel_name} not found in database.")
        else:
            if setting == "time":
                c.execute("UPDATE channel_configs SET time_between_messages = ? WHERE channel_name = ?", (new_value_int, channel_name))
            elif setting == "lines":
                c.execute("UPDATE channel_configs SET lines_between_messages = ? WHERE channel_name = ?", (new_value_int, channel_name))

            conn.commit()
            await ctx.send(f"Set {setting}_between_messages to {new_value_int} for channel {channel_name}.")
            self.my_logger.info(f"{ctx.author.name} set {setting}_between_messages to {new_value_int} for channel {channel_name}.")

        conn.close()

    elif setting == "trust":
        # Get the bot owner's name from the configuration file
        config = configparser.ConfigParser()
        config.read("settings.conf")
        bot_owner = config.get("auth", "owner")

        # Check if the user is the bot owner or the channel owner
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute("SELECT owner FROM channel_configs WHERE channel_name = ?", (ctx.channel.name,))
        channel_owner_row = c.fetchone()
        channel_owner = channel_owner_row[0] if channel_owner_row else None

        if ctx.author.name != bot_owner and ctx.author.name != channel_owner:
            await ctx.send("You do not have permission to use this command.")
            conn.close()
            return

        # Update the trusted_users field for the specific channel in the database
        c.execute("SELECT trusted_users FROM channel_configs WHERE channel_name = ?", (ctx.channel.name,))
        result = c.fetchone()
        existing_trusted_users = result[0].split(",") if result and result[0] else []
        if new_value not in existing_trusted_users:
            updated_trusted_users = existing_trusted_users + [new_value]
            c.execute("UPDATE channel_configs SET trusted_users = ? WHERE channel_name = ?", (",".join(updated_trusted_users), ctx.channel.name))
            conn.commit()
            await ctx.send(f"User {new_value} is now trusted in {ctx.channel.name}.")
        else:
            await ctx.send(f"User {new_value} is already trusted in {ctx.channel.name}.")

        conn.close()

    elif setting == "join":
        if ctx.author.name != self.owner:
            await ctx.send("Unauthorized attempt to use join command.")
            return

        with sqlite3.connect(self.db_file) as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM channel_configs WHERE channel_name = ?", (new_value,))
            if c.fetchone()[0] == 0:
                try:
                    await self.join_channels([new_value])
                    self.channels.append(new_value)
                    # Corrected line below
                    c.execute("INSERT OR REPLACE INTO channel_configs (channel_name, voice_enabled, tts_enabled, join_channel, owner, trusted_users) VALUES (?, 0, 0, 1, ?, '')", (new_value, new_value))
                    conn.commit()
                    await ctx.send(f"Joined {new_value} and added to channels.")
                except Exception as e:
                    await ctx.send(f"Failed to join {new_value}: {str(e)}")
            else:
                await ctx.send(f"Already in {new_value} or it's already in the database.")

    elif setting == "part":
        if ctx.author.name != self.owner:
            await ctx.send("You do not have permission to use this command.")
            return

        with sqlite3.connect(self.db_file) as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM channel_configs WHERE channel_name = ?", (new_value,))
            if c.fetchone()[0] > 0 and new_value in self.channels:
                try:
                    await self.part_channels([new_value])
                    self.channels.remove(new_value)
                    c.execute("UPDATE channel_configs SET join_channel = 0 WHERE channel_name = ?", (new_value,))
                    conn.commit()
                    await ctx.send(f"Left channel: {new_value}")
                except Exception as e:
                    await ctx.send(f"Failed to leave channel: {new_value}. Error: {str(e)}")
            else:
                await ctx.send(f"The bot is not in channel: {new_value} or it's not in the database.")

    elif setting == "voice_preset":
        # Get the bot owner's name from the configuration file
        config = configparser.ConfigParser()
        config.read("settings.conf")
        bot_owner = config.get("auth", "owner")

        # Fetch the channel's owner and trusted users
        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()
        c.execute("SELECT owner, trusted_users FROM channel_configs WHERE channel_name = ?", (ctx.channel.name,))
        result = c.fetchone()

        if result is None:
            await ctx.send("Channel not found in database.")
            return

        channel_owner, trusted_users = result
        trusted_users_list = trusted_users.split(",") if trusted_users else []

        # Check if the user has permission
        if ctx.author.name != bot_owner and ctx.author.name != channel_owner and ctx.author.name not in trusted_users_list:
            await ctx.send("You do not have permission to change the voice preset.")
            return

        # Validate and update the voice preset
        c.execute("SELECT COUNT(*) FROM voice_options WHERE voice_code = ?", (new_value,))
        if c.fetchone()[0] == 0:
            await ctx.send("Invalid voice preset.")
        else:
            c.execute("UPDATE channel_configs SET voice_preset = ? WHERE channel_name = ?", (new_value, ctx.channel.name))
            conn.commit()
            await ctx.send(f"Voice preset updated to {new_value} for channel {ctx.channel.name}.")

    elif setting == "tts":
        # Only the bot owner or channel owner can change TTS status
        config = configparser.ConfigParser()
        config.read("settings.conf")
        bot_owner = config.get("auth", "owner")

        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()

        # Determine the target channel
        target_channel = ctx.channel.name

        # Retrieve the owner and user_id of the target channel from the database
        c.execute("SELECT owner, user_id FROM channel_configs WHERE channel_name = ?", (target_channel,))
        channel_config = c.fetchone()

        if channel_config is None:
            await ctx.send(f"Channel {target_channel} not found in database.")
            conn.close()
            return

        channel_owner = channel_config[0]
        user_id = channel_config[1]

        # Check if the user is the bot owner or the channel owner
        if ctx.author.name != bot_owner and ctx.author.name != channel_owner:
            await ctx.send("You do not have permission to change TTS settings for this channel.")
            conn.close()
            return

        # Check if the channel owner has TTS access (Premium subscription)
        # Bot owner bypasses this check
        if ctx.author.name != bot_owner:
            if user_id is None:
                await ctx.send("TTS is a Premium feature. Please link your Twitch account on the dashboard to upgrade.")
                conn.close()
                return

            # Check user's subscription status
            c.execute("""
                SELECT subscription_tier FROM users WHERE user_id = ?
            """, (user_id,))
            user_result = c.fetchone()

            if not user_result or user_result[0] != 'premium':
                await ctx.send("TTS is a Premium feature. Upgrade to Premium ($2/month) at https://your-domain.com/premium to unlock TTS.")
                conn.close()
                return

        # Determine the new TTS status based on the command
        if new_value.lower() == "on":
            new_tts_status = True
        elif new_value.lower() == "off":
            new_tts_status = False
        else:
            await ctx.send("Invalid command. Use '!ansv tts on' or '!ansv tts off'.")
            conn.close()
            return

        # Update the TTS status in the database
        c.execute("UPDATE channel_configs SET tts_enabled = ? WHERE channel_name = ?", (new_tts_status, target_channel))
        conn.commit()
        conn.close()

        status_text = "enabled" if new_tts_status else "disabled"
        await ctx.send(f"TTS {status_text} for channel {target_channel}.")
    # Build the model
    #self.text_model = markovify.Text(self.text)
