import asyncio
import json
import os
import time

from bot.colors import YELLOW, RED, GREEN, RESET
from bot.events import SendMessageCommand


async def check_message_requests(bot):
    """Check for message requests from the web interface"""
    # First check for task restart requests
    restart_file = 'bot_task_restart.json'
    if os.path.exists(restart_file):
        try:
            with open(restart_file, 'r') as f:
                restart_data = json.load(f)

            task_name = restart_data.get('task', '')
            bot.logger.info(f"{YELLOW}Found task restart request for {task_name}{RESET}")

            if task_name == 'message_request_checker' and bot.message_request_check:
                # Cancel the existing task
                try:
                    bot.message_request_check.cancel()
                    bot.logger.info(f"{YELLOW}Cancelled existing message_request_checker task{RESET}")
                except:
                    pass

                # Create a new task
                bot.message_request_check = bot.loop.create_task(message_request_checker(bot))
                bot.logger.info(f"{GREEN}Restarted message_request_checker task{RESET}")

            # Remove the restart file
            try:
                os.remove(restart_file)
            except Exception as e:
                bot.logger.error(f"{RED}Error removing restart file: {e}{RESET}")
        except Exception as e:
            bot.logger.error(f"{RED}Error processing restart request: {e}{RESET}")
            try:
                os.remove(restart_file)
            except:
                pass

    # Now check for message requests
    request_file = 'bot_message_request.json'

    if os.path.exists(request_file):
        bot.logger.info(f"{YELLOW}Found message request file{RESET}")
        try:
            # Read the request file
            with open(request_file, 'r') as f:
                data = json.load(f)

            # Log the request details
            request_id = data.get('request_id', 'unknown')
            action = data.get('action', 'unknown')
            force = data.get('force', False)
            bot.logger.info(f"{YELLOW}Processing message request{RESET}: ID={request_id}, Action={action}, Force={force}")

            # Translate the file request into a bus command; commands_intake
            # performs the actual send (+ join/retry + TTS). The file poller is
            # just one transport feeding the bus.
            if data['action'] == 'send_message':
                bot.event_bus.publish(SendMessageCommand(
                    channel=data['channel'],
                    message=data['message'],
                    force=force,
                    request_id=str(request_id),
                ))
                bot.logger.info(f"{GREEN}Queued send_message command for {data['channel']}{RESET}")

            # Always remove the request file after processing
            try:
                os.remove(request_file)
                bot.logger.info(f"{GREEN}Removed processed request file{RESET}")
            except Exception as rm_error:
                bot.logger.error(f"{RED}Error removing request file: {rm_error}{RESET}")

        except Exception as e:
            bot.logger.error(f"{RED}Error processing message request: {e}{RESET}")

            # Rename the file to avoid repeated errors
            try:
                error_file = f"{request_file}.error.{int(time.time())}"
                os.rename(request_file, error_file)
                bot.logger.info(f"{YELLOW}Renamed error file to {error_file}{RESET}")
            except Exception as rename_error:
                bot.logger.error(f"{RED}Error renaming request file: {rename_error}{RESET}")
                try:
                    # Last resort: try to delete it
                    os.remove(request_file)
                    bot.logger.info(f"{YELLOW}Deleted error file as fallback{RESET}")
                except:
                    pass


async def message_request_checker(bot):
    """Periodically check for message requests"""
    while True:
        await check_message_requests(bot)
        await asyncio.sleep(2)  # Check every 2 seconds
