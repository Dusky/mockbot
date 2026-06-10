from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import DataTable, Button, Input, Label, Static, Select, TextArea
from textual.containers import Horizontal, Vertical, VerticalScroll

from bot.settings_registry import REGISTRY, by_category

class LoreManagerScreen(ModalScreen):
    """Screen to manage multiple lore configurations."""
    BINDINGS = [("escape", "app.pop_screen", "Back")]
    
    def compose(self) -> ComposeResult:
        with Vertical(classes="manager-container"):
            self.table = DataTable(id="lore_table")
            self.table.cursor_type = "row"
            self.table.add_columns("Status", "Lore File")
            
            yield Label(f"Manage Lore for {self.app.current_context}", id="lore_manager_title", classes="manager-title")
            yield Static("Click a row to toggle it ON/OFF. Then press Save.", classes="manager-help")
            
            with Horizontal(classes="manager-actions"):
                yield Label("Base Channel Weight:", id="lore_bias_label")
                yield Input(id="lore_bias_input", type="number", value="15.0")
                yield Button("Save & Apply", id="lore_save", variant="success")

            yield self.table
            yield Static("", classes="manager-status")

    async def on_mount(self) -> None:
        await self.load_data()

    async def load_data(self) -> None:
        self.table.clear()
        if not self.app.bot: return

        context = self.app.current_context.lstrip('#')
        enabled_str = ""
        lore_bias_val = 15.0
        cfg = await self.app.bot.db.get_channel_config(context)
        if cfg:
            enabled_str = cfg.get("enabled_lore") or ""
            lore_bias_raw = cfg.get("lore_bias")
            if lore_bias_raw is not None:
                lore_bias_val = float(lore_bias_raw)
        try:
            self.query_one("#lore_bias_input").value = str(lore_bias_val)
        except Exception:
            pass
                
        enabled_list = [f.strip() for f in enabled_str.split(",") if f.strip()]
        
        import os
        if os.path.exists("./lore"):
            for file in sorted(os.listdir("./lore")):
                if file.endswith(".txt"):
                    is_active = file in enabled_list
                    status_str = "[X]" if is_active else "[ ]"
                    self.table.add_row(status_str, file, key=file)
                    
    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        col_key = list(self.table.columns.keys())[0]
        current_status = self.table.get_cell(event.row_key, col_key)
        new_status = "[ ]" if current_status == "[X]" else "[X]"
        self.table.update_cell(event.row_key, col_key, new_status)
        
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "lore_save":
            enabled_files = []
            col_key = list(self.table.columns.keys())[0]
            
            for row_key in self.table.rows:
                status = self.table.get_cell(row_key, col_key)
                if status == "[X]":
                    enabled_files.append(row_key.value)
                    
            new_enabled_str = ",".join(enabled_files)
            
            lore_bias_val = 15.0
            try:
                lore_bias_val = float(self.query_one("#lore_bias_input").value)
            except Exception:
                pass
                
            context = self.app.current_context.lstrip('#')
            await self.app.bot.db.set_lore_config(context, new_enabled_str, lore_bias_val)
                
            self.app.notify(f"Lore updated successfully.")
            self.app.pop_screen()

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
            yield Static("", classes="manager-status")

    async def on_mount(self) -> None:
        await self.load_data()

    async def load_data(self) -> None:
        self.table.clear()
        if not self.app.bot:
            return
            
        context = self.app.current_context.lstrip('#')
        # If Global context, show all global custom commands OR prompt to select a channel. 
        # For simplicity, if global, we might show a channel column.
        rows = await self.app.bot.db.get_commands(context)
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
            await self.app.bot.db.upsert_command(context, name, resp)
            
            self.query_one("#cmd_name_input", Input).value = ""
            self.query_one("#cmd_resp_input", Input).value = ""
            self.app.notify(f"Command {name} saved.")
            await self.load_data()
            
        elif event.button.id == "cmd_delete":
            try:
                row_key = self.table.coordinate_to_cell_key(self.table.cursor_coordinate).row_key
                cmd_name = row_key.value
                context = self.app.current_context.lstrip('#')
                await self.app.bot.db.delete_command(context, cmd_name)
                self.app.notify(f"Deleted command: {cmd_name}")
                await self.load_data()
            except Exception as e:
                self.app.notify("Select a row to delete.", severity="warning")

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        cmd_name = event.row_key.value
        context = self.app.current_context.lstrip('#')
        template = await self.app.bot.db.get_command(context, cmd_name)
        if template is not None:
            self.query_one("#cmd_name_input", Input).value = cmd_name
            self.query_one("#cmd_resp_input", Input).value = template


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
            yield Static("", classes="manager-status")

    async def on_mount(self) -> None:
        await self.load_data()

    async def load_data(self) -> None:
        self.table.clear()
        if not self.app.bot: return
        context = self.app.current_context.lstrip('#')
        rows = await self.app.bot.db.get_grammar_all(context)
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
            await self.app.bot.db.upsert_grammar_rule(context, name, opts)
            
            self.query_one("#gram_name_input", Input).value = ""
            self.query_one("#gram_opts_input", Input).value = ""
            self.app.notify(f"Grammar rule {name} saved.")
            await self.load_data()
            
        elif event.button.id == "gram_delete":
            try:
                row_key = self.table.coordinate_to_cell_key(self.table.cursor_coordinate).row_key
                rule_name = row_key.value
                context = self.app.current_context.lstrip('#')
                await self.app.bot.db.delete_grammar_rule(context, rule_name)
                self.app.notify(f"Deleted rule: {rule_name}")
                await self.load_data()
            except Exception:
                self.app.notify("Select a row to delete.", severity="warning")
                
        elif event.button.id == "gram_export":
            context = self.app.current_context.lstrip('#')
            try:
                import json, os
                rows = await self.app.bot.db.get_grammar_all(context)
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
                
                await self.app.bot.db.import_grammar(context, import_data)
                self.app.notify(f"Imported {len(import_data)} rules from {filename}")
                await self.load_data()
            except Exception as e:
                self.app.notify(f"Import failed: {e}", severity="error")

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        rule_name = event.row_key.value
        context = self.app.current_context.lstrip('#')
        opts_json = await self.app.bot.db.get_grammar_rule(context, rule_name)
        if opts_json is not None:
            self.query_one("#gram_name_input", Input).value = rule_name
            self.query_one("#gram_opts_input", Input).value = opts_json


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

        setting = REGISTRY.get(self.setting_key)
        control = setting.control if setting else "input"
        wid = f"input_{self.setting_key}"

        with Horizontal(classes="setting-controls"):
            if control == "lore_button":
                yield Button("Manage Lore Library", id="btn_enabled_lore_manage", variant="primary")
                return
            if control == "bool_select":
                val = "1" if str(self.current_value) in ("1", "True", "true", "1.0") else "0"
                yield Select([("Enabled", "1"), ("Disabled", "0")], value=val, id=wid)
            elif control == "select":
                if setting.dynamic_choices:
                    # File-backed choices (voice_preset / rvc_model): keep any
                    # stored-but-unlisted value selectable.
                    options = list(setting.dynamic_choices())
                    val = str(self.current_value) if self.current_value not in (None, "") else str(setting.default)
                    if val and val not in [o[1] for o in options]:
                        options.append((val, val))
                else:  # fixed enum: fall back to the default if the stored value is invalid
                    options = list(setting.choices)
                    val = str(self.current_value)
                    if val not in [o[1] for o in options]:
                        val = str(setting.default)
                yield Select(options, value=val, id=wid)
            else:  # plain text/numeric input
                yield Input(value=str(self.current_value), id=wid)
            # No per-row Update button: changes apply on Select change / Input submit.


