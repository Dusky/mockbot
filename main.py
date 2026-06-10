import argparse
from threading import Thread
from bot.core import setup_bot
from bot.db import ensure_db_setup
from bot.config import config as _cfg
import os
try:
    import torch # Keep torch import for TTS check
except ImportError:
    torch = None
import signal
import sys
import time # For shutdown delay
import asyncio # Added import for asyncio

def _warn_missing_eventsub_scopes(db_file, token_scopes):
    """Warn at startup if Bits/Channel-Point features are enabled for any
    channel but the token is missing the EventSub scope they require."""
    import sqlite3
    from bot.config import EVENTSUB_SCOPES, TOKEN_GENERATOR_URL
    try:
        conn = sqlite3.connect(db_file)
        bits_on, points_on = conn.execute(
            "SELECT COALESCE(SUM(pubsub_bits),0), COALESCE(SUM(pubsub_points),0) FROM channel_configs"
        ).fetchone()
        conn.close()
    except Exception:
        return  # DB not ready / no table yet — nothing to warn about

    have = set(token_scopes or [])
    missing = []
    if bits_on and EVENTSUB_SCOPES["pubsub_bits"] not in have:
        missing.append(f"Bits is enabled but the token lacks '{EVENTSUB_SCOPES['pubsub_bits']}'")
    if points_on and EVENTSUB_SCOPES["pubsub_points"] not in have:
        missing.append(f"Channel Points is enabled but the token lacks '{EVENTSUB_SCOPES['pubsub_points']}'")
    if missing:
        print("⚠️  EventSub scope warning:")
        for m in missing:
            print(f"   - {m}")
        print(f"   Regenerate your token with the missing scope(s) at {TOKEN_GENERATOR_URL}")
        print("   (Those Bits/Channel-Point subscriptions will be skipped until then.)")


# Global variable to hold the bot instance for graceful shutdown
bot_instance = None
# Global flag for TTS status, to be passed to webapp
enable_tts_global = False


def graceful_shutdown(signum, frame):
    print("\n🌀 Shutting down...")
    
    global bot_instance

    # Stop the bot
    if bot_instance:
        if bot_instance.loop and bot_instance.loop.is_running():
            # Schedule the close operation on the bot's event loop
            future = asyncio.run_coroutine_threadsafe(bot_instance.close(), bot_instance.loop)
            try:
                future.result(timeout=5) # Wait up to 5 seconds for close to complete
            except TimeoutError:
                pass
            except Exception as e:
                pass
            
            # Stop the bot's event loop
            if bot_instance.loop.is_running():
                # Let the loop stop naturally after pending tasks (like close)
                bot_instance.loop.call_soon_threadsafe(bot_instance.loop.stop)
                
    # Clean up PID file
    if os.path.exists("bot.pid"):
        try:
            os.remove("bot.pid")
        except:
            pass
            
    try:
        sys.exit(0)
    except SystemExit:
        os._exit(0)


