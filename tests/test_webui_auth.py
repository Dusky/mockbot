import sqlite3
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import bot.webui.auth as auth
from bot.webui.app import create_app


def make_db(tmp_path, channel="firestarman", owner="streamerguy"):
    db = str(tmp_path / "t.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE channel_configs (channel_name TEXT, owner TEXT)")
    conn.execute("INSERT INTO channel_configs VALUES (?,?)", (channel, owner))
    conn.commit()
    conn.close()
    return db


def app_client(tmp_path, **kw):
    cfg = auth.AuthConfig(client_id="cid", client_secret="sec",
                          redirect_uri="http://localhost:5001/auth/twitch/callback")
    app = create_app(bot=None, auth_cfg=cfg, db_file=make_db(tmp_path), owner="bossman",
                     secret_key="test-secret", **kw)
    return TestClient(app)


# ── pure helpers ────────────────────────────────────────────────────────────

def test_build_authorize_url():
    cfg = auth.AuthConfig(client_id="cid", redirect_uri="http://x/cb", scopes="")
    url = auth.build_authorize_url(cfg, "st8")
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://id.twitch.tv/oauth2/authorize")
    assert q["client_id"] == ["cid"] and q["state"] == ["st8"] and q["response_type"] == ["code"]


def test_is_authorized(tmp_path):
    db = make_db(tmp_path, channel="firestarman", owner="streamerguy")
    assert auth.is_authorized(db, "firestarman")          # matches a channel
    assert auth.is_authorized(db, "STREAMERGUY")          # matches channel owner (case-insensitive)
    assert auth.is_authorized(db, "bossman", owner="bossman")  # configured bot owner
    assert not auth.is_authorized(db, "rando")
    assert not auth.is_authorized(db, "")


# ── login redirect ────────────────────────────────────────────────────────────

def test_login_redirects_to_twitch_with_state(tmp_path):
    client = app_client(tmp_path)
    r = client.get("/auth/twitch/login", follow_redirects=False)
    assert r.status_code in (302, 307)
    loc = r.headers["location"]
    assert loc.startswith("https://id.twitch.tv/oauth2/authorize")
    assert "state=" in loc and "client_id=cid" in loc


def test_login_503_when_unconfigured(tmp_path):
    app = create_app(bot=None, auth_cfg=auth.AuthConfig(), db_file=make_db(tmp_path),
                     owner="x", secret_key="s")
    assert TestClient(app).get("/auth/twitch/login", follow_redirects=False).status_code == 503


# ── callback ────────────────────────────────────────────────────────────────

def _login_state(client):
    loc = client.get("/auth/twitch/login", follow_redirects=False).headers["location"]
    return parse_qs(urlparse(loc).query)["state"][0]


def test_callback_authorized_creates_session(tmp_path, monkeypatch):
    client = app_client(tmp_path)
    state = _login_state(client)

    async def fake_exchange(cfg, code): return {"access_token": "tok"}
    async def fake_fetch(cfg, tok): return {"id": "1", "login": "FireStarman", "display_name": "Fire"}
    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    monkeypatch.setattr(auth, "fetch_user", fake_fetch)

    r = client.get(f"/auth/twitch/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"
    me = client.get("/me")
    assert me.status_code == 200 and me.json()["login"] == "firestarman"


def test_callback_rejects_bad_state(tmp_path, monkeypatch):
    client = app_client(tmp_path)
    _login_state(client)  # seeds a different state
    r = client.get("/auth/twitch/callback?code=abc&state=WRONG", follow_redirects=False)
    assert r.status_code == 400


def test_callback_forbids_unauthorized_user(tmp_path, monkeypatch):
    client = app_client(tmp_path)
    state = _login_state(client)

    async def fake_exchange(cfg, code): return {"access_token": "tok"}
    async def fake_fetch(cfg, tok): return {"id": "9", "login": "randostranger", "display_name": "R"}
    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    monkeypatch.setattr(auth, "fetch_user", fake_fetch)

    r = client.get(f"/auth/twitch/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 403
    assert client.get("/me").status_code == 401  # no session created


# ── gate enforcement ────────────────────────────────────────────────────────

def test_me_requires_login(tmp_path):
    assert app_client(tmp_path).get("/me").status_code == 401


def test_index_redirects_anon_to_login(tmp_path):
    r = app_client(tmp_path).get("/", follow_redirects=False)
    assert r.status_code == 302 and "/auth/twitch/login" in r.headers["location"]


def test_ws_events_rejects_anonymous(tmp_path):
    client = app_client(tmp_path)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/events") as ws:
            ws.receive_text()