class SettingsManagerScreen(ModalScreen):
    """Screen to manage generic channel settings like TTS, voice, delays, etc."""
    
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="manager-container"):
            yield Label(f"Configuration for {self.app.current_context}", classes="manager-title")
            with VerticalScroll(id="settings_list"):
                yield Label("Loading...", id="settings_loading")
            yield Label("Esc to close · changes apply instantly", id="settings_status", classes="manager-status")

    async def on_mount(self) -> None:
        # Ignore the Select.Changed events that fire while rows are first mounted;
        # only user-driven changes (after the first refresh) should write to the DB.
        self._ready = False
        if not self.app.bot:
            self.query_one("#settings_loading").update("Bot offline. Cannot fetch settings.")
            return

        context = self.app.current_context.lstrip('#')
        row = await self.app.bot.db.get_channel_config(context)

        settings_list = self.query_one("#settings_list")
        settings_list.query("*").remove()

        if not row:
            settings_list.mount(Label("No channel configuration found. Is the bot joined to this channel?"))
            return

        # Layout + descriptions come straight from the settings registry.
        for cat_name, settings in by_category().items():
            shown = [s for s in settings if s.key in row.keys()]
            if not shown:
                continue
            settings_list.mount(Label(f"── {cat_name} ──", classes="setting-category"))
            for s in shown:
                settings_list.mount(SettingRow(s.key, row[s.key], s.description))

        # Rows are mounted; enable apply only after the initial render settles so
        # the Selects' mount-time Changed events don't trigger spurious writes.
        self.call_after_refresh(self._enable_apply)

    def _enable_apply(self) -> None:
        # Snapshot the settled initial values so any later change event that
        # merely echoes the initial value (e.g. a Select's mount-time Changed
        # that slips past the _ready flag) is treated as a no-op.
        self._applied = {}
        for w in list(self.query(Select)) + list(self.query(Input)):
            wid = w.id or ""
            if wid.startswith("input_"):
                self._applied[wid[len("input_"):]] = self._widget_raw(w)
        self._ready = True

    @staticmethod
    def _widget_raw(widget) -> str:
        if isinstance(widget, Select):
            return str(widget.value) if widget.value is not None else ""
        return widget.value.strip()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        # The only button left in this screen opens the Lore manager.
        if event.button.id == "btn_enabled_lore_manage":
            self.app.push_screen(LoreManagerScreen())

    async def on_select_changed(self, event: Select.Changed) -> None:
        if not getattr(self, "_ready", False):
            return
        await self._apply_widget(event.select)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        await self._apply_widget(event.input)

    async def _apply_widget(self, widget) -> None:
        """Coerce a setting widget's value and persist it, skipping no-op changes."""
        wid = widget.id or ""
        if not wid.startswith("input_"):
            return
        key = wid[len("input_"):]
        raw = self._widget_raw(widget)

        # Ignore changes that don't actually alter the stored value.
        if getattr(self, "_applied", {}).get(key) == raw:
            return

        # Simple typing conversions based on known bools/ints
        val = raw
        if raw.isdigit():
            val = int(raw)
        elif raw.replace(".", "", 1).isdigit():
            val = float(raw)

        try:
            status = self.query_one("#settings_status", Label)
        except Exception:
            status = None
        try:
            await self.app._update_setting(key, val)
            self._applied[key] = raw
            if status:
                status.update(f"[green]✓[/] {key} = {val}")
        except Exception as e:
            if status:
                status.update(f"[red]✗[/] {key}: {e}")

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
            yield Static("", classes="manager-status")

    async def on_mount(self) -> None:
        await self.load_data()

    async def load_data(self) -> None:
        self.table.clear()
        if not self.app.bot: return
        context = self.app.current_context.lstrip('#')
        import json
        pools = await self.app.bot.db.get_timed_pools(context)
        for pool_name, interval in pools:
            msgs = await self.app.bot.db.get_pool_messages(context, pool_name)
            self.table.add_row(pool_name, str(interval), json.dumps(msgs), key=pool_name)

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
            await self.app.bot.db.upsert_timed_pool_with_messages(context, name, interval, parsed_msgs)
            
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
                await self.app.bot.db.delete_pool_messages(context, pool_name)
                await self.app.bot.db.delete_timed_pool(context, pool_name)
                self.app.notify(f"Deleted timer: {pool_name}")
                await self.load_data()
            except Exception:
                self.app.notify("Select a row to delete.", severity="warning")

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        pool_name = event.row_key.value
        context = self.app.current_context.lstrip('#')
        pools = await self.app.bot.db.get_timed_pools(context)
        pool_map = {p[0]: p[1] for p in pools}
        if pool_name in pool_map:
            import json
            self.query_one("#timer_name_input", Input).value = pool_name
            self.query_one("#timer_interval_input", Input).value = str(pool_map[pool_name])
            msgs = await self.app.bot.db.get_pool_messages(context, pool_name)
            self.query_one("#timer_msgs_input", Input).value = json.dumps(msgs)


