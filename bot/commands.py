import configparser
from datetime import datetime

from twitchio.ext import commands
import aiosqlite
import logging
import sqlite3
from tabulate import tabulate
from bot.tts import start_tts_processing

YELLOW = "\x1b[33m" #xterm colors. dunno why tbh
RESET = "\x1b[0m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
PURPLE = "\x1b[35m"

async def mockbot_command(self, ctx, setting=None, new_value=None, **kwargs):
    if setting is None:
        await ctx.send("Mockbot - A Not So Vanilla Twitch Bot")
        return

    # Fetch channel-specific trusted users and owner
    conn = await aiosqlite.connect(self.db_file)
    c = await conn.cursor()
    await c.execute("SELECT voice_enabled, owner, trusted_users FROM channel_configs WHERE channel_name = ?", (ctx.channel.name,))
    channel_config = await c.fetchone()
    await conn.close()

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
        self.my_logger.log_warning(f"Unauthorized attempt to use ansv command by {ctx.author.name}", channel=ctx.channel.name)
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
                conn = await aiosqlite.connect(self.db_file)
                c = await conn.cursor()
                await c.execute("SELECT tts_enabled, voice_preset FROM channel_configs WHERE channel_name = ?", (ctx.channel.name,))
                tts_config = await c.fetchone()
                await conn.close()

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
                    logging.info(f"TTS processing started for speak command in {ctx.channel.name}")
                else:
                    logging.info(f"TTS not enabled for channel {ctx.channel.name}")

                # Reset counters to prevent automatic response from triggering immediately after !ansv speak
                import time
                self.channel_chat_line_count[ctx.channel.name] = 0
                self.channel_last_message_time[ctx.channel.name] = time.time()

            except Exception as e:
                self.my_logger.log_error(f"Failed to send message due to: {e}", channel=ctx.channel.name)
        else:
            await ctx.send("Unable to generate a message at this time.")

    elif setting in ["start", "stop"]:
        # Get the bot owner's name from the configuration file
        config = configparser.ConfigParser()
        config.read("settings.conf")
        bot_owner = config.get("auth", "owner")

        conn = await aiosqlite.connect(self.db_file)
        c = await conn.cursor()

        # Determine the target channel
        target_channel = new_value if ctx.author.name == bot_owner and new_value else ctx.channel.name

        # Retrieve the owner of the target channel from the database
        await c.execute("SELECT owner FROM channel_configs WHERE channel_name = ?", (target_channel,))
        channel_config = await c.fetchone()

        if channel_config is None:
            await ctx.send(f"Channel {target_channel} not found in database.")
            await conn.close()
            return

        channel_owner = channel_config[0]

        # Check if the user is the bot owner or the channel owner
        if ctx.author.name != bot_owner and ctx.author.name != channel_owner:
            await ctx.send("You do not have permission to use this command for this channel.")
            await conn.close()
            return

        # Update the voice_enabled field based on the command
        voice_enabled_status = 1 if setting == "start" else 0
        await c.execute("UPDATE channel_configs SET voice_enabled = ? WHERE channel_name = ?", (voice_enabled_status, target_channel))
        await conn.commit()
        await conn.close()

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
        conn = await aiosqlite.connect(self.db_file)
        c = await conn.cursor()

        # Retrieve the owner and trusted users of the channel from the database
        await c.execute("SELECT owner, trusted_users FROM channel_configs WHERE channel_name = ?", (channel_name,))
        channel_config = await c.fetchone()

        if channel_config is None:
            await ctx.send(f"Channel {channel_name} not found in database.")
            await conn.close()
            return

        channel_owner = channel_config[0]
        trusted_users = channel_config[1].split(",") if channel_config[1] else []

        # Check if the user is the bot owner, channel owner, or a trusted user
        if ctx.author.name != bot_owner and ctx.author.name != channel_owner and ctx.author.name not in trusted_users:
            await ctx.send("You do not have permission to use this command in this channel.")
            await conn.close()
            return

        # Check if the channel is in the database
        await c.execute("SELECT COUNT(*) FROM channel_configs WHERE channel_name = ?", (channel_name,))
        if (await c.fetchone())[0] == 0:
            await ctx.send(f"Channel {channel_name} not found in database.")
        else:
            if setting == "time":
                await c.execute("UPDATE channel_configs SET time_between_messages = ? WHERE channel_name = ?", (new_value_int, channel_name))
            elif setting == "lines":
                await c.execute("UPDATE channel_configs SET lines_between_messages = ? WHERE channel_name = ?", (new_value_int, channel_name))

            await conn.commit()
            await ctx.send(f"Set {setting}_between_messages to {new_value_int} for channel {channel_name}.")
            self.my_logger.info(f"{ctx.author.name} set {setting}_between_messages to {new_value_int} for channel {channel_name}.")

        await conn.close()

    elif setting == "trust":
        # Get the bot owner's name from the configuration file
        config = configparser.ConfigParser()
        config.read("settings.conf")
        bot_owner = config.get("auth", "owner")

        # Check if the user is the bot owner or the channel owner
        conn = await aiosqlite.connect(self.db_file)
        c = await conn.cursor()
        await c.execute("SELECT owner FROM channel_configs WHERE channel_name = ?", (ctx.channel.name,))
        channel_owner_row = await c.fetchone()
        channel_owner = channel_owner_row[0] if channel_owner_row else None

        if ctx.author.name != bot_owner and ctx.author.name != channel_owner:
            await ctx.send("You do not have permission to use this command.")
            await conn.close()
            return

        # Update the trusted_users field for the specific channel in the database
        await c.execute("SELECT trusted_users FROM channel_configs WHERE channel_name = ?", (ctx.channel.name,))
        result = await c.fetchone()
        existing_trusted_users = result[0].split(",") if result and result[0] else []
        if new_value not in existing_trusted_users:
            updated_trusted_users = existing_trusted_users + [new_value]
            await c.execute("UPDATE channel_configs SET trusted_users = ? WHERE channel_name = ?", (",".join(updated_trusted_users), ctx.channel.name))
            await conn.commit()
            await ctx.send(f"User {new_value} is now trusted in {ctx.channel.name}.")
        else:
            await ctx.send(f"User {new_value} is already trusted in {ctx.channel.name}.")

        await conn.close()

    elif setting == "join":
        if ctx.author.name != self.owner:
            await ctx.send("Unauthorized attempt to use join command.")
            return

        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute("SELECT COUNT(*) FROM channel_configs WHERE channel_name = ?", (new_value,))
            if (await c.fetchone())[0] == 0:
                try:
                    await self.join_channels([new_value])
                    self.channels.append(new_value)
                    # Corrected line below
                    await c.execute("INSERT OR REPLACE INTO channel_configs (channel_name, voice_enabled, tts_enabled, join_channel, owner, trusted_users) VALUES (?, 0, 0, 1, ?, '')", (new_value, new_value))
                    await conn.commit()
                    await ctx.send(f"Joined {new_value} and added to channels.")
                except Exception as e:
                    await ctx.send(f"Failed to join {new_value}: {str(e)}")
            else:
                await ctx.send(f"Already in {new_value} or it's already in the database.")

    elif setting == "part":
        if ctx.author.name != self.owner:
            await ctx.send("You do not have permission to use this command.")
            return

        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute("SELECT COUNT(*) FROM channel_configs WHERE channel_name = ?", (new_value,))
            if (await c.fetchone())[0] > 0 and new_value in self.channels:
                try:
                    await self.part_channels([new_value])
                    self.channels.remove(new_value)
                    await c.execute("UPDATE channel_configs SET join_channel = 0 WHERE channel_name = ?", (new_value,))
                    await conn.commit()
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
        conn = await aiosqlite.connect(self.db_file)
        c = await conn.cursor()
        await c.execute("SELECT owner, trusted_users FROM channel_configs WHERE channel_name = ?", (ctx.channel.name,))
        result = await c.fetchone()

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
        await c.execute("SELECT COUNT(*) FROM voice_options WHERE voice_code = ?", (new_value,))
        if (await c.fetchone())[0] == 0:
            await ctx.send("Invalid voice preset.")
        else:
            await c.execute("UPDATE channel_configs SET voice_preset = ? WHERE channel_name = ?", (new_value, ctx.channel.name))
            await conn.commit()
            await ctx.send(f"Voice preset updated to {new_value} for channel {ctx.channel.name}.")

    elif setting == "tts":
        # Only the bot owner or channel owner can change TTS status
        config = configparser.ConfigParser()
        config.read("settings.conf")
        bot_owner = config.get("auth", "owner")

        conn = await aiosqlite.connect(self.db_file)
        c = await conn.cursor()

        # Determine the target channel
        target_channel = ctx.channel.name

        # Retrieve the owner and user_id of the target channel from the database
        await c.execute("SELECT owner, user_id FROM channel_configs WHERE channel_name = ?", (target_channel,))
        channel_config = await c.fetchone()

        if channel_config is None:
            await ctx.send(f"Channel {target_channel} not found in database.")
            await conn.close()
            return

        channel_owner = channel_config[0]
        user_id = channel_config[1]

        # Check if the user is the bot owner or the channel owner
        if ctx.author.name != bot_owner and ctx.author.name != channel_owner:
            await ctx.send("You do not have permission to change TTS settings for this channel.")
            await conn.close()
            return



        # Determine the new TTS status based on the command
        if new_value.lower() == "on":
            new_tts_status = True
        elif new_value.lower() == "off":
            new_tts_status = False
        else:
            await ctx.send("Invalid command. Use '!mockbot tts on' or '!mockbot tts off'.")
            await conn.close()
            return

        # Update the TTS status in the database
        await c.execute("UPDATE channel_configs SET tts_enabled = ? WHERE channel_name = ?", (new_tts_status, target_channel))
        await conn.commit()
        await conn.close()

        status_text = "enabled" if new_tts_status else "disabled"
        await ctx.send(f"TTS {status_text} for channel {target_channel}.")

    elif setting == "bits":
        # Only the bot owner or channel owner can change Bits status
        config = configparser.ConfigParser()
        config.read("settings.conf")
        bot_owner = config.get("auth", "owner")

        conn = await aiosqlite.connect(self.db_file)
        c = await conn.cursor()

        target_channel = ctx.channel.name

        await c.execute("SELECT owner FROM channel_configs WHERE channel_name = ?", (target_channel,))
        channel_config = await c.fetchone()

        if channel_config is None:
            await ctx.send(f"Channel {target_channel} not found in database.")
            await conn.close()
            return

        channel_owner = channel_config[0]

        if ctx.author.name != bot_owner and ctx.author.name != channel_owner:
            await ctx.send("You do not have permission to change Bits settings for this channel.")
            await conn.close()
            return

        if new_value.lower() == "on":
            new_bits_status = True
        elif new_value.lower() == "off":
            new_bits_status = False
        else:
            await ctx.send("Invalid command. Use '!mockbot bits on' or '!mockbot bits off'.")
            await conn.close()
            return

        await c.execute("UPDATE channel_configs SET pubsub_bits = ? WHERE channel_name = ?", (new_bits_status, target_channel))
        await conn.commit()
        await conn.close()

        status_text = "enabled" if new_bits_status else "disabled"
        await ctx.send(f"Bits tracking {status_text} for channel {target_channel}.")

    elif setting == "points":
        # Only the bot owner or channel owner can change Points status
        config = configparser.ConfigParser()
        config.read("settings.conf")
        bot_owner = config.get("auth", "owner")

        conn = await aiosqlite.connect(self.db_file)
        c = await conn.cursor()

        target_channel = ctx.channel.name

        await c.execute("SELECT owner FROM channel_configs WHERE channel_name = ?", (target_channel,))
        channel_config = await c.fetchone()

        if channel_config is None:
            await ctx.send(f"Channel {target_channel} not found in database.")
            await conn.close()
            return

        channel_owner = channel_config[0]

        if ctx.author.name != bot_owner and ctx.author.name != channel_owner:
            await ctx.send("You do not have permission to change Points settings for this channel.")
            await conn.close()
            return

        if new_value.lower() == "on":
            new_points_status = True
        elif new_value.lower() == "off":
            new_points_status = False
        else:
            await ctx.send("Invalid command. Use '!mockbot points on' or '!mockbot points off'.")
            await conn.close()
            return

        await c.execute("UPDATE channel_configs SET pubsub_points = ? WHERE channel_name = ?", (new_points_status, target_channel))
        await conn.commit()
        await conn.close()

        status_text = "enabled" if new_points_status else "disabled"
        await ctx.send(f"Points tracking {status_text} for channel {target_channel}.")



