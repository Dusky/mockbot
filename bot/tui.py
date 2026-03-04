import asyncio
from collections import defaultdict
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, RichLog, Static, ListView, ListItem, Label, Button
from textual.containers import Container, Horizontal, VerticalScroll
from textual import work
from datetime import datetime
from textual.events import Key

MAX_BUFFER = 500  # Maximum messages to keep per channel buffer

class CommandInput(Input):
    """Custom Input widget that supports command history and basic tab completion."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.history = []
        self.history_index = -1
        self.temp_value = "" # Store what user was typing before browsing history
        self.autocomplete_options = [
            "help", "use ", "join ", "leave ", "tts", "timer", "model", 
            "status", "testvoice", "lines", "chance", "dice"
        ]
        self.tab_index = -1
        self.last_tab_base = ""

    def add_history(self, command: str):
        if not command or (self.history and self.history[-1] == command):
            return
        self.history.append(command)
        self.history_index = len(self.history)
        self.temp_value = ""
        self.tab_index = -1

    async def on_key(self, event: Key):
        # Handle Up/Down for history
        if event.key == "up":
            event.prevent_default()
            if self.history_index == len(self.history):
                self.temp_value = self.value
            
            if self.history_index > 0:
                self.history_index -= 1
                self.value = self.history[self.history_index]
                self.action_end()
        elif event.key == "down":
            event.prevent_default()
            if self.history_index < len(self.history) - 1:
                self.history_index += 1
                self.value = self.history[self.history_index]
                self.action_end()
            elif self.history_index == len(self.history) - 1:
                self.history_index = len(self.history)
                self.value = self.temp_value
                self.action_end()
        
        # Handle Tab for autocompletion
        elif event.key == "tab":
            event.prevent_default()
            if self.tab_index == -1:
                self.last_tab_base = self.value
            
            # Find matches
            matches = [opt for opt in self.autocomplete_options if opt.startswith(self.last_tab_base.lower())]
            if matches:
                self.tab_index = (self.tab_index + 1) % len(matches)
                self.value = matches[self.tab_index]
                self.action_end()
        else:
            # Type normally, reset tab state
            if event.is_printable:
                self.tab_index = -1



class MockbotDashboard(App):
    """A Textual terminal dashboard for Mockbot."""
    
    CSS = """
    MockbotDashboard {
        layout: vertical;
        background: transparent;
    }
    
    #status_bar {
        dock: top;
        width: 100%;
        padding: 0 1;
        background: $boost;
        color: $text-muted;
        text-style: bold;
    }
    
    #log_container {
        height: 1fr;
        padding: 0;
        background: transparent;
    }
    
    RichLog {
        height: 100%;
        width: 100%;
        background: transparent;
        scrollbar-background: transparent;
    }
    
    #input_container {
        dock: bottom;
        height: 1;
        width: 100%;
        background: transparent;
    }
    
    #input_prefix {
        width: auto;
        padding: 0 1 0 0;
        text-style: bold;
        color: $accent;
    }
    
    Input {
        width: 1fr;
        border: none;
        background: $surface;
        padding: 0 1;
    }
    
    Input:focus {
        border: none;
        background: $surface-lighten-2;
        color: $text;
    }
    
    #channel_sidebar {
        dock: right;
        width: 24;
        height: 100%;
        background: $panel;
        border-left: solid $primary;
    }
    
    #channel_sidebar > ListItem {
        padding: 0 1;
    }

    .channel-container {
        height: 1;
        layout: horizontal;
        width: 100%;
    }
    
    .channel-name {
        width: 1fr;
        content-align: left middle;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    
    .channel-status {
        width: auto;
        content-align: right middle;
        padding-left: 1;
    }
    """
    
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear_log", "Clear Log")
    ]

    def __init__(self, bot=None):
        super().__init__()
        self.bot = bot
        self.log_widget = None
        self.current_context = "Global"
        # Per-channel message buffer: {"global": [...], "channelname": [...]}
        self.log_buffers = defaultdict(list)
        
    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Static("🔌 [bold]Mockbot[/bold] | Global Context", id="status_bar")
        with Container(id="log_container"):
            self.log_widget = RichLog(highlight=False, markup=True, wrap=True)
            yield self.log_widget
        with Horizontal(id="input_container"):
            yield Static("mockbot >", id="input_prefix")
            yield CommandInput(placeholder="", id="command_input")
        yield ListView(id="channel_sidebar")

    def on_mount(self) -> None:
        """Called when app starts."""
        self.write_log("[bold green]Mockbot Dashboard Initialized![/bold green]")
        self.write_log("Type 'help' for commands, or 'use #channel' to switch context.")
        
        # Set up a live clock to refresh the status bar periodically
        self.set_interval(5.0, self.update_status_bar)
        
        # Periodically refresh the sidebar to pick up Live Stream status changes
        self.set_interval(60.0, self.update_sidebar)

        # Focus the input field immediately
        self.update_prompt()
        self.update_sidebar()
        self.query_one(CommandInput).focus()

    def write_log(self, message, channel=None) -> None:
        """Thread-safe way to write to the log widget with channel-aware buffering."""
        import threading
        
        # Determine buffer key
        buf_key = (channel or "global").lower().lstrip('#')
        
        # Store in buffer
        buf = self.log_buffers[buf_key]
        buf.append(message)
        if len(buf) > MAX_BUFFER:
            self.log_buffers[buf_key] = buf[-MAX_BUFFER:]
        
        # Decide whether to display this message in the current view
        should_display = False
        if self.current_context == "Global":
            should_display = True  # Global shows everything
        elif self.current_context == "System":
            should_display = (buf_key == "global") # System shows only global (bot) messages
        else:
            ctx_channel = self.current_context.lower().lstrip('#')
            should_display = (buf_key == ctx_channel)
        
        if not should_display or not self.log_widget:
            return
            
        if getattr(self, "_thread_id", None) == threading.get_ident():
            self.log_widget.write(self._render_msg(message))
        else:
            try:
                self.call_from_thread(self.log_widget.write, self._render_msg(message))
            except Exception:
                pass
    
    def _cmd_log(self, message: str) -> None:
        """Helper to route user-facing command feedback to the active channel log."""
        target = "global" if self.current_context in ("Global", "System") else self.current_context
        if target != "global":
            self.write_log(message, channel="global")
        self.write_log(message, channel=target)

    def _repopulate_log(self):
        """Clear and refill the RichLog widget based on the current context."""
        if not self.log_widget:
            return
        self.log_widget.clear()
        
        if self.current_context == "Global":
            # Global: See literally everything interleaved
            for msg in self.log_buffers.get("global", []):
                self.log_widget.write(self._render_msg(msg))
            for key, msgs in self.log_buffers.items():
                if key != "global":
                    for msg in msgs:
                        self.log_widget.write(self._render_msg(msg, force_channel=key))
        elif self.current_context == "System":
            # System: Only see bot events/boot logs
            for msg in self.log_buffers.get("global", []):
                self.log_widget.write(self._render_msg(msg))
        else:
            # Channel context: Show ONLY this channel's chat
            ctx_key = self.current_context.lower().lstrip('#')
            for msg in self.log_buffers.get(ctx_key, []):
                self.log_widget.write(self._render_msg(msg))

    def action_clear_log(self) -> None:
        """Clear the log widget."""
        if self.log_widget:
            self.log_widget.clear()
            self._cmd_log("[italic]Log cleared.[/italic]")

    def _render_msg(self, msg_obj, force_channel=None):
        """Converts a dictionary from logger.py into a Rich Table with hanging indents."""
        if not isinstance(msg_obj, dict):
            # Fallback for generic string logs (e.g. system events)
            if force_channel and isinstance(msg_obj, str):
                color_idx = "white"
                if self.bot and hasattr(self.bot, 'my_logger'):
                    color_idx = self.bot.my_logger.color_manager.get_channel_color(force_channel)
                
                if isinstance(color_idx, str) and color_idx.isdigit():
                    prefix_style = f"color({color_idx})"
                else:
                    prefix_style = f"{color_idx}"
                return f"[bold {prefix_style}]#{force_channel}[/] | {msg_obj}"
            return msg_obj

        from rich.table import Table
        from rich.text import Text

        table = Table(
            show_header=False, 
            show_edge=False, 
            box=None, 
            padding=(0, 1, 0, 0),
            collapse_padding=True
        )
        table.add_column("Time", justify="left", no_wrap=True)
        
        show_channel = msg_obj.get("channel") and (self.current_context == "Global" or force_channel)
        if show_channel:
            table.add_column("Channel", justify="left", no_wrap=True)
            
        table.add_column("User", justify="right", no_wrap=True)
        table.add_column("Message", justify="left", ratio=1)
        
        time_text = Text(f"[{msg_obj['timestamp']}] {msg_obj['tags']}", style="dim")
        
        chan_text = None
        if show_channel:
            c_name = force_channel or msg_obj.get("channel")
            color_idx = "white"
            if self.bot and hasattr(self.bot, 'my_logger'):
                color_idx = self.bot.my_logger.color_manager.get_channel_color(c_name)
            
            if isinstance(color_idx, str) and color_idx.isdigit():
                chan_style = f"bold color({color_idx})"
            else:
                chan_style = f"bold {color_idx}"
            chan_text = Text(f"#{c_name}", style=chan_style)
            
        user_color = msg_obj.get("color", "white")
        # Ensure # is present for hex codes, unless it's a known rich color string
        if isinstance(user_color, str) and not user_color.startswith("#") and user_color.isalnum() and len(user_color) == 6:
            user_color = f"#{user_color}"

        if msg_obj.get("is_bot"):
            user_text = Text(f"<{msg_obj['username']}>", style="bold magenta")
            msg_text = Text(msg_obj['message'], style="magenta italic")
        else:
            if isinstance(user_color, str) and user_color.isdigit():
                user_style = f"bold color({user_color})"
            else:
                user_style = f"bold {user_color}" if user_color else "bold"
            user_text = Text(f"<{msg_obj['username']}>", style=user_style)
            msg_text = Text(msg_obj['message'], style="italic")
            
        if msg_obj.get("not_logged"):
            msg_text.append(f" {msg_obj['not_logged']}", style="dim red")
            
        if chan_text:
            table.add_row(time_text, chan_text, user_text, msg_text)
        else:
            table.add_row(time_text, user_text, msg_text)
            
        return table

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle when the user hits Enter in the input field."""
        command_text = event.value.strip()
        if not command_text:
            return
            
        # Add to history
        self.query_one(CommandInput).add_history(command_text)
            
        # Echo the command to the log
        self._cmd_log(f"> [bold cyan]{command_text}[/bold cyan]")
        
        # Clear the input
        event.input.value = ""
        
        # Process the command (mirrors the old interactive.py logic)
        await self.handle_command(command_text)
        
        # Trigger an async refresh of the top dashboard bar
        self.update_status_bar()
        
    def update_prompt(self):
        """Update the input placeholder when context changes."""
        prefix = self.query_one("#input_prefix")
        prefix.update(f"mockbot ({self.current_context})>")
        self.update_status_bar()

    async def _async_update_status_bar(self):
        """Async worker to fetch channel stats and update the status bar."""
        # If it's the generic context, show live bot stats
        if self.current_context in ("Global", "System"):
            try:
                uptime_str = "0:00:00"
                if self.bot and hasattr(self.bot, 'start_time'):
                    uptime = datetime.now() - self.bot.start_time
                    uptime_str = str(uptime).split('.')[0] # Remove microseconds
                    
                import threading
                thread_count = threading.active_count()
                
                self.query_one("#status_bar").update(
                    f"🟢 [bold]Mockbot[/bold] | {self.current_context} Context | Uptime: {uptime_str} | Threads: {thread_count}"
                )
            except Exception:
                pass
            return
            
        clean_name = self.current_context.lstrip('#')
        try:
            import aiosqlite
            async with aiosqlite.connect(self.bot.db_file) as conn:
                c = await conn.cursor()
                await c.execute("SELECT voice_enabled, tts_enabled, join_channel, time_between_messages, random_chance, use_general_model FROM channel_configs WHERE channel_name = ?", (clean_name,))
                row = await c.fetchone()
                
                if row:
                    voice, tts, jn, time_b, chance, model = row
                    t_str = "[green]ON[/]" if tts else "[red]OFF[/]"
                    v_str = "[green]ON[/]" if voice else "[red]OFF[/]"
                    m_str = "[green]General[/]" if model else "[magenta]Indiv[/]"
                    c_str = f"[cyan]{chance}%[/]"
                    is_joined = "🟢" if f"#{clean_name}" in self.bot._joined_channels else "🔴"
                    
                    status_text = f"{is_joined} [bold]#{clean_name}[/bold] | Model: {m_str} | TTS: {t_str} | Voice: {v_str} | Chance: {c_str} | Delay: {time_b}s"
                    self.query_one("#status_bar").update(status_text)
                else:
                    self.query_one("#status_bar").update(f"🔴 [bold]#{clean_name}[/bold] | (Not in Database)")
        except Exception:
            pass

    def update_status_bar(self):
        # Fire and forget the async updater so we don't block the UI thread bindings
        asyncio.create_task(self._async_update_status_bar())

    async def handle_command(self, text: str) -> None:
        """Process CLI commands natively."""
        parts = text.split()
        cmd = parts[0].lower()
        args = parts[1:]
        
        if cmd in ['quit', 'exit', 'q']:
            self._cmd_log("[bold red]Shutting down...[/bold red]")
            if self.bot:
                await self.bot.close()
            self.exit()
            
        elif cmd == 'clear':
            self.action_clear_log()
            
        elif cmd == 'status':
            if self.bot:
                if self.current_context == "Global":
                    await self.bot.print_channel_status(out_func=self._cmd_log)
                else:
                    await self.bot.print_channel_status(self.current_context.lstrip('#'), out_func=self._cmd_log)
            else:
                self._cmd_log("Bot instance not connected.")
                
        elif cmd in ['brain', 'stats']:
            if self.bot:
                if args:
                    target_channel = args[0].lstrip('#')
                    await self.bot.print_brain_status(target_channel, out_func=self._cmd_log)
                elif self.current_context != "Global":
                    await self.bot.print_brain_status(self.current_context.lstrip('#'), out_func=self._cmd_log)
                else:
                    await self.bot.print_brain_status(out_func=self._cmd_log)
            else:
                self._cmd_log("Bot instance not connected.")
                
        elif cmd == 'use':
            if not args:
                self.current_context = "Global"
                if self.bot and hasattr(self.bot, 'my_logger'):
                    self.bot.my_logger.active_channel_filter = None
            else:
                target = args[0].lower()
                if target == 'global':
                    self.current_context = "Global"
                    if self.bot and hasattr(self.bot, 'my_logger'):
                        self.bot.my_logger.active_channel_filter = None
                elif target == 'system':
                    self.current_context = "System"
                    if self.bot and hasattr(self.bot, 'my_logger'):
                        self.bot.my_logger.active_channel_filter = None
                elif target.startswith('#'):
                    self.current_context = target
                    if self.bot and hasattr(self.bot, 'my_logger'):
                        self.bot.my_logger.active_channel_filter = target.lstrip('#')
                else:
                    self.current_context = f"#{target}"
                    if self.bot and hasattr(self.bot, 'my_logger'):
                        self.bot.my_logger.active_channel_filter = target
            
            self.update_prompt()
            self._repopulate_log()
            self.update_sidebar()
            
        elif cmd == 'say':
            if self.current_context in ("Global", "System"):
                self._cmd_log("[bold red]Error:[/bold red] Cannot 'say' in Global context. Use 'use #channel' first.")
                return
            if not args:
                self._cmd_log("Usage: say <message>")
                return
            message = " ".join(args)
            channel_name = self.current_context.lstrip('#')
            if self.bot:
                # Route through the unified message queue to ensure proper rate limits and DB logging
                await self.bot.send_message_to_channel(channel_name, message)
        elif cmd == 'poll':
            if self.current_context == "Global":
                self._cmd_log("[bold red]Error:[/bold red] Must 'use #channel' before creating a poll.")
                return
            if not self.bot:
                return
                
            full_args = " ".join(args)
            try:
                duration_str, rest = full_args.split(" ", 1)
                duration = int(duration_str)
                parts = [p.strip() for p in rest.split("|")]
                title = parts[0]
                choices = parts[1:]
                
                if len(choices) < 2:
                    self._cmd_log("[bold red]Error:[/bold red] A poll requires at least 2 choices separated by '|'.")
                    return
                if duration < 1:
                    self._cmd_log("[bold red]Error:[/bold red] Duration must be at least 1 minute.")
                    return
                    
                channel_name = self.current_context.lstrip('#')
                self.bot.loop.create_task(self.bot.create_poll_via_api(channel_name, title, choices, duration))
                self._cmd_log(f"[bold green]Spawning poll in {self.current_context}...[/bold green]")
            except Exception as e:
                self._cmd_log(f"[bold red]Format Error:[/bold red] poll <duration> <question> | <opt1> | <opt2> ...")
                
        elif cmd == 'tts':
            if not args or args[0].lower() not in ['on', 'off']:
                self._cmd_log("Usage: tts <on|off>")
                return
            state = 1 if args[0].lower() == 'on' else 0
            await self._update_setting('tts_enabled', state)

        elif cmd == 'voice':
            if not args or args[0].lower() not in ['on', 'off']:
                self._cmd_log("Usage: voice <on|off>")
                return
            state = 1 if args[0].lower() == 'on' else 0
            await self._update_setting('voice_enabled', state)

        elif cmd == 'model':
            if not args or args[0].lower() not in ['general', 'individual']:
                self._cmd_log("Usage: model <general|individual>")
                return
            state = 1 if args[0].lower() == 'general' else 0
            await self._update_setting('use_general_model', state)
            
        elif cmd == 'set':
            if len(args) < 2:
                self._cmd_log("Usage: set <lines|time|model|voice|bits|points> <val>")
                return
            key = args[0].lower()
            val_str = args[1].lower()

            if key == 'lines':
                try: val = int(val_str)
                except ValueError: self._cmd_log("[bold red]Error: Value must be a number.[/bold red]"); return
                await self._update_setting('lines_between_messages', val)
            elif key == 'time':
                try: val = int(val_str)
                except ValueError: self._cmd_log("[bold red]Error: Value must be a number.[/bold red]"); return
                await self._update_setting('time_between_messages', val)
            elif key == 'model':
                if val_str not in ['general', 'individual']:
                    self._cmd_log("Usage: set model <general|individual>")
                    return
                state = 1 if val_str == 'general' else 0
                await self._update_setting('use_general_model', state)
            elif key == 'chance':
                try: 
                    val = float(val_str)
                    if val < 0.0 or val > 100.0:
                        raise ValueError()
                except ValueError: 
                    self._cmd_log("[bold red]Error: Value must be a number between 0 and 100.[/bold red]"); return
                await self._update_setting('random_chance', val)
            elif key == 'log_dice':
                if val_str not in ['on', 'off', 'true', 'false']:
                    self._cmd_log("Usage: set log_dice <on|off>")
                    return
                state = 1 if val_str in ['on', 'true'] else 0
                await self._update_setting('log_dice', state)
            elif key == 'voice':
                if not args or len(args) < 2:
                    self._cmd_log("Usage: set voice <model_name>")
                    return
                actual_val = args[1]
                await self._update_setting('voice_preset', actual_val)
            elif key == 'delay':
                if val_str not in ['on', 'off', 'true', 'false']:
                    self._cmd_log("Usage: set delay <on|off>")
                    return
                state = 1 if val_str in ['on', 'true'] else 0
                await self._update_setting('tts_delay_enabled', state)
            elif key in ['bits', 'points']:
                if val_str not in ['on', 'off']:
                    self._cmd_log(f"Usage: set {key} <on|off>")
                    return
                state = 1 if val_str == 'on' else 0
                await self._update_setting(f'pubsub_{key}', state)
            else:
                self._cmd_log(f"[bold red]Unknown setting: {key}[/bold red]. Available: lines, time, chance, model, log_dice, voice, delay, bits, points.")

        elif cmd in ['trust', 'untrust', 'ignore', 'unignore']:
            if not args:
                self._cmd_log(f"Usage: {cmd} <username>")
                return
                
            username = args[0].lower()
            column = 'trusted_users' if cmd in ['trust', 'untrust'] else 'ignored_users'
            is_add = cmd in ['trust', 'ignore']
            
            db_file = self.bot.db_file
            try:
                import aiosqlite
                async with aiosqlite.connect(db_file) as conn:
                    c = await conn.cursor()
                    
                    if self.current_context == "Global":
                        # Apply across ALL channels
                        await c.execute(f"SELECT channel_name, {column} FROM channel_configs")
                        rows = await c.fetchall()
                        updated = 0
                        for ch_name, current_val in rows:
                            user_list = [u.strip() for u in (current_val or "").split(',') if u.strip()]
                            if is_add and username not in user_list:
                                user_list.append(username)
                                updated += 1
                            elif not is_add and username in user_list:
                                user_list.remove(username)
                                updated += 1
                            new_val = ",".join(user_list)
                            await c.execute(f"UPDATE channel_configs SET {column} = ? WHERE channel_name = ?", (new_val, ch_name))
                        await conn.commit()
                        action = "Added" if is_add else "Removed"
                        prep = "to" if is_add else "from"
                        self._cmd_log(f"[bold green]{action}[/bold green] {username} {prep} {column} globally ({updated} channels updated).")
                    else:
                        # Apply to current channel only
                        clean_name = self.current_context.lstrip('#')
                        await c.execute(f"SELECT {column} FROM channel_configs WHERE channel_name = ?", (clean_name,))
                        row = await c.fetchone()
                        if row is None:
                            self._cmd_log(f"[bold red]Error:[/bold red] Channel {clean_name} not found in database.")
                            return
                        
                        user_list = [u.strip() for u in (row[0] or "").split(',') if u.strip()]
                        
                        if is_add:
                            if username not in user_list:
                                user_list.append(username)
                                self._cmd_log(f"[bold green]Added[/bold green] {username} to {column} for {self.current_context}.")
                            else:
                                self._cmd_log(f"User {username} is already in {column} for {self.current_context}.")
                        else:
                            if username in user_list:
                                user_list.remove(username)
                                self._cmd_log(f"[bold green]Removed[/bold green] {username} from {column} for {self.current_context}.")
                            else:
                                self._cmd_log(f"User {username} is not in {column} for {self.current_context}.")
                                
                        new_val = ",".join(user_list)
                        await c.execute(f"UPDATE channel_configs SET {column} = ? WHERE channel_name = ?", (new_val, clean_name))
                        await conn.commit()
                self.bot.load_channel_settings()
            except Exception as e:
                self._cmd_log(f"[bold red]Database Error:[/bold red] {e}")

        elif cmd == 'ignorelist':
            if not self.bot:
                self._cmd_log("Bot instance not connected.")
                return
            try:
                import aiosqlite
                from rich.table import Table
                from rich import box
                async with aiosqlite.connect(self.bot.db_file) as conn:
                    c = await conn.cursor()
                    if self.current_context == "Global":
                        await c.execute("SELECT channel_name, ignored_users FROM channel_configs WHERE join_channel = 1 ORDER BY channel_name")
                    else:
                        clean_name = self.current_context.lstrip('#')
                        await c.execute("SELECT channel_name, ignored_users FROM channel_configs WHERE channel_name = ?", (clean_name,))
                    rows = await c.fetchall()
                    
                    # Collect unique ignored users across all channels
                    all_ignored = set()
                    per_channel = {}
                    for ch, ignored_str in rows:
                        users = [u.strip() for u in (ignored_str or "").split(',') if u.strip()]
                        per_channel[ch] = users
                        all_ignored.update(users)
                    
                    if not all_ignored:
                        self._cmd_log("[dim]No ignored users found.[/dim]")
                        return
                    
                    table = Table(
                        title="Ignored Users",
                        title_style="bold cyan",
                        box=box.ROUNDED,
                        border_style="dim",
                        header_style="bold white",
                        padding=(0, 1),
                    )
                    table.add_column("Channel", justify="left")
                    table.add_column("Ignored Users", justify="left")
                    
                    for ch, users in per_channel.items():
                        if users:
                            users_str = ", ".join(f"[yellow]{u}[/]" for u in sorted(users))
                            table.add_row(f"#{ch}", users_str)
                    
                    self._cmd_log(table)
            except Exception as e:
                self._cmd_log(f"[bold red]Database Error:[/bold red] {e}")

        elif cmd == 'join':
            if not args:
                self._cmd_log("Usage: join <#channel>")
                return
            target = args[0].lower().lstrip('#')
            try:
                await self.bot.join_channel(target)
                self._cmd_log(f"[bold green]Joined[/bold green] #{target} and added to channels.")
                self.update_sidebar()
            except Exception as e:
                self._cmd_log(f"[bold red]Failed to join {target}:[/bold red] {e}")

        elif cmd == 'part':
            if not args:
                self._cmd_log("Usage: part <#channel>")
                return
            target = args[0].lower().lstrip('#')
            try:
                success = await self.bot.leave_channel(target)
                if success:
                    import aiosqlite
                    async with aiosqlite.connect(self.bot.db_file) as conn:
                        await conn.execute("UPDATE channel_configs SET join_channel = 0 WHERE channel_name = ?", (target,))
                        await conn.commit()
                    self._cmd_log(f"[bold green]Left channel:[/bold green] #{target}")
                    self.update_sidebar()
                else:
                    self._cmd_log(f"The bot is not currently active in channel: #{target}.")
            except Exception as e:
                self._cmd_log(f"[bold red]Failed to part {target}:[/bold red] {e}")

        elif cmd == 'addc':
            if len(args) < 2:
                self._cmd_log("Usage: addc <cmd> <response>")
                return
            cmd_name = args[0].lower()
            if not cmd_name.startswith('!'): cmd_name = f"!{cmd_name}"
            response = " ".join(args[1:])
            target_chan = 'global' if self.current_context == 'Global' else self.current_context.lstrip('#')
            
            try:
                import sqlite3
                import aiosqlite
                async with aiosqlite.connect(self.bot.db_file) as conn:
                    c = await conn.cursor()
                    await c.execute(
                        "INSERT INTO custom_commands (channel_name, command_name, response_template) VALUES (?, ?, ?)",
                        (target_chan, cmd_name, response)
                    )
                    await conn.commit()
                self._cmd_log(f"[bold green]Added[/bold green] {cmd_name} to {target_chan}.")
            except sqlite3.IntegrityError:
                self._cmd_log(f"[bold red]Error:[/bold red] Command {cmd_name} already exists. Use editc.")
            except Exception as e:
                self._cmd_log(f"[bold red]Error:[/bold red] {e}")

        elif cmd == 'editc':
            if len(args) < 2:
                self._cmd_log("Usage: editc <cmd> <response>")
                return
            cmd_name = args[0].lower()
            if not cmd_name.startswith('!'): cmd_name = f"!{cmd_name}"
            response = " ".join(args[1:])
            target_chan = 'global' if self.current_context == 'Global' else self.current_context.lstrip('#')
            
            try:
                import aiosqlite
                async with aiosqlite.connect(self.bot.db_file) as conn:
                    c = await conn.cursor()
                    await c.execute(
                        "UPDATE custom_commands SET response_template = ? WHERE channel_name = ? AND command_name = ?",
                        (response, target_chan, cmd_name)
                    )
                    if c.rowcount > 0:
                        self._cmd_log(f"[bold green]Updated[/bold green] {cmd_name} in {target_chan}.")
                    else:
                        self._cmd_log(f"[bold red]Error:[/bold red] Command {cmd_name} not found in {target_chan}.")
                    await conn.commit()
            except Exception as e:
                self._cmd_log(f"[bold red]Error:[/bold red] {e}")

        elif cmd == 'delc':
            if len(args) < 1:
                self._cmd_log("Usage: delc <cmd>")
                return
            cmd_name = args[0].lower()
            if not cmd_name.startswith('!'): cmd_name = f"!{cmd_name}"
            target_chan = 'global' if self.current_context == 'Global' else self.current_context.lstrip('#')
            
            try:
                import aiosqlite
                async with aiosqlite.connect(self.bot.db_file) as conn:
                    c = await conn.cursor()
                    await c.execute(
                        "DELETE FROM custom_commands WHERE channel_name = ? AND command_name = ?",
                        (target_chan, cmd_name)
                    )
                    if c.rowcount > 0:
                        self._cmd_log(f"[bold green]Deleted[/bold green] {cmd_name} from {target_chan}.")
                    else:
                        self._cmd_log(f"[bold red]Error:[/bold red] Command {cmd_name} not found in {target_chan}.")
                    await conn.commit()
            except Exception as e:
                self._cmd_log(f"[bold red]Error:[/bold red] {e}")

        elif cmd == 'timer':
            if len(args) < 1:
                self._cmd_log("Usage: timer <add|del|msg|list> ...")
                return
                
            subcmd = args[0].lower()
            target_chan = 'global' if self.current_context == 'Global' else self.current_context.lstrip('#')
            
            try:
                import sqlite3
                import aiosqlite
                async with aiosqlite.connect(self.bot.db_file) as conn:
                    c = await conn.cursor()
                    
                    if subcmd == 'add':
                        if len(args) < 3:
                            self._cmd_log("Usage: timer add <pool_name> <interval_minutes>")
                            return
                        pool_name = args[1].lower()
                        try:
                            interval = int(args[2])
                        except ValueError:
                            self._cmd_log("[bold red]Error:[/bold red] Interval must be a number of minutes.")
                            return
                            
                        try:
                            await c.execute(
                                "INSERT INTO timed_message_pools (channel_name, pool_name, interval_minutes) VALUES (?, ?, ?)",
                                (target_chan, pool_name, interval)
                            )
                            await conn.commit()
                            self._cmd_log(f"[bold green]Created timer pool[/bold green] '{pool_name}' for {target_chan} (Interval: {interval}m).")
                        except sqlite3.IntegrityError:
                            self._cmd_log(f"[bold red]Error:[/bold red] Timer pool '{pool_name}' already exists in {target_chan}.")
                            
                    elif subcmd == 'del':
                        if len(args) < 2:
                            self._cmd_log("Usage: timer del <pool_name>")
                            return
                        pool_name = args[1].lower()
                        await c.execute(
                            "DELETE FROM timed_message_pools WHERE channel_name = ? AND pool_name = ?",
                            (target_chan, pool_name)
                        )
                        if c.rowcount > 0:
                            await conn.commit()
                            self._cmd_log(f"[bold green]Deleted timer pool[/bold green] '{pool_name}' from {target_chan}.")
                        else:
                            self._cmd_log(f"[bold red]Error:[/bold red] Timer pool '{pool_name}' not found in {target_chan}.")
                            
                    elif subcmd == 'msg':
                        if len(args) < 3:
                            self._cmd_log("Usage: timer msg <pool_name> <message...>")
                            return
                        pool_name = args[1].lower()
                        message_text = " ".join(args[2:])
                        
                        # Verify pool exists
                        await c.execute("SELECT 1 FROM timed_message_pools WHERE channel_name = ? AND pool_name = ?", (target_chan, pool_name))
                        if not await c.fetchone():
                            self._cmd_log(f"[bold red]Error:[/bold red] Timer pool '{pool_name}' not found in {target_chan}. Create it first with 'timer add'.")
                            return
                            
                        await c.execute(
                            "INSERT INTO timed_messages (pool_name, channel_name, message_text) VALUES (?, ?, ?)",
                            (pool_name, target_chan, message_text)
                        )
                        await conn.commit()
                        self._cmd_log(f"[bold green]Added message[/bold green] to timer pool '{pool_name}' in {target_chan}.")
                        
                    elif subcmd == 'list':
                        await c.execute(
                            "SELECT pool_name, interval_minutes FROM timed_message_pools WHERE channel_name = ?",
                            (target_chan,)
                        )
                        pools = await c.fetchall()
                        
                        if not pools:
                            self._cmd_log(f"No timer pools found for {target_chan}.")
                            return
                            
                        from rich.table import Table
                        from rich import box
                        table = Table(title=f"Timer Pools ({target_chan})", box=box.ROUNDED)
                        table.add_column("Pool Name", style="cyan")
                        table.add_column("Interval (m)", justify="right")
                        table.add_column("Messages", justify="right")
                        
                        for p_name, p_int in pools:
                            await c.execute("SELECT COUNT(*) FROM timed_messages WHERE channel_name = ? AND pool_name = ?", (target_chan, p_name))
                            msg_count = (await c.fetchone())[0]
                            table.add_row(p_name, str(p_int), str(msg_count))
                            
                        self._cmd_log(table)
                    else:
                        self._cmd_log(f"Unknown timer subcommand: {subcmd}. Use add, del, msg, or list.")
                        
            except Exception as e:
                self._cmd_log(f"[bold red]Timer Error:[/bold red] {e}")

        elif cmd == 'grammar':
            if len(args) < 2:
                self._cmd_log("Usage: grammar <add|list|clear> <rule> [text]")
                return
            action = args[0].lower()
            rule = args[1].lower()
            text = " ".join(args[2:])
            target_chan = 'global' if self.current_context == 'Global' else self.current_context.lstrip('#')
            
            try:
                import json
                import aiosqlite
                async with aiosqlite.connect(self.bot.db_file) as conn:
                    c = await conn.cursor()
                    await c.execute("SELECT options_json FROM custom_grammar WHERE channel_name = ? AND rule_name = ?", (target_chan, rule))
                    row = await c.fetchone()
                    options = json.loads(row[0]) if row else []
                    
                    if action == 'add':
                        if not text:
                            self._cmd_log("Please provide text.")
                            return
                        options.append(text)
                        if row:
                            await c.execute("UPDATE custom_grammar SET options_json = ? WHERE channel_name = ? AND rule_name = ?", (json.dumps(options), target_chan, rule))
                        else:
                            await c.execute("INSERT INTO custom_grammar (channel_name, rule_name, options_json) VALUES (?, ?, ?)", (target_chan, rule, json.dumps(options)))
                        self._cmd_log(f"[bold green]Added[/bold green] '{text}' to #{rule}# in {target_chan}.")
                    elif action == 'list':
                        if not options: self._cmd_log(f"Rule #{rule}# empty.")
                        else: self._cmd_log(f"Rule #{rule}# options: {', '.join(options)}")
                    elif action == 'clear':
                        await c.execute("DELETE FROM custom_grammar WHERE channel_name = ? AND rule_name = ?", (target_chan, rule))
                        self._cmd_log(f"[bold green]Cleared[/bold green] rule #{rule}# from {target_chan}.")
                    await conn.commit()
            except Exception as e:
                self._cmd_log(f"[bold red]Error:[/bold red] {e}")

        elif cmd == 'compile':
            if not self.bot:
                self._cmd_log("Bot instance not connected.")
                return
            
            context = self.current_context
            if context == "Global":
                self._cmd_log("[bold yellow]Compiling General Markov Model and all active channels...[/bold yellow]")
            else:
                self._cmd_log(f"[bold yellow]Compiling brain cache tailored for {context}...[/bold yellow]")

            import threading
            def _compile():
                try:
                    original_rebuild = self.bot.rebuild_cache
                    self.bot.rebuild_cache = True
                    target = "Global" if context == "Global" else context.lstrip('#')
                    self.bot.load_text_and_build_model(create_individual_caches=True, target_channel=target)
                    self.bot.rebuild_cache = original_rebuild
                    
                    if context == "Global":
                        self._cmd_log("[bold green]General Model & Channel caches compiled successfully![/bold green]")
                    else:
                        self._cmd_log(f"[bold green]Brain cache for {context} compiled successfully![/bold green]")
                except Exception as e:
                    self._cmd_log(f"[bold red]Error compiling caches:[/bold red] {e}")
            threading.Thread(target=_compile).start()

        elif cmd == 'help':
            from rich.table import Table
            from rich import box
            table = Table(
                title="Available Commands",
                title_style="bold cyan",
                show_header=False,
                box=box.SIMPLE,
                border_style="dim",
                padding=(0, 1),
            )
            
            # Format: (Command, Description)
            commands = [
                ("[green]status[/]", "Show status table (context-aware)"),
                ("[green]use \\[channel][/]", "Switch context (empty clears to global)"),
                ("[green]join <#channel>[/]", "Join a channel"),
                ("[green]part <#channel>[/]", "Leave a channel"),
                ("[green]say <message>[/]", "Send chat (in channel context)"),
                ("[green]tts <on|off>[/]", "Toggle TTS for current context"),
                ("[green]voice <on|off>[/]", "Toggle Voice for current context"),
                ("[green]trust <user>[/]", "Add user to trusted users (allows command usage)"),
                ("[green]untrust <user>[/]", "Remove user from trusted users"),
                ("[green]ignore <user>[/]", "Ignore user (global context = all channels)"),
                ("[green]unignore <user>[/]", "Unignore user (global context = all channels)"),
                ("[green]ignorelist[/]", "Show ignored users per channel"),
                ("[green]timer <add|del|msg|list>[/]", "Manage scheduled message pools for this channel"),
                ("[green]model <gen|indivi>[/]", "Toggle Markov model type (general/individual)"),
                ("[green]set <key> <val>[/]", "Set config (keys: lines, time, chance, model, log_dice, voice, delay, bits...)"),
                ("[green]poll <args>[/]", "Create a poll (e.g. poll 5 Yes/No? | Yes | No)"),
                ("[green]addc <cmd> <rsp>[/]", "Add custom command (use <sender> <streamer> <input>)"),
                ("[green]editc <cmd> <rsp>[/]", "Edit custom command"),
                ("[green]delc <cmd>[/]", "Delete custom command"),
                ("[green]grammar <action>[/]", "Manage grammar (add, list, clear) <rule> [text]"),
                ("[green]compile[/]", "Force rebuild of all JSON Brain caches synchronously"),
                ("[green]brain, stats[/]", "Show number of lines loaded per channel"),
                ("[green]quit, exit, q[/]", "Exit bot")
            ]
            
            table.add_column("Command", style="bold")
            table.add_column("Description", style="dim italic")
            
            for cmd_str, desc in commands:
                table.add_row(cmd_str, desc)
                
            self._cmd_log(table)

        else:
            self._cmd_log(f"[italic]Unknown command:[/italic] {cmd}")

    async def _update_setting(self, column, value):
        if not self.bot:
            return
            
        db_file = self.bot.db_file
        try:
            import aiosqlite
            async with aiosqlite.connect(db_file) as conn:
                c = await conn.cursor()
                if self.current_context == "Global":
                    await c.execute(f"UPDATE channel_configs SET {column} = ?", (value,))
                    self.write_log(f"[bold green]Updated[/bold green] {column} globally to {value}")
                else:
                    clean_name = self.current_context.lstrip('#')
                    await c.execute(f"UPDATE channel_configs SET {column} = ? WHERE channel_name = ?", (value, clean_name))
                    self.write_log(f"[bold green]Updated[/bold green] {column} for {self.current_context} to {value}")
                await conn.commit()
            self.bot.load_channel_settings() # Reload settings into memory
        except Exception as e:
            self.write_log(f"[bold red]Database Error:[/bold red] {e}")

    def update_sidebar(self):
        asyncio.create_task(self._async_update_sidebar())

    async def _async_update_sidebar(self):
        """Refresh the list of channels in the sidebar."""
        try:
            sidebar = self.query_one("#channel_sidebar", ListView)
        except Exception:
            return
            
        current_index = sidebar.index
        await sidebar.clear()
        
        # Add Global and System contexts first
        sidebar.append(ListItem(Label("🌐 Global (All Chat)"), id="ctx_global"))
        sidebar.append(ListItem(Label("⚙️ System (Bot Logs)"), id="ctx_system"))

        # Add channels from the database
        if self.bot:
            try:
                import aiosqlite
                async with aiosqlite.connect(self.bot.db_file) as conn:
                    c = await conn.cursor()
                    await c.execute("SELECT channel_name FROM channel_configs WHERE join_channel = 1 ORDER BY channel_name")
                    rows = await c.fetchall()
                    live_streamers = getattr(self.bot, 'live_streamers', set())
                    
                    for row in rows:
                        clean_name = row[0]
                        
                        color_idx = "white"
                        if hasattr(self.bot, 'my_logger'):
                            color_idx = self.bot.my_logger.color_manager.get_channel_color(clean_name)
                        
                        if isinstance(color_idx, str) and color_idx.isdigit():
                            chan_style = f"bold color({color_idx})"
                        else:
                            chan_style = f"bold {color_idx}"

                        if clean_name.lower() in live_streamers:
                            status_label = Label("[green][*][/]", classes="channel-status")
                        else:
                            status_label = Label("[red][*][/]", classes="channel-status")
                            
                        item = ListItem(
                            Horizontal(
                                Label(f"#[{chan_style}]{clean_name}[/]", classes="channel-name"),
                                status_label,
                                classes="channel-container"
                            ),
                            id=f"ctx_{clean_name}"
                        )
                        await sidebar.append(item)
            except Exception:
                pass
                
        if current_index is not None and current_index < len(sidebar.children):
            sidebar.index = current_index


        self.update_prompt()
        self._repopulate_log()

    async def on_list_view_selected(self, event: ListView.Selected):
        item_id = event.item.id
        if item_id == "ctx_global":
            self.current_context = "Global"
        elif item_id == "ctx_system":
            self.current_context = "System"
        else:
            self.current_context = f"#{item_id.replace('ctx_', '')}"
            

        if self.bot and hasattr(self.bot, 'my_logger'):
            if self.current_context in ("Global", "System"):
                self.bot.my_logger.active_channel_filter = None
            else:
                self.bot.my_logger.active_channel_filter = self.current_context.lstrip('#')
            
        self.update_prompt()
        self._repopulate_log()

if __name__ == "__main__":
    app = MockbotDashboard()
    app.run()
