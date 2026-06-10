"""Twitch OAuth login for the webui (Phase 2).

Identity-only login: no scopes are needed to read the authenticated user, so the
webui's `[oauth]` app stays separate from the bot's chat/EventSub token. On
callback we exchange the code, fetch the Twitch user, check they're allowed to
manage this bot, and store a signed session. The network calls are module-level
functions so tests can monkeypatch them without touching Twitch.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from urllib.parse import urlencode

import aiohttp

logger = logging.getLogger("bot")

AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
USERS_URL = "https://api.twitch.tv/helix/users"


@dataclass
class AuthConfig:
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://localhost:5001/auth/twitch/callback"
    scopes: str = ""  # identity only — fetching the authenticated user needs none

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


def auth_config_from_config(config) -> AuthConfig:
    """Build an AuthConfig from the [oauth] section of the bot config."""
    if not config.has_section("oauth"):
        return AuthConfig()
    return AuthConfig(
        client_id=config.get("oauth", "twitch_client_id", fallback=""),
        client_secret=config.get("oauth", "twitch_client_secret", fallback=""),
        redirect_uri=config.get("oauth", "twitch_redirect_uri",
                                fallback="http://localhost:5001/auth/twitch/callback"),
    )


def build_authorize_url(cfg: AuthConfig, state: str) -> str:
    return f"{AUTHORIZE_URL}?" + urlencode({
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "response_type": "code",
        "scope": cfg.scopes,
        "state": state,
    })


async def exchange_code(cfg: AuthConfig, code: str) -> dict:
    """Exchange an authorization code for tokens (uses core aiohttp, no new dep)."""
    data = {
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": cfg.redirect_uri,
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(TOKEN_URL, data=data) as r:
            r.raise_for_status()
            return await r.json()


async def fetch_user(cfg: AuthConfig, access_token: str) -> dict:
    """Fetch the authenticated Twitch user (id, login, display_name)."""
    headers = {"Authorization": f"Bearer {access_token}", "Client-Id": cfg.client_id}
    async with aiohttp.ClientSession() as s:
        async with s.get(USERS_URL, headers=headers) as r:
            r.raise_for_status()
            payload = await r.json()
    data = payload.get("data") or []
    return data[0] if data else {}


def is_authorized(db_file: str, login: str, owner: str = "") -> bool:
    """May this Twitch login manage the bot? Allowed if they are the configured
    owner, or a broadcaster the bot serves (their login is a channel, or they're
    listed as a channel's owner)."""
    login = (login or "").lower()
    if not login:
        return False
    if owner and login == owner.lower():
        return True
    try:
        conn = sqlite3.connect(db_file)
        row = conn.execute(
            "SELECT 1 FROM channel_configs WHERE lower(channel_name)=? OR lower(owner)=? LIMIT 1",
            (login, login),
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False
