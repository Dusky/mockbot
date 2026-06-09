import asyncio
from datetime import datetime


async def sleep_monitor_loop(bot):
    """Monitors global chat activity and suspends heavy background tasks during long quiet periods (15m)."""
    bot.logger.info("Smart Sleep Monitor started.")
    while True:
        try:
            await asyncio.sleep(60.0)  # Check every minute
            delta = (datetime.now() - bot.last_global_message_time).total_seconds()

            # If total silence > 15 minutes and we aren't asleep yet
            if delta > 900 and not bot.is_sleeping:
                bot.is_sleeping = True
                bot.logger.info("Global chat has been silent for 15+ minutes. Entering Smart Sleep Mode.")
                bot.my_logger.print_message("[dim italic]Entering Smart Sleep Mode due to inactivity...[/dim italic]")
        except asyncio.CancelledError:
            break
        except Exception as e:
            bot.logger.error(f"Error in sleep monitor: {e}")
