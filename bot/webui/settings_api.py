"""Settings control surface helpers — schema, read, and validated apply.

All rendering/validation flows from bot.settings_registry (the single source of
truth), so the web form can't drift from the TUI/chat /set command. Writes go
straight to channel_configs with the column validated against the registry
allowlist (the f-string interpolation is safe: keys are checked against
sr.all_columns() first).
"""
from __future__ import annotations

import sqlite3

import bot.settings_registry as sr


def settings_schema() -> list:
    """Form schema grouped by category, derived from the registry."""
    cats = []
    for category, items in sr.by_category().items():
        fields = []
        for s in items:
            choices = list(s.choices)
            if s.dynamic_choices:
                try:
                    choices = list(s.dynamic_choices())
                except Exception:
                    choices = []
            fields.append({
                "key": s.key,
                "control": s.control,         # bool_select | select | input | lore_button
                "kind": s.kind,
                "description": s.description,
                "choices": [[label, value] for label, value in choices],
                "min": s.minimum,
                "max": s.maximum,
            })
        cats.append({"category": category, "fields": fields})
    return cats


def get_values(db_file: str, channel: str) -> dict:
    """Current channel_configs values for a channel (secrets excluded)."""
    ch = channel.lstrip("#").lower()
    try:
        conn = sqlite3.connect(db_file)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(channel_configs)")]
        row = conn.execute(
            f"SELECT {','.join(cols)} FROM channel_configs WHERE lower(channel_name)=?", (ch,)
        ).fetchone()
        conn.close()
        if not row:
            return {}
        values = dict(zip(cols, row))
        values.pop("tts_token", None)  # never expose the private TTS secret
        return values
    except Exception:
        return {}


def apply_settings(db_file: str, channel: str, payload: dict) -> tuple[dict, dict]:
    """Coerce + validate + persist a {key: raw} map. Returns (applied, errors)."""
    ch = channel.lstrip("#").lower()
    editable = set(sr.editable_keys())
    columns = set(sr.all_columns())
    applied, errors = {}, {}
    conn = sqlite3.connect(db_file)
    try:
        for key, raw in (payload or {}).items():
            if key not in editable or key not in columns:
                errors[key] = "not an editable setting"
                continue
            try:
                value = sr.coerce(key, raw)
            except ValueError as e:
                errors[key] = str(e)
                continue
            ok, msg = sr.validate(key, value)
            if not ok:
                errors[key] = msg
                continue
            conn.execute(
                f"UPDATE channel_configs SET {key}=? WHERE lower(channel_name)=?", (value, ch)
            )
            applied[key] = value
        conn.commit()
    finally:
        conn.close()
    return applied, errors
