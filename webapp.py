import os
import sqlite3
import time
import asyncio
import logging
import re
import psutil
import json
import random
import traceback
import signal # Added import for signal
import requests
from urllib.parse import urlencode
from flask import Flask, render_template, request, jsonify, redirect, url_for, send_from_directory, make_response, session, g
from flask_socketio import SocketIO, join_room # Import SocketIO and join_room
from datetime import datetime, timedelta
import configparser
import hashlib
import secrets
from functools import wraps
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
from utils.markov_handler import MarkovHandler
from utils.logger import Logger
from utils.tts import start_tts_processing # Import for TTS generation
from utils.db_setup import ensure_db_setup
from utils.db_manager import get_db_manager, execute_query_sync, execute_update_sync
from utils.user_db import UserDatabase
from utils.stripe_service import stripe_service
from utils.auth import (
    init_auth, require_auth, require_permission, require_role, require_channel_access,
    login_user, logout_user, is_authenticated, get_current_user,
    auth_context_processor, Permissions, Roles
)
from utils.security import (
    SecurityConfig, RateLimiter, PasswordValidator, UsernameValidator,
    CSRFProtection, SessionSecurity, rate_limiter, require_rate_limit,
    require_csrf_token, secure_headers, enforce_https, session_security_middleware
)

# Initialize application components
app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

# SECURITY: Generate a secret key for session management
# In production, this should be set via environment variable
app.secret_key = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))

# Security configuration
app.config['SESSION_COOKIE_SECURE'] = not app.debug  # HTTPS only in production
app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevent XSS
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF protection
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=SecurityConfig.SESSION_TIMEOUT_MINUTES)

socketio = SocketIO(app) # Initialize SocketIO with the Flask app

# Setup logging
logger = Logger()
logger.setup_logger() # This was called logger.setup_logger() in your logger.py
app.logger.setLevel(logging.INFO) # Use app.logger for Flask's internal logging

# Set up database
db_file = "messages.db"
ensure_db_setup(db_file)

# Add custom Jinja2 filters
def strftime_filter(datetime_obj, fmt='%Y-%m-%d %H:%M'):
    """Custom strftime filter for Jinja2."""
    if datetime_obj is None:
        return ""
    if isinstance(datetime_obj, str):
        try:
            datetime_obj = datetime.fromisoformat(datetime_obj.replace('Z', '+00:00'))
        except ValueError:
            return datetime_obj
    return datetime_obj.strftime(fmt)

app.jinja_env.filters['strftime'] = strftime_filter

# Security middleware setup (temporarily disabled for debugging)
# @app.before_request
# def security_middleware():
#     """Apply security middleware to all requests"""
#     # HTTPS enforcement in production
#     if not app.debug:
#         https_redirect = enforce_https()
#         if https_redirect:
#             return https_redirect
#     
#     # Session security checks
#     session_security_middleware()
#     
#     # Rate limiting for all requests (skip for static files)
#     if not request.endpoint or not request.endpoint.startswith('static'):
#         if not rate_limiter.check_request_rate_limit():
#             app.logger.warning(f"Request rate limit exceeded for IP: {rate_limiter._get_client_ip()}")
#             abort(429)

@app.after_request
def apply_security_headers(response):
    """Apply security headers to all responses"""
    return secure_headers(response)

# Add CSRF token to template context
@app.context_processor
def inject_csrf_token():
    """Make CSRF token available in all templates"""
    return {'csrf_token': CSRFProtection.generate_csrf_token()}

# Initialize user database and authentication system
user_db = UserDatabase('users.db')  # Users are stored in users.db, not messages.db
init_auth(user_db)

# Initialize Markov handler
markov_handler = MarkovHandler(cache_directory="cache")

# Global variable to hold the bot instance (can be set by ansv.py if needed, though direct coupling is not ideal)
bot_instance = None

# Initialize configuration
config = configparser.ConfigParser()
config.read("settings.conf")

# Cache variable for join status
channel_join_status = {}
last_status_check = 0

# Security: Input validation functions
def validate_channel_config_fields(fields):
    """Validate channel configuration field values to prevent injection attacks."""
    for field, value in fields.items():
        # Boolean fields
        if field in ['tts_enabled', 'voice_enabled', 'join_channel', 'use_general_model', 'tts_delay_enabled']:
            if not isinstance(value, bool):
                return f"Field '{field}' must be a boolean (true/false)"
                
        # Integer fields
        elif field in ['lines_between_messages', 'time_between_messages']:
            if not isinstance(value, int) or value < 0:
                return f"Field '{field}' must be a non-negative integer"
            if field == 'lines_between_messages' and value > 10000:
                return f"Field '{field}' cannot exceed 10000"
            if field == 'time_between_messages' and value > 3600:
                return f"Field '{field}' cannot exceed 3600 seconds (1 hour)"
                
        # Text fields with length limits
        elif field in ['owner', 'trusted_users', 'ignored_users']:
            if value is not None:
                if not isinstance(value, str):
                    return f"Field '{field}' must be a string"
                if len(value) > 1000:
                    return f"Field '{field}' cannot exceed 1000 characters"
                # Basic sanitization - remove potentially dangerous characters
                if re.search(r'[<>"\'\\\\\\x00-\\x1f]', value):
                    return f"Field '{field}' contains invalid characters"
                    
        # Voice preset validation
        elif field == 'voice_preset':
            if value is not None:
                if not isinstance(value, str):
                    return f"Field '{field}' must be a string"
                if len(value) > 100:
                    return f"Field '{field}' cannot exceed 100 characters"
                # Allow alphanumeric, slash, underscore, dash
                if not re.match(r'^[a-zA-Z0-9/_-]+$', value):
                    return f"Field '{field}' contains invalid characters"
                    
        # Bark model validation
        elif field == 'bark_model':
            if value is not None:
                if not isinstance(value, str):
                    return f"Field '{field}' must be a string"
                allowed_models = ['regular', 'small', 'large']
                if value not in allowed_models:
                    return f"Field '{field}' must be one of: {', '.join(allowed_models)}"
    
    return None  # All validations passed

# Old authentication functions removed - now using utils/auth.py

# Helper function to redirect streamers to their channel
def redirect_streamers_to_channel():
    """Redirect streamers to their managed channel page (1 account = 1 channel)"""
    user = get_current_user()
    if user and user.get('role_name') == 'streamer':
        # Get their managed channel (set during OAuth signup)
        managed_channel = user.get('managed_channel')
        if managed_channel:
            # Redirect to their single managed channel
            return redirect(f'/beta/channel/{managed_channel}')
    return None

# Helper functions for is_bot_actually_running
def _check_pid_file(verbose_logging):
    """Checks if the bot is running based on the PID file."""
    try:
        if os.path.exists("bot.pid"):
            with open("bot.pid", "r") as f:
                pid_str = f.read().strip()
                if pid_str:  # Ensure pid_str is not empty
                    pid = int(pid_str)
                    if psutil.pid_exists(pid):
                        process = psutil.Process(pid)
                        # Check if the process name or command line indicates it's the bot
                        if "python" in process.name().lower() or "ansv.py" in " ".join(process.cmdline()).lower():
                            if verbose_logging:
                                app.logger.info(f"Bot process (PID {pid}) verified via PID file.")
                            return True
                        else:
                            if verbose_logging:
                                app.logger.warning(f"PID {pid} exists but is not the bot process (Name: {process.name()}, Cmdline: {' '.join(process.cmdline())}).")
                    else:
                        if verbose_logging:
                            app.logger.warning(f"PID {pid} from bot.pid does not exist.")
                else:
                    if verbose_logging:
                        app.logger.warning("bot.pid file is empty.")
    except (ValueError, FileNotFoundError, psutil.NoSuchProcess, psutil.AccessDenied) as e:
        app.logger.error(f"Error checking PID file: {e}")
    return False

def _check_heartbeat_file(current_time, verbose_logging, status_cache):
    """Checks the bot_heartbeat.json file. Updates status_cache if valid."""
    try:
        if os.path.exists("bot_heartbeat.json"):
            with open("bot_heartbeat.json", "r") as f:
                heartbeat_data = json.load(f)
                timestamp = heartbeat_data.get("timestamp", 0)
                if current_time - timestamp < 120:  # Heartbeat within last 2 minutes
                    if verbose_logging:
                        app.logger.info(f"Bot verified via recent heartbeat file (last beat: {datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')}).")
                    status_cache.update(heartbeat_data) # Update cache with heartbeat data
                    status_cache['running'] = True # Explicitly set running from heartbeat
                    return True
                else:
                    if verbose_logging:
                        app.logger.warning(f"Heartbeat file is stale (last beat: {datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')}).")
    except (FileNotFoundError, json.JSONDecodeError, Exception) as e:
        app.logger.error(f"Error checking heartbeat file: {e}")
    return False

def _check_database_heartbeat(verbose_logging):
    """Checks the database for the last heartbeat timestamp."""
    try:
        conn = sqlite3.connect(db_file)
        c = conn.cursor()
        c.execute("SELECT value FROM bot_status WHERE key = 'last_heartbeat'")
        result = c.fetchone()
        conn.close()
        if result:
            last_heartbeat_dt = datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S')
            if (datetime.now() - last_heartbeat_dt).total_seconds() < 120:  # DB heartbeat within last 2 minutes
                if verbose_logging:
                    app.logger.info(f"Bot verified via recent database heartbeat (last beat: {last_heartbeat_dt}).")
                return True
            else:
                if verbose_logging:
                    app.logger.warning(f"Database heartbeat is stale (last beat: {last_heartbeat_dt}).")
    except (sqlite3.Error, ValueError, Exception) as e:
        app.logger.error(f"Error checking database heartbeat: {e}")
    return False

# Global to store TTS status from ansv.py
_enable_tts_webapp = False

@app.context_processor
def inject_theme():
    """Injects theme information into the template context."""
    theme = request.cookies.get('theme', 'darkly') # Default to 'darkly'
    return dict(theme=theme)

@app.context_processor  
def inject_auth():
    """Inject authentication context into templates."""
    return auth_context_processor()

def set_enable_tts(status: bool):
    """Allows ansv.py to set the TTS status for the webapp."""
    global _enable_tts_webapp
    _enable_tts_webapp = status
    app.logger.info(f"Webapp TTS status set by ansv.py to: {_enable_tts_webapp}")


