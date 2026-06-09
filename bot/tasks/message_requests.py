import asyncio
import json
import os
import time
import logging
from datetime import datetime

from bot.colors import YELLOW, RED, GREEN, PURPLE, RESET
from bot.tts import start_tts_processing


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

            # Process the message request
            if data['action'] == 'send_message':
                channel = data['channel']
                message = data['message']

                # Make sure the channel is in the correct format
                if not channel.startswith('#'):
                    channel = f"#{channel}"

                bot.logger.info(f"{YELLOW}Attempting to send message to {channel}{RESET}: {message[:50]}...")

                try:
                    # Try to get the channel object first
                    channel_obj = bot.get_channel(channel.lstrip('#'))
                    success = False

                    if channel_obj:
                        # Send directly with channel object
                        await channel_obj.send(message)
                        bot.logger.info(f"{GREEN}Successfully sent message via channel object to {channel}{RESET}")
                        success = True
                    else:
                        # Fallback to our helper method
                        sent = await bot.send_message_to_channel(channel, message)
                        if sent:
                            bot.logger.info(f"{GREEN}Successfully sent message via helper to {channel}{RESET}")
                            success = True
                        else:
                            bot.logger.error(f"{RED}Failed to send message to {channel}{RESET}")

                            # Try to join the channel and send again - especially if force flag is set
                            bot.logger.info(f"{YELLOW}Attempting to join {channel} and retry...{RESET}")
                            join_success = await bot.join_channel(channel)

                            if join_success:
                                # Try sending one more time - with a slight delay to ensure the join completes
                                await asyncio.sleep(0.5)

                                # Try to get channel object again
                                channel_obj = bot.get_channel(channel.lstrip('#'))
                                if channel_obj:
                                    try:
                                        await channel_obj.send(message)
                                        bot.logger.info(f"{GREEN}Successfully sent message on retry to {channel}{RESET}")
                                        success = True
                                    except Exception as send_error:
                                        bot.logger.error(f"{RED}Error sending message on retry: {send_error}{RESET}")
                                else:
                                    # Last fallback - try helper again
                                    sent = await bot.send_message_to_channel(channel, message)
                                    if sent:
                                        bot.logger.info(f"{GREEN}Successfully sent message on second retry to {channel}{RESET}")
                                        success = True
                                    else:
                                        bot.logger.error(f"{RED}Failed to send message on second retry to {channel}{RESET}")
                            else:
                                bot.logger.error(f"{RED}Failed to join channel {channel}{RESET}")

                    # Log the final result
                    if success:
                        bot.logger.info(f"{GREEN}Message request processed successfully{RESET}: Sent to {PURPLE}{channel}{RESET}")
                        # Save message to logs
                        channel_clean = channel.lstrip('#')
                        bot.my_logger.log_message(channel_clean, bot.nick, message, is_bot_message=True)

                        # Generate TTS if enabled for this channel
                        try:
                            # Fetch channel settings to check if TTS is enabled
                            lines_between, time_between, tts_enabled, voice_enabled, _, _ = await bot.fetch_channel_settings(channel_clean)

                            if bot.enable_tts and tts_enabled:
                                # Generate a unique message ID for TTS processing
                                message_id = int(time.time() * 1000)  # Use timestamp as message ID
                                timestamp_str = datetime.now().isoformat()

                                # Get voice preset for this channel
                                voice_preset_for_tts = bot.channel_settings.get(channel_clean, {}).get('voice_preset', 'v2/en_speaker_0')

                                bot.logger.info(f"Starting TTS processing for generated message. Channel: {channel_clean}, Text: '{message[:30]}...'")
                                start_tts_processing(
                                    input_text=message,
                                    channel_name=channel_clean,
                                    message_id=message_id,
                                    timestamp_str=timestamp_str,
                                    voice_preset_override=voice_preset_for_tts,
                                    db_file=bot.db_file
                                )
                                bot.logger.info("TTS processing initiated for generated message.")

                        except Exception as tts_error:
                            bot.logger.error(f"Error starting TTS for generated message in {channel_clean}: {tts_error}")
                    else:
                        bot.logger.error(f"{RED}Failed to send message to {channel} after all attempts{RESET}")

                except Exception as send_error:
                    bot.logger.error(f"{RED}Error sending message to {channel}: {send_error}{RESET}")

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
