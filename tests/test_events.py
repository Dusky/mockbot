"""Tests for the internal event bus."""
import asyncio
import threading

from bot.events import (
    EventBus, ConnectionStateChanged, ErrorLogged, TtsGenerated,
    SendMessageCommand, to_legacy_dict,
)


# ── sync delivery ────────────────────────────────────────────────────────────

def test_sync_subscribers_receive_event():
    bus = EventBus()
    got = []
    bus.subscribe(ErrorLogged, lambda e: got.append(e))
    bus.subscribe(ErrorLogged, lambda e: got.append(e.message))
    bus.publish(ErrorLogged("error", "boom", "t0"))
    assert len(got) == 2
    assert got[1] == "boom"


def test_only_matching_type_delivered():
    bus = EventBus()
    got = []
    bus.subscribe(ErrorLogged, lambda e: got.append(e))
    bus.publish(ConnectionStateChanged("connected"))
    assert got == []


def test_handler_exception_is_isolated():
    bus = EventBus()
    seen = []

    def boom(_):
        raise RuntimeError("kaboom")

    bus.subscribe(ErrorLogged, boom)
    bus.subscribe(ErrorLogged, lambda e: seen.append(1))
    bus.publish(ErrorLogged("error", "m", "t"))  # must not raise
    assert seen == [1]


# ── legacy compatibility mapping ─────────────────────────────────────────────

def test_to_legacy_dict_shapes():
    assert to_legacy_dict(ConnectionStateChanged("disconnected")) == {
        "event": "connection_state_changed", "state": "disconnected"}
    assert to_legacy_dict(ConnectionStateChanged("reconnecting", attempts=2, next_delay=8)) == {
        "event": "connection_state_changed", "state": "reconnecting",
        "attempts": 2, "next_delay": 8}
    assert to_legacy_dict(ErrorLogged("warn", "m", "ts")) == {
        "event": "error_logged", "level": "warn", "message": "m", "timestamp": "ts"}
    tts = to_legacy_dict(TtsGenerated("chan", "mid", "/audio/x.wav", voice="v", text="hi"))
    assert tts["event"] == "new_tts_entry"
    assert tts["tts_url"] == "/audio/x.wav"
    assert tts["channel"] == "chan"
    # Commands have no legacy emitter shape.
    assert to_legacy_dict(SendMessageCommand("c", "m")) is None


# ── async delivery ───────────────────────────────────────────────────────────

def test_async_subscriber_runs_on_loop():
    async def main():
        bus = EventBus(asyncio.get_running_loop())
        received = []

        async def handler(e):
            received.append(e.channel)

        bus.subscribe(SendMessageCommand, handler)
        bus.publish(SendMessageCommand("dusky", "hi"))
        await asyncio.sleep(0.05)
        return received

    assert asyncio.run(main()) == ["dusky"]


def test_threadsafe_publish_from_non_loop_thread():
    loop = asyncio.new_event_loop()
    received = []
    done = threading.Event()

    async def handler(e):
        received.append(e.message)
        done.set()

    def run_loop():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    t = threading.Thread(target=run_loop, daemon=True)
    t.start()
    try:
        bus = EventBus(loop)
        bus.subscribe(SendMessageCommand, handler)
        # Publish from the main thread (not the loop thread) — the TTS-worker case.
        bus.publish(SendMessageCommand("c", "hello"))
        assert done.wait(2.0)
        assert received == ["hello"]
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(2.0)
        loop.close()
