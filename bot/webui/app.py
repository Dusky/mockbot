"""FastAPI app factory, the bus→websocket fan-out hub, and an on-loop runner."""
from __future__ import annotations

import asyncio
import logging
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import bot.webui.auth as auth
import bot.webui.tts_source as tts_src
from bot.events import (
    BotStatus,
    ConnectionStateChanged,
    ErrorLogged,
    TtsGenerated,
    TtsKill,
    to_legacy_dict,
)

_AUDIO_DIR = "static/outputs"  # where the TTS pipeline writes <channel>/<file>.wav

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


def _tts_payload(event: TtsGenerated) -> dict:
    """Build the overlay-compatible play_audio message for a TTS event,
    mapping the on-disk path to the /audio mount (static/outputs/<x> -> /audio/<x>)."""
    fp = event.file_path or ""
    try:
        rel = str(Path(fp).relative_to(_AUDIO_DIR))
        url = f"/audio/{rel}"
    except ValueError:
        url = fp  # already a url or outside the audio dir — pass through
    return {
        "action": "play_audio",
        "file": url,
        "message": event.text,
        "provider": event.provider,
        "voice": event.voice,
        "author": event.author,
    }


def _enqueue(q: asyncio.Queue, payload: dict) -> None:
    """Enqueue, dropping the oldest item if the client is full (bounds latency)."""
    try:
        q.put_nowait(payload)
    except asyncio.QueueFull:
        try:
            q.get_nowait()
            q.put_nowait(payload)
        except Exception:
            pass


