import os
import re
import time
import json
import math
import logging
import threading
import datetime
import markovify
from datetime import datetime as dt_datetime, timezone

from bot.colors import YELLOW, RED, GREEN, PURPLE, RESET
from bot.utils import convert_size


class MarkovBrain:
    def __init__(self, db, logger, my_logger, nick: str, rebuild_cache: bool = False):
        self.db = db
        self.logger = logger        # logging.getLogger("bot")
        self.my_logger = my_logger  # Logger() instance for TUI display
        self.nick = nick
        self.rebuild_cache = rebuild_cache
        self.general_model = None
        self.models = {}
        self.text = ""
        self.cache_build_times = {}

    def compile_lore_caches(self):
        """Pre-compiles .txt files from the lore/ folder into markovify JSON caches."""
        lore_dir = "lore/"
        cache_dir = "cache/"
        if not os.path.exists(lore_dir):
            os.makedirs(lore_dir)
            return

        for filename in os.listdir(lore_dir):
            if filename.endswith(".txt"):
                txt_path = os.path.join(lore_dir, filename)
                cache_path = os.path.join(cache_dir, f"lore_{filename}_model.json")

                # Check if cache needs updating (if lore txt is newer than cache)
                needs_update = True
                if os.path.exists(cache_path):
                    txt_mtime = os.path.getmtime(txt_path)
                    cache_mtime = os.path.getmtime(cache_path)
                    if cache_mtime > txt_mtime:
                        needs_update = False

                if needs_update or getattr(self, 'rebuild_cache', False):
                    try:
                        with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
                            text = f.read()
                        if text.strip():
                            # Remove hex addresses commonly found in game text dumps like 0x1d2d9b8:
                            text = re.sub(r'0x[0-9a-fA-F]+:\s*', '', text)

                            lore_model = markovify.NewlineText(text)
                            with open(cache_path, "w") as cf:
                                cf.write(lore_model.to_json())
                            self.my_logger.print_message(f"Compiled lore model for {filename}")
                    except Exception as e:
                        self.logger.error(f"Failed to compile lore {filename}: {e}")

    def load_text_and_build_model(self, create_individual_caches=False, target_channel=None):
        self.compile_lore_caches()
        cache_directory = "cache/"
        if not os.path.exists(cache_directory):
            os.makedirs(cache_directory)
        self.text = ""  # Text for the general model
        self.models = {}  # Dictionary for channel-specific models
        total_lines = 0
        files_data = []

        self.cache_build_times = self.load_last_cache_build_times()
        line_threshold = 50

        with self.db.connect_sync() as conn:
            c = conn.cursor()

            # ONE-TIME MIGRATION: Migrating old logs/*.txt to DB if they exist
            directory = "logs/"
            if os.path.exists(directory):
                for filename in os.listdir(directory):
                    if filename.endswith(".txt"):
                        file_path = os.path.join(directory, filename)
                        channel_name = filename[:-4]
                        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                            lines = [line.strip() for line in f if line.strip()]
                        if lines:
                            self.my_logger.print_message(f"Migrating {len(lines)} legacy log entries for #{channel_name} to database...")
                            timestamp = dt_datetime.now(timezone.utc).isoformat()
                            c.executemany(
                                "INSERT INTO messages (message, timestamp, channel, author_name, is_bot_response) VALUES (?, ?, ?, ?, 0)",
                                [(line, timestamp, channel_name, "legacy_user") for line in lines]
                            )
                        os.rename(file_path, file_path + ".imported")
                conn.commit()

            # Get active channels
            c.execute("SELECT channel_name FROM channel_configs")
            valid_channels = set(row[0] for row in c.fetchall())

            # Grab all non-bot messages ordered by channel
            c.execute("SELECT channel, message, author_name FROM messages WHERE is_bot_response = 0 ORDER BY channel")
            rows = c.fetchall()

        # Group messages by channel and generate user stats
        channel_messages = {}
        user_stats = {}

        for row in rows:
            # Handle standard unpacking based on length
            if len(row) == 3:
                channel, msg, author = row
            else:
                channel, msg = row[0], row[1]
                author = None

            if author:
                author_lower = author.lower()
                if author_lower not in user_stats:
                    user_stats[author_lower] = {
                        'messages': 0,
                        'chars': 0,
                        'words': 0
                    }
                user_stats[author_lower]['messages'] += 1
                user_stats[author_lower]['chars'] += len(msg)
                user_stats[author_lower]['words'] += len(msg.split())

            if channel:
                channel_name = channel.lstrip('#')
                if channel_name not in channel_messages:
                    channel_messages[channel_name] = []
                channel_messages[channel_name].append(msg)

        # Save user stats to model cache
        try:
            with open(os.path.join(cache_directory, "user_model_stats.json"), 'w') as f:
                json.dump(user_stats, f)
        except Exception as e:
            self.logger.error(f"Failed to save user model stats to cache: {e}")

        for channel_name, msgs in channel_messages.items():
            if not msgs: continue
            file_text = "\n".join(msgs) + "\n"
            line_count = len(msgs)
            total_lines += line_count
            self.text += file_text

            cache_status = f"{RED}Unchanged{RESET}"

            should_compile_individual = create_individual_caches and channel_name in valid_channels and line_count >= line_threshold
            if target_channel and target_channel != "Global" and channel_name != target_channel.lstrip('#'):
                should_compile_individual = False

            if should_compile_individual:
                cache_file_path = os.path.join(cache_directory, f"{channel_name}_model.json")
                cache_status = self.create_channel_model(channel_name, file_text, cache_file_path)

            files_data.append([
                channel_name,
                f"{line_count:,}",
                cache_status,
                "General Model" if cache_status == f"{RED}Unchanged{RESET}" else f"{channel_name}_model.json"
            ])

        if self.text:
            self.general_model = markovify.NewlineText(self.text)

            should_compile_general = True
            if target_channel and target_channel != "Global":
                should_compile_general = False

            general_cache_file_path = os.path.join(cache_directory, "general_markov_model.json")
            last_build_time = self.cache_build_times.get("general_markov_model.json")
            general_cache_status = f"{RED}Unchanged{RESET}"

            if should_compile_general and (self.rebuild_cache or last_build_time is None):
                self.save_general_model_to_cache(general_cache_file_path)
                general_cache_status = f"{GREEN}Updated{RESET}"
                # Update build time
                self.cache_build_times["general_markov_model.json"] = time.time()
                self.save_cache_build_times()

        # Add total and general model status to table
        total_label = f"{YELLOW}Total{RESET}"
        files_data.append([total_label, f"{total_lines:,}", general_cache_status, "general_markov_model.json"])

        # Print the table outside the loop, after processing all files
        headers = ["Channel", "Brain Size", "Brain Status", "Brain"]
        # print(tabulate(files_data, headers=headers, tablefmt="pretty", numalign="right"))
        self.my_logger.print_message(f"Brain loaded: {total_lines:,} lines active.")

    def determine_cache_status(self, channel_name, file_text, create_individual_caches, cache_directory):
        """Determine cache status for a given channel"""
        cache_file_path = os.path.join(cache_directory, f"{channel_name}_model.json")
        cache_status = "Unchanged"
        cache_file_display = "general_markov_model.json"

        # Check for DB-registered channels
        with self.db.connect_sync() as conn:
            c = conn.cursor()
            c.execute("SELECT channel_name FROM channel_configs")
            valid_channels = set(row[0] for row in c.fetchall())

        if channel_name in valid_channels and create_individual_caches:
            channel_model = markovify.NewlineText(file_text)
            self.models[channel_name] = channel_model

            # Check if the cache file needs to be updated
            # Note: Checking last build time or model differences to decide
            last_build_time = self.cache_build_times.get(channel_name)
            if self.rebuild_cache or last_build_time is None:
                with open(cache_file_path, 'w') as cache_file:
                    cache_file.write(channel_model.to_json())
                cache_status = "Updated"
                # Update the build time
                self.cache_build_times[channel_name] = time.time()

            cache_file_display = f"{channel_name}_model.json"

        return cache_status, cache_file_display

    def cache_individual_model(self, channel_name, model, cache_file_path):
        model_json = model.to_json()
        with open(cache_file_path, "w") as f:
            f.write(model_json)

    def create_channel_model(self, channel_name, file_text, cache_file_path):
        """Create a model for a specific channel and save it to the cache."""
        try:
            chan_color = self.my_logger.color_manager.get_channel_color(channel_name)
            self.my_logger.print_message(f"Compiling individual brain model for [{chan_color}]#{channel_name}[/]...")
            channel_model = markovify.NewlineText(file_text)
            self.models[channel_name] = channel_model

            # Check if we should update cache
            last_build_time = self.cache_build_times.get(channel_name)
            if self.rebuild_cache or last_build_time is None:
                with open(cache_file_path, 'w') as cache_file:
                    cache_file.write(channel_model.to_json())

            # Update build time
            self.cache_build_times[channel_name] = time.time()
            self.save_cache_build_times()
            return f"[green]Updated[/]"
        except Exception as e:
            self.my_logger.print_message(f"Error creating model for {channel_name}: {e}")
            return f"[red]Error[/]"

    def save_general_model_to_cache(self, cache_file_path):
        """Save the general model to the cache."""
        try:
            with open(cache_file_path, 'w') as cache_file:
                cache_file.write(self.general_model.to_json())
            return True
        except Exception as e:
            self.my_logger.print_message(f"Error saving general model to cache: {e}")
            return False

    def load_model_from_cache(self, channel_name):
        cache_file_path = os.path.join("cache", f"{channel_name}_model.json")
        try:
            with open(cache_file_path, "r") as f:
                model_json = f.read()
                channel_model = markovify.NewlineText.from_json(model_json)

            # Check for lore configs
            enabled_lore_str = ""
            lore_bias = 15.0
            try:
                with self.db.connect_sync() as conn:
                    c = conn.cursor()
                    c.execute("SELECT enabled_lore, lore_bias FROM channel_configs WHERE channel_name = ?", (channel_name,))
                    row = c.fetchone()
                    if row:
                        if row[0]: enabled_lore_str = row[0]
                        if len(row) > 1 and row[1] is not None: lore_bias = float(row[1])
            except Exception as e:
                self.logger.error(f"Error fetching enabled_lore: {e}")

            if not enabled_lore_str:
                return channel_model

            # Combine lore
            lores = [l.strip() for l in enabled_lore_str.split(",") if l.strip()]
            models_to_combine = [channel_model]
            weights = [lore_bias]  # Configurable channel base multiplier

            for lore_file in lores:
                lore_cache_path = os.path.join("cache", f"lore_{lore_file}_model.json")
                if os.path.exists(lore_cache_path):
                    with open(lore_cache_path, "r") as f:
                        lore_json = f.read()
                        lore_model = markovify.NewlineText.from_json(lore_json)
                        models_to_combine.append(lore_model)
                        weights.append(1.0)

            if len(models_to_combine) > 1:
                return markovify.combine(models_to_combine, weights)
            return channel_model

        except FileNotFoundError:
            return None

    def generate_message(self, channel_name):
        # Connect to the SQLite database
        cache_file_used = ""  # Variable to store the name of the cache file used
        model = None

        with self.db.connect_sync() as conn:
            c = conn.cursor()
            # Check the database to see if this channel should use the general model
            c.execute(
                "SELECT use_general_model FROM channel_configs WHERE channel_name = ?",
                (channel_name,),
            )
            result = c.fetchone()

            # Determine which model to use and add debug information
            if result and result[0]:
                model = self.general_model
                cache_file_used = "general_markov_model.json"
            else:
                model = self.load_model_from_cache(channel_name)
                if model:
                    cache_file_used = f"{channel_name}_model.json"
                else:
                    # If explicitly set to NOT use general model, we must NOT fall back to it.
                    # Build an ephemeral model dynamically from the DB for this channel.
                    c.execute("SELECT message FROM messages WHERE channel = ? AND is_bot_response = 0", (channel_name,))
                    rows = c.fetchall()
                    if rows:
                        text = "\n".join(row[0] for row in rows if row[0])
                        if text.strip():
                            try:
                                model = markovify.NewlineText(text)
                                cache_file_used = f"{channel_name}_dynamic_fallback"
                                self.my_logger.log_message(
                                    channel_name,
                                    "TwitchSystem",
                                    f"[bold yellow]⚠️ Missing Brain Cache! Used slow dynamic fallback. Please run 'compile' to build the brain.[/bold yellow]",
                                    is_bot_message=True
                                )
                            except Exception as e:
                                self.logger.error(f"Failed to build dynamic model for {channel_name}: {e}")

                    # If we still have no model (e.g. channel has zero messages), return early
                    if not model:
                        self.logger.info(f"Failed to generate isolated message for {channel_name}: Not enough data.")
                        return None

        # Generate a message using the chosen model
        message = model.make_sentence()
        if message:
            # Clean up the message to ensure all characters are printable
            message = "".join(char for char in message if char.isprintable())
            # Save and return the generated message
            self.save_message(message, channel_name)
            return message
        else:
            # If no message was generated, return None and add debug information
            print(
                f"[DEBUG] Failed to generate message for channel: {channel_name} using cache file: {cache_file_used}"
            )
            return None

    def save_message(self, message, channel_name):
        with self.db.connect_sync() as conn:
            c = conn.cursor()
            c.execute(
                """INSERT INTO messages (message, timestamp, channel, state_size, message_length, author_name, is_bot_response)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    message,
                    dt_datetime.now(timezone.utc).isoformat(),  # Store timestamp as ISO string in UTC
                    channel_name,
                    self.general_model.state_size if hasattr(self.general_model, 'state_size') else None,  # Ensure general_model exists
                    len(message),
                    self.nick,  # Bot's name as author
                    1  # Mark as bot response
                ),
            )
            conn.commit()

    def update_model_periodically(self, interval=86400, initial_delay=120):
        def delayed_execution():
            try:
                if self.rebuild_cache:
                    # Rebuild cache including individual caches
                    self.load_text_and_build_model(create_individual_caches=True)
                else:
                    # Check if the general model cache is loaded
                    cache_loaded = self.load_model_from_cache("general_markov_model.json")
                    if not cache_loaded:
                        # Just rebuild the general model
                        self.load_text_and_build_model(create_individual_caches=False)
                        self.my_logger.info("Markov model updated.")
                    else:
                        self.my_logger.info("Markov model loaded from cache.")
            except Exception as e:
                self.my_logger.error(f"Error during model update: {e}")
            finally:
                # Schedule the next execution
                threading.Timer(interval, delayed_execution).start()

        # Start the first execution after the initial delay
        threading.Timer(initial_delay, delayed_execution).start()

    def load_last_cache_build_times(self):
        """Load the last build times of cache files from the database or create a default."""
        try:
            # Check if a cache_build_times file exists
            cache_time_file = os.path.join("cache", "cache_build_times.json")
            if os.path.exists(cache_time_file):
                with open(cache_time_file, 'r') as f:
                    data = json.load(f)

                    # Convert from list to dictionary if needed
                    if isinstance(data, list):
                        self.logger.info("Converting cache build times from list to dictionary format...")
                        result = {}
                        for entry in data:
                            if isinstance(entry, dict) and "channel" in entry and "timestamp" in entry:
                                # Use the channel name as the key, timestamp as the value
                                channel_key = entry["channel"]
                                if channel_key == "general_markov":
                                    channel_key = "general_markov_model.json"
                                result[channel_key] = entry["timestamp"]
                        return result
                    return data
            return {}
        except Exception as e:
            self.logger.info(f"Error loading cache build times: {e}")
            return {}

    def save_cache_build_times(self):
        """Save the current cache build times to a file."""
        try:
            # Ensure cache directory exists
            if not os.path.exists("cache"):
                os.makedirs("cache")

            cache_time_file = os.path.join("cache", "cache_build_times.json")

            # Convert from dictionary to list for backwards compatibility
            # or just save as dictionary if we've already migrated
            with open(cache_time_file, 'w') as f:
                # Check if we need to maintain the list format for backwards compatibility
                try:
                    with open(cache_time_file, 'r') as read_f:
                        old_data = json.load(read_f)
                        if isinstance(old_data, list):
                            # Convert our dictionary back to the list format
                            list_data = []
                            for key, timestamp in self.cache_build_times.items():
                                channel_name = key
                                if key == "general_markov_model.json":
                                    channel_name = "general_markov"
                                list_data.append({
                                    "channel": channel_name,
                                    "timestamp": timestamp,
                                    "success": True,
                                    "duration": 3.45  # Default duration
                                })
                            json.dump(list_data, f, indent=2)
                            self.logger.info("Saved cache build times in list format for compatibility")
                            return
                except Exception:
                    # If we can't read the old file, just use the dictionary format
                    pass

                # Save as dictionary
                json.dump(self.cache_build_times, f, indent=2)
        except Exception as e:
            self.logger.info(f"Error saving cache build times: {e}")

    async def print_brain_status(self, channel_filter=None, out_func=None):
        """Print a status table showing the number of lines loaded for each channel's Markov brain and cache metadata."""
        out = out_func or self.my_logger.print_message
        try:
            async with self.db.connect_async() as conn:
                c = await conn.cursor()

                # Get use_general_model for channels
                await c.execute('SELECT channel_name, use_general_model FROM channel_configs')
                channel_models = {row[0]: row[1] for row in await c.fetchall()}

                table_data = []

                if channel_filter:
                    clean_channel = channel_filter.lstrip('#')
                    use_general = channel_models.get(clean_channel, 1)  # Default to 1 (general) if not found
                    model_type = "General" if use_general else "Individual"

                    if use_general:
                        cache_file_path = os.path.join("cache", "general_markov_model.json")
                        model_name = "general_markov_model"
                    else:
                        cache_file_path = os.path.join("cache", f"{clean_channel}_model.json")
                    model_name = f"{clean_channel}_model"

                    await c.execute('SELECT COUNT(*) FROM messages WHERE is_bot_response = 0 AND channel = ?', (clean_channel,))
                    row = await c.fetchone()
                    msg_count = row[0] if row else 0
                    chan_color_hex = self.my_logger.color_manager.get_channel_color(clean_channel)
                    out(f"\n🧠 [bold]Detailed Brain Stats for [{chan_color_hex}]#{clean_channel}[/]:[/bold]")
                    out(f"  • Raw Messages in DB: {msg_count:,}")
                    out(f"  • Source Model:       {model_type} ({model_name})")

                    if os.path.exists(cache_file_path):
                        size_bytes = os.path.getsize(cache_file_path)
                        cache_size_str = f"{size_bytes / 1024:.1f} KB"
                        mtime = os.path.getmtime(cache_file_path)
                        dt_obj = datetime.datetime.fromtimestamp(mtime)

                        out(f"  • Cache File Size:    {cache_size_str}")
                        out(f"  • Last Compiled:      {dt_obj.strftime('%Y-%m-%d %H:%M:%S')}")

                        try:
                            with open(cache_file_path, 'r', encoding='utf-8') as f:
                                json_str = f.read()
                            model = markovify.NewlineText.from_json(json_str)

                            state_size = model.state_size
                            num_parsed_sentences = len(model.parsed_sentences) if model.parsed_sentences else 0

                            # Dictionary representation of the chain
                            chain_model = model.chain.model
                            num_states = len(chain_model) if isinstance(chain_model, dict) else "Unknown"

                            # Top start words
                            try:
                                starts = chain_model.get((markovify.chain.BEGIN,) * state_size, {})
                                top_starts = sorted(starts.items(), key=lambda x: x[1], reverse=True)[:5]
                                top_starts_str = ", ".join([f"'{w[0]}': {c}" if isinstance(w, tuple) else f"'{w}': {c}" for w, c in top_starts])
                            except Exception:
                                top_starts_str = "Unavailable"

                            out(f"  • State Size:         {state_size}")
                            out(f"  • Sentences Parsed:   {num_parsed_sentences:,}")
                            out(f"  • Unique States:      {num_states:,}" if isinstance(num_states, int) else f"  • Unique States:      {num_states}")
                            out(f"  • Top Start Words:    {top_starts_str}")

                        except Exception as e:
                            out(f"  • Error parsing cache: {str(e)}")
                    else:
                        out(f"  • Cache Status:       Not generated yet")

                    out("")
                    return

                # Get total counts per channel from DB
                await c.execute('''
                    SELECT channel, COUNT(*) as count
                    FROM messages
                    WHERE is_bot_response = 0
                    GROUP BY channel
                    ORDER BY channel
                ''')

                total_lines = 0

                # Pre-fetch general model stats
                gen_cache_size_str = "N/A"
                gen_last_compiled_str = "None"
                gen_cache_file_path = os.path.join("cache", "general_markov_model.json")
                if os.path.exists(gen_cache_file_path):
                    size_bytes = os.path.getsize(gen_cache_file_path)
                    gen_cache_size_str = f"{size_bytes / 1024:.1f} KB"
                    mtime = os.path.getmtime(gen_cache_file_path)
                    dt_obj = datetime.datetime.fromtimestamp(mtime)
                    gen_last_compiled_str = dt_obj.strftime('%Y-%m-%d %H:%M:%S')

                for row in await c.fetchall():
                    channel, count = row
                    if channel:
                        clean_channel = channel.lstrip('#')

                        use_general = channel_models.get(clean_channel, 1)  # Default to 1 (general) if not found
                        model_type = "General" if use_general else "Individual"

                        if use_general:
                            cache_size_str = gen_cache_size_str
                            last_compiled_str = gen_last_compiled_str
                        else:
                            cache_size_str = "N/A"
                            last_compiled_str = "None"
                            cache_file_path = os.path.join("cache", f"{clean_channel}_model.json")
                            if os.path.exists(cache_file_path):
                                size_bytes = os.path.getsize(cache_file_path)
                                cache_size_str = f"{size_bytes / 1024:.1f} KB"

                                mtime = os.path.getmtime(cache_file_path)
                                dt_obj = datetime.datetime.fromtimestamp(mtime)
                                last_compiled_str = dt_obj.strftime('%Y-%m-%d %H:%M:%S')

                        # Add to table
                        table_data.append([
                            f"[{self.my_logger.color_manager.get_channel_color(clean_channel)}]#{clean_channel}[/]",
                            f"{count:,}",
                            model_type,
                            cache_size_str,
                            last_compiled_str
                        ])
                        total_lines += count

                total_label = "[bold yellow]Total[/bold yellow]"
                table_data.append([total_label, f"{total_lines:,}", "", "", ""])

                from rich.table import Table
                from rich import box
                table = Table(
                    title="Brain Statistics",
                    title_style="bold cyan",
                    box=box.ROUNDED,
                    border_style="dim",
                    header_style="bold white",
                    padding=(0, 1),
                )
                headers = [
                    ("Channel", "left"),
                    ("Lines in Brain", "right"),
                    ("Model Type", "center"),
                    ("Cache Size", "right"),
                    ("Last Compiled", "center"),
                ]
                for h, j in headers:
                    table.add_column(h, justify=j)
                for row in table_data:
                    table.add_row(*row)
                out(table)

                # Check for cached general model (standalone print below the table)
                if gen_cache_size_str != "N/A":
                    self.my_logger.print_message(f"\nGeneral Model Cache Size: {gen_cache_size_str}")
                    self.my_logger.print_message(f"General Model Last Compiled: {gen_last_compiled_str}")
                else:
                    self.my_logger.print_message("\nGeneral Model Cache: Not generated yet")

        except Exception as e:
            self.my_logger.print_message(f"Error printing brain status: {e}")
