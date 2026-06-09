import asyncio
import logging


async def live_stream_monitor_loop(bot):
    """Periodically check which of our joined channels are currently live on Twitch."""
    # Wait a bit before first check so bot can init fully
    await asyncio.sleep(15)
    while True:
        try:
            if bot._joined_channels:
                check_channels = [c.lstrip('#') for c in bot._joined_channels]
                live_set = set()

                # fetch_streams takes a max of 100 user logins at a time
                for i in range(0, len(check_channels), 100):
                    chunk = check_channels[i:i+100]
                    try:
                        streams = await bot.fetch_streams(user_logins=chunk)
                        for stream in streams:
                            live_set.add(stream.user.name.lower())
                    except Exception as chunk_err:
                        bot.logger.error(f"Failed to fetch stream chunk: {chunk_err}")

                bot.live_streamers = live_set

        except asyncio.CancelledError:
            break
        except Exception as e:
            bot.logger.error(f"Unexpected error in live stream monitor: {e}")

        # Check every 3 minutes to avoid API spam
        await asyncio.sleep(180)