def get_today_message_count():
    """Get count of messages sent today"""
    try:
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM messages
            WHERE DATE(timestamp) = ?
        """, (today,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else 0
    except Exception as e:
        app.logger.error(f"Error getting today's message count: {e}")
        return 0

def get_all_channel_configs():
    """Get all channel configurations from database"""
    try:
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM channel_configs ORDER BY channel_name")
        channels = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return channels
    except Exception as e:
        app.logger.error(f"Error getting channel configs: {e}")
        return []

def get_last_10_tts_files_with_last_id(db_file_path): # Renamed db_file to db_file_path for clarity
    try:
        conn = sqlite3.connect(db_file_path)
        c = conn.cursor()
        # Assuming 'message_id' is the primary key for tts_logs
        c.execute("SELECT message_id, file_path, message, timestamp, channel FROM tts_logs ORDER BY message_id DESC LIMIT 10")
        rows = c.fetchall()
        conn.close()
        
        last_id_val = rows[0][0] if rows else 0 # Corrected variable name
        
        files_data = [] # Corrected variable name
        for row in rows:
            files_data.append({
                "id": row[0],       # This is message_id
                "file": row[1],     # This is file_path
                "message": row[2],
                "timestamp": row[3],
                "channel": row[4] if len(row) > 4 else None   # This is channel
            })
        
        return files_data, last_id_val
    except Exception as e:
        app.logger.error(f"Error getting TTS files: {e}")
        return [], 0

def is_bot_actually_running():
    """Check if the bot is actually running using multiple methods."""
    global last_status_check, channel_join_status
    current_time = time.time()

    try:
        verbose_logging = config.getboolean('settings', 'verbose_heartbeat_log')
    except (configparser.NoSectionError, configparser.NoOptionError):
        verbose_logging = False

    # Use cached status if checked recently
    if current_time - last_status_check < 5: # Cache duration of 5 seconds
        if verbose_logging:
            app.logger.debug(f"Using cached bot status: {channel_join_status.get('running', False)}")
        return bool(channel_join_status.get('running', False))

    last_status_check = current_time
    bot_running_status = False

    # Method 1: Check PID file
    if _check_pid_file(verbose_logging):
        bot_running_status = True
    
    # Method 2: Check heartbeat file (if not confirmed by PID)
    # This method also updates channel_join_status if the heartbeat is valid.
    if not bot_running_status:
        if _check_heartbeat_file(current_time, verbose_logging, channel_join_status):
            bot_running_status = True 
            # channel_join_status['running'] is set by _check_heartbeat_file if it returns True

    # Method 3: Check database heartbeat (if still not confirmed)
    if not bot_running_status:
        if _check_database_heartbeat(verbose_logging):
            bot_running_status = True

    # Update the global cache
    channel_join_status['running'] = bot_running_status
    
    if verbose_logging:
        app.logger.debug(f"Final bot running status after checks: {bot_running_status}")
        
    return bot_running_status

def send_message_via_pid(channel, message):
    """Fallback method to send messages via request file"""
    try:
        bot_is_verified_running = is_bot_actually_running() # Use our robust check
        
        if not bot_is_verified_running:
            app.logger.warning(f"Attempting to send message to {channel} via PID, but bot does not appear to be running.")
            # Still proceed to create request file, bot might pick it up if it starts.

        request_file_path = 'bot_message_request.json'
        if os.path.exists(request_file_path):
            try:
                os.remove(request_file_path) # Clean up old request
                app.logger.info("Removed existing message request file.")
            except OSError as e:
                app.logger.warning(f"Could not remove existing request file {request_file_path}: {e}")
        
        clean_channel = channel.lstrip('#')
        request_id = f"{int(time.time())}_{random.randint(1000, 9999)}"
        
        data_to_write = {
            'action': 'send_message',
            'channel': clean_channel,
            'message': message,
            'timestamp': datetime.now().isoformat(), # Use ISO format
            'request_id': request_id,
            'force': True # Indicate this is a forced send attempt
        }
        
        with open(request_file_path, 'w') as f:
            json.dump(data_to_write, f)
            
        app.logger.info(f"Created message request file for channel {clean_channel}: {message[:30]}...")
        
        # Give bot a moment to process
        time.sleep(0.5) 
        
        if not os.path.exists(request_file_path):
            app.logger.info(f"Message request file for {request_id} processed by bot.")
            return True
        else:
            app.logger.warning(f"Message request file {request_id} still exists. Bot might not be processing requests.")
            # Consider this a success for the webapp's perspective of trying to send
            return True 
            
    except Exception as e:
        app.logger.error(f"Failed to create/process message request file: {e}")
        app.logger.error(traceback.format_exc())
        return False

@app.route('/send_markov_message/<channel_name>', methods=['POST'])
@require_channel_access('channel_name', 'edit')
def send_markov_message(channel_name):
    global bot_instance # bot_instance might not be set if webapp runs standalone
    
    try:
        data = request.get_json(silent=True) or {}
        # client_verified = data.get('verify_running', False) # Less reliable
        force_send_params = data.get('force_send', False) or \
                            data.get('bypass_check', False) or \
                            data.get('manual_trigger', False) or \
                            data.get('skip_verification', False) or \
                            request.args.get('force', 'false').lower() == 'true'

        if not re.match(r"^[a-zA-Z0-9_]{1,25}$", channel_name):
            return jsonify({'success': False, 'error': 'Invalid channel name format'}), 400
            
        server_verified_running = is_bot_actually_running()
        
        if force_send_params:
            app.logger.info(f"Force send parameters detected for {channel_name}. Assuming bot is available.")
            # If forcing, we act as if the bot is running for the purpose of attempting to send.
            # The actual send might still fail if the bot is truly down.
            server_verified_running = True

        # Check if a custom message was provided (from "Send to Chat" button)
        custom_message = data.get('custom_message')
        if custom_message:
            generated_message = custom_message
            app.logger.info(f"Using custom message for {channel_name}: {generated_message[:50]}...")
        else:
            # Generate a new message
            generated_message = markov_handler.generate_message(channel_name, max_attempts=8, max_fallbacks=2)
        
        if not generated_message:
            app.logger.error(f"Failed to generate Markov message for {channel_name}.")
            return jsonify({'success': False, 'error': 'Failed to generate message', 'message': "Could not generate a message."}), 500
        
        app.logger.info(f"Generated Markov message for {channel_name}: {generated_message[:50]}...")
        
        sent_successfully = False
        send_error_reason = None
        
        if server_verified_running: # Attempt to send if bot is (or assumed to be) running
            try:
                if bot_instance and hasattr(bot_instance, 'send_message_to_channel') and hasattr(bot_instance, 'loop'):
                    # Ensure channel name has # prefix for bot's send_message_to_channel
                    target_channel_for_bot = f"#{channel_name.lstrip('#')}"
                    coro = bot_instance.send_message_to_channel(target_channel_for_bot, generated_message)
                    future = asyncio.run_coroutine_threadsafe(coro, bot_instance.loop)
                    future.result(timeout=5) # Wait for the send to complete or timeout
                    sent_successfully = True
                    app.logger.info(f"Message sent to {channel_name} via direct bot instance call.")
                else:
                    app.logger.info("Bot instance not available or not fully initialized for direct send, trying PID method.")
                    sent_successfully = send_message_via_pid(channel_name, generated_message)
                    if sent_successfully:
                         app.logger.info(f"Message request for {channel_name} created via PID method.")
                    else:
                        send_error_reason = "PID-based send method failed."
                        app.logger.warning(f"PID-based send method failed for {channel_name}.")

            except Exception as send_exc:
                app.logger.error(f"Error sending message to {channel_name}: {send_exc}")
                send_error_reason = str(send_exc)
        else:
            send_error_reason = "Bot not running or not verified."
            app.logger.info(f"Message for {channel_name} not sent: Bot not running/verified (force_send_params={force_send_params}).")
            
        return jsonify({
            'success': True, # Message generation was successful
            'message': generated_message,
            'sent': sent_successfully,
            'server_verified': is_bot_actually_running(), # Actual current status
            'force_applied': force_send_params,
            'error': send_error_reason if not sent_successfully else None
        })
            
    except Exception as e:
        app.logger.error(f"General error in /send_markov_message/{channel_name}: {e}")
        app.logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e), 'message': "Server error during message generation/send."}), 500

# Authentication routes
@app.route('/login', methods=['GET', 'POST'])
# @require_rate_limit  # Temporarily disabled
# @require_csrf_token  # Temporarily disabled  
def login():
    """Login page and authentication handler with multi-user support."""
    # If already authenticated, redirect to beta dashboard
    if is_authenticated():
        return redirect('/beta')
    
    if request.method == 'POST':
        # Check rate limiting for login attempts
        client_ip = rate_limiter._get_client_ip()
        if not rate_limiter.check_login_rate_limit(client_ip):
            app.logger.warning(f"Login rate limit exceeded for IP: {client_ip}")
            return render_template('login.html', error='Too many login attempts. Please try again later.'), 429
        
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        remember_me = request.form.get('remember_me') == 'on'
        
        # Record login attempt for rate limiting
        rate_limiter.record_login_attempt(client_ip)
        
        # Enhanced input validation
        if not username and not password:
            # Fallback for old single-password mode (for migration compatibility)
            password = request.form.get('password', '').strip()
            if password:
                username = 'admin'  # Default admin username
        
        if not username:
            app.logger.warning(f"Empty username login attempt from {client_ip}")
            return render_template('login.html', error='Username is required', csrf_token=CSRFProtection.generate_csrf_token())
            
        if not password:
            app.logger.warning(f"Empty password login attempt from {client_ip}")
            return render_template('login.html', error='Password is required', csrf_token=CSRFProtection.generate_csrf_token())
        
        # Enhanced security validation
        if len(username) > SecurityConfig.MAX_USERNAME_LENGTH or len(password) > 1000:
            app.logger.warning(f"Oversized login attempt from {client_ip}")
            return render_template('login.html', error='Invalid credentials', csrf_token=CSRFProtection.generate_csrf_token())
        
        # Validate username format (basic security check)
        if not SecurityConfig.ALLOWED_USERNAME_CHARS.match(username):
            app.logger.warning(f"Invalid username format login attempt from {client_ip}: {username}")
            return render_template('login.html', error='Invalid credentials', csrf_token=CSRFProtection.generate_csrf_token())
        
        # Authenticate using new user system
        success, error_message, user_data = login_user(
            username, 
            password, 
            request.remote_addr, 
            request.headers.get('User-Agent', 'Unknown'),
            remember_me
        )
        
        if success:
            # Don't regenerate session ID since security middleware is disabled
            # SessionSecurity.regenerate_session_id()
            
            app.logger.info(f"Successful login for user {user_data['username']} from {client_ip}")
            
            # Handle special redirect logic for streamers
            if user_data and user_data.get('role_name') == 'streamer':
                # Update streamer permissions
                user_db.update_streamer_permissions()
                
                # Get channels assigned to this streamer
                user_channels = user_db.get_user_channels_from_db(user_data['id'])
                
                if user_channels:
                    # Redirect to their first assigned channel
                    channel_name = user_channels[0]
                    app.logger.info(f"Redirecting streamer {user_data['username']} to their channel: {channel_name}")
                    return redirect(f'/beta/channel/{channel_name}')
                else:
                    app.logger.error(f"No channels assigned to streamer {user_data['username']}")
                    return render_template('login.html', error='No channel assigned. Please contact admin.', csrf_token=CSRFProtection.generate_csrf_token())
            
            # For non-streamers: redirect to the page they were trying to access, or beta dashboard
            next_page = request.args.get('next', '/beta')
            
            # Enhanced security: Validate redirect URL to prevent open redirects
            if next_page and (not next_page.startswith('/') or '..' in next_page):
                next_page = '/beta'
            
            return redirect(next_page)
        else:
            # Failed login - log security event
            app.logger.warning(f"Failed login attempt for user {username} from {client_ip}")
            return render_template('login.html', error=error_message or 'Invalid credentials', csrf_token=CSRFProtection.generate_csrf_token())
    
    # GET request - show login form with CSRF token
    return render_template('login.html', csrf_token=CSRFProtection.generate_csrf_token())

@app.route('/auth/twitch')
def auth_twitch():
    """Initiate Twitch OAuth flow."""
    # Try to get from config first, fallback to environment variables
    client_id = config.get('oauth', 'twitch_client_id', fallback=None) or os.getenv('TWITCH_CLIENT_ID')
    redirect_uri = config.get('oauth', 'twitch_redirect_uri', fallback=None) or os.getenv('TWITCH_REDIRECT_URI', 'http://localhost:5001/auth/twitch/callback')

    if not client_id or client_id == 'your_oauth_client_id_here':
        app.logger.error("Twitch OAuth not configured")
        error_msg = ('Twitch OAuth not configured. '
                    'Please set twitch_client_id and twitch_client_secret in settings.conf [oauth] section. '
                    'Create an OAuth app at https://dev.twitch.tv/console')
        return render_template('login.html', error=error_msg), 500

    # Generate random state for CSRF protection
    state = secrets.token_urlsafe(32)
    session['oauth_state'] = state

    # Build Twitch OAuth authorization URL
    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': 'user:read:email',  # Request email permission
        'state': state
    }

    auth_url = f"https://id.twitch.tv/oauth2/authorize?{urlencode(params)}"
    return redirect(auth_url)

@app.route('/auth/twitch/callback')
def auth_twitch_callback():
    """Handle Twitch OAuth callback."""
    # Verify state to prevent CSRF
    state = request.args.get('state')
    if not state or state != session.get('oauth_state'):
        app.logger.warning("OAuth state mismatch - possible CSRF attack")
        return render_template('login.html', error='Authentication failed. Please try again.'), 400

    # Clear state from session
    session.pop('oauth_state', None)

    # Get authorization code
    code = request.args.get('code')
    if not code:
        error = request.args.get('error', 'Unknown error')
        app.logger.warning(f"OAuth authorization failed: {error}")
        return render_template('login.html', error='Twitch authorization denied.'), 400

    # Exchange code for access token
    client_id = config.get('oauth', 'twitch_client_id', fallback=None) or os.getenv('TWITCH_CLIENT_ID')
    client_secret = config.get('oauth', 'twitch_client_secret', fallback=None) or os.getenv('TWITCH_CLIENT_SECRET')
    redirect_uri = config.get('oauth', 'twitch_redirect_uri', fallback=None) or os.getenv('TWITCH_REDIRECT_URI', 'http://localhost:5001/auth/twitch/callback')

    if not client_id or not client_secret or client_id == 'your_oauth_client_id_here':
        app.logger.error("Twitch OAuth credentials not configured")
        error_msg = ('Twitch OAuth not configured. '
                    'Please set twitch_client_id and twitch_client_secret in settings.conf [oauth] section.')
        return render_template('login.html', error=error_msg), 500

    token_url = 'https://id.twitch.tv/oauth2/token'
    token_data = {
        'client_id': client_id,
        'client_secret': client_secret,
        'code': code,
        'grant_type': 'authorization_code',
        'redirect_uri': redirect_uri
    }

    try:
        # Request access token
        token_response = requests.post(token_url, data=token_data, timeout=10)
        token_response.raise_for_status()
        token_json = token_response.json()
        access_token = token_json.get('access_token')

        if not access_token:
            app.logger.error("No access token in Twitch response")
            return render_template('login.html', error='Authentication failed.'), 500

        # Fetch user information from Twitch
        user_url = 'https://api.twitch.tv/helix/users'
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Client-Id': client_id
        }

        user_response = requests.get(user_url, headers=headers, timeout=10)
        user_response.raise_for_status()
        user_json = user_response.json()

        if not user_json.get('data'):
            app.logger.error("No user data in Twitch response")
            return render_template('login.html', error='Failed to fetch user data.'), 500

        twitch_user = user_json['data'][0]
        twitch_user_id = twitch_user['id']
        twitch_username = twitch_user['login']
        twitch_email = twitch_user.get('email', '')
        avatar_url = twitch_user.get('profile_image_url', '')

        app.logger.info(f"OAuth successful for Twitch user: {twitch_username} (ID: {twitch_user_id})")

        # Check if user already exists by Twitch ID
        conn = user_db.get_connection()
        existing_user = conn.execute(
            "SELECT * FROM users WHERE twitch_user_id = ?",
            (twitch_user_id,)
        ).fetchone()

        if existing_user:
            # User exists - log them in
            user_dict = dict(existing_user)

            # Update OAuth data if changed
            conn.execute("""
                UPDATE users
                SET twitch_username = ?, avatar_url = ?, last_login = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (twitch_username, avatar_url, user_dict['id']))
            conn.commit()
            conn.close()

            # Create proper session in database (like login_user does)
            session_id = user_db.create_session(
                user_dict['id'],
                request.remote_addr,
                request.headers.get('User-Agent', 'Unknown'),
                24  # 24 hours session duration
            )

            # Store in Flask session
            session.permanent = False
            session['session_id'] = session_id
            session['user_id'] = user_dict['id']
            session['username'] = user_dict['username']
            session['role'] = user_dict.get('role_name', 'streamer')

            app.logger.info(f"Existing user logged in via OAuth: {user_dict['username']}")

            # Redirect to onboarding if not completed, otherwise dashboard
            if not user_dict.get('onboarding_completed'):
                return redirect('/onboarding')
            return redirect('/beta')

        else:
            # New user - create account
            # Get streamer role ID
            streamer_role = conn.execute(
                "SELECT id FROM roles WHERE name = 'streamer'"
            ).fetchone()

            if not streamer_role:
                app.logger.error("Streamer role not found in database")
                conn.close()
                return render_template('login.html', error='System error. Please contact administrator.'), 500

            # Generate random password (won't be used for OAuth users)
            random_password = secrets.token_urlsafe(32)
            password_hash = user_db.hash_password(random_password)

            # Create new user
            try:
                conn.execute("""
                    INSERT INTO users (
                        username, email, password_hash, role_id,
                        twitch_user_id, twitch_username, avatar_url, managed_channel,
                        subscription_tier, subscription_status, onboarding_completed
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    twitch_username,  # Use Twitch username
                    twitch_email,
                    password_hash,
                    streamer_role['id'],
                    twitch_user_id,
                    twitch_username,
                    avatar_url,
                    twitch_username,  # Set managed_channel to their username
                    'free',
                    'inactive',
                    0  # Not onboarded yet
                ))
                conn.commit()

                # Get the newly created user
                new_user = conn.execute(
                    "SELECT * FROM users WHERE twitch_user_id = ?",
                    (twitch_user_id,)
                ).fetchone()
                conn.close()

                if new_user:
                    user_dict = dict(new_user)

                    # Create proper session in database (like login_user does)
                    session_id = user_db.create_session(
                        user_dict['id'],
                        request.remote_addr,
                        request.headers.get('User-Agent', 'Unknown'),
                        24  # 24 hours session duration
                    )

                    # Store in Flask session
                    session.permanent = False
                    session['session_id'] = session_id
                    session['user_id'] = user_dict['id']
                    session['username'] = user_dict['username']
                    session['role'] = user_dict.get('role_name', 'streamer')

                    app.logger.info(f"New user created via OAuth: {twitch_username}")

                    # Redirect new users to onboarding
                    return redirect('/onboarding')
                else:
                    app.logger.error("Failed to retrieve newly created user")
                    return render_template('login.html', error='Account creation failed.'), 500

            except Exception as e:
                conn.rollback()
                conn.close()
                app.logger.error(f"Error creating user from OAuth: {e}")
                return render_template('login.html', error='Failed to create account.'), 500

    except requests.exceptions.RequestException as e:
        app.logger.error(f"OAuth request failed: {e}")
        return render_template('login.html', error='Failed to communicate with Twitch.'), 500
    except Exception as e:
        app.logger.error(f"OAuth error: {e}")
        return render_template('login.html', error='Authentication failed.'), 500

@app.route('/logout')
def logout():
    """Logout and clear session."""
    logout_user(request.remote_addr, request.headers.get('User-Agent', 'Unknown'))
    return redirect(url_for('login'))

# Stripe Billing & Premium Routes
@app.route('/premium')
@require_auth
def premium_page():
    """Premium subscription and billing page."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    subscription_status = user_db.get_subscription_status(user['user_id']) if user else None
    has_premium = user_db.has_tts_access(user['user_id']) if user else False

    return render_template('beta/premium.html',
                         user=user,
                         subscription_status=subscription_status,
                         has_premium=has_premium,
                         stripe_publishable_key=stripe_service.publishable_key)

@app.route('/checkout/create', methods=['POST'])
@require_auth
def create_checkout():
    """Create Stripe checkout session for Premium subscription."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    # Check if already has premium
    if user_db.has_tts_access(user['user_id']):
        return jsonify({'success': False, 'error': 'Already subscribed to Premium'}), 400

    try:
        # Get user email
        user_email = user.get('email') or f"{user['username']}@ansv.bot"

        # Create checkout session
        success_url = url_for('checkout_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}'
        cancel_url = url_for('checkout_cancel', _external=True)

        result = stripe_service.create_checkout_session(
            user_id=user['user_id'],
            user_email=user_email,
            success_url=success_url,
            cancel_url=cancel_url
        )

        if result and result.get('success'):
            return jsonify({
                'success': True,
                'session_id': result['session_id'],
                'url': result['url']
            })
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', 'Failed to create checkout session')
            }), 500

    except Exception as e:
        app.logger.error(f"Error creating checkout: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/checkout/success')
@require_auth
def checkout_success():
    """Handle successful checkout redirect."""
    session_id = request.args.get('session_id')

    return render_template('checkout_success.html',
                         session_id=session_id,
                         message='Payment successful! Your Premium subscription is being activated.')

@app.route('/checkout/cancel')
@require_auth
def checkout_cancel():
    """Handle cancelled checkout redirect."""
    return render_template('checkout_cancel.html',
                         message='Checkout cancelled. You can try again anytime.')

@app.route('/billing/portal', methods=['POST'])
@require_auth
def billing_portal():
    """Redirect to Stripe customer portal for subscription management."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    # Get subscription status to find customer ID
    subscription_status = user_db.get_subscription_status(user['user_id'])

    if not subscription_status or not subscription_status.get('stripe_customer_id'):
        return jsonify({'success': False, 'error': 'No active subscription found'}), 400

    try:
        return_url = url_for('premium_page', _external=True)

        result = stripe_service.create_customer_portal_session(
            customer_id=subscription_status['stripe_customer_id'],
            return_url=return_url
        )

        if result and result.get('success'):
            return jsonify({
                'success': True,
                'url': result['url']
            })
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', 'Failed to create portal session')
            }), 500

    except Exception as e:
        app.logger.error(f"Error creating portal session: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/webhook/stripe', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events."""
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')

    if not sig_header:
        app.logger.error("No Stripe signature in webhook request")
        return jsonify({'error': 'No signature'}), 400

    # Verify and construct event
    event = stripe_service.construct_webhook_event(payload, sig_header)

    if not event:
        return jsonify({'error': 'Invalid signature'}), 400

    event_type = event['type']
    app.logger.info(f"Received Stripe webhook: {event_type}")

    try:
        if event_type == 'checkout.session.completed':
            # Payment successful, activate subscription
            session = event['data']['object']
            user_id = int(session.get('client_reference_id') or session['metadata'].get('user_id'))
            customer_id = session['customer']
            subscription_id = session.get('subscription')

            app.logger.info(f"Checkout completed for user {user_id}, subscription {subscription_id}")

            # Fetch full subscription details from Stripe
            try:
                import stripe
                subscription = stripe.Subscription.retrieve(subscription_id)
                current_period_start = subscription.current_period_start
                current_period_end = subscription.current_period_end
                cancel_at_period_end = subscription.cancel_at_period_end
            except Exception as e:
                app.logger.error(f"Error fetching subscription details: {e}")
                current_period_start = None
                current_period_end = None
                cancel_at_period_end = False

            # Update user subscription status
            user_db.update_subscription(
                user_id=user_id,
                tier='premium',
                status='active',
                stripe_customer_id=customer_id,
                stripe_subscription_id=subscription_id,
                current_period_start=current_period_start,
                current_period_end=current_period_end,
                cancel_at_period_end=cancel_at_period_end
            )

        elif event_type == 'invoice.payment_succeeded':
            # Recurring payment successful
            invoice = event['data']['object']
            subscription_id = invoice['subscription']
            customer_id = invoice['customer']

            # Find user by subscription ID
            conn = user_db.get_connection()
            c = conn.cursor()
            c.execute("SELECT id FROM users WHERE stripe_subscription_id = ?", (subscription_id,))
            user_row = c.fetchone()
            conn.close()

            if user_row:
                user_id = user_row[0]
                app.logger.info(f"Payment succeeded for user {user_id}, subscription {subscription_id}")

                # Fetch subscription details from Stripe
                try:
                    import stripe
                    subscription = stripe.Subscription.retrieve(subscription_id)
                    current_period_start = subscription.current_period_start
                    current_period_end = subscription.current_period_end
                    cancel_at_period_end = subscription.cancel_at_period_end
                except Exception as e:
                    app.logger.error(f"Error fetching subscription details: {e}")
                    current_period_start = None
                    current_period_end = None
                    cancel_at_period_end = False

                # Ensure subscription is active
                user_db.update_subscription(
                    user_id=user_id,
                    tier='premium',
                    status='active',
                    stripe_customer_id=customer_id,
                    stripe_subscription_id=subscription_id,
                    current_period_start=current_period_start,
                    current_period_end=current_period_end,
                    cancel_at_period_end=cancel_at_period_end
                )

        elif event_type == 'invoice.payment_failed':
            # Payment failed
            invoice = event['data']['object']
            subscription_id = invoice['subscription']

            # Find user by subscription ID
            conn = user_db.get_connection()
            c = conn.cursor()
            c.execute("SELECT id FROM users WHERE stripe_subscription_id = ?", (subscription_id,))
            user_row = c.fetchone()
            conn.close()

            if user_row:
                user_id = user_row[0]
                app.logger.warning(f"Payment failed for user {user_id}, subscription {subscription_id}")

                # Mark as past_due
                user_db.update_subscription(
                    user_id=user_id,
                    tier='premium',
                    status='past_due',
                    stripe_subscription_id=subscription_id
                )

        elif event_type == 'customer.subscription.deleted':
            # Subscription cancelled
            subscription = event['data']['object']
            subscription_id = subscription['id']

            # Find user by subscription ID
            conn = user_db.get_connection()
            c = conn.cursor()
            c.execute("SELECT id FROM users WHERE stripe_subscription_id = ?", (subscription_id,))
            user_row = c.fetchone()
            conn.close()

            if user_row:
                user_id = user_row[0]
                app.logger.info(f"Subscription cancelled for user {user_id}, subscription {subscription_id}")

                # Downgrade to free
                user_db.update_subscription(
                    user_id=user_id,
                    tier='free',
                    status='cancelled',
                    stripe_subscription_id=subscription_id
                )

        elif event_type == 'customer.subscription.updated':
            # Subscription updated (e.g., cancel at period end set)
            subscription = event['data']['object']
            subscription_id = subscription['id']
            cancel_at_period_end = subscription.get('cancel_at_period_end', False)
            current_period_start = subscription.get('current_period_start')
            current_period_end = subscription.get('current_period_end')
            status_map = {
                'active': 'active',
                'past_due': 'past_due',
                'canceled': 'cancelled',
                'unpaid': 'cancelled'
            }
            status = status_map.get(subscription.get('status'), 'active')

            # Find user by subscription ID
            conn = user_db.get_connection()
            c = conn.cursor()
            c.execute("SELECT id FROM users WHERE stripe_subscription_id = ?", (subscription_id,))
            user_row = c.fetchone()
            conn.close()

            if user_row:
                user_id = user_row[0]
                app.logger.info(f"Subscription updated for user {user_id}, cancel_at_period_end={cancel_at_period_end}")

                # Update subscription with all details
                tier = 'premium' if status in ['active', 'past_due'] else 'free'
                user_db.update_subscription(
                    user_id=user_id,
                    tier=tier,
                    status=status,
                    stripe_subscription_id=subscription_id,
                    current_period_start=current_period_start,
                    current_period_end=current_period_end,
                    cancel_at_period_end=cancel_at_period_end
                )

        return jsonify({'success': True}), 200

    except Exception as e:
        app.logger.error(f"Error processing webhook {event_type}: {e}")
        return jsonify({'error': str(e)}), 500

# Onboarding Routes
@app.route('/onboarding')
@require_auth
def onboarding_welcome():
    """Welcome page for new users."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    # If already completed onboarding, redirect to dashboard
    if user.get('onboarding_completed'):
        return redirect(url_for('beta_dashboard'))

    return render_template('onboarding/welcome.html')

@app.route('/onboarding/channel')
@require_auth
def onboarding_channel():
    """Channel confirmation page."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    if user.get('onboarding_completed'):
        return redirect(url_for('beta_dashboard'))

    return render_template('onboarding/channel.html')

@app.route('/onboarding/channel', methods=['POST'])
@require_auth
def onboarding_channel_confirm():
    """Confirm channel selection."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        # DEBUG: Log what we have in the user object
        app.logger.info(f"Onboarding channel check - User fields: {list(user.keys())}")
        app.logger.info(f"Onboarding channel check - managed_channel value: {user.get('managed_channel')}")

        # Channel is already set from OAuth (managed_channel)
        # Just confirm it exists
        managed_channel = user.get('managed_channel')
        if not managed_channel:
            # If managed_channel is missing from session, try to fetch from database directly
            conn = user_db.get_connection()
            db_user = conn.execute("SELECT managed_channel FROM users WHERE id = ?", (user['user_id'],)).fetchone()
            conn.close()

            if db_user and db_user['managed_channel']:
                app.logger.info(f"Found managed_channel in DB but not in session: {db_user['managed_channel']}")
                return jsonify({'success': False, 'error': 'Session outdated. Please log out and log in again.'}), 400

            return jsonify({'success': False, 'error': 'No managed channel found'}), 400

        return jsonify({'success': True})

    except Exception as e:
        app.logger.error(f"Error confirming channel: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/onboarding/settings')
@require_auth
def onboarding_settings():
    """Bot settings configuration page."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    if user.get('onboarding_completed'):
        return redirect(url_for('beta_dashboard'))

    return render_template('onboarding/settings.html')

@app.route('/onboarding/settings', methods=['POST'])
@require_auth
def onboarding_settings_save():
    """Save initial bot settings and create channel config."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        data = request.json
        managed_channel = user.get('managed_channel')

        if not managed_channel:
            return jsonify({'success': False, 'error': 'No managed channel found'}), 400

        # Create channel config with user's settings
        conn = sqlite3.connect(db_file)
        try:
            c = conn.cursor()

            # Check if channel config already exists
            c.execute("SELECT channel_name FROM channel_configs WHERE channel_name = ?", (managed_channel,))
            existing = c.fetchone()

            if not existing:
                # Create new channel config
                c.execute("""
                    INSERT INTO channel_configs (
                        channel_name, user_id, join_channel, voice_enabled,
                        tts_enabled, lines_between_messages
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    managed_channel,
                    user['user_id'],
                    data.get('join_channel', True),
                    data.get('voice_enabled', True),
                    False,  # TTS disabled by default (Premium only)
                    data.get('lines_between_messages', 100)
                ))
                conn.commit()

            return jsonify({'success': True})
        finally:
            conn.close()

    except Exception as e:
        app.logger.error(f"Error saving settings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/onboarding/premium')
@require_auth
def onboarding_premium():
    """Premium upsell page."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))

    if user.get('onboarding_completed'):
        return redirect(url_for('beta_dashboard'))

    return render_template('onboarding/premium.html')

@app.route('/onboarding/complete', methods=['POST'])
@require_auth
def onboarding_complete():
    """Mark onboarding as complete."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        # Mark onboarding as complete in database
        conn = user_db.get_connection()
        c = conn.cursor()
        # IMPORTANT: user['user_id'] is the actual user ID, user['id'] is the session ID!
        c.execute("UPDATE users SET onboarding_completed = 1 WHERE id = ?", (user['user_id'],))
        conn.commit()
        conn.close()

        app.logger.info(f"User {user['user_id']} (username: {user.get('username')}) completed onboarding")

        # Clear any cached user data in Flask's g object to force a refresh
        # This ensures the next get_current_user() call will fetch updated data from DB
        if hasattr(g, 'current_user'):
            delattr(g, 'current_user')

        return jsonify({'success': True})

    except Exception as e:
        app.logger.error(f"Error completing onboarding: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# User Profile Management Routes
@app.route('/profile')
@require_auth
def profile():
    """User profile management page."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    
    return render_template('profile.html', user=user)

@app.route('/profile/change-password', methods=['POST'])
@require_auth
def change_password():
    """Change user password."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        current_password = request.form.get('current_password', '').strip()
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        
        if not all([current_password, new_password, confirm_password]):
            return jsonify({'success': False, 'error': 'All fields are required'}), 400
            
        if new_password != confirm_password:
            return jsonify({'success': False, 'error': 'New passwords do not match'}), 400
            
        if len(new_password) < 6:
            return jsonify({'success': False, 'error': 'Password must be at least 6 characters'}), 400
        
        # Verify current password
        conn = user_db.get_connection()
        try:
            current_user_data = conn.execute(
                "SELECT password_hash FROM users WHERE id = ?", (user['user_id'],)
            ).fetchone()
            
            if not current_user_data or not user_db.verify_password(current_password, current_user_data['password_hash']):
                return jsonify({'success': False, 'error': 'Current password is incorrect'}), 400
                
            # Update password
            new_password_hash = user_db.hash_password(new_password)
            conn.execute("""
                UPDATE users 
                SET password_hash = ?, password_changed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (new_password_hash, user['user_id']))
            conn.commit()
            
            # Log password change
            user_db.log_action(
                user['user_id'], 'user.password_changed', 'user', str(user['user_id']),
                {'username': user['username']},
                request.remote_addr, request.headers.get('User-Agent', 'Unknown')
            )
            
            return jsonify({'success': True, 'message': 'Password changed successfully'})
            
        finally:
            conn.close()
            
    except Exception as e:
        app.logger.error(f"Error changing password for user {user['username']}: {e}")
        return jsonify({'success': False, 'error': 'Error changing password'}), 500

@app.route('/profile/change-email', methods=['POST'])
@require_auth
def change_email():
    """Change user email."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        new_email = request.form.get('new_email', '').strip()
        password = request.form.get('password', '').strip()
        
        if not all([new_email, password]):
            return jsonify({'success': False, 'error': 'Email and password are required'}), 400
            
        # Basic email validation
        import re
        if not re.match(r'^[^@]+@[^@]+\.[^@]+$', new_email):
            return jsonify({'success': False, 'error': 'Invalid email format'}), 400
        
        # Verify password
        conn = user_db.get_connection()
        try:
            current_user_data = conn.execute(
                "SELECT password_hash FROM users WHERE id = ?", (user['user_id'],)
            ).fetchone()
            
            if not current_user_data or not user_db.verify_password(password, current_user_data['password_hash']):
                return jsonify({'success': False, 'error': 'Password is incorrect'}), 400
                
            # Check if email is already in use
            existing_email = conn.execute(
                "SELECT id FROM users WHERE email = ? AND id != ?", (new_email, user['user_id'])
            ).fetchone()
            
            if existing_email:
                return jsonify({'success': False, 'error': 'Email is already in use'}), 400
                
            # Update email
            conn.execute("""
                UPDATE users 
                SET email = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (new_email, user['user_id']))
            conn.commit()
            
            # Log email change
            user_db.log_action(
                user['user_id'], 'user.email_changed', 'user', str(user['user_id']),
                {'username': user['username'], 'new_email': new_email},
                request.remote_addr, request.headers.get('User-Agent', 'Unknown')
            )
            
            return jsonify({'success': True, 'message': 'Email changed successfully'})
            
        finally:
            conn.close()
            
    except Exception as e:
        app.logger.error(f"Error changing email for user {user['username']}: {e}")
        return jsonify({'success': False, 'error': 'Error changing email'}), 500

##############################
# Admin User Management Routes
##############################

@app.route('/admin/users')
@require_role('admin')
def admin_users():
    """Admin user management page with table view"""
    try:
        users = user_db.get_all_users()
        roles = user_db.get_all_roles()
        return render_template('admin_users.html', users=users, roles=roles)
    except Exception as e:
        app.logger.error(f"Error loading admin users page: {e}")
        return render_template("500.html", error_message=f"Error loading users: {str(e)}"), 500

@app.route('/admin/users/create', methods=['POST'])
@require_role('admin')
def admin_create_user():
    """Create a new user account"""
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        email = data.get('email', '').strip()
        password = data.get('password', '').strip()
        role_id = data.get('role_id', 2)  # Default to viewer
        
        # Get role name from role_id
        roles = user_db.get_all_roles()
        role_name = None
        for role in roles:
            if role['id'] == role_id:
                role_name = role['name']
                break
        
        if not role_name:
            return jsonify({'success': False, 'error': 'Invalid role selected'}), 400
        
        # Validation
        if not username or not email or not password:
            return jsonify({'success': False, 'error': 'Username, email, and password are required'}), 400
            
        if len(username) < 3:
            return jsonify({'success': False, 'error': 'Username must be at least 3 characters'}), 400
            
        if len(password) < 6:
            return jsonify({'success': False, 'error': 'Password must be at least 6 characters'}), 400
            
        # Check if username or email already exists
        existing_user = user_db.get_user_by_username(username)
        if existing_user:
            return jsonify({'success': False, 'error': 'Username already exists'}), 400
            
        existing_email = user_db.get_user_by_email(email)
        if existing_email:
            return jsonify({'success': False, 'error': 'Email already exists'}), 400
        
        # Create user
        user_id = user_db.create_user(username, password, role_name, email)
        if user_id:
            # Log user creation
            current_user = get_current_user()
            user_db.log_action(
                current_user['user_id'], 'user.created', 'user', str(user_id),
                {'created_username': username, 'created_email': email, 'role_id': role_id},
                request.remote_addr, request.headers.get('User-Agent', 'Unknown')
            )
            return jsonify({'success': True, 'message': f'User {username} created successfully', 'user_id': user_id})
        else:
            return jsonify({'success': False, 'error': 'Failed to create user'}), 500
            
    except Exception as e:
        app.logger.error(f"Error creating user: {e}")
        return jsonify({'success': False, 'error': 'Error creating user'}), 500

@app.route('/admin/users/<int:user_id>/edit', methods=['POST'])
@require_role('admin')
def admin_edit_user(user_id):
    """Edit user account details"""
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        email = data.get('email', '').strip()
        role_id = data.get('role_id')
        
        # Get current user data
        user = user_db.get_user_by_id(user_id)
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404
            
        # Validation
        if not username or not email:
            return jsonify({'success': False, 'error': 'Username and email are required'}), 400
            
        if len(username) < 3:
            return jsonify({'success': False, 'error': 'Username must be at least 3 characters'}), 400
            
        # Check if username/email conflicts with other users
        existing_user = user_db.get_user_by_username(username)
        if existing_user and existing_user['user_id'] != user_id:
            return jsonify({'success': False, 'error': 'Username already exists'}), 400
            
        existing_email = user_db.get_user_by_email(email)
        if existing_email and existing_email['user_id'] != user_id:
            return jsonify({'success': False, 'error': 'Email already exists'}), 400
        
        # Update user
        success = user_db.update_user(user_id, username=username, email=email, role_id=role_id)
        if success:
            # Log user update
            current_user = get_current_user()
            changes = {}
            if username != user['username']:
                changes['username'] = {'old': user['username'], 'new': username}
            if email != user['email']:
                changes['email'] = {'old': user['email'], 'new': email}
            if role_id != user['role_id']:
                changes['role_id'] = {'old': user['role_id'], 'new': role_id}
                
            user_db.log_action(
                current_user['user_id'], 'user.updated', 'user', str(user_id),
                {'target_username': username, 'changes': changes},
                request.remote_addr, request.headers.get('User-Agent', 'Unknown')
            )
            return jsonify({'success': True, 'message': f'User {username} updated successfully'})
        else:
            return jsonify({'success': False, 'error': 'Failed to update user'}), 500
            
    except Exception as e:
        app.logger.error(f"Error updating user {user_id}: {e}")
        return jsonify({'success': False, 'error': 'Error updating user'}), 500

@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@require_role('admin')
def admin_delete_user(user_id):
    """Delete/deactivate user account"""
    try:
        # Get current user data
        user = user_db.get_user_by_id(user_id)
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404
            
        # Don't allow deleting yourself
        current_user = get_current_user()
        if current_user['user_id'] == user_id:
            return jsonify({'success': False, 'error': 'Cannot delete your own account'}), 400
            
        # Delete user
        success = user_db.delete_user(user_id)
        if success:
            # Log user deletion
            user_db.log_action(
                current_user['user_id'], 'user.deleted', 'user', str(user_id),
                {'deleted_username': user['username'], 'deleted_email': user['email']},
                request.remote_addr, request.headers.get('User-Agent', 'Unknown')
            )
            return jsonify({'success': True, 'message': f'User {user["username"]} deleted successfully'})
        else:
            return jsonify({'success': False, 'error': 'Failed to delete user'}), 500
            
    except Exception as e:
        app.logger.error(f"Error deleting user {user_id}: {e}")
        return jsonify({'success': False, 'error': 'Error deleting user'}), 500

@app.route('/admin/users/<int:user_id>/assign-channels', methods=['POST'])
@require_role('admin')
def admin_assign_channels(user_id):
    """Assign channels to a user"""
    try:
        data = request.get_json()
        channel_names = data.get('channels', [])
        
        # Get user data
        user = user_db.get_user_by_id(user_id)
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404
        
        # Clear existing assignments and assign new channels
        success = user_db.assign_channels_to_user(user_id, channel_names)
        if success:
            # Log channel assignment
            current_user = get_current_user()
            user_db.log_action(
                current_user['user_id'], 'user.channels_assigned', 'user', str(user_id),
                {'target_username': user['username'], 'assigned_channels': channel_names},
                request.remote_addr, request.headers.get('User-Agent', 'Unknown')
            )
            return jsonify({'success': True, 'message': f'Channels assigned to {user["username"]} successfully'})
        else:
            return jsonify({'success': False, 'error': 'Failed to assign channels'}), 500
            
    except Exception as e:
        app.logger.error(f"Error assigning channels to user {user_id}: {e}")
        return jsonify({'success': False, 'error': 'Error assigning channels'}), 500

@app.route('/api/user/<int:user_id>/channels')
@require_role('admin')
def api_user_channels(user_id):
    """Get channels assigned to a specific user"""
    try:
        channels = user_db.get_user_channels_from_db(user_id)
        return jsonify({'success': True, 'channels': channels})
    except Exception as e:
        app.logger.error(f"Error getting channels for user {user_id}: {e}")
        return jsonify({'success': False, 'error': 'Error getting user channels'}), 500

@app.route('/admin/users/<int:user_id>/toggle-pro', methods=['POST'])
@require_role('admin')
def admin_toggle_pro_status(user_id):
    """Toggle pro status for a user"""
    try:
        data = request.get_json()
        is_pro = data.get('is_pro', False)

        # Get user
        user = user_db.get_user_by_id(user_id)
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404

        # Update subscription status
        if is_pro:
            # Set to premium/active
            success = user_db.update_subscription(
                user_id,
                tier='premium',
                status='active'
            )
            message = f"User {user['username']} upgraded to Pro (manual)"
        else:
            # Set to free/inactive
            success = user_db.update_subscription(
                user_id,
                tier='free',
                status='inactive'
            )
            message = f"User {user['username']} downgraded to Free (manual)"

        if success:
            # Log action
            current_user = get_current_user()
            user_db.log_action(
                current_user['user_id'], 'user.pro_status_changed', 'user', str(user_id),
                {'target_username': user['username'], 'is_pro': is_pro, 'manual': True},
                request.remote_addr, request.headers.get('User-Agent', 'Unknown')
            )
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': 'Failed to update pro status'}), 500

    except Exception as e:
        app.logger.error(f"Error toggling pro status for user {user_id}: {e}")
        return jsonify({'success': False, 'error': 'Error updating pro status'}), 500

##############################
# Admin Panel Routes
##############################

@app.route('/admin')
@require_role('admin')
def admin_dashboard():
    """Main admin dashboard"""
    try:
        # Get stats
        stats = {
            'total_users': user_db.get_total_users(),
            'total_channels': len(get_all_channel_configs()),
            'pro_users': user_db.get_pro_users_count(),
            'total_messages': get_today_message_count()
        }

        # Get recent activity
        recent_activity = user_db.get_recent_audit_logs(limit=10)

        return render_template('admin_dashboard.html', stats=stats, recent_activity=recent_activity)
    except Exception as e:
        app.logger.error(f"Error loading admin dashboard: {e}")
        return render_template("500.html", error_message=f"Error loading dashboard: {str(e)}"), 500

@app.route('/admin/bot-control')
@require_role('admin')
def admin_bot_control():
    """Bot control page"""
    return render_template('admin_bot_control.html')

@app.route('/admin/message-tools')
@require_role('admin')
def admin_message_tools():
    """Message generation and sending tools"""
    try:
        channels = get_all_channel_configs()
        return render_template('admin_message_tools.html', channels=channels)
    except Exception as e:
        app.logger.error(f"Error loading message tools: {e}")
        return render_template("500.html", error_message=f"Error loading message tools: {str(e)}"), 500

@app.route('/admin/channels')
@require_role('admin')
def admin_channels():
    """Channel management page"""
    try:
        channels = get_all_channel_configs()
        return render_template('admin_channels.html', channels=channels)
    except Exception as e:
        app.logger.error(f"Error loading channel management: {e}")
        return render_template("500.html", error_message=f"Error loading channels: {str(e)}"), 500

@app.route('/admin/monitoring')
@require_role('admin')
def admin_monitoring():
    """System monitoring page"""
    return render_template('admin_monitoring.html')

@app.route('/admin/audit-logs')
@require_role('admin')
def admin_audit_logs():
    """Audit logs page"""
    try:
        logs = user_db.get_recent_audit_logs(limit=100)
        return render_template('admin_audit_logs.html', logs=logs)
    except Exception as e:
        app.logger.error(f"Error loading audit logs: {e}")
        return render_template("500.html", error_message=f"Error loading audit logs: {str(e)}"), 500

@app.route('/admin/settings')
@require_role('admin')
def admin_settings():
    """Admin settings page"""
    return render_template('admin_settings.html')

@app.route('/admin/tts')
@require_role('admin')
def admin_tts():
    """TTS generation page"""
    try:
        channels = get_all_channel_configs()
        return render_template('admin_tts.html', channels=channels)
    except Exception as e:
        app.logger.error(f"Error loading TTS page: {e}")
        return render_template("500.html", error_message=f"Error loading TTS: {str(e)}"), 500

##############################
# Admin API Routes
##############################

@app.route('/api/admin/stats')
@require_role('admin')
def api_admin_stats():
    """Get admin dashboard statistics"""
    try:
        stats = {
            'total_users': user_db.get_total_users(),
            'total_channels': len(get_all_channel_configs()),
            'pro_users': user_db.get_pro_users_count(),
            'total_messages': get_today_message_count()
        }
        return jsonify({'success': True, 'stats': stats})
    except Exception as e:
        app.logger.error(f"Error getting admin stats: {e}")
        return jsonify({'success': False, 'error': 'Error getting stats'}), 500

@app.route('/api/bot/status')
@require_role('admin')
def api_bot_status():
    """Get bot status"""
    try:
        bot_running = is_bot_actually_running()

        if bot_running:
            # Read heartbeat from database instead of JSON file
            conn = sqlite3.connect(db_file)
            c = conn.cursor()

            # Get last heartbeat
            c.execute("SELECT value, timestamp FROM bot_status WHERE key = 'last_heartbeat'")
            heartbeat_row = c.fetchone()
            last_heartbeat = heartbeat_row[0] if heartbeat_row else None

            # Get connected channels
            c.execute("SELECT value FROM bot_status WHERE key = 'connected_channels'")
            channels_row = c.fetchone()
            connected_channels = channels_row[0].split(',') if channels_row and channels_row[0] else []

            # Get TTS status from channel configs (check if any channel has TTS enabled)
            c.execute("SELECT COUNT(*) FROM channel_configs WHERE tts_enabled = 1")
            tts_count = c.fetchone()[0]
            tts_enabled = tts_count > 0

            conn.close()

            # Calculate uptime if possible
            uptime = 0
            if last_heartbeat:
                try:
                    heartbeat_time = datetime.strptime(last_heartbeat, '%Y-%m-%d %H:%M:%S')
                    # Rough uptime estimate (this isn't accurate without start time, but better than nothing)
                    uptime = (datetime.now() - heartbeat_time).total_seconds()
                except:
                    pass

            return jsonify({
                'success': True,
                'bot_running': True,
                'uptime': uptime,
                'connected_channels': connected_channels,
                'tts_enabled': tts_enabled,
                'last_heartbeat': last_heartbeat
            })
        else:
            return jsonify({
                'success': True,
                'bot_running': False
            })
    except Exception as e:
        app.logger.error(f"Error getting bot status: {e}")
        return jsonify({'success': False, 'error': 'Error getting bot status'}), 500

@app.route('/api/bot/start', methods=['POST'])
@require_role('admin')
def api_bot_start():
    """Start the bot"""
    try:
        # Check if bot is already running
        if is_bot_actually_running():
            return jsonify({'success': False, 'error': 'Bot is already running'}), 400

        # Start bot in background
        import subprocess
        subprocess.Popen(['python3', 'ansv.py', '--web', '--tts'],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        start_new_session=True)

        # Log action
        current_user = get_current_user()
        user_db.log_action(
            current_user['user_id'], 'bot.started', 'system', 'bot',
            {'started_by': current_user['username']},
            request.remote_addr, request.headers.get('User-Agent', 'Unknown')
        )

        return jsonify({'success': True, 'message': 'Bot starting...'})
    except Exception as e:
        app.logger.error(f"Error starting bot: {e}")
        return jsonify({'success': False, 'error': 'Error starting bot'}), 500

@app.route('/api/bot/stop', methods=['POST'])
@require_role('admin')
def api_bot_stop():
    """Stop the bot"""
    try:
        # Check if bot is running
        if not is_bot_actually_running():
            return jsonify({'success': False, 'error': 'Bot is not running'}), 400

        # Read PID file
        if os.path.exists('bot.pid'):
            with open('bot.pid', 'r') as f:
                pid = int(f.read().strip())

            # Send SIGTERM to bot process
            os.kill(pid, signal.SIGTERM)

            # Log action
            current_user = get_current_user()
            user_db.log_action(
                current_user['user_id'], 'bot.stopped', 'system', 'bot',
                {'stopped_by': current_user['username']},
                request.remote_addr, request.headers.get('User-Agent', 'Unknown')
            )

            return jsonify({'success': True, 'message': 'Bot stopping...'})
        else:
            return jsonify({'success': False, 'error': 'Bot PID file not found'}), 500

    except Exception as e:
        app.logger.error(f"Error stopping bot: {e}")
        return jsonify({'success': False, 'error': 'Error stopping bot'}), 500

@app.route('/api/bot/restart', methods=['POST'])
@require_role('admin')
def api_bot_restart():
    """Restart the bot"""
    try:
        # Stop bot first
        if is_bot_actually_running():
            if os.path.exists('bot.pid'):
                with open('bot.pid', 'r') as f:
                    pid = int(f.read().strip())
                os.kill(pid, signal.SIGTERM)
                time.sleep(2)

        # Start bot
        import subprocess
        subprocess.Popen(['python3', 'ansv.py', '--web', '--tts'],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        start_new_session=True)

        # Log action
        current_user = get_current_user()
        user_db.log_action(
            current_user['user_id'], 'bot.restarted', 'system', 'bot',
            {'restarted_by': current_user['username']},
            request.remote_addr, request.headers.get('User-Agent', 'Unknown')
        )

        return jsonify({'success': True, 'message': 'Bot restarting...'})
    except Exception as e:
        app.logger.error(f"Error restarting bot: {e}")
        return jsonify({'success': False, 'error': 'Error restarting bot'}), 500

@app.route('/api/bot/reconnect', methods=['POST'])
@require_role('admin')
def api_bot_reconnect():
    """Reconnect bot to all channels"""
    try:
        # Write reconnect command to database
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bot_commands (command, status) VALUES (?, ?)",
            ('reconnect', 'pending')
        )
        conn.commit()
        conn.close()

        # Log action
        current_user = get_current_user()
        user_db.log_action(
            current_user['user_id'], 'bot.reconnect_requested', 'system', 'bot',
            {'requested_by': current_user['username']},
            request.remote_addr, request.headers.get('User-Agent', 'Unknown')
        )

        return jsonify({'success': True, 'message': 'Reconnect command sent to bot'})
    except Exception as e:
        app.logger.error(f"Error reconnecting bot: {e}")
        return jsonify({'success': False, 'error': 'Error reconnecting bot'}), 500

@app.route('/api/bot/logs')
@require_role('admin')
def api_bot_logs():
    """Get recent bot logs"""
    try:
        logs = []
        # Check multiple possible log file locations
        log_files = ['app.log', 'ansv_bot.log', 'bot.log']
        log_file = None

        for filename in log_files:
            if os.path.exists(filename):
                log_file = filename
                break

        if log_file:
            with open(log_file, 'r') as f:
                lines = f.readlines()[-50:]  # Last 50 lines
                for line in lines:
                    # Parse log line - try to extract level
                    level = 'info'
                    if 'ERROR' in line.upper():
                        level = 'error'
                    elif 'WARNING' in line.upper() or 'WARN' in line.upper():
                        level = 'warning'
                    elif 'DEBUG' in line.upper():
                        level = 'debug'

                    logs.append({
                        'timestamp': datetime.now().isoformat(),
                        'level': level,
                        'message': line.strip()
                    })
        else:
            # No log file found - add informative message
            logs.append({
                'timestamp': datetime.now().isoformat(),
                'level': 'info',
                'message': 'No bot log file found. Bot may not be running or logs are not being written.'
            })

        return jsonify({'success': True, 'logs': logs})
    except Exception as e:
        app.logger.error(f"Error getting bot logs: {e}")
        return jsonify({'success': False, 'error': 'Error getting logs'}), 500

@app.route('/api/admin/generate-message', methods=['POST'])
@require_role('admin')
def api_admin_generate_message():
    """Generate a message using Markov model"""
    try:
        data = request.get_json()
        channel = data.get('channel')
        attempts = data.get('attempts', 8)

        if not channel:
            return jsonify({'success': False, 'error': 'Channel is required'}), 400

        # Generate message
        markov_handler = MarkovHandler(db_file)

        if channel == 'general':
            message = markov_handler.generate_message(None, attempts)
        else:
            message = markov_handler.generate_message(channel, attempts)

        if message:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': 'Failed to generate message'}), 500

    except Exception as e:
        app.logger.error(f"Error generating message: {e}")
        return jsonify({'success': False, 'error': 'Error generating message'}), 500

@app.route('/api/admin/send-message', methods=['POST'])
@require_role('admin')
def api_admin_send_message():
    """Send a message to a channel"""
    try:
        data = request.get_json()
        channel = data.get('channel')
        message = data.get('message')
        generate_tts = data.get('generate_tts', False)

        if not channel or not message:
            return jsonify({'success': False, 'error': 'Channel and message are required'}), 400

        # Write message request to JSON file for bot to pick up
        message_request = {
            'channel': channel,
            'message': message,
            'generate_tts': generate_tts,
            'timestamp': datetime.now().isoformat(),
            'requested_by': get_current_user()['username']
        }

        with open('bot_message_request.json', 'w') as f:
            json.dump(message_request, f)

        # Log action
        current_user = get_current_user()
        user_db.log_action(
            current_user['user_id'], 'message.sent_via_admin', 'channel', channel,
            {'message': message, 'generate_tts': generate_tts},
            request.remote_addr, request.headers.get('User-Agent', 'Unknown')
        )

        return jsonify({'success': True, 'message': 'Message sent to bot for delivery'})
    except Exception as e:
        app.logger.error(f"Error sending message: {e}")
        return jsonify({'success': False, 'error': 'Error sending message'}), 500

@app.route('/api/admin/generate-tts', methods=['POST'])
@require_role('admin')
def api_admin_generate_tts():
    """Generate TTS audio"""
    try:
        data = request.get_json()
        text = data.get('text')
        voice_preset = data.get('voice_preset', 'v2/en_speaker_5')
        channel = data.get('channel')

        if not text:
            return jsonify({'success': False, 'error': 'Text is required'}), 400

        # Generate TTS (async, won't block)
        start_tts_processing(text, voice_preset, channel or 'admin', db_file)

        # Log action
        current_user = get_current_user()
        user_db.log_action(
            current_user['user_id'], 'tts.generated_via_admin', 'system', 'tts',
            {'text': text[:50], 'voice_preset': voice_preset, 'channel': channel},
            request.remote_addr, request.headers.get('User-Agent', 'Unknown')
        )

        # Note: TTS is generated asynchronously, so we can't return the exact file path
        # The file will be available in the TTS logs table once complete
        return jsonify({
            'success': True,
            'message': 'TTS generation started - check recent TTS files',
            'file_path': '/static/outputs/' + (channel or 'admin') + '/latest.wav'
        })
    except Exception as e:
        app.logger.error(f"Error generating TTS: {e}")
        return jsonify({'success': False, 'error': 'Error generating TTS'}), 500

@app.route('/api/admin/message-history')
@require_role('admin')
def api_admin_message_history():
    """Get recent messages sent via admin panel"""
    try:
        # Get recent bot messages from database
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT channel, message, timestamp
            FROM messages
            WHERE is_bot_response = 1
            ORDER BY timestamp DESC
            LIMIT 20
        """)

        messages = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return jsonify({'success': True, 'messages': messages})
    except Exception as e:
        app.logger.error(f"Error getting message history: {e}")
        return jsonify({'success': False, 'error': 'Error getting message history'}), 500

##############################
# Channel Management API Routes
##############################

@app.route('/api/admin/channels/create', methods=['POST'])
@require_role('admin')
def api_admin_create_channel():
    """Create a new channel configuration"""
    try:
        data = request.get_json()
        channel_name = data.get('channel_name', '').strip()

        if not channel_name:
            return jsonify({'success': False, 'error': 'Channel name is required'}), 400

        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()

        # Check if channel already exists
        cursor.execute("SELECT 1 FROM channel_configs WHERE channel_name = ?", (channel_name,))
        if cursor.fetchone():
            conn.close()
            return jsonify({'success': False, 'error': 'Channel already exists'}), 400

        # Insert new channel
        cursor.execute("""
            INSERT INTO channel_configs (
                channel_name, owner, lines_between_messages, time_between_messages,
                voice_preset, join_channel, tts_enabled, use_general_model,
                currently_connected, voice_enabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1)
        """, (
            channel_name,
            data.get('owner', ''),
            data.get('lines_between_messages', 30),
            data.get('time_between_messages', 120),
            data.get('voice_preset', 'v2/en_speaker_5'),
            data.get('join_channel', 1),
            data.get('tts_enabled', 0),
            data.get('use_general_model', 1)
        ))

        conn.commit()
        conn.close()

        # Log action
        current_user = get_current_user()
        user_db.log_action(
            current_user['user_id'], 'channel.created', 'channel', channel_name,
            {'channel_name': channel_name},
            request.remote_addr, request.headers.get('User-Agent', 'Unknown')
        )

        return jsonify({'success': True, 'message': f'Channel {channel_name} created'})
    except Exception as e:
        app.logger.error(f"Error creating channel: {e}")
        return jsonify({'success': False, 'error': 'Error creating channel'}), 500

@app.route('/api/admin/channels/<channel_name>')
@require_role('admin')
def api_admin_get_channel(channel_name):
    """Get channel configuration"""
    try:
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM channel_configs WHERE channel_name = ?", (channel_name,))
        channel = cursor.fetchone()
        conn.close()

        if not channel:
            return jsonify({'success': False, 'error': 'Channel not found'}), 404

        return jsonify({'success': True, 'channel': dict(channel)})
    except Exception as e:
        app.logger.error(f"Error getting channel: {e}")
        return jsonify({'success': False, 'error': 'Error getting channel'}), 500

@app.route('/api/admin/channels/<channel_name>/update', methods=['POST'])
@require_role('admin')
def api_admin_update_channel(channel_name):
    """Update channel configuration"""
    try:
        data = request.get_json()

        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE channel_configs
            SET owner = ?, voice_preset = ?, lines_between_messages = ?,
                time_between_messages = ?, join_channel = ?, tts_enabled = ?,
                use_general_model = ?
            WHERE channel_name = ?
        """, (
            data.get('owner', ''),
            data.get('voice_preset'),
            data.get('lines_between_messages'),
            data.get('time_between_messages'),
            data.get('join_channel'),
            data.get('tts_enabled'),
            data.get('use_general_model'),
            channel_name
        ))

        conn.commit()
        conn.close()

        # Log action
        current_user = get_current_user()
        user_db.log_action(
            current_user['user_id'], 'channel.updated', 'channel', channel_name,
            {'changes': data},
            request.remote_addr, request.headers.get('User-Agent', 'Unknown')
        )

        return jsonify({'success': True, 'message': 'Channel updated'})
    except Exception as e:
        app.logger.error(f"Error updating channel: {e}")
        return jsonify({'success': False, 'error': 'Error updating channel'}), 500

@app.route('/api/admin/channels/<channel_name>/toggle-join', methods=['POST'])
@require_role('admin')
def api_admin_toggle_join(channel_name):
    """Toggle auto-join for channel"""
    try:
        data = request.get_json()
        join_channel = data.get('join_channel', 0)

        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE channel_configs SET join_channel = ? WHERE channel_name = ?",
            (join_channel, channel_name)
        )
        conn.commit()
        conn.close()

        return jsonify({'success': True, 'message': 'Auto-join updated'})
    except Exception as e:
        app.logger.error(f"Error toggling join: {e}")
        return jsonify({'success': False, 'error': 'Error updating setting'}), 500

@app.route('/api/admin/channels/<channel_name>/toggle-tts', methods=['POST'])
@require_role('admin')
def api_admin_toggle_tts(channel_name):
    """Toggle TTS for channel"""
    try:
        data = request.get_json()
        tts_enabled = data.get('tts_enabled', 0)

        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE channel_configs SET tts_enabled = ? WHERE channel_name = ?",
            (tts_enabled, channel_name)
        )
        conn.commit()
        conn.close()

        return jsonify({'success': True, 'message': 'TTS updated'})
    except Exception as e:
        app.logger.error(f"Error toggling TTS: {e}")
        return jsonify({'success': False, 'error': 'Error updating TTS'}), 500

@app.route('/api/admin/channels/<channel_name>/delete', methods=['POST'])
@require_role('admin')
def api_admin_delete_channel(channel_name):
    """Delete channel configuration"""
    try:
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM channel_configs WHERE channel_name = ?", (channel_name,))
        conn.commit()
        conn.close()

        # Log action
        current_user = get_current_user()
        user_db.log_action(
            current_user['user_id'], 'channel.deleted', 'channel', channel_name,
            {'channel_name': channel_name},
            request.remote_addr, request.headers.get('User-Agent', 'Unknown')
        )

        return jsonify({'success': True, 'message': 'Channel deleted'})
    except Exception as e:
        app.logger.error(f"Error deleting channel: {e}")
        return jsonify({'success': False, 'error': 'Error deleting channel'}), 500

##############################
# Settings & System API Routes
##############################

def get_bot_uptime():
    """Get bot uptime if bot is running"""
    try:
        if not os.path.exists('bot.pid'):
            return None
        with open('bot.pid', 'r') as f:
            pid = int(f.read().strip())
        process = psutil.Process(pid)
        create_time = process.create_time()
        uptime_seconds = time.time() - create_time
        hours = int(uptime_seconds // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        return f"{hours}h {minutes}m"
    except:
        return None

def ensure_settings_table():
    """Ensure app_settings table exists"""
    try:
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        conn.commit()
        conn.close()
    except Exception as e:
        app.logger.error(f"Error creating settings table: {e}")

def get_setting(key, default=None):
    """Get a setting value"""
    try:
        ensure_settings_table()
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
        result = cursor.fetchone()
        conn.close()
        if result:
            # Try to parse as JSON, fall back to string
            try:
                return json.loads(result[0])
            except:
                return result[0]
        return default
    except Exception as e:
        app.logger.error(f"Error getting setting {key}: {e}")
        return default

def set_setting(key, value):
    """Set a setting value"""
    try:
        ensure_settings_table()
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        # Convert to JSON if not a string
        value_str = json.dumps(value) if not isinstance(value, str) else value
        cursor.execute("""
            INSERT OR REPLACE INTO app_settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        """, (key, value_str))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        app.logger.error(f"Error setting {key}: {e}")
        return False

@app.route('/api/admin/system-info')
@require_role('admin')
def api_admin_system_info():
    """Get system information"""
    try:
        import sys

        # Check if bot is running
        bot_running = False
        if os.path.exists('bot.pid'):
            try:
                with open('bot.pid', 'r') as f:
                    pid = int(f.read().strip())
                bot_running = psutil.pid_exists(pid)
            except:
                pass

        # Get total channels
        channels = get_all_channel_configs()

        # Get total messages
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM messages")
        total_messages = cursor.fetchone()[0]
        conn.close()

        # Get database size
        db_size = os.path.getsize(db_file) if os.path.exists(db_file) else 0

        return jsonify({
            'success': True,
            'bot_running': bot_running,
            'total_channels': len(channels),
            'total_messages': total_messages,
            'db_size': db_size,
            'python_version': f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            'bot_uptime': get_bot_uptime(),
            'db_path': db_file
        })
    except Exception as e:
        app.logger.error(f"Error getting system info: {e}")
        return jsonify({'success': False, 'error': 'Error getting system info'}), 500

@app.route('/api/admin/settings')
@require_role('admin')
def api_admin_get_settings():
    """Get all settings"""
    try:
        settings = {
            # Bot settings
            'default_voice_preset': get_setting('default_voice_preset', 'v2/en_speaker_5'),
            'default_lines_between': get_setting('default_lines_between', 20),
            'default_time_between': get_setting('default_time_between', 300),
            'markov_attempts': get_setting('markov_attempts', 8),
            'tts_enabled_default': get_setting('tts_enabled_default', True),
            'auto_join_default': get_setting('auto_join_default', True),
            # App settings
            'log_level': get_setting('log_level', 'INFO'),
            'session_timeout': get_setting('session_timeout', 24),
            'maintenance_mode': get_setting('maintenance_mode', False)
        }
        return jsonify({'success': True, 'settings': settings})
    except Exception as e:
        app.logger.error(f"Error getting settings: {e}")
        return jsonify({'success': False, 'error': 'Error getting settings'}), 500

@app.route('/api/admin/settings/update', methods=['POST'])
@require_role('admin')
def api_admin_update_settings():
    """Update settings"""
    try:
        data = request.get_json()

        # Update each setting
        for key, value in data.items():
            set_setting(key, value)

        # Log action
        current_user = get_current_user()
        user_db.log_action(
            current_user['user_id'], 'settings.updated', 'system', 'settings',
            {'updated_keys': list(data.keys())},
            request.remote_addr, request.headers.get('User-Agent', 'Unknown')
        )

        return jsonify({'success': True, 'message': 'Settings updated'})
    except Exception as e:
        app.logger.error(f"Error updating settings: {e}")
        return jsonify({'success': False, 'error': 'Error updating settings'}), 500

@app.route('/api/admin/settings/reset', methods=['POST'])
@require_role('admin')
def api_admin_reset_settings():
    """Reset all settings to defaults"""
    try:
        ensure_settings_table()
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM app_settings")
        conn.commit()
        conn.close()

        # Log action
        current_user = get_current_user()
        user_db.log_action(
            current_user['user_id'], 'settings.reset', 'system', 'settings',
            {},
            request.remote_addr, request.headers.get('User-Agent', 'Unknown')
        )

        return jsonify({'success': True, 'message': 'Settings reset to defaults'})
    except Exception as e:
        app.logger.error(f"Error resetting settings: {e}")
        return jsonify({'success': False, 'error': 'Error resetting settings'}), 500

@app.route('/api/admin/database-stats')
@require_role('admin')
def api_admin_database_stats():
    """Get database table statistics"""
    try:
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()

        # Get list of tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]

        stats = []
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]

            # Try to get last updated timestamp if table has a timestamp column
            last_updated = None
            try:
                cursor.execute(f"SELECT timestamp FROM {table} ORDER BY timestamp DESC LIMIT 1")
                result = cursor.fetchone()
                if result and result[0]:
                    last_updated = result[0]
            except:
                pass

            stats.append({
                'name': table,
                'count': count,
                'last_updated': last_updated
            })

        conn.close()
        return jsonify({'success': True, 'tables': stats})
    except Exception as e:
        app.logger.error(f"Error getting database stats: {e}")
        return jsonify({'success': False, 'error': 'Error getting database stats'}), 500

@app.route('/api/admin/database/backup', methods=['POST'])
@require_role('admin')
def api_admin_backup_database():
    """Create database backup"""
    try:
        import shutil

        # Create backups directory if it doesn't exist
        backup_dir = 'backups'
        os.makedirs(backup_dir, exist_ok=True)

        # Create backup with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_file = f"{backup_dir}/messages_backup_{timestamp}.db"

        shutil.copy2(db_file, backup_file)

        # Also backup users database
        if os.path.exists('users.db'):
            users_backup = f"{backup_dir}/users_backup_{timestamp}.db"
            shutil.copy2('users.db', users_backup)

        # Log action
        current_user = get_current_user()
        user_db.log_action(
            current_user['user_id'], 'database.backup', 'system', 'database',
            {'backup_file': backup_file},
            request.remote_addr, request.headers.get('User-Agent', 'Unknown')
        )

        return jsonify({
            'success': True,
            'message': 'Backup created successfully',
            'backup_file': backup_file
        })
    except Exception as e:
        app.logger.error(f"Error creating backup: {e}")
        return jsonify({'success': False, 'error': 'Error creating backup'}), 500

@app.route('/api/admin/database/vacuum', methods=['POST'])
@require_role('admin')
def api_admin_vacuum_database():
    """Vacuum database to optimize"""
    try:
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute("VACUUM")
        conn.commit()
        conn.close()

        # Log action
        current_user = get_current_user()
        user_db.log_action(
            current_user['user_id'], 'database.vacuum', 'system', 'database',
            {},
            request.remote_addr, request.headers.get('User-Agent', 'Unknown')
        )

        return jsonify({'success': True, 'message': 'Database optimized'})
    except Exception as e:
        app.logger.error(f"Error vacuuming database: {e}")
        return jsonify({'success': False, 'error': 'Error optimizing database'}), 500

@app.route('/api/admin/database/clear-messages', methods=['POST'])
@require_role('admin')
def api_admin_clear_messages():
    """Clear all messages from database"""
    try:
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages")
        conn.commit()
        conn.close()

        # Log action
        current_user = get_current_user()
        user_db.log_action(
            current_user['user_id'], 'database.clear_messages', 'system', 'database',
            {'warning': 'All messages deleted'},
            request.remote_addr, request.headers.get('User-Agent', 'Unknown')
        )

        return jsonify({'success': True, 'message': 'All messages cleared'})
    except Exception as e:
        app.logger.error(f"Error clearing messages: {e}")
        return jsonify({'success': False, 'error': 'Error clearing messages'}), 500

##############################
# Audit Logs API Routes
##############################

@app.route('/api/admin/audit-logs')
@require_role('admin')
def api_admin_audit_logs():
    """Get audit logs with stats and filtering"""
    try:
        # Get all audit logs
        logs = user_db.get_recent_audit_logs(limit=10000)  # Get more for client-side filtering

        # Calculate stats
        today = datetime.now().date()
        stats = {
            'total': len(logs),
            'today': len([log for log in logs if datetime.fromisoformat(log['timestamp']).date() == today]),
            'unique_users': len(set(log['username'] for log in logs if log.get('username'))),
            'failed': 0  # Can be enhanced to track failed actions
        }

        return jsonify({'success': True, 'logs': logs, 'stats': stats})
    except Exception as e:
        app.logger.error(f"Error getting audit logs: {e}")
        return jsonify({'success': False, 'error': 'Error getting audit logs'}), 500

@app.route('/api/admin/audit-logs/export', methods=['POST'])
@require_role('admin')
def api_admin_audit_logs_export():
    """Export audit logs as CSV or JSON"""
    try:
        import csv
        from io import StringIO

        data = request.get_json()
        format_type = request.args.get('format', 'csv')
        filters = data.get('filters', {})

        # Get logs
        logs = user_db.get_recent_audit_logs(limit=10000)

        # Apply filters (simplified version, same logic as frontend)
        if filters.get('action'):
            logs = [log for log in logs if log['action'] == filters['action']]
        if filters.get('user'):
            logs = [log for log in logs if log.get('username') == filters['user']]
        if filters.get('search'):
            search_term = filters['search'].lower()
            logs = [log for log in logs if any(
                search_term in str(v).lower() for v in log.values() if v
            )]

        # Log export action
        current_user = get_current_user()
        user_db.log_action(
            current_user['user_id'], 'audit_logs.exported', 'system', 'audit_logs',
            {'format': format_type, 'count': len(logs)},
            request.remote_addr, request.headers.get('User-Agent', 'Unknown')
        )

        if format_type == 'csv':
            # Generate CSV
            output = StringIO()
            if logs:
                fieldnames = ['timestamp', 'username', 'action', 'resource_type', 'resource_id', 'ip_address', 'user_agent']
                writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
                writer.writeheader()
                for log in logs:
                    writer.writerow(log)

            response = make_response(output.getvalue())
            response.headers['Content-Type'] = 'text/csv'
            response.headers['Content-Disposition'] = f'attachment; filename=audit-logs-{datetime.now().strftime("%Y%m%d")}.csv'
            return response

        elif format_type == 'json':
            # Generate JSON
            response = make_response(json.dumps(logs, indent=2, default=str))
            response.headers['Content-Type'] = 'application/json'
            response.headers['Content-Disposition'] = f'attachment; filename=audit-logs-{datetime.now().strftime("%Y%m%d")}.json'
            return response

        else:
            return jsonify({'success': False, 'error': 'Invalid format'}), 400

    except Exception as e:
        app.logger.error(f"Error exporting audit logs: {e}")
        return jsonify({'success': False, 'error': 'Error exporting audit logs'}), 500

##############################
# Notification System
##############################

def ensure_notifications_table():
    """Ensure notifications table exists"""
    try:
        conn = user_db.get_connection()
        conn.execute('''CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            link TEXT,
            icon TEXT,
            is_read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )''')
        conn.commit()
        conn.close()
    except Exception as e:
        app.logger.error(f"Error creating notifications table: {e}")

def create_notification(user_id, type, title, message, link=None, icon=None):
    """Create a new notification for a user"""
    try:
        ensure_notifications_table()
        conn = user_db.get_connection()
        conn.execute("""
            INSERT INTO notifications (user_id, type, title, message, link, icon)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, type, title, message, link, icon))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        app.logger.error(f"Error creating notification: {e}")
        return False

def create_notification_for_role(role_name, type, title, message, link=None, icon=None):
    """Create notification for all users with a specific role"""
    try:
        conn = user_db.get_connection()
        users = conn.execute("""
            SELECT u.id FROM users u
            JOIN roles r ON u.role_id = r.id
            WHERE r.name = ?
        """, (role_name,)).fetchall()
        conn.close()

        for user in users:
            create_notification(user[0], type, title, message, link, icon)
        return True
    except Exception as e:
        app.logger.error(f"Error creating role notifications: {e}")
        return False

@app.route('/api/notifications')
@require_auth
def api_get_notifications():
    """Get user notifications"""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'success': False, 'error': 'Not authenticated'}), 401

        ensure_notifications_table()
        conn = user_db.get_connection()
        notifications = conn.execute("""
            SELECT * FROM notifications
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 50
        """, (user['user_id'],)).fetchall()
        conn.close()

        notifications_list = [dict(n) for n in notifications]

        # Count unread
        unread_count = len([n for n in notifications_list if not n['is_read']])

        return jsonify({
            'success': True,
            'notifications': notifications_list,
            'unread_count': unread_count
        })
    except Exception as e:
        app.logger.error(f"Error getting notifications: {e}")
        return jsonify({'success': False, 'error': 'Error getting notifications'}), 500

