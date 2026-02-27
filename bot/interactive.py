import asyncio
import logging
import aiosqlite
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.completion import WordCompleter, NestedCompleter
from prompt_toolkit.styles import Style
from colorama import Fore, Style as CStyle

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
            channel = self.bot.get_channel(self.current_context.lstrip('#'))
            if channel:
                await channel.send(message)
                print(f"Sent to {self.current_context}: {message}")
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
            else:
                print(f"Unknown setting: {key}. Available: lines, time, chance, model, log_dice.")

        elif cmd == 'help':
            print("""
Available Commands:
  status            Show status table (context-aware)
  use [channel]     Switch context (empty clears to global)
  join <#channel>   Join a channel
  say <message>     Send chat (in channel context)
  tts <on|off>      Toggle TTS for current context
  voice <on|off>    Toggle voice for current context
  model <gen|indiv> Toggle Markov model type (general/individual)
  set <key> <val>   Set config (keys: lines, time, chance, model)
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
