"""Command intake — bus handlers for client -> bot commands.

Transports translate external requests into bus commands; the handlers here are
the single place the bot acts on them. Today the only transport is the file
poller (bot/tasks/message_requests.py); a websocket/REST transport plugs into the
same bus later without touching this logic.
"""
import asyncio
import time
from datetime import datetime

from bot.colors import YELLOW, RED, GREEN, PURPLE, RESET
from bot.tts import start_tts_processing


async def handle_send_message(bot, cmd):
    """Send a SendMessageCommand to chat (with join+retry), log it, then optional TTS."""
    channel = cmd.channel
    message = cmd.message
    if not channel.startswith('#'):
        channel = f"#{channel}"

    bot.logger.info(f"{YELLOW}Attempting to send message to {channel}{RESET}: {message[:50]}...")
    try:
        channel_obj = bot.get_channel(channel.lstrip('#'))
        success = False

        if channel_obj:
            await channel_obj.send(message)
            bot.logger.info(f"{GREEN}Successfully sent message via channel object to {channel}{RESET}")
            success = True
        else:
            sent = await bot.send_message_to_channel(channel, message)
            if sent:
                bot.logger.info(f"{GREEN}Successfully sent message via helper to {channel}{RESET}")
                success = True
            else:
                bot.logger.error(f"{RED}Failed to send message to {channel}{RESET}")
                bot.logger.info(f"{YELLOW}Attempting to join {channel} and retry...{RESET}")
                join_success = await bot.join_channel(channel)

                if join_success:
                    await asyncio.sleep(0.5)
                    channel_obj = bot.get_channel(channel.lstrip('#'))
                    if channel_obj:
                        try:
                            await channel_obj.send(message)
                            bot.logger.info(f"{GREEN}Successfully sent message on retry to {channel}{RESET}")
                            success = True
                        except Exception as send_error:
                            bot.logger.error(f"{RED}Error sending message on retry: {send_error}{RESET}")
                    else:
                        sent = await bot.send_message_to_channel(channel, message)
                        if sent:
                            bot.logger.info(f"{GREEN}Successfully sent message on second retry to {channel}{RESET}")
                            success = True
                        else:
                            bot.logger.error(f"{RED}Failed to send message on second retry to {channel}{RESET}")
                else:
                    bot.logger.error(f"{RED}Failed to join channel {channel}{RESET}")

        if success:
            bot.logger.info(f"{GREEN}Message request processed successfully{RESET}: Sent to {PURPLE}{channel}{RESET}")
            channel_clean = channel.lstrip('#')
            bot.my_logger.log_message(channel_clean, bot.nick, message, is_bot_message=True)

            # Generate TTS if enabled for this channel
            try:
                lines_between, time_between, tts_enabled, voice_enabled, _, _ = await bot.fetch_channel_settings(channel_clean)
                if bot.enable_tts and tts_enabled:
                    message_id = int(time.time() * 1000)  # timestamp as message id
                    timestamp_str = datetime.now().isoformat()
                    voice_preset_for_tts = bot.channel_settings.get(channel_clean, {}).get('voice_preset', 'v2/en_speaker_0')
                    bot.logger.info(f"Starting TTS processing for generated message. Channel: {channel_clean}, Text: '{message[:30]}...'")
                    start_tts_processing(
                        input_text=message,
                        channel_name=channel_clean,
                        message_id=message_id,
                        timestamp_str=timestamp_str,
                        voice_preset_override=voice_preset_for_tts,
                        db_file=bot.db_file,
                    )
                    bot.logger.info("TTS processing initiated for generated message.")
            except Exception as tts_error:
                bot.logger.error(f"Error starting TTS for generated message in {channel_clean}: {tts_error}")
        else:
            bot.logger.error(f"{RED}Failed to send message to {channel} after all attempts{RESET}")

    except Exception as send_error:
        bot.logger.error(f"{RED}Error sending message to {channel}: {send_error}{RESET}")