@app.route('/api/notifications/<int:notification_id>/read', methods=['POST'])
@require_auth
def api_mark_notification_read(notification_id):
    """Mark notification as read"""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'success': False, 'error': 'Not authenticated'}), 401

        conn = user_db.get_connection()
        conn.execute("""
            UPDATE notifications
            SET is_read = 1
            WHERE id = ? AND user_id = ?
        """, (notification_id, user['user_id']))
        conn.commit()
        conn.close()

        return jsonify({'success': True})
    except Exception as e:
        app.logger.error(f"Error marking notification read: {e}")
        return jsonify({'success': False, 'error': 'Error updating notification'}), 500

@app.route('/api/notifications/read-all', methods=['POST'])
@require_auth
def api_mark_all_notifications_read():
    """Mark all notifications as read"""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'success': False, 'error': 'Not authenticated'}), 401

        conn = user_db.get_connection()
        conn.execute("""
            UPDATE notifications
            SET is_read = 1
            WHERE user_id = ? AND is_read = 0
        """, (user['user_id'],))
        conn.commit()
        conn.close()

        return jsonify({'success': True})
    except Exception as e:
        app.logger.error(f"Error marking all notifications read: {e}")
        return jsonify({'success': False, 'error': 'Error updating notifications'}), 500

@app.route('/api/notifications/<int:notification_id>', methods=['DELETE'])
@require_auth
def api_delete_notification(notification_id):
    """Delete a notification"""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'success': False, 'error': 'Not authenticated'}), 401

        conn = user_db.get_connection()
        conn.execute("""
            DELETE FROM notifications
            WHERE id = ? AND user_id = ?
        """, (notification_id, user['user_id']))
        conn.commit()
        conn.close()

        return jsonify({'success': True})
    except Exception as e:
        app.logger.error(f"Error deleting notification: {e}")
        return jsonify({'success': False, 'error': 'Error deleting notification'}), 500

