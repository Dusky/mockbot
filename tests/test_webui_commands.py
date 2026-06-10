import sqlite3
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

import bot.webui.auth as auth
import bot.webui.cmds_api as cmds_api
from bot.webui.app import create_app


def make_db(tmp_path, channel="firestarman", owner="streamerguy"):
    db = str(tmp_path / "t.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE channel_configs (channel_name TEXT, owner TEXT)")
    conn.execute("INSERT INTO channel_configs VALUES (?,?)", (channel, owner))
    conn.execute("""CREATE TABLE custom_commands (
        channel_name TEXT, command_name TEXT, response_template TEXT NOT NULL,
        PRIMARY KEY(channel_name, command_name))""")
    conn.commit(); conn.close()
    return db


def client_db(tmp_path):
    db = make_db(tmp_path)
    app = create_app(bot=None, auth_cfg=auth.AuthConfig(client_id="c", client_secret="s"),
                     db_file=db, owner="bossman", secret_key="test-secret")
    return TestClient(app), db


def _login(client, monkeypatch, login_name):
    state = parse_qs(urlparse(
        client.get("/auth/twitch/login", follow_redirects=False).headers["location"]).query)["state"][0]

    async def ex(cfg, code): return {"access_token": "tok"}
    async def fu(cfg, tok): return {"id": "1", "login": login_name, "display_name": login_name}
    monkeypatch.setattr(auth, "exchange_code", ex)
    monkeypatch.setattr(auth, "fetch_user", fu)
    client.get(f"/auth/twitch/callback?code=x&state={state}", follow_redirects=False)


# ── cmds_api unit ───────────────────────────────────────────────────────────────

def test_upsert_lowercases_and_updates(tmp_path):
    db = make_db(tmp_path)
    ok, err = cmds_api.upsert_command(db, "firestarman", "!Hello", "hi #{sender}")
    assert ok and not err
    cmds = cmds_api.list_commands(db, "firestarman")
    assert cmds == [{"command_name": "!hello", "response_template": "hi #{sender}"}]
    # upsert again updates in place (no duplicate)
    cmds_api.upsert_command(db, "firestarman", "!hello", "yo #{sender}")
    cmds = cmds_api.list_commands(db, "firestarman")
    assert len(cmds) == 1 and cmds[0]["response_template"] == "yo #{sender}"


def test_upsert_validates_required_fields(tmp_path):
    db = make_db(tmp_path)
    assert cmds_api.upsert_command(db, "firestarman", "", "x")[0] is False
    assert cmds_api.upsert_command(db, "firestarman", "!x", "")[0] is False


def test_delete(tmp_path):
    db = make_db(tmp_path)
    cmds_api.upsert_command(db, "firestarman", "!bye", "later")
    assert cmds_api.delete_command(db, "firestarman", "!BYE") is True   # case-insensitive
    assert cmds_api.list_commands(db, "firestarman") == []
    assert cmds_api.delete_command(db, "firestarman", "!nope") is False


# ── routes ──────────────────────────────────────────────────────────────────────

def test_command_routes_require_login_and_authz(tmp_path, monkeypatch):
    client, _ = client_db(tmp_path)
    assert client.get("/api/commands/firestarman").status_code == 401
    _login(client, monkeypatch, "firestarman")
    assert client.get("/api/commands/firestarman").status_code == 200
    assert client.get("/api/commands/someoneelse").status_code == 403


def test_command_crud_via_api(tmp_path, monkeypatch):
    client, db = client_db(tmp_path)
    _login(client, monkeypatch, "firestarman")
    # create
    r = client.post("/api/commands/firestarman", json={"command_name": "!hi", "response_template": "yo"})
    assert r.status_code == 200 and r.json()["command_name"] == "!hi"
    assert any(c["command_name"] == "!hi" for c in client.get("/api/commands/firestarman").json()["commands"])
    # bad input -> 400
    assert client.post("/api/commands/firestarman", json={"command_name": "", "response_template": "x"}).status_code == 400
    # delete
    assert client.delete("/api/commands/firestarman/!hi").json()["deleted"] is True
    assert client.get("/api/commands/firestarman").json()["commands"] == []


def test_commands_page_gated(tmp_path, monkeypatch):
    client, _ = client_db(tmp_path)
    assert client.get("/commands", follow_redirects=False).status_code == 302
    _login(client, monkeypatch, "firestarman")
    page = client.get("/commands")
    assert page.status_code == 200 and 'x-data="commands"' in page.text
