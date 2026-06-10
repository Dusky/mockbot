"""Bot startup sequence — runs once on the TwitchIO ready event."""
import os
import time

from bot.config import config
from bot.colors import YELLOW, RED, GREEN, RESET


async def run(bot):
    """Handle the bot ready event."""
    # Bind the event bus to the now-running loop (publishers may fire from threads).
    bot.event_bus.set_loop(bot.loop)

    # Use verbose flag for detailed output
    verbose = os.environ.get('VERBOSE', '').lower() in ('true', '1', 'yes')

    # Step 1: Initialize channel configs in the database
    try:
        if verbose:
            bot.logger.info(f"{YELLOW}Step 1: Initializing channel configurations...{RESET}")
        bot.ensure_channel_configs()
        if verbose:
            bot.logger.info(f"{GREEN}✅ Channel configs initialized{RESET}")
    except Exception as e:
        bot.logger.info(f"{RED}❌ Error initializing channel configs: {e}{RESET}")

    # Step 2: Set start time for uptime tracking
    bot._start_time = time.time()

    # Step 2.5: Cache Bot's Twitch User ID for API calls (like Timeout)
    try:
        bot_users = await bot.fetch_users(names=[bot.nick])
        if bot_users:
            bot.bot_user_id = bot_users[0].id
            if verbose:
                bot.logger.info(f"{GREEN}✅ Bot User ID Cached: {bot.bot_user_id}{RESET}")
    except Exception as e:
        bot.logger.info(f"{RED}❌ Failed to cache Bot User ID: {e}{RESET}")

    # Step 3: Process channels from config file
    try:
        if verbose:
            bot.logger.info(f"{YELLOW}Step 3: Processing channels from config file...{RESET}")
        if config.channels:
            config_channels = [ch.strip() for ch in config.channels if ch.strip()]

            if verbose:
                bot.logger.info(f"{YELLOW}Found {len(config_channels)} channels in config file{RESET}")

            # Make sure each config channel has a database entry
            for channel in config_channels:
                clean_name = channel.lstrip('#')
                # Update channel config to ensure it's set to be joined
                try:
                    with bot.db.connect_sync() as conn:
                        c = conn.cursor()
                        c.execute("SELECT 1 FROM channel_configs WHERE channel_name = ?", (clean_name,))

                        if not c.fetchone():
                            # Create new entry
                            if verbose:
                                bot.logger.info(f"{YELLOW}Creating config for config file channel: {clean_name}{RESET}")
                            c.execute('''
                                INSERT INTO channel_configs
                                (channel_name, tts_enabled, voice_enabled, join_channel, owner,
                                trusted_users, ignored_users, use_general_model, lines_between_messages, time_between_messages, currently_connected, tts_delay_enabled, pubsub_bits, pubsub_points, tts_reward)
                                VALUES (?, 0, 1, 1, ?, '', '', 1, 50, 15, 0, 0, 0, 0, '')
                            ''', (clean_name, clean_name))
                        else:
                            # Update existing entry to make sure join_channel is enabled
                            c.execute("UPDATE channel_configs SET join_channel = 1 WHERE channel_name = ?", (clean_name,))

                        conn.commit()
                except Exception as db_error:
                    bot.logger.info(f"{RED}Error updating channel config for {clean_name}: {db_error}{RESET}")
        elif verbose:
            bot.logger.info(f"{YELLOW}No channels found in config file{RESET}")

        if verbose:
            bot.logger.info(f"{GREEN}✅ Config file channels processed{RESET}")
    except Exception as e:
        bot.logger.info(f"{RED}❌ Error processing config file channels: {e}{RESET}")

    # Step 4: Join all configured channels from database
    try:
        if verbose:
            bot.logger.info(f"{YELLOW}Step 4: Joining all configured channels...{RESET}")
        await bot.check_and_join_channels(silent=False)  # Initial join, show full output
        if verbose:
            bot.logger.info(f"{GREEN}✅ Channel joining completed{RESET}")
    except Exception as e:
        bot.logger.info(f"{RED}❌ Error joining channels: {e}{RESET}")

    # Step 5: Start periodic channel checking
    try:
        if verbose:
            bot.logger.info(f"{YELLOW}Step 5: Setting up periodic channel check...{RESET}")
        await bot.setup_periodic_channel_check()
        if verbose:
            bot.logger.info(f"{GREEN}✅ Periodic checking started{RESET}")
    except Exception as e:
        bot.logger.info(f"{RED}❌ Error setting up periodic channel check: {e}{RESET}")

    # Step 6: Print status table
    try:
        if verbose:
            bot.logger.info(f"{YELLOW}Step 6: Printing status table...{RESET}")
        await bot.print_channel_status()
        if verbose:
            bot.logger.info(f"{GREEN}✅ Status printed{RESET}")
    except Exception as e:
        bot.logger.info(f"{RED}❌ Error printing channel status: {e}{RESET}")

    # Step 7: Create PID file
    try:
        with open("bot.pid", "w") as f:
            f.write(str(os.getpid()))
        if verbose:
            bot.logger.info(f"{GREEN}✅ Created PID file with PID: {os.getpid()}{RESET}")
    except Exception as e:
        bot.logger.info(f"{RED}❌ Error creating PID file: {e}{RESET}")

    # Step 8: Setup heartbeat
    try:
        if verbose:
            bot.logger.info(f"{YELLOW}Step 8: Setting up heartbeat...{RESET}")
        from bot.tasks import heartbeat as _heartbeat_mod
        _heartbeat_mod.update_heartbeat_file(bot)
        bot.loop.create_task(_heartbeat_mod.heartbeat_loop(bot))
        if verbose:
            bot.logger.info(f"{GREEN}✅ Heartbeat task started{RESET}")
    except Exception as e:
        bot.logger.info(f"{RED}❌ Error setting up heartbeat: {e}{RESET}")

    # Step 9: Start background DB writer & Timed Message Loop
    try:
        if verbose:
            bot.logger.info(f"{YELLOW}Step 9: Starting background DB writer, Timed Message Loop, and Sleep Monitor...{RESET}")
        from bot.tasks import db_writer, timed_messages, sleep_monitor, message_requests, live_stream_monitor
        bot.db_flush_task = bot.loop.create_task(db_writer.background_db_writer(bot))
        bot.timed_msg_task = bot.loop.create_task(timed_messages.timed_message_loop(bot))
        bot.sleep_monitor_task = bot.loop.create_task(sleep_monitor.sleep_monitor_loop(bot))
        bot.message_request_check = bot.loop.create_task(message_requests.message_request_checker(bot))
        bot.live_stream_monitor_task = bot.loop.create_task(live_stream_monitor.live_stream_monitor_loop(bot))
        if verbose:
            bot.logger.info(f"{GREEN}✅ Background loops started{RESET}")
    except Exception as e:
        bot.logger.info(f"{RED}❌ Error starting background loops: {e}{RESET}")

    # Step 10: Setup EventSub (Bits & Channel Points) over websocket.
    # Replaces the deprecated PubSub. Each subscription needs a user token with
    # the right scope for that broadcaster: `bits:read` for cheers and
    # `channel:read:redemptions` for channel-point redemptions. A failure for
    # one channel (e.g. missing scope/authorization) won't abort the rest.
    try:
        if verbose:
            bot.logger.info(f"{YELLOW}Step 10: Setting up EventSub for Bits & Channel Points...{RESET}")

        tmi_token = config.get("auth", "tmi_token")
        if tmi_token.startswith("oauth:"):
            tmi_token = tmi_token[6:]

        clean_channels = [c.lstrip('#') for c in bot._joined_channels]
        users = await bot.fetch_users(names=clean_channels)

        subscribed = 0
        async with bot.db.connect_async() as conn:
            c = await conn.cursor()
            for user in users:
                bot._channel_ids[user.id] = f"#{user.name}"
                try:
                    await c.execute("SELECT pubsub_bits, pubsub_points FROM channel_configs WHERE channel_name = ?", (user.name,))
                    row = await c.fetchone()
                except Exception as e:
                    bot.logger.info(f"Failed to load eventsub config for {user.name}: {e}")
                    continue
                bits_enabled, points_enabled = row if row else (0, 0)

                if bits_enabled:
                    try:
                        await bot.eventsub_ws.subscribe_channel_cheers(broadcaster=user.id, token=tmi_token)
                        subscribed += 1
                    except Exception as e:
                        bot.logger.info(f"{RED}EventSub cheers subscribe failed for #{user.name} (needs bits:read on the broadcaster's token): {e}{RESET}")
                if points_enabled:
                    try:
                        await bot.eventsub_ws.subscribe_channel_points_redeemed(broadcaster=user.id, token=tmi_token)
                        subscribed += 1
                    except Exception as e:
                        bot.logger.info(f"{RED}EventSub redemptions subscribe failed for #{user.name} (needs channel:read:redemptions on the broadcaster's token): {e}{RESET}")

        if subscribed and verbose:
            bot.logger.info(f"{GREEN}✅ Subscribed to {subscribed} EventSub topic(s){RESET}")
    except Exception as e:
        bot.logger.info(f"{RED}❌ Error setting up EventSub: {e}{RESET}")

    # Final verification
    if verbose:
        bot.logger.info(f"{GREEN}Bot initialization complete!{RESET}")

    # Extra verification for channels of interest
    for channel in bot.channels:
        clean_channel = channel.lstrip('#')
        # Create the properly formatted channel name for _joined_channels check
        formatted_channel = f"#{clean_channel}"

        if formatted_channel in bot._joined_channels:
            # Update database to mark channel as connected
            try:
                with bot.db.connect_sync() as conn:
                    c = conn.cursor()
                    c.execute(
                        "UPDATE channel_configs SET currently_connected = 1 WHERE channel_name = ?",
                        (clean_channel,)
                    )
                    conn.commit()
            except Exception as e:
                if verbose:
                    bot.logger.info(f"Error updating channel connection status in DB: {e}")
        else:
            # Make sure database shows it's not connected
            try:
                with bot.db.connect_sync() as conn:
                    c = conn.cursor()
                    c.execute(
                        "UPDATE channel_configs SET currently_connected = 0 WHERE channel_name = ?",
                        (clean_channel,)
                    )
                    conn.commit()
            except Exception as e:
                if verbose:
                    bot.logger.info(f"Error updating channel connection status in DB: {e}")

    # Mark connection as successful for reconnection manager
    bot.connection_manager.mark_connected()