@app.route('/api/notifications/clear-all', methods=['POST'])
@require_auth
def api_clear_all_notifications():
    """Delete all notifications for user"""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'success': False, 'error': 'Not authenticated'}), 401

        conn = user_db.get_connection()
        conn.execute("""
            DELETE FROM notifications
            WHERE user_id = ?
        """, (user['user_id'],))
        conn.commit()
        conn.close()

        return jsonify({'success': True})
    except Exception as e:
        app.logger.error(f"Error clearing notifications: {e}")
        return jsonify({'success': False, 'error': 'Error clearing notifications'}), 500

##############################
# Activity Logs for Streamers
##############################

@app.route('/beta/activity')
@require_auth
def beta_activity_logs():
    """Activity logs page for streamers"""
    return render_template('beta/activity.html')

@app.route('/api/activity-logs')
@require_auth
def api_get_activity_logs():
    """Get activity logs for current user's channel"""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'success': False, 'error': 'Not authenticated'}), 401

        # Get all logs, we'll filter based on user role
        all_logs = user_db.get_recent_audit_logs(limit=5000)

        # Filter logs based on user role
        if user.get('role_name') in ['admin', 'super_admin']:
            # Admins see everything
            logs = all_logs
        elif user.get('role_name') == 'streamer' and user.get('managed_channel'):
            # Streamers see only logs related to their channel
            channel = user['managed_channel']
            logs = [log for log in all_logs if (
                log.get('resource_type') == 'channel' and log.get('resource_id') == channel
            ) or (
                log.get('action', '').startswith('channel.') and channel in str(log.get('details', ''))
            ) or (
                log.get('user_id') == user['user_id']
            )]
        else:
            # Regular users see only their own actions
            logs = [log for log in all_logs if log.get('user_id') == user['user_id']]

        # Calculate stats
        today = datetime.now().date()
        stats = {
            'total': len(logs),
            'today': len([log for log in logs if datetime.fromisoformat(log['timestamp']).date() == today]),
            'this_week': len([log for log in logs if (datetime.now() - datetime.fromisoformat(log['timestamp'])).days <= 7]),
            'actions_count': {}
        }

        # Count action types
        for log in logs:
            action = log.get('action', 'unknown')
            stats['actions_count'][action] = stats['actions_count'].get(action, 0) + 1

        return jsonify({'success': True, 'logs': logs, 'stats': stats})
    except Exception as e:
        app.logger.error(f"Error getting activity logs: {e}")
        return jsonify({'success': False, 'error': 'Error getting activity logs'}), 500

##############################
# Analytics API
##############################

