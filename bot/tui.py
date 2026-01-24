from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, Label, RichLog
from textual.containers import Container
from textual.binding import Binding
from textual import work
from textual.design import ColorSystem
import logging
import asyncio

class MockbotTUI(App):
    """A Textual app to control Mockbot (WeeChat Style)."""

    CSS = """
    Screen {
        background: $surface;
        color: $text;
    }

    #chat-container {
        height: 1fr;
        width: 1fr;
        background: $surface;
        border: vkey $primary;
        margin: 0;
        padding: 0 1;
    }

    #sidebar {
        dock: right;
        width: 30;
        height: 100%;
        background: $panel;
        border-left: vkey $primary;
        padding: 1;
    }

    #input-container {
        dock: bottom;
        height: 3;
        background: $surface;
        border-top: solid $primary;
    }

    RichLog {
        height: 100%;
        background: $surface;
 
    }
    
    Input {
        width: 100%;
        background: $surface;
        border: none;
    }
    
    .header {
        text-style: bold;
        color: $secondary-lighten-2;
        border-bottom: solid $secondary;
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+l", "clear_log", "Clear Log"),
    ]

    def __init__(self, db_file, rebuild_cache, enable_tts):
        super().__init__()
        self.db_file = db_file
        self.rebuild_cache = rebuild_cache
        self.enable_tts = enable_tts
        self.bot = None
        self.setup_logging()

    def setup_logging(self):
        # customized logging handler that pushes to TUI
        class TUIHandler(logging.Handler):
            def __init__(self, tui_app):
                super().__init__()
                self.tui = tui_app

            def emit(self, record):
                try:
                    msg = self.format(record)
                    # Colorize based on level
                    if record.levelno >= logging.ERROR:
                        msg = f"[bold red]{escape(msg)}[/bold red]"
                    elif record.levelno >= logging.WARNING:
                        msg = f"[bold yellow]{escape(msg)}[/bold yellow]"
                    elif "TwitchIO" in record.name:
                        msg = f"[dim cyan]{escape(msg)}[/dim cyan]"
                    else:
                        msg = escape(msg)
                        
                    self.tui.call_from_thread(self.tui.write_log, msg)
                except Exception:
                    self.handleError(record)

        self.log_handler = TUIHandler(self)
        self.log_handler.setFormatter(logging.Formatter('%(asctime)s %(message)s', datefmt='[%H:%M:%S]'))
        
        # Attach to root logger
        logging.getLogger().addHandler(self.log_handler)
        logging.getLogger("twitchio").setLevel(logging.INFO)

    def compose(self) -> ComposeResult:
    # ... (unchanged)

    def update_ui_state(self):
        """Periodically update UI elements based on bot state."""
        if not self.bot:
            return

        # Update Status
        status_lbl = self.query_one("#status_text", Label)
        
        # Robust connection check
        is_connected = False
        try:
            # Check internal websocket state
            if hasattr(self.bot, '_ws') and self.bot._ws is not None:
                 if not self.bot._ws.is_closed:
                     is_connected = True
            
            # Fallback: If we have joined channels, we are connected
            if hasattr(self.bot, '_joined_channels') and len(self.bot._joined_channels) > 0:
                is_connected = True
        except:
            pass
        
        if is_connected:
             self.sub_title = f"Connected as {self.bot.nick}"
             status_lbl.update(f"[green]● Connected[/green]\n[dim]{self.bot.nick}[/dim]")
        else:
             self.sub_title = "Disconnected"
             # Show *why* disconnected if possible (connecting...)
             status_lbl.update("[yellow]● Connecting...[/yellow]")

        # Update Channels
        channel_lbl = self.query_one("#channel_list_text", Label)
        if hasattr(self.bot, 'channels') and self.bot.channels:
            # ... existing logic ...
            lines = []
            for ch in self.bot.channels:
                display_name = f"#{ch.lstrip('#')}"
                if hasattr(self.bot, '_joined_channels') and display_name in self.bot._joined_channels:
                    lines.append(f"[bold white]{display_name}[/bold white]")
                else:
                    lines.append(f"[dim]{display_name}[/dim]")
            channel_lbl.update("\n".join(lines))
        else:
            channel_lbl.update("[dim ital]No channels[/dim ital]")

    async def run_bot(self):
        """Start the Twitch bot on the main event loop."""
        try:
            self.write_log("[dim]Loading configuration...[/dim]")
            from bot.core import setup_bot
            
            self.bot = setup_bot(self.db_file, self.rebuild_cache, self.enable_tts)
            
            # Hook logging
            if hasattr(self.bot, 'logger'):
                self.bot.logger.addHandler(self.log_handler)
            
            # Inject ready listener for debugging
            @self.bot.event
            async def event_ready():
                self.write_log(f"[bold green]EVENT: Bot is ready! Logged in as {self.bot.nick}[/bold green]")
                
            self.write_log(f"[bold green]✓ Bot created. Token: ...{self.bot._http.token[-4:]}[/bold green]")
            self.write_log("[dim]Connecting to Twitch...[/dim]")
            
            await self.bot.start()
            
        except Exception as e:
            self.write_log(f"[bold red]❌ Bot Crashed:[/bold red] {e}")
            import traceback
            self.write_log(traceback.format_exc())

    def write_log(self, message: str) -> None:
        """Write a message to the RichLog widget."""
        try:
            log = self.query_one("#chat_log", RichLog)
            log.write(message)
        except Exception:
            pass 

    async def on_input_submitted(self, message: Input.Submitted) -> None:
        """Handle input submission."""
        value = message.value
        if not value:
            return

        message.input.value = ""
        
        if not self.bot or not hasattr(self.bot, '_ws') or self.bot._ws is None or self.bot._ws.is_closed:
            self.write_log("[bold red]Cannot send: Bot disconnected.[/bold red]")
            return

        # Target Channel
        target = None
        if self.bot.channels:
             target = self.bot.channels[0]
        
        if target:
            self.write_log(f"[bold white]You[/bold white] [dim]({target})[/dim]: {value}")
            try:
                # Basic send attempt - cleaner to use bot.get_channel if available
                # or manually construct message if API allows.
                # TwitchIO `get_channel` returns None if not in cache (joined).
                chan_obj = self.bot.get_channel(target)
                if chan_obj:
                    await chan_obj.send(value)
                else:
                    self.write_log(f"[red]Error: Not participating in {target}[/red]")
            except Exception as e:
                self.write_log(f"[bold red]Send Failed:[/bold red] {e}")
        else:
             self.write_log("[red]No channels configured.[/red]")

    def action_clear_log(self) -> None:
        self.query_one("#chat_log", RichLog).clear()

def start_tui(db_file, rebuild_cache, enable_tts):
    app = MockbotTUI(db_file, rebuild_cache, enable_tts)
    app.run()
