# Mockbot — Session Handoff

## What this is

Mockbot is a Twitch chatbot (Python/TwitchIO) with Markov-chain text generation, multi-provider TTS (Bark, Chatterbox, RVC), a Textual TUI dashboard, and an OBS overlay. Main entry point: `main.py`. Uses `.venv/bin/python`.

GitHub: https://github.com/Dusky/mockbot  
DB: `messages.db` (SQLite, 135MB+, canonical — ignore any other .db files)

---

## What was just done (3-phase tech debt refactor)

### Phase 1 — DB + Config consolidation
- Created `bot/database.py` — single `Database` class, all DB access goes through it
- Created `bot/config.py` — `Config` singleton, replaces 17 scattered `ConfigParser` reads
- Migrated `commands.py`, `tts.py`, `tui.py`, `ui_managers.py`, `overlay.py` off raw connections
- Database exposes `connect_sync()` and `connect_async()` context managers for complex inline queries
- Fixed wrong import paths (`utils.tts` → `bot.tts`) left by a remote agent session

### Phase 2 — Dead code + color consolidation
- Created `bot/colors.py` — single source for ANSI constants; removed inline definitions from `core.py`, `commands.py`, `logger.py`
- Fixed `color_control.py` bug: `get_channel_color()` was reading `user_colors` instead of `channel_colors`
- Removed dead instance vars from `Bot.__init__`: `chat_line_count`, `trusted_users`, `ignored_users`, `user_colors`, `channel_colors`, `color_manager`, `cache_update_threshold`, `first_model_update`
- Deleted 4 orphaned zero-byte `.db` files and 10 scratch/test files from repo root
- `overlay.py` now uses `Database.get_all_variables()` via `init_overlay_db()`

### Phase 3 — core.py decomposition
`core.py` went from **3,084 → 1,342 lines**. Extracted:

| New file | What moved there |
|---|---|
| `bot/utils.py` | `LRUCache`, `convert_size()` |
| `bot/connection.py` | `ConnectionStateManager` (reconnect/backoff) |
| `bot/brain.py` | `MarkovBrain` — model loading, building, generation, caching |
| `bot/channel_manager.py` | `ChannelManager` — join/leave, settings, channel state |
| `bot/custom_commands.py` | `CustomCommandHandler` — Tracery grammar, variable/moderation macros |
| `bot/tasks/heartbeat.py` | Heartbeat loop + file writer |
| `bot/tasks/db_writer.py` | Async bulk message queue flusher |
| `bot/tasks/timed_messages.py` | Scheduled channel message loop |
| `bot/tasks/sleep_monitor.py` | Inactivity sleep mode |
| `bot/tasks/message_requests.py` | File-based message request poller |
| `bot/tasks/live_stream_monitor.py` | Stream status monitor |

`Bot` class is now a thin dispatcher. **Proxy properties on Bot** preserve the external interface so `tui.py`, `ui_managers.py`, and `commands.py` needed zero changes:
- `bot._joined_channels` → `bot.channel_manager._joined_channels`
- `bot.channel_settings` → `bot.channel_manager.channel_settings`
- `bot.general_model` → `bot.brain.general_model`
- `bot.models` → `bot.brain.models`
- etc.

---

## Current architecture

```
main.py
  └── bot/core.py          Bot class (~707 lines, thin dispatcher)
        ├── bot/brain.py           MarkovBrain
        ├── bot/channel_manager.py ChannelManager
        ├── bot/custom_commands.py CustomCommandHandler
        ├── bot/connection.py      ConnectionStateManager
        ├── bot/handlers/          Event/command bodies (delegated from Bot)
        │     ├── startup.py         event_ready 10-step sequence (run)
        │     ├── pubsub.py          Bits + Channel Point redemptions
        │     ├── tts.py             voice preset/delay lookup, generate_tts_sync, !speak
        │     └── raw_data.py        IRC NOTICE parsing
        ├── bot/tasks/             Background async loops
        ├── bot/database.py        Database DAO (all SQL lives here)
        ├── bot/config.py          Config singleton
        ├── bot/tts.py             TTS pipeline (Bark/Chatterbox/RVC)
        ├── bot/commands.py        !mockbot command handlers
        ├── bot/tui.py             Textual TUI dashboard
        ├── bot/ui_managers.py     TUI modal screens
        ├── bot/overlay.py         OBS WebSocket overlay
        ├── bot/logger.py          Logger with Rich + TUI callback
        ├── bot/colors.py          ANSI constants
        └── bot/utils.py           LRUCache, convert_size
```

**Handler pattern:** TwitchIO dispatches events by method name (`event_ready`,
`event_message`, …) and `@commands.command`s must live on the `Bot` class, so
those stay as *thin* methods that delegate into `bot/handlers/` functions taking
`bot` as the first arg — same shape as `bot/tasks/`. What remains in `core.py` as
real logic: `__init__` (composition), the proxy properties, `mockbot_wrapper`
(command sub-dispatch), and `event_message` (the message→generate→TTS pipeline).

