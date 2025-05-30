"""
ANSV Bot - Authentication and Authorization System
Provides decorators and utilities for user authentication and permission checking.
"""

from functools import wraps
from flask import session, request, redirect, url_for, jsonify, g
from typing import List, Optional, Any, Dict
import logging

logger = logging.getLogger(__name__)

# Import our user database (will be initialized by webapp)
user_db = None

def init_auth(user_database):
    """Initialize the auth system with the user database instance."""
    global user_db
    user_db = user_database
    logger.info("Authentication system initialized")

def get_current_user() -> Optional[Dict[str, Any]]:
    """Get the current authenticated user from session."""
    if not user_db:
        logger.error("User database not initialized")
        return None
    
    session_id = session.get('session_id')
    
    if not session_id:
        return None
    
    # Get user data from session
    user_data = user_db.get_session(session_id)
    if user_data:
        # Store in Flask's g object for easy access
        g.current_user = user_data
        return user_data
    
    # Session invalid, clear it
    session.clear()
    return None

def is_authenticated() -> bool:
    """Check if current session is authenticated."""
    return get_current_user() is not None

def has_permission(required_permission: str) -> bool:
    """Check if current user has the required permission."""
    user = get_current_user()
    if not user:
        return False
    
    return user_db.has_permission(user['permissions'], required_permission)

def require_auth(f):
    """Decorator to require authentication for a route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_authenticated():
            if request.is_json:
                return jsonify({'error': 'Authentication required', 'redirect': '/login'}), 401
            
            # Store the intended destination
            next_url = request.url if request.method == 'GET' else request.referrer
            if next_url and next_url != request.url_root:
                return redirect(url_for('login', next=next_url))
            return redirect(url_for('login'))
        
        return f(*args, **kwargs)
    return decorated_function

def require_permission(permission: str):
    """Decorator to require specific permission for a route."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not is_authenticated():
                if request.is_json:
                    return jsonify({'error': 'Authentication required', 'redirect': '/login'}), 401
                # Store the intended destination
                next_url = request.url if request.method == 'GET' else request.referrer
                if next_url and next_url != request.url_root:
                    return redirect(url_for('login', next=next_url))
                return redirect(url_for('login'))
            
            if not has_permission(permission):
                logger.warning(f"Permission denied: {permission} for user {g.current_user.get('username', 'unknown')}")
                if request.is_json:
                    return jsonify({'error': 'Permission denied', 'required_permission': permission}), 403
                
                # For web requests, you might want to show a 403 page
                from flask import render_template
                return render_template('403.html', required_permission=permission), 403
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def require_role(role_name: str):
    """Decorator to require specific role for a route."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = get_current_user()
            if not user:
                if request.is_json:
                    return jsonify({'error': 'Authentication required', 'redirect': '/login'}), 401
                # Store the intended destination
                next_url = request.url if request.method == 'GET' else request.referrer
                if next_url and next_url != request.url_root:
                    return redirect(url_for('login', next=next_url))
                return redirect(url_for('login'))
            
            if user['role_name'] != role_name and user['role_name'] != 'super_admin':
                logger.warning(f"Role access denied: required {role_name}, user has {user['role_name']}")
                if request.is_json:
                    return jsonify({'error': 'Insufficient role', 'required_role': role_name}), 403
                
                from flask import render_template
                return render_template('403.html', required_role=role_name), 403
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def login_user(username: str, password: str, ip_address: str, user_agent: str, 
               remember_me: bool = False) -> tuple[bool, Optional[str], Optional[Dict]]:
    """
    Authenticate and login a user.
    Returns: (success, error_message, user_data)
    """
    if not user_db:
        return False, "Authentication system not initialized", None
    
    # Authenticate user
    user_data = user_db.authenticate_user(username, password)
    if not user_data:
        return False, "Invalid username or password", None
    
    try:
        # Create session
        session_hours = 24 * 7 if remember_me else 24  # 7 days if remember me, otherwise 24 hours
        session_id = user_db.create_session(
            user_data['id'], 
            ip_address, 
            user_agent, 
            session_hours
        )
        
        # Store in Flask session
        session.permanent = remember_me
        session['session_id'] = session_id
        session['user_id'] = user_data['id']
        session['username'] = user_data['username']
        session['role'] = user_data['role_name']
        
        # Log successful login
        user_db.log_action(
            user_data['id'], 
            'auth.login', 
            'session', 
            session_id,
            {'username': username, 'remember_me': remember_me},
            ip_address, 
            user_agent
        )
        
        logger.info(f"User logged in successfully: {username} from {ip_address}")
        return True, None, user_data
        
    except Exception as e:
        logger.error(f"Login error for user {username}: {e}")
        return False, "Login failed due to system error", None

def logout_user(ip_address: str = None, user_agent: str = None) -> bool:
    """Logout current user and invalidate session."""
    if not user_db:
        return False
    
    session_id = session.get('session_id')
    user_id = session.get('user_id')
    username = session.get('username')
    
    if session_id:
        try:
            # Invalidate session in database
            user_db.invalidate_session(session_id)
            
            # Log logout
            if user_id:
                user_db.log_action(
                    user_id,
                    'auth.logout',
                    'session',
                    session_id,
                    {'username': username},
                    ip_address,
                    user_agent
                )
            
            logger.info(f"User logged out: {username}")
            
        except Exception as e:
            logger.error(f"Error during logout for {username}: {e}")
    
    # Clear Flask session
    session.clear()
    return True

def get_user_permissions() -> List[str]:
    """Get current user's permissions."""
    user = get_current_user()
    return user['permissions'] if user else []

