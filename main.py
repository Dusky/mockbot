import argparse
import configparser
from threading import Thread
from bot.core import setup_bot
from bot.db import ensure_db_setup
import os
try:
    import torch # Keep torch import for TTS check
except ImportError:
    torch = None
import signal
import sys
import time # For shutdown delay
import asyncio # Added import for asyncio

# Global variable to hold the bot instance for graceful shutdown
bot_instance = None
# Global flag for TTS status, to be passed to webapp
enable_tts_global = False


def graceful_shutdown(signum, frame):
    print("\n\n🌀 Received shutdown signal! Cleaning up...")
    
    global bot_instance

    # Stop the bot
    if bot_instance:
        print("🛑 Stopping Twitch bot...")
        if bot_instance.loop and bot_instance.loop.is_running():
            # Schedule the close operation on the bot's event loop
            future = asyncio.run_coroutine_threadsafe(bot_instance.close(), bot_instance.loop)
            try:
                future.result(timeout=5) # Wait up to 5 seconds for close to complete
                print("Twitch bot closed.")
            except TimeoutError:
                print("Twitch bot close timed out.")
            except Exception as e:
                print(f"Error during bot close: {e}")
            
            # Stop the bot's event loop
            if bot_instance.loop.is_running():
                # Let the loop stop naturally after pending tasks (like close)
                bot_instance.loop.call_soon_threadsafe(bot_instance.loop.stop)
                
    # Clean up PID file
    if os.path.exists("bot.pid"):
        try:
            os.remove("bot.pid")
            print("PID file removed.")
        except OSError as e:
            print(f"Error removing PID file: {e}")
            
    print("✅ Shutdown signal received. Waiting 1s for cleanup...")
    time.sleep(1)
    print("Exiting process.")
    # Force exit to prevent hanging on stuck threads
    os._exit(0)


def main():
    global bot_instance, enable_tts_global
    try:
        parser = argparse.ArgumentParser(description="Mockbot")
        parser.add_argument("--rebuild-cache", action="store_true", help="Rebuild markov models")
        parser.add_argument("--tts", action="store_true", help="Enable TTS functionality")
        parser.add_argument("--voice-preset", dest="voice_preset", type=str, help="Set default voice preset for TTS")
        parser.add_argument("--tui", action="store_true", help="Enable Textual TUI mode")
        args = parser.parse_args()

        # print(f"Arguments parsed: {args}")
        
        enable_tts_global = args.tts # Set the global flag
        
        if args.voice_preset:
            os.environ["DEFAULT_VOICE_PRESET"] = args.voice_preset
            print(f"Set default voice preset via environment: {args.voice_preset}")

        if not os.path.exists('settings.conf'):
            print("FATAL: Missing settings.conf file. Please copy settings.example.conf to settings.conf and configure it.")
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
        config = configparser.ConfigParser()
        config.read('settings.conf')
        token = config.get('auth', 'tmi_token', fallback='').replace('oauth:', '')
        
        if token:
            print("Verifying token...", end='', flush=True)
            try:
                resp = requests.get('https://id.twitch.tv/oauth2/validate', headers={'Authorization': f'OAuth {token}'}, timeout=5)
                if resp.status_code == 200:
                    print(" OK")
                else:
                    print(f"\nFATAL: Token invalid! Twitch says: {resp.json().get('message')}")
                    print("Please update 'settings.conf' with a fresh token from https://twitchapps.com/tmi/")
                    sys.exit(1)
            except Exception as e:
                print(f" (SKIP - Check failed: {e})")

        # print("Setting up database...")
        db_file = "messages.db"
        ensure_db_setup(db_file)
        # print("Database setup complete.")
            
        # Execution Mode
        # CLI Mode
        print("Setting up bot...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        bot_instance = setup_bot(db_file, rebuild_cache=args.rebuild_cache, enable_tts=enable_tts_global)
        print("Bot setup complete.")
        
        # Create PID file
        try:
            with open("bot.pid", "w") as f:
                f.write(str(os.getpid()))
        except IOError:
            pass

        # Simple CLI mode - direct output
        sys.stdout.flush()
        bot_instance.run()

        print("Bot run loop has exited.")
        
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
        # This finally block might not be reached if bot.run() is blocking indefinitely
        # and is only interrupted by a signal. The signal handler is more reliable for cleanup.
        print("Main function's finally block reached.")
        # Call graceful_shutdown here if not triggered by a signal (e.g., normal exit from bot.run())
        # However, if bot.run() is truly blocking, this won't be hit until it stops.
        # If signals are the primary way to stop, the signal handler is key.


if __name__ == "__main__":
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)
    
    # print("Starting Mockbot...")
    main()
    # If main() completes without sys.exit(), it means bot.run() returned.
    print("Mockbot main function has completed. Performing final cleanup...")
    graceful_shutdown(None, None) # Ensure cleanup if main() returns normally