---

## Known remaining issues (backlog)

### Medium priority
1. ~~**Voice preset validation removed**~~ — ✅ Fixed. `Database.voice_preset_exists()` checks the `voice_options` table, and the `commands.py` `voice_preset` setter rejects unknown codes with "Invalid voice preset" before saving (restores pre-refactor behavior via the DB abstraction layer).

2. ~~**`core.py` still 1,342 lines**~~ — ✅ Slimmed to **707 lines**. Extracted `event_ready`, the pubsub handlers, TTS orchestration (`get_channel_voice_preset`/`get_tts_delay_setting`/`generate_tts_sync`/`is_tts_enabled`/`handle_speak_command`), and `event_raw_data` into the new `bot/handlers/` package (thin delegators remain on `Bot`). Removed dead module-level functions (`fetch_users`, `insert_initial_channels_to_db`) and the duplicate `fetch_initial_channels` (now uses `Database.get_all_join_channels_sync()`). Pruned unused imports (`markovify`, `sqlite3`, `tabulate`, `LRUCache`/`convert_size`, `threading`, `timezone`, `PURPLE`). Also fixed a latent `NameError` in `handle_speak_command` (`author_name` was undefined → now `ctx.author.name`). **Structural only — no hot-path logic was rewritten.** What's left in `core.py` is `__init__`, proxy properties, `mockbot_wrapper`, and `event_message`.

3. ~~**`load_last_cache_build_times()` reads from JSON file**~~ — ✅ Fixed. `brain.py` now loads via `db.get_cache_build_times_sync()` and saves via the new `db.replace_cache_build_times_sync()` (one row per channel, replaces prior rows so the `cache_build_log` table stays a current-state snapshot instead of growing unbounded). The `cache/cache_build_times.json` file is no longer read or written. General model is keyed `general_markov_model.json` in memory / `general_markov` in the DB (legacy convention preserved).

### Low priority
4. ~~**Schema version tracking**~~ — ✅ Done. `db.py` now has a `schema_version` table and a `CURRENT_SCHEMA_VERSION` constant; `ensure_db_setup()` stamps and logs the version on startup, and `Database.get_schema_version_sync()` exposes it. Deliberately *not* version-gated: the existing per-column `PRAGMA`/`ALTER` checks remain idempotent and self-healing (PRAGMA is metadata-only, so the "slow" concern was negligible). The version is for tracking/observability and lays groundwork if gated migrations are ever wanted.

5. ~~**`overlay.py` `aiosqlite` import**~~ — ✅ Done (already clean). `overlay.py` no longer imports `aiosqlite` or `sqlite3`; `api_get_variables()` uses `Database.get_all_variables()`.

6. **`bot/db.py` is separate from `database.py`** — `db.py` contains `ensure_db_setup()` (schema creation + migrations), called from `Database.__init__`. **Intentional, working as designed** — it's the bootstrap layer and legitimately owns its own `sqlite3.connect`. No action.

7. ~~**`commands.py` imports `sqlite3`**~~ — ✅ Done. `IntegrityError` is now re-exported from `bot.database`; `commands.py` imports it from there and no longer touches `sqlite3` directly.

---

## Key patterns to know

**Database access:**
```python
# Async (most code)
async with self.db.connect_async() as conn:
    c = await conn.cursor()
    ...

# Sync (thread-pool or __init__ context)
with self.db.connect_sync() as conn:
    c = conn.cursor()
    ...

# Dedicated methods for common ops
cfg = self.db.get_tts_config_sync(channel)   # returns dict
await self.db.get_channel_config(channel)     # returns dict | None
await self.db.set_channel_field(ch, field, v) # safe column update
```

**Config:**
```python
from bot.config import config
config.owner          # str
config.tmi_token      # str
config.enable_tts     # bool
config.get('section', 'key', fallback=None)  # escape hatch
```

**Colors:**
```python
from bot.colors import YELLOW, RED, GREEN, PURPLE, RESET, CYAN, BRIGHT_GREEN, ...
```

**Markov generation:**
```python
msg = bot.brain.generate_message(channel_name)   # str | None
# or via proxy:
msg = bot.generate_message(channel_name)
```

---

## Verification status

All imports clean. `setup_bot()` instantiates correctly with real DB (392k messages loaded, generation works). Proxy layer fully tested — setters route to correct managers. `update_heartbeat_file()` writes correct JSON. Zero raw DB connects outside `database.py`/`db.py`. Color bug fix confirmed. Not tested live against Twitch (requires network + valid token).

---

## What to tell the new session

> "This is Mockbot, a Python Twitch chatbot. We just completed a 3-phase tech debt refactor — DB consolidation, dead code cleanup, and splitting a 3084-line core.py into focused modules. The codebase is clean and working. See HANDOFF.md for full context. I want to continue working on [X]."
