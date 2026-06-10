import asyncio
import sqlite3
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

import bot.webui.auth as auth
from bot.events import ChatMessage, TtsGenerated
from bot.webui.app import WebUIHub, _serialize, create_app


def full_db(tmp_path, channel="firestarman", owner="streamerguy"):
    db = str(tmp_path / "t.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE channel_configs (
        channel_name TEXT, owner TEXT, use_general_model INT, tts_enabled INT,
        voice_enabled INT, random_chance REAL, time_between_messages INT, tts_token TEXT DEFAULT '')""")
    conn.execute("INSERT INTO channel_configs VALUES (?,?,1,1,1,15,15,'')", (channel, owner))
    conn.commit(); conn.close()
    return db


def client_db(tmp_path, owner="bossman"):
    db = full_db(tmp_path)
    app = create_app(bot=None, auth_cfg=auth.AuthConfig(client_id="c", client_secret="s"),
                     db_file=db, owner=owner, secret_key="test-secret")
    return TestClient(app), db


def _login(client, monkeypatch, login_name):
    state = parse_qs(urlparse(
        client.get("/auth/twitch/login", follow_redirects=False).headers["location"]).query)["state"][0]

    async def ex(cfg, code): return {"access_token": "tok"}
    async def fu(cfg, tok): return {"id": "1", "login": login_name, "display_name": login_name}
    monkeypatch.setattr(auth, "exchange_code", ex)
    monkeypatch.setattr(auth, "fetch_user", fu)
    client.get(f"/auth/twitch/callback?code=x&state={state}", follow_redirects=False)


# ── serialization / hub ──────────────────────────────────────────────────────

def test_serialize_chat_message():
    assert _serialize(ChatMessage("fire", "bob", "hi", color="#abcdef")) == {
        "event": "chat_message", "channel": "fire", "author": "bob", "text": "hi",
        "color": "#abcdef", "is_bot": False, "timestamp": "",
    }


def test_serialize_tts_includes_author_and_provider():
    d = _serialize(TtsGenerated("fire", "m", "static/outputs/fire/x.wav",
                                text="hi", provider="bark", voice="v", author="bob"))
    assert d["event"] == "new_tts_entry" and d["author"] == "bob" and d["provider"] == "bark"
    assert d["tts_url"] == "static/outputs/fire/x.wav" and d["voice"] == "v"


def test_hub_relays_chat_message():
    async def go():
        hub = WebUIHub(); q = hub.register()
        hub.broadcast(ChatMessage("fire", "bob", "hello", color="#abc", timestamp="t"))
        msg = q.get_nowait()
        assert msg["event"] == "chat_message" and msg["author"] == "bob" and msg["text"] == "hello"
    asyncio.run(go())


# ── /api/status ──────────────────────────────────────────────────────────────

def test_api_status_requires_login(tmp_path):
    client, _ = client_db(tmp_path)
    assert client.get("/api/status").status_code == 401


def test_api_status_returns_channel_state(tmp_path, monkeypatch):
    client, _ = client_db(tmp_path)
    _login(client, monkeypatch, "firestarman")
    s = client.get("/api/status").json()
    assert "pid" in s and s["channels"]
    fire = next(c for c in s["channels"] if c["channel"] == "firestarman")
    assert fire["general"] is True and fire["tts"] is True and fire["chance"] == 15 and fire["delay"] == 15


# ── /monitor + static ──────────────────────────────────────────────────────────

def test_monitor_redirects_anon_and_serves_for_user(tmp_path, monkeypatch):
    client, _ = client_db(tmp_path)
    r = client.get("/monitor", follow_redirects=False)
    assert r.status_code == 302 and "/auth/twitch/login" in r.headers["location"]
    _login(client, monkeypatch, "firestarman")
    page = client.get("/monitor")
    assert page.status_code == 200 and "mockbot" in page.text and 'x-data="dashboard"' in page.text


def test_static_assets_served(tmp_path):
    client, _ = client_db(tmp_path)
    assert client.get("/static/dashboard.js").status_code == 200
    alp = client.get("/static/vendor/alpine.min.js")
    assert alp.status_code == 200 and len(alp.content) > 1000


def test_root_redirects_logged_in_user_to_monitor(tmp_path, monkeypatch):
    client, _ = client_db(tmp_path)
    _login(client, monkeypatch, "firestarman")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/monitor"
