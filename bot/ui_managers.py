import aiosqlite
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import DataTable, Button, Input, Label, Static, Select
from textual.containers import Horizontal, Vertical, VerticalScroll

class CommandsManagerScreen(ModalScreen):
    """Screen to manage custom commands for the current channel."""
    
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="manager-container"):
            self.table = DataTable(id="commands_table")
            self.table.cursor_type = "row"
            self.table.add_columns("Command", "Response Template")
            
            yield Label(f"Managing Commands for {self.app.current_context}", id="cmd_manager_title", classes="manager-title")
            
            help_text = (
                "💡 [b]Variables:[/b] <{sender}> (user who typed), <{input}> (text after command), <{streamer}> (channel name).\n"
                "💡 [b]Grammar:[/b] Include [green]#rule_name#[/green] to pick a random option from a grammar pool.\n"
                "💡 [b]Timeouts:[/b] {timeout:username:seconds} (e.g. {timeout:<{sender}>:60}) to auto-timeout."
            )
            yield Static(help_text, classes="manager-help")
            
            with Horizontal(id="cmd_actions", classes="manager-actions"):
                yield Input(placeholder="!command_name", id="cmd_name_input")
                yield Input(placeholder="Response Template", id="cmd_resp_input")
                yield Button("Add/Update", id="cmd_save", variant="success")
                yield Button("Delete Selected", id="cmd_delete", variant="error")
            
            yield self.table

    async def on_mount(self) -> None:
        await self.load_data()

    async def load_data(self) -> None:
        self.table.clear()
        if not self.app.bot:
            return
            
        context = self.app.current_context.lstrip('#')
        # If Global context, show all global custom commands OR prompt to select a channel. 
        # For simplicity, if global, we might show a channel column.
        query = "SELECT command_name, response_template FROM custom_commands WHERE channel_name = ?"
        params = (context,)
        
        async with aiosqlite.connect(self.app.bot.db_file) as conn:
            c = await conn.cursor()
            await c.execute(query, params)
            rows = await c.fetchall()
            for row in rows:
                self.table.add_row(row[0], row[1], key=row[0])

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cmd_save":
            name = self.query_one("#cmd_name_input", Input).value.strip()
            resp = self.query_one("#cmd_resp_input", Input).value.strip()
            if not name or not resp:
                self.app.notify("Both name and response are required.", severity="error")
                return
            if not name.startswith("!"):
                name = f"!{name}"
                
            context = self.app.current_context.lstrip('#')
            async with aiosqlite.connect(self.app.bot.db_file) as conn:
                await conn.execute(
                    "INSERT OR REPLACE INTO custom_commands (channel_name, command_name, response_template) VALUES (?, ?, ?)",
                    (context, name, resp)
                )
                await conn.commit()
            
            self.query_one("#cmd_name_input", Input).value = ""
            self.query_one("#cmd_resp_input", Input).value = ""
            self.app.notify(f"Command {name} saved.")
            await self.load_data()
            
        elif event.button.id == "cmd_delete":
            try:
                row_key = self.table.coordinate_to_cell_key(self.table.cursor_coordinate).row_key
                cmd_name = row_key.value
                context = self.app.current_context.lstrip('#')
                async with aiosqlite.connect(self.app.bot.db_file) as conn:
                    await conn.execute("DELETE FROM custom_commands WHERE channel_name = ? AND command_name = ?", (context, cmd_name))
                    await conn.commit()
                self.app.notify(f"Deleted command: {cmd_name}")
                await self.load_data()
            except Exception as e:
                self.app.notify("Select a row to delete.", severity="warning")

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        cmd_name = event.row_key.value
        context = self.app.current_context.lstrip('#')
        async with aiosqlite.connect(self.app.bot.db_file) as conn:
            c = await conn.cursor()
            await c.execute("SELECT response_template FROM custom_commands WHERE channel_name = ? AND command_name = ?", (context, cmd_name))
            row = await c.fetchone()
            if row:
                self.query_one("#cmd_name_input", Input).value = cmd_name
                self.query_one("#cmd_resp_input", Input).value = row[0]


