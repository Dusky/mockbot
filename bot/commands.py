from datetime import datetime

from twitchio.ext import commands
import logging
from tabulate import tabulate
from bot.config import config
from bot.database import IntegrityError
from bot.colors import YELLOW, RED, GREEN, PURPLE, RESET
from bot.tts import start_tts_processing

async def mockbot_command(self, ctx, setting=None, new_value=None, **kwargs):
    if setting is None:
        await ctx.send("Mockbot - A Not So Vanilla Twitch Bot")
        return

    # Fetch channel-specific trusted users and owner
    channel_config_row = await self.db.get_channel_config(ctx.channel.name)

    if channel_config_row is None:
        await ctx.send("This channel is not configured.")
        return

    voice_enabled = channel_config_row["voice_enabled"]
    channel_owner = channel_config_row["owner"]
    channel_trusted_users_raw = channel_config_row["trusted_users"] or ""
    channel_trusted_users = [u for u in channel_trusted_users_raw.split(",") if u]

    # Check if the user is allowed to use the command
    bot_owner = config.owner

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
                tts_cfg = await self.db.get_channel_config(ctx.channel.name)

                if tts_cfg and tts_cfg["tts_enabled"]:
                    voice_preset = tts_cfg["voice_preset"] or 'v2/en_speaker_5'
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
        bot_owner = config.owner
        target_channel = new_value if ctx.author.name == bot_owner and new_value else ctx.channel.name

        ch_auth = await self.db.get_channel_auth(target_channel)
        if ch_auth is None:
            await ctx.send(f"Channel {target_channel} not found in database.")
            return

        channel_owner = ch_auth[0]
        if ctx.author.name != bot_owner and ctx.author.name != channel_owner:
            await ctx.send("You do not have permission to use this command for this channel.")
            return

        voice_enabled_status = 1 if setting == "start" else 0
        await self.db.set_channel_field(target_channel, "voice_enabled", voice_enabled_status)
        action = "enabled" if setting == "start" else "disabled"
        await ctx.send(f"Voice {action} in {target_channel}.")

    elif setting in ["time", "lines"]:
        new_value_int = int(new_value)
        channel_name = ctx.channel.name
        bot_owner = config.owner

        ch_auth = await self.db.get_channel_auth(channel_name)
        if ch_auth is None:
            await ctx.send(f"Channel {channel_name} not found in database.")
            return

        channel_owner, trusted_raw = ch_auth
        trusted_users = [u for u in (trusted_raw or "").split(",") if u]

        if ctx.author.name != bot_owner and ctx.author.name != channel_owner and ctx.author.name not in trusted_users:
            await ctx.send("You do not have permission to use this command in this channel.")
            return

        col = "time_between_messages" if setting == "time" else "lines_between_messages"
        await self.db.set_channel_field(channel_name, col, new_value_int)
        await ctx.send(f"Set {setting}_between_messages to {new_value_int} for channel {channel_name}.")
        self.my_logger.info(f"{ctx.author.name} set {setting}_between_messages to {new_value_int} for channel {channel_name}.")

    elif setting == "trust":
        bot_owner = config.owner

        ch_auth = await self.db.get_channel_auth(ctx.channel.name)
        channel_owner = ch_auth[0] if ch_auth else None

        if ctx.author.name != bot_owner and ctx.author.name != channel_owner:
            await ctx.send("You do not have permission to use this command.")
            return

        trusted_raw = ch_auth[1] if ch_auth else ""
        existing_trusted_users = [u for u in (trusted_raw or "").split(",") if u]
        if new_value not in existing_trusted_users:
            existing_trusted_users.append(new_value)
            await self.db.set_channel_field(ctx.channel.name, "trusted_users", ",".join(existing_trusted_users))
            await ctx.send(f"User {new_value} is now trusted in {ctx.channel.name}.")
        else:
            await ctx.send(f"User {new_value} is already trusted in {ctx.channel.name}.")

    elif setting == "join":
        if ctx.author.name != self.owner:
            await ctx.send("Unauthorized attempt to use join command.")
            return

        if not await self.db.channel_exists(new_value):
            try:
                await self.join_channels([new_value])
                self.channels.append(new_value)
                await self.db.insert_channel(new_value, new_value)
                await ctx.send(f"Joined {new_value} and added to channels.")
            except Exception as e:
                await ctx.send(f"Failed to join {new_value}: {str(e)}")
        else:
            await ctx.send(f"Already in {new_value} or it's already in the database.")

    elif setting == "part":
        if ctx.author.name != self.owner:
            await ctx.send("You do not have permission to use this command.")
            return

        if await self.db.channel_exists(new_value) and new_value in self.channels:
            try:
                await self.part_channels([new_value])
                self.channels.remove(new_value)
                await self.db.set_channel_field(new_value, "join_channel", 0)
                await ctx.send(f"Left channel: {new_value}")
            except Exception as e:
                await ctx.send(f"Failed to leave channel: {new_value}. Error: {str(e)}")
        else:
            await ctx.send(f"The bot is not in channel: {new_value} or it's not in the database.")

    elif setting == "voice_preset":
        bot_owner = config.owner

        ch_auth = await self.db.get_channel_auth(ctx.channel.name)
        if ch_auth is None:
            await ctx.send("Channel not found in database.")
            return

        channel_owner, trusted_raw = ch_auth
        trusted_users_list = [u for u in (trusted_raw or "").split(",") if u]

        if ctx.author.name != bot_owner and ctx.author.name != channel_owner and ctx.author.name not in trusted_users_list:
            await ctx.send("You do not have permission to change the voice preset.")
            return

        if not await self.db.voice_preset_exists(new_value):
            await ctx.send(f"Invalid voice preset: {new_value}.")
            return

        await self.db.set_channel_field(ctx.channel.name, "voice_preset", new_value)
        await ctx.send(f"Voice preset updated to {new_value} for channel {ctx.channel.name}.")

    elif setting == "tts":
        bot_owner = config.owner
        target_channel = ctx.channel.name

        ch_auth = await self.db.get_channel_auth(target_channel)
        if ch_auth is None:
            await ctx.send(f"Channel {target_channel} not found in database.")
            return

        channel_owner = ch_auth[0]
        if ctx.author.name != bot_owner and ctx.author.name != channel_owner:
            await ctx.send("You do not have permission to change TTS settings for this channel.")
            return

        if new_value.lower() == "on":
            new_tts_status = True
        elif new_value.lower() == "off":
            new_tts_status = False
        else:
            await ctx.send("Invalid command. Use '!mockbot tts on' or '!mockbot tts off'.")
            return

        await self.db.set_channel_field(target_channel, "tts_enabled", new_tts_status)
        status_text = "enabled" if new_tts_status else "disabled"
        await ctx.send(f"TTS {status_text} for channel {target_channel}.")

    elif setting == "bits":
        bot_owner = config.owner
        target_channel = ctx.channel.name

        ch_auth = await self.db.get_channel_auth(target_channel)
        if ch_auth is None:
            await ctx.send(f"Channel {target_channel} not found in database.")
            return

        if ctx.author.name != bot_owner and ctx.author.name != ch_auth[0]:
            await ctx.send("You do not have permission to change Bits settings for this channel.")
            return

        if new_value.lower() == "on":
            new_bits_status = True
        elif new_value.lower() == "off":
            new_bits_status = False
        else:
            await ctx.send("Invalid command. Use '!mockbot bits on' or '!mockbot bits off'.")
            return

        await self.db.set_channel_field(target_channel, "pubsub_bits", new_bits_status)
        status_text = "enabled" if new_bits_status else "disabled"
        await ctx.send(f"Bits tracking {status_text} for channel {target_channel}.")

    elif setting == "points":
        bot_owner = config.owner
        target_channel = ctx.channel.name

        ch_auth = await self.db.get_channel_auth(target_channel)
        if ch_auth is None:
            await ctx.send(f"Channel {target_channel} not found in database.")
            return

        if ctx.author.name != bot_owner and ctx.author.name != ch_auth[0]:
            await ctx.send("You do not have permission to change Points settings for this channel.")
            return

        if new_value.lower() == "on":
            new_points_status = True
        elif new_value.lower() == "off":
            new_points_status = False
        else:
            await ctx.send("Invalid command. Use '!mockbot points on' or '!mockbot points off'.")
            return

        await self.db.set_channel_field(target_channel, "pubsub_points", new_points_status)
        status_text = "enabled" if new_points_status else "disabled"
        await ctx.send(f"Points tracking {status_text} for channel {target_channel}.")

    elif setting == "tts_reward":
        if not new_value:
            await ctx.send("Please provide the exact name of the Twitch Channel Point reward. Example: !mockbot tts_reward TTS Voice")
            return

        bot_owner = config.owner
        target_channel = ctx.channel.name

        ch_auth = await self.db.get_channel_auth(target_channel)
        if ch_auth is None:
            await ctx.send(f"Channel {target_channel} not found in database.")
            return

        if ctx.author.name != bot_owner and ctx.author.name != ch_auth[0]:
            await ctx.send("You do not have permission to change the TTS reward command for this channel.")
            return

        reward_val = "" if new_value.lower() in ("none", "off") else new_value
        await self.db.set_channel_field(target_channel, "tts_reward", reward_val)

        if reward_val:
            await ctx.send(f"TTS Channel Point reward set to '{reward_val}' for channel {target_channel}.")
        else:
            await ctx.send(f"TTS Channel Point reward disabled for channel {target_channel}.")


