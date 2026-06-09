import asyncio
import os
import json
import time
from datetime import datetime

from bot.colors import YELLOW, RESET


async def heartbeat_loop(bot):
    """was heartbeat_task — update the heartbeat file periodically."""
    while True:
        update_heartbeat_file(bot)
        await asyncio.sleep(60)  # Update every 60 seconds


def update_heartbeat_file(bot):
    """Write current bot status to heartbeat file and database."""
    try:
        # Get current joined channels - strip # for consistent matching
        # We use a list comprehension to get only the channel names from _joined_channels
        # This ensures we only list truly joined channels
        channels_list = [channel.lstrip('#') for channel in bot._joined_channels]

        # Remove empty strings from the list
        channels_list = [ch for ch in channels_list if ch]

        # Current timestamp for consistency
        current_time = time.time()
        formatted_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        data = {
            "timestamp": current_time,
            "nick": bot.nick,
            "channels": channels_list,  # Store channels without # prefix
            "uptime": current_time - bot.start_time,
            "tts_enabled": bot.enable_tts,
            "pid": os.getpid()
        }

        # Write to heartbeat JSON file
        with open("bot_heartbeat.json", "w") as f:
            json.dump(data, f)

        # Also update the PID file to ensure it exists
        with open("bot.pid", "w") as f:
            f.write(str(os.getpid()))

        if bot.verbose_heartbeat_log:  # Use the new config setting
            bot.logger.info(f"{YELLOW}Heartbeat: Raw channels from _joined_channels: {channels_list}{RESET}")

        # Update the database for web UI connection status
        try:
            bot.db.set_bot_status_sync('last_heartbeat', formatted_time)
            bot.db.set_bot_status_sync('connected_channels', ','.join(channels_list))
            with bot.db.connect_sync() as conn:
                conn.execute("UPDATE channel_configs SET currently_connected = 0")
                for channel in channels_list:
                    clean_channel = channel.lstrip('#')
                    conn.execute(
                        "UPDATE channel_configs SET currently_connected = 1 WHERE channel_name = ?",
                        (clean_channel,),
                    )
                conn.commit()
            if bot.verbose_heartbeat_log:
                bot.my_logger.log_info(f"Heartbeat: Updated database heartbeat at {formatted_time}")
                bot.logger.info(f"{YELLOW}Heartbeat: Processed connected channels for DB: {channels_list}{RESET}")
        except Exception as db_error:
            bot.my_logger.error(f"Heartbeat: Error updating database heartbeat: {db_error}")

    except Exception as e:
        bot.my_logger.error(f"Heartbeat: Error updating heartbeat file: {e}")
