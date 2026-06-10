import asyncio

from fastapi.testclient import TestClient

from bot.events import (
    BotStatus,
    ConnectionStateChanged,
    ErrorLogged,
    EventBus,
    TtsGenerated,
)
from bot.webui.app import WebUIHub, _serialize, create_app


# ── _serialize ────────────────────────────────────────────────────────────────

def test_serialize_connection_state_includes_attempts():
    assert _serialize(ConnectionStateChanged("connected", attempts=2)) == {
        "event": "connection_state_changed", "state": "connected", "attempts": 2,
    }


def test_serialize_disconnected_omits_attempts():
    d = _serialize(ConnectionStateChanged("disconnected", attempts=5))
    assert d["state"] == "disconnected" and "attempts" not in d


def test_serialize_error_and_tts():
    e = _serialize(ErrorLogged("ERROR", "boom", "2026-01-01T00:00:00"))
    assert e["event"] == "error_logged" and e["message"] == "boom"
    t = _serialize(TtsGenerated("chan", "mid", "static/outputs/chan/x.wav", text="hi", voice="v"))
    assert t["event"] == "new_tts_entry" and t["tts_url"] == "static/outputs/chan/x.wav"


def test_serialize_tts_prefixes_static_when_missing():
    assert _serialize(TtsGenerated("c", "m", "outputs/c/x.wav"))["tts_url"] == "static/outputs/c/x.wav"


def test_serialize_bot_status():
    assert _serialize(BotStatus(nick="bot", channels=("a", "b"), uptime=1.0, tts_enabled=True, pid=9)) == {
        "event": "bot_status", "nick": "bot", "channels": ["a", "b"],
        "uptime": 1.0, "tts_enabled": True, "pid": 9, "timestamp": 0.0,
    }


def test_serialize_unknown_event_returns_none():
    assert _serialize(object()) is None


# ── WebUIHub ────────────────────────────────────────────────────────────────────

def test_hub_broadcast_enqueues_to_all_and_unregister():
    async def go():
        hub = WebUIHub()
        q1, q2 = hub.register(), hub.register()
        assert hub.client_count == 2
        hub.broadcast(ConnectionStateChanged("connected"))
        assert q1.get_nowait()["state"] == "connected"
        assert q2.get_nowait()["state"] == "connected"
        hub.unregister(q1)
        assert hub.client_count == 1
    asyncio.run(go())


def test_hub_skips_unmapped_events():
    async def go():
        hub = WebUIHub()
        q = hub.register()
        hub.broadcast(object())
        assert q.empty()
    asyncio.run(go())


def test_hub_full_queue_drops_oldest():
    async def go():
        hub = WebUIHub()
        small = asyncio.Queue(maxsize=1)
        hub._clients.add(small)
        hub.broadcast(ErrorLogged("E", "first", "t"))
        hub.broadcast(ErrorLogged("E", "second", "t"))  # full → drop oldest
        assert small.get_nowait()["message"] == "second"
        assert small.empty()
    asyncio.run(go())


def test_hub_attaches_to_bus_and_relays_published_events():
    async def go():
        bus = EventBus(asyncio.get_running_loop())
        hub = WebUIHub()
        q = hub.register()
        hub.attach_to_bus(bus)
        bus.publish(ConnectionStateChanged("reconnecting", attempts=1))  # sync handler runs inline
        assert q.get_nowait() == {"event": "connection_state_changed", "state": "reconnecting", "attempts": 1}
    asyncio.run(go())


# ── /healthz ────────────────────────────────────────────────────────────────────

def test_healthz_reports_bot_state():
    class FakeBot:
        nick = "mockbot"
        _joined_channels = ["#a", "#b"]

    client = TestClient(create_app(bot=FakeBot(), hub=WebUIHub()))
    body = client.get("/healthz").json()
    assert body == {"status": "ok", "nick": "mockbot", "channels": ["#a", "#b"], "ws_clients": 0}
