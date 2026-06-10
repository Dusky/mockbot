import configparser


# A scope-aware token generator (the old twitchapps.com/tmi only grants chat
# scopes and can't add the EventSub ones).
TOKEN_GENERATOR_URL = "https://twitchtokengenerator.com"

# Recommended OAuth scopes for the bot's token. Chat scopes are essential; the
# EventSub scopes power Bits (channel.cheer) and Channel Point redemptions; the
# last two back the !poll command and moderator timeouts.
RECOMMENDED_SCOPES = [
    "chat:read",
    "chat:edit",
    "bits:read",                       # EventSub: channel.cheer
    "channel:read:redemptions",        # EventSub: channel point redemptions
    "channel:manage:polls",            # !poll command
    "moderator:manage:banned_users",   # timeouts
]

# EventSub feature flag (channel_configs column) -> the scope it requires.
EVENTSUB_SCOPES = {
    "pubsub_bits": "bits:read",
    "pubsub_points": "channel:read:redemptions",
}


class Config:
    def __init__(self, path: str = "settings.conf"):
        self._cfg = configparser.ConfigParser()
        self._cfg.read(path)

    # [auth]
    @property
    def tmi_token(self) -> str:
        return self._cfg.get("auth", "tmi_token", fallback="")

    @property
    def client_id(self) -> str:
        return self._cfg.get("auth", "client_id", fallback="")

    @property
    def nickname(self) -> str:
        return self._cfg.get("auth", "nickname", fallback="")

    @property
    def owner(self) -> str:
        return self._cfg.get("auth", "owner", fallback="")

    # [settings]
    @property
    def command_prefix(self) -> str:
        return self._cfg.get("settings", "command_prefix", fallback="!")

    @property
    def channels(self) -> list:
        raw = self._cfg.get("settings", "channels", fallback="")
        return [c for c in raw.split(",") if c.strip()]

    @property
    def debug_mode(self) -> bool:
        return self._cfg.getboolean("settings", "debug_mode", fallback=False)

    # [tts]
    @property
    def enable_tts(self) -> bool:
        return self._cfg.getboolean("tts", "enable_tts", fallback=False)

    @property
    def voice_preset(self) -> str:
        return self._cfg.get("tts", "voice_preset", fallback="v2/en_speaker_6")

    # escape hatch for arbitrary reads
    def get(self, section: str, key: str, fallback=None):
        return self._cfg.get(section, key, fallback=fallback)

    def getboolean(self, section: str, key: str, fallback: bool = False) -> bool:
        return self._cfg.getboolean(section, key, fallback=fallback)

    def has_section(self, section: str) -> bool:
        return self._cfg.has_section(section)

    def has_option(self, section: str, key: str) -> bool:
        return self._cfg.has_option(section, key)


config = Config()