class GrammarManagerScreen(ModalScreen):
    """Screen to manage Tracery grammar rules."""
    
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="manager-container"):
            self.table = DataTable(id="grammar_table")
            self.table.cursor_type = "row"
            self.table.add_columns("Rule Name", "Options (JSON)")
            
            yield Label(f"Managing Grammar for {self.app.current_context}", classes="manager-title")
            
            help_text = (
                "💡 [b]What is this?[/b] Grammar rules are 'Word Pools' used to randomize bot responses.\n"
                "💡 [b]Usage:[/b] Name your rule (e.g., 'weapons'), and provide a JSON list of options (e.g. [\"sword\", \"bow\"]).\n"
                "💡 [b]Commands:[/b] In a custom command response, use [green]#weapons#[/green] to randomly select an option!"
            )
            yield Static(help_text, classes="manager-help")
            
            with Horizontal(classes="manager-actions"):
                yield Input(placeholder="rule_name", id="gram_name_input")
                yield Input(placeholder='["option1", "option2"]', id="gram_opts_input")
                yield Button("Add/Update", id="gram_save", variant="success")
                yield Button("Delete Selected", id="gram_delete", variant="error")
            with Horizontal(classes="manager-actions", id="gram_io_actions"):
                yield Button("Export JSON Backup", id="gram_export", variant="primary")
                yield Button("Import JSON Backup", id="gram_import", variant="warning")
            
            yield self.table

    async def on_mount(self) -> None:
        await self.load_data()

    async def load_data(self) -> None:
        self.table.clear()
        if not self.app.bot: return
        context = self.app.current_context.lstrip('#')
        async with aiosqlite.connect(self.app.bot.db_file) as conn:
            c = await conn.cursor()
            await c.execute("SELECT rule_name, options_json FROM custom_grammar WHERE channel_name = ?", (context,))
            rows = await c.fetchall()
            for row in rows:
                self.table.add_row(row[0], row[1], key=row[0])

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "gram_save":
            name = self.query_one("#gram_name_input", Input).value.strip()
            opts = self.query_one("#gram_opts_input", Input).value.strip()
            if not name or not opts:
                self.app.notify("Both name and options are required.", severity="error")
                return
                
            import json
            try:
                # Basic validation
                parsed = json.loads(opts)
                if not isinstance(parsed, list):
                    raise ValueError("Must be a JSON list.")
            except Exception as e:
                self.app.notify(f"Invalid JSON: {e}", severity="error")
                return
                
            context = self.app.current_context.lstrip('#')
            async with aiosqlite.connect(self.app.bot.db_file) as conn:
                await conn.execute(
                    "INSERT OR REPLACE INTO custom_grammar (channel_name, rule_name, options_json) VALUES (?, ?, ?)",
                    (context, name, opts)
                )
                await conn.commit()
            
            self.query_one("#gram_name_input", Input).value = ""
            self.query_one("#gram_opts_input", Input).value = ""
            self.app.notify(f"Grammar rule {name} saved.")
            await self.load_data()
            
        elif event.button.id == "gram_delete":
            try:
                row_key = self.table.coordinate_to_cell_key(self.table.cursor_coordinate).row_key
                rule_name = row_key.value
                context = self.app.current_context.lstrip('#')
                async with aiosqlite.connect(self.app.bot.db_file) as conn:
                    await conn.execute("DELETE FROM custom_grammar WHERE channel_name = ? AND rule_name = ?", (context, rule_name))
                    await conn.commit()
                self.app.notify(f"Deleted rule: {rule_name}")
                await self.load_data()
            except Exception:
                self.app.notify("Select a row to delete.", severity="warning")
                
        elif event.button.id == "gram_export":
            context = self.app.current_context.lstrip('#')
            try:
                import json, os
                async with aiosqlite.connect(self.app.bot.db_file) as conn:
                    c = await conn.cursor()
                    await c.execute("SELECT rule_name, options_json FROM custom_grammar WHERE channel_name = ?", (context,))
                    rows = await c.fetchall()
                export_data = {row[0]: json.loads(row[1]) for row in rows}
                filename = f"{context}_grammar_backup.json"
                with open(filename, 'w') as f:
                    json.dump(export_data, f, indent=4)
                self.app.notify(f"Exported {len(rows)} rules to {filename}")
            except Exception as e:
                self.app.notify(f"Export failed: {e}", severity="error")

        elif event.button.id == "gram_import":
            context = self.app.current_context.lstrip('#')
            filename = f"{context}_grammar_backup.json"
            try:
                import json, os
                if not os.path.exists(filename):
                    self.app.notify(f"File {filename} not found in root.", severity="error")
                    return
                with open(filename, 'r') as f:
                    import_data = json.load(f)
                
                async with aiosqlite.connect(self.app.bot.db_file) as conn:
                    await conn.execute("DELETE FROM custom_grammar WHERE channel_name = ?", (context,))
                    for rule_name, options_list in import_data.items():
                        await conn.execute(
                            "INSERT INTO custom_grammar (channel_name, rule_name, options_json) VALUES (?, ?, ?)",
                            (context, rule_name, json.dumps(options_list))
                        )
                    await conn.commit()
                self.app.notify(f"Imported {len(import_data)} rules from {filename}")
                await self.load_data()
            except Exception as e:
                self.app.notify(f"Import failed: {e}", severity="error")

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        rule_name = event.row_key.value
        context = self.app.current_context.lstrip('#')
        async with aiosqlite.connect(self.app.bot.db_file) as conn:
            c = await conn.cursor()
            await c.execute("SELECT options_json FROM custom_grammar WHERE channel_name = ? AND rule_name = ?", (context, rule_name))
            row = await c.fetchone()
            if row:
                self.query_one("#gram_name_input", Input).value = rule_name
                self.query_one("#gram_opts_input", Input).value = row[0]


