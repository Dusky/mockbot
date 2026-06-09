import sqlite3
import threading
import logging
from contextlib import contextmanager, asynccontextmanager
from datetime import datetime

import aiosqlite

from bot.db import ensure_db_setup

# Re-exported so callers can catch DB integrity violations without importing
# sqlite3 directly (keeps all DB-engine knowledge behind this module).
IntegrityError = sqlite3.IntegrityError

_VALID_CHANNEL_CONFIG_COLS = frozenset({
    "tts_enabled", "voice_enabled", "join_channel", "owner", "trusted_users",
    "ignored_users", "use_general_model", "lines_between_messages",
    "time_between_messages", "voice_preset", "bark_model", "currently_connected",
    "tts_delay_enabled", "random_chance", "log_dice", "pubsub_bits", "pubsub_points",
    "tts_reward", "tts_provider", "rvc_model", "chatterbox_temperature",
    "chatterbox_exaggeration", "bark_text_temp", "bark_waveform_temp", "rvc_pitch",
    "rvc_index_rate", "rvc_api_url", "enabled_lore", "lore_bias", "user_id",
})

_sync_lock = threading.Lock()


class Database:
    def __init__(self, db_file: str):
        self.db_file = db_file
        ensure_db_setup(db_file)

    # ── Channel config ──────────────────────────────────────────────────────

    async def get_channel_config(self, channel: str) -> dict | None:
        async with aiosqlite.connect(self.db_file) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            await c.execute("SELECT * FROM channel_configs WHERE channel_name = ?", (channel,))
            row = await c.fetchone()
            return dict(row) if row else None

    async def get_all_join_channels(self) -> list:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute("SELECT channel_name FROM channel_configs WHERE join_channel = 1 ORDER BY channel_name")
            rows = await c.fetchall()
            return [r[0] for r in rows]

    async def channel_exists(self, channel: str) -> bool:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute("SELECT COUNT(*) FROM channel_configs WHERE channel_name = ?", (channel,))
            return (await c.fetchone())[0] > 0

    async def insert_channel(self, channel: str, owner: str) -> None:
        async with aiosqlite.connect(self.db_file) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO channel_configs "
                "(channel_name, voice_enabled, tts_enabled, join_channel, owner, trusted_users) "
                "VALUES (?, 0, 0, 1, ?, '')",
                (channel, owner),
            )
            await conn.commit()

    async def set_channel_field(self, channel: str, field: str, value) -> None:
        if field not in _VALID_CHANNEL_CONFIG_COLS:
            raise ValueError(f"Invalid channel_configs column: {field!r}")
        async with aiosqlite.connect(self.db_file) as conn:
            await conn.execute(
                f"UPDATE channel_configs SET {field} = ? WHERE channel_name = ?",
                (value, channel),
            )
            await conn.commit()

    async def set_channel_field_all(self, field: str, value) -> None:
        if field not in _VALID_CHANNEL_CONFIG_COLS:
            raise ValueError(f"Invalid channel_configs column: {field!r}")
        async with aiosqlite.connect(self.db_file) as conn:
            await conn.execute(f"UPDATE channel_configs SET {field} = ?", (value,))
            await conn.commit()

    async def get_channel_field(self, channel: str, field: str):
        if field not in _VALID_CHANNEL_CONFIG_COLS:
            raise ValueError(f"Invalid channel_configs column: {field!r}")
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                f"SELECT {field} FROM channel_configs WHERE channel_name = ?", (channel,)
            )
            row = await c.fetchone()
            return row[0] if row else None

    async def get_all_channels_field(self, field: str) -> list:
        if field not in _VALID_CHANNEL_CONFIG_COLS:
            raise ValueError(f"Invalid channel_configs column: {field!r}")
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(f"SELECT channel_name, {field} FROM channel_configs")
            return await c.fetchall()

    async def get_channel_auth(self, channel: str) -> tuple | None:
        """Return (owner, trusted_users) for a channel."""
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                "SELECT owner, trusted_users FROM channel_configs WHERE channel_name = ?",
                (channel,),
            )
            return await c.fetchone()

    async def voice_preset_exists(self, voice_code: str) -> bool:
        """Return True if voice_code is a known preset in the voice_options table."""
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                "SELECT 1 FROM voice_options WHERE voice_code = ? LIMIT 1",
                (voice_code,),
            )
            return await c.fetchone() is not None

    async def get_all_ignored_users(self, channel: str | None = None) -> list:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            if channel:
                await c.execute(
                    "SELECT channel_name, ignored_users FROM channel_configs WHERE channel_name = ?",
                    (channel,),
                )
            else:
                await c.execute(
                    "SELECT channel_name, ignored_users FROM channel_configs WHERE join_channel = 1 ORDER BY channel_name"
                )
            return await c.fetchall()

    async def set_lore_config(self, channel: str, enabled_lore: str, lore_bias: float) -> None:
        async with aiosqlite.connect(self.db_file) as conn:
            await conn.execute(
                "UPDATE channel_configs SET enabled_lore = ?, lore_bias = ? WHERE channel_name = ?",
                (enabled_lore, lore_bias, channel),
            )
            await conn.commit()

    # ── Custom commands ─────────────────────────────────────────────────────

    async def get_commands(self, channel: str) -> list:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                "SELECT command_name, response_template FROM custom_commands WHERE channel_name = ?",
                (channel,),
            )
            return await c.fetchall()

    async def get_command(self, channel: str, name: str) -> str | None:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                "SELECT response_template FROM custom_commands WHERE channel_name = ? AND command_name = ?",
                (channel, name),
            )
            row = await c.fetchone()
            return row[0] if row else None

    async def insert_command(self, channel: str, name: str, template: str) -> None:
        async with aiosqlite.connect(self.db_file) as conn:
            await conn.execute(
                "INSERT INTO custom_commands (channel_name, command_name, response_template) VALUES (?, ?, ?)",
                (channel, name, template),
            )
            await conn.commit()

    async def upsert_command(self, channel: str, name: str, template: str) -> None:
        async with aiosqlite.connect(self.db_file) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO custom_commands (channel_name, command_name, response_template) VALUES (?, ?, ?)",
                (channel, name, template),
            )
            await conn.commit()

    async def update_command(self, channel: str, name: str, template: str) -> bool:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                "UPDATE custom_commands SET response_template = ? WHERE channel_name = ? AND command_name = ?",
                (template, channel, name),
            )
            await conn.commit()
            return c.rowcount > 0

    async def delete_command(self, channel: str, name: str) -> bool:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                "DELETE FROM custom_commands WHERE channel_name = ? AND command_name = ?",
                (channel, name),
            )
            await conn.commit()
            return c.rowcount > 0

    # ── Custom grammar ──────────────────────────────────────────────────────

    async def get_grammar_all(self, channel: str) -> list:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                "SELECT rule_name, options_json FROM custom_grammar WHERE channel_name = ?",
                (channel,),
            )
            return await c.fetchall()

    async def get_grammar_rule(self, channel: str, rule: str) -> str | None:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                "SELECT options_json FROM custom_grammar WHERE channel_name = ? AND rule_name = ?",
                (channel, rule),
            )
            row = await c.fetchone()
            return row[0] if row else None

    async def upsert_grammar_rule(self, channel: str, rule: str, options_json: str) -> None:
        async with aiosqlite.connect(self.db_file) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO custom_grammar (channel_name, rule_name, options_json) VALUES (?, ?, ?)",
                (channel, rule, options_json),
            )
            await conn.commit()

    async def delete_grammar_rule(self, channel: str, rule: str) -> None:
        async with aiosqlite.connect(self.db_file) as conn:
            await conn.execute(
                "DELETE FROM custom_grammar WHERE channel_name = ? AND rule_name = ?",
                (channel, rule),
            )
            await conn.commit()

    async def delete_grammar_all(self, channel: str) -> None:
        async with aiosqlite.connect(self.db_file) as conn:
            await conn.execute("DELETE FROM custom_grammar WHERE channel_name = ?", (channel,))
            await conn.commit()

    async def import_grammar(self, channel: str, rules: dict) -> None:
        async with aiosqlite.connect(self.db_file) as conn:
            await conn.execute("DELETE FROM custom_grammar WHERE channel_name = ?", (channel,))
            import json
            for rule_name, options_list in rules.items():
                await conn.execute(
                    "INSERT INTO custom_grammar (channel_name, rule_name, options_json) VALUES (?, ?, ?)",
                    (channel, rule_name, json.dumps(options_list)),
                )
            await conn.commit()

    # ── Timed message pools ─────────────────────────────────────────────────

    async def get_timed_pools(self, channel: str) -> list:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                "SELECT pool_name, interval_minutes FROM timed_message_pools WHERE channel_name = ?",
                (channel,),
            )
            return await c.fetchall()

    async def pool_exists(self, channel: str, pool: str) -> bool:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                "SELECT 1 FROM timed_message_pools WHERE channel_name = ? AND pool_name = ?",
                (channel, pool),
            )
            return (await c.fetchone()) is not None

    async def insert_timed_pool(self, channel: str, pool: str, interval: int) -> None:
        async with aiosqlite.connect(self.db_file) as conn:
            await conn.execute(
                "INSERT INTO timed_message_pools (channel_name, pool_name, interval_minutes) VALUES (?, ?, ?)",
                (channel, pool, interval),
            )
            await conn.commit()

    async def upsert_timed_pool(self, channel: str, pool: str, interval: int) -> None:
        async with aiosqlite.connect(self.db_file) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO timed_message_pools (channel_name, pool_name, interval_minutes) VALUES (?, ?, ?)",
                (channel, pool, interval),
            )
            await conn.commit()

    async def delete_timed_pool(self, channel: str, pool: str) -> bool:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                "DELETE FROM timed_message_pools WHERE channel_name = ? AND pool_name = ?",
                (channel, pool),
            )
            await conn.commit()
            return c.rowcount > 0

    async def get_pool_messages(self, channel: str, pool: str) -> list:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                "SELECT message_text FROM timed_messages WHERE channel_name = ? AND pool_name = ?",
                (channel, pool),
            )
            rows = await c.fetchall()
            return [r[0] for r in rows]

    async def count_pool_messages(self, channel: str, pool: str) -> int:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                "SELECT COUNT(*) FROM timed_messages WHERE channel_name = ? AND pool_name = ?",
                (channel, pool),
            )
            return (await c.fetchone())[0]

    async def add_pool_message(self, channel: str, pool: str, text: str) -> None:
        async with aiosqlite.connect(self.db_file) as conn:
            await conn.execute(
                "INSERT INTO timed_messages (pool_name, channel_name, message_text) VALUES (?, ?, ?)",
                (pool, channel, text),
            )
            await conn.commit()

    async def delete_pool_messages(self, channel: str, pool: str) -> None:
        async with aiosqlite.connect(self.db_file) as conn:
            await conn.execute(
                "DELETE FROM timed_messages WHERE channel_name = ? AND pool_name = ?",
                (channel, pool),
            )
            await conn.commit()

    async def upsert_timed_pool_with_messages(
        self, channel: str, pool: str, interval: int, messages: list
    ) -> None:
        async with aiosqlite.connect(self.db_file) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO timed_message_pools (channel_name, pool_name, interval_minutes) VALUES (?, ?, ?)",
                (channel, pool, interval),
            )
            await conn.execute(
                "DELETE FROM timed_messages WHERE channel_name = ? AND pool_name = ?",
                (channel, pool),
            )
            for msg in messages:
                await conn.execute(
                    "INSERT INTO timed_messages (channel_name, pool_name, message_text) VALUES (?, ?, ?)",
                    (channel, pool, str(msg)),
                )
            await conn.commit()

    # ── Channel variables ───────────────────────────────────────────────────

    async def get_variable(self, channel: str, name: str) -> int:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                "SELECT var_value FROM channel_variables WHERE channel_name = ? AND var_name = ?",
                (channel, name),
            )
            row = await c.fetchone()
            return row[0] if row else 0

    async def set_variable(self, channel: str, name: str, value: int) -> None:
        async with aiosqlite.connect(self.db_file) as conn:
            await conn.execute(
                "INSERT INTO channel_variables (channel_name, var_name, var_value) VALUES (?, ?, ?) "
                "ON CONFLICT(channel_name, var_name) DO UPDATE SET var_value = ?",
                (channel, name, value, value),
            )
            await conn.commit()

    async def increment_variable(self, channel: str, name: str, by: int) -> int:
        async with aiosqlite.connect(self.db_file) as conn:
            await conn.execute(
                "INSERT INTO channel_variables (channel_name, var_name, var_value) VALUES (?, ?, ?) "
                "ON CONFLICT(channel_name, var_name) DO UPDATE SET var_value = var_value + ?",
                (channel, name, by, by),
            )
            await conn.commit()
            c = await conn.cursor()
            await c.execute(
                "SELECT var_value FROM channel_variables WHERE channel_name = ? AND var_name = ?",
                (channel, name),
            )
            return (await c.fetchone())[0]

    async def get_all_variables(self, channel: str) -> dict:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute(
                "SELECT var_name, var_value FROM channel_variables WHERE channel_name = ?",
                (channel,),
            )
            rows = await c.fetchall()
            return {r[0]: r[1] for r in rows}

    # ── TTS logs ────────────────────────────────────────────────────────────

    async def log_tts(
        self, message_id: str, channel: str, file_path: str, voice_preset: str, message: str,
        timestamp: str | None = None,
    ) -> None:
        if timestamp is None:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        async with aiosqlite.connect(self.db_file) as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO tts_logs (message_id, channel, timestamp, file_path, voice_preset, message) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (message_id, channel, timestamp, file_path, voice_preset, message),
            )
            await conn.commit()

    async def get_tts_logs(self, channel: str | None = None, limit: int = 100) -> list:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            if channel:
                await c.execute(
                    "SELECT timestamp, channel, voice_preset, message, file_path FROM tts_logs "
                    "WHERE channel = ? ORDER BY timestamp DESC LIMIT ?",
                    (channel, limit),
                )
            else:
                await c.execute(
                    "SELECT timestamp, channel, voice_preset, message, file_path FROM tts_logs "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                )
            return await c.fetchall()

    async def get_all_tts_log_files(self) -> list:
        async with aiosqlite.connect(self.db_file) as conn:
            c = await conn.cursor()
            await c.execute("SELECT message_id, file_path FROM tts_logs")
            return await c.fetchall()

    async def delete_tts_log(self, message_id: str) -> None:
        async with aiosqlite.connect(self.db_file) as conn:
            await conn.execute("DELETE FROM tts_logs WHERE message_id = ?", (message_id,))
            await conn.commit()

    # ── Sync path for tts.py thread pool ────────────────────────────────────

    def get_tts_config_sync(self, channel: str) -> dict:
        with _sync_lock:
            conn = sqlite3.connect(self.db_file, timeout=15.0)
            try:
                c = conn.cursor()
                c.execute(
                    """SELECT voice_preset, bark_model, tts_provider,
                             rvc_model, chatterbox_temperature, chatterbox_exaggeration,
                             bark_text_temp, bark_waveform_temp, rvc_pitch, rvc_index_rate,
                             rvc_api_url, tts_enabled, tts_delay_enabled
                       FROM channel_configs WHERE channel_name = ?""",
                    (channel,),
                )
                row = c.fetchone()
                if row:
                    return {
                        "voice_preset": row[0] or "v2/en_speaker_5",
                        "bark_model": row[1] or "regular",
                        "tts_provider": row[2] or "bark",
                        "rvc_model": row[3] or "",
                        "chatterbox_temperature": row[4] if row[4] is not None else 0.8,
                        "chatterbox_exaggeration": row[5] if row[5] is not None else 0.5,
                        "bark_text_temp": row[6] if row[6] is not None else 0.7,
                        "bark_waveform_temp": row[7] if row[7] is not None else 0.7,
                        "rvc_pitch": row[8] if row[8] is not None else 0,
                        "rvc_index_rate": row[9] if row[9] is not None else 0.75,
                        "rvc_api_url": row[10] or "http://127.0.0.1:5051",
                        "tts_enabled": bool(row[11]),
                        "tts_delay_enabled": bool(row[12]),
                    }
            except sqlite3.Error as e:
                logging.error(f"Database.get_tts_config_sync error for {channel}: {e}")
            finally:
                conn.close()
        return {
            "voice_preset": "v2/en_speaker_5",
            "bark_model": "regular",
            "tts_provider": "bark",
            "rvc_model": "",
            "chatterbox_temperature": 0.8,
            "chatterbox_exaggeration": 0.5,
            "bark_text_temp": 0.7,
            "bark_waveform_temp": 0.7,
            "rvc_pitch": 0,
            "rvc_index_rate": 0.75,
            "rvc_api_url": "http://127.0.0.1:5051",
            "tts_enabled": False,
            "tts_delay_enabled": False,
        }

    def log_tts_sync(
        self,
        message_id: str,
        channel: str,
        file_path: str,
        voice_preset: str,
        message: str,
        timestamp: str | None = None,
    ) -> int | None:
        if timestamp is None:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        if not voice_preset:
            voice_preset = "v2/en_speaker_5"
        with _sync_lock:
            conn = sqlite3.connect(self.db_file, timeout=10.0)
            try:
                c = conn.cursor()
                c.execute(
                    "INSERT OR IGNORE INTO tts_logs "
                    "(message_id, channel, timestamp, file_path, voice_preset, message) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (message_id, channel, timestamp, file_path, voice_preset, message),
                )
                conn.commit()
                if c.rowcount == 0:
                    logging.warning(
                        f"[log_tts_sync] Insert IGNORED for message_id {message_id} (likely duplicate)."
                    )
                    c.execute("SELECT ROWID FROM tts_logs WHERE message_id = ?", (message_id,))
                    row = c.fetchone()
                    return row[0] if row else None
                return c.lastrowid
            except sqlite3.Error as e:
                logging.error(f"[log_tts_sync] SQLite error for {message_id}: {e}")
                return None
            finally:
                conn.close()

    def fetch_latest_message_sync(self) -> tuple:
        with _sync_lock:
            conn = sqlite3.connect(self.db_file, timeout=15.0)
            try:
                c = conn.cursor()
                c.execute(
                    "SELECT id, channel, timestamp, message_length, message "
                    "FROM messages ORDER BY id DESC LIMIT 1"
                )
                result = c.fetchone()
                return result if result else (None, None, None, None, None)
            except sqlite3.Error as e:
                logging.error(f"fetch_latest_message_sync error: {e}")
                raise
            finally:
                conn.close()

    # ── General-purpose connection helpers ──────────────────────────────────

    @contextmanager
    def connect_sync(self, timeout: float = 15.0):
        """Yield a raw sqlite3 connection using this database's file path."""
        conn = sqlite3.connect(self.db_file, timeout=timeout)
        try:
            yield conn
        finally:
            conn.close()

    @asynccontextmanager
    async def connect_async(self):
        """Async context manager yielding an aiosqlite connection."""
        async with aiosqlite.connect(self.db_file) as conn:
            yield conn

    # ── Bot status / heartbeat ───────────────────────────────────────────────

    def set_bot_status_sync(self, key: str, value: str) -> None:
        with self.connect_sync() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO bot_status (key, value, timestamp) VALUES (?, ?, ?)",
                (key, value, datetime.now().isoformat()),
            )
            conn.commit()

    # ── Connection history ───────────────────────────────────────────────────

    def log_connection_event_sync(
        self, event_type: str, details_json: str, attempt_number: int = 0
    ) -> None:
        try:
            with self.connect_sync() as conn:
                conn.execute(
                    "INSERT INTO connection_history (timestamp, event_type, details, attempt_number) VALUES (?, ?, ?, ?)",
                    (datetime.now().isoformat(), event_type, details_json, attempt_number),
                )
                conn.commit()
        except Exception as e:
            logging.error(f"log_connection_event_sync error: {e}")

    # ── Cache build log ──────────────────────────────────────────────────────

    def get_cache_build_times_sync(self) -> dict:
        with self.connect_sync() as conn:
            c = conn.cursor()
            c.execute("SELECT channel_name, timestamp FROM cache_build_log ORDER BY timestamp DESC")
            seen: dict = {}
            for channel_name, ts in c.fetchall():
                if channel_name not in seen:
                    seen[channel_name] = ts
            return seen

    def save_cache_build_time_sync(
        self, channel_name: str, duration: float, success: bool, message: str = ""
    ) -> None:
        with self.connect_sync() as conn:
            conn.execute(
                "INSERT INTO cache_build_log (channel_name, timestamp, duration, success, message) VALUES (?, ?, ?, ?, ?)",
                (channel_name, datetime.now().timestamp(), duration, success, message),
            )
            conn.commit()

    def get_schema_version_sync(self) -> int:
        """Return the schema revision this DB is stamped at (0 if untracked)."""
        with self.connect_sync() as conn:
            c = conn.cursor()
            c.execute("SELECT MAX(version) FROM schema_version")
            row = c.fetchone()
            return row[0] if row and row[0] is not None else 0

    def replace_cache_build_times_sync(self, times: dict) -> None:
        """Persist the full set of cache build times, one row per channel.

        Replaces any prior row for each channel so the table stays a current-state
        snapshot rather than growing unbounded. Mirrors MarkovBrain's flush-the-whole-
        dict semantics now that the bot owns this table (no external writer).
        """
        with self.connect_sync() as conn:
            c = conn.cursor()
            for channel_name, ts in times.items():
                c.execute("DELETE FROM cache_build_log WHERE channel_name = ?", (channel_name,))
                c.execute(
                    "INSERT INTO cache_build_log (channel_name, timestamp, duration, success, message) "
                    "VALUES (?, ?, 0.0, 1, '')",
                    (channel_name, ts),
                )
            conn.commit()

    # ── Channel settings (sync bulk load for __init__) ───────────────────────

    def load_channel_settings_sync(self) -> dict:
        """Return {channel: {trusted_users, ignored_users, time_between_messages, lines_between_messages}}."""
        settings: dict = {}
        with self.connect_sync() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT channel_name, trusted_users, ignored_users, "
                "time_between_messages, lines_between_messages FROM channel_configs"
            )
            for channel, trusted, ignored, time_between, lines_between in c.fetchall():
                settings[channel] = {
                    "trusted_users": [u for u in (trusted or "").split(",") if u],
                    "ignored_users": [u for u in (ignored or "").split(",") if u],
                    "time_between_messages": time_between,
                    "lines_between_messages": lines_between,
                }
        return settings

    def get_all_join_channels_sync(self) -> list:
        """Sync version of get_all_join_channels for use before event loop starts."""
        with self.connect_sync() as conn:
            c = conn.cursor()
            c.execute("SELECT channel_name FROM channel_configs WHERE join_channel = 1 ORDER BY channel_name")
            return [r[0] for r in c.fetchall()]

    def insert_channels_sync(self, channels: list) -> None:
        """Insert channels with default config if they don't already exist."""
        with self.connect_sync() as conn:
            for channel in channels:
                conn.execute(
                    "INSERT INTO channel_configs "
                    "(channel_name, tts_enabled, voice_enabled, join_channel, owner, "
                    "trusted_users, ignored_users, use_general_model, lines_between_messages, "
                    "time_between_messages, currently_connected, tts_delay_enabled, tts_reward) "
                    "SELECT ?, 0, 0, 1, ?, '', '', 1, 100, 0, 0, 0, '' "
                    "WHERE NOT EXISTS(SELECT 1 FROM channel_configs WHERE channel_name = ?)",
                    (channel, channel, channel),
                )
            conn.commit()
