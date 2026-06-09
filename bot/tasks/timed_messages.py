import asyncio
import logging
import random
from datetime import datetime


async def timed_message_loop(bot):
    """Background asynchronous task that periodically evaluates and sends Timed Messages."""
    bot.logger.info("Timed message loop started.")
    while True:
        try:
            await asyncio.sleep(60.0)  # Check every 60 seconds
            if bot.is_sleeping:
                continue  # Do not dispatch timed messages or query DB if the bot is globally sleeping

            try:
                import json
                async with bot.db.connect_async() as conn:
                    c = await conn.cursor()

                    # Find pools where the interval has elapsed since last_sent_time
                    # and verify the bot is currently in the channel
                    await c.execute("""
                        SELECT pool_name, channel_name
                        FROM timed_message_pools
                        WHERE (julianday(CURRENT_TIMESTAMP) - julianday(last_sent_time)) * 1440 >= interval_minutes
                    """)
                    ready_pools = await c.fetchall()

                    for pool_name, channel_name in ready_pools:
                        # Verify bot is in channel
                        if f"#{channel_name}" not in bot._joined_channels:
                            continue

                        # Retrieve all messages for this pool
                        await c.execute(
                            "SELECT message_text FROM timed_messages WHERE pool_name = ? AND channel_name = ?",
                            (pool_name, channel_name)
                        )
                        messages = await c.fetchall()

                        if messages:
                            # Pick a random message from the pool
                            msg_text = random.choice(messages)[0]

                            # Process Tracery if it contains '#'
                            if '#' in msg_text:
                                import tracery
                                from tracery.modifiers import base_english

                                # Fetch grammar rules for this channel + global rules
                                await c.execute(
                                    "SELECT rule_name, options_json FROM custom_grammar WHERE channel_name = ? OR channel_name = 'global'",
                                    (channel_name,)
                                )
                                grammar_rows = await c.fetchall()

                                rules = {}
                                for rule_name, options_json in grammar_rows:
                                    try:
                                        rules[rule_name] = json.loads(options_json)
                                    except:
                                        pass

                                rules["origin"] = [msg_text]
                                rules["streamer"] = [channel_name]
                                grammar = tracery.Grammar(rules)
                                grammar.add_modifiers(base_english)
                                msg_text = grammar.flatten("#origin#")

                            # Send the timed message
                            channel_obj = bot.get_channel(channel_name)
                            if channel_obj and msg_text:
                                await channel_obj.send(msg_text)
                                bot.my_logger.log_message(channel_name, bot.nick, msg_text, is_bot_message=True)

                                # Update the last_sent_time so the interval resets
                                await c.execute(
                                    "UPDATE timed_message_pools SET last_sent_time = CURRENT_TIMESTAMP WHERE pool_name = ? AND channel_name = ?",
                                    (pool_name, channel_name)
                                )
                                await conn.commit()
            except Exception as loop_db_error:
                bot.logger.error(f"Database error in timed messages loop: {loop_db_error}")

        except asyncio.CancelledError:
            bot.logger.info("Timed message loop cancelled.")
            break
        except Exception as e:
            bot.logger.error(f"Unexpected error in timed message loop: {e}")