async def _check_custom_auth(self, ctx):
    if ctx.author.name == config.owner:
        return True

    row = await self.db.get_channel_auth(ctx.channel.name)
    if not row:
        return False

    channel_owner, trusted_users_str = row
    if ctx.author.name == channel_owner:
        return True

    trusted_users = [u for u in (trusted_users_str or "").split(",") if u]
    return ctx.author.name in trusted_users

async def mockbot_addc(self, ctx, cmd_name: str, *, response_template: str):
    if not await _check_custom_auth(self, ctx):
        await ctx.send("You don't have permission to manage custom commands.")
        return
        
    cmd_name = cmd_name.lower()
    if not cmd_name.startswith('!'):
        cmd_name = f"!{cmd_name}"
        
    try:
        await self.db.insert_command(ctx.channel.name, cmd_name, response_template)
        await ctx.send(f"Command {cmd_name} added successfully!")
    except IntegrityError:
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
        updated = await self.db.update_command(ctx.channel.name, cmd_name, response_template)
        if updated:
            await ctx.send(f"Command {cmd_name} updated successfully!")
        else:
            await ctx.send(f"Command {cmd_name} not found.")
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
        deleted = await self.db.delete_command(ctx.channel.name, cmd_name)
        if deleted:
            await ctx.send(f"Command {cmd_name} deleted successfully!")
        else:
            await ctx.send(f"Command {cmd_name} not found.")
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
        options_json_str = await self.db.get_grammar_rule(ctx.channel.name, rule)
        options = json.loads(options_json_str) if options_json_str else []

        if action == 'add':
            if not text:
                await ctx.send("Please provide text to add to the rule.")
                return
            options.append(text)
            await self.db.upsert_grammar_rule(ctx.channel.name, rule, json.dumps(options))
            await ctx.send(f"Added '{text}' to rule #{rule}#.")

        elif action == 'list':
            if not options:
                await ctx.send(f"Rule #{rule}# has no options.")
            else:
                items = ", ".join(f"'{o}'" for o in options)
                await ctx.send(f"Rule #{rule}# options: {items}")

        elif action == 'clear':
            await self.db.delete_grammar_rule(ctx.channel.name, rule)
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
        
        token = config.tmi_token
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

