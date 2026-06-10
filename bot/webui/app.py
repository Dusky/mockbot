"""FastAPI app factory, the bus→websocket fan-out hub, and an on-loop runner."""
from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from bot.events import (
    BotStatus,
    ConnectionStateChanged,
    ErrorLogged,
    TtsGenerated,
    to_legacy_dict,
)

logger = logging.getLogger("bot")

# Bot→client event types the webui relays to browsers.
_RELAYED_EVENTS = (ConnectionStateChanged, ErrorLogged, TtsGenerated, BotStatus)


def _serialize(event) -> dict | None:
    """Turn a bus event into a JSON-safe dict for ws clients, or None to skip."""
    d = to_legacy_dict(event)  # reuses the established dashboard payload shape
    if d is not None:
        return d
    if isinstance(event, BotStatus):
        return {
            "event": "bot_status",
            "nick": event.nick,
            "channels": list(event.channels),
            "uptime": event.uptime,
            "tts_enabled": event.tts_enabled,
            "pid": event.pid,
            "timestamp": event.timestamp,
        }
    return None


class WebUIHub:
    """Fan-out from the EventBus to connected websocket clients.

    Each client gets its own bounded ``asyncio.Queue``; bus events (delivered on
    the bot loop) are enqueued to every client, and each ws task drains its own
    queue. A full queue (slow client) drops its oldest item to bound latency.
    """

    def __init__(self) -> None:
        self._clients: set[asyncio.Queue] = set()

    def register(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._clients.add(q)
        return q

    def unregister(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def broadcast(self, event) -> None:
        """EventBus handler. Runs on the bot loop; never raises into the bus."""
        payload = _serialize(event)
        if payload is None:
            return
        for q in list(self._clients):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                try:  # slow client: drop oldest, then enqueue newest
                    q.get_nowait()
                    q.put_nowait(payload)
                except Exception:
                    pass

    def attach_to_bus(self, bus) -> None:
        for event_type in _RELAYED_EVENTS:
            bus.subscribe(event_type, self.broadcast)


def create_app(bot=None, hub: WebUIHub | None = None) -> FastAPI:
    hub = hub or WebUIHub()
    app = FastAPI(title="Mockbot WebUI", version="0.1.0")
    app.state.bot = bot
    app.state.hub = hub

    @app.get("/healthz")
    async def healthz():
        return {
            "status": "ok",
            "nick": getattr(bot, "nick", None) if bot else None,
            "channels": list(getattr(bot, "_joined_channels", []) or []) if bot else [],
            "ws_clients": hub.client_count,
        }

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket):
        await ws.accept()
        q = hub.register()
        try:
            while True:
                await ws.send_json(await q.get())
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass  # client left or server is shutting down — both are clean exits
        except Exception:
            logger.exception("webui /ws/events client error")
        finally:
            hub.unregister(q)

    return app


async def start_webui(bot, host: str = "127.0.0.1", port: int = 5001):
    """Start the FastAPI app on the current (bot) loop without blocking.

    Returns the uvicorn ``Server`` so the caller can stop it (``should_exit``).
    Binds to localhost in this phase — flip to the configured host once the
    Twitch-OAuth gate (Phase 2) is in place, since the endpoints are unauth'd.
    Signal handlers are disabled so uvicorn doesn't hijack main.py's SIGINT/TERM.
    """
    import uvicorn

    hub = WebUIHub()
    hub.attach_to_bus(bot.event_bus)
    bot.webui_hub = hub  # live consumer for the formerly-dormant emitter seam
    app = create_app(bot, hub)

    config = uvicorn.Config(app, host=host, port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    asyncio.create_task(server.serve())
    logger.info("WebUI listening on http://%s:%s", host, port)
    return server
