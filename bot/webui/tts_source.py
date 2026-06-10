"""Per-channel private TTS playback sources (Phase 3).

Each channel gets an unguessable token (stored in channel_configs.tts_token).
The token-gated page + websocket let a broadcaster add a private OBS browser
source that plays their channel's TTS — replacing the public :5050 overlay.
The token is the credential (OBS can't do OAuth), so these routes are not
behind the login gate; the management endpoints that mint/rotate tokens are.
"""
from __future__ import annotations

import secrets
import sqlite3


def ensure_token_column(db_file: str) -> None:
    """Add channel_configs.tts_token if missing (idempotent migration)."""
    try:
        conn = sqlite3.connect(db_file)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(channel_configs)")]
        if "tts_token" not in cols:
            conn.execute("ALTER TABLE channel_configs ADD COLUMN tts_token TEXT DEFAULT ''")
            conn.commit()
        conn.close()
    except Exception:
        pass


def get_or_create_tts_token(db_file: str, channel: str) -> str | None:
    """Return the channel's token, creating one on first use. None if unknown."""
    channel = channel.lstrip("#").lower()
    conn = sqlite3.connect(db_file)
    try:
        row = conn.execute(
            "SELECT tts_token FROM channel_configs WHERE lower(channel_name)=?", (channel,)
        ).fetchone()
        if row is None:
            return None
        token = row[0]
        if not token:
            token = secrets.token_urlsafe(24)
            conn.execute(
                "UPDATE channel_configs SET tts_token=? WHERE lower(channel_name)=?", (token, channel)
            )
            conn.commit()
        return token
    finally:
        conn.close()


def rotate_tts_token(db_file: str, channel: str) -> str | None:
    """Issue a fresh token (invalidating the old URL). None if channel unknown."""
    channel = channel.lstrip("#").lower()
    token = secrets.token_urlsafe(24)
    conn = sqlite3.connect(db_file)
    try:
        cur = conn.execute(
            "UPDATE channel_configs SET tts_token=? WHERE lower(channel_name)=?", (token, channel)
        )
        conn.commit()
        return token if cur.rowcount else None
    finally:
        conn.close()


