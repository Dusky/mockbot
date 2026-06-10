"""Tests for the settings registry and its agreement with the DB schema."""
import sqlite3
import tempfile

import pytest

from bot import settings_registry as sr
from bot.db import ensure_db_setup


def _channel_config_columns():
    """Return {column_name: parsed_default} from a freshly built channel_configs."""
    path = tempfile.mktemp(suffix=".db")
    ensure_db_setup(path)
    conn = sqlite3.connect(path)
    cols = {}
    for row in conn.execute("PRAGMA table_info(channel_configs)").fetchall():
        cols[row[1]] = _parse_sql_default(row[4])
    conn.close()
    return cols


def _parse_sql_default(text):
    if text is None:
        return None
    t = text.strip()
    if len(t) >= 2 and t[0] == "'" and t[-1] == "'":
        return t[1:-1]
    try:
        return float(t) if "." in t else int(t)
    except ValueError:
        return t


# ── registry <-> schema agreement ───────────────────────────────────────────

# Columns that are infrastructure, not user-tunable settings, so they are
# intentionally absent from the settings registry.
_NON_SETTING_COLUMNS = {
    "channel_name",  # primary key, never updated via the registry
    "tts_token",     # generated secret for the private TTS source URL
}


def test_registry_columns_match_schema_exactly():
    cols = set(_channel_config_columns()) - _NON_SETTING_COLUMNS
    assert set(sr.all_columns()) == cols


def test_registry_defaults_match_schema_defaults():
    cols = _channel_config_columns()
    # rvc_api_url is the one deliberate divergence (canonicalised to :5051).
    skip = {"rvc_api_url"} | _NON_SETTING_COLUMNS
    for key, schema_default in cols.items():
        if key in skip or schema_default is None:
            continue
        reg = sr.REGISTRY[key].default
        if isinstance(reg, float) or isinstance(schema_default, float):
            assert float(reg) == pytest.approx(float(schema_default)), key
        else:
            assert reg == schema_default, key


def test_rvc_api_url_default_canonicalised():
    assert sr.REGISTRY["rvc_api_url"].default == "http://127.0.0.1:5051"


# ── coercion ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("on", 1), ("off", 0), ("1", 1), ("0", 0),
    ("true", 1), ("false", 0), ("general", 1), ("individual", 0),
])
def test_coerce_bool(raw, expected):
    assert sr.coerce("tts_enabled", raw) == expected


def test_coerce_bool_rejects_garbage():
    with pytest.raises(ValueError):
        sr.coerce("tts_enabled", "maybe")


def test_coerce_int_and_float():
    assert sr.coerce("lines_between_messages", "50") == 50
    assert isinstance(sr.coerce("lines_between_messages", "50"), int)
    assert sr.coerce("random_chance", "12.5") == 12.5
    assert sr.coerce("rvc_index_rate", "0.75") == 0.75


def test_coerce_str_and_enum_passthrough():
    assert sr.coerce("tts_reward", "  My Reward ") == "My Reward"
    assert sr.coerce("tts_provider", "chatterbox") == "chatterbox"


# ── validation ──────────────────────────────────────────────────────────────

def test_validate_range():
    assert sr.validate("random_chance", 50.0) == (True, "")
    ok, err = sr.validate("random_chance", 150.0)
    assert not ok and "100" in err
    ok, err = sr.validate("random_chance", -1.0)
    assert not ok


def test_validate_enum():
    assert sr.validate("tts_provider", "bark")[0]
    assert not sr.validate("tts_provider", "nonsense")[0]
    assert sr.validate("bark_model", "regular")[0]
    assert not sr.validate("bark_model", "huge")[0]


# ── aliases & grouping ──────────────────────────────────────────────────────

def test_alias_resolution():
    assert sr.alias_to_key("lines") == "lines_between_messages"
    assert sr.alias_to_key("voice") == "voice_preset"
    assert sr.alias_to_key("model") == "use_general_model"
    assert sr.alias_to_key("CHANCE") == "random_chance"
    assert sr.alias_to_key("tts_enabled") == "tts_enabled"  # real key passes through
    assert sr.alias_to_key("nope") is None


def test_set_aliases_help_is_the_curated_nine():
    keys = set(sr.set_aliases_help().split(", "))
    assert keys == {"model", "chance", "log_dice", "lines", "time",
                    "bits", "points", "delay", "voice"}


def test_by_category_layout():
    groups = sr.by_category()
    assert list(groups.keys()) == sr.CATEGORY_ORDER
    shown = {s.key for items in groups.values() for s in items}
    assert "enabled_lore" in shown          # shown as the lore button
    assert "lore_bias" not in shown         # managed inside the lore screen
    assert "owner" not in shown             # metadata, not user-editable
    assert "currently_connected" not in shown


def test_control_types():
    assert sr.REGISTRY["tts_enabled"].control == "bool_select"
    assert sr.REGISTRY["tts_provider"].control == "select"
    assert sr.REGISTRY["voice_preset"].control == "select"   # dynamic choices
    assert sr.REGISTRY["lines_between_messages"].control == "input"
    assert sr.REGISTRY["enabled_lore"].control == "lore_button"
    assert sr.REGISTRY["lore_bias"].control is None
    assert sr.REGISTRY["owner"].control is None