async def mockbot_timer(self, ctx, *args):
    """Manage timed messages: !mockbot timer <add|del|msg|list> ..."""
    if not await _check_custom_auth(self, ctx):
        await ctx.send("You don't have permission to manage timers.")
        return

    if not args:
        await ctx.send("Usage: !mockbot timer <add|del|msg|list> ...")
        return

    subcmd = args[0].lower()
    channel_name = ctx.channel.name

    try:
        if subcmd == 'add':
            if len(args) < 3:
                await ctx.send("Usage: !mockbot timer add <pool_name> <interval_minutes>")
                return
            pool_name = args[1].lower()
            try:
                interval = int(args[2])
            except ValueError:
                await ctx.send("Error: Interval must be a number of minutes.")
                return

            try:
                await self.db.insert_timed_pool(channel_name, pool_name, interval)
                await ctx.send(f"Created timer pool '{pool_name}' (Interval: {interval}m).")
            except IntegrityError:
                await ctx.send(f"Error: Timer pool '{pool_name}' already exists.")

        elif subcmd == 'del':
            if len(args) < 2:
                await ctx.send("Usage: !mockbot timer del <pool_name>")
                return
            pool_name = args[1].lower()
            if await self.db.delete_timed_pool(channel_name, pool_name):
                await ctx.send(f"Deleted timer pool '{pool_name}'.")
            else:
                await ctx.send(f"Error: Timer pool '{pool_name}' not found.")

        elif subcmd == 'msg':
            if len(args) < 3:
                await ctx.send("Usage: !mockbot timer msg <pool_name> <message...>")
                return
            pool_name = args[1].lower()
            message_text = " ".join(args[2:])

            if not await self.db.pool_exists(channel_name, pool_name):
                await ctx.send(f"Error: Timer pool '{pool_name}' not found. Create it first with 'timer add'.")
                return

            await self.db.add_pool_message(channel_name, pool_name, message_text)
            await ctx.send(f"Added message to timer pool '{pool_name}'.")

        elif subcmd == 'list':
            pools = await self.db.get_timed_pools(channel_name)

            if not pools:
                await ctx.send(f"No timer pools found for #{channel_name}.")
                return

            output_parts = []
            for p_name, p_int in pools:
                msg_count = await self.db.count_pool_messages(channel_name, p_name)
                output_parts.append(f"{p_name} ({p_int}m, {msg_count} msgs)")

            await ctx.send("Timer Pools: " + " | ".join(output_parts))
        else:
            await ctx.send(f"Unknown timer subcommand: {subcmd}. Use add, del, msg, or list.")

    except Exception as e:
        await ctx.send(f"Timer Error: {e}")

