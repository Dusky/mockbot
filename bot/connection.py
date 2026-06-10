import asyncio
import json
import logging
import time

from bot.colors import YELLOW, RED, GREEN, RESET
from bot.events import ConnectionStateChanged


class ConnectionStateManager:
    """Manages WebSocket connection state and reconnection with exponential backoff."""

    def __init__(self, bot_instance):
        self.bot = bot_instance
        self.state = "disconnected"
        self.reconnect_attempts = 0
        self.last_attempt_time = None
        self.current_delay = 5
        self.max_delay = 300
        self.base_delay = 5
        self.reconnect_task = None
        self.logger = logging.getLogger("connection_manager")

    def calculate_backoff_delay(self):
        return min(self.base_delay * (2 ** self.reconnect_attempts), self.max_delay)

    async def attempt_reconnect(self):
        self.state = "reconnecting"
        self.reconnect_attempts += 1
        self.last_attempt_time = time.time()
        self.current_delay = self.calculate_backoff_delay()

        self.logger.warning(
            f"{YELLOW}Reconnection attempt #{self.reconnect_attempts}, "
            f"waiting {self.current_delay}s before retry...{RESET}"
        )
        self._log_connection_event("reconnect_attempt", {
            "attempt": self.reconnect_attempts,
            "delay": self.current_delay,
        })

        self.bot.event_bus.publish(ConnectionStateChanged(
            "reconnecting", attempts=self.reconnect_attempts, next_delay=self.current_delay))

        try:
            await asyncio.sleep(self.current_delay)

            if hasattr(self.bot, '_ws') and self.bot._ws and not self.bot._ws.is_closed:
                await self.bot._ws.close()

            self.logger.info(f"{GREEN}Attempting to reconnect to Twitch...{RESET}")
            await self.bot.connect()

            self.state = "connected"
            total_attempts = self.reconnect_attempts
            self.reconnect_attempts = 0
            self.current_delay = self.base_delay

            self.logger.info(f"{GREEN}Successfully reconnected after {total_attempts} attempt(s)!{RESET}")
            self._log_connection_event("reconnect_success", {"total_attempts": total_attempts})

            self.bot.event_bus.publish(ConnectionStateChanged("connected", attempts=0))

        except Exception as e:
            self.logger.error(f"{RED}Reconnection attempt #{self.reconnect_attempts} failed: {e}{RESET}")
            self._log_connection_event("reconnect_failed", {
                "error": str(e),
                "attempt": self.reconnect_attempts,
            })
            self.reconnect_task = asyncio.create_task(self.attempt_reconnect())

    def _log_connection_event(self, event_type, details):
        self.bot.db.log_connection_event_sync(
            event_type, json.dumps(details), self.reconnect_attempts
        )

    def mark_connected(self):
        self.state = "connected"
        self.reconnect_attempts = 0
        self.current_delay = self.base_delay
        self._log_connection_event("connected", {"channels": list(self.bot._joined_channels)})
