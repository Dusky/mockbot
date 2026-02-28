import asyncio
import logging
import aiosqlite
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.completion import WordCompleter, NestedCompleter
from prompt_toolkit.styles import Style
from colorama import Fore, Style as CStyle
import uuid
from datetime import datetime
from bot.tts import start_tts_processing

class InteractiveShell:
    def __init__(self, bot):
        self.bot = bot
        self.current_context = "Global" # "Global" or "#channel"
        self.running = True
        
        # Define completer
        self.completer = NestedCompleter.from_nested_dict({
            'status': None,
            'use': {'global': None, '#': None}, # Dynamic completion for channels would be better
            'join': None,
            'part': None,
            'say': None,
            'voice': {'on': None, 'off': None},
            'tts': {'on': None, 'off': None},
            'help': None,
            'quit': None,
            'exit': None
        })
        
        self.style = Style.from_dict({
            'prompt': '#ansiteal bold',
            'context': '#ansipurple bold',
        })

    def get_prompt_text(self):
        context_str = f"({self.current_context})" if self.current_context != "Global" else ""
        return [
            ('class:prompt', 'mockbot '),
            ('class:context', context_str),
            ('class:prompt', '> '),
        ]

    async def handle_command(self, text):
        parts = text.strip().split()
        if not parts:
            return

        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in ['quit', 'exit', 'q']:
            self.running = False
            await self.bot.close()
            # Stop the loop
            asyncio.get_event_loop().stop()
            return

        elif cmd == 'status':
            if self.current_context == "Global":
                await self.bot.print_channel_status()
            else:
                await self.bot.print_channel_status(self.current_context.lstrip('#'))

        elif cmd in ['brain', 'stats']:
            if args:
                target_channel = args[0].lstrip('#')
                await self.bot.print_brain_status(target_channel)
            elif self.current_context != "Global":
                await self.bot.print_brain_status(self.current_context.lstrip('#'))
            else:
                await self.bot.print_brain_status()

        elif cmd == 'use':
            if not args:
                self.current_context = "Global"
                if hasattr(self.bot, 'my_logger'):
                    self.bot.my_logger.active_channel_filter = None
                print(f"Context switched to: {self.current_context}")
                return
            target = args[0].lower()
            if target == 'global':
                self.current_context = "Global"
                if hasattr(self.bot, 'my_logger'):
                    self.bot.my_logger.active_channel_filter = None
            elif target.startswith('#'):
                # Ideally verify channel exists or is joined
                self.current_context = target
                if hasattr(self.bot, 'my_logger'):
                    self.bot.my_logger.active_channel_filter = target.lstrip('#')
            else:
                self.current_context = f"#{target}"
                if hasattr(self.bot, 'my_logger'):
                    self.bot.my_logger.active_channel_filter = target
            print(f"Context switched to: {self.current_context}")

        elif cmd == 'join':
            if not args:
                print("Usage: join <#channel>")
                return
            channel = args[0]
            if not channel.startswith('#'):
                channel = f"#{channel}"
            await self.bot.join_channel(channel)
            self.bot.ensure_channel_configs() # Ensure config exists
            
        elif cmd == 'part':
            if not args:
                print("Usage: part <#channel>")
                return
            channel = args[0]
            if not channel.startswith('#'):
                channel = f"#{channel}"
            # Need to implement part logic in core if not exists (TwitchIO has part)
            # await self.bot.part_channels([channel]) 
            # TwitchIO part is mostly just closing connection or sending part command
            print("Parting not fully implemented in core logic for specific channels via CLI yet.")

        elif cmd == 'say':
            if self.current_context == "Global":
                print("Error: Cannot 'say' in Global context. Use 'use #channel' first.")
                return
            if not args:
                print("Usage: say <message>")
                return
            message = " ".join(args)
            channel_name = self.current_context.lstrip('#')
            channel = self.bot.get_channel(channel_name)
            if channel:
                await channel.send(message)
                print(f"Sent to {self.current_context}: {message}")
                
                # Check if TTS is enabled and trigger it
                if self.bot.enable_tts and self.bot.is_tts_enabled(channel_name):
                    voice_preset = self.bot.get_channel_voice_preset(channel_name)
                    msg_id = f"cli_{uuid.uuid4().hex[:8]}"
                    timestamp_str = datetime.now().isoformat()
                    
                    start_tts_processing(
                        input_text=message,
                        channel_name=channel_name,
                        message_id=msg_id,
                        timestamp_str=timestamp_str,
                        voice_preset_override=voice_preset,
                        db_file=self.bot.db_file
                    )
                    print(f"🎙️ TTS Request dispatched for manual 'say' command.")
            else:
                print(f"Error: Not connected to {self.current_context}")

        elif cmd == 'tts':
            if not args or args[0].lower() not in ['on', 'off']:
                print("Usage: tts <on|off>")
                return
            state = 1 if args[0].lower() == 'on' else 0
            await self._update_setting('tts_enabled', state)

        elif cmd == 'voice':
            if not args or args[0].lower() not in ['on', 'off']:
                print("Usage: voice <on|off>")
                return
            state = 1 if args[0].lower() == 'on' else 0
            await self._update_setting('voice_enabled', state)

        elif cmd == 'model':
            if not args or args[0].lower() not in ['general', 'individual']:
                print("Usage: model <general|individual>")
                return
            state = 1 if args[0].lower() == 'general' else 0
            await self._update_setting('use_general_model', state)
            
        elif cmd == 'set':
            if len(args) < 2:
                print("Usage: set <lines|time|model> <val>")
                return
            key = args[0].lower()
            val_str = args[1].lower()

            if key == 'lines':
                try: val = int(val_str)
                except ValueError: print("Error: Value must be a number."); return
                await self._update_setting('lines_between_messages', val)
            elif key == 'time':
                try: val = int(val_str)
                except ValueError: print("Error: Value must be a number."); return
                await self._update_setting('time_between_messages', val)
            elif key == 'model':
                if val_str not in ['general', 'individual']:
                    print("Usage: set model <general|individual>")
                    return
                state = 1 if val_str == 'general' else 0
                await self._update_setting('use_general_model', state)
            elif key == 'chance':
                try: 
                    val = float(val_str)
                    if val < 0.0 or val > 100.0:
                        raise ValueError()
                except ValueError: 
                    print("Error: Value must be a number between 0 and 100."); return
                await self._update_setting('random_chance', val)
            elif key == 'log_dice':
                if val_str not in ['on', 'off', 'true', 'false']:
                    print("Usage: set log_dice <on|off>")
                    return
                state = 1 if val_str in ['on', 'true'] else 0
                await self._update_setting('log_dice', state)
            elif key == 'voice':
                if not args or len(args) < 2:
                    print("Usage: set voice <model_name>")
                    return
                # We will store the original string (case-sensitive if needed) as val_str is already lowered above.
                # Let's use the actual passed argument for voice preset to preserve case:
                actual_val = args[1]
                await self._update_setting('voice_preset', actual_val)
            elif key == 'delay':
                if val_str not in ['on', 'off', 'true', 'false']:
                    print("Usage: set delay <on|off>")
                    return
                state = 1 if val_str in ['on', 'true'] else 0
                await self._update_setting('tts_delay_enabled', state)
            else:
                print(f"Unknown setting: {key}. Available: lines, time, chance, model, log_dice, voice, delay.")

        elif cmd in ['trust', 'untrust', 'ignore', 'unignore']:
            if self.current_context == "Global":
                print(f"Error: Cannot use '{cmd}' in Global context. Use 'use #channel' first.")
                return
            if not args:
                print(f"Usage: {cmd} <username>")
                return
                
            username = args[0].lower()
            column = 'trusted_users' if cmd in ['trust', 'untrust'] else 'ignored_users'
            is_add = cmd in ['trust', 'ignore']
            clean_name = self.current_context.lstrip('#')
            
            db_file = self.bot.db_file
            try:
                async with aiosqlite.connect(db_file) as conn:
                    c = await conn.cursor()
                    await c.execute(f"SELECT {column} FROM channel_configs WHERE channel_name = ?", (clean_name,))
                    row = await c.fetchone()
                    if row is None:
                        print(f"Error: Channel {clean_name} not found in database.")
                        return
                    
                    user_list = [u.strip() for u in (row[0] or "").split(',') if u.strip()]
                    
                    if is_add:
                        if username not in user_list:
                            user_list.append(username)
                            print(f"✅ Added {username} to {column} for {self.current_context}.")
                        else:
                            print(f"ℹ️ User {username} is already in {column} for {self.current_context}.")
                    else:
                        if username in user_list:
                            user_list.remove(username)
                            print(f"✅ Removed {username} from {column} for {self.current_context}.")
                        else:
                            print(f"ℹ️ User {username} is not in {column} for {self.current_context}.")
                            
                    new_val = ",".join(user_list)
                    await c.execute(f"UPDATE channel_configs SET {column} = ? WHERE channel_name = ?", (new_val, clean_name))
                    await conn.commit()
                self.bot.load_channel_settings()
            except Exception as e:
                print(f"Database Error: {e}")

        elif cmd == 'addc':
            if len(args) < 2:
                print("Usage: addc <cmd> <response>")
                return
            cmd_name = args[0].lower()
            if not cmd_name.startswith('!'): cmd_name = f"!{cmd_name}"
            response = " ".join(args[1:])
            target_chan = 'global' if self.current_context == 'Global' else self.current_context.lstrip('#')
            
            try:
                import sqlite3
                async with aiosqlite.connect(self.bot.db_file) as conn:
                    c = await conn.cursor()
                    await c.execute(
                        "INSERT INTO custom_commands (channel_name, command_name, response_template) VALUES (?, ?, ?)",
                        (target_chan, cmd_name, response)
                    )
                    await conn.commit()
                print(f"✅ Added {cmd_name} to {target_chan}.")
            except sqlite3.IntegrityError:
                print(f"❌ Command {cmd_name} already exists. Use editc.")
            except Exception as e:
                print(f"Error: {e}")

        elif cmd == 'editc':
            if len(args) < 2:
                print("Usage: editc <cmd> <response>")
                return
            cmd_name = args[0].lower()
            if not cmd_name.startswith('!'): cmd_name = f"!{cmd_name}"
            response = " ".join(args[1:])
            target_chan = 'global' if self.current_context == 'Global' else self.current_context.lstrip('#')
            
            try:
                async with aiosqlite.connect(self.bot.db_file) as conn:
                    c = await conn.cursor()
                    await c.execute(
                        "UPDATE custom_commands SET response_template = ? WHERE channel_name = ? AND command_name = ?",
                        (response, target_chan, cmd_name)
                    )
                    if c.rowcount > 0:
                        print(f"✅ Updated {cmd_name} in {target_chan}.")
                    else:
                        print(f"❌ Command {cmd_name} not found in {target_chan}.")
                    await conn.commit()
            except Exception as e:
                print(f"Error: {e}")

        elif cmd == 'delc':
            if len(args) < 1:
                print("Usage: delc <cmd>")
                return
            cmd_name = args[0].lower()
            if not cmd_name.startswith('!'): cmd_name = f"!{cmd_name}"
            target_chan = 'global' if self.current_context == 'Global' else self.current_context.lstrip('#')
            
            try:
                async with aiosqlite.connect(self.bot.db_file) as conn:
                    c = await conn.cursor()
                    await c.execute(
                        "DELETE FROM custom_commands WHERE channel_name = ? AND command_name = ?",
                        (target_chan, cmd_name)
                    )
                    if c.rowcount > 0:
                        print(f"✅ Deleted {cmd_name} from {target_chan}.")
                    else:
                        print(f"❌ Command {cmd_name} not found in {target_chan}.")
                    await conn.commit()
            except Exception as e:
                print(f"Error: {e}")

        elif cmd == 'grammar':
            if len(args) < 2:
                print("Usage: grammar <add|list|clear> <rule> [text]")
                return
            action = args[0].lower()
            rule = args[1].lower()
            text = " ".join(args[2:])
            target_chan = 'global' if self.current_context == 'Global' else self.current_context.lstrip('#')
            
            try:
                import json
                async with aiosqlite.connect(self.bot.db_file) as conn:
                    c = await conn.cursor()
                    await c.execute("SELECT options_json FROM custom_grammar WHERE channel_name = ? AND rule_name = ?", (target_chan, rule))
                    row = await c.fetchone()
                    options = json.loads(row[0]) if row else []
                    
                    if action == 'add':
                        if not text:
                            print("Please provide text.")
                            return
                        options.append(text)
                        if row:
                            await c.execute("UPDATE custom_grammar SET options_json = ? WHERE channel_name = ? AND rule_name = ?", (json.dumps(options), target_chan, rule))
                        else:
                            await c.execute("INSERT INTO custom_grammar (channel_name, rule_name, options_json) VALUES (?, ?, ?)", (target_chan, rule, json.dumps(options)))
                        print(f"✅ Added '{text}' to #{rule}# in {target_chan}.")
                    elif action == 'list':
                        if not options: print(f"Rule #{rule}# empty.")
                        else: print(f"Rule #{rule}# options: {', '.join(options)}")
                    elif action == 'clear':
                        await c.execute("DELETE FROM custom_grammar WHERE channel_name = ? AND rule_name = ?", (target_chan, rule))
                        print(f"✅ Cleared rule #{rule}# from {target_chan}.")
                    await conn.commit()
            except Exception as e:
                print(f"Error: {e}")
        elif cmd == 'poll':
            if self.current_context == "Global":
                print("Cannot create a poll globally. Please 'use <channel>' first.")
                return
            
            if not args:
                print("Usage: poll <duration_minutes> <question> | <opt1> | <opt2>")
                return
                
            try:
                args_str = " ".join(args)
                parts = [p.strip() for p in args_str.split('|') if p.strip()]
                
                if len(parts) < 3:
                    print("A poll needs a question and at least two choices separated by '|'.")
                    return
                    
                first_part = parts[0]
                first_part_words = first_part.split(maxsplit=1)
                if len(first_part_words) < 2:
                    print("Please provide a duration and a question. Example: poll 5 Is this cool? | Yes | No")
                    return
                    
                duration_minutes_str, question = first_part_words
                
                try:
                    duration_minutes = float(duration_minutes_str)
                except ValueError:
                    print(f"Invalid duration: {duration_minutes_str}")
                    return
                    
                duration_seconds = int(duration_minutes * 60)
                duration_seconds = max(15, min(1800, duration_seconds))
                
                choices = parts[1:]
                if len(choices) > 5:
                    print("Twitch polls can have at most 5 choices.")
                    return

                clean_channel = self.current_context.lstrip('#')
                users = await self.bot.fetch_users(names=[clean_channel])
                if not users:
                    print("Failed to fetch channel from Twitch API.")
                    return
                    
                broadcaster = users[0]
                
                import configparser
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
                
                print(f"✅ Poll started in {self.current_context}: {question} ({duration_minutes_str}m)!")
            except Exception as e:
                print(f"Failed to create poll: {e}")

        elif cmd == 'help':
            print("""
Available Commands:
  status            Show status table (context-aware)
  use [channel]     Switch context (empty clears to global)
  join <#channel>   Join a channel
  say <message>     Send chat (in channel context)
  tts <on|off>      Toggle TTS for current context
  trust <user>      Add user to trusted users (allows command usage)
  untrust <user>    Remove user from trusted users
  ignore <user>     Add user to ignored lists (prevents brain learning)
  unignore <user>   Remove user from ignored lists
  model <gen|indiv> Toggle Markov model type (general/individual)
  set <key> <val>   Set config (keys: lines, time, chance, model, log_dice, voice, delay, bits, points)
  poll <args>       Create a poll (e.g. poll 5 Yes/No? | Yes | No)
  addc <cmd> <resp> Add custom command (use <sender> <streamer> <input>)
  editc <cmd> <rsp> Edit custom command
  delc <cmd>        Delete custom command
  grammar <action>  Manage grammar (add, list, clear) <rule> [text]
  brain, stats      Show number of lines loaded per channel
  quit, q           Exit bot
            """)
        else:
            print(f"Unknown command: {cmd}")

    async def _update_setting(self, column, value):
        db_file = self.bot.db_file
        try:
            async with aiosqlite.connect(db_file) as conn:
                c = await conn.cursor()
                if self.current_context == "Global":
                    await c.execute(f"UPDATE channel_configs SET {column} = ?", (value,))
                    print(f"Updated {column} globally to {value}")
                else:
                    clean_name = self.current_context.lstrip('#')
                    await c.execute(f"UPDATE channel_configs SET {column} = ? WHERE channel_name = ?", (value, clean_name))
                    print(f"Updated {column} for {self.current_context} to {value}")
                await conn.commit()
            self.bot.load_channel_settings() # Reload settings into memory
        except Exception as e:
            print(f"Database Error: {e}")

    async def run(self):
        session = PromptSession(completer=self.completer, style=self.style)
        
        # Keep prompt running
        with patch_stdout(raw=True):
            while self.running:
                try:
                    text = await session.prompt_async(self.get_prompt_text())
                    await self.handle_command(text)
                except KeyboardInterrupt:
                    continue
                except EOFError:
                    break
                except Exception as e:
                    print(f"Shell Error: {e}")