async def _check_custom_auth(self, ctx):
    """Helper to check if user is admin/trusted for custom command management."""
    config = configparser.ConfigParser()
    config.read("settings.conf")
    bot_owner = config.get("auth", "owner")
    
    if ctx.author.name == bot_owner:
        return True
        
    async with aiosqlite.connect(self.db_file) as conn:
        c = await conn.cursor()
        await c.execute("SELECT owner, trusted_users FROM channel_configs WHERE channel_name = ?", (ctx.channel.name,))
        row = await c.fetchone()
        if not row:
            return False
            
        channel_owner, trusted_users_str = row
        if ctx.author.name == channel_owner:
            return True
            
        trusted_users = trusted_users_str.split(",") if trusted_users_str else []
        if ctx.author.name in trusted_users:
            return True
            
    return False

async def mockbot_addc(self, ctx, cmd_name: str, *, response_template: str):
    if not await _check_custom_auth(self, ctx):
        await ctx.send("You don't have permission to manage custom commands.")
        return
        
    cmd_name = cmd_name.lower()
    if not cmd_name.startswith('!'):
        cmd_name = f"!{cmd_name}"
        
    try:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                "INSERT INTO custom_commands (channel_name, command_name, response_template) VALUES (?, ?, ?)",
                (ctx.channel.name, cmd_name, response_template)
            )
            await conn.commit()
        await ctx.send(f"Command {cmd_name} added successfully!")
    except sqlite3.IntegrityError:
        await ctx.send(f"Command {cmd_name} already exists. Use !editc to change it.")
    except Exception as e:
        await ctx.send(f"Error saving command: {e}")