def channel_for_token(db_file: str, token: str) -> str | None:
    """Resolve a token back to its channel name, or None."""
    if not token:
        return None
    conn = sqlite3.connect(db_file)
    try:
        row = conn.execute(
            "SELECT channel_name FROM channel_configs WHERE tts_token=?", (token,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def authorized_channels(db_file: str, login: str, owner: str = "") -> list[str]:
    """Channels a logged-in user may manage: all if they're the bot owner, else
    the channel matching their login plus any they own."""
    login = (login or "").lower()
    conn = sqlite3.connect(db_file)
    try:
        if owner and login == owner.lower():
            rows = conn.execute("SELECT channel_name FROM channel_configs").fetchall()
        else:
            rows = conn.execute(
                "SELECT channel_name FROM channel_configs WHERE lower(channel_name)=? OR lower(owner)=?",
                (login, login),
            ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


# Playback page — ported from the :5050 overlay (audio + visualizer + message
# bubbles), with the websocket pointed at the token-gated /ws/tts/<token> and a
# scheme-aware ws/wss. __TOKEN__ / __CHANNEL__ are substituted per request.
_PLAYBACK_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Mockbot TTS - __CHANNEL__</title>
    <style>
        body { margin: 0; padding: 20px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: #f8fafc; display: flex; flex-direction: column; gap: 16px; align-items: flex-start; background: transparent; }
        .main-hud { background: rgba(15, 23, 42, 0.85); backdrop-filter: blur(10px); border-radius: 12px; padding: 16px 20px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3); width: 100%; max-width: 450px; display: flex; flex-direction: column; gap: 12px; border: 1px solid rgba(51, 65, 85, 0.5); transition: opacity 0.5s ease-in-out, transform 0.5s ease-in-out; opacity: 0; transform: translateY(20px); }
        .main-hud.active { opacity: 1; transform: translateY(0); }
        .hud-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(71, 85, 105, 0.5); padding-bottom: 8px; }
        .hud-title { font-size: 0.9rem; font-weight: 600; color: #94a3b8; display: flex; align-items: center; gap: 8px; }
        .status-dot { width: 8px; height: 8px; border-radius: 50%; background-color: #ef4444; }
        .status-dot.connected { background-color: #22c55e; }
        .visualizer { display: flex; align-items: center; gap: 3px; height: 14px; opacity: 0; transition: opacity 0.2s; }
        .visualizer.playing { opacity: 1; }
        .bar { width: 3px; background-color: #38bdf8; border-radius: 2px; animation: bounce 0.5s ease infinite alternate; }
        .bar:nth-child(1) { height: 60%; animation-delay: 0.0s; }
        .bar:nth-child(2) { height: 100%; animation-delay: 0.1s; background-color: #818cf8; }
        .bar:nth-child(3) { height: 80%; animation-delay: 0.2s; }
        .bar:nth-child(4) { height: 40%; animation-delay: 0.3s; background-color: #818cf8; }
        @keyframes bounce { from { transform: scaleY(0.4); } to { transform: scaleY(1.0); } }
        .chat-container { display: flex; flex-direction: column; gap: 12px; }
        .message-bubble { background: rgba(30, 41, 59, 0.8); border-radius: 8px; padding: 12px; transition: all 0.4s ease; border-left: 3px solid #6366f1; }
        .message-bubble.new { opacity: 0; transform: translateX(-10px); }
        .message-bubble.fade-out { opacity: 0; transform: scale(0.95); }
        .msg-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
        .msg-author { font-size: 0.95rem; font-weight: 700; color: #e2e8f0; }
        .msg-badges { display: flex; gap: 6px; }
        .badge-provider { background: #312e81; color: #a5b4fc; font-size: 0.7rem; padding: 2px 6px; border-radius: 4px; font-weight: 600; text-transform: uppercase; }
        .badge-voice { background: #0f766e; color: #5eead4; font-size: 0.7rem; padding: 2px 6px; border-radius: 4px; font-weight: 600; }
        .msg-text { font-size: 1.05rem; line-height: 1.4; color: #f1f5f9; }
    </style>
</head>
<body>
    <div class="main-hud" id="hud">
        <div class="hud-header">
            <div class="hud-title">
                <div class="status-dot" id="statusDot"></div>
                <span>TTS Broadcast (__CHANNEL__)</span>
            </div>
            <div class="visualizer" id="visualizer">
                <div class="bar"></div><div class="bar"></div><div class="bar"></div><div class="bar"></div>
            </div>
        </div>
        <div class="chat-container" id="chatContainer"></div>
    </div>
    <audio id="ttsAudioPlayer" style="display: none;"></audio>
    <script>
        let ws;
        const player = document.getElementById('ttsAudioPlayer');
        const hud = document.getElementById('hud');
        const chatContainer = document.getElementById('chatContainer');
        const visualizer = document.getElementById('visualizer');
        const statusDot = document.getElementById('statusDot');
        let hideHudTimeout = null;

        player.onplay = () => { visualizer.classList.add('playing'); if (hideHudTimeout) clearTimeout(hideHudTimeout); };
        player.onended = () => { visualizer.classList.remove('playing'); resetHudTimeout(); };

        function resetHudTimeout() {
            if (hideHudTimeout) clearTimeout(hideHudTimeout);
            hideHudTimeout = setTimeout(() => {
                if (player.paused && chatContainer.children.length === 0) hud.classList.remove('active');
            }, 10000);
        }

        function appendMessage(text, author, provider, voice) {
            hud.classList.add('active');
            if (hideHudTimeout) clearTimeout(hideHudTimeout);
            const msgDiv = document.createElement('div');
            msgDiv.className = 'message-bubble new';
            let headerHtml = `<div class="msg-header"><div class="msg-author">${author || 'Anonymous'}</div><div class="msg-badges">`;
            if (provider) headerHtml += `<span class="badge-provider">${provider}</span>`;
            if (voice) headerHtml += `<span class="badge-voice">${voice}</span>`;
            headerHtml += `</div></div>`;
            msgDiv.innerHTML = headerHtml + `<div class="msg-text">${text}</div>`;
            chatContainer.appendChild(msgDiv);
            requestAnimationFrame(() => { msgDiv.classList.remove('new'); });
            while (chatContainer.children.length > 3) chatContainer.removeChild(chatContainer.firstChild);
            setTimeout(() => {
                msgDiv.classList.add('fade-out');
                setTimeout(() => { if (msgDiv.parentElement) msgDiv.remove(); resetHudTimeout(); }, 400);
            }, 15000);
        }

        function connect() {
            const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
            ws = new WebSocket(`${scheme}://${window.location.host}/ws/tts/__TOKEN__`);
            ws.onopen = () => { statusDot.classList.add('connected'); appendMessage("TTS source connected and listening...", "SYSTEM", "MOCKBOT", ""); };
            ws.onclose = () => { statusDot.classList.remove('connected'); setTimeout(connect, 3000); };
            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.action === 'kill_audio') {
                        player.pause(); player.src = ''; visualizer.classList.remove('playing'); chatContainer.innerHTML = ''; resetHudTimeout();
                    }
                    if (data.action === 'play_audio' && data.file) {
                        appendMessage(data.message || '\\uD83C\\uDF99 << audio transmission >>', data.author, data.provider, data.voice);
                        player.src = data.file;
                        player.play().catch(e => console.error("Autoplay blocked:", e));
                    }
                } catch (e) { console.error("WS parse error:", e); }
            };
        }
        connect();
    </script>
</body>
</html>"""


def render_playback_page(token: str, channel: str) -> str:
    return _PLAYBACK_HTML.replace("__TOKEN__", token).replace("__CHANNEL__", channel.lstrip("#"))
