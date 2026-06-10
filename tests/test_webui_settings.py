import sqlite3
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

import bot.webui.auth as auth
import bot.webui.settings_api as settings_api
from bot.webui.app import create_app


def full_db(tmp_path, channel="firestarman", owner="streamerguy"):
    db = str(tmp_path / "t.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE channel_configs (
        channel_name TEXT, owner TEXT, use_general_model INT DEFAULT 1, tts_enabled INT DEFAULT 0,
        voice_enabled INT DEFAULT 0, random_chance REAL DEFAULT 0, time_between_messages INT DEFAULT 0,
        lines_between_messages INT DEFAULT 100, tts_provider TEXT DEFAULT 'bark',
        tts_token TEXT DEFAULT '')""")
    conn.execute("INSERT INTO channel_configs (channel_name, owner) VALUES (?,?)", (channel, owner))
    conn.commit(); conn.close()
    return db


def client_db(tmp_path):
    db = full_db(tmp_path)
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


# ── settings_api unit ──────────────────────────────────────────────────────────

def test_schema_groups_by_category_with_controls():
    schema = settings_api.settings_schema()
    cats = {c["category"] for c in schema}
    assert "Behavior & Core Rules" in cats and "TTS Fundamentals" in cats
    keys = {f["key"]: f for c in schema for f in c["fields"]}
    assert keys["use_general_model"]["control"] == "bool_select"
    assert keys["tts_provider"]["control"] == "select" and keys["tts_provider"]["choices"]
    assert keys["random_chance"]["control"] == "input"


def test_get_values_excludes_secret(tmp_path):
    db = full_db(tmp_path)
    sqlite3.connect(db).execute("UPDATE channel_configs SET tts_token='SECRET'").connection.commit()
    v = settings_api.get_values(db, "firestarman")
    assert "tts_token" not in v and v["channel_name"] == "firestarman"


def test_apply_coerces_validates_and_persists(tmp_path):
    db = full_db(tmp_path)
    applied, errors = settings_api.apply_settings(db, "firestarman", {
        "tts_enabled": "on",            # bool coercion
        "random_chance": "12.5",        # float
        "use_general_model": "individual",  # -> 0
        "channel_name": "hacked",       # not editable -> rejected
        "random_chance_bad": "x",       # unknown -> rejected
    })
    assert applied["tts_enabled"] == 1 and applied["random_chance"] == 12.5 and applied["use_general_model"] == 0
    assert "channel_name" in errors and "random_chance_bad" in errors
    row = sqlite3.connect(db).execute(
        "SELECT tts_enabled, random_chance, use_general_model FROM channel_configs").fetchone()
    assert row == (1, 12.5, 0)


def test_apply_rejects_out_of_range(tmp_path):
    db = full_db(tmp_path)
    applied, errors = settings_api.apply_settings(db, "firestarman", {"random_chance": "150"})
    assert "random_chance" in errors and not applied  # max is 100


# ── routes ──────────────────────────────────────────────────────────────────────

def test_settings_api_requires_login_and_authz(tmp_path, monkeypatch):
    client, _ = client_db(tmp_path)
    assert client.get("/api/settings/firestarman").status_code == 401
    _login(client, monkeypatch, "firestarman")
    assert client.get("/api/settings/firestarman").status_code == 200
    assert client.get("/api/settings/someoneelse").status_code == 403  # not their channel


def test_get_then_post_settings_roundtrip(tmp_path, monkeypatch):
    client, db = client_db(tmp_path)
    _login(client, monkeypatch, "firestarman")
    schema = client.get("/api/settings/firestarman").json()
    assert any(f["key"] == "tts_enabled" for c in schema["schema"] for f in c["fields"])
    r = client.post("/api/settings/firestarman", json={"tts_enabled": "on", "random_chance": "20"})
    body = r.json()
    assert body["applied"]["tts_enabled"] == 1 and not body["errors"]
    assert sqlite3.connect(db).execute(
        "SELECT tts_enabled FROM channel_configs WHERE channel_name='firestarman'").fetchone()[0] == 1


class _FakeBot:
    def __init__(self, db):
        self.db_file = db; self.nick = "mockbot"; self._joined_channels = ["#firestarman"]
        self.start_time = 0; self.enable_tts = False
        self.refreshed = 0; self.joined = []; self.left = []
    def load_channel_settings(self): self.refreshed += 1
    async def join_channel(self, ch): self.joined.append(ch)
    async def leave_channel(self, ch): self.left.append(ch)


def test_save_refreshes_cache_and_join_leave_call_bot(tmp_path, monkeypatch):
    db = full_db(tmp_path)
    bot = _FakeBot(db)
    app = create_app(bot=bot, auth_cfg=auth.AuthConfig(client_id="c", client_secret="s"),
                     db_file=db, owner="bossman", secret_key="s")
    client = TestClient(app)
    _login(client, monkeypatch, "firestarman")

    client.post("/api/settings/firestarman", json={"tts_enabled": "on"})
    assert bot.refreshed == 1  # settings cache reloaded after a successful write

    assert client.post("/api/channels/firestarman/join").json()["joined"] is True
    assert bot.joined == ["firestarman"]
    client.post("/api/channels/firestarman/leave")
    assert bot.left == ["firestarman"]


def test_settings_page_and_join_leave_authz(tmp_path, monkeypatch):
    client, _ = client_db(tmp_path)
    assert client.get("/settings", follow_redirects=False).status_code == 302
    _login(client, monkeypatch, "firestarman")
    page = client.get("/settings")
    assert page.status_code == 200 and 'x-data="settings"' in page.text
    # join/leave authz: unowned channel -> 403; bot unavailable (bot=None) -> 503 for owned
    assert client.post("/api/channels/someoneelse/join").status_code == 403
    assert client.post("/api/channels/firestarman/join").status_code == 503
    assert client.post("/api/channels/firestarman/sing").status_code == 404  # bad action
