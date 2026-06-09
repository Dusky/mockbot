import asyncio
import logging


async def flush_db_queue(bot):
    """Force flush remaining messages in the queue to the database."""
    if not bot.message_queue:
        return

    messages_to_insert = list(bot.message_queue)
    bot.message_queue.clear()

    try:
        async with bot.db.connect_async() as conn:
            c = await conn.cursor()
            await c.executemany(
                """INSERT INTO messages (twitch_message_id, message, author_name, timestamp, channel, is_bot_response, message_length, tts_processed)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                messages_to_insert
            )
            await conn.commit()
        bot.logger.info(f"Forcibly flushed {len(messages_to_insert)} messages to DB during shutdown.")
    except Exception as e:
        bot.logger.error(f"Failed to cleanly flush message queue: {e}")


async def background_db_writer(bot):
    """Background asynchronous task that periodically commits queued messages to the database in bulk."""
    bot.logger.info("Background DB writer started.")
    while True:
        try:
            await asyncio.sleep(2.0)  # Flush every 2 seconds

            if not bot.message_queue:
                continue

            # Take a shallow copy and clear the main list lock-free
            messages_to_insert = list(bot.message_queue)
            bot.message_queue.clear()

            try:
                async with bot.db.connect_async() as conn:
                    c = await conn.cursor()
                    await c.executemany(
                        """INSERT INTO messages (twitch_message_id, message, author_name, timestamp, channel, is_bot_response, message_length, tts_processed)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        messages_to_insert
                    )
                    await conn.commit()
            except Exception as db_err:
                bot.logger.error(f"Failed bulk inserting messages to DB: {db_err}")
                # Re-queue the messages so they aren't lost if the DB momentarily locked
                bot.message_queue.extend(messages_to_insert)
        except asyncio.CancelledError:
            bot.logger.info("Background DB writer cancelled. Flushing remaining messages...")
            await flush_db_queue(bot)
            break
        except Exception as e:
            bot.logger.error(f"Unexpected error in background DB writer: {e}")
            await asyncio.sleep(2.0)