def main():
    global bot_instance, enable_tts_global
    try:
        parser = argparse.ArgumentParser(description="Mockbot")
        parser.add_argument("--rebuild-cache", action="store_true", help="Rebuild markov models")
        parser.add_argument("--tts", action="store_true", help="Enable TTS functionality")
        parser.add_argument("--voice-preset", dest="voice_preset", type=str, help="Set default voice preset for TTS")
        parser.add_argument("--rvc-url", dest="rvc_url", type=str, help="Set the background server wrapper endpoint for mockbot cloning")
        args = parser.parse_args()

        # print(f"Arguments parsed: {args}")
        
        enable_tts_global = args.tts # Set the global flag
        
        if args.voice_preset:
            os.environ["DEFAULT_VOICE_PRESET"] = args.voice_preset
            print(f"Set default voice preset via environment: {args.voice_preset}")
            
        if args.rvc_url:
            os.environ["RVC_API_URL"] = args.rvc_url
            print(f"Set remote cloning endpoint via environment: {args.rvc_url}")

        from bot.setup_wizard import needs_setup, run_setup_wizard
        if needs_setup():
            run_setup_wizard()
            if needs_setup():
                print("FATAL: Setup incomplete. Exiting.")
                sys.exit(1)

        if enable_tts_global:
            try:
                print("Importing TTS modules (transformers, torch, scipy)...")
                from transformers import AutoProcessor, BarkModel # Required by Bark
                import torch # Required by Bark and for CUDA check
                import scipy.io.wavfile # Used by Bark for saving
                print("TTS core modules imported successfully.")
            except ImportError as e:
                print(f"FATAL: Error importing TTS modules: {e}. TTS cannot be enabled.")
                print("Please ensure you have installed all dependencies from requirements-tts.txt.")
                enable_tts_global = False # Disable TTS if imports fail
            except Exception as e:
                print(f"An unexpected error occurred during TTS module import: {e}")
                enable_tts_global = False

        # print("Starting CLI TUI...")
        cli_mode = True

        # Pre-flight Token Check
        import requests
        # Load TTS defaults from configuration file into environment
        if _cfg.has_section('tts'):
            if _cfg.has_option('tts', 'rvc_api_url') and "RVC_API_URL" not in os.environ:
                os.environ["RVC_API_URL"] = _cfg.get('tts', 'rvc_api_url')

        token = (_cfg.tmi_token or '').replace('oauth:', '')
        
        token_scopes = []
        if token:
            print("Verifying token...", end='', flush=True)
            try:
                resp = requests.get('https://id.twitch.tv/oauth2/validate', headers={'Authorization': f'OAuth {token}'}, timeout=5)
                if resp.status_code == 200:
                    print(" OK")
                    token_scopes = resp.json().get('scopes', []) or []
                else:
                    from bot.config import TOKEN_GENERATOR_URL, RECOMMENDED_SCOPES
                    print(f"\nFATAL: Token invalid! Twitch says: {resp.json().get('message')}")
                    print(f"Please update 'settings.conf' with a fresh token from {TOKEN_GENERATOR_URL}")
                    print(f"  (Custom Scope Token; include: {', '.join(RECOMMENDED_SCOPES)})")
                    sys.exit(1)
            except Exception as e:
                print(f" (SKIP - Check failed: {e})")

        # print("Setting up database...")
        db_file = "messages.db"
        ensure_db_setup(db_file)
        _warn_missing_eventsub_scopes(db_file, token_scopes)
        # print("Database setup complete.")
            
        # print("Setting up bot...")
        # Create event loop for TwitchIO
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        bot_instance = setup_bot(db_file, rebuild_cache=args.rebuild_cache, enable_tts=enable_tts_global)
        # print("Bot setup complete.")
        
        # Create PID file
        try:
            with open("bot.pid", "w") as f:
                f.write(str(os.getpid()))
        except IOError:
            pass

        # Execution Mode
        # Execution Mode
        # Simple CLI mode - direct output
        sys.stdout.flush()
        
        # Initialize Textual Dashboard
        print("[DEBUG] Importing MockbotDashboard from bot.tui...")
        from bot.tui import MockbotDashboard
        import bot.logger
        
        print("[DEBUG] Instantiating MockbotDashboard...")
        tui_app = MockbotDashboard(bot=bot_instance)
        bot.logger.TUI_LOG_CALLBACK = tui_app.write_log
        
        async def run_concurrently():
            # Start the FastAPI webui (serves the dashboard, the bus event stream,
            # and the per-channel private TTS sources that replaced the :5050
            # overlay). Host/port/proxy/cookie settings come from [web] config —
            # see docs/webui.md for the VPS reverse-proxy + HTTPS setup.
            webui_server = None
            try:
                from bot.webui import start_webui
                webui_server = await start_webui(bot_instance)
            except Exception as e:
                # Log loudly (the TUI swallows stdout): surfaces in logs/mockbot.log
                # and the dashboard log panel. Usually a missing dep — run
                # `pip install -r requirements.txt`.
                import logging
                logging.getLogger("bot").error(
                    f"WebUI failed to start ({type(e).__name__}: {e}). "
                    f"If it's a missing module, run: pip install -r requirements.txt",
                    exc_info=True,
                )
                print(f"[WARN] WebUI failed to start: {e}")
            
            # Create task for the TUI first
            tui_task = asyncio.create_task(tui_app.run_async())
            
            # Wait a moment to ensure TUI mounts before bot starts logging
            await asyncio.sleep(1)
            
            # Create task for the bot
            bot_task = asyncio.create_task(bot_instance.start())
            
            # Wait for either to finish (likely TUI quit)
            done, pending = await asyncio.wait(
                [bot_task, tui_task], 
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # If TUI finished (quit), cancel bot
            for task in pending:
                task.cancel()
                
            # Clean up the web server
            if webui_server is not None:
                webui_server.should_exit = True
        
        # Run the concurrent loop
        loop.run_until_complete(run_concurrently())
        
        # Ensure proper cleanup after run_concurrently finishes
        try:
            loop.run_until_complete(bot_instance.close())
        except AttributeError:
            pass # Websocket keeper wasn't instantiated yet due to early crash
        loop.close()

        
        if enable_tts_global and not torch.cuda.is_available():
             print("WARNING: TTS was enabled without CUDA.")

    except FileNotFoundError as e:
        print(f"FATAL ERROR: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error in main Mockbot execution: {e}")
        import traceback
        traceback.print_exc()
        # graceful_shutdown will be called by the finally block or signal handler
        sys.exit(1) # Ensure exit if main crashes before bot.run()
    finally:
        # Call graceful_shutdown here if not triggered by a signal (e.g., normal exit from bot.run())
        pass


if __name__ == "__main__":
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)
    
    # print("Starting Mockbot...")
    main()
    graceful_shutdown(None, None) # Ensure cleanup if main() returns normally