class SettingRow(Horizontal):
    """A row containing a setting's label, input, button, and description."""
    def __init__(self, key: str, current_value: str, description: str, **kwargs):
        super().__init__(**kwargs)
        self.setting_key = key
        self.current_value = current_value
        self.description = description

    def compose(self) -> ComposeResult:
        with Vertical(classes="setting-info"):
            yield Label(self.setting_key, classes="setting-key")
            yield Label(self.description, classes="setting-desc")
        
        with Horizontal(classes="setting-controls"):
            BOOLEAN_FIELDS = {
                "tts_enabled", "voice_enabled", "join_channel", "use_general_model", 
                "tts_delay_enabled", "log_dice", "pubsub_bits", "pubsub_points"
            }
            if self.setting_key in BOOLEAN_FIELDS:
                options = [("Enabled", "1"), ("Disabled", "0")]
                val_str = "1" if str(self.current_value) in ("1", "True", "true", "1.0") else "0"
                yield Select(options, value=val_str, id=f"input_{self.setting_key}")
            elif self.setting_key == "bark_model":
                options = [("Small", "small"), ("Regular", "regular")]
                val_str = str(self.current_value) if self.current_value in ("small", "regular") else "small"
                yield Select(options, value=val_str, id=f"input_{self.setting_key}")
            else:
                yield Input(value=str(self.current_value), id=f"input_{self.setting_key}")

            yield Button("Update", id=f"btn_{self.setting_key}", variant="primary")


