"""TTS orchestration — voice preset lookup, delay settings, sync generation, and !speak."""
import asyncio
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from bot.database import IntegrityError


def get_channel_voice_preset(bot, channel_name):
    """Fetch the voice_preset for a given channel from the database."""
    clean = channel_name.lstrip('#')
    cfg = bot.db.get_tts_config_sync(clean)
    preset = cfg.get("voice_preset")
    if preset:
        bot.logger.debug(f"Voice preset for channel {clean}: {preset}")
    else:
        bot.logger.debug(f"No specific voice preset found for channel {clean}, using default.")
    return preset or None


def get_tts_delay_setting(bot, channel_name):
    """Get TTS delay setting for a channel"""
    try:
        clean_channel_name = channel_name.lstrip('#')
        cfg = bot.db.get_tts_config_sync(clean_channel_name)
        enabled = bool(cfg.get("tts_delay_enabled", False))
        if enabled:
            bot.logger.debug(f"TTS delay enabled for channel {clean_channel_name}")
        else:
            bot.logger.debug(f"TTS delay disabled or not set for channel {clean_channel_name}")
        return enabled
    except Exception as e:
        bot.logger.error(f"Error in get_tts_delay_setting for {channel_name}: {e}")
        return False


def is_tts_enabled(bot, channel_name):
    """Check if TTS is enabled for a channel"""
    try:
        clean_channel = channel_name.lstrip('#')
        return bot.db.get_tts_config_sync(clean_channel).get("tts_enabled", False)
    except Exception as e:
        bot.logger.error(f"Error checking TTS status for {channel_name}: {e}")
        return False


async def generate_tts_sync(bot, text, channel_name, voice_preset, message_id, timestamp_str):
    """Generate TTS synchronously and return success status"""
    try:
        bot.logger.info(f"Starting synchronous TTS generation for {channel_name}: '{text[:30]}...'")

        # Create a result container
        result = {'success': False, 'file_path': None, 'tts_id': None}

        def tts_worker():
            """Worker function to run TTS generation in thread"""
            try:
                from bot.tts import process_text_thread

                # Generate filename
                filename_timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                clean_channel_name = channel_name.lstrip('#')
                output_dir = f"static/outputs/{clean_channel_name}"
                os.makedirs(output_dir, exist_ok=True)
                generated_full_path = f"{output_dir}/{clean_channel_name}-{filename_timestamp}.wav"

                # Call process_text_thread synchronously
                file_path, tts_id = process_text_thread(
                    input_text=text,
                    channel_name=channel_name,
                    db_file=bot.db_file,
                    full_path=generated_full_path,
                    timestamp=timestamp_str,
                    message_id=message_id,
                    voice_preset=voice_preset
                )

                if file_path and tts_id:
                    result['success'] = True
                    result['file_path'] = file_path
                    result['tts_id'] = tts_id
                    bot.logger.info(f"Synchronous TTS generation completed: {file_path}")
                else:
                    bot.logger.error(f"TTS generation failed for {channel_name}")

            except Exception as e:
                bot.logger.error(f"Error in TTS worker thread: {e}")

        # Run TTS generation in thread pool
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            # Wait for TTS generation to complete with timeout
            await loop.run_in_executor(executor, tts_worker)

        return result['success']

    except Exception as e:
        bot.logger.error(f"Error in generate_tts_sync for {channel_name}: {e}")
        return False


