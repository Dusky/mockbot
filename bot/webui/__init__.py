"""FastAPI webui — a first-class client of the in-process event bus.

Phase 1 (foundation): serves a health check and a `/ws/events` websocket that
streams bot→client events (connection state, errors, TTS generation, status) as
JSON, sourced entirely from the EventBus. This finally gives the long-dormant
`socketio_emitter` seam a real consumer.

Later phases layer on: Twitch-OAuth auth, the dashboard UI (monitor + control,
with settings rendered from the settings registry), and per-channel token-gated
TTS playback sources (which replace the public :5050 overlay).
"""
from .app import WebUIHub, create_app, start_webui

__all__ = ["WebUIHub", "create_app", "start_webui"]
