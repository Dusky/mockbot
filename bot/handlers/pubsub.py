"""PubSub event handling — Bits/Cheers and Channel Point redemptions."""
from datetime import datetime


async def handle_bits(bot, event):
    """Handle incoming Bits/Cheers via PubSub."""
    channel_name = bot._channel_ids.get(event.channel_id, "unknown")
    user_name = event.user.name if event.user else "Anonymous"

    try:
        async with bot.db.connect_async() as conn:
            c = await conn.cursor()
            await c.execute("SELECT pubsub_bits FROM channel_configs WHERE channel_name = ?", (channel_name.lstrip('#'),))
            row = await c.fetchone()
            if not row or not row[0]:
                return  # Bits tracking is disabled
    except Exception as e:
        bot.logger.error(f"Failed to check pubsub_bits config: {e}")
        return

    bot.logger.info(f"Received {event.bits_used} bits from {user_name} in {channel_name}!")

    # We can implement a fun random response or customized cheer logic here!
    channel = bot.get_channel(channel_name.lstrip('#'))
    if channel:
        await channel.send(f"Thank you {user_name} for the {event.bits_used} bits! bloodTrail")


async def handle_channel_points(bot, event):
    """Handle channel point redemptions via PubSub."""
    channel_name = bot._channel_ids.get(event.channel_id, "unknown")
    user_name = event.user.name if event.user else "Anonymous"
    reward_title = event.reward.title

    try:
        async with bot.db.connect_async() as conn:
            c = await conn.cursor()
            await c.execute("SELECT pubsub_points, tts_reward, voice_preset FROM channel_configs WHERE channel_name = ?", (channel_name.lstrip('#'),))
            row = await c.fetchone()
            if not row or not row[0]:
                return  # Points tracking is disabled

            tts_reward = row[1]
            voice_preset = row[2]

    except Exception as e:
        bot.logger.error(f"Failed to check pubsub_points config: {e}")
        return

    bot.logger.info(f"Channel point redemption: {reward_title} by {user_name} in {channel_name}")

    # 1. Check if this is a TTS Reward redemption!
    if tts_reward and tts_reward.lower() == reward_title.lower() and event.input:
        bot.logger.info(f"TTS Channel Point Reward triggered by {user_name}: {event.input}")
        import uuid
        fake_msg_id = f"cp_tts_{uuid.uuid4().hex[:8]}"
        timestamp_str = datetime.now().isoformat()

        from bot.tts import start_tts_processing
        start_tts_processing(
            input_text=event.input,
            channel_name=channel_name.lstrip('#'),
            db_file=bot.db_file,
            message_id=fake_msg_id,
            timestamp_str=timestamp_str,
            voice_preset_override=voice_preset
        )

    # If the reward title matches a custom command, run it through the shared
    # evaluator (same Tracery/var-macro engine as chat; no moderation context here).
    # We prefix it with '!' just in case it's defined that way in the DB.
    cmd_trigger = reward_title if reward_title.startswith('!') else f"!{reward_title}"
    clean_channel = channel_name.lstrip('#')
    try:
        final_response = await bot.custom_cmd_handler.evaluate(
            clean_channel, cmd_trigger, event.input or "", sender=user_name, author=None,
        )
        if final_response:
            channel = bot.get_channel(clean_channel)
            if channel:
                await channel.send(final_response)
            bot.logger.info(f"Custom command triggered by channel points: {cmd_trigger} -> {final_response}")
    except Exception as e:
        bot.logger.error(f"Error evaluating custom command from channel points: {e}")