async def mockbot_editc(self, ctx, cmd_name: str, *, response_template: str):
    if not await _check_custom_auth(self, ctx):
        await ctx.send("You don't have permission to manage custom commands.")
        return
        
    cmd_name = cmd_name.lower()
    if not cmd_name.startswith('!'):
        cmd_name = f"!{cmd_name}"
        
    try:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                "UPDATE custom_commands SET response_template = ? WHERE channel_name = ? AND command_name = ?",
                (response_template, ctx.channel.name, cmd_name)
            )
            if c.rowcount > 0:
                await ctx.send(f"Command {cmd_name} updated successfully!")
            else:
                await ctx.send(f"Command {cmd_name} not found.")
            await conn.commit()
    except Exception as e:
        await ctx.send(f"Error updating command: {e}")

async def mockbot_delc(self, ctx, cmd_name: str):
    if not await _check_custom_auth(self, ctx):
        await ctx.send("You don't have permission to manage custom commands.")
        return
        
    cmd_name = cmd_name.lower()
    if not cmd_name.startswith('!'):
        cmd_name = f"!{cmd_name}"
        
    try:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                "DELETE FROM custom_commands WHERE channel_name = ? AND command_name = ?",
                (ctx.channel.name, cmd_name)
            )
            if c.rowcount > 0:
                await ctx.send(f"Command {cmd_name} deleted successfully!")
            else:
                await ctx.send(f"Command {cmd_name} not found.")
            await conn.commit()
    except Exception as e:
        await ctx.send(f"Error deleting command: {e}")