async def handle_speak_command(bot, ctx):
    """Handle the !speak command with improved TTS processing"""
    channel = ctx.channel.name
    author_name = ctx.author.name

    try:
        # Get the last message from this channel that wasn't a command
        with bot.db.connect_sync() as conn:
            c = conn.cursor()
            c.execute("""
                SELECT message FROM messages
                WHERE channel = ? AND NOT message LIKE '!%'
                ORDER BY timestamp DESC LIMIT 1
            """, (channel,))

            result = c.fetchone()

        if not result:
            await ctx.send("No recent messages to speak.")
            return

        message_to_speak = result[0]

        # Check channel TTS settings
        if not is_tts_enabled(bot, channel):
            await ctx.send("TTS is not enabled for this channel.")
            return

        # Only attempt TTS if it's enabled globally
        if not bot.enable_tts:
            await ctx.send("TTS is not currently enabled globally.")
            return

        # Process the TTS with proper error handling
        try:
            # Get the voice preset for the current channel
            voice_preset_for_speak = get_channel_voice_preset(bot, channel)
            if not voice_preset_for_speak:
                voice_preset_for_speak = 'v2/en_speaker_0'  # Default for !speak if none set
                bot.logger.info(f"Using default voice preset '{voice_preset_for_speak}' for !speak in {channel}.")
            else:
                bot.logger.info(f"Using voice preset '{voice_preset_for_speak}' for !speak in {channel}.")

            bot.logger.info(f"Calling process_text for !speak command. Channel: {channel}, Text: '{message_to_speak[:30]}...', Voice: {voice_preset_for_speak}")

            def _speak_blocking_wrapper():
                from bot.tts import process_text_thread
                import uuid
                msg_id = "speak_" + str(uuid.uuid4())[:8]
                out_dir = f"static/outputs/{channel}"
                os.makedirs(out_dir, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                f_path = f"{out_dir}/{channel}_{msg_id}_{ts}.wav"
                audio_file, tts_id = process_text_thread(message_to_speak, channel, full_path=f_path, message_id=msg_id, voice_preset=voice_preset_for_speak, author_name=author_name)
                return audio_file is not None, audio_file

            success, audio_file = await asyncio.to_thread(_speak_blocking_wrapper)
        except Exception as tts_error:
            bot.logger.error(f"Error calling or during TTS generation via process_text for !speak: {tts_error}", exc_info=True)
            success, audio_file = False, None

        bot.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] After process_text call. success: {success}, audio_file: '{audio_file}'")

        if success and audio_file:
            bot.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] Condition (success and audio_file) is TRUE.")
            # TTS was successful, log and notify
            # The audio_file path from process_text should be like "static/outputs/channel/file.wav"
            web_path = audio_file
            if not web_path.startswith('static/'):  # Ensure it's a web path if not already
                web_path = f"static/{web_path.lstrip('/')}"

            if hasattr(bot, 'socketio_emitter') and bot.socketio_emitter:
                try:
                    bot.socketio_emitter({
                        'event': 'new_tts_entry',
                        'channel': channel,
                        'message_id': getattr(getattr(ctx, 'message', None), 'id', 'unknown'),
                        'tts_url': web_path,
                        'voice': voice_preset_for_speak,
                        'text': message_to_speak
                    })
                except Exception as e:
                    bot.logger.error(f"Failed to emit new_tts_entry event: {e}")

            bot.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] Sending message to Twitch: Speaking: {message_to_speak[:50]}... (Audio: {web_path})")
            await ctx.send(f"Speaking: {message_to_speak[:50]}... (Audio: {web_path})")
            bot.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] Message sent to Twitch.")

            # Log the TTS usage in the database for tracking
            try:
                bot.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] Attempting to log !speak TTS to DB. audio_file from process_text: {audio_file}")
                with bot.db.connect_sync() as conn:
                    c = conn.cursor()
                    # Get the message_id of the command message itself
                    command_message_id = ctx.message.id
                    # Use the timestamp of the command message in ISO format
                    command_timestamp_str = ctx.message.timestamp.isoformat() if isinstance(ctx.message.timestamp, datetime) else str(ctx.message.timestamp)

                    # audio_file from process_text is "static/outputs/channel/file.wav"
                    # For the database, we want "outputs/channel/file.wav"
                    db_audio_file_path = None
                    if audio_file and audio_file.startswith('static/'):
                        db_audio_file_path = audio_file[len('static/'):]
                        bot.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] Derived db_audio_file_path: '{db_audio_file_path}'")
                    elif audio_file:  # If it doesn't start with static/ for some reason, log a warning but use it as is
                        bot.logger.warning(f"Audio file path from process_text does not start with 'static/': {audio_file}")
                        db_audio_file_path = audio_file  # Use as is, might be an issue later
                        bot.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] Using audio_file as is for db_audio_file_path: '{db_audio_file_path}'")
                    else:
                        bot.logger.error(f"[HANDLE_SPEAK_COMMAND_TRACE] audio_file is None or empty, cannot derive db_audio_file_path.")

                    # Get voice preset used. This should be the one passed to process_text.
                    voice_preset_used = voice_preset_for_speak  # This was determined before calling process_text

                    if db_audio_file_path:
                        bot.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] Logging !speak TTS to DB: msg_id={command_message_id}, channel={channel}, timestamp={command_timestamp_str}, path='{db_audio_file_path}', voice='{voice_preset_used}'")
                        c.execute("""
                            INSERT INTO tts_logs (message_id, channel, timestamp, file_path, voice_preset, message)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (command_message_id, channel, command_timestamp_str, db_audio_file_path, voice_preset_used, message_to_speak))
                        conn.commit()
                        bot.logger.info(f"[HANDLE_SPEAK_COMMAND_TRACE] !speak TTS logged to DB with message_id: {command_message_id}")
                    else:
                        bot.logger.error(f"[HANDLE_SPEAK_COMMAND_TRACE] Could not log !speak TTS as db_audio_file_path was not determined or was None. Original audio_file: {audio_file}")
            except IntegrityError as ie:
                bot.logger.error(f"[HANDLE_SPEAK_COMMAND_TRACE] SQLite IntegrityError logging !speak TTS (likely duplicate message_id {command_message_id}): {ie}")
            except Exception as e:
                bot.logger.error(f"Error logging TTS usage: {e}")
        else:
            # TTS failed, inform the user
            await ctx.send("Sorry, there was an error generating the TTS audio.")
    except Exception as e:
        bot.logger.error(f"Error in speak command: {e}")
        await ctx.send(f"Error: {str(e)}")