class TTSHistoryScreen(ModalScreen):
    """Screen to manage and playback TTS history logs."""
    
    BINDINGS = [("escape", "app.pop_screen", "Back")]
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._current_audio_process = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="manager-container"):
            yield Label(f"TTS History Archive | Context: {self.app.current_context}", classes="manager-title")
            yield Label("Use UP/DOWN arrows to preview generated text, and ENTER to play the audio file.", classes="manager-help")
            
            self.table = DataTable(id="tts_table")
            self.table.cursor_type = "row"
            self.table.zebra_stripes = True
            yield self.table
                
            yield TextArea("Select a row to preview TTS message...", id="tts_preview_text", read_only=True, classes="tts-preview")
            
            with Horizontal(classes="manager-actions"):
                yield Button("▶ Play Audio (Enter)", id="btn_tts_play", variant="success", disabled=True)
                yield Button("■ Stop Audio", id="btn_tts_stop", variant="error")
                yield Button("📂 Open Folder", id="btn_tts_folder", variant="primary", disabled=True)
                yield Button("Refresh", id="btn_tts_refresh")
                yield Button("🧹 Clean Orphans", id="btn_tts_clean", variant="warning")
                yield Button("Close", id="btn_close", variant="primary")

            yield Static("", classes="manager-status")

    async def on_mount(self) -> None:
        self.table.add_columns("Time", "Channel", "Voice", "Prompt Preview")
        await self.load_data()

    async def load_data(self) -> None:
        self.table.clear()
        if not self.app.bot:
            return
            
        context = self.app.current_context
        channel = None if context == "Global" else context.lstrip('#')

        import json
        try:
            rows = await self.app.bot.db.get_tts_logs(channel=channel, limit=100)
                
            for row in rows:
                timestamp, ch, voice, msg, fpath = row
                preview = (msg[:45] + '...') if len(msg) > 45 else msg
                row_key = json.dumps({"f": fpath, "m": msg})
                self.table.add_row(timestamp, f"#{ch}", voice, preview, key=row_key)
                
            if len(rows) > 0:
                self.table.move_cursor(row=0)
        except Exception as e:
            self.app.notify(f"Failed to load TTS history: {e}", severity="error")

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._action_play_row(event.row_key.value)

    async def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._update_preview(event.row_key.value)

    def _update_preview(self, row_key_str: str) -> None:
        import json
        try:
            data = json.loads(row_key_str)
            self.query_one("#tts_preview_text", TextArea).text = data["m"]
            self.query_one("#btn_tts_play", Button).disabled = False
            self.query_one("#btn_tts_folder", Button).disabled = False
        except Exception:
            pass

    def _action_play_row(self, row_key_str: str) -> None:
        import json, os, subprocess
        try:
            data = json.loads(row_key_str)
            fpath = data["f"]
            if not os.path.exists(fpath):
                self.app.notify("Audio file no longer exists on disk!", severity="error")
                return
                
            if self._current_audio_process:
                try:
                    self._current_audio_process.terminate()
                except Exception:
                    pass
                    
            self._current_audio_process = subprocess.Popen(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", fpath], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.app.notify(f"Playing '{os.path.basename(fpath)}'", severity="info")
        except Exception as e:
            self.app.notify(f"Playback failed: {e}", severity="error")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_close":
            self.app.pop_screen()
        elif event.button.id == "btn_tts_refresh":
            await self.load_data()
            self.app.notify("TTS logs refreshed.")
        elif event.button.id == "btn_tts_stop":
            if self._current_audio_process:
                try:
                    self._current_audio_process.terminate()
                    self.app.notify("Audio stopped.")
                except Exception:
                    pass
        elif event.button.id == "btn_tts_play":
            try:
                row_key = self.table.coordinate_to_cell_key(self.table.cursor_coordinate).row_key
                self._action_play_row(row_key.value)
            except Exception:
                self.app.notify("Select a valid row first.", severity="warning")
        elif event.button.id == "btn_tts_folder":
            try:
                import json, os, subprocess
                row_key = self.table.coordinate_to_cell_key(self.table.cursor_coordinate).row_key
                data = json.loads(row_key.value)
                fdir = os.path.dirname(os.path.abspath(data["f"]))
                subprocess.Popen(["xdg-open", fdir])
                self.app.notify(f"Opened {fdir}")
            except Exception as e:
                self.app.notify(f"Failed to open folder: {e}", severity="error")
        elif event.button.id == "btn_tts_clean":
            import asyncio
            asyncio.create_task(self._clean_orphans())

    async def _clean_orphans(self) -> None:
        import os
        count = 0
        try:
            rows = await self.app.bot.db.get_all_tts_log_files()
            for msg_id, file_path in rows:
                if not os.path.exists(file_path):
                    await self.app.bot.db.delete_tts_log(msg_id)
                    count += 1
                
            self.app.notify(f"Purged {count} orphaned TTS logs from DB!")
            await self.load_data()
        except Exception as e:
            self.app.notify(f"Error cleaning orphans: {e}", severity="error")
