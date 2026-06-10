import os
import configparser
import secrets
import sqlite3
from prompt_toolkit import prompt
from prompt_toolkit.styles import Style

from bot.db import ensure_db_setup
from bot.config import TOKEN_GENERATOR_URL, RECOMMENDED_SCOPES

def run_setup_wizard(db_file="messages.db"):
    style = Style.from_dict({
        'prompt': '#ansiteal bold',
    })

    print("\n" + "="*50)
    print("🤖 MockBot First-Time Setup Wizard 🤖")
    print("="*50 + "\n")
    print("Let's get your bot configured.")
    print(f"Generate a Twitch token at: {TOKEN_GENERATOR_URL}")
    print("  → choose 'Custom Scope Token' and select these scopes:")
    print(f"  {', '.join(RECOMMENDED_SCOPES)}")
    print("You can create an application for a Client ID here: https://dev.twitch.tv/console\n")

    tmi_token = prompt("Twitch TMI Token (oauth:...): ", style=style)
    if not tmi_token.startswith("oauth:") and tmi_token:
        print("Note: Token should usually start with 'oauth:'")
        tmi_token = f"oauth:{tmi_token}"

    client_id = prompt("Twitch Client ID: ", style=style)
    nickname = prompt("Bot Nickname (e.g., mycoolbot): ", style=style)
    owner = prompt("Your Twitch Username (Owner): ", style=style)
    first_channel = prompt("First Channel to Join (e.g., your_channel): ", style=style)

    # ── Web dashboard OAuth (a SEPARATE credential from the chat token above) ──
    print("\n" + "-"*50)
    print("Web dashboard login (optional — press Enter to skip).")
    print("This needs your OWN Twitch application, NOT a token generator.")
    print("At https://dev.twitch.tv/console create an app and set its")
    print("OAuth Redirect URL to EXACTLY:  http://localhost:5001/auth/twitch/callback")
    print("(use your public https URL instead when hosting on a server).")
    print("Then paste that app's Client ID and Secret here.\n")
    oauth_client_id = prompt("Dashboard OAuth Client ID (or Enter to skip): ", style=style).strip()
    oauth_client_secret = ""
    oauth_redirect = "http://localhost:5001/auth/twitch/callback"
    if oauth_client_id:
        oauth_client_secret = prompt("Dashboard OAuth Client Secret: ", style=style).strip()
        entered = prompt(f"Redirect URL [{oauth_redirect}]: ", style=style).strip()
        if entered:
            oauth_redirect = entered

    first_channel = first_channel.lstrip('#').lower()

    print("\nSaving configuration...")

    config = configparser.ConfigParser()
    if os.path.exists("settings.example.conf"):
        config.read("settings.example.conf")

    for section in ("auth", "settings", "oauth", "web"):
        if not config.has_section(section):
            config.add_section(section)

    config.set("auth", "tmi_token", tmi_token)
    config.set("auth", "client_id", client_id)
    config.set("auth", "nickname", nickname)
    config.set("auth", "owner", owner)

    # Dashboard OAuth app + a stable session secret (so logins survive restarts).
    if oauth_client_id:
        config.set("oauth", "twitch_client_id", oauth_client_id)
        config.set("oauth", "twitch_client_secret", oauth_client_secret)
        config.set("oauth", "twitch_redirect_uri", oauth_redirect)
    config.set("web", "secret_key", secrets.token_urlsafe(48))


    with open("settings.conf", "w") as f:
        config.write(f)

    print("✓ settings.conf created/updated.")
    
    print("Initializing Database...")
    from bot.database import Database
    db = Database(db_file)
    try:
        with db.connect_sync() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO channel_configs "
                "(channel_name, tts_enabled, voice_enabled, join_channel, owner, trusted_users, use_general_model) "
                "VALUES (?, 0, 1, 1, ?, '', 1)",
                (first_channel, owner),
            )
            conn.commit()
        print(f"✓ Added #{first_channel} to database auto-join list.")
    except Exception as e:
        print(f"Failed to populate database: {e}")
        
    print("\nSetup complete! Starting bot...\n" + "="*50 + "\n")

def needs_setup():
    if not os.path.exists("settings.conf"):
        return True
    
    config = configparser.ConfigParser()
    config.read("settings.conf")
    
    if not config.has_section("auth"):
        return True
        
    token = config.get("auth", "tmi_token", fallback="")
    if "your_oauth_token_here" in token or not token:
        return True
        
    return False

if __name__ == "__main__":
    run_setup_wizard()