@app.route('/api/analytics')
@require_auth
def api_get_analytics():
    """Get comprehensive analytics data"""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'success': False, 'error': 'Not authenticated'}), 401

        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get total stats
        cursor.execute("SELECT COUNT(*) as count FROM messages")
        total_messages = cursor.fetchone()['count']

        cursor.execute("SELECT COUNT(*) as count FROM tts_logs")
        total_tts = cursor.fetchone()['count']

        # Get message frequency (last 7 days)
        message_freq_query = """
            SELECT DATE(timestamp) as date, COUNT(*) as count
            FROM messages
            WHERE timestamp >= datetime('now', '-7 days')
            GROUP BY DATE(timestamp)
            ORDER BY date
        """
        cursor.execute(message_freq_query)
        message_freq = cursor.fetchall()

        # Get TTS frequency (last 7 days)
        tts_freq_query = """
            SELECT DATE(timestamp) as date, COUNT(*) as count
            FROM tts_logs
            WHERE timestamp >= datetime('now', '-7 days')
            GROUP BY DATE(timestamp)
            ORDER BY date
        """
        cursor.execute(tts_freq_query)
        tts_freq = cursor.fetchall()

        # Get channel activity
        channel_activity_query = """
            SELECT channel, COUNT(*) as count
            FROM messages
            WHERE timestamp >= datetime('now', '-30 days')
            GROUP BY channel
            ORDER BY count DESC
            LIMIT 10
        """
        cursor.execute(channel_activity_query)
        channel_activity = cursor.fetchall()

        # Get hourly activity (for heatmap)
        hourly_query = """
            SELECT CAST(strftime('%H', timestamp) AS INTEGER) as hour, COUNT(*) as count
            FROM messages
            WHERE timestamp >= datetime('now', '-7 days')
            GROUP BY hour
            ORDER BY hour
        """
        cursor.execute(hourly_query)
        hourly_activity = cursor.fetchall()

        # Get channels
        cursor.execute("SELECT * FROM channel_configs")
        channels = cursor.fetchall()

        # Calculate stats
        # Calculate actual week-over-week changes
        cursor.execute("""
            SELECT COUNT(*) as count FROM messages
            WHERE timestamp >= datetime('now', '-7 days')
        """)
        messages_this_week = cursor.fetchone()['count']

        cursor.execute("""
            SELECT COUNT(*) as count FROM messages
            WHERE timestamp >= datetime('now', '-14 days')
            AND timestamp < datetime('now', '-7 days')
        """)
        messages_last_week = cursor.fetchone()['count']

        cursor.execute("""
            SELECT COUNT(*) as count FROM tts_logs
            WHERE timestamp >= datetime('now', '-7 days')
        """)
        tts_this_week = cursor.fetchone()['count']

        cursor.execute("""
            SELECT COUNT(*) as count FROM tts_logs
            WHERE timestamp >= datetime('now', '-14 days')
            AND timestamp < datetime('now', '-7 days')
        """)
        tts_last_week = cursor.fetchone()['count']

        conn.close()

        # Calculate percentage changes
        messages_change = round(((messages_this_week - messages_last_week) / messages_last_week * 100)) if messages_last_week > 0 else 0
        tts_change = round(((tts_this_week - tts_last_week) / tts_last_week * 100)) if tts_last_week > 0 else 0

        # Find peak hour
        peak_hour = max(hourly_activity, key=lambda x: x['count'])['hour'] if hourly_activity else 0

        # Find most active channel
        most_active = channel_activity[0]['channel'] if channel_activity else None

        # Prepare chart data
        # Message frequency
        message_labels = [row['date'] for row in message_freq]
        message_values = [row['count'] for row in message_freq]

        # TTS frequency
        tts_labels = [row['date'] for row in tts_freq]
        tts_values = [row['count'] for row in tts_freq]

        # Channel activity
        channel_labels = [row['channel'] for row in channel_activity]
        channel_values = [row['count'] for row in channel_activity]

        # Hourly activity (fill missing hours with 0)
        hourly_labels = [f"{h:02d}:00" for h in range(24)]
        hourly_dict = {row['hour']: row['count'] for row in hourly_activity}
        hourly_values = [hourly_dict.get(h, 0) for h in range(24)]

        # Model performance - calculate based on actual model data
        model_labels = []
        model_values = []

        # Get unique models from messages
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT model_used, COUNT(*) as usage_count
            FROM messages
            WHERE model_used IS NOT NULL
            AND timestamp >= datetime('now', '-30 days')
            GROUP BY model_used
            ORDER BY usage_count DESC
            LIMIT 5
        """)
        model_usage = cursor.fetchall()
        conn.close()

        if model_usage:
            model_labels = [row['model_used'] for row in model_usage]
            model_values = [row['usage_count'] for row in model_usage]

        # Get model count and size
        try:
            import os
            model_files = [f for f in os.listdir('models') if f.endswith('.json')]
            total_models = len(model_files)
            models_size = sum(os.path.getsize(f'models/{f}') for f in model_files if os.path.exists(f'models/{f}'))
        except:
            total_models = 0
            models_size = 0

        return jsonify({
            'success': True,
            'stats': {
                'total_messages': total_messages,
                'total_tts': total_tts,
                'total_models': total_models,
                'total_channels': len(channels),
                'active_channels': len([c for c in channels if c['join_channel']]),
                'messages_change': messages_change,
                'tts_change': tts_change,
                'models_size': models_size
            },
            'message_frequency': {
                'labels': message_labels,
                'values': message_values
            },
            'tts_frequency': {
                'labels': tts_labels,
                'values': tts_values
            },
            'channel_activity': {
                'labels': channel_labels,
                'values': channel_values
            },
            'hourly_activity': {
                'labels': hourly_labels,
                'values': hourly_values
            },
            'model_performance': {
                'labels': model_labels,
                'values': model_values
            },
            'peak_hour': peak_hour,
            'most_active_channel': most_active
        })
    except Exception as e:
        app.logger.error(f"Error getting analytics: {e}")
        return jsonify({'success': False, 'error': 'Error getting analytics'}), 500

# ============================================================================
# Export API Routes
# ============================================================================

@app.route('/api/export/<export_type>')
@require_auth
def api_export_data(export_type):
    """Export data in various formats (CSV/JSON)."""
    try:
        user = get_current_user()
        username = user.get('username', 'unknown')

        if export_type == 'settings':
            # Export channel settings as JSON
            channels = get_all_channel_configs()

            # Filter by role
            if user.get('role_name') == 'streamer' and user.get('managed_channel'):
                channels = [ch for ch in channels if ch['channel_name'] == user['managed_channel']]

            output = json.dumps(channels, indent=2)
            filename = f'ansv-bot-settings-{datetime.now().strftime("%Y%m%d-%H%M%S")}.json'

            response = app.response_class(
                response=output,
                status=200,
                mimetype='application/json'
            )
            response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'

            # Log export
            user_db.log_audit_action(
                username=username,
                action='export_settings',
                resource_type='settings',
                details=f'Exported {len(channels)} channel settings',
                ip_address=request.remote_addr,
                user_agent=request.headers.get('User-Agent')
            )

            return response

        elif export_type == 'tts':
            # Export TTS history as CSV
            conn = sqlite3.connect(db_file)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query = "SELECT * FROM tts_files ORDER BY timestamp DESC LIMIT 10000"
            cursor.execute(query)
            rows = cursor.fetchall()
            conn.close()

            # Create CSV
            from io import StringIO
            import csv

            output = StringIO()
            writer = csv.writer(output)

            # Write header
            writer.writerow(['ID', 'Username', 'Message', 'Model', 'Timestamp', 'File Path'])

            # Write rows
            for row in rows:
                writer.writerow([
                    row['id'],
                    row['username'],
                    row['message_text'],
                    row['model'],
                    row['timestamp'],
                    row['file_path']
                ])

            csv_data = output.getvalue()
            filename = f'ansv-bot-tts-{datetime.now().strftime("%Y%m%d-%H%M%S")}.csv'

            response = app.response_class(
                response=csv_data,
                status=200,
                mimetype='text/csv'
            )
            response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'

            # Log export
            user_db.log_audit_action(
                username=username,
                action='export_tts',
                resource_type='tts',
                details=f'Exported {len(rows)} TTS records',
                ip_address=request.remote_addr,
                user_agent=request.headers.get('User-Agent')
            )

            return response

        elif export_type == 'messages':
            # Export bot messages as CSV
            conn = sqlite3.connect(db_file)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query = "SELECT * FROM messages ORDER BY timestamp DESC LIMIT 10000"
            cursor.execute(query)
            rows = cursor.fetchall()
            conn.close()

            # Create CSV
            from io import StringIO
            import csv

            output = StringIO()
            writer = csv.writer(output)

            # Write header
            writer.writerow(['ID', 'Username', 'Message', 'Channel', 'Timestamp'])

            # Write rows
            for row in rows:
                writer.writerow([
                    row['id'],
                    row['username'],
                    row['message'],
                    row.get('channel', 'N/A'),
                    row['timestamp']
                ])

            csv_data = output.getvalue()
            filename = f'ansv-bot-messages-{datetime.now().strftime("%Y%m%d-%H%M%S")}.csv'

            response = app.response_class(
                response=csv_data,
                status=200,
                mimetype='text/csv'
            )
            response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'

            # Log export
            user_db.log_audit_action(
                username=username,
                action='export_messages',
                resource_type='messages',
                details=f'Exported {len(rows)} message records',
                ip_address=request.remote_addr,
                user_agent=request.headers.get('User-Agent')
            )

            return response

        elif export_type == 'all':
            # Export everything as a ZIP file
            from io import BytesIO
            import zipfile

            # Create in-memory ZIP
            zip_buffer = BytesIO()

            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                # Add settings JSON
                channels = get_all_channel_configs()
                if user.get('role_name') == 'streamer' and user.get('managed_channel'):
                    channels = [ch for ch in channels if ch['channel_name'] == user['managed_channel']]

                settings_json = json.dumps(channels, indent=2)
                zip_file.writestr('settings.json', settings_json)

                # Add TTS CSV
                conn = sqlite3.connect(db_file)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute("SELECT * FROM tts_files ORDER BY timestamp DESC LIMIT 10000")
                tts_rows = cursor.fetchall()

                from io import StringIO
                import csv

                tts_output = StringIO()
                tts_writer = csv.writer(tts_output)
                tts_writer.writerow(['ID', 'Username', 'Message', 'Model', 'Timestamp', 'File Path'])
                for row in tts_rows:
                    tts_writer.writerow([row['id'], row['username'], row['message_text'], row['model'], row['timestamp'], row['file_path']])

                zip_file.writestr('tts_history.csv', tts_output.getvalue())

                # Add messages CSV
                cursor.execute("SELECT * FROM messages ORDER BY timestamp DESC LIMIT 10000")
                msg_rows = cursor.fetchall()

                msg_output = StringIO()
                msg_writer = csv.writer(msg_output)
                msg_writer.writerow(['ID', 'Username', 'Message', 'Channel', 'Timestamp'])
                for row in msg_rows:
                    msg_writer.writerow([row['id'], row['username'], row['message'], row.get('channel', 'N/A'), row['timestamp']])

                zip_file.writestr('messages.csv', msg_output.getvalue())
                conn.close()

            zip_buffer.seek(0)
            filename = f'ansv-bot-export-{datetime.now().strftime("%Y%m%d-%H%M%S")}.zip'

            response = app.response_class(
                response=zip_buffer.getvalue(),
                status=200,
                mimetype='application/zip'
            )
            response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'

            # Log export
            user_db.log_audit_action(
                username=username,
                action='export_all',
                resource_type='export',
                details='Exported all data (settings, TTS, messages)',
                ip_address=request.remote_addr,
                user_agent=request.headers.get('User-Agent')
            )

            return response

        else:
            return jsonify({'success': False, 'error': 'Invalid export type'}), 400

    except Exception as e:
        app.logger.error(f"Error exporting data: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================================
# Bulk Operations API Routes
# ============================================================================

@app.route('/api/bulk/tts/enable', methods=['POST'])
@require_role('admin')
def api_bulk_enable_tts():
    """Bulk enable TTS for multiple channels."""
    try:
        user = get_current_user()
        username = user.get('username', 'unknown')

        data = request.get_json()
        channels = data.get('channels', [])

        if not channels:
            return jsonify({'success': False, 'error': 'No channels provided'}), 400

        # Update each channel
        success_count = 0
        failed_channels = []

        for channel in channels:
            try:
                update_channel_config(channel, {'tts_enabled': True})
                success_count += 1
            except Exception as e:
                app.logger.error(f"Failed to enable TTS for {channel}: {e}")
                failed_channels.append(channel)

        # Log bulk action
        user_db.log_audit_action(
            username=username,
            action='bulk_enable_tts',
            resource_type='channel',
            details=f'Enabled TTS for {success_count}/{len(channels)} channels',
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent')
        )

        return jsonify({
            'success': True,
            'updated': success_count,
            'failed': len(failed_channels),
            'failed_channels': failed_channels
        })

    except Exception as e:
        app.logger.error(f"Error in bulk enable TTS: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/bulk/tts/disable', methods=['POST'])
@require_role('admin')
def api_bulk_disable_tts():
    """Bulk disable TTS for multiple channels."""
    try:
        user = get_current_user()
        username = user.get('username', 'unknown')

        data = request.get_json()
        channels = data.get('channels', [])

        if not channels:
            return jsonify({'success': False, 'error': 'No channels provided'}), 400

        success_count = 0
        failed_channels = []

        for channel in channels:
            try:
                update_channel_config(channel, {'tts_enabled': False})
                success_count += 1
            except Exception as e:
                app.logger.error(f"Failed to disable TTS for {channel}: {e}")
                failed_channels.append(channel)

        user_db.log_audit_action(
            username=username,
            action='bulk_disable_tts',
            resource_type='channel',
            details=f'Disabled TTS for {success_count}/{len(channels)} channels',
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent')
        )

        return jsonify({
            'success': True,
            'updated': success_count,
            'failed': len(failed_channels),
            'failed_channels': failed_channels
        })

    except Exception as e:
        app.logger.error(f"Error in bulk disable TTS: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/bulk/autojoin/enable', methods=['POST'])
@require_role('admin')
def api_bulk_enable_autojoin():
    """Bulk enable auto-join for multiple channels."""
    try:
        user = get_current_user()
        username = user.get('username', 'unknown')

        data = request.get_json()
        channels = data.get('channels', [])

        if not channels:
            return jsonify({'success': False, 'error': 'No channels provided'}), 400

        success_count = 0
        failed_channels = []

        for channel in channels:
            try:
                update_channel_config(channel, {'auto_join': True})
                success_count += 1
            except Exception as e:
                app.logger.error(f"Failed to enable auto-join for {channel}: {e}")
                failed_channels.append(channel)

        user_db.log_audit_action(
            username=username,
            action='bulk_enable_autojoin',
            resource_type='channel',
            details=f'Enabled auto-join for {success_count}/{len(channels)} channels',
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent')
        )

        return jsonify({
            'success': True,
            'updated': success_count,
            'failed': len(failed_channels),
            'failed_channels': failed_channels
        })

    except Exception as e:
        app.logger.error(f"Error in bulk enable auto-join: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/bulk/autojoin/disable', methods=['POST'])
@require_role('admin')
def api_bulk_disable_autojoin():
    """Bulk disable auto-join for multiple channels."""
    try:
        user = get_current_user()
        username = user.get('username', 'unknown')

        data = request.get_json()
        channels = data.get('channels', [])

        if not channels:
            return jsonify({'success': False, 'error': 'No channels provided'}), 400

        success_count = 0
        failed_channels = []

        for channel in channels:
            try:
                update_channel_config(channel, {'auto_join': False})
                success_count += 1
            except Exception as e:
                app.logger.error(f"Failed to disable auto-join for {channel}: {e}")
                failed_channels.append(channel)

        user_db.log_audit_action(
            username=username,
            action='bulk_disable_autojoin',
            resource_type='channel',
            details=f'Disabled auto-join for {success_count}/{len(channels)} channels',
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent')
        )

        return jsonify({
            'success': True,
            'updated': success_count,
            'failed': len(failed_channels),
            'failed_channels': failed_channels
        })

    except Exception as e:
        app.logger.error(f"Error in bulk disable auto-join: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/bulk/delete', methods=['POST'])
@require_role('admin')
def api_bulk_delete_channels():
    """Bulk delete multiple channels."""
    try:
        user = get_current_user()
        username = user.get('username', 'unknown')

        data = request.get_json()
        channels = data.get('channels', [])

        if not channels:
            return jsonify({'success': False, 'error': 'No channels provided'}), 400

        success_count = 0
        failed_channels = []

        for channel in channels:
            try:
                delete_channel_config(channel)
                success_count += 1
            except Exception as e:
                app.logger.error(f"Failed to delete {channel}: {e}")
                failed_channels.append(channel)

        user_db.log_audit_action(
            username=username,
            action='bulk_delete_channels',
            resource_type='channel',
            details=f'Deleted {success_count}/{len(channels)} channels',
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent')
        )

        return jsonify({
            'success': True,
            'deleted': success_count,
            'failed': len(failed_channels),
            'failed_channels': failed_channels
        })

    except Exception as e:
        app.logger.error(f"Error in bulk delete: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/')
def index():
    """Landing page for public visitors, redirect to dashboard for authenticated users."""
    if is_authenticated():
        # Authenticated users go to dashboard
        return redirect(url_for('beta_dashboard'))
    else:
        # Public visitors see landing page
        return render_template("landing.html")

@app.route('/legacy')
@require_auth
def legacy_main():
    """Legacy main page - preserved for backwards compatibility."""
    # Redirect streamers to their channel instead of main page
    streamer_redirect = redirect_streamers_to_channel()
    if streamer_redirect:
        return streamer_redirect

    # Theme is now injected by context_processor, but can be accessed here if needed
    # theme = request.cookies.get("theme", "darkly")
    tts_files_data, last_id_val = get_last_10_tts_files_with_last_id(db_file)

    bot_running_status = is_bot_actually_running()

    bot_status_info = {
        'running': bot_running_status,
        'uptime': 'N/A',
        'channels': []
    }

    if bot_running_status and os.path.exists('bot_heartbeat.json'):
        try:
            with open('bot_heartbeat.json', 'r') as f:
                heartbeat = json.load(f)
                uptime_seconds = heartbeat.get('uptime', 0)
                days, rem = divmod(uptime_seconds, 86400)
                hours, rem = divmod(rem, 3600)
                mins, secs = divmod(rem, 60)
                bot_status_info['uptime'] = f"{int(days)}d {int(hours)}h {int(mins)}m {int(secs)}s"
                bot_status_info['channels'] = heartbeat.get('channels', [])
        except (FileNotFoundError, json.JSONDecodeError) as e:
            app.logger.warning(f"Could not read or parse bot_heartbeat.json: {e}")

    return render_template("index.html", tts_files=tts_files_data,
                           last_id=last_id_val, bot_status=bot_status_info) # No need to pass theme explicitly

@app.route("/generate-message/<channel_name>")
@require_channel_access('channel_name', 'view')
def generate_message_get(channel_name): 
    try:
        message = markov_handler.generate_message(channel_name, max_attempts=8, max_fallbacks=2)
        if message:
            return jsonify({"success": True, "message": message, "channel": channel_name})
        else:
            return jsonify({"success": False, "error": "Failed to generate message", "message": "Could not generate message."}), 500
    except Exception as e:
        app.logger.error(f"Error in GET /generate-message/{channel_name}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/generate-message", methods=["POST"])
@require_auth
def generate_message_post():
    try:
        data = request.get_json(silent=True) or {}
        model_name_req = data.get('model')
        channel_req = data.get('channel') 
        
        model_to_use = model_name_req or channel_req or "general_markov"
        
        app.logger.info(f"Generating message with effective model: {model_to_use} (requested model: {model_name_req}, channel context: {channel_req})")
        
        message = markov_handler.generate_message(model_to_use, max_attempts=8, max_fallbacks=2)
        
        if message:
            return jsonify({"success": True, "message": message, "model_used": model_to_use})
        else:
            return jsonify({"success": False, "error": "Failed to generate message", "message": "Could not generate message."}), 500
    except Exception as e:
        app.logger.error(f"Error in POST /generate-message: {e}")
        app.logger.error(traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/available-models')
@require_auth
def available_models():
    try:
        models = markov_handler.get_available_models()
        return jsonify(models)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/rebuild-general-cache', methods=['POST'])
@require_role('admin')
def rebuild_general_cache():
    try:
        success = markov_handler.rebuild_general_cache('logs')
        return jsonify({"success": success, "message": "General cache rebuild " + ("succeeded" if success else "failed")})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/rebuild-cache/<channel_name>', methods=['POST'])
@require_channel_access('channel_name', 'edit')
def rebuild_cache(channel_name):
    try:
        success = markov_handler.rebuild_cache_for_channel(channel_name, 'logs')
        return jsonify({"success": success, "message": f"Cache rebuild for {channel_name} " + ("succeeded" if success else "failed")})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/rebuild_model/<channel_name>', methods=['POST'])
@require_channel_access('channel_name', 'edit')
def rebuild_model(channel_name):
    """Rebuild Markov model for a channel (alias for rebuild-cache)."""
    import os

    try:
        # Check if log file exists first
        log_file_path = os.path.join('logs', f'{channel_name}.txt')

        if not os.path.exists(log_file_path):
            app.logger.warning(f"Log file not found for {channel_name}: {log_file_path}")
            return jsonify({
                "success": False,
                "error": f"No chat log file found for #{channel_name}",
                "message": "The bot needs to collect chat messages first before building a model. Make sure the bot is connected and chat messages are being logged."
            }), 404

        # Check if log file has content
        file_size = os.path.getsize(log_file_path)
        if file_size < 100:
            return jsonify({
                "success": False,
                "error": "Chat log file is too small",
                "message": f"The log file for #{channel_name} only has {file_size} bytes. More chat messages are needed to build a model."
            }), 400

        # Attempt rebuild
        success = markov_handler.rebuild_cache_for_channel(channel_name, 'logs')

        if success:
            return jsonify({
                "success": True,
                "message": f"Model rebuilt successfully for #{channel_name}"
            })
        else:
            return jsonify({
                "success": False,
                "error": "Rebuild failed",
                "message": "Check the server logs for details"
            }), 500

    except Exception as e:
        app.logger.error(f"Error rebuilding model for {channel_name}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e),
            "message": "An unexpected error occurred during rebuild"
        }), 500

@app.route('/rebuild-all-caches', methods=['POST'])
@require_role('admin')
def rebuild_all_caches():
    try:
        success = markov_handler.rebuild_all_caches()
        return jsonify({"success": success, "message": "All caches rebuild " + ("succeeded" if success else "failed")})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/bot_status')
@require_auth
def bot_status():
    """Get current bot status."""
    try:
        running = is_bot_actually_running()
        return jsonify({
            'success': True,
            'running': running,
            'timestamp': time.time()
        })
    except Exception as e:
        app.logger.error(f"Error getting bot status: {e}")
        return jsonify({'success': False, 'running': False, 'error': str(e)}), 500

@app.route('/start_bot', methods=['POST'])
@require_permission(Permissions.BOT_START)
def start_bot_route():
    if is_bot_actually_running():
        return jsonify({"success": False, "message": "Bot is already running"}), 400
    try:
        import subprocess
        subprocess.Popen(["./launch.sh", "--web", "--tts"], creationflags=subprocess.DETACHED_PROCESS if os.name == 'nt' else 0)
        
        # PERFORMANCE: Broadcast real-time status update instead of requiring polling
        events.bot_status_changed({
            'running': True,
            'status': 'starting',
            'timestamp': time.time(),
            'message': 'Bot start command issued'
        })
        
        return jsonify({"success": True, "message": "Bot start command issued."})
    except Exception as e:
        # Broadcast failure event
        events.bot_status_changed({
            'running': False,
            'status': 'error',
            'timestamp': time.time(),
            'message': f"Error starting bot: {str(e)}"
        })
        return jsonify({"success": False, "message": f"Error starting bot: {str(e)}"}), 500

@app.route('/stop_bot', methods=['POST'])
@require_permission(Permissions.BOT_STOP)
def stop_bot_route():
    if not is_bot_actually_running():
        return jsonify({"success": False, "message": "Bot is not running"}), 400
    try:
        if os.path.exists("bot.pid"):
            with open("bot.pid", "r") as f:
                pid = int(f.read().strip())
            if psutil.pid_exists(pid):
                os.kill(pid, signal.SIGTERM) 
                time.sleep(1) 
                if psutil.pid_exists(pid): 
                    os.kill(pid, signal.SIGKILL)
                
                # PERFORMANCE: Broadcast real-time status update
                events.bot_status_changed({
                    'running': False,
                    'status': 'stopped',
                    'timestamp': time.time(),
                    'message': 'Bot stopped successfully'
                })
                
                return jsonify({"success": True, "message": "Bot stop command issued."})
            else:
                events.bot_status_changed({
                    'running': False,
                    'status': 'error',
                    'timestamp': time.time(),
                    'message': 'Bot PID found but process does not exist'
                })
                return jsonify({"success": False, "message": "Bot PID found but process does not exist."}), 404
        else:
            events.bot_status_changed({
                'running': False,
                'status': 'error',
                'timestamp': time.time(),
                'message': 'Bot PID file not found'
            })
            return jsonify({"success": False, "message": "Bot PID file not found."}), 404
    except Exception as e:
        events.bot_status_changed({
            'running': False,
            'status': 'error',
            'timestamp': time.time(),
            'message': f"Error stopping bot: {str(e)}"
        })
        return jsonify({"success": False, "message": f"Error stopping bot: {str(e)}"}), 500

@app.route('/api/bot-status')
def api_bot_status_legacy():
    bot_running = is_bot_actually_running()
    is_connected = False  # Default to not connected
    current_uptime_seconds = 0
    bot_tts_status = False
    current_joined_channels = []
    bot_pid = None
    heartbeat_data_available = False
    
    # Read verbose_heartbeat_log setting from config
    try:
        verbose_logging = config.getboolean('settings', 'verbose_heartbeat_log')
    except (configparser.NoSectionError, configparser.NoOptionError):
        verbose_logging = False # Default to False if not found

    if bot_running:
        # If bot is running, try to get details from heartbeat
        if os.path.exists("bot_heartbeat.json"):
            try:
                with open("bot_heartbeat.json", "r") as f:
                    heartbeat = json.load(f)
                heartbeat_data_available = True # Mark that we could read the file
                    
                # Check if heartbeat is recent enough to be considered valid for connection status
                heartbeat_timestamp = heartbeat.get("timestamp", 0)
                if time.time() - heartbeat_timestamp < 120: # Heartbeat within last 2 minutes
                    current_joined_channels = heartbeat.get("channels", [])
                    is_connected = bool(current_joined_channels) # Connected if in at least one channel
                    bot_tts_status = heartbeat.get("tts_enabled", False)
                    current_uptime_seconds = heartbeat.get("uptime", 0)
                    bot_pid = heartbeat.get("pid")
                else:
                    if verbose_logging: # Use the new config setting
                        app.logger.warning(f"Heartbeat file is stale (last beat: {datetime.fromtimestamp(heartbeat_timestamp).strftime('%Y-%m-%d %H:%M:%S')}), considering bot as running but not reliably connected.")
                    # Bot is running (per is_bot_actually_running) but heartbeat is old.
                    # is_connected remains False. Uptime/TTS might be stale.
                    current_uptime_seconds = heartbeat.get("uptime", 0) # Report last known uptime
                    bot_tts_status = heartbeat.get("tts_enabled", False) # Report last known TTS status
                    bot_pid = heartbeat.get("pid")
                    # is_connected is already False, which is appropriate for stale heartbeat

            except (FileNotFoundError, json.JSONDecodeError) as e:
                if verbose_logging: # Use the new config setting
                    app.logger.warning(f"Could not read or parse bot_heartbeat.json for detailed status: {e}")
                # Bot is running, but can't get details, so is_connected remains False.
        else:
            if verbose_logging: # Use the new config setting
                app.logger.warning("Bot is running but bot_heartbeat.json not found. Cannot confirm connection status or details.")
            # Bot is running, but no heartbeat file, so is_connected remains False.

    status_details = {
        "running": bot_running,
        "connected": is_connected,  # True if running, heartbeat recent, and joined_channels is not empty
        "uptime": current_uptime_seconds,  # Expected by JS as 'uptime' (seconds)
        "tts_enabled": bot_tts_status,  # Expected by JS as 'tts_enabled' (bot's actual TTS state)
        "joined_channels": current_joined_channels,
        "pid": bot_pid,
        "timestamp": datetime.now().isoformat(),  # Timestamp of this API response
        "heartbeat_available": heartbeat_data_available, # Info if heartbeat file was read
        # "tts_enabled_webapp": _enable_tts_webapp # This is a webapp-specific setting, less critical for core bot status
    }
    
    return jsonify(status_details)

@app.route('/settings')
@require_permission(Permissions.SYSTEM_SETTINGS)
def settings_page(): 
    # Redirect streamers to their channel instead of settings page
    streamer_redirect = redirect_streamers_to_channel()
    if streamer_redirect:
        return streamer_redirect
        
    # theme = request.cookies.get("theme", "darkly") # Theme is now injected by context_processor
    bot_running = is_bot_actually_running()
    channels_data = []
    try:
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row 
        c = conn.cursor()
        c.execute("SELECT * FROM channel_configs")
        channels_data = [dict(row) for row in c.fetchall()]
        conn.close()
    except Exception as e:
        app.logger.error(f"Error fetching channels for settings page: {e}")
    
    return render_template("settings.html", channels=channels_data, bot_running=bot_running) # No need to pass theme

@app.route('/stats')
@require_permission(Permissions.DASHBOARD_STATS)
def stats_page(): 
    # Redirect streamers to their channel instead of stats page
    streamer_redirect = redirect_streamers_to_channel()
    if streamer_redirect:
        return streamer_redirect
        
    # theme = request.cookies.get("theme", "darkly") # Theme is now injected by context_processor
    return render_template("stats.html") # No need to pass theme

@app.route('/bot-control', endpoint='bot_control_page') # Corrected endpoint name based on previous fixes
@require_permission(Permissions.BOT_START)
def bot_control_page(): 
    """Render the bot control page."""
    # Redirect streamers to their channel instead of bot control page
    streamer_redirect = redirect_streamers_to_channel()
    if streamer_redirect:
        return streamer_redirect
        
    # theme = request.cookies.get("theme", "darkly") # Theme is now injected by context_processor
    bot_running_status = is_bot_actually_running() 
    return render_template("bot_control.html", bot_status={'running': bot_running_status}) # No need to pass theme

@app.route('/tts-history')
@require_permission(Permissions.TTS_HISTORY)
def tts_history_page():
    """Render the TTS history page."""
    # Redirect streamers to their channel instead of TTS history page
    streamer_redirect = redirect_streamers_to_channel()
    if streamer_redirect:
        return streamer_redirect
        
    return render_template("tts_history.html")

@app.route('/logs')
@require_permission(Permissions.SYSTEM_LOGS)
def logs_page():
    """Render the chat logs page."""
    # Redirect streamers to their channel instead of logs page
    streamer_redirect = redirect_streamers_to_channel()
    if streamer_redirect:
        return streamer_redirect

    return render_template("logs.html")

@app.route('/tts-popup')
@require_auth
def tts_popup():
    """Render TTS popup window for stream capture."""
    return render_template("tts_popup.html")

@app.route('/api/channel/<channel_name>/recent_messages')
@require_channel_access('channel_name', 'view')
def api_channel_recent_messages(channel_name):
    """Get recent bot-generated messages for a channel."""
    try:
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row

        # Get recent bot-generated messages from the channel (last 20)
        # The column is 'is_bot_response' not 'is_bot_message'
        messages = conn.execute("""
            SELECT message, timestamp, author_name
            FROM messages
            WHERE channel = ? AND is_bot_response = 1
            ORDER BY id DESC
            LIMIT 20
        """, (channel_name,)).fetchall()

        conn.close()

        # Reverse to show oldest first (chronological order)
        messages_list = [dict(row) for row in reversed(messages)]

        return jsonify({
            'success': True,
            'messages': messages_list
        })

    except Exception as e:
        app.logger.error(f"Error fetching recent messages for {channel_name}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/channel/<channel_name>/trusted_users', methods=['GET'])
@require_channel_access('channel_name', 'view')
def api_get_trusted_users(channel_name):
    """Get list of trusted users for a channel."""
    try:
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row

        channel_config = conn.execute("""
            SELECT trusted_users
            FROM channel_configs
            WHERE channel_name = ?
        """, (channel_name,)).fetchone()

        conn.close()

        if channel_config:
            trusted_users_str = channel_config['trusted_users'] or ''
            trusted_users = [u.strip() for u in trusted_users_str.split(',') if u.strip()]
            return jsonify({
                'success': True,
                'trusted_users': trusted_users
            })
        else:
            return jsonify({'success': False, 'error': 'Channel not found'}), 404

    except Exception as e:
        app.logger.error(f"Error fetching trusted users for {channel_name}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/channel/<channel_name>/trusted_users', methods=['POST'])
@require_channel_access('channel_name', 'edit')
def api_update_trusted_users(channel_name):
    """Update list of trusted users for a channel."""
    try:
        data = request.json
        trusted_users = data.get('trusted_users', [])

        # Validate and clean usernames
        cleaned_users = []
        for username in trusted_users:
            username = username.strip().lower()
            if username and username.isalnum() or '_' in username:
                cleaned_users.append(username)

        # Convert to comma-separated string
        trusted_users_str = ','.join(cleaned_users)

        conn = sqlite3.connect(db_file)
        conn.execute("""
            UPDATE channel_configs
            SET trusted_users = ?
            WHERE channel_name = ?
        """, (trusted_users_str, channel_name))
        conn.commit()
        conn.close()

        app.logger.info(f"Updated trusted users for {channel_name}: {cleaned_users}")

        return jsonify({
            'success': True,
            'trusted_users': cleaned_users
        })

    except Exception as e:
        app.logger.error(f"Error updating trusted users for {channel_name}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/channel/<channel_name>/last_build', methods=['GET'])
@require_channel_access('channel_name', 'view')
def api_get_last_build(channel_name):
    """Get last model build time for a channel."""
    try:
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row

        # Get most recent successful build from cache_build_log
        build_log = conn.execute("""
            SELECT timestamp, duration, success
            FROM cache_build_log
            WHERE channel_name = ? AND success = 1
            ORDER BY timestamp DESC
            LIMIT 1
        """, (channel_name,)).fetchone()

        conn.close()

        if build_log:
            return jsonify({
                'success': True,
                'timestamp': build_log['timestamp'],
                'duration': build_log['duration']
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': None,
                'duration': None
            })

    except Exception as e:
        app.logger.error(f"Error fetching last build for {channel_name}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/channel/<channel_name>')
@require_permission(Permissions.CHANNELS_VIEW)
def channel_page(channel_name):
    """Render the channel-specific dashboard page."""
    # Redirect streamers to their beta channel page instead of old interface
    user = get_current_user()
    if user and user.get('role_name') == 'streamer':
        from utils.user_db import UserDatabase
        user_db = UserDatabase('users.db')
        user_channels = user_db.get_user_channels_from_db(user['user_id'])
        if user_channels and channel_name in user_channels:
            # Redirect to beta channel page for their channel
            return redirect(f'/beta/channel/{channel_name}')
        else:
            # Redirect to their assigned channel if trying to access wrong channel
            return redirect_streamers_to_channel() or redirect('/beta')
    
    # Validate channel exists in our database
    try:
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM channel_configs WHERE channel_name = ?", (channel_name,))
        channel_config = c.fetchone()
        conn.close()
        
        if not channel_config:
            # Channel doesn't exist in our database
            return render_template("404.html", error_message=f"Channel '{channel_name}' not found in bot configuration"), 404
        
        # Convert to dict for template
        channel_data = dict(channel_config)
        channel_data['name'] = channel_name
        
        # Get current bot status for this channel
        bot_running = is_bot_actually_running()
        
        # Check if bot is currently connected to this channel
        currently_connected = False
        if bot_running and os.path.exists("bot_heartbeat.json"):
            try:
                with open("bot_heartbeat.json", "r") as f:
                    heartbeat = json.load(f)
                if time.time() - heartbeat.get("timestamp", 0) < 120:  # Within 2 minutes
                    heartbeat_channels = [ch.lstrip('#') for ch in heartbeat.get("channels", [])]
                    currently_connected = channel_name in heartbeat_channels
            except (FileNotFoundError, json.JSONDecodeError):
                pass
        
        channel_data['currently_connected'] = currently_connected
        channel_data['bot_running'] = bot_running
        
        return render_template("channel_page.html", channel=channel_data)
        
    except Exception as e:
        app.logger.error(f"Error loading channel page for {channel_name}: {e}")
        return render_template("500.html", error_message=f"Error loading channel data: {str(e)}"), 500

@app.route('/api/channels')
@require_auth
def api_channels_list():
    try:
        # SECURITY: Filter channels by user role
        user = get_current_user()
        user_role = user.get('role_name')

        # PERFORMANCE OPTIMIZATION: Use optimized database queries with connection pooling
        # Single query to get both channel configs and last activity in one operation
        if user_role in ['admin', 'super_admin']:
            # Admins see all channels
            optimized_query = """
            SELECT
                cc.*,
                COALESCE(msg_stats.last_activity_ts, NULL) as last_activity,
                COALESCE(msg_stats.message_count, 0) as message_count
            FROM channel_configs cc
            LEFT JOIN (
                SELECT
                    channel,
                    MAX(timestamp) as last_activity_ts,
                    COUNT(*) as message_count
                FROM messages
                GROUP BY channel
            ) msg_stats ON cc.channel_name = msg_stats.channel
            ORDER BY cc.channel_name
            """
            query_params = ()
        else:
            # Streamers only see their own channel
            managed_channel = user.get('managed_channel')
            if not managed_channel:
                return jsonify([])  # No channel to manage

            optimized_query = """
            SELECT
                cc.*,
                COALESCE(msg_stats.last_activity_ts, NULL) as last_activity,
                COALESCE(msg_stats.message_count, 0) as message_count
            FROM channel_configs cc
            LEFT JOIN (
                SELECT
                    channel,
                    MAX(timestamp) as last_activity_ts,
                    COUNT(*) as message_count
                FROM messages
                GROUP BY channel
            ) msg_stats ON cc.channel_name = msg_stats.channel
            WHERE cc.channel_name = ?
            ORDER BY cc.channel_name
            """
            query_params = (managed_channel,)

        # Use pooled connection for better performance
        raw_channels_data = execute_query_sync(optimized_query, query_params, db_file)

        # Get heartbeat data (file I/O optimization can be added later)
        heartbeat_joined_channels = []
        bot_is_running_for_heartbeat = is_bot_actually_running()

        if bot_is_running_for_heartbeat and os.path.exists("bot_heartbeat.json"):
            try:
                with open("bot_heartbeat.json", "r") as f:
                    heartbeat = json.load(f)
                if time.time() - heartbeat.get("timestamp", 0) < 120: # Within 2 minutes
                    heartbeat_joined_channels = [ch.lstrip('#') for ch in heartbeat.get("channels", [])]
                else:
                    app.logger.warning("Heartbeat file is stale for /api/channels, 'currently_connected' status might be inaccurate.")
            except (FileNotFoundError, json.JSONDecodeError) as e:
                app.logger.warning(f"Could not read or parse bot_heartbeat.json for /api/channels: {e}")
        elif bot_is_running_for_heartbeat:
            app.logger.warning("Bot is running but bot_heartbeat.json not found for /api/channels.")

        # Fetch model details (includes line_count) - cached for better performance
        model_details_list = markov_handler.get_available_models()
        model_info_map = {model['name']: model for model in model_details_list if isinstance(model, dict) and 'name' in model}

        # PERFORMANCE OPTIMIZATION: Streamlined data processing
        channels_data_adapted = []
        for raw_channel_config in raw_channels_data:
            channel_item = dict(raw_channel_config)
            db_channel_name = raw_channel_config.get('channel_name')

            # Set channel name
            if db_channel_name and 'name' not in channel_item:
                channel_item['name'] = db_channel_name
            elif 'name' not in channel_item and not db_channel_name:
                channel_item['name'] = 'Unknown Channel'
                app.logger.warning(f"Channel config row missing 'channel_name': {raw_channel_config}")

            # Set join configuration
            join_channel_val = raw_channel_config.get('join_channel', 0)
            channel_item['configured_to_join'] = bool(join_channel_val)
        
            # Check current connection status
            current_channel_name_for_check = channel_item.get('name', '').lstrip('#')
            channel_item['currently_connected'] = current_channel_name_for_check in heartbeat_joined_channels
        
            # Set TTS status
            if 'tts_enabled' in raw_channel_config:
                 channel_item['tts_enabled'] = bool(raw_channel_config.get('tts_enabled', 0))

            # PERFORMANCE: Use optimized data from JOIN query instead of separate lookups
            # Message count from database (more accurate than model line count)
            channel_item['messages_sent'] = raw_channel_config.get('message_count', 0)
            
            # Last activity already included in query result
            channel_item['last_activity'] = raw_channel_config.get('last_activity')
            
            # Supplement with model data if available (for line count comparison)
            model_data = model_info_map.get(current_channel_name_for_check, {})
            if model_data.get('line_count', 0) > channel_item['messages_sent']:
                # Use model line count if it's higher (more recent)
                channel_item['messages_sent'] = model_data.get('line_count', 0)
        
            channels_data_adapted.append(channel_item)
        
        return jsonify(channels_data_adapted)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/get-channel-settings/<channel_name>')
@require_channel_access('channel_name', 'view')
def get_channel_settings_route(channel_name): 
    try:
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM channel_configs WHERE channel_name = ?", (channel_name,))
        row = c.fetchone()
        conn.close()
        if row:
            return jsonify(dict(row))
        else:
            return jsonify({"error": "Channel not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/update-channel-settings', methods=['POST'])
@require_channel_access('channel_name', 'edit')
def update_channel_settings_route():
    try:
        data = request.json
        channel_name = data.get('channel_name') if data else None

        if not channel_name:
            return jsonify({"success": False, "message": "Channel name required"}), 400

        # SECURITY FIX: Whitelist allowed column names to prevent SQL injection
        ALLOWED_COLUMNS = {
            'tts_enabled', 'voice_enabled', 'join_channel', 'owner',
            'trusted_users', 'ignored_users', 'use_general_model',
            'lines_between_messages', 'time_between_messages',
            'voice_preset', 'bark_model', 'tts_delay_enabled'
        }

        # Premium-only fields (TTS features)
        PREMIUM_FIELDS = {
            'tts_enabled', 'bark_model', 'voice_preset', 'tts_delay_enabled'
        }

        # Filter and validate input fields
        fields_to_update = {}
        for k, v in data.items():
            if k != 'channel_name':
                if k not in ALLOWED_COLUMNS:
                    return jsonify({
                        "success": False,
                        "message": f"Invalid field: {k}. Allowed fields: {', '.join(sorted(ALLOWED_COLUMNS))}"
                    }), 400
                fields_to_update[k] = v

        if not fields_to_update:
            return jsonify({"success": False, "message": "No valid fields to update"}), 400

        # Premium check: Verify subscription for premium features
        # Super admins can bypass this check
        premium_fields_being_updated = PREMIUM_FIELDS & fields_to_update.keys()
        if premium_fields_being_updated:
            user = get_current_user()
            if user and user['role_name'] != 'super_admin':
                conn = sqlite3.connect(db_file)
                c = conn.cursor()

                # Get the user_id for this channel
                c.execute("SELECT user_id FROM channel_configs WHERE channel_name = ?", (channel_name,))
                config = c.fetchone()

                if config and config[0]:
                    # Check subscription tier
                    c.execute("SELECT subscription_tier FROM users WHERE user_id = ?", (config[0],))
                    user_result = c.fetchone()

                    if not user_result or user_result[0] != 'premium':
                        conn.close()
                        premium_field_list = ', '.join(sorted(premium_fields_being_updated))
                        return jsonify({
                            "success": False,
                            "message": f"Premium subscription required to modify: {premium_field_list}. TTS is a Premium feature."
                        }), 403
                else:
                    # No user_id linked to channel
                    conn.close()
                    return jsonify({
                        "success": False,
                        "message": "Premium subscription required for TTS features. Please link your Twitch account to upgrade."
                    }), 403

                conn.close()

        # Additional input validation
        validation_error = validate_channel_config_fields(fields_to_update)
        if validation_error:
            return jsonify({"success": False, "message": validation_error}), 400

        # Safe to build query now since column names are validated
        set_clause = ", ".join([f"{key} = ?" for key in fields_to_update.keys()])
        params = list(fields_to_update.values()) + [channel_name]

        conn = sqlite3.connect(db_file)
        c = conn.cursor()
        c.execute(f"UPDATE channel_configs SET {set_clause} WHERE channel_name = ?", params)
        
        if c.rowcount == 0:
            conn.close()
            return jsonify({"success": False, "message": "Channel not found"}), 404
            
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Settings updated successfully."})
        
    except sqlite3.Error as e:
        return jsonify({"success": False, "message": f"Database error: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500

@app.route('/add-channel', methods=['POST'])
@require_role('admin')
def add_channel_route(): 
    try:
        data = request.json
        channel_name = data.get('channel_name')
        if not channel_name: return jsonify({"success": False, "message": "Channel name required"}), 400

        conn = sqlite3.connect(db_file)
        c = conn.cursor()
        c.execute("SELECT 1 FROM channel_configs WHERE channel_name = ?", (channel_name,))
        if c.fetchone():
            conn.close()
            return jsonify({"success": False, "message": "Channel already exists"}), 400
        
        fields = {
            'channel_name': channel_name,
            'tts_enabled': data.get('tts_enabled', 0),
            'voice_enabled': data.get('voice_enabled', 0),
            'join_channel': data.get('join_channel', 1),
            'owner': data.get('owner', channel_name),
            'trusted_users': data.get('trusted_users', ''),
            'ignored_users': data.get('ignored_users', ''),
            'use_general_model': data.get('use_general_model', 1),
            'lines_between_messages': data.get('lines_between_messages', 100),
            'time_between_messages': data.get('time_between_messages', 0),
            'voice_preset': data.get('voice_preset', 'v2/en_speaker_0'), 
            'bark_model': data.get('bark_model', 'regular') 
        }
        
        columns = ', '.join(fields.keys())
        placeholders = ', '.join(['?'] * len(fields))
        values = tuple(fields.values())
        
        c.execute(f"INSERT INTO channel_configs ({columns}) VALUES ({placeholders})", values)
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Channel added."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/delete-channel', methods=['POST'])
@require_role('admin')
def delete_channel_route(): 
    try:
        data = request.json
        channel_name = data.get('channel_name')
        if not channel_name: return jsonify({"success": False, "message": "Channel name required"}), 400
        
        conn = sqlite3.connect(db_file)
        c = conn.cursor()
        c.execute("DELETE FROM channel_configs WHERE channel_name = ?", (channel_name,))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Channel deleted."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/list-voices')
@require_auth
def list_voices_route(): 
    try:
        voices_dir = "voices"
        if not os.path.exists(voices_dir): os.makedirs(voices_dir) 
        voices = [f for f in os.listdir(voices_dir) if f.endswith('.npz')]
        return jsonify({"voices": voices})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/rebuild-voice-index') 
def rebuild_voice_index_route(): 
    return jsonify({"success": True, "message": "Voice index rebuild (placeholder) successful."})

@app.route('/get-latest-tts')
def get_latest_tts_route(): 
    try:
        last_id = int(request.args.get('last_id', '0'))
        conn = sqlite3.connect(db_file)
        c = conn.cursor()
        c.execute("SELECT message_id, file_path, message, timestamp FROM tts_logs WHERE message_id > ? ORDER BY message_id DESC LIMIT 10", (last_id,))
        rows = c.fetchall()
        conn.close()
        
        files_data = [{"id": r[0], "file": r[1], "message": r[2], "timestamp": r[3]} for r in rows]
        current_max_id = files_data[0]['id'] if files_data else last_id
        
        return jsonify({"files": files_data, "last_id": current_max_id})
    except Exception as e:
        app.logger.error(f"Error in /get-latest-tts: {e}")
        return jsonify({"error": str(e), "files": []}), 500

@app.route('/static/outputs/<path:filename>')
def serve_tts_output(filename):
    directory = os.path.join(app.root_path, 'static', 'outputs')
    return send_from_directory(directory, filename)


@app.route('/set-theme/<theme_name>') 
def set_theme_route(theme_name): 
    # Create a JSON response
    response_data = {"success": True, "theme": theme_name, "message": f"Theme set to {theme_name}"}
    response = make_response(jsonify(response_data))
    
    # Set the cookie on this response
    # Added httponly=True and samesite='Lax' for better security
    response.set_cookie('theme', theme_name, max_age=60*60*24*365, httponly=True, samesite='Lax')
    
    return response

@app.errorhandler(404)
def page_not_found_error(e): 
    # theme = request.cookies.get('theme', 'darkly') # Theme is now injected by context_processor
    return render_template('404.html', error_message="404: Page Not Found"), 404 # No need to pass theme

@app.errorhandler(500)
def server_error_handler(e): 
    app.logger.error(f"Server Error: {e}\n{traceback.format_exc()}")
    # theme = request.cookies.get('theme', 'darkly') # Theme is now injected by context_processor
    return render_template('500.html', error_message="500: Internal Server Error"), 500 # No need to pass theme

@app.route('/api/recent-tts')
@require_auth
def api_recent_tts():
    try:
        # SECURITY: Filter by user's channel for non-admins
        user = get_current_user()
        user_role = user.get('role_name')

        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row # To access columns by name
        c = conn.cursor()
        # Fetch necessary fields, including channel and voice_preset
        # Order by message_id DESC as it's likely the primary key and indicates insertion order.

        if user_role not in ['admin', 'super_admin']:
            # Streamers can only see their own channel's TTS
            managed_channel = user.get('managed_channel')
            if not managed_channel:
                conn.close()
                return jsonify([])
            c.execute("""
                SELECT message_id, channel, file_path, message, timestamp, voice_preset
                FROM tts_logs
                WHERE channel = ?
                ORDER BY message_id DESC
                LIMIT 10
            """, (managed_channel,))
        else:
            # Admins see all recent TTS
            c.execute("""
                SELECT message_id, channel, file_path, message, timestamp, voice_preset
                FROM tts_logs
                ORDER BY message_id DESC
                LIMIT 10
            """)
        rows = c.fetchall()
        conn.close()
        
        files_data = [{
            "id": row["message_id"], 
            "channel": row["channel"],
            "file_path": row["file_path"], 
            "message": row["message"], 
            "timestamp": row["timestamp"], # Ensure this is in a format JS Date() can parse (ISO 8601 ideally)
            "voice_preset": row["voice_preset"]
        } for row in rows]
        
        return jsonify(files_data)
    except Exception as e:
        app.logger.error(f"Error in /api/recent-tts: {e}")
        return jsonify({"error": str(e), "files": []}), 500

@app.route('/api/tts-stats')
@require_auth
def api_tts_stats():
    try:
        conn = sqlite3.connect(db_file)
        c = conn.cursor()

        now = datetime.now()
        today_start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Assuming 'timestamp' in tts_logs is stored in a format like 'YYYY-MM-DD HH:MM:SS'
        # or any format that allows lexicographical comparison for dates.
        # If timestamp is Unix epoch, adjustments would be needed here.
        # For simplicity, assuming string format that works with direct comparison.
        # A more robust way would be to convert DB timestamp to datetime objects or use SQL date functions.
        today_start_str = today_start_dt.strftime('%Y-%m-%d %H:%M:%S') 
        
        # This query assumes 'timestamp' is text and can be compared.
        # If 'timestamp' is a Unix timestamp (numeric), the query should be:
        # c.execute("SELECT COUNT(*) FROM tts_logs WHERE timestamp >= ?", (today_start_dt.timestamp(),))
        c.execute("SELECT COUNT(*) FROM tts_logs WHERE timestamp >= ?", (today_start_str,))
        today_count = c.fetchone()[0]

        seven_days_ago_dt = now - timedelta(days=7)
        seven_days_ago_str = seven_days_ago_dt.strftime('%Y-%m-%d %H:%M:%S')
        # Similar consideration for timestamp format here
        c.execute("SELECT COUNT(*) FROM tts_logs WHERE timestamp >= ?", (seven_days_ago_str,))
        week_count = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM tts_logs")
        total_count = c.fetchone()[0]
        
        conn.close()
        return jsonify({"today": today_count, "week": week_count, "total": total_count})
    except Exception as e:
        app.logger.error(f"Error in /api/tts-stats: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "today": 0, "week": 0, "total": 0}), 500

@app.route('/api/tts-logs')
@require_auth
def api_tts_logs():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 15, type=int)
        offset = (page - 1) * per_page

        # Sorting parameters
        sort_by_input = request.args.get('sort_by', 'timestamp') # Default sort by timestamp
        sort_order_input = request.args.get('sort_order', 'desc').lower() # Default sort descending

        # Filtering parameters
        channel_filter_input = request.args.get('channel_filter', None, type=str)
        message_filter_input = request.args.get('message_filter', None, type=str)
        id_filter = request.args.get('id', None, type=str)

        # SECURITY: Filter by user's channel for non-admins
        user = get_current_user()
        user_role = user.get('role_name')

        if user_role not in ['admin', 'super_admin']:
            # Streamers can only see their own channel's TTS logs
            managed_channel = user.get('managed_channel')
            if not managed_channel:
                return jsonify({"logs": [], "page": page, "per_page": per_page, "total_items": 0, "total_pages": 0})
            # Override channel filter to user's channel
            channel_filter_input = managed_channel

        # Validate sort_order
        sort_order = "ASC" if sort_order_input == "asc" else "DESC"

        # Validate sort_by column to prevent SQL injection
        allowed_sort_columns = {
            "timestamp": "timestamp",
            "channel": "channel",
            "voice_preset": "voice_preset",
            "message": "message",
            "id": "message_id" # Allow sorting by ID if needed
        }
        sort_column = allowed_sort_columns.get(sort_by_input, "timestamp") # Default to timestamp if invalid

        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # Build WHERE clause for filtering
        where_clauses = []
        query_params = []

        if channel_filter_input:
            where_clauses.append("channel LIKE ?")
            query_params.append(f"%{channel_filter_input}%")
        
        if message_filter_input:
            where_clauses.append("message LIKE ?")
            query_params.append(f"%{message_filter_input}%")
        
        if id_filter:
            where_clauses.append("message_id = ?")
            query_params.append(id_filter)
        
        where_sql = ""
        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)

        # Get total count with filters
        count_query = f"SELECT COUNT(*) FROM tts_logs {where_sql}"
        c.execute(count_query, tuple(query_params))
        total_items = c.fetchone()[0]
        total_pages = (total_items + per_page - 1) // per_page if per_page > 0 else 0

        app.logger.debug(f"[api_tts_logs] Filters: channel='{channel_filter_input}', message='{message_filter_input}'. DB TotalItems (filtered): {total_items}, TotalPages: {total_pages}")

        # Fetch paginated and sorted data
        sort_clause = f"ORDER BY {sort_column}"
        if sort_column in ["channel", "voice_preset", "message"]:
            sort_clause += " COLLATE NOCASE"
        sort_clause += f" {sort_order}"

        # PERFORMANCE: Use cursor-based pagination instead of OFFSET for better performance
        cursor_value = request.args.get('cursor')
        
        if cursor_value and sort_column in ['message_id', 'timestamp']:
            # Cursor-based pagination for indexed columns
            if sort_order.upper() == 'DESC':
                cursor_condition = f"AND {sort_column} < ?"
            else:
                cursor_condition = f"AND {sort_column} > ?"
            
            data_query = f"""
                SELECT message_id, channel, file_path, message, timestamp, voice_preset 
                FROM tts_logs 
                {where_sql} {cursor_condition}
                {sort_clause}
                LIMIT ?
            """
            final_query_params = tuple(query_params) + (cursor_value, per_page)
        else:
            # Fallback to OFFSET for non-indexed sorts or first page
            data_query = f"""
                SELECT message_id, channel, file_path, message, timestamp, voice_preset 
                FROM tts_logs 
                {where_sql}
                {sort_clause}
                LIMIT ? OFFSET ?
            """
            final_query_params = tuple(query_params) + (per_page, offset)
        
        app.logger.debug(f"[api_tts_logs] Executing query: {data_query} with params {final_query_params}")
        c.execute(data_query, final_query_params)
        rows = c.fetchall()
        conn.close()
        app.logger.debug(f"[api_tts_logs] Fetched {len(rows)} rows from DB for page {page} with sort: {sort_column} {sort_order}.")
        
        logs_data = [{
            "id": row["message_id"], 
            "channel": row["channel"],
            "file_path": row["file_path"], 
            "message": row["message"], 
            "timestamp": row["timestamp"],
            "voice_preset": row["voice_preset"]
        } for row in rows]
        
        # PERFORMANCE: Include cursor information for efficient pagination
        response_data = {
            "logs": logs_data,
            "page": page,
            "per_page": per_page,
            "total_items": total_items,
            "total_pages": total_pages
        }
        
        # Add cursor for next page if using cursor-based pagination
        if logs_data and sort_column in ['message_id', 'timestamp']:
            last_row = logs_data[-1]
            if sort_column == 'message_id':
                response_data["next_cursor"] = last_row["id"]
            elif sort_column == 'timestamp':
                response_data["next_cursor"] = last_row["timestamp"]
        
        return jsonify(response_data)
    except Exception as e:
        app.logger.error(f"Error in /api/tts-logs: {e}", exc_info=True)
        return jsonify({"error": str(e), "logs": [], "total_pages": 0, "total_items": 0}), 500

@app.route('/get-stats')
@require_auth
def get_stats_route():
    try:
        app.logger.debug("Attempting to connect to database for /get-stats")
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        app.logger.debug("Executing query: SELECT channel_name, use_general_model, lines_between_messages FROM channel_configs")
        c.execute("SELECT channel_name, use_general_model, lines_between_messages FROM channel_configs")
        config_rows = c.fetchall()
        conn.close()
        app.logger.debug(f"Fetched {len(config_rows)} channel_configs rows.")

        stats_data = []
        
        available_models_info = None
        try:
            app.logger.debug("Checking and calling markov_handler.get_available_models()")
            if markov_handler and hasattr(markov_handler, 'get_available_models') and callable(markov_handler.get_available_models):
                available_models_info = markov_handler.get_available_models()
                app.logger.debug(f"markov_handler.get_available_models() returned: {type(available_models_info)} - {str(available_models_info)[:200]}")
            else:
                app.logger.error("markov_handler is not properly initialized or get_available_models is not callable. Proceeding with no model info.")
                # available_models_info remains None
        except Exception as mh_exc:
            app.logger.error(f"Exception during call to markov_handler.get_available_models(): {mh_exc}", exc_info=True)
            # Proceed with available_models_info as None, which is handled by the logic below

        model_info_map = {}
        if isinstance(available_models_info, list): # Check if it's a list
            if not available_models_info: # Handle empty list
                app.logger.debug("markov_handler.get_available_models() returned an empty list.")
            else: # Process non-empty list
                for item_index, item in enumerate(available_models_info):
                    if isinstance(item, dict):
                        model_name = item.get('name')
                        if model_name:
                            model_info_map[model_name] = item
                        else:
                            app.logger.warning(f"Item at index {item_index} in available_models_info is a dict but missing 'name' key: {str(item)[:100]}")
                    elif isinstance(item, str):
                        model_info_map[item] = {'name': item} # Basic info for string items
                    else:
                        app.logger.warning(f"Unexpected type for item at index {item_index} in available_models_info: {type(item)}. Item: {str(item)[:100]}")
        elif available_models_info is None:
             app.logger.warning("markov_handler.get_available_models() returned None or failed. Proceeding with empty model_info_map.")
        else: # Not a list and not None
             app.logger.warning(f"markov_handler.get_available_models() returned non-list type: {type(available_models_info)}. Proceeding with empty model_info_map.")
        
        app.logger.debug(f"Processing {len(config_rows)} config_rows with model_info_map: {str(model_info_map)[:200]}")
        for i, row in enumerate(config_rows):
            try:
                channel_name = row['channel_name'] 
                use_general_model_val = row['use_general_model']
                lines_between_messages_val = row['lines_between_messages']
                
                model_data = model_info_map.get(channel_name, {}) # model_data now contains more details
                
                # Correct log file name and check existence
                log_filename = f"{channel_name}.txt" 
                log_file_path = os.path.join('logs', log_filename)
                log_file_exists = os.path.exists(log_file_path)

                # cache_file filename comes directly from model_data if model exists
                cache_filename_from_model = model_data.get('cache_file') # This is just the filename like 'channel_model.json'
                
                stats_data.append({
                    "name": channel_name,
                    "cache_file": cache_filename_from_model, 
                    "log_file": log_filename if log_file_exists else None,
                    "cache_size_str": model_data.get('cache_size_str', '0 B'), # For individual display
                    "cache_size_bytes": model_data.get('cache_size_bytes', 0), # For summation
                    "line_count": model_data.get('line_count', 0),
                    "use_general_model": bool(use_general_model_val),
                    "lines_between_messages": lines_between_messages_val
                })
            except KeyError as ke:
                app.logger.error(f"KeyError processing row {i} in /get-stats: {ke}. Row data: {dict(row) if row else 'Row is None'}", exc_info=True)
                continue 
            except Exception as row_exc:
                app.logger.error(f"Exception processing row {i} ('{row['channel_name'] if row and 'channel_name' in row else 'UnknownChannel'}') in /get-stats: {row_exc}", exc_info=True)
                continue

        # Add general model if it exists and isn't already covered
        # Add general model if it exists in model_info_map
        # The name "general_markov" is what get_available_models should return for it
        if "general_markov" in model_info_map: 
            general_model_data = model_info_map["general_markov"]
            # Check if it wasn't already added (e.g. if a channel_config was named 'general_markov')
            if not any(s['name'] == "general_markov" for s in stats_data): 
                stats_data.append({
                    "name": "general_markov",
                    "cache_file": general_model_data.get('cache_file'), 
                    "log_file": None, 
                    "cache_size_str": general_model_data.get('cache_size_str', '0 B'), # For individual display
                    "cache_size_bytes": general_model_data.get('cache_size_bytes', 0), # For summation
                    "line_count": general_model_data.get('line_count', 0),
                    "use_general_model": True, # General model is always "using" itself
                    "lines_between_messages": 0 # Not applicable
                })
        
        app.logger.debug(f"Successfully prepared stats_data for {len(stats_data)} items for /get-stats.")
        return jsonify(stats_data)
    except Exception as e:
        app.logger.error(f"Error in /get-stats: {e}", exc_info=True)
        return jsonify({"error": str(e), "data": []}), 500

@app.route('/api/cache-build-performance')
def api_cache_build_performance():
    retries = 0
    max_retries = 1
    while retries <= max_retries:
        try:
            conn = sqlite3.connect(db_file)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            # Fetch data from cache_build_log table
            c.execute("""
                SELECT channel_name as channel, timestamp, duration, success 
                FROM cache_build_log 
                ORDER BY timestamp DESC 
                LIMIT 20 
            """)
            build_times = [dict(row) for row in c.fetchall()]
            conn.close()
            return jsonify(build_times)
        except sqlite3.OperationalError as oe:
            if "no such table: cache_build_log" in str(oe) and retries < max_retries:
                app.logger.warning(f"/api/cache-build-performance: 'cache_build_log' table not found. Attempting to run ensure_db_setup. Retry {retries + 1}/{max_retries}")
                ensure_db_setup(db_file) # Attempt to create the table
                retries += 1
                time.sleep(0.1) # Small delay before retrying
                continue # Retry the loop
            else:
                app.logger.error(f"Error in /api/cache-build-performance after retries or for other OperationalError: {oe}\n{traceback.format_exc()}")
                return jsonify({"error": str(oe), "data": []}), 500
        except Exception as e:
            app.logger.error(f"Error in /api/cache-build-performance: {e}\n{traceback.format_exc()}")
            return jsonify({"error": str(e), "data": []}), 500
    # If loop finishes due to max_retries exceeded
    app.logger.error(f"/api/cache-build-performance: Failed to access 'cache_build_log' after {max_retries} retries.")
    return jsonify({"error": "Failed to access cache build log data after setup attempt.", "data": []}), 500

@app.route('/view-file/<file_type>/<path:filename>')
def view_file(file_type, filename):
    base_dir = None
    if file_type == 'logs':
        base_dir = 'logs'
    elif file_type == 'cache':
        base_dir = 'cache'
    else:
        return "Invalid file type specified.", 400

    # Basic security: prevent directory traversal
    if '..' in filename or filename.startswith('/'):
        return "Invalid filename.", 400

    file_path = os.path.join(base_dir, filename)
    
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        return f"File not found: {filename}", 404
        
    try:
        # For JSON cache files, pretty print. For logs, serve as plain text.
        if file_type == 'cache' and filename.endswith('.json'):
            with open(file_path, 'r', encoding='utf-8') as f:
                content = json.load(f)
            response_content = json.dumps(content, indent=2)
            mimetype = 'application/json'
        else: # Assuming log files are plain text
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                response_content = f.read()
            mimetype = 'text/plain'
        
        response = make_response(response_content)
        response.mimetype = mimetype
        return response
    except Exception as e:
        app.logger.error(f"Error serving file {file_path}: {e}")
        return "Error reading file.", 500

@app.route('/api/bot-response-stats')
def api_bot_response_stats():
    try:
        conn = sqlite3.connect(db_file)
        c = conn.cursor()
        # Assuming messages in the 'messages' table are bot responses
        c.execute("SELECT COUNT(*) FROM messages")
        total_responses = c.fetchone()[0]
        conn.close()
        return jsonify({"total_responses": total_responses})
    except Exception as e:
        app.logger.error(f"Error in /api/bot-response-stats: {e}", exc_info=True)
        return jsonify({"error": str(e), "total_responses": 0}), 500

@app.route('/api/channel/<channel_name>/toggle-join', methods=['POST'])
@require_channel_access('channel_name', 'edit')
def toggle_channel_join_route(channel_name):
    try:
        if not re.match(r"^[a-zA-Z0-9_]{1,25}$", channel_name):
            return jsonify({"success": False, "message": "Invalid channel name format"}), 400

        conn = sqlite3.connect(db_file)
        c = conn.cursor()
        c.execute("SELECT join_channel FROM channel_configs WHERE channel_name = ?", (channel_name,))
        row = c.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "Channel not found"}), 404
        
        current_status = row[0]
        new_status = 1 if current_status == 0 else 0
        
        c.execute("UPDATE channel_configs SET join_channel = ? WHERE channel_name = ?", (new_status, channel_name))
        conn.commit()
        conn.close()
        
        # Optionally, trigger bot to join/leave if running (bot handles this via periodic check)
        # For immediate effect, a mechanism to notify the bot would be needed.
        
        return jsonify({"success": True, "message": f"Channel {channel_name} {'enabled' if new_status else 'disabled'}.", "new_status": new_status})
    except Exception as e:
        app.logger.error(f"Error toggling join for {channel_name}: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/channel/<channel_name>/toggle-tts', methods=['POST'])
@require_channel_access('channel_name', 'edit')
def toggle_channel_tts_route(channel_name):
    try:
        if not re.match(r"^[a-zA-Z0-9_]{1,25}$", channel_name):
            return jsonify({"success": False, "message": "Invalid channel name format"}), 400

        conn = sqlite3.connect(db_file)
        c = conn.cursor()

        # Premium check: TTS is a Premium feature
        # Super admins can bypass this check
        user = get_current_user()
        if user and user['role_name'] != 'super_admin':
            # Get the user_id for this channel
            c.execute("SELECT user_id FROM channel_configs WHERE channel_name = ?", (channel_name,))
            config = c.fetchone()

            if config and config[0]:
                # Check subscription tier
                c.execute("SELECT subscription_tier FROM users WHERE user_id = ?", (config[0],))
                user_result = c.fetchone()

                if not user_result or user_result[0] != 'premium':
                    conn.close()
                    return jsonify({
                        "success": False,
                        "message": "TTS is a Premium feature. Upgrade to Premium to unlock TTS."
                    }), 403
            else:
                # No user_id linked to channel - cannot use TTS
                conn.close()
                return jsonify({
                    "success": False,
                    "message": "TTS is a Premium feature. Please link your Twitch account to upgrade."
                }), 403

        c.execute("SELECT tts_enabled FROM channel_configs WHERE channel_name = ?", (channel_name,))
        row = c.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "Channel not found"}), 404

        current_status = row[0]
        new_status = 1 if current_status == 0 else 0

        c.execute("UPDATE channel_configs SET tts_enabled = ? WHERE channel_name = ?", (new_status, channel_name))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": f"TTS for {channel_name} {'enabled' if new_status else 'disabled'}.", "new_status": new_status})
    except Exception as e:
        app.logger.error(f"Error toggling TTS for {channel_name}: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/channel/trusted-users', methods=['POST'])
@require_channel_access('channel_name', 'edit')
def manage_trusted_users():
    """Add or remove trusted users for a channel"""
    try:
        data = request.json
        channel_name = data.get('channel_name')
        username = data.get('username', '').strip()
        action = data.get('action')  # 'add' or 'remove'
        
        if not channel_name:
            return jsonify({"success": False, "message": "Channel name required"}), 400
        
        if not username:
            return jsonify({"success": False, "message": "Username required"}), 400
            
        if action not in ['add', 'remove']:
            return jsonify({"success": False, "message": "Action must be 'add' or 'remove'"}), 400
        
        # Validate username format
        if not re.match(r"^[a-zA-Z0-9_-]{1,25}$", username):
            return jsonify({"success": False, "message": "Invalid username format"}), 400
        
        # Validate channel name format
        if not re.match(r"^[a-zA-Z0-9_]{1,25}$", channel_name):
            return jsonify({"success": False, "message": "Invalid channel name format"}), 400
        
        conn = sqlite3.connect(db_file)
        c = conn.cursor()
        
        # Get current trusted users
        c.execute("SELECT trusted_users FROM channel_configs WHERE channel_name = ?", (channel_name,))
        row = c.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "Channel not found"}), 404
        
        current_trusted = row[0] or ""
        trusted_list = [user.strip() for user in current_trusted.split(',') if user.strip()]
        
        if action == 'add':
            if username not in trusted_list:
                trusted_list.append(username)
                message = f"Added {username} as trusted user"
            else:
                conn.close()
                return jsonify({"success": False, "message": f"{username} is already a trusted user"}), 400
        
        elif action == 'remove':
            if username in trusted_list:
                trusted_list.remove(username)
                message = f"Removed {username} from trusted users"
            else:
                conn.close()
                return jsonify({"success": False, "message": f"{username} is not a trusted user"}), 400
        
        # Update database
        new_trusted = ','.join(trusted_list)
        c.execute("UPDATE channel_configs SET trusted_users = ? WHERE channel_name = ?", (new_trusted, channel_name))
        conn.commit()
        conn.close()
        
        return jsonify({"success": True, "message": message, "trusted_users": trusted_list})
        
    except Exception as e:
        app.logger.error(f"Error managing trusted users: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/system-logs')
@require_permission(Permissions.SYSTEM_LOGS)
def api_system_logs():
    try:
        log_file_path = logger.APP_LOG_FILE # Use the path from the logger instance
        if not os.path.exists(log_file_path):
            return jsonify({"logs": ["Log file not found."]})
        
        lines = []
        with open(log_file_path, 'r', encoding='utf-8') as f:
            # Read last N lines (e.g., 200)
            # This is a simple way, for very large files, more efficient methods exist
            all_lines = f.readlines()
            lines = all_lines[-200:] # Get the last 200 lines
        return jsonify({"logs": [line.strip() for line in lines]})
    except Exception as e:
        app.logger.error(f"Error reading system logs: {e}")
        return jsonify({"logs": [f"Error reading logs: {str(e)}"]}), 500

@app.route('/api/chat-logs')
@require_auth
def api_chat_logs():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        channel_filter = request.args.get('channel', None, type=str)
        offset = (page - 1) * per_page

        # SECURITY: Filter by user's channel for non-admins
        user = get_current_user()
        user_role = user.get('role_name')

        if user_role not in ['admin', 'super_admin']:
            # Streamers can only see their own channel's logs
            managed_channel = user.get('managed_channel')
            if not managed_channel:
                return jsonify({"logs": [], "page": page, "per_page": per_page, "total_items": 0, "total_pages": 0})
            # Override channel filter to user's channel
            channel_filter = managed_channel

        # --- REAL DATABASE LOGIC ---
        try:
            conn = sqlite3.connect(db_file)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            base_query = "FROM messages"
            count_query = "SELECT COUNT(*) "
            # Select twitch_message_id as id for frontend compatibility if needed, or just use it as twitch_message_id
            data_query = "SELECT twitch_message_id AS id, timestamp, channel, author_name AS username, message, is_bot_response "

            conditions = []  # Show ALL messages including bot responses
            params = []

            if channel_filter:
                conditions.append("channel = ?")
                params.append(channel_filter)

            if conditions:
                base_query += " WHERE " + " AND ".join(conditions)

            # Get total count
            c.execute(count_query + base_query, params)
            total_items = c.fetchone()[0]
            total_pages = (total_items + per_page - 1) // per_page if per_page > 0 else 0

            # PERFORMANCE: Use cursor-based pagination for better performance
            cursor_value = request.args.get('cursor')
            order_clause = " ORDER BY timestamp DESC"
            
            if cursor_value:
                # Cursor-based pagination using timestamp
                cursor_condition = " AND timestamp < ?"
                final_query = data_query + base_query + cursor_condition + order_clause + " LIMIT ?"
                final_params = tuple(params) + (cursor_value, per_page)
            else:
                # Fallback to OFFSET for first page
                final_query = data_query + base_query + order_clause + " LIMIT ? OFFSET ?"
                final_params = tuple(params) + (per_page, offset)
            
            c.execute(final_query, final_params)
            log_rows = c.fetchall()
            conn.close()

            logs_data = [dict(row) for row in log_rows]
            
            # PERFORMANCE: Include cursor for efficient pagination
            response_data = {
                "logs": logs_data,
                "page": page,
                "per_page": per_page,
                "total_items": total_items,
                "total_pages": total_pages
            }
            
            # Add cursor for next page
            if logs_data:
                response_data["next_cursor"] = logs_data[-1]["timestamp"]
            
            return jsonify(response_data)
        except sqlite3.OperationalError as oe:
            app.logger.error(f"Database schema error for chat logs: {oe}. The 'messages' table might be missing an 'author_name' column or 'message_authors' table, or 'is_bot_response'.")
            return jsonify({
                "error": "Database schema error. Chat logs might be unavailable. Please check server logs.",
                "logs": [], "total_pages": 0, "total_items": 0
            }), 500
        app.logger.error(f"SQLite operational error in /api/chat-logs: {oe}", exc_info=True)
        return jsonify({"error": str(oe), "logs": [], "total_pages": 0, "total_items": 0}), 500
    except Exception as e:
        app.logger.error(f"Error in /api/chat-logs: {e}", exc_info=True)
        return jsonify({"error": str(e), "logs": [], "total_pages": 0, "total_items": 0}), 500

@app.route('/new-audio-notification', methods=['POST'])
def new_audio_notification():
    # SECURITY: Only allow requests from localhost (internal TTS processor)
    if request.remote_addr not in ['127.0.0.1', 'localhost', '::1']:
        app.logger.warning(f"[TTS NOTIFY] ⚠️ Blocked external request from {request.remote_addr}")
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    data = request.json
    app.logger.info(f"[TTS NOTIFY] 🔔 Received new audio notification: {data}")
    channel_name = data.get('channel_name')
    # message_id from the request is the tts_logs table ID (ROWID or PK)
    tts_log_id = data.get('message_id')

    if channel_name and tts_log_id is not None:
        room_name = f"channel_{channel_name}"
        event_data = {'id': tts_log_id, 'channel': channel_name}

        app.logger.info(f"[TTS NOTIFY] Emitting to room: {room_name}, event_data: {event_data}")

        # Emit an event to all connected SocketIO clients
        # The event name 'new_tts_entry' should match what clients listen for.
        socketio.emit('new_tts_entry', event_data, room=room_name)

        app.logger.info(f"[TTS NOTIFY] ✅ Emitted 'new_tts_entry' for tts_log_id: {tts_log_id}, channel: {channel_name}")
        return jsonify({"success": True, "message": "Notification emitted"}), 200
    else:
        app.logger.warning(f"[TTS NOTIFY] ⚠️ Missing channel_name or message_id. Data: {data}")
        return jsonify({"success": False, "message": "Missing channel_name or message_id"}), 400

@app.route('/api/channel/<channel_name>/stats')
@require_channel_access('channel_name', 'view')
def api_channel_stats(channel_name):
    """Get channel-specific statistics."""
    try:
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # Check if channel exists
        c.execute("SELECT * FROM channel_configs WHERE channel_name = ?", (channel_name,))
        channel_config = c.fetchone()
        if not channel_config:
            conn.close()
            return jsonify({"error": "Channel not found"}), 404
        
        # Get message count for this channel
        c.execute("SELECT COUNT(*) as total_messages FROM messages WHERE channel = ?", (channel_name,))
        message_count = c.fetchone()['total_messages']
        
        # Get today's message count
        today = datetime.now().strftime('%Y-%m-%d')
        c.execute("SELECT COUNT(*) as today_messages FROM messages WHERE channel = ? AND DATE(timestamp) = ?", (channel_name, today))
        today_count = c.fetchone()['today_messages']
        
        # Get TTS count for this channel
        c.execute("SELECT COUNT(*) as tts_count FROM tts_logs WHERE channel = ?", (channel_name,))
        tts_count = c.fetchone()['tts_count']
        
        # Get last activity
        c.execute("SELECT MAX(timestamp) as last_activity FROM messages WHERE channel = ?", (channel_name,))
        last_activity = c.fetchone()['last_activity']
        
        # Get bot response count (messages sent by the bot to this channel)
        bot_nickname = config.get('auth', 'nickname', fallback='ansvbot').lower()
        c.execute("SELECT COUNT(*) as bot_responses FROM messages WHERE channel = ? AND LOWER(author_name) = ?", (channel_name, bot_nickname))
        bot_responses = c.fetchone()['bot_responses']
        
        conn.close()
        
        # Get model info
        model_details = markov_handler.get_available_models()
        model_info = None
        for model in model_details:
            if isinstance(model, dict) and model.get('name') == channel_name:
                model_info = model
                break
        
        return jsonify({
            "channel_name": channel_name,
            "total_messages": message_count,
            "today_messages": today_count,
            "tts_count": tts_count,
            "bot_responses": bot_responses,
            "last_activity": last_activity,
            "model_info": model_info,
            "config": dict(channel_config)
        })
        
    except Exception as e:
        app.logger.error(f"Error getting channel stats for {channel_name}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/channel/<channel_name>/activity')
@require_channel_access('channel_name', 'view')
def api_channel_activity(channel_name):
    """Get recent activity for a specific channel."""
    try:
        limit = request.args.get('limit', 20, type=int)
        
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # Get recent messages
        c.execute("""
            SELECT author_name as username, message, timestamp, 'message' as type 
            FROM messages 
            WHERE channel = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        """, (channel_name, limit))
        
        messages = [dict(row) for row in c.fetchall()]
        
        # Get recent TTS entries
        c.execute("""
            SELECT message, timestamp, voice_preset, file_path, 'tts' as type
            FROM tts_logs 
            WHERE channel = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        """, (channel_name, limit))
        
        tts_entries = [dict(row) for row in c.fetchall()]
        
        conn.close()
        
        # Combine and sort by timestamp
        all_activity = messages + tts_entries
        all_activity.sort(key=lambda x: x['timestamp'], reverse=True)
        
        return jsonify({
            "channel_name": channel_name,
            "activity": all_activity[:limit],
            "total_items": len(all_activity)
        })
        
    except Exception as e:
        app.logger.error(f"Error getting channel activity for {channel_name}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/channel/<channel_name>/generate', methods=['POST'])
@require_channel_access('channel_name', 'edit')
def api_channel_generate_message(channel_name):
    """Generate a message for a specific channel."""
    try:
        data = request.get_json() or {}
        send_to_chat = data.get('send_to_chat', False)
        use_general_model = data.get('use_general_model', False)
        model_override = data.get('model_override')  # New parameter for explicit model selection
        
        # Check if channel exists
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM channel_configs WHERE channel_name = ?", (channel_name,))
        channel_config = c.fetchone()
        conn.close()
        
        if not channel_config:
            return jsonify({"error": "Channel not found"}), 404
        
        # Determine which model to use - priority: override > use_general_model param > config setting > default to channel
        if model_override:
            model_name = "general" if model_override == "general" else channel_name
            app.logger.info(f"Using model override: {model_name} for channel {channel_name}")
        elif use_general_model:
            model_name = "general"
        elif channel_config['use_general_model']:
            model_name = "general" 
        else:
            model_name = channel_name
        
        # Generate message using markov handler
        try:
            generated_message = markov_handler.generate_message(model_name)
            if not generated_message:
                return jsonify({"error": "Failed to generate message", "message": None}), 400
        except Exception as e:
            app.logger.error(f"Error generating message for channel {channel_name}: {e}")
            return jsonify({"error": f"Message generation failed: {str(e)}", "message": None}), 500
        
        # If requested, send to chat
        sent_to_chat = False
        if send_to_chat:
            try:
                bot_running = is_bot_actually_running()
                if bot_running:
                    send_message_via_pid(channel_name, generated_message)
                    sent_to_chat = True
                else:
                    app.logger.warning(f"Cannot send message to {channel_name}: bot not running")
            except Exception as e:
                app.logger.error(f"Error sending message to {channel_name}: {e}")
        
        return jsonify({
            "success": True,
            "channel_name": channel_name,
            "message": generated_message,
            "sent_to_chat": sent_to_chat,
            "model_used": model_name,
            "timestamp": datetime.now().isoformat()
        })
        
    except Exception as e:
        app.logger.error(f"Error in channel message generation for {channel_name}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/beta')
@require_permission(Permissions.DASHBOARD_VIEW)
def beta_dashboard():
    """Render the redesigned beta dashboard."""
    try:
        # Get current user and subscription status
        user = get_current_user()

        # Redirect to onboarding if not completed
        if user and not user.get('onboarding_completed'):
            return redirect(url_for('onboarding_welcome'))

        subscription_status = user_db.get_subscription_status(user['user_id']) if user else None
        has_premium = user_db.has_tts_access(user['user_id']) if user else False

        # Get bot status and basic info for the beta dashboard
        bot_running = is_bot_actually_running()

        # Get channels data based on user role
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        if user and user.get('role_name') == 'streamer':
            # Streamers get redirected to their channel-specific dashboard
            managed_channel = user.get('managed_channel')
            conn.close()
            if managed_channel:
                return redirect(url_for('beta_channel_page', channel_name=managed_channel))
            # If no managed channel, show empty dashboard
            channels_data = []
        else:
            # Super admins see all channels
            c.execute("SELECT * FROM channel_configs ORDER BY channel_name")
            channels_data = [dict(row) for row in c.fetchall()]

        conn.close()

        # Get recent TTS activity
        recent_tts, _ = get_last_10_tts_files_with_last_id(db_file)

        return render_template("beta/dashboard.html",
                             bot_running=bot_running,
                             channels=channels_data,
                             recent_tts=recent_tts[:5],  # Just show 5 most recent
                             subscription_status=subscription_status,
                             has_premium=has_premium,
                             is_single_channel=(user and user.get('role_name') == 'streamer'))
        
    except Exception as e:
        app.logger.error(f"Error loading beta dashboard: {e}")
        return render_template("500.html", error_message=f"Error loading beta dashboard: {str(e)}"), 500

@app.route('/beta/stats')
@require_permission(Permissions.DASHBOARD_STATS)
def beta_stats_page():
    """Render the analytics dashboard with Chart.js"""
    return render_template('beta/analytics.html')

@app.route('/beta/stats/legacy')
@require_permission(Permissions.DASHBOARD_STATS)
def beta_stats_page_legacy():
    """Legacy stats page - kept for reference"""
    try:
        # Get comprehensive stats data
        bot_running = is_bot_actually_running()

        # Get model details
        try:
            model_details = markov_handler.get_available_models()
            if not isinstance(model_details, list):
                app.logger.error(f"Model details is not a list, got: {type(model_details)} - {model_details}")
                model_details = []
        except Exception as model_error:
            app.logger.error(f"Error getting model details: {model_error}")
            model_details = []

        # Get channels data
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM channel_configs ORDER BY channel_name")
        channels_data = [dict(row) for row in c.fetchall()]
        conn.close()
        
        # Get recent TTS and message stats
        try:
            recent_tts, _ = get_last_10_tts_files_with_last_id(db_file)
            if not isinstance(recent_tts, list):
                app.logger.error(f"Recent TTS is not a list, got: {type(recent_tts)} - {recent_tts}")
                recent_tts = []
        except Exception as tts_error:
            app.logger.error(f"Error getting TTS data: {tts_error}")
            recent_tts = []
        
        return render_template("beta/stats.html", 
                             bot_running=bot_running,
                             channels=channels_data,
                             models=model_details,
                             recent_tts=recent_tts[:10] if recent_tts else [])
        
    except Exception as e:
        app.logger.error(f"Error loading beta stats page: {e}")
        return render_template("500.html", error_message=f"Error loading stats: {str(e)}"), 500

@app.route('/beta/settings')
@require_permission(Permissions.SYSTEM_SETTINGS)
def beta_settings_page():
    """Render the redesigned beta settings page."""
    try:
        # Get current user and subscription status
        user = get_current_user()
        subscription_status = user_db.get_subscription_status(user['user_id']) if user else None
        has_premium = user_db.has_tts_access(user['user_id']) if user else False

        bot_running = is_bot_actually_running()

        # Get channels data based on user role
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        if user and user.get('role_name') == 'streamer':
            # Streamers see only their managed channel
            managed_channel = user.get('managed_channel')
            if managed_channel:
                c.execute("SELECT * FROM channel_configs WHERE channel_name = ?", (managed_channel,))
                channel_row = c.fetchone()
                channels_data = [dict(channel_row)] if channel_row else []
            else:
                channels_data = []
        else:
            # Super admins see all channels
            c.execute("SELECT * FROM channel_configs ORDER BY channel_name")
            channels_data = [dict(row) for row in c.fetchall()]

        conn.close()

        # Get bot status for connection info
        bot_status = {}
        if bot_running and os.path.exists("bot_heartbeat.json"):
            try:
                with open("bot_heartbeat.json", "r") as f:
                    bot_status = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                pass

        # Add connection status to channels
        heartbeat_channels = []
        if bot_status.get('channels'):
            heartbeat_channels = [ch.lstrip('#').lower() for ch in bot_status.get('channels', [])]

        for channel in channels_data:
            channel['currently_connected'] = channel['channel_name'].lower() in heartbeat_channels

        return render_template("beta/settings.html",
                             bot_running=bot_running,
                             channels=channels_data,
                             bot_status=bot_status,
                             subscription_status=subscription_status,
                             has_premium=has_premium,
                             is_single_channel=(user and user.get('role_name') == 'streamer'))
        
    except Exception as e:
        app.logger.error(f"Error loading beta settings page: {e}")
        return render_template("500.html", error_message=f"Error loading settings: {str(e)}"), 500

@app.route('/debug-session')
@require_auth
def debug_session():
    """Debug route to check session data"""
    user = get_current_user()
    if user:
        from utils.user_db import UserDatabase
        user_db = UserDatabase('users.db')
        channels = user_db.get_user_channels_from_db(user['user_id'])
        return jsonify({
            'session_data': dict(session),
            'current_user': user,
            'assigned_channels': channels,
            'role': user.get('role_name'),
            'permissions': user.get('role_permissions')
        })
    else:
        return jsonify({'error': 'No user session found'})

@app.route('/beta/channel/<channel_name>')
@require_channel_access('channel_name', 'view')
def beta_channel_page(channel_name):
    """Render the redesigned beta channel page."""
    try:
        # Get current user and subscription status
        user = get_current_user()
        subscription_status = user_db.get_subscription_status(user['user_id']) if user else None
        has_premium = user_db.has_tts_access(user['user_id']) if user else False

        # Validate channel exists
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM channel_configs WHERE channel_name = ?", (channel_name,))
        channel_config = c.fetchone()
        conn.close()

        if not channel_config:
            return render_template("404.html", error_message=f"Channel '{channel_name}' not found"), 404

        # Convert to dict for template
        channel_data = dict(channel_config)
        channel_data['name'] = channel_name
        
        # Get message count for warning logic
        try:
            conn = sqlite3.connect(db_file)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM messages WHERE channel = ?", (channel_name,))
            message_count = c.fetchone()[0]
            channel_data['messages_sent'] = message_count
            conn.close()
        except Exception as e:
            app.logger.warning(f"Could not get message count for {channel_name}: {e}")
            channel_data['messages_sent'] = 0
        
        # Get current bot status for this channel
        bot_running = is_bot_actually_running()
        currently_connected = False
        
        if bot_running and os.path.exists("bot_heartbeat.json"):
            try:
                with open("bot_heartbeat.json", "r") as f:
                    heartbeat = json.load(f)
                if time.time() - heartbeat.get("timestamp", 0) < 120:
                    heartbeat_channels = [ch.lstrip('#') for ch in heartbeat.get("channels", [])]
                    currently_connected = channel_name in heartbeat_channels
            except (FileNotFoundError, json.JSONDecodeError):
                pass
        
        channel_data['currently_connected'] = currently_connected
        channel_data['bot_running'] = bot_running

        return render_template("beta/channel.html",
                             channel=channel_data,
                             subscription_status=subscription_status,
                             has_premium=has_premium)
        
    except Exception as e:
        app.logger.error(f"Error loading beta channel page for {channel_name}: {e}")
        return render_template("500.html", error_message=f"Error loading channel data: {str(e)}"), 500

@app.route('/api/channel/<channel_name>/tts', methods=['POST'])
@require_channel_access('channel_name', 'edit')
def api_channel_tts(channel_name):
    """Generate TTS for a specific channel (Premium feature)."""
    # Check if user has TTS access (Premium subscription)
    user = get_current_user()
    if user and not user_db.has_tts_access(user['user_id']):
        app.logger.warning(f"User {user['username']} attempted TTS without Premium subscription")
        return jsonify({
            'success': False,
            'error': 'TTS is a Premium feature',
            'message': 'Upgrade to Premium ($2/month) to unlock Text-to-Speech',
            'upgrade_url': '/premium',
            'requires_premium': True
        }), 403

    try:
        data = request.get_json() or {}
        text = data.get('text', '').strip()

        if not text:
            return jsonify({"error": "Text is required"}), 400
        
        # Check if channel exists and TTS is enabled
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM channel_configs WHERE channel_name = ?", (channel_name,))
        channel_config = c.fetchone()
        conn.close()
        
        if not channel_config:
            return jsonify({"error": "Channel not found"}), 404
        
        if not channel_config['tts_enabled']:
            return jsonify({"error": "TTS not enabled for this channel"}), 400

        # Check if TTS is globally enabled (--tts flag at startup)
        if not _enable_tts_webapp:
            app.logger.warning(f"TTS request rejected - TTS not enabled globally (restart with --tts flag)")
            return jsonify({
                "success": False,
                "error": "TTS not enabled",
                "message": "TTS is disabled. Please restart the bot with --tts flag to enable TTS functionality.",
                "details": "The service must be started with the --tts flag for TTS to work."
            }), 503

        # Check if TTS dependencies are available before starting background processing
        try:
            import torch
            from transformers import AutoProcessor, BarkModel
            import scipy
            app.logger.info(f"TTS dependencies check passed: torch={torch.__version__}")
        except ImportError as e:
            app.logger.error(f"TTS dependencies not available: {e}")
            return jsonify({
                "success": False,
                "error": "TTS system not configured",
                "message": f"Missing required dependencies: {str(e)}. Please install torch, transformers, and scipy.",
                "details": "The TTS feature requires PyTorch and transformers libraries to be installed on the server."
            }), 503

        # Use start_tts_processing for consistency with bot's TTS generation
        # This will process TTS in a background thread and use the notification system.

        # Generate a synthetic message_id for this web-initiated TTS
        synthetic_message_id = f"webtts_{channel_name}_{int(time.time())}"
        current_timestamp_str = datetime.now().isoformat()

        app.logger.info(f"Web UI TTS request for channel '{channel_name}'. Text: '{text[:30]}...'. Voice: '{channel_config['voice_preset']}'. MsgID: {synthetic_message_id}")

        start_tts_processing(
            input_text=text,
            channel_name=channel_name,
            db_file=db_file, # Ensure db_file is accessible here
            message_id=synthetic_message_id,
            timestamp_str=current_timestamp_str,
            voice_preset_override=channel_config['voice_preset']
            # bark_model is handled within process_text_thread if needed
        )
        
        # Since start_tts_processing is threaded, we don't get an immediate file_path.
        # The client-side will rely on WebSocket updates or polling for the new TTS entry.
        return jsonify({
            "success": True,
            "message": "TTS generation initiated. Listen for updates.",
            "channel_name": channel_name,
            "text": text,
            "timestamp": current_timestamp_str 
            # file_path will be available via the tts_logs table and WebSocket notification
        })
        
    except Exception as e:
        app.logger.error(f"Error in channel TTS for {channel_name}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/system-info')
@require_role('admin')
def api_system_info():
    """Get system information for the stats dashboard."""
    try:
        import os
        import time
        import psutil
        
        # Get uptime (from process start time)
        process = psutil.Process(os.getpid())
        start_time = process.create_time()
        uptime_seconds = time.time() - start_time
        
        # Get cache directory size
        cache_size = 0
        cache_size_str = "0 B"
        if os.path.exists("cache"):
            try:
                for dirpath, dirnames, filenames in os.walk("cache"):
                    for filename in filenames:
                        filepath = os.path.join(dirpath, filename)
                        if os.path.exists(filepath):
                            cache_size += os.path.getsize(filepath)
                
                # Convert to human readable
                if cache_size > 0:
                    size_names = ["B", "KB", "MB", "GB"]
                    i = 0
                    size_calc = cache_size
                    while size_calc >= 1024 and i < len(size_names) - 1:
                        size_calc /= 1024
                        i += 1
                    cache_size_str = f"{size_calc:.1f} {size_names[i]}"
            except Exception as e:
                logging.warning(f"Error calculating cache size: {e}")
        
        # Get database size
        db_size_str = "Unknown"
        try:
            if os.path.exists("messages.db"):
                db_size = os.path.getsize("messages.db")
                size_names = ["B", "KB", "MB", "GB"]
                i = 0
                size_calc = db_size
                while size_calc >= 1024 and i < len(size_names) - 1:
                    size_calc /= 1024
                    i += 1
                db_size_str = f"{size_calc:.1f} {size_names[i]}"
        except Exception as e:
            logging.warning(f"Error calculating database size: {e}")
        
        # Get memory usage
        memory_usage_str = "Unknown"
        try:
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / (1024 * 1024)  # Convert bytes to MB
            memory_usage_str = f"{memory_mb:.1f} MB"
        except Exception as e:
            logging.warning(f"Error getting memory usage: {e}")
        
        return jsonify({
            "uptime": int(uptime_seconds),
            "cache_size": cache_size_str,
            "database_size": db_size_str,
            "memory_usage": memory_usage_str
        })
        
    except Exception as e:
        logging.error(f"Error getting system info: {e}")
        return jsonify({
            "uptime": 0,
            "cache_size": "Unknown",
            "database_size": "Unknown",
            "memory_usage": "Unknown"
        }), 500

@app.route('/api/clear-cache', methods=['POST'])
@require_role('admin')
def api_clear_cache():
    """Clear all cached data."""
    try:
        import shutil
        
        # Clear cache directory
        cache_dir = "cache"
        if os.path.exists(cache_dir):
            # Remove all files in cache directory
            for filename in os.listdir(cache_dir):
                file_path = os.path.join(cache_dir, filename)
                try:
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    logging.warning(f"Error removing cached file {file_path}: {e}")
        
        # Clear in-memory models if markov_handler exists
        if 'markov_handler' in globals():
            markov_handler.models.clear()
        
        return jsonify({
            "success": True,
            "message": "Cache cleared successfully"
        })
        
    except Exception as e:
        logging.error(f"Error clearing cache: {e}")
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500

@app.route('/api/settings/export')
def api_export_settings():
    """Export current settings as JSON."""
    try:
        # Get all channel configurations
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # Get channels
        c.execute("SELECT * FROM channel_configs")
        channels = [dict(row) for row in c.fetchall()]
        
        # Get system settings (if they exist in a separate table)
        settings_data = {
            "channels": channels,
            "export_timestamp": time.time(),
            "export_date": datetime.now().isoformat(),
            "version": "1.0"
        }
        
        conn.close()
        
        # Create response
        response = make_response(json.dumps(settings_data, indent=2))
        response.headers['Content-Type'] = 'application/json'
        response.headers['Content-Disposition'] = f'attachment; filename=ansv-settings-{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
        
        return response
        
    except Exception as e:
        logging.error(f"Error exporting settings: {e}")
        return jsonify({
            "error": str(e)
        }), 500

# PERFORMANCE OPTIMIZATION: Real-time WebSocket Events
# Replace polling with event-driven updates for better performance

class EventBroadcaster:
    """Centralized event broadcasting for real-time updates."""
    
    @staticmethod
    def bot_status_changed(status_data):
        """Broadcast bot status changes to all clients."""
        socketio.emit('bot_status_update', status_data)
        logging.debug(f"Broadcasted bot status update: {status_data}")
    
    @staticmethod
    def channel_updated(channel_name, update_data):
        """Broadcast channel-specific updates."""
        socketio.emit('channel_update', {
            'channel': channel_name,
            'data': update_data
        })
        logging.debug(f"Broadcasted channel update for {channel_name}")
    
    @staticmethod
    def new_message(channel_name, message_data):
        """Broadcast new messages to channel subscribers."""
        socketio.emit('new_message', {
            'channel': channel_name,
            'message': message_data
        })
    
    @staticmethod
    def model_rebuilt(channel_name, model_stats):
        """Broadcast Markov model rebuild completion."""
        socketio.emit('model_rebuilt', {
            'channel': channel_name,
            'stats': model_stats
        })
        logging.info(f"Broadcasted model rebuild completion for {channel_name}")

# Global event broadcaster instance
events = EventBroadcaster()

# SocketIO Event Handlers
@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    logging.info(f"Client connected: {request.sid}")
    # Send current bot status to newly connected client
    try:
        status = {
            'running': is_bot_actually_running(),
            'timestamp': time.time()
        }
        socketio.emit('bot_status_update', status, room=request.sid)
    except Exception as e:
        logging.error(f"Error sending initial status to client: {e}")

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    logging.debug(f"Client disconnected: {request.sid}")

@socketio.on('subscribe_channel')
def handle_channel_subscription(data):
    """Handle client subscribing to channel-specific updates."""
    channel_name = data.get('channel')
    app.logger.info(f"[SOCKETIO] Received subscribe_channel event from {request.sid}, data: {data}")
    if channel_name:
        room_name = f"channel_{channel_name}"
        join_room(room_name)  # Use imported join_room function
        app.logger.info(f"[SOCKETIO] ✅ Client {request.sid} joined room: {room_name}")
    else:
        app.logger.warning(f"[SOCKETIO] ⚠️ subscribe_channel called without channel_name")

@socketio.on('request_status')
def handle_status_request():
    """Handle explicit status request from client."""
    try:
        status = {
            'running': is_bot_actually_running(),
            'timestamp': time.time()
        }
        socketio.emit('bot_status_update', status, room=request.sid)
    except Exception as e:
        logging.error(f"Error handling status request: {e}")

if __name__ == "__main__":
    markov_handler.load_models()
    
    @app.route('/health')
    def health_check_route(): 
        return jsonify({"status": "ok", "tts_enabled_webapp": _enable_tts_webapp})
    
    socketio.run(app, host="0.0.0.0", port=5001, debug=True, use_reloader=False)
