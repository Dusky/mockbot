import asyncio
import sqlite3

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import pytest

import bot.webui.auth as auth
import bot.webui.tts_source as tts_src
from bot.events import ConnectionStateChanged, TtsGenerated
from bot.webui.app import WebUIHub, _tts_payload, create_app


def make_db(tmp_path, channel="firestarman", owner="streamerguy"):
    db = str(tmp_path / "t.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE channel_configs (channel_name TEXT, owner TEXT)")
    conn.execute("INSERT INTO channel_configs VALUES (?,?)", (channel, owner))
    conn.commit()
    conn.close()
    tts_src.ensure_token_column(db)
    return db


# ── token helpers ──────────────────────────────────────────────────────────────

def test_get_or_create_is_idempotent_and_resolvable(tmp_path):
    db = make_db(tmp_path)
    t1 = tts_src.get_or_create_tts_token(db, "firestarman")
    t2 = tts_src.get_or_create_tts_token(db, "#FireStarman")  # case/hash insensitive
    assert t1 and t1 == t2
    assert tts_src.channel_for_token(db, t1) == "firestarman"
    assert tts_src.channel_for_token(db, "nope") is None


def test_rotate_invalidates_old_token(tmp_path):
    db = make_db(tmp_path)
    old = tts_src.get_or_create_tts_token(db, "firestarman")
    new = tts_src.rotate_tts_token(db, "firestarman")
    assert new and new != old
    assert tts_src.channel_for_token(db, old) is None
    assert tts_src.channel_for_token(db, new) == "firestarman"


def test_unknown_channel_returns_none(tmp_path):
    db = make_db(tmp_path)
    assert tts_src.get_or_create_tts_token(db, "ghost") is None
    assert tts_src.rotate_tts_token(db, "ghost") is None


def test_authorized_channels(tmp_path):
    db = make_db(tmp_path, channel="firestarman", owner="streamerguy")
    assert tts_src.authorized_channels(db, "firestarman") == ["firestarman"]
    assert tts_src.authorized_channels(db, "streamerguy") == ["firestarman"]
    assert tts_src.authorized_channels(db, "bossman", owner="bossman") == ["firestarman"]  # bot owner sees all
    assert tts_src.authorized_channels(db, "rando") == []


# ── payload mapping ──────────────────────────────────────────────────────────────

def test_tts_payload_maps_path_to_audio_mount():
    p = _tts_payload(TtsGenerated("firestarman", "m", "static/outputs/firestarman/x.wav",
                                  text="hi", provider="bark", voice="v", author="bob"))
    assert p == {"action": "play_audio", "file": "/audio/firestarman/x.wav",
                 "message": "hi", "provider": "bark", "voice": "v", "author": "bob"}


# ── channel-scoped hub routing ────────────────────────────────────────────────

def test_hub_routes_tts_only_to_that_channel():
    async def go():
        hub = WebUIHub()
        dash = hub.register()
        fire = hub.register_tts("firestarman")
        other = hub.register_tts("someoneelse")
        hub.broadcast(TtsGenerated("firestarman", "m", "static/outputs/firestarman/x.wav", text="hi"))
        # the channel's TTS client gets play_audio
        assert fire.get_nowait()["action"] == "play_audio"
        # a different channel gets nothing
        assert other.empty()
        # the dashboard monitor stream still gets the new_tts_entry
        assert dash.get_nowait()["event"] == "new_tts_entry"
        assert hub.tts_client_count("firestarman") == 1
        hub.unregister_tts("firestarman", fire)
        assert hub.tts_client_count("firestarman") == 0
    asyncio.run(go())


def test_hub_non_tts_event_skips_tts_clients():
    async def go():
        hub = WebUIHub()
        fire = hub.register_tts("firestarman")
        hub.broadcast(ConnectionStateChanged("connected"))
        assert fire.empty()
    asyncio.run(go())


def test_tts_kill_targets_one_channel_or_all():
    from bot.events import TtsKill

    async def go():
        hub = WebUIHub()
        fire = hub.register_tts("firestarman")
        other = hub.register_tts("otherguy")
        # targeted kill -> only that channel
        hub.broadcast(TtsKill(channel="firestarman"))
        assert fire.get_nowait() == {"action": "kill_audio"} and other.empty()
        # global kill (empty channel) -> everyone
        hub.broadcast(TtsKill())
        assert fire.get_nowait() == {"action": "kill_audio"}
        assert other.get_nowait() == {"action": "kill_audio"}
    asyncio.run(go())


# ── routes ──────────────────────────────────────────────────────────────────────

def _client_and_db(tmp_path):
    db = make_db(tmp_path)
    app = create_app(bot=None, auth_cfg=auth.AuthConfig(client_id="c", client_secret="s"),
                     db_file=db, owner="bossman", secret_key="test-secret")
    return TestClient(app), db


def test_tts_page_renders_for_valid_token_and_404s_otherwise(tmp_path):
    client, db = _client_and_db(tmp_path)
    token = tts_src.get_or_create_tts_token(db, "firestarman")
    r = client.get(f"/tts/{token}")
    assert r.status_code == 200
    assert "TTS Broadcast (firestarman)" in r.text and token in r.text
    assert client.get("/tts/bogus").status_code == 404


def test_ws_tts_rejects_unknown_token(tmp_path):
    client, _ = _client_and_db(tmp_path)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/tts/bogus") as ws:
            ws.receive_text()


def test_list_and_rotate_require_login(tmp_path):
    client, _ = _client_and_db(tmp_path)
    assert client.get("/api/tts-sources").status_code == 401
    assert client.post("/api/tts-sources/firestarman/rotate").status_code == 401


def _login(client, monkeypatch, login_name):
    from urllib.parse import parse_qs, urlparse
    state = parse_qs(urlparse(
        client.get("/auth/twitch/login", follow_redirects=False).headers["location"]).query)["state"][0]

    async def fake_exchange(cfg, code): return {"access_token": "tok"}
    async def fake_fetch(cfg, tok): return {"id": "1", "login": login_name, "display_name": login_name}
    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    monkeypatch.setattr(auth, "fetch_user", fake_fetch)
    client.get(f"/auth/twitch/callback?code=x&state={state}", follow_redirects=False)


def test_list_returns_url_then_rotate_changes_it(tmp_path, monkeypatch):
    client, db = _client_and_db(tmp_path)
    _login(client, monkeypatch, "firestarman")  # broadcaster manages their own channel

    sources = client.get("/api/tts-sources").json()
    assert len(sources) == 1 and sources[0]["channel"] == "firestarman"
    url1 = sources[0]["url"]
    assert url1.startswith("/tts/")

    url2 = client.post("/api/tts-sources/firestarman/rotate").json()["url"]
    assert url2.startswith("/tts/") and url2 != url1
    # the rotated token is the one now resolvable
    assert tts_src.channel_for_token(db, url2.split("/tts/")[1]) == "firestarman"


def test_rotate_forbidden_for_unowned_channel(tmp_path, monkeypatch):
    client, _ = _client_and_db(tmp_path)
    _login(client, monkeypatch, "firestarman")
    assert client.post("/api/tts-sources/someoneelse/rotate").status_code == 403