async def mockbot_grammar(self, ctx, action: str, rule: str, *, text: str = ""):
    if not await _check_custom_auth(self, ctx):
        await ctx.send("You don't have permission to manage custom grammar.")
        return
        
    action = action.lower()
    rule = rule.lower()
    
    if action not in ['add', 'list', 'clear']:
        await ctx.send("Usage: !grammar <add|list|clear> <rule> [text]")
        return
        
    try:
        import json
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                "SELECT options_json FROM custom_grammar WHERE channel_name = ? AND rule_name = ?",
                (ctx.channel.name, rule)
            )
            row = await c.fetchone()
            options = json.loads(row[0]) if row else []
            
            if action == 'add':
                if not text:
                    await ctx.send("Please provide text to add to the rule.")
                    return
                options.append(text)
                options_str = json.dumps(options)
                
                if row:
                    await c.execute(
                        "UPDATE custom_grammar SET options_json = ? WHERE channel_name = ? AND rule_name = ?",
                        (options_str, ctx.channel.name, rule)
                    )
                else:
                    await c.execute(
                        "INSERT INTO custom_grammar (channel_name, rule_name, options_json) VALUES (?, ?, ?)",
                        (ctx.channel.name, rule, options_str)
                    )
                await conn.commit()
                await ctx.send(f"Added '{text}' to rule #{rule}#.")
                
            elif action == 'list':
                if not options:
                    await ctx.send(f"Rule #{rule}# has no options.")
                else:
                    items = ", ".join(f"'{o}'" for o in options)
                    await ctx.send(f"Rule #{rule}# options: {items}")
                    
            elif action == 'clear':
                await c.execute(
                    "DELETE FROM custom_grammar WHERE channel_name = ? AND rule_name = ?",
                    (ctx.channel.name, rule)
                )
                await conn.commit()
                await ctx.send(f"Rule #{rule}# cleared.")
    except Exception as e:
        await ctx.send(f"Error managing grammar: {e}")