class WebUIHub:
    """Fan-out from the EventBus to connected websocket clients.

    Each client gets its own bounded ``asyncio.Queue``; bus events (delivered on
    the bot loop) are enqueued to every client, and each ws task drains its own
    queue. A full queue (slow client) drops its oldest item to bound latency.
    """

    def __init__(self) -> None:
        self._clients: set[asyncio.Queue] = set()                 # dashboard /ws/events
        self._tts_clients: dict[str, set[asyncio.Queue]] = {}     # per-channel /ws/tts

    def register(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._clients.add(q)
        return q

    def unregister(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)

    def register_tts(self, channel: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._tts_clients.setdefault(channel.lstrip("#").lower(), set()).add(q)
        return q

    def unregister_tts(self, channel: str, q: asyncio.Queue) -> None:
        ch = channel.lstrip("#").lower()
        clients = self._tts_clients.get(ch)
        if clients:
            clients.discard(q)
            if not clients:
                del self._tts_clients[ch]

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def tts_client_count(self, channel: str | None = None) -> int:
        if channel is None:
            return sum(len(s) for s in self._tts_clients.values())
        return len(self._tts_clients.get(channel.lstrip("#").lower(), ()))

    def broadcast(self, event) -> None:
        """EventBus handler. Runs on the bot loop; never raises into the bus."""
        payload = _serialize(event)
        if payload is not None:
            for q in list(self._clients):  # dashboard monitor stream
                _enqueue(q, payload)
        if isinstance(event, TtsGenerated):  # channel-scoped private playback
            ch = (event.channel or "").lstrip("#").lower()
            audio = _tts_payload(event)
            for q in list(self._tts_clients.get(ch, ())):
                _enqueue(q, audio)
        elif isinstance(event, TtsKill):  # stop playback; empty channel = all
            ch = (event.channel or "").lstrip("#").lower()
            channels = [ch] if ch else list(self._tts_clients.keys())
            for c in channels:
                for q in list(self._tts_clients.get(c, ())):
                    _enqueue(q, {"action": "kill_audio"})

    def attach_to_bus(self, bus) -> None:
        for event_type in _RELAYED_EVENTS:
            bus.subscribe(event_type, self.broadcast)
        bus.subscribe(TtsKill, self.broadcast)


def _require_user(request: Request) -> dict:
    """FastAPI dependency: 401 unless an authorized user is in the session."""
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="login required")
    return user


def create_app(bot=None, hub: WebUIHub | None = None, *, auth_cfg=None,
               db_file=None, owner="", secret_key=None) -> FastAPI:
    hub = hub or WebUIHub()

    # Resolve config — everything is injectable for tests; otherwise read the
    # bot config (settings.conf) for [oauth], owner, and the session secret.
    if auth_cfg is None or not owner or not secret_key:
        try:
            from bot.config import config as _config
            if auth_cfg is None:
                auth_cfg = auth.auth_config_from_config(_config)
            if not owner and _config.has_section("auth"):
                owner = _config.get("auth", "owner", fallback="")
            if not secret_key and _config.has_section("web"):
                secret_key = _config.get("web", "secret_key", fallback="")
        except Exception:
            pass
    auth_cfg = auth_cfg or auth.AuthConfig()
    if db_file is None:
        db_file = getattr(bot, "db_file", None) or "messages.db"
    if not secret_key or secret_key == "your-secret-key-here":
        secret_key = secrets.token_urlsafe(32)
        logger.warning("webui: no [web] secret_key set; using a random one (sessions reset on restart)")

    app = FastAPI(title="Mockbot WebUI", version="0.2.0")
    app.add_middleware(SessionMiddleware, secret_key=secret_key, same_site="lax", https_only=False)
    app.state.bot = bot
    app.state.hub = hub
    app.state.auth_cfg = auth_cfg

    @app.get("/healthz")
    async def healthz():  # unauthenticated health check
        return {
            "status": "ok",
            "nick": getattr(bot, "nick", None) if bot else None,
            "channels": list(getattr(bot, "_joined_channels", []) or []) if bot else [],
            "ws_clients": hub.client_count,
        }

    # ── Twitch OAuth login ──────────────────────────────────────────────────
    @app.get("/auth/twitch/login")
    async def login(request: Request):
        if not auth_cfg.configured:
            raise HTTPException(503, "Twitch OAuth not configured — set [oauth] in settings.conf")
        state = secrets.token_urlsafe(24)
        request.session["oauth_state"] = state
        return RedirectResponse(auth.build_authorize_url(auth_cfg, state))

    @app.get("/auth/twitch/callback")
    async def callback(request: Request, code: str = "", state: str = "", error: str = ""):
        if error:
            raise HTTPException(400, f"Twitch returned an error: {error}")
        expected = request.session.pop("oauth_state", None)
        if not state or state != expected:
            raise HTTPException(400, "Invalid OAuth state")
        if not code:
            raise HTTPException(400, "Missing authorization code")
        token = await auth.exchange_code(auth_cfg, code)
        user = await auth.fetch_user(auth_cfg, token.get("access_token", ""))
        login_name = (user.get("login") or "").lower()
        if not auth.is_authorized(db_file, login_name, owner):
            raise HTTPException(403, f"'{login_name or 'unknown'}' is not authorized to manage this bot")
        request.session["user"] = {
            "id": user.get("id"),
            "login": login_name,
            "display_name": user.get("display_name") or login_name,
        }
        return RedirectResponse("/", status_code=302)

    @app.get("/auth/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/auth/twitch/login", status_code=302)

    @app.get("/me")
    async def me(user=Depends(_require_user)):
        return user

    @app.get("/")
    async def index(request: Request):
        if not request.session.get("user"):
            return RedirectResponse("/auth/twitch/login", status_code=302)
        return {"dashboard": "coming soon", "user": request.session["user"]}  # Phase 4/5

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket):
        if not ws.session.get("user"):
            await ws.close(code=1008)  # policy violation: login required
            return
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

    # ── private per-channel TTS sources (token-gated, no login — OBS can't auth) ──
    tts_src.ensure_token_column(db_file)
    app.mount("/audio", StaticFiles(directory=_AUDIO_DIR, check_dir=False), name="audio")

    @app.get("/api/variables/{channel}")
    async def api_variables(channel: str):
        db = getattr(bot, "db", None) if bot else None
        if db is None:
            raise HTTPException(503, "database unavailable")
        try:
            return await db.get_all_variables(channel.lstrip("#").lower())
        except Exception:
            logger.exception("webui /api/variables error")
            raise HTTPException(500, "error fetching variables")

    @app.get("/tts/{token}", response_class=HTMLResponse)
    async def tts_page(token: str):
        channel = tts_src.channel_for_token(db_file, token)
        if not channel:
            raise HTTPException(404, "Unknown TTS source")
        return HTMLResponse(tts_src.render_playback_page(token, channel))

    @app.websocket("/ws/tts/{token}")
    async def ws_tts(ws: WebSocket, token: str):
        channel = tts_src.channel_for_token(db_file, token)
        if not channel:
            await ws.close(code=1008)  # unknown/revoked token
            return
        await ws.accept()
        q = hub.register_tts(channel)
        try:
            while True:
                await ws.send_json(await q.get())
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception:
            logger.exception("webui /ws/tts client error")
        finally:
            hub.unregister_tts(channel, q)

    # ── TTS source management (authenticated) ─────────────────────────────────
    @app.get("/api/tts-sources")
    async def list_tts_sources(user=Depends(_require_user)):
        out = []
        for ch in tts_src.authorized_channels(db_file, user["login"], owner):
            token = tts_src.get_or_create_tts_token(db_file, ch)
            if token:
                out.append({"channel": ch, "url": f"/tts/{token}"})
        return out

    @app.post("/api/tts-sources/{channel}/rotate")
    async def rotate_tts_source(channel: str, user=Depends(_require_user)):
        ch = channel.lstrip("#").lower()
        allowed = {c.lower() for c in tts_src.authorized_channels(db_file, user["login"], owner)}
        if ch not in allowed:
            raise HTTPException(403, "not authorized for this channel")
        token = tts_src.rotate_tts_token(db_file, ch)
        if not token:
            raise HTTPException(404, "unknown channel")
        return {"channel": ch, "url": f"/tts/{token}"}

    return app


async def start_webui(bot, host: str = "127.0.0.1", port: int = 5001):
    """Start the FastAPI app on the current (bot) loop without blocking.

    Returns the uvicorn ``Server`` so the caller can stop it (``should_exit``).
    Binds to localhost in this phase — flip to the configured host once you've
    confirmed the Twitch-OAuth login round-trips. Signal handlers are disabled so
    uvicorn doesn't hijack main.py's SIGINT/TERM.
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
