import asyncio
from collections import defaultdict, deque
import time
import os
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, RichLog, Static, ListView, ListItem, Label, Button, TextArea, Sparkline
from textual.containers import Container, Horizontal, VerticalScroll, Vertical
from textual import work
from datetime import datetime
from textual.events import Key, Resize

# Flat dark "IRC" theme — replaces Textual's stock palette (no purple). Because the
# CSS is written against theme tokens ($primary/$accent/$surface/...), swapping the
# theme reskins the whole app. Tune the hexes here to taste.
# Guarded: the Theme API (textual.theme) only exists in Textual >= 0.86; on older
# versions this stays None and the app falls back to the stock theme rather than
# failing to import.
try:
    from textual.theme import Theme
    MOCKBOT_THEME = Theme(
        name="mockbot",
        dark=True,
        background="#0c0f14",   # near-black canvas
        surface="#161b22",      # inputs / modal bodies
        panel="#0f141b",        # sidebar / status strips
        primary="#3b6ea5",      # muted IRC blue — separators / status accents
        secondary="#2aa198",    # teal
        accent="#56b6c2",       # cyan — focus / highlights
        foreground="#c5c8c6",
        success="#98c379",
        warning="#e5c07b",
        error="#e06c75",
    )
except Exception:
    MOCKBOT_THEME = None
from bot.ui_managers import CommandsManagerScreen, GrammarManagerScreen, SettingsManagerScreen, TimersManagerScreen, TTSHistoryScreen

MAX_BUFFER = 500  # Maximum messages to keep per channel buffer