class SettingsManagerScreen(ModalScreen):
    """Screen to manage generic channel settings like TTS, voice, delays, etc."""
    
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    SETTINGS_META = {
        "tts_enabled": "1 to enable TTS, 0 to disable. Determines if the bot speaks out loud.",
        "voice_enabled": "1 to enable custom voices, 0 to disable. Determines if AI voices are used over basic OS voices.",
        "join_channel": "1 to automatically join this Twitch channel on startup, 0 to not.",
        "use_general_model": "1 to use the generic AI brain model, 0 to use an individual channel-specific model.",
        "lines_between_messages": "Number of chat messages that must pass before the bot interjects passively.",
        "time_between_messages": "Seconds that must pass before the bot interjects passively.",
        "voice_preset": "The voice profile for TTS (e.g. 'v2/en_speaker_5', 'v2/en_speaker_1').",
        "bark_model": "Which bark model to use ('small', 'regular').",
        "tts_delay_enabled": "1 to delay TTS generation slightly to improve pacing, 0 for immediate generation.",
        "random_chance": "Number 0-100 indicating percentage chance for the bot to spontaneously reply to a user message.",
        "log_dice": "1 to log random chance rolls to the console window, 0 to hide them.",
        "pubsub_bits": "1 to respond to bits/cheers, 0 to ignore them.",
        "pubsub_points": "1 to respond to channel point redemptions, 0 to ignore.",
        "tts_reward": "The exact name of a Twitch Channel Point reward that triggers TTS."
    }

    def compose(self) -> ComposeResult:
        with Vertical(classes="manager-container"):
            yield Label(f"Configuration for {self.app.current_context}", classes="manager-title")
            with VerticalScroll(id="settings_list"):
                yield Label("Loading...", id="settings_loading")

    async def on_mount(self) -> None:
        if not self.app.bot:
            self.query_one("#settings_loading").update("Bot offline. Cannot fetch settings.")
            return
            
        context = self.app.current_context.lstrip('#')
        # Load all columns from channel_configs
        async with aiosqlite.connect(self.app.bot.db_file) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            await c.execute("SELECT * FROM channel_configs WHERE channel_name = ?", (context,))
            row = await c.fetchone()
            
            settings_list = self.query_one("#settings_list")
            settings_list.query("*").remove()
            
            if not row:
                settings_list.mount(Label("No channel configuration found. Is the bot joined to this channel?"))
                return
                
            for key in row.keys():
                if key in ["channel_name", "user_id", "owner", "trusted_users", "ignored_users", "currently_connected"]:
                    continue # Skip non-configurable or separately managed fields
                
                desc = self.SETTINGS_META.get(key, "No description available.")
                settings_list.mount(SettingRow(key, row[key], desc))

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id and btn_id.startswith("btn_"):
            key = btn_id[4:]
            
            # Check if input is Select or Input
            widgetNodes = self.query(f"#input_{key}")
            if not widgetNodes: return
            
            widget = widgetNodes.first()
            if isinstance(widget, Select):
                input_val = str(widget.value) if widget.value is not None else ""
            else:
                input_val = widget.value.strip()
            
            # Simple typing conversions based on known bools/ints
            val = input_val
            if input_val.isdigit():
                val = int(input_val)
            elif input_val.replace(".", "", 1).isdigit():
                val = float(input_val)
                
            try:
                await self.app._update_setting(key, val)
                self.app.notify(f"Updated {key} to {val}", severity="information")
            except Exception as e:
                self.app.notify(f"Failed to update {key}: {e}", severity="error")