def can_access_resource(resource: str, action: str = 'view') -> bool:
    """Check if current user can access a specific resource with given action."""
    permission = f"{resource}.{action}"
    return has_permission(permission)

def get_user_channels() -> List[str]:
    """Get list of channels owned by current user (for streamers)."""
    user = get_current_user()
    if not user or not user_db:
        return []
    
    # Super admins and admins can access all channels
    if user['role_name'] in ['super_admin', 'admin']:
        # Return empty list - they have wildcard permissions anyway
        return []
    
    # For streamers and other users, get channels from database
    return user_db.get_user_channels_from_db(user['user_id'])

def can_access_channel(channel_name: str, action: str = 'view') -> bool:
    """Check if current user can access a specific channel with given action."""
    user = get_current_user()
    if not user:
        return False
    
    # Super admin and admin have access to all channels
    if user['role_name'] in ['super_admin', 'admin']:
        return has_permission(f'channels.{action}')
    
    # Moderators have access to all channels they're assigned to
    if user['role_name'] == 'moderator':
        return has_permission(f'channels.{action}')
    
    # Streamers can only access their own channels
    if user['role_name'] == 'streamer':
        user_channels = get_user_channels()
        if channel_name in user_channels:
            # Check if they have the "own" version of the permission
            return has_permission(f'channels.{action}_own')
        return False
    
    # Default permission check
    return has_permission(f'channels.{action}')

def require_channel_access(channel_param: str = 'channel', action: str = 'view'):
    """Decorator to require access to a specific channel."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Get channel name from request parameters, args, form data, or JSON
            from flask import request
            
            # Check authentication first
            if not is_authenticated():
                if request.is_json:
                    return jsonify({'error': 'Authentication required', 'redirect': '/login'}), 401
                # Store the intended destination
                next_url = request.url if request.method == 'GET' else request.referrer
                if next_url and next_url != request.url_root:
                    return redirect(url_for('login', next=next_url))
                return redirect(url_for('login'))
            channel_name = (request.view_args.get(channel_param) or 
                           request.args.get(channel_param) or
                           request.form.get(channel_param))
            
            # Also check JSON data for POST requests
            if not channel_name and request.is_json and request.json:
                channel_name = request.json.get(channel_param)
            
            if not channel_name:
                logger.warning("Channel access check failed: no channel specified")
                if request.is_json:
                    return jsonify({'error': 'Channel not specified'}), 400
                from flask import render_template
                return render_template('403.html', required_permission=f'channels.{action}'), 403
            
            if not can_access_channel(channel_name, action):
                user = get_current_user()
                username = user['username'] if user else 'anonymous'
                logger.warning(f"Channel access denied: {action} on {channel_name} for user {username}")
                
                if request.is_json:
                    return jsonify({'error': 'Channel access denied', 'channel': channel_name}), 403
                
                from flask import render_template
                return render_template('403.html', 
                                     required_permission=f'channels.{action}',
                                     channel=channel_name), 403
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# Context processor to make auth functions available in templates
def auth_context_processor():
    """Make authentication functions available in templates."""
    return {
        'current_user': get_current_user(),
        'is_authenticated': is_authenticated(),
        'has_permission': has_permission,
        'can_access_resource': can_access_resource,
        'can_access_channel': can_access_channel,
        'get_user_permissions': get_user_permissions,
        'get_user_channels': get_user_channels
    }

# Permission constants for easy reference
class Permissions:
    """Constants for permission strings."""
    
    # Dashboard
    DASHBOARD_VIEW = 'dashboard.view'
    DASHBOARD_STATS = 'dashboard.stats'
    
    # Bot Management
    BOT_START = 'bot.start'
    BOT_STOP = 'bot.stop'
    BOT_RESTART = 'bot.restart'
    BOT_CONFIGURE = 'bot.configure'
    
    # Channel Management
    CHANNELS_VIEW = 'channels.view'
    CHANNELS_CREATE = 'channels.create'
    CHANNELS_EDIT = 'channels.edit'
    CHANNELS_DELETE = 'channels.delete'
    CHANNELS_MODERATE = 'channels.moderate'
    
    # Channel Ownership (for streamers)
    CHANNELS_OWN = 'channels.own'
    CHANNELS_VIEW_OWN = 'channels.view_own'
    CHANNELS_EDIT_OWN = 'channels.edit_own'
    CHANNELS_SETTINGS_OWN = 'channels.settings_own'
    
    # TTS Management
    TTS_GENERATE = 'tts.generate'
    TTS_MANAGE = 'tts.manage'
    TTS_HISTORY = 'tts.history'
    TTS_HISTORY_OWN = 'tts.history_own'
    
    # Model Management
    MODELS_VIEW = 'models.view'
    MODELS_REBUILD = 'models.rebuild'
    MODELS_CONFIGURE = 'models.configure'
    
    # User Management
    USERS_VIEW = 'users.view'
    USERS_CREATE = 'users.create'
    USERS_EDIT = 'users.edit'
    USERS_DELETE = 'users.delete'
    USERS_ROLES = 'users.roles'
    
    # System Management
    SYSTEM_LOGS = 'system.logs'
    SYSTEM_BACKUP = 'system.backup'
    SYSTEM_SETTINGS = 'system.settings'

# Role constants
class Roles:
    """Constants for role names."""
    SUPER_ADMIN = 'super_admin'
    ADMIN = 'admin'
    MODERATOR = 'moderator'
    STREAMER = 'streamer'
    VIEWER = 'viewer'