import sqlite3
import configparser
import logging

def ensure_db_setup(db_file):
    try:
        conn = sqlite3.connect(db_file)
        c = conn.cursor()

        # Create 'cache_build_log' table (moved earlier)
        c.execute('''CREATE TABLE IF NOT EXISTS cache_build_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        channel_name TEXT,
                        timestamp REAL,
                        duration REAL,
                        success BOOLEAN,
                        message TEXT
                    )''')
        conn.commit() # Explicit commit after creating this table

        # Create 'messages' table
        c.execute('''CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        twitch_message_id TEXT UNIQUE,
                        message TEXT NOT NULL,
                        author_name TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        channel TEXT NOT NULL,
                        is_bot_response BOOLEAN NOT NULL DEFAULT 0,
                        state_size INTEGER,
                        message_length INTEGER,
                        tts_processed BOOLEAN NOT NULL DEFAULT 0
                    )''')
        conn.commit() # Commit after table creation

        # Add new columns to messages table if they don't exist (for migration)
        messages_columns = [row[1] for row in c.execute("PRAGMA table_info(messages)").fetchall()]
        if 'twitch_message_id' not in messages_columns:
            # Removed UNIQUE constraint here for ALTER TABLE as SQLite cannot add it directly
            # if there's existing data that might violate it.
            # The CREATE TABLE statement handles UNIQUE for new tables.
            c.execute('ALTER TABLE messages ADD COLUMN twitch_message_id TEXT')
            logging.info("Column 'twitch_message_id' added to 'messages'.")
        if 'author_name' not in messages_columns:
            # Add with a default for existing rows, then make NOT NULL if desired,
            # but for simplicity, allow NULL initially if altering, then ensure new inserts are NOT NULL.
            # For new tables, it's NOT NULL. For altered, it's complex.
            # Let's assume new inserts will handle NOT NULL.
            c.execute('ALTER TABLE messages ADD COLUMN author_name TEXT') # Default to NULL for existing
            logging.info("Column 'author_name' added to 'messages'.")
        if 'is_bot_response' not in messages_columns:
            c.execute('ALTER TABLE messages ADD COLUMN is_bot_response BOOLEAN NOT NULL DEFAULT 0')
            logging.info("Column 'is_bot_response' added to 'messages'.")
        
        # Make state_size and message_length nullable if they aren't already
        # This is complex with ALTER TABLE in SQLite. Usually involves recreating the table.
        # For now, we'll assume new inserts will handle NULLs if appropriate.
        # And the table definition above makes them nullable by default if not specified otherwise.

        conn.commit()


        # Create 'channel_configs' table with default columns
        c.execute('''CREATE TABLE IF NOT EXISTS channel_configs (
                        channel_name TEXT PRIMARY KEY,
                        user_id INTEGER,
                        tts_enabled BOOLEAN NOT NULL,
                        voice_enabled BOOLEAN NOT NULL,
                        join_channel BOOLEAN NOT NULL,
                        owner TEXT,
                        trusted_users TEXT,
                        ignored_users TEXT,
                        use_general_model BOOLEAN NOT NULL DEFAULT 1,
                        lines_between_messages INTEGER DEFAULT 100,
                        time_between_messages INTEGER DEFAULT 0,
                        voice_preset TEXT DEFAULT 'v2/en_speaker_5',
                        bark_model TEXT DEFAULT 'regular',
                        currently_connected BOOLEAN DEFAULT 0,
                        tts_delay_enabled BOOLEAN DEFAULT 0,
                        random_chance REAL DEFAULT 0.0,
                        log_dice BOOLEAN DEFAULT 0,
                        pubsub_bits BOOLEAN DEFAULT 0,
                        pubsub_points BOOLEAN DEFAULT 0
                    )''')

        # Add random_chance column to channel_configs if it doesn't exist (migration)
        channel_configs_columns = [row[1] for row in c.execute("PRAGMA table_info(channel_configs)").fetchall()]
        if 'random_chance' not in channel_configs_columns:
            c.execute("ALTER TABLE channel_configs ADD COLUMN random_chance REAL DEFAULT 0.0")
            logging.info("Column 'random_chance' added to 'channel_configs'.")
            
        # Add log_dice column to channel_configs if it doesn't exist (migration)
        if 'log_dice' not in channel_configs_columns:
            c.execute("ALTER TABLE channel_configs ADD COLUMN log_dice BOOLEAN DEFAULT 0")
            logging.info("Column 'log_dice' added to 'channel_configs'.")
            
        # Add pubsub_bits column to channel_configs if it doesn't exist (migration)
        if 'pubsub_bits' not in channel_configs_columns:
            c.execute("ALTER TABLE channel_configs ADD COLUMN pubsub_bits BOOLEAN DEFAULT 0")
            logging.info("Column 'pubsub_bits' added to 'channel_configs'.")
            
        # Add pubsub_points column to channel_configs if it doesn't exist (migration)
        if 'pubsub_points' not in channel_configs_columns:
            c.execute("ALTER TABLE channel_configs ADD COLUMN pubsub_points BOOLEAN DEFAULT 0")
            logging.info("Column 'pubsub_points' added to 'channel_configs'.")
            
        conn.commit()

        # Create 'user_colors' table
        c.execute('''CREATE TABLE IF NOT EXISTS user_colors (
                        username TEXT PRIMARY KEY,
                        color TEXT
                    )''')

        # Create 'tts_logs' table for TTS file logging
        # message_id should be TEXT to store UUIDs
        c.execute('''CREATE TABLE IF NOT EXISTS tts_logs (
                        message_id TEXT PRIMARY KEY, 
                        channel TEXT,
                        timestamp TEXT,
                        file_path TEXT,
                        voice_preset TEXT,
                        message TEXT
                    )''')
                    
        # Create 'bot_status' table for storing bot status information
        c.execute('''CREATE TABLE IF NOT EXISTS bot_status (
                        key TEXT PRIMARY KEY,
                        value TEXT,
                        timestamp TEXT
                    )''')
        conn.commit() # Explicit commit

        # Create 'connection_history' table for WebSocket reconnection tracking
        c.execute('''CREATE TABLE IF NOT EXISTS connection_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        details TEXT,
                        attempt_number INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_connection_timestamp ON connection_history(timestamp)')
        conn.commit()

        # Create 'error_log' table for centralized error tracking
        c.execute('''CREATE TABLE IF NOT EXISTS error_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        level TEXT NOT NULL,
                        message TEXT NOT NULL,
                        source TEXT,
                        stack_trace TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_error_timestamp ON error_log(timestamp)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_error_level ON error_log(level)')
        conn.commit()
        
        # Create 'custom_commands' table for Funtoon-style dynamic commands
        c.execute('''CREATE TABLE IF NOT EXISTS custom_commands (
                        channel_name TEXT,
                        command_name TEXT,
                        response_template TEXT NOT NULL,
                        PRIMARY KEY(channel_name, command_name)
                    )''')
        conn.commit()
        
        # Create 'custom_grammar' table for Funtoon-style tracery grammar rules
        c.execute('''CREATE TABLE IF NOT EXISTS custom_grammar (
                        channel_name TEXT,
                        rule_name TEXT,
                        options_json TEXT NOT NULL,
                        PRIMARY KEY(channel_name, rule_name)
                    )''')
        conn.commit()

        # Check and migrate tts_logs table if needed
        c.execute("PRAGMA table_info(tts_logs)")
        tts_logs_columns = {row[1]: row[2] for row in c.fetchall()}  # {column_name: data_type}
        
        if 'message_id' in tts_logs_columns and tts_logs_columns['message_id'].upper() == 'INTEGER':
            logging.info("Attempting to migrate tts_logs.message_id from INTEGER to TEXT...")
            try:
                # Start a transaction
                conn.execute('BEGIN TRANSACTION')

                # Drop the temporary table if it exists from a previous failed attempt
                c.execute('DROP TABLE IF EXISTS tts_logs_new')
                logging.debug("Dropped tts_logs_new if it existed.")

                # Create new table with correct schema
                c.execute('''CREATE TABLE tts_logs_new (
                                message_id TEXT PRIMARY KEY, 
                                channel TEXT,
                                timestamp TEXT,
                                file_path TEXT,
                                voice_preset TEXT,
                                message TEXT
                            )''')
                logging.debug("Created tts_logs_new table.")

                # Copy data, converting INTEGER message_id to TEXT
                c.execute('''INSERT INTO tts_logs_new 
                            SELECT CAST(message_id AS TEXT), channel, timestamp, file_path, voice_preset, message 
                            FROM tts_logs''')
                logging.debug("Copied data from tts_logs to tts_logs_new.")

                # Drop old table and rename new one
                c.execute('DROP TABLE tts_logs')
                logging.debug("Dropped old tts_logs table.")
                c.execute('ALTER TABLE tts_logs_new RENAME TO tts_logs')
                logging.debug("Renamed tts_logs_new to tts_logs.")

                # Commit the transaction
                conn.commit()
                logging.info("tts_logs table migrated successfully.")
            except sqlite3.Error as migration_error:
                logging.error(f"Error during tts_logs migration: {migration_error}. Rolling back.")
                conn.rollback() # Rollback on error
                # It's possible the error is "table tts_logs_new already exists" if BEGIN TRANSACTION isn't fully effective for DDL.
                # The DROP TABLE IF EXISTS should handle this, but if not, manual DB intervention might be needed.
            except Exception as general_migration_exc:
                logging.error(f"A non-SQLite error occurred during tts_logs migration: {general_migration_exc}. Rolling back.")
                conn.rollback()


        # Check and add missing columns in 'channel_configs'
        c.execute("PRAGMA table_info(channel_configs)")
        channel_config_columns = [row[1] for row in c.fetchall()]
        if 'user_id' not in channel_config_columns:
            c.execute('ALTER TABLE channel_configs ADD COLUMN user_id INTEGER')
            logging.info("Column 'user_id' added to 'channel_configs'.")
        if 'lines_between_messages' not in channel_config_columns:
            c.execute('ALTER TABLE channel_configs ADD COLUMN lines_between_messages INTEGER DEFAULT 100')
            logging.info("Column 'lines_between_messages' added to 'channel_configs'.")
        if 'time_between_messages' not in channel_config_columns:
            c.execute('ALTER TABLE channel_configs ADD COLUMN time_between_messages INTEGER DEFAULT 0')
            logging.info("Column 'time_between_messages' added to 'channel_configs'.")
        if 'voice_preset' not in channel_config_columns:
            c.execute('ALTER TABLE channel_configs ADD COLUMN voice_preset TEXT DEFAULT \'v2/en_speaker_5\'')
            logging.info("Column 'voice_preset' added to 'channel_configs'.")
        if 'bark_model' not in channel_config_columns:
            c.execute('ALTER TABLE channel_configs ADD COLUMN bark_model TEXT DEFAULT \'regular\'')
            logging.info("Column 'bark_model' added to 'channel_configs'.")
        if 'currently_connected' not in channel_config_columns:
            c.execute('ALTER TABLE channel_configs ADD COLUMN currently_connected BOOLEAN DEFAULT 0')
            logging.info("Column 'currently_connected' added to 'channel_configs'.")
        if 'tts_delay_enabled' not in channel_config_columns:
            c.execute('ALTER TABLE channel_configs ADD COLUMN tts_delay_enabled BOOLEAN DEFAULT 0')
            logging.info("Column 'tts_delay_enabled' added to 'channel_configs'.")

        # PERFORMANCE OPTIMIZATION: Add indexes for frequently queried columns
        # These indexes will significantly improve query performance
        performance_indexes = [
            # Messages table indexes
            "CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel)",
            "CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_messages_author ON messages(author_name)",
            "CREATE INDEX IF NOT EXISTS idx_messages_channel_timestamp ON messages(channel, timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_messages_twitch_id ON messages(twitch_message_id)",
            
            # Channel configs indexes  
            "CREATE INDEX IF NOT EXISTS idx_channel_configs_join ON channel_configs(join_channel)",
            "CREATE INDEX IF NOT EXISTS idx_channel_configs_tts ON channel_configs(tts_enabled)",
            
            # TTS logs indexes
            "CREATE INDEX IF NOT EXISTS idx_tts_logs_channel ON tts_logs(channel)",
            "CREATE INDEX IF NOT EXISTS idx_tts_logs_timestamp ON tts_logs(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_tts_logs_channel_timestamp ON tts_logs(channel, timestamp)",
            
            # Bot status indexes
            "CREATE INDEX IF NOT EXISTS idx_bot_status_key ON bot_status(key)",
            "CREATE INDEX IF NOT EXISTS idx_bot_status_timestamp ON bot_status(timestamp)",
            
            # User colors indexes
            "CREATE INDEX IF NOT EXISTS idx_user_colors_username ON user_colors(username)"
        ]
        
        for index_sql in performance_indexes:
            try:
                c.execute(index_sql)
                logging.debug(f"Created index: {index_sql}")
            except sqlite3.Error as e:
                logging.warning(f"Failed to create index: {index_sql} - {e}")
        
        conn.commit()
        logging.info("Database setup completed successfully with performance indexes.")

        # Further code for initializing channels and handling settings...

    except sqlite3.Error as e:
        logging.error(f"Database error during setup: {e}") # Use logging.error
    finally:
        if conn: # Ensure conn exists before closing
            conn.close()