class TimersManagerScreen(ModalScreen):
    """Screen to manage timed message pools."""
    
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="manager-container"):
            self.table = DataTable(id="timers_table")
            self.table.cursor_type = "row"
            self.table.add_columns("Timer Name", "Interval (Mins)", "Messages (JSON)")
            
            yield Label(f"Managing Timers for {self.app.current_context}", classes="manager-title")
            
            help_text = (
                "💡 [b]What is this?[/b] Timers send automated chat messages every X minutes.\n"
                "💡 [b]Messages:[/b] Provide a JSON array (e.g. [\"Follow!\", \"Discord!\"]) to rotate through multiple messages randomly or sequentially.\n"
                "💡 [b]Commands:[/b] Grammar tags like [green]#weapons#[/green] work here too!"
            )
            yield Static(help_text, classes="manager-help")
            
            with Horizontal(classes="manager-actions"):
                yield Input(placeholder="timer_name", id="timer_name_input")
                yield Input(placeholder="Minutes (e.g. 15)", id="timer_interval_input")
                yield Input(placeholder='["Message 1", "Message 2"]', id="timer_msgs_input")
                yield Button("Add/Update", id="timer_save", variant="success")
                yield Button("Delete Selected", id="timer_delete", variant="error")
            
            yield self.table

    async def on_mount(self) -> None:
        await self.load_data()

    async def load_data(self) -> None:
        self.table.clear()
        if not self.app.bot: return
        context = self.app.current_context.lstrip('#')
        async with aiosqlite.connect(self.app.bot.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                "SELECT pool_name, interval_minutes FROM timed_message_pools WHERE channel_name = ?", 
                (context,)
            )
            pools = await c.fetchall()
            
            for pool in pools:
                pool_name, interval = pool[0], pool[1]
                # get messages
                await c.execute("SELECT message_text FROM timed_messages WHERE channel_name = ? AND pool_name = ?", (context, pool_name))
                msgs = await c.fetchall()
                msg_list = [m[0] for m in msgs]
                import json
                msg_json = json.dumps(msg_list)
                self.table.add_row(pool_name, str(interval), msg_json, key=pool_name)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "timer_save":
            name = self.query_one("#timer_name_input", Input).value.strip()
            interval_str = self.query_one("#timer_interval_input", Input).value.strip()
            msgs_str = self.query_one("#timer_msgs_input", Input).value.strip()
            
            if not name or not interval_str or not msgs_str:
                self.app.notify("Name, interval, and messages are required.", severity="error")
                return
                
            try:
                interval = int(interval_str)
            except ValueError:
                self.app.notify("Interval must be an integer.", severity="error")
                return
                
            import json
            try:
                parsed_msgs = json.loads(msgs_str)
                if not isinstance(parsed_msgs, list) or len(parsed_msgs) == 0:
                    raise ValueError("Must be a non-empty JSON list of strings.")
            except Exception as e:
                self.app.notify(f"Invalid JSON: {e}", severity="error")
                return
                
            context = self.app.current_context.lstrip('#')
            async with aiosqlite.connect(self.app.bot.db_file) as conn:
                # Upsert pool
                await conn.execute(
                    "INSERT OR REPLACE INTO timed_message_pools (channel_name, pool_name, interval_minutes) VALUES (?, ?, ?)",
                    (context, name, interval)
                )
                # Clear old messages
                await conn.execute("DELETE FROM timed_messages WHERE channel_name = ? AND pool_name = ?", (context, name))
                
                # Insert new messages
                for msg in parsed_msgs:
                    await conn.execute(
                        "INSERT INTO timed_messages (channel_name, pool_name, message_text) VALUES (?, ?, ?)",
                        (context, name, str(msg))
                    )
                await conn.commit()
            
            self.query_one("#timer_name_input", Input).value = ""
            self.query_one("#timer_interval_input", Input).value = ""
            self.query_one("#timer_msgs_input", Input).value = ""
            self.app.notify(f"Timer {name} saved.")
            await self.load_data()
            
        elif event.button.id == "timer_delete":
            try:
                row_key = self.table.coordinate_to_cell_key(self.table.cursor_coordinate).row_key
                pool_name = row_key.value
                context = self.app.current_context.lstrip('#')
                async with aiosqlite.connect(self.app.bot.db_file) as conn:
                    # Cascade should handle timed_messages if enabled, but manual deletion is safer 
                    await conn.execute("DELETE FROM timed_messages WHERE channel_name = ? AND pool_name = ?", (context, pool_name))
                    await conn.execute("DELETE FROM timed_message_pools WHERE channel_name = ? AND pool_name = ?", (context, pool_name))
                    await conn.commit()
                self.app.notify(f"Deleted timer: {pool_name}")
                await self.load_data()
            except Exception:
                self.app.notify("Select a row to delete.", severity="warning")

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        pool_name = event.row_key.value
        context = self.app.current_context.lstrip('#')
        async with aiosqlite.connect(self.app.bot.db_file) as conn:
            c = await conn.cursor()
            await c.execute("SELECT interval_minutes FROM timed_message_pools WHERE channel_name = ? AND pool_name = ?", (context, pool_name))
            row = await c.fetchone()
            if row:
                self.query_one("#timer_name_input", Input).value = pool_name
                self.query_one("#timer_interval_input", Input).value = str(row[0])
                
                await c.execute("SELECT message_text FROM timed_messages WHERE channel_name = ? AND pool_name = ?", (context, pool_name))
                msgs = await c.fetchall()
                msg_list = [m[0] for m in msgs]
                import json
                self.query_one("#timer_msgs_input", Input).value = json.dumps(msg_list)