class CommandInput(TextArea):
    """Custom Input widget that supports command history and basic tab completion."""
    def __init__(self, *args, **kwargs):
        kwargs.pop('placeholder', None) # TextArea doesn't guarantee placeholder support
        super().__init__(*args, **kwargs)
        self.show_line_numbers = False
        self.wrap = True
        self.cmd_history = []
        self.cmd_history_index = -1
        self.temp_value = "" # Store what user was typing before browsing history
        self.autocomplete_options = [
            "/use ", "/status", "/clear", "/quit",
            "/join ", "/part ",
            "/say ", "/speak ", "/testvoice ", "/poll ",
            "/trust ", "/untrust ", "/ignore ", "/unignore ", "/ignorelist",
            "/set ", "/tts ", "/voice ", "/model ", "/timer ",
            "/addc ", "/editc ", "/delc ", "/grammar ",
            "/brain", "/stats", "/compile",
            "/settings", "/commands", "/timers", "/ttshistory", "/ttskill",
            "/help",
        ]
        self.tab_index = -1
        self.last_tab_base = ""

    def add_history(self, command: str):
        if not command or (self.cmd_history and self.cmd_history[-1] == command):
            return
        self.cmd_history.append(command)
        self.cmd_history_index = len(self.cmd_history)
        self.temp_value = ""
        self.tab_index = -1

    async def on_key(self, event: Key):
        # Handle Enter for submit, Shift+Enter for newline
        if event.key == "enter":
            event.prevent_default()
            command_text = self.text.strip()
            if command_text:
                self.add_history(command_text)
                
                dashboard = self.app
                dashboard._cmd_log(f"> [bold cyan]{command_text}[/bold cyan]")
                
                # Clear the input
                self.text = ""
                
                # Process the command (mirrors the old interactive.py logic)
                dashboard.run_worker(dashboard.handle_command(command_text))
                
                # Trigger an async refresh of the top dashboard bar
                dashboard.update_status_bar()
        elif event.key == "shift+enter" or event.key == "ctrl+j":
            event.prevent_default()
            self.insert("\n")
            
        # Handle Up/Down for history
        elif event.key == "up":
            event.prevent_default()
            if self.cmd_history_index == len(self.cmd_history):
                self.temp_value = self.text
            
            if self.cmd_history_index > 0:
                self.cmd_history_index -= 1
                self.text = self.cmd_history[self.cmd_history_index]
                self.action_cursor_line_end()
        elif event.key == "down":
            event.prevent_default()
            if self.cmd_history_index < len(self.cmd_history) - 1:
                self.cmd_history_index += 1
                self.text = self.cmd_history[self.cmd_history_index]
                self.action_cursor_line_end()
            elif self.cmd_history_index == len(self.cmd_history) - 1:
                self.cmd_history_index = len(self.cmd_history)
                self.text = self.temp_value
                self.action_cursor_line_end()
        
        # Handle Tab for autocompletion
        elif event.key == "tab":
            event.prevent_default()
            if self.tab_index == -1:
                words = self.text.split()
                self.last_tab_base = words[-1] if words else ""
            
            # Find matches
            options = list(self.autocomplete_options)
            if hasattr(self.app, 'bot') and self.app.bot:
                options.extend([f"#{ch}" for ch in self.app.bot._joined_channels])
                
            matches = [opt for opt in options if opt.startswith(self.last_tab_base.lower())]
            if matches:
                self.tab_index = (self.tab_index + 1) % len(matches)
                words = self.text.split()
                if not words:
                    words = [""]
                words[-1] = matches[self.tab_index]
                self.text = " ".join(words)
                self.action_cursor_line_end()
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
    
    #top_bar {
        dock: top;
        height: auto;
        width: 100%;
        background: $boost;
    }
    
    #status_bar {
        width: 100%;
        padding: 0 1;
        color: $text-muted;
        text-style: bold;
    }
    
    #log_container {
        height: 1fr;
        padding: 0;
        background: transparent;
    }
    
    #sys_monitor {
        height: 1;
        width: 100%;
        color: $text-muted;
        padding: 0 1;
        text-style: bold;
    }
    
    RichLog {
        height: 100%;
        width: 100%;
        background: transparent;
        scrollbar-background: transparent;
    }
    
    .hidden {
        display: none;
    }
    
    #input_container {
        dock: bottom;
        height: auto;
        min-height: 1;
        max-height: 10;
        width: 100%;
        background: transparent;
    }
    
    #input_prefix {
        width: auto;
        padding: 0 1 0 0;
        text-style: bold;
        color: $accent;
    }
    
    #command_input {
        width: 1fr;
        height: 100%;
        border: none;
        background: $surface;
        padding: 0 1;
    }
    
    #command_input:focus {
        border: none;
        background: $surface-lighten-2;
        color: $text;
    }
    
    #channel_sidebar {
        dock: left;
        width: 24;
        height: 100%;
        background: $panel;
        border-right: solid $primary;
    }

    Footer {
        background: $panel;
        color: $text-muted;
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

    .manager-title {
        dock: top;
        padding: 1 2;
        background: $primary-background;
        color: $text;
        text-style: bold;
        width: 100%;
        content-align: center middle;
    }
    
    .manager-help {
        padding: 1 2;
        background: $surface;
        color: $text-muted;
        border-bottom: solid $primary;
        height: auto;
    }
    
    .manager-actions {
        dock: bottom;
        height: 3;
        padding: 0 1;
        width: 100%;
    }

    .manager-status {
        dock: bottom;
        height: 1;
        width: 100%;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
    }
    
    .manager-actions > Input {
        width: 2fr;
    }
    
    .manager-actions > Button {
        width: 1fr;
    }

    #settings_list {
        height: 1fr;
        padding: 1;
        overflow-y: auto;
    }
    
    SettingRow {
        layout: horizontal;
        height: auto;
        padding: 0 1;
        border-bottom: solid $primary-background;
    }
    
    .setting-info {
        width: 3fr;
        height: auto;
    }
    
    .setting-key {
        text-style: bold;
        color: $accent;
    }
    
    .setting-desc {
        color: $text-muted;
        height: auto;
    }
    
    .setting-category {
        text-style: bold;
        color: $primary;
        width: 100%;
        content-align: center middle;
        padding: 1 0 0 0;
    }
    
    .setting-controls {
        width: 2fr;
        height: auto;
        align: right middle;
        layout: horizontal;
    }
    
    .setting-controls > Input, .setting-controls > Select {
        width: 28;
        margin-right: 1;
    }
    
    .manager-container Input, .setting-controls > Input {
        border: none;
        background: $primary-background;
        padding: 0 1;
        min-height: 1;
        height: 1;
    }

    .setting-controls > Select {
        border: none;
        height: 1;
        min-height: 1;
        background: $primary-background;
        color: $text;
    }
    
    .setting-controls > Select > SelectCurrent {
        border: none;
        background: $primary-background;
        color: $text;
    }

    .manager-container Input:focus, .setting-controls > Input:focus {
        background: $surface-lighten-2;
    }

    .manager-container Button, .setting-controls > Button {
        border: none;
        background: $primary;
        color: $background;
        min-width: 10;
        min-height: 1;
        height: 1;
        padding: 0 2;
        content-align: center middle;
    }

    .manager-container Button:hover, .setting-controls > Button:hover {
        background: $accent;
    }
    
    .manager-container Button.-success {
        background: $success;
        color: $background;
    }
    
    .manager-container Button.-success:hover {
        background: $success-lighten-2;
    }

    .manager-container Button.-error {
        background: $error;
        color: $background;
    }
    
    .manager-container Button.-error:hover {
        background: $error-lighten-2;
    }

    #commands_table, #grammar_table, #timers_table {
        height: 1fr;
        padding: 0 1;
    }

    #tts_table {
        height: 1fr;
        padding: 0 1;
        margin-bottom: 1;
    }
    
    .tts-preview {
        height: 6;
        border: solid $primary;
        margin: 0 1 1 1;
        background: $surface-lighten-1;
    }

    ModalScreen {
        align: center middle;
        background: $background 80%;
    }
    
    .manager-container {
        width: 80%;
        height: 80%;
        background: $surface;
        border: solid $primary;
    }
    """
    
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear_log", "Clear Log"),
        ("f1", "manage_settings", "Settings"),
        ("f2", "manage_commands", "Commands"),
        ("f3", "manage_grammar", "Grammar"),
        ("f4", "manage_timers", "Timers"),
        ("f5", "toggle_events", "Events (F5)"),
        ("f6", "kill_tts", "Kill TTS"),
        ("f7", "manage_tts_history", "TTS History")
    ]

    def __init__(self, bot=None):
        super().__init__()
        self.bot = bot
        self._thread_id = __import__("threading").get_ident()
        self.log_widget = None
        self.event_log_widget = None
        self.current_context = "Global"
        self.log_buffers = defaultdict(list)
        self.global_interleaved_buffer = []
        self.cpu_history = deque([0.0]*15, maxlen=15)
        self.ram_history = deque([0.0]*15, maxlen=15)
        
    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        with Vertical(id="top_bar"):
            yield Static("🔌 [bold]Mockbot[/bold] | Global Context", id="status_bar")
            yield Static("CPU: 0.0% | RAM: 0.0%", id="sys_monitor")
            
        yield Footer()
        yield ListView(id="channel_sidebar")
        
        with Horizontal(id="input_container"):
            yield Static("mockbot >", id="input_prefix")
            yield CommandInput(id="command_input")
            
        with Container(id="log_container"):
            self.log_widget = RichLog(highlight=False, markup=True, wrap=True, id="main_log")
            self.event_log_widget = RichLog(highlight=False, markup=True, wrap=True, id="event_log")
            self.event_log_widget.add_class("hidden")
            yield self.log_widget
            yield self.event_log_widget

    def on_mount(self) -> None:
        """Called when app starts."""
        # Activate the flat dark IRC theme before anything renders (no-op on
        # Textual versions that predate the theme API).
        if MOCKBOT_THEME is not None:
            try:
                self.register_theme(MOCKBOT_THEME)
                self.theme = "mockbot"
            except Exception:
                pass

        self.write_log("[bold green]Mockbot Dashboard Initialized![/bold green]")
        self.write_log("Type '/help' for commands, or '/use #channel' to switch context.")
        
        # Set up a live clock to refresh the status bar periodically
        self.set_interval(5.0, self.update_status_bar)
        
        # Periodically refresh the sidebar to pick up Live Stream status changes
        self.set_interval(60.0, self.update_sidebar)
        
        # Periodically refresh the system health (every 3 seconds)
        self.set_interval(3.0, self.update_system_health)

        # Focus the input field immediately
        self.update_prompt()
        self.update_sidebar()
        self.query_one(CommandInput).focus()

    def on_resize(self, event: Resize) -> None:
        """Handle terminal resize gracefully by repopulating the wrapped RichLog component."""
        timer = getattr(self, "_resize_timer", None)
        if timer is not None:
            timer.stop()
        self._resize_timer = self.set_timer(0.3, self._repopulate_log)

    def write_event(self, message: str) -> None:
        """Write strictly to the background Event/Moderation Feed."""
        if self.event_log_widget:
            timestamp = datetime.now().strftime("%H:%M:%S")
            try:
                self.call_from_thread(self.event_log_widget.write, f"[dim]{timestamp}[/dim] {message}")
            except Exception:
                self.event_log_widget.write(f"[dim]{timestamp}[/dim] {message}")

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
            
        self.global_interleaved_buffer.append((buf_key, message))
        if len(self.global_interleaved_buffer) > (MAX_BUFFER * 5):
            self.global_interleaved_buffer = self.global_interleaved_buffer[-(MAX_BUFFER * 5):]
        
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

    def notify(self, message: str, *, title: str = "", severity: str = "information",
               timeout=None, **kwargs) -> None:
        """IRC-style feedback: no default toasts. Route to the active screen's
        status line (`.manager-status`) if it has one, else to the command log."""
        icon = {"information": "[green]✓[/]", "info": "[green]✓[/]",
                "warning": "[yellow]![/]", "error": "[red]✗[/]"}.get(severity, "")
        text = f"{icon} {message}".strip()
        status = None
        try:
            status = self.screen.query(".manager-status").first()
        except Exception:
            status = None
        if status is not None:
            status.update(text)
        else:
            self._cmd_log(text)

    def _repopulate_log(self):
        """Clear and refill the RichLog widget based on the current context."""
        if not self.log_widget:
            return
        self.log_widget.clear()
        
        if self.current_context == "Global":
            # Global: Read from the sequential interleaved buffer directly
            for key, msg in self.global_interleaved_buffer:
                if key == "global":
                    self.log_widget.write(self._render_msg(msg))
                else:
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
        if self.event_log_widget:
            self.event_log_widget.clear()
            self._cmd_log("[italic]Log cleared.[/italic]")
            
    def action_toggle_events(self) -> None:
        """Swap visibility between main chat log and event log."""
        if self.log_widget.has_class("hidden"):
            self.log_widget.remove_class("hidden")
            self.event_log_widget.add_class("hidden")
        else:
            self.log_widget.add_class("hidden")
            self.event_log_widget.remove_class("hidden")
            self.write_event("[cyan]Switched to isolated Event Feed view.[/cyan]")
            
    def update_system_health(self) -> None:
        try:
            load = min(100.0, (os.getloadavg()[0] / max(1, os.cpu_count())) * 100)
            self.cpu_history.append(load)
        except Exception:
            self.cpu_history.append(0.0)
            
        try:
            with open('/proc/meminfo', 'r') as f:
                lines = f.readlines()
            mem_total = int(lines[0].split()[1])
            mem_avail = mem_total
            for line in lines:
                if line.startswith('MemAvailable:'):
                    mem_avail = int(line.split()[1])
                    break
            mem_avail_pct = 100.0 * (1.0 - (mem_avail / mem_total))
            self.ram_history.append(mem_avail_pct)
        except Exception:
            self.ram_history.append(0.0)
            
        try:
            self.query_one("#sys_monitor").update(f"CPU: {self.cpu_history[-1]:.1f}% | RAM: {self.ram_history[-1]:.1f}%")
        except Exception:
            pass

    def action_manage_settings(self) -> None:
        if self.current_context in ("Global", "System"):
            self._cmd_log("[bold red]Error:[/bold red] Cannot manage settings in Global/System context. Use 'use #channel' first.")
            return
        self.push_screen(SettingsManagerScreen())

    def action_manage_commands(self) -> None:
        if self.current_context in ("Global", "System"):
            self._cmd_log("[bold red]Error:[/bold red] Cannot manage commands in Global/System context. Use 'use #channel' first.")
            return
        self.push_screen(CommandsManagerScreen())

    def action_manage_grammar(self) -> None:
        if self.current_context in ("Global", "System"):
            self._cmd_log("[bold red]Error:[/bold red] Cannot manage grammar in Global/System context. Use 'use #channel' first.")
            return
        self.push_screen(GrammarManagerScreen())

    def action_manage_timers(self) -> None:
        if self.current_context in ("Global", "System"):
            self._cmd_log("[bold red]Error:[/bold red] Cannot manage timers in Global/System context. Use 'use #channel' first.")
            return
        self.push_screen(TimersManagerScreen())

    def action_manage_tts_history(self) -> None:
        self.push_screen(TTSHistoryScreen())

    def action_kill_tts(self) -> None:
        try:
            from bot.tts import clear_tts_queue
            clear_tts_queue()
            self._cmd_log("[bold red]🛑 Sent kill switch command to active TTS Audio and backend queue![/bold red]")
        except Exception as e:
            self._cmd_log(f"[bold red]Failed to issue TTS Kill signal: {e}[/bold red]")

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
        
        time_text = Text(f"[{msg_obj['timestamp']}] ", style="dim")
        tags = msg_obj.get('tags', '').strip('[] ')
        if tags == "BLK":
            time_text.append("BLK ", style="bold red")
        elif tags == "BOT":
            time_text.append("BOT ", style="bold magenta")
        elif tags == "LOG":
            time_text.append("LOG ", style="bold green")
        elif tags:
            time_text.append(f"{tags} ", style="dim")
        
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
            msg_text = Text.from_markup(msg_obj['message'], style="magenta italic")
        else:
            if isinstance(user_color, str) and user_color.isdigit():
                user_style = f"bold color({user_color})"
            else:
                user_style = f"bold {user_color}" if user_color else "bold"
            user_text = Text(f"<{msg_obj['username']}>", style=user_style)
            msg_text = Text.from_markup(msg_obj['message'], style="italic")
            
        if chan_text:
            table.add_row(time_text, chan_text, user_text, msg_text)
        else:
            table.add_row(time_text, user_text, msg_text)
            
        return table


        
    def update_prompt(self):
        """Update the input placeholder when context changes."""
        try:
            prefix = self.query_one("#input_prefix")
            prefix.update(f"mockbot ({self.current_context})>")
        except Exception:
            pass
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
            cfg = await self.bot.db.get_channel_config(clean_name)
            if cfg:
                voice = cfg.get("voice_enabled")
                tts = cfg.get("tts_enabled")
                time_b = cfg.get("time_between_messages", 0)
                chance = cfg.get("random_chance", 0.0)
                model = cfg.get("use_general_model")
                t_str = "[green]ON[/]" if tts else "[red]OFF[/]"
                v_str = "[green]ON[/]" if voice else "[red]OFF[/]"
                m_str = "[green]General[/]" if model else "[magenta]Indiv[/]"
                c_str = f"[cyan]{chance}%[/]"
                is_joined = "🟢" if f"#{clean_name}" in self.bot._joined_channels else "🔴"
                status_text = f"{is_joined} [bold]#{clean_name}[/bold] | Model: {m_str} | TTS: {t_str} | Voice: {v_str} | Chance: {c_str} | Delay: {time_b}s | [dim]http://localhost:5050/overlay/{clean_name}[/dim]"
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
        # Accept an optional leading slash so every command shares one IRC-style
        # convention (/cmd) while bare forms keep working.
        if len(cmd) > 1 and cmd.startswith('/'):
            cmd = cmd[1:]
        args = parts[1:]

        if cmd in ['quit', 'exit', 'q']:
            self._cmd_log("[bold red]Shutting down...[/bold red]")
            if self.bot:
                await self.bot.close()
            self.exit()

        elif cmd == 'clear':
            self.action_clear_log()

        elif cmd == 'commands':
            self.action_manage_commands()

        elif cmd == 'timers':
            self.action_manage_timers()

        elif cmd == 'ttskill':
            self.action_kill_tts()

        elif cmd == 'ttshistory':
            self.action_manage_tts_history()

        elif cmd == 'settings':
            self.action_manage_settings()
            
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
                self._cmd_log("Usage: [bold yellow]say <message>[/bold yellow]")
                return
            message = " ".join(args)
            channel_name = self.current_context.lstrip('#').lower()
            if self.bot:
                # Route through the unified message queue to ensure proper rate limits and DB logging
                success = await self.bot.send_message_to_channel(channel_name, message)
                
                if success:
                    # Surface the message in the TUI stream log (is_bot_message=True prevents it from adding to Markov DB)
                    if hasattr(self.bot, 'my_logger'):
                        self.bot.my_logger.log_message(channel_name, self.bot.nick, message, is_bot_message=True)
                else:
                    self._cmd_log(f"[bold red]Error:[/bold red] Failed to send message to Twitch channel {channel_name}.")
                
        elif cmd == 'speak':
            if self.current_context in ("Global", "System"):
                self._cmd_log("[bold red]Error:[/bold red] Cannot 'speak' in Global context. Use 'use #channel' first.")
                return
            if not self.bot:
                self._cmd_log("Bot instance not connected.")
                return
            channel_name = self.current_context.lstrip('#').lower()
            # Optional seed: `speak <word...>` forces the message to start with it.
            seed = " ".join(args) if args else None
            try:
                msg = self.bot.generate_message(channel_name, seed=seed)
                if msg:
                    # In TUI speak, original_message_id is not applicable since it's forced
                    # Queue the response immediately
                    success = await self.bot.send_message_to_channel(channel_name, msg)
                    if success:
                        self._cmd_log(f"[bold green]Forcing bot to speak in {self.current_context}...[/bold green]")
                        if hasattr(self.bot, 'my_logger'):
                            self.bot.my_logger.log_message(channel_name, self.bot.nick, msg, is_bot_message=True)
                            
                        # Now optionally generate TTS if the bot has it enabled
                        if getattr(self.bot, 'enable_tts', False):
                            # Check channel configs for TTS settings
                            try:
                                tts_cfg_sync = self.bot.db.get_tts_config_sync(channel_name)
                                tts_enabled = tts_cfg_sync.get("tts_enabled", False)
                                tts_delay_enabled = tts_cfg_sync.get("tts_delay_enabled", False)
                                
                                if tts_enabled:
                                    from bot.tts import start_tts_processing
                                    from datetime import datetime
                                    
                                    # Fallback simple call if TTS delay was meant to be used, we ignore it here because it's a CLI force command
                                    # and we already sent the message. 
                                    original_timestamp_str = datetime.now().strftime("%Y%m%d-%H%M%S")
                                    
                                    start_tts_processing(
                                        input_text=msg,
                                        channel_name=channel_name,
                                        message_id="cli-forced-speak", 
                                        timestamp_str=original_timestamp_str,
                                        db_file=self.bot.db_file
                                    )
                                    self._cmd_log(f"[italic]Queued TTS task for {channel_name}[/italic]")
                            except Exception as e:
                                self._cmd_log(f"[bold red]Error starting TTS:[/bold red] {e}")

                    else:
                        self._cmd_log(f"[bold red]Error:[/bold red] Failed to send message to Twitch channel {channel_name}.")
                else:
                    self._cmd_log(f"[bold red]Error:[/bold red] Failed to generate message for {self.current_context}.")
            except Exception as e:
                self._cmd_log(f"[bold red]Error:[/bold red] {e}")

        elif cmd == 'testvoice':
            if self.current_context in ("Global", "System"):
                self._cmd_log("[bold red]Error:[/bold red] Cannot run testvoice in Global context. Use 'use #channel' first.")
                return
                
            channel_name = self.current_context.lstrip('#').lower()
            
            if len(args) < 1:
                test_text = self.bot.generate_message(channel_name)
                if not test_text:
                    self._cmd_log(f"[bold red]Error:[/bold red] Failed to generate Markov message for {self.current_context}.")
                    return
                self._cmd_log(f"🧬 Generated Markov string: '{test_text}'")
            else:
                test_text = " ".join(args)
                
            self._cmd_log(f"🧠 Pushing '{test_text}' natively to the TTS Engine without sending to Twitch...")
            
            try:
                from bot.tts import process_text
                import asyncio
                import subprocess
                
                async def _run_testvoice():
                    try:
                        def _blocking_wrapper():
                            from bot.tts import process_text_thread
                            import uuid, os
                            from datetime import datetime
                            msg_id = "test_" + str(uuid.uuid4())[:8]
                            out_dir = f"static/outputs/{channel_name}"
                            os.makedirs(out_dir, exist_ok=True)
                            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                            f_path = f"{out_dir}/{channel_name}_{msg_id}_{ts}.wav"
                            audio_file, tts_id = process_text_thread(test_text, channel_name, full_path=f_path, message_id=msg_id)
                            return audio_file is not None, audio_file
                            
                        success, audio_file = await asyncio.to_thread(_blocking_wrapper)
                        
                        if success and audio_file:
                            self._cmd_log(f"[bold green]Testvoice generated safely. Playing audio natively...[/bold green]")
                            subprocess.Popen(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", audio_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        else:
                            self._cmd_log(f"[bold red]Testvoice generated nothing.[/bold red]")
                    except Exception as e:
                        self._cmd_log(f"[bold red]Testvoice thread crash:[/bold red] {e}")

                if self.bot.loop:
                    self.bot.loop.create_task(_run_testvoice())
            except Exception as e:
                self._cmd_log(f"[bold red]Failed to execute testvoice:[/bold red] {e}")
                
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
                self._cmd_log("Usage: [bold yellow]tts <on|off>[/bold yellow]")
                return
            state = 1 if args[0].lower() == 'on' else 0
            await self._update_setting('tts_enabled', state)

        elif cmd == 'voice':
            if not args or args[0].lower() not in ['on', 'off']:
                self._cmd_log("Usage: [bold yellow]voice <on|off>[/bold yellow]")
                return
            state = 1 if args[0].lower() == 'on' else 0
            await self._update_setting('voice_enabled', state)

        elif cmd == 'model':
            if not args or args[0].lower() not in ['general', 'individual']:
                self._cmd_log("Usage: [bold yellow]model <general|individual>[/bold yellow]")
                return
            state = 1 if args[0].lower() == 'general' else 0
            await self._update_setting('use_general_model', state)
            
        elif cmd == 'set':
            if len(args) < 2:
                self._cmd_log("Usage: [bold yellow]set <lines|time|model|voice|bits|points> <val>[/bold yellow]")
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
                    self._cmd_log("Usage: [bold yellow]set model <general|individual>[/bold yellow]")
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
                    self._cmd_log("Usage: [bold yellow]set log_dice <on|off>[/bold yellow]")
                    return
                state = 1 if val_str in ['on', 'true'] else 0
                await self._update_setting('log_dice', state)
            elif key == 'voice':
                if not args or len(args) < 2:
                    self._cmd_log("Usage: [bold yellow]set voice <model_name>[/bold yellow]")
                    return
                actual_val = args[1]
                await self._update_setting('voice_preset', actual_val)
            elif key == 'delay':
                if val_str not in ['on', 'off', 'true', 'false']:
                    self._cmd_log("Usage: [bold yellow]set delay <on|off>[/bold yellow]")
                    return
                state = 1 if val_str in ['on', 'true'] else 0
                await self._update_setting('tts_delay_enabled', state)
            elif key in ['bits', 'points']:
                if val_str not in ['on', 'off']:
                    self._cmd_log(f"Usage: [bold yellow]set {key} <on|off>[/bold yellow]")
                    return
                state = 1 if val_str == 'on' else 0
                await self._update_setting(f'pubsub_{key}', state)
            else:
                self._cmd_log(f"[bold red]Unknown setting: {key}[/bold red]. Available: lines, time, chance, model, log_dice, voice, delay, bits, points.")

        elif cmd in ['trust', 'untrust', 'ignore', 'unignore']:
            if not args:
                self._cmd_log(f"Usage: [bold yellow]{cmd} <username>[/bold yellow]")
                return
                
            username = args[0].lower()
            column = 'trusted_users' if cmd in ['trust', 'untrust'] else 'ignored_users'
            is_add = cmd in ['trust', 'ignore']
            
            try:
                if self.current_context == "Global":
                    rows = await self.bot.db.get_all_channels_field(column)
                    updated = 0
                    for ch_name, current_val in rows:
                        user_list = [u.strip() for u in (current_val or "").split(',') if u.strip()]
                        if is_add and username not in user_list:
                            user_list.append(username)
                            updated += 1
                        elif not is_add and username in user_list:
                            user_list.remove(username)
                            updated += 1
                        await self.bot.db.set_channel_field(ch_name, column, ",".join(user_list))
                    action = "Added" if is_add else "Removed"
                    prep = "to" if is_add else "from"
                    self._cmd_log(f"[bold green]{action}[/bold green] {username} {prep} {column} globally ({updated} channels updated).")
                else:
                    clean_name = self.current_context.lstrip('#')
                    current_val = await self.bot.db.get_channel_field(clean_name, column)
                    if current_val is None:
                        self._cmd_log(f"[bold red]Error:[/bold red] Channel {clean_name} not found in database.")
                        return
                    user_list = [u.strip() for u in (current_val or "").split(',') if u.strip()]
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
                    await self.bot.db.set_channel_field(clean_name, column, ",".join(user_list))
                self.bot.load_channel_settings()
            except Exception as e:
                self._cmd_log(f"[bold red]Database Error:[/bold red] {e}")

        elif cmd == 'ignorelist':
            if not self.bot:
                self._cmd_log("Bot instance not connected.")
                return
            try:
                from rich.table import Table
                from rich import box
                channel_filter = None if self.current_context == "Global" else self.current_context.lstrip('#')
                rows = await self.bot.db.get_all_ignored_users(channel_filter)

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
                    await self.bot.db.set_channel_field(target, "join_channel", 0)
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
                await self.bot.db.insert_command(target_chan, cmd_name, response)
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
                updated = await self.bot.db.update_command(target_chan, cmd_name, response)
                if updated:
                    self._cmd_log(f"[bold green]Updated[/bold green] {cmd_name} in {target_chan}.")
                else:
                    self._cmd_log(f"[bold red]Error:[/bold red] Command {cmd_name} not found in {target_chan}.")
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
                deleted = await self.bot.db.delete_command(target_chan, cmd_name)
                if deleted:
                    self._cmd_log(f"[bold green]Deleted[/bold green] {cmd_name} from {target_chan}.")
                else:
                    self._cmd_log(f"[bold red]Error:[/bold red] Command {cmd_name} not found in {target_chan}.")
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
                        await self.bot.db.insert_timed_pool(target_chan, pool_name, interval)
                        self._cmd_log(f"[bold green]Created timer pool[/bold green] '{pool_name}' for {target_chan} (Interval: {interval}m).")
                    except sqlite3.IntegrityError:
                        self._cmd_log(f"[bold red]Error:[/bold red] Timer pool '{pool_name}' already exists in {target_chan}.")

                elif subcmd == 'del':
                    if len(args) < 2:
                        self._cmd_log("Usage: timer del <pool_name>")
                        return
                    pool_name = args[1].lower()
                    if await self.bot.db.delete_timed_pool(target_chan, pool_name):
                        self._cmd_log(f"[bold green]Deleted timer pool[/bold green] '{pool_name}' from {target_chan}.")
                    else:
                        self._cmd_log(f"[bold red]Error:[/bold red] Timer pool '{pool_name}' not found in {target_chan}.")

                elif subcmd == 'msg':
                    if len(args) < 3:
                        self._cmd_log("Usage: timer msg <pool_name> <message...>")
                        return
                    pool_name = args[1].lower()
                    message_text = " ".join(args[2:])
                    if not await self.bot.db.pool_exists(target_chan, pool_name):
                        self._cmd_log(f"[bold red]Error:[/bold red] Timer pool '{pool_name}' not found in {target_chan}. Create it first with 'timer add'.")
                        return
                    await self.bot.db.add_pool_message(target_chan, pool_name, message_text)
                    self._cmd_log(f"[bold green]Added message[/bold green] to timer pool '{pool_name}' in {target_chan}.")

                elif subcmd == 'list':
                    pools = await self.bot.db.get_timed_pools(target_chan)
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
                        msg_count = await self.bot.db.count_pool_messages(target_chan, p_name)
                        table.add_row(p_name, str(p_int), str(msg_count))
                    self._cmd_log(table)
                else:
                    self._cmd_log(f"Unknown timer subcommand: {subcmd}. Use add, del, msg, or list.")

            except Exception as e:
                self._cmd_log(f"[bold red]Timer Error:[/bold red] {e}")

        elif cmd == 'grammar':
            if not args:
                self.action_manage_grammar()
                return
            if len(args) < 2:
                self._cmd_log("Usage: [bold yellow]/grammar <add|list|clear> <rule> [text][/bold yellow]  (or /grammar alone to open the manager)")
                return
            action = args[0].lower()
            rule = args[1].lower()
            text = " ".join(args[2:])
            target_chan = 'global' if self.current_context == 'Global' else self.current_context.lstrip('#')
            
            try:
                import json
                opts_json = await self.bot.db.get_grammar_rule(target_chan, rule)
                options = json.loads(opts_json) if opts_json else []
                if action == 'add':
                    if not text:
                        self._cmd_log("Please provide text.")
                        return
                    options.append(text)
                    await self.bot.db.upsert_grammar_rule(target_chan, rule, json.dumps(options))
                    self._cmd_log(f"[bold green]Added[/bold green] '{text}' to #{rule}# in {target_chan}.")
                elif action == 'list':
                    if not options: self._cmd_log(f"Rule #{rule}# empty.")
                    else: self._cmd_log(f"Rule #{rule}# options: {', '.join(options)}")
                elif action == 'clear':
                    await self.bot.db.delete_grammar_rule(target_chan, rule)
                    self._cmd_log(f"[bold green]Cleared[/bold green] rule #{rule}# from {target_chan}.")
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

            # Grouped, kept in sync with the dispatcher above and the `set` handler.
            groups = [
                ("Navigation", [
                    ("/use [channel]", "Switch context; no arg returns to Global"),
                    ("/status", "Status table for the current context"),
                    ("/clear", "Clear the message log (Ctrl+L)"),
                    ("/quit", "Exit the bot (aliases: exit, q)"),
                ]),
                ("Channels", [
                    ("/join <#channel>", "Join a Twitch channel"),
                    ("/part <#channel>", "Leave a Twitch channel"),
                ]),
                ("Chat & Generation", [
                    ("/say <message>", "Send a chat message in the current channel"),
                    ("/speak [seed]", "Force a Markov message; [seed] starts the first word"),
                    ("/testvoice [text]", "Synthesize locally without sending to Twitch"),
                    ("/poll <min> <q> | <a> | <b>", "Start a Twitch poll (2-5 choices)"),
                ]),
                ("Moderation", [
                    ("/trust <user>", "Add a trusted user (may use bot commands)"),
                    ("/untrust <user>", "Remove a trusted user"),
                    ("/ignore <user>", "Ignore a user (Global = all channels)"),
                    ("/unignore <user>", "Stop ignoring a user"),
                    ("/ignorelist", "List ignored users per channel"),
                ]),
                ("Configuration", [
                    ("/set <key> <value>", "Set a channel config value (keys below)"),
                    ("/tts <on|off>", "Toggle TTS audio generation"),
                    ("/voice <on|off>", "Toggle custom AI voice output"),
                    ("/model <general|individual>", "Choose the Markov model type"),
                ]),
                ("Custom Commands & Grammar", [
                    ("/addc <cmd> <response>", "Add a command; tags <{sender}> <{streamer}> <{input}>"),
                    ("/editc <cmd> <response>", "Edit a custom command"),
                    ("/delc <cmd>", "Delete a custom command"),
                    ("/grammar [add|list|clear <rule> [text]]", "Edit grammar pools; no args opens the manager"),
                ]),
                ("Brain", [
                    ("/brain", "Lines loaded per channel (alias: /stats)"),
                    ("/compile", "Rebuild the Markov brain caches"),
                ]),
                ("Timed Messages", [
                    ("/timers", "Open the timed-messages manager"),
                    ("/timer <add|del|msg|list>", "Manage timed-message pools inline"),
                ]),
                ("Managers & TTS", [
                    ("/settings", "Open the channel settings manager"),
                    ("/commands", "Open the custom commands manager"),
                    ("/ttshistory", "Open the TTS history browser"),
                    ("/ttskill", "Stop active TTS audio and clear its queue"),
                ]),
            ]

            table = Table(show_header=False, box=box.SIMPLE, padding=(0, 1), expand=False)
            table.add_column("Command", style="bold", no_wrap=True)
            table.add_column("Description", style="dim")
            for cat, rows in groups:
                table.add_row(f"[bold cyan]{cat}[/]", "")
                for c, d in rows:
                    table.add_row(f"  [green]{c}[/]", d)
                table.add_row("", "")
            self._cmd_log(table)
            self._cmd_log("[dim]/set keys:[/] lines, time, chance, model, log_dice, voice, delay, bits, points")
            self._cmd_log("[dim]Commands also work without the leading '/'. Tab completes; Up/Down browse history.[/]")

        else:
            self._cmd_log(f"[italic]Unknown command:[/italic] {cmd}")

    async def _update_setting(self, column, value):
        if not self.bot:
            return
        try:
            if self.current_context == "Global":
                await self.bot.db.set_channel_field_all(column, value)
                self.write_log(f"[bold green]Updated[/bold green] {column} globally to {value}")
            else:
                clean_name = self.current_context.lstrip('#')
                await self.bot.db.set_channel_field(clean_name, column, value)
                self.write_log(f"[bold green]Updated[/bold green] {column} for {self.current_context} to {value}")
            self.bot.load_channel_settings()

            # Clear per-channel TTS config cache so next generation picks up new values
            try:
                import bot.tts
                for func_name in ['_get_tts_config_cached', 'get_advanced_tts_configs_cached', 'get_tts_provider_cached', 'get_voice_preset_cached']:
                    func = getattr(bot.tts, func_name, None)
                    if func and hasattr(func, 'cache_clear'):
                        func.cache_clear()
            except Exception:
                pass

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
                channel_names = await self.bot.db.get_all_join_channels()
                rows = [(name,) for name in channel_names]
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
