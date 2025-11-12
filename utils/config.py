"""
Configuration management utilities for the application.
Handles persistent secret keys, environment variables, and app configuration.
"""
import os
import secrets
import logging
from pathlib import Path


class ConfigManager:
    """Manages application configuration including persistent secret keys."""

    SECRET_KEY_FILE = '.flask_secret_key'

    @staticmethod
    def get_secret_key():
        """
        Get or generate a persistent secret key for Flask sessions.

        Order of precedence:
        1. FLASK_SECRET_KEY environment variable (highest priority)
        2. Secret key stored in .flask_secret_key file
        3. Generate new key and save to file (only if file doesn't exist)

        Returns:
            str: The secret key for Flask session management
        """
        # First check environment variable
        env_secret = os.environ.get('FLASK_SECRET_KEY')
        if env_secret:
            logging.info("Using FLASK_SECRET_KEY from environment variable")
            return env_secret

        # Check for existing secret key file
        secret_file = Path(ConfigManager.SECRET_KEY_FILE)

        if secret_file.exists():
            try:
                with open(secret_file, 'r') as f:
                    key = f.read().strip()
                    if key:
                        logging.info(f"Loaded secret key from {ConfigManager.SECRET_KEY_FILE}")
                        return key
            except Exception as e:
                logging.warning(f"Failed to read secret key file: {e}")

        # Generate new secret key and save it
        new_key = secrets.token_hex(32)
        try:
            with open(secret_file, 'w') as f:
                f.write(new_key)
            # Set restrictive permissions (owner read/write only)
            os.chmod(secret_file, 0o600)
            logging.info(f"Generated new secret key and saved to {ConfigManager.SECRET_KEY_FILE}")
        except Exception as e:
            logging.error(f"Failed to save secret key to file: {e}")

        return new_key

    @staticmethod
    def get_session_dir():
        """
        Get the directory for storing Flask sessions.

        Returns:
            str: Path to the session storage directory
        """
        session_dir = os.environ.get('SESSION_DIR', 'flask_session')

        # Create directory if it doesn't exist
        Path(session_dir).mkdir(exist_ok=True)

        return session_dir

    @staticmethod
    def get_database_path():
        """
        Get the path to the main database file.

        Returns:
            str: Path to the database file
        """
        return os.environ.get('DATABASE_PATH', 'messages.db')

    @staticmethod
    def is_production():
        """
        Check if the application is running in production mode.

        Returns:
            bool: True if in production, False otherwise
        """
        return os.environ.get('FLASK_ENV') == 'production'

    @staticmethod
    def get_config_dict():
        """
        Get a dictionary of all configuration values for logging/debugging.

        Returns:
            dict: Configuration values (excluding sensitive data)
        """
        return {
            'session_dir': ConfigManager.get_session_dir(),
            'database_path': ConfigManager.get_database_path(),
            'is_production': ConfigManager.is_production(),
            'has_env_secret_key': bool(os.environ.get('FLASK_SECRET_KEY')),
            'secret_key_file_exists': Path(ConfigManager.SECRET_KEY_FILE).exists()
        }
