"""Single source of truth for per-channel settings.

Every channel setting is declared once here. The database column allowlist
(`bot/database.py`), the TUI settings modal (`bot/ui_managers.py`), and the chat
`/set` command (`bot/tui.py`) all derive from this registry instead of
maintaining their own parallel lists — which had drifted (help text, the `/set`
usage string, and the error string each listed different keys).

This module is pure stdlib (dataclasses + os) and imports nothing from the rest
of the app, so it stays import-cheap and unit-testable without the heavy deps.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from collections import OrderedDict


@dataclass(frozen=True)
class Setting:
    key: str                      # == channel_configs column name
    kind: str                     # "bool" | "int" | "float" | "str" | "enum"
    default: object
    category: str = ""
    description: str = ""
    choices: tuple = ()           # for enum: ((label, value), ...)
    minimum: float | None = None
    maximum: float | None = None
    aliases: tuple = ()           # chat /set aliases, e.g. "lines"
    user_editable: bool = True    # False for metadata columns
    show_in_settings: bool = True  # False = managed elsewhere (e.g. lore_bias)
    ui_override: str = ""         # force a control type, e.g. "lore_button"
    dynamic_choices: object = None  # callable() -> [(label, value), ...]

    @property
    def control(self) -> str | None:
        """UI control type for the settings modal, or None if not shown there."""
        if not (self.user_editable and self.show_in_settings):
            return None
        if self.ui_override:
            return self.ui_override
        if self.kind == "bool":
            return "bool_select"
        if self.kind == "enum" or self.dynamic_choices is not None:
            return "select"
        return "input"


# ── dynamic choice providers (scan the ./voices directory) ──────────────────

_BARK_PRESETS = [(f"Bark: Gen {i}", f"v2/en_speaker_{i}") for i in range(10)]


def voice_preset_choices() -> list:
    options = list(_BARK_PRESETS)
    if os.path.exists("./voices"):
        for fname in sorted(os.listdir("./voices")):
            if fname.endswith((".npz", ".wav")):
                options.append((fname, fname))
    return options


def rvc_model_choices() -> list:
    options = [("None", "")]
    if os.path.exists("./voices"):
        for fname in sorted(os.listdir("./voices")):
            if fname.endswith(".pth"):
                name = fname[:-len(".pth")]
                options.append((name, name))
    return options


# ── the registry ────────────────────────────────────────────────────────────
# Ordered so by_category() reproduces the existing settings-modal layout.
# Defaults mirror the channel_configs schema in bot/db.py (no behavior change),
# except rvc_api_url which is canonicalised to :5051 (the runtime default the
# migration and get_tts_config_sync already use; the CREATE TABLE said :5000).

CATEGORY_ORDER = [
    "Behavior & Core Rules",
    "External Lore Modeling",
    "Twitch Event Triggers",
    "TTS Fundamentals",
    "Synthesis Tuning (Bark/Chatterbox)",
    "Voice Cloning (RVC)",
]

_SETTINGS = [
    # ── Behavior & Core Rules ──
    Setting("join_channel", "bool", 1, "Behavior & Core Rules",
            "1 to automatically join this Twitch channel on startup, 0 to not."),
    Setting("use_general_model", "bool", 1, "Behavior & Core Rules",
            "1 to use the generic AI brain model, 0 to use an individual channel-specific model.",
            aliases=("model",)),
    Setting("random_chance", "float", 0.0, "Behavior & Core Rules",
            "Percentage chance (0-100) for the bot to spontaneously reply to a user message.",
            minimum=0.0, maximum=100.0, aliases=("chance",)),
    Setting("log_dice", "bool", 0, "Behavior & Core Rules",
            "1 to log random-chance rolls to the console window, 0 to hide them.",
            aliases=("log_dice",)),
    Setting("lines_between_messages", "int", 100, "Behavior & Core Rules",
            "Number of chat messages that must pass before the bot interjects passively.",
            minimum=0, aliases=("lines",)),
    Setting("time_between_messages", "int", 0, "Behavior & Core Rules",
            "Seconds that must pass before the bot interjects passively.",
            minimum=0, aliases=("time",)),

    # ── External Lore Modeling ──
    Setting("enabled_lore", "str", "", "External Lore Modeling",
            "Comma-separated list of .txt files from 'lore/' to inject into the brain.",
            ui_override="lore_button"),
    Setting("lore_bias", "float", 15.0, "External Lore Modeling",
            "Base weighting for the channel lore model when combined with external lore.",
            show_in_settings=False),  # edited inside the Lore manager

    # ── Twitch Event Triggers ──
    Setting("pubsub_bits", "bool", 0, "Twitch Event Triggers",
            "1 to respond to bits/cheers, 0 to ignore them.", aliases=("bits",)),
    Setting("pubsub_points", "bool", 0, "Twitch Event Triggers",
            "1 to respond to channel point redemptions, 0 to ignore.", aliases=("points",)),
    Setting("tts_reward", "str", "", "Twitch Event Triggers",
            "The exact name of a Twitch Channel Point reward that triggers TTS."),

    # ── TTS Fundamentals ──
    Setting("tts_enabled", "bool", 0, "TTS Fundamentals",
            "1 to enable TTS, 0 to disable. Determines if the bot speaks out loud."),
    Setting("voice_enabled", "bool", 0, "TTS Fundamentals",
            "1 to enable custom voices, 0 to disable. Custom AI voices over basic OS voices."),
    Setting("tts_delay_enabled", "bool", 0, "TTS Fundamentals",
            "1 to generate TTS before sending the message (better pacing), 0 for immediate.",
            aliases=("delay",)),
    Setting("tts_provider", "enum", "bark", "TTS Fundamentals",
            "Which TTS engine to use.",
            choices=(("Suno Bark", "bark"), ("Chatterbox TTS", "chatterbox"),
                     ("RVC (Bark Base)", "rvc"), ("RVC (Chatterbox Base)", "rvc_chatterbox"))),
    Setting("voice_preset", "str", "v2/en_speaker_5", "TTS Fundamentals",
            "The voice profile for TTS (e.g. 'v2/en_speaker_5').",
            aliases=("voice",), dynamic_choices=voice_preset_choices),

    # ── Synthesis Tuning ──
    Setting("bark_model", "enum", "regular", "Synthesis Tuning (Bark/Chatterbox)",
            "Which Bark model to use.",
            choices=(("Small", "small"), ("Regular", "regular"))),
    Setting("bark_text_temp", "float", 0.7, "Synthesis Tuning (Bark/Chatterbox)",
            "Text temperature for Suno Bark (default 0.7)."),
    Setting("bark_waveform_temp", "float", 0.7, "Synthesis Tuning (Bark/Chatterbox)",
            "Waveform temperature for Suno Bark (default 0.7)."),
    Setting("chatterbox_temperature", "float", 0.8, "Synthesis Tuning (Bark/Chatterbox)",
            "Generation randomness for Chatterbox (default 0.8)."),
    Setting("chatterbox_exaggeration", "float", 0.5, "Synthesis Tuning (Bark/Chatterbox)",
            "Expressiveness control for Chatterbox (default 0.5)."),

    # ── Voice Cloning (RVC) ──
    Setting("rvc_model", "str", "", "Voice Cloning (RVC)",
            "The underlying .pth model name in /voices for voice cloning.",
            dynamic_choices=rvc_model_choices),
    Setting("rvc_pitch", "int", 0, "Voice Cloning (RVC)",
            "Pitch shift for RVC (-12 female->male, +12 male->female)."),
    Setting("rvc_index_rate", "float", 0.75, "Voice Cloning (RVC)",
            "Index rate for cloning accuracy ratio (default 0.75)."),
    Setting("rvc_api_url", "str", "http://127.0.0.1:5051", "Voice Cloning (RVC)",
            "API URL of the RVC wrapper (e.g. http://127.0.0.1:5051)."),

    # ── Metadata columns (not user-editable settings, but valid DB columns) ──
    Setting("owner", "str", "", user_editable=False, show_in_settings=False),
    Setting("trusted_users", "str", "", user_editable=False, show_in_settings=False),
    Setting("ignored_users", "str", "", user_editable=False, show_in_settings=False),
    Setting("currently_connected", "bool", 0, user_editable=False, show_in_settings=False),
    Setting("user_id", "int", None, user_editable=False, show_in_settings=False),
]

REGISTRY: "OrderedDict[str, Setting]" = OrderedDict((s.key, s) for s in _SETTINGS)

_ALIASES = {alias: s.key for s in _SETTINGS for alias in s.aliases}

# Tokens accepted for boolean settings. "general"/"individual" let `/set model`
# (use_general_model) share the same coercion path.
_BOOL_TRUE = {"1", "on", "true", "yes", "enabled", "general"}
_BOOL_FALSE = {"0", "off", "false", "no", "disabled", "individual"}


# ── public helpers ──────────────────────────────────────────────────────────

def all_columns() -> frozenset:
    """Every channel_configs column the app may read/write (excludes channel_name)."""
    return frozenset(REGISTRY.keys())


def editable_keys() -> list:
    return [k for k, s in REGISTRY.items() if s.user_editable]


def alias_to_key(token: str) -> str | None:
    """Resolve a chat token (real key or alias) to its canonical column key."""
    token = (token or "").lower()
    if token in REGISTRY:
        return token
    return _ALIASES.get(token)


def coerce(key: str, raw) -> object:
    """Convert a raw string/value to the stored type for `key`. Raises ValueError."""
    s = REGISTRY[key]
    if s.kind == "bool":
        t = str(raw).strip().lower()
        if t in _BOOL_TRUE:
            return 1
        if t in _BOOL_FALSE:
            return 0
        raise ValueError(f"{key} expects on/off (got {raw!r})")
    if s.kind == "int":
        return int(str(raw).strip())
    if s.kind == "float":
        return float(str(raw).strip())
    return str(raw).strip()


def validate(key: str, value) -> tuple:
    """Return (ok, error_message). Enum membership + numeric range checks."""
    s = REGISTRY[key]
    if s.kind == "enum":
        valid = [v for _, v in s.choices]
        if str(value) not in valid:
            return False, f"{key} must be one of: {', '.join(valid)}"
    if s.kind in ("int", "float") and value is not None:
        if s.minimum is not None and value < s.minimum:
            return False, f"{key} must be >= {s.minimum}"
        if s.maximum is not None and value > s.maximum:
            return False, f"{key} must be <= {s.maximum}"
    return True, ""


def by_category() -> "OrderedDict[str, list]":
    """Settings shown in the modal, grouped in CATEGORY_ORDER."""
    groups: "OrderedDict[str, list]" = OrderedDict((c, []) for c in CATEGORY_ORDER)
    for s in REGISTRY.values():
        if s.control is None:
            continue
        groups.setdefault(s.category, []).append(s)
    return OrderedDict((c, items) for c, items in groups.items() if items)


def set_aliases_help() -> str:
    """Comma-joined list of the curated /set aliases for help text."""
    return ", ".join(s.aliases[0] for s in REGISTRY.values() if s.aliases)
