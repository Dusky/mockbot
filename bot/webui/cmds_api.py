"""Custom-command CRUD for the webui.

Backs the custom_commands table (channel_name, command_name, response_template).
Commands are matched in chat by the lowercased first token, so names are stored
lowercased here too. Channel-scoped; the routes enforce that the logged-in user
may manage the channel before calling these.
"""
from __future__ import annotations

import sqlite3


def list_commands(db_file: str, channel: str) -> list:
    ch = channel.lstrip("#").lower()
    conn = sqlite3.connect(db_file)
    try:
        rows = conn.execute(
            "SELECT command_name, response_template FROM custom_commands "
            "WHERE channel_name=? ORDER BY command_name", (ch,)
        ).fetchall()
        return [{"command_name": r[0], "response_template": r[1]} for r in rows]
    finally:
        conn.close()


def upsert_command(db_file: str, channel: str, name: str, template: str) -> tuple[bool, str]:
    ch = channel.lstrip("#").lower()
    name = (name or "").strip().lower()
    template = (template or "").strip()
    if not name:
        return False, "command name is required"
    if not template:
        return False, "response template is required"
    conn = sqlite3.connect(db_file)
    try:
        conn.execute(
            "INSERT INTO custom_commands (channel_name, command_name, response_template) "
            "VALUES (?,?,?) ON CONFLICT(channel_name, command_name) "
            "DO UPDATE SET response_template=excluded.response_template",
            (ch, name, template),
        )
        conn.commit()
        return True, ""
    finally:
        conn.close()


def delete_command(db_file: str, channel: str, name: str) -> bool:
    ch = channel.lstrip("#").lower()
    name = (name or "").strip().lower()
    conn = sqlite3.connect(db_file)
    try:
        cur = conn.execute(
            "DELETE FROM custom_commands WHERE channel_name=? AND command_name=?", (ch, name)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