async def mockbot_var(self, ctx, subcmd, var_name, value=""):
    """Handle the !var command"""
    channel_name = ctx.channel.name
    
    # Check permissions
    is_authorized = False
    if hasattr(ctx.author, 'name'):
        bot_owner = config.owner.lower()
        
        is_owner = ctx.author.name.lower() == bot_owner
        is_mod = getattr(ctx.author, 'is_mod', False)
        is_broadcaster = getattr(ctx.author, 'is_broadcaster', False)
        is_authorized = is_owner or is_mod or is_broadcaster
    
    if subcmd in ['set', 'add'] and not is_authorized:
        await ctx.send("You do not have permission to modify variables.")
        return

    try:
        if subcmd == 'get':
            val = await self.db.get_variable(channel_name, var_name)
            await ctx.send(f"Variable '{var_name}' is currently: {val}")

        elif subcmd == 'set':
            try:
                val_int = int(value)
            except ValueError:
                await ctx.send("Value must be an integer.")
                return
            await self.db.set_variable(channel_name, var_name, val_int)
            await ctx.send(f"Variable '{var_name}' set to {val_int}.")

        elif subcmd == 'add':
            try:
                val_int = int(value) if value.strip() else 1
            except ValueError:
                val_int = 1
            new_val = await self.db.increment_variable(channel_name, var_name, val_int)
            await ctx.send(f"Added {val_int} to '{var_name}'. New value: {new_val}")

        else:
            await ctx.send(f"Unknown var subcommand: {subcmd}. Use set, add, or get.")

    except Exception as e:
        await ctx.send(f"Variable Error: {e}")
