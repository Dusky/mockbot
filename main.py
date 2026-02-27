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
        args = parser.parse_args()

        # print(f"Arguments parsed: {args}")
        
        enable_tts_global = args.tts # Set the global flag
        
        if args.voice_preset:
            os.environ["DEFAULT_VOICE_PRESET"] = args.voice_preset
            print(f"Set default voice preset via environment: {args.voice_preset}")

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
        
        # Initialize Interactive Shell
        from bot.interactive import InteractiveShell
        from bot.overlay import start_server
        shell = InteractiveShell(bot_instance)
        
        async def run_concurrently():
            # Start the OBS overlay web server
            # start_server returns an AppRunner but doesn't block
            runner = await start_server(port=5050)
            
            # Create tasks for both the bot and the shell
            # bot.start() is the coroutine version of bot.run()
            bot_task = asyncio.create_task(bot_instance.start()) 
            shell_task = asyncio.create_task(shell.run())
            
            # Wait for either to finish (likely shell quit)
            done, pending = await asyncio.wait(
                [bot_task, shell_task], 
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # If shell finished (quit), cancel bot
            for task in pending:
                task.cancel()
                
            # Clean up the web server port
            if runner:
                await runner.cleanup()
        
        # Run the concurrent loop
        loop.run_until_complete(run_concurrently())
        
        # Ensure proper cleanup after run_concurrently finishes
        loop.run_until_complete(bot_instance.close())
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
