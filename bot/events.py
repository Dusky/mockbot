"""Internal event bus — the single integration seam for the bot.

Today the bot, TUI, overlay, and external webui are wired together by four ad-hoc
mechanisms (a stub socketio emitter, a file-based command poller, the overlay's
own websocket, and the shared DB). This module introduces one in-process pub/sub
bus that producers publish to and consumers subscribe to. A later stage adds a
FastAPI transport on top of the same bus so the webui becomes a first-class
client; for now the existing pipes keep working and are migrated incrementally.

Event catalog (this is the forward contract for clients):

  Bot -> clients (state/notifications):
    ConnectionStateChanged(state, attempts, next_delay)
    ErrorLogged(level, message, timestamp)
    TtsGenerated(channel, message_id, file_url, voice, text, provider, author)
    TtsKill(channel)                 # stop playback (empty channel = all)
    ChatMessage(channel, author, text, color, is_bot, timestamp)
    BotStatus(nick, channels, uptime, tts_enabled, pid, timestamp)

  clients -> Bot (commands):
    SendMessageCommand(channel, message, force, request_id)

Pure stdlib — importable and testable without the heavy deps.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field


# ── event types ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ConnectionStateChanged:
    state: str                       # "connected" | "disconnected" | "reconnecting"
    attempts: int = 0
    next_delay: int | None = None


@dataclass(frozen=True)
class ErrorLogged:
    level: str
    message: str
    timestamp: str


@dataclass(frozen=True)
class TtsGenerated:
    channel: str
    message_id: str
    file_path: str          # full output path, e.g. static/outputs/<ch>/<file>.wav
    text: str = ""
    provider: str = ""
    voice: str = ""
    author: str = ""


@dataclass(frozen=True)
class ChatMessage:
    """A chat line for the monitor feed."""
    channel: str
    author: str
    text: str
    color: str = ""
    is_bot: bool = False
    timestamp: str = ""


@dataclass(frozen=True)
class TtsKill:
    """Stop playback on TTS sources. Empty channel means all channels."""
    channel: str = ""


@dataclass(frozen=True)
class BotStatus:
    nick: str
    channels: tuple = ()
    uptime: float = 0.0
    tts_enabled: bool = False
    pid: int = 0
    timestamp: float = 0.0


@dataclass(frozen=True)
class SendMessageCommand:
    channel: str
    message: str
    force: bool = False
    request_id: str = ""


# ── legacy socketio compatibility ────────────────────────────────────────────

def to_legacy_dict(event) -> dict | None:
    """Map an event to the legacy `socketio_emitter` dict shape, or None.

    Preserves the (currently unwired) admin-dashboard emitter contract so nothing
    observable changes when the emitter call sites move onto the bus.
    """
    if isinstance(event, ConnectionStateChanged):
        d = {"event": "connection_state_changed", "state": event.state}
        if event.state != "disconnected":
            d["attempts"] = event.attempts
        if event.next_delay is not None:
            d["next_delay"] = event.next_delay
        return d
    if isinstance(event, ErrorLogged):
        return {"event": "error_logged", "level": event.level,
                "message": event.message, "timestamp": event.timestamp}
    if isinstance(event, TtsGenerated):
        web = event.file_path if event.file_path.startswith("static/") \
            else f"static/{event.file_path.lstrip('/')}"
        return {"event": "new_tts_entry", "channel": event.channel,
                "message_id": event.message_id, "tts_url": web,
                "voice": event.voice, "text": event.text}
    return None


# ── the bus ──────────────────────────────────────────────────────────────────

class EventBus:
    """Minimal typed pub/sub. Thread-safe publish; sync or async subscribers.

    Sync handlers run inline. Coroutine handlers are scheduled on `loop`
    (`create_task` when publishing from the loop thread, `run_coroutine_threadsafe`
    when publishing from another thread — e.g. the TTS worker).
    """

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None):
        self._loop = loop
        self._subs: dict[type, list] = {}

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, event_type: type, handler) -> None:
        self._subs.setdefault(event_type, []).append(handler)

    def publish(self, event) -> None:
        for handler in list(self._subs.get(type(event), ())):
            try:
                if inspect.iscoroutinefunction(handler):
                    self._schedule(handler(event))
                else:
                    result = handler(event)
                    if inspect.iscoroutine(result):
                        self._schedule(result)
            except Exception:
                logging.exception("EventBus handler failed for %s", type(event).__name__)

    def _schedule(self, coro) -> None:
        loop = self._loop
        if loop is None:
            # No registered loop: run on the current loop if there is one, else drop.
            try:
                asyncio.get_running_loop().create_task(coro)
            except RuntimeError:
                logging.warning("EventBus: no loop to run async handler; dropping")
                coro.close()
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            loop.create_task(coro)
        else:
            asyncio.run_coroutine_threadsafe(coro, loop)