async def mockbot_poll(self, ctx, *args):
    """Creates a poll using Twitch API: !poll <duration_minutes> <question> | <opt1> | <opt2>"""
    if not await _check_custom_auth(self, ctx):
        await ctx.send("You don't have permission to create polls.")
        return

    if not args:
        await ctx.send("Usage: !poll <duration_minutes> <question> | <opt1> | <opt2>")
        return

    try:
        args_str = " ".join(args)
        parts = [p.strip() for p in args_str.split('|') if p.strip()]
        
        if len(parts) < 3:
            await ctx.send("A poll needs a question and at least two choices separated by '|'.")
            return
            
        first_part = parts[0]
        first_part_words = first_part.split(maxsplit=1)
        if len(first_part_words) < 2:
            await ctx.send("Please provide a duration and a question. Example: !poll 5 Is this cool? | Yes | No")
            return
            
        duration_minutes_str, question = first_part_words
        
        try:
            duration_minutes = float(duration_minutes_str)
        except ValueError:
            await ctx.send(f"Invalid duration: {duration_minutes_str}")
            return
            
        duration_seconds = int(duration_minutes * 60)
        # Twitch Poll duration must be between 15 and 1800 seconds
        duration_seconds = max(15, min(1800, duration_seconds))
        
        choices = parts[1:]
        if len(choices) > 5:
            await ctx.send("Twitch polls can have at most 5 choices.")
            return

        clean_channel = ctx.channel.name.lstrip('#')
        users = await self.fetch_users(names=[clean_channel])
        if not users:
            await ctx.send("Failed to fetch channel from Twitch API.")
            return
            
        broadcaster = users[0]
        
        config = configparser.ConfigParser()
        config.read("settings.conf")
        token = config.get("auth", "tmi_token")
        if token.startswith("oauth:"):
            token = token[6:]

        await broadcaster.create_poll(
            token=token,
            title=question,
            choices=choices,
            duration=duration_seconds,
            channel_points_voting_enabled=False
        )
        
        await ctx.send(f"Poll started: {question} ({duration_minutes_str}m)!")
    except Exception as e:
        await ctx.send(f"Failed to create poll: {e}")
