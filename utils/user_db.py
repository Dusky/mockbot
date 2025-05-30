"""
ANSV Bot - User Database Management
Handles user accounts, roles, permissions, and sessions
"""

import sqlite3
import json
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
import bcrypt
import logging

logger = logging.getLogger(__name__)

class UserDatabase:
    """Enhanced database management for user accounts and permissions."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_user_tables()
    
    def get_connection(self):
        """Get database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_user_tables(self):
        """Initialize all user-related database tables."""
        conn = self.get_connection()
        try:
            # Create roles table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS roles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(50) UNIQUE NOT NULL,
                    display_name VARCHAR(100) NOT NULL,
                    description TEXT,
                    permissions TEXT, -- JSON string of permissions
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Check if users table exists and has the old schema
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
            users_table_exists = cursor.fetchone() is not None
            
            if users_table_exists:
                # Check if the table has the old schema (is_admin column instead of role_id)
                cursor = conn.execute("PRAGMA table_info(users)")
                columns = [col[1] for col in cursor.fetchall()]
                
                if 'is_admin' in columns and 'role_id' not in columns:
                    logger.info("Found old users table schema, migrating to new schema...")
                    
                    # Rename old table
                    conn.execute("ALTER TABLE users RENAME TO users_old")
                    
                    # Create new users table
                    conn.execute("""
                        CREATE TABLE users (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            username VARCHAR(50) UNIQUE NOT NULL,
                            email VARCHAR(100),
                            password_hash VARCHAR(255) NOT NULL,
                            role_id INTEGER NOT NULL,
                            status VARCHAR(20) DEFAULT 'active',
                            last_login DATETIME,
                            login_attempts INTEGER DEFAULT 0,
                            locked_until DATETIME,
                            password_changed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (role_id) REFERENCES roles(id)
                        )
                    """)
                    
                    logger.info("New users table created with updated schema")
                else:
                    logger.info("Users table already has correct schema")
            else:
                # Create new users table
                conn.execute("""
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username VARCHAR(50) UNIQUE NOT NULL,
                        email VARCHAR(100),
                        password_hash VARCHAR(255) NOT NULL,
                        role_id INTEGER NOT NULL,
                        status VARCHAR(20) DEFAULT 'active',
                        last_login DATETIME,
                        login_attempts INTEGER DEFAULT 0,
                        locked_until DATETIME,
                        password_changed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (role_id) REFERENCES roles(id)
                    )
                """)
            
            # Create enhanced sessions table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_sessions (
                    id VARCHAR(255) PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    ip_address VARCHAR(45),
                    user_agent TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    expires_at DATETIME NOT NULL,
                    last_activity DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)
            
            # Create audit log table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    action VARCHAR(100) NOT NULL,
                    resource VARCHAR(100),
                    resource_id VARCHAR(100),
                    details TEXT, -- JSON string
                    ip_address VARCHAR(45),
                    user_agent TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)
            
            # Create user-channel assignments table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    channel_name VARCHAR(100) NOT NULL,
                    assigned_by INTEGER NOT NULL,
                    assigned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1,
                    UNIQUE(user_id, channel_name),
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (assigned_by) REFERENCES users(id)
                )
            """)
            
            
            conn.commit()
            logger.info("User database tables initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing user tables: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def create_default_roles(self):
        """Create default system roles with permissions."""
        
        # Define default roles and their permissions
        default_roles = {
            'super_admin': {
                'display_name': 'Super Administrator',
                'description': 'Full system access with all permissions',
                'permissions': ['*']  # Wildcard for all permissions
            },
            'admin': {
                'display_name': 'Administrator',
                'description': 'System administration with user management',
                'permissions': [
                    'dashboard.*', 'bot.*', 'channels.*', 
                    'tts.*', 'models.*', 'users.*',
                    'system.logs', 'system.settings'
                ]
            },
            'moderator': {
                'display_name': 'Moderator',
                'description': 'Channel management and monitoring',
                'permissions': [
                    'dashboard.view', 'dashboard.stats',
                    'channels.view', 'channels.edit', 'channels.moderate',
                    'tts.generate', 'tts.history', 'tts.manage',
                    'models.view'
                ]
            },
            'streamer': {
                'display_name': 'Streamer',
                'description': 'Channel owner with access to own channel settings only',
                'permissions': [
                    'channels.own', 'channels.view_own', 'channels.edit_own', 
                    'channels.settings_own', 'tts.generate', 'tts.history_own'
                ]
            },
            'viewer': {
                'display_name': 'Viewer',
                'description': 'Read-only access to dashboard and statistics',
                'permissions': [
                    'dashboard.view', 'dashboard.stats',
                    'channels.view', 'tts.history', 'models.view'
                ]
            }
        }
        
        conn = self.get_connection()
        try:
            for role_name, role_data in default_roles.items():
                # Check if role already exists
                existing = conn.execute(
                    "SELECT id FROM roles WHERE name = ?", (role_name,)
                ).fetchone()
                
                if not existing:
                    conn.execute("""
                        INSERT INTO roles (name, display_name, description, permissions)
                        VALUES (?, ?, ?, ?)
                    """, (
                        role_name,
                        role_data['display_name'],
                        role_data['description'],
                        json.dumps(role_data['permissions'])
                    ))
                    logger.info(f"Created default role: {role_name}")
            
            conn.commit()
            logger.info("Default roles created successfully")
            
        except Exception as e:
            logger.error(f"Error creating default roles: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def hash_password(self, password: str) -> str:
        """Hash a password using bcrypt."""
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
        return hashed.decode('utf-8')
    
    def verify_password(self, password: str, hashed: str) -> bool:
        """Verify a password against its hash."""
        try:
            # Try bcrypt first (new system)
            return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
        except Exception as e:
            # If bcrypt fails, try SHA256 (legacy system)
            import hashlib
            sha256_hash = hashlib.sha256(password.encode()).hexdigest()
            if sha256_hash == hashed:
                logger.info(f"Legacy SHA256 password verified, consider upgrading to bcrypt")
                return True
            logger.error(f"Password verification error: {e}")
            return False
    
    def create_user(self, username: str, password: str, role_name: str, 
                   email: Optional[str] = None) -> Optional[int]:
        """Create a new user account."""
        conn = self.get_connection()
        try:
            # Get role ID
            role = conn.execute(
                "SELECT id FROM roles WHERE name = ?", (role_name,)
            ).fetchone()
            
            if not role:
                raise ValueError(f"Role '{role_name}' not found")
            
            # Hash password
            password_hash = self.hash_password(password)
            
            # Create user
            if email:
                cursor = conn.execute("""
                    INSERT INTO users (username, email, password_hash, role_id)
                    VALUES (?, ?, ?, ?)
                """, (username, email, password_hash, role['id']))
            else:
                cursor = conn.execute("""
                    INSERT INTO users (username, password_hash, role_id)
                    VALUES (?, ?, ?)
                """, (username, password_hash, role['id']))
            
            user_id = cursor.lastrowid
            conn.commit()
            
            # Log user creation
            self.log_action(user_id, 'user.created', 'user', str(user_id), 
                          {'username': username, 'role': role_name})
            
            logger.info(f"User created successfully: {username} (ID: {user_id})")
            return user_id
            
        except Exception as e:
            logger.error(f"Error creating user {username}: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Authenticate a user and return user data if successful."""
        conn = self.get_connection()
        try:
            # Get user with role information
            user = conn.execute("""
                SELECT u.*, r.name as role_name, r.display_name as role_display_name,
                       r.permissions as role_permissions
                FROM users u
                JOIN roles r ON u.role_id = r.id
                WHERE u.username = ? AND u.status = 'active'
            """, (username,)).fetchone()
            
            if not user:
                logger.warning(f"Authentication failed: user not found - {username}")
                return None
            
            # Check if account is locked
            if user['locked_until'] and datetime.fromisoformat(user['locked_until']) > datetime.now():
                logger.warning(f"Authentication failed: account locked - {username}")
                return None
            
            # Verify password
            if not self.verify_password(password, user['password_hash']):
                # Increment failed login attempts
                self.increment_login_attempts(user['id'])
                logger.warning(f"Authentication failed: invalid password - {username}")
                return None
            
            # Reset login attempts and update last login
            self.reset_login_attempts(user['id'])
            self.update_last_login(user['id'])
            
            # Convert to dict and remove sensitive data
            user_data = dict(user)
            del user_data['password_hash']
            
            # Parse permissions
            user_data['permissions'] = json.loads(user_data['role_permissions'])
            
            logger.info(f"User authenticated successfully: {username}")
            return user_data
            
        except Exception as e:
            logger.error(f"Error authenticating user {username}: {e}")
            return None
        finally:
            conn.close()
    
    def increment_login_attempts(self, user_id: int):
        """Increment failed login attempts and lock account if needed."""
        conn = self.get_connection()
        try:
            # Get current attempts
            user = conn.execute(
                "SELECT login_attempts FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            
            new_attempts = (user['login_attempts'] or 0) + 1
            locked_until = None
            
            # Lock account after 5 failed attempts for 30 minutes
            if new_attempts >= 5:
                locked_until = (datetime.now() + timedelta(minutes=30)).isoformat()
                logger.warning(f"User account locked due to failed attempts: {user_id}")
            
            conn.execute("""
                UPDATE users 
                SET login_attempts = ?, locked_until = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (new_attempts, locked_until, user_id))
            
            conn.commit()
            
        except Exception as e:
            logger.error(f"Error incrementing login attempts for user {user_id}: {e}")
        finally:
            conn.close()
    
    def reset_login_attempts(self, user_id: int):
        """Reset failed login attempts after successful login."""
        conn = self.get_connection()
        try:
            conn.execute("""
                UPDATE users 
                SET login_attempts = 0, locked_until = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (user_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Error resetting login attempts for user {user_id}: {e}")
        finally:
            conn.close()
    
    def update_last_login(self, user_id: int):
        """Update user's last login timestamp."""
        conn = self.get_connection()
        try:
            conn.execute("""
                UPDATE users 
                SET last_login = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (user_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Error updating last login for user {user_id}: {e}")
        finally:
            conn.close()
    
    def create_session(self, user_id: int, ip_address: str, user_agent: str, 
                      expires_hours: int = 24) -> str:
        """Create a new user session."""
        session_id = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(hours=expires_hours)
        
        conn = self.get_connection()
        try:
            conn.execute("""
                INSERT INTO user_sessions (id, user_id, ip_address, user_agent, expires_at)
                VALUES (?, ?, ?, ?, ?)
            """, (session_id, user_id, ip_address, user_agent, expires_at.isoformat()))
            
            conn.commit()
            logger.info(f"Session created for user {user_id}: {session_id[:8]}...")
            return session_id
            
        except Exception as e:
            logger.error(f"Error creating session for user {user_id}: {e}")
            raise
        finally:
            conn.close()
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session data if valid and active."""
        conn = self.get_connection()
        try:
            session = conn.execute("""
                SELECT s.*, u.username, u.status as user_status,
                       r.name as role_name, r.permissions as role_permissions
                FROM user_sessions s
                JOIN users u ON s.user_id = u.id
                JOIN roles r ON u.role_id = r.id
                WHERE s.id = ? AND s.is_active = 1 AND s.expires_at > ?
            """, (session_id, datetime.now().isoformat())).fetchone()
            
            if session and session['user_status'] == 'active':
                # Update last activity
                conn.execute("""
                    UPDATE user_sessions 
                    SET last_activity = CURRENT_TIMESTAMP 
                    WHERE id = ?
                """, (session_id,))
                conn.commit()
                
                session_data = dict(session)
                session_data['permissions'] = json.loads(session_data['role_permissions'])
                return session_data
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting session {session_id}: {e}")
            return None
        finally:
            conn.close()
    
    def invalidate_session(self, session_id: str):
        """Invalidate a user session."""
        conn = self.get_connection()
        try:
            conn.execute("""
                UPDATE user_sessions 
                SET is_active = 0, updated_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (session_id,))
            conn.commit()
            logger.info(f"Session invalidated: {session_id[:8]}...")
        except Exception as e:
            logger.error(f"Error invalidating session {session_id}: {e}")
        finally:
            conn.close()
    
    def log_action(self, user_id: Optional[int], action: str, resource: str = None, 
                  resource_id: str = None, details: Dict = None, 
                  ip_address: str = None, user_agent: str = None):
        """Log user action for audit trail."""
        conn = self.get_connection()
        try:
            conn.execute("""
                INSERT INTO audit_log 
                (user_id, action, resource, resource_id, details, ip_address, user_agent)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, action, resource, resource_id,
                json.dumps(details) if details else None,
                ip_address, user_agent
            ))
            conn.commit()
        except Exception as e:
            logger.error(f"Error logging action: {e}")
        finally:
            conn.close()
    
    def has_permission(self, user_permissions: List[str], required_permission: str) -> bool:
        """Check if user has required permission."""
        # Super admin wildcard
        if '*' in user_permissions:
            return True
        
        # Exact match
        if required_permission in user_permissions:
            return True
        
        # Wildcard matching (e.g., 'dashboard.*' matches 'dashboard.view')
        for permission in user_permissions:
            if permission.endswith('.*'):
                prefix = permission[:-2]
                if required_permission.startswith(prefix + '.'):
                    return True
        
        return False
    
    def assign_channel_to_user(self, user_id: int, channel_name: str, assigned_by: int) -> bool:
        """Assign a channel to a user and automatically upgrade to streamer role if needed."""
        conn = self.get_connection()
        try:
            # Get current user info
            user = conn.execute(
                "SELECT username, role_id, status FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            
            if not user or user['status'] != 'active':
                raise ValueError(f"User ID {user_id} not found or inactive")
            
            # Get role info
            role = conn.execute(
                "SELECT name FROM roles WHERE id = ?", (user['role_id'],)
            ).fetchone()
            
            if not role:
                raise ValueError(f"Role not found for user {user_id}")
            
            # Check if user should be upgraded to streamer
            should_upgrade = role['name'] == 'viewer'
            new_role_id = user['role_id']
            
            if should_upgrade:
                # Get streamer role ID
                streamer_role = conn.execute(
                    "SELECT id FROM roles WHERE name = 'streamer'"
                ).fetchone()
                
                if streamer_role:
                    new_role_id = streamer_role['id']
                    logger.info(f"Upgrading user {user['username']} from viewer to streamer")
                else:
                    logger.warning("Streamer role not found, keeping current role")
            
            # Assign channel
            conn.execute("""
                INSERT OR REPLACE INTO user_channels (user_id, channel_name, assigned_by)
                VALUES (?, ?, ?)
            """, (user_id, channel_name, assigned_by))
            
            # Update user role if needed
            if should_upgrade and new_role_id != user['role_id']:
                conn.execute("""
                    UPDATE users 
                    SET role_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (new_role_id, user_id))
                
                # Log role upgrade
                self.log_action(
                    assigned_by, 'user.role_upgraded', 'user', str(user_id),
                    {
                        'username': user['username'],
                        'from_role': role['name'],
                        'to_role': 'streamer',
                        'reason': 'channel_assignment'
                    }
                )
            
            conn.commit()
            
            # Log channel assignment
            self.log_action(
                assigned_by, 'channel.assigned', 'channel', channel_name,
                {
                    'user_id': user_id,
                    'username': user['username'],
                    'channel_name': channel_name,
                    'role_upgraded': should_upgrade
                }
            )
            
            logger.info(f"Channel '{channel_name}' assigned to user {user['username']} (ID: {user_id})")
            if should_upgrade:
                logger.info(f"User {user['username']} upgraded to streamer role")
            
            return True
            
        except Exception as e:
            logger.error(f"Error assigning channel {channel_name} to user {user_id}: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def unassign_channel_from_user(self, user_id: int, channel_name: str, unassigned_by: int) -> bool:
        """Unassign a channel from a user and downgrade to viewer if no channels remain."""
        conn = self.get_connection()
        try:
            # Get current user info
            user = conn.execute(
                "SELECT username, role_id FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            
            if not user:
                raise ValueError(f"User ID {user_id} not found")
            
            # Remove channel assignment
            conn.execute("""
                UPDATE user_channels 
                SET is_active = 0
                WHERE user_id = ? AND channel_name = ?
            """, (user_id, channel_name))
            
            # Check if user has any remaining active channels
            remaining_channels = conn.execute("""
                SELECT COUNT(*) as count 
                FROM user_channels 
                WHERE user_id = ? AND is_active = 1
            """, (user_id,)).fetchone()
            
            # Get current role
            role = conn.execute(
                "SELECT name FROM roles WHERE id = ?", (user['role_id'],)
            ).fetchone()
            
            # If streamer has no channels left, downgrade to viewer
            should_downgrade = (role['name'] == 'streamer' and 
                              remaining_channels['count'] == 0)
            
            if should_downgrade:
                # Get viewer role ID
                viewer_role = conn.execute(
                    "SELECT id FROM roles WHERE name = 'viewer'"
                ).fetchone()
                
                if viewer_role:
                    conn.execute("""
                        UPDATE users 
                        SET role_id = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (viewer_role['id'], user_id))
                    
                    # Log role downgrade
                    self.log_action(
                        unassigned_by, 'user.role_downgraded', 'user', str(user_id),
                        {
                            'username': user['username'],
                            'from_role': 'streamer',
                            'to_role': 'viewer',
                            'reason': 'no_channels_remaining'
                        }
                    )
                    
                    logger.info(f"User {user['username']} downgraded to viewer (no channels remaining)")
            
            conn.commit()
            
            # Log channel unassignment
            self.log_action(
                unassigned_by, 'channel.unassigned', 'channel', channel_name,
                {
                    'user_id': user_id,
                    'username': user['username'],
                    'channel_name': channel_name,
                    'role_downgraded': should_downgrade
                }
            )
            
            logger.info(f"Channel '{channel_name}' unassigned from user {user['username']} (ID: {user_id})")
            return True
            
        except Exception as e:
            logger.error(f"Error unassigning channel {channel_name} from user {user_id}: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def get_user_channels_from_db(self, user_id: int) -> List[str]:
        """Get list of channels assigned to a user from database."""
        conn = self.get_connection()
        try:
            # Get user info to check role and username
            user = conn.execute("""
                SELECT u.username, r.name as role_name
                FROM users u
                JOIN roles r ON u.role_id = r.id
                WHERE u.id = ?
            """, (user_id,)).fetchone()
            
            if not user:
                return []
            
            channels = []
            
            # Get explicitly assigned channels
            assigned_channels = conn.execute("""
                SELECT channel_name 
                FROM user_channels 
                WHERE user_id = ? AND is_active = 1
            """, (user_id,)).fetchall()
            
            channels.extend([channel['channel_name'] for channel in assigned_channels])
            
            # For streamers, also check channels they own by username or owner field
            if user['role_name'] == 'streamer':
                # Connect to messages.db for channel ownership check
                import sqlite3
                msg_conn = sqlite3.connect('messages.db')
                msg_conn.row_factory = sqlite3.Row
                try:
                    owned_channels = msg_conn.execute("""
                        SELECT channel_name 
                        FROM channel_configs 
                        WHERE owner = ? OR channel_name = ?
                    """, (user['username'], user['username'])).fetchall()
                    
                    for channel in owned_channels:
                        if channel['channel_name'] not in channels:
                            channels.append(channel['channel_name'])
                finally:
                    msg_conn.close()
            
            return channels
            
        except Exception as e:
            logger.error(f"Error getting channels for user {user_id}: {e}")
            return []
        finally:
            conn.close()
    
    def update_streamer_permissions(self):
        """Update existing streamer roles with new permissions including channels.view_own"""
        conn = self.get_connection()
        try:
            # Get current streamer role
            streamer_role = conn.execute("""
                SELECT id, permissions FROM roles WHERE name = 'streamer'
            """).fetchone()
            
            if not streamer_role:
                logger.warning("Streamer role not found")
                return False
            
            # Parse current permissions
            import json
            current_permissions = json.loads(streamer_role['permissions'])
            
            # Add channels.view_own if missing
            if 'channels.view_own' not in current_permissions:
                current_permissions.append('channels.view_own')
                
                # Update role permissions
                conn.execute("""
                    UPDATE roles 
                    SET permissions = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE name = 'streamer'
                """, (json.dumps(current_permissions),))
                
                logger.info("Updated streamer role with channels.view_own permission")
            
            conn.commit()
            return True
            
        except Exception as e:
            logger.error(f"Error updating streamer permissions: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()
    
    def ensure_streamer_channel_access(self, user_id: int, username: str):
        """Ensure streamer has access to their channel by creating/assigning it"""
        # Connect to messages.db for channel operations
        import sqlite3
        msg_conn = sqlite3.connect('messages.db')
        msg_conn.row_factory = sqlite3.Row
        
        user_conn = self.get_connection()
        
        try:
            # Check if channel exists for this username in messages.db
            channel = msg_conn.execute("""
                SELECT channel_name FROM channel_configs 
                WHERE channel_name = ? OR owner = ?
            """, (username, username)).fetchone()
            
            # If no channel exists, create one
            if not channel:
                logger.info(f"Creating channel for streamer: {username}")
                msg_conn.execute("""
                    INSERT INTO channel_configs (
                        channel_name, owner, tts_enabled, voice_enabled, 
                        join_channel, lines_between_messages, time_between_messages,
                        voice_preset, bark_model, trusted_users, ignored_users, use_general_model
                    ) VALUES (?, ?, 1, 1, 1, 100, 0, 'v2/en_speaker_6', 'regular', '', '', 1)
                """, (username, username))
                
                channel_name = username
            else:
                channel_name = channel['channel_name']
                
                # Ensure owner field is set correctly
                msg_conn.execute("""
                    UPDATE channel_configs 
                    SET owner = ? 
                    WHERE channel_name = ? AND (owner IS NULL OR owner = '')
                """, (username, channel_name))
            
            # Assign user to channel in user_channels table (in users.db)
            user_conn.execute("""
                INSERT OR REPLACE INTO user_channels (user_id, channel_name, assigned_by, is_active)
                VALUES (?, ?, ?, 1)
            """, (user_id, channel_name, user_id))  # Self-assigned
            
            msg_conn.commit()
            user_conn.commit()
            logger.info(f"Ensured streamer {username} has access to channel {channel_name}")
            return channel_name
            
        except Exception as e:
            logger.error(f"Error ensuring streamer channel access: {e}")
            msg_conn.rollback()
            user_conn.rollback()
            return None
        finally:
            msg_conn.close()
            user_conn.close()

    def assign_channel_to_user(self, user_id: int, channel_name: str, assigned_by_id: int = None) -> bool:
        """Manually assign a channel to a user and update channel ownership"""
        # Connect to both databases
        import sqlite3
        msg_conn = sqlite3.connect('messages.db')
        msg_conn.row_factory = sqlite3.Row
        user_conn = self.get_connection()
        
        try:
            # Get user info
            user = user_conn.execute("""
                SELECT username, role_id FROM users WHERE id = ?
            """, (user_id,)).fetchone()
            
            if not user:
                logger.error(f"User {user_id} not found")
                return False
            
            # Check if channel exists
            channel = msg_conn.execute("""
                SELECT channel_name, owner FROM channel_configs WHERE channel_name = ?
            """, (channel_name,)).fetchone()
            
            if not channel:
                logger.error(f"Channel {channel_name} not found")
                return False
            
            # Update channel owner to match the user
            msg_conn.execute("""
                UPDATE channel_configs SET owner = ? WHERE channel_name = ?
            """, (user['username'], channel_name))
            
            # Assign user to channel
            assigned_by = assigned_by_id or user_id
            user_conn.execute("""
                INSERT OR REPLACE INTO user_channels (user_id, channel_name, assigned_by, is_active)
                VALUES (?, ?, ?, 1)
            """, (user_id, channel_name, assigned_by))
            
            msg_conn.commit()
            user_conn.commit()
            
            logger.info(f"Assigned channel {channel_name} to user {user['username']} (id: {user_id})")
            return True
            
        except Exception as e:
            logger.error(f"Error assigning channel to user: {e}")
            msg_conn.rollback()
            user_conn.rollback()
            return False
        finally:
            msg_conn.close()
            user_conn.close()

    def get_channel_assignments(self) -> List[Dict[str, Any]]:
        """Get all channel assignments with user details."""
        conn = self.get_connection()
        try:
            assignments = conn.execute("""
                SELECT uc.channel_name, u.username, u.id as user_id, 
                       r.display_name as role, uc.assigned_at,
                       assigned_by_user.username as assigned_by_username
                FROM user_channels uc
                JOIN users u ON uc.user_id = u.id
                JOIN roles r ON u.role_id = r.id
                JOIN users assigned_by_user ON uc.assigned_by = assigned_by_user.id
                WHERE uc.is_active = 1
                ORDER BY uc.channel_name, u.username
            """).fetchall()
            
            return [dict(assignment) for assignment in assignments]
            
        except Exception as e:
            logger.error(f"Error getting channel assignments: {e}")
            return []
        finally:
            conn.close()
    
    ##############################
    # Admin User Management Methods
    ##############################
    
    def get_all_users(self) -> List[Dict[str, Any]]:
        """Get all users with their role information."""
        conn = self.get_connection()
        try:
            users = conn.execute("""
                SELECT u.id as user_id, u.username, u.email, u.created_at, 
                       u.last_login, u.login_attempts, u.role_id,
                       r.name as role_name, r.display_name as role_display_name,
                       COUNT(uc.channel_name) as channel_count
                FROM users u
                JOIN roles r ON u.role_id = r.id
                LEFT JOIN user_channels uc ON u.id = uc.user_id AND uc.is_active = 1
                GROUP BY u.id
                ORDER BY u.created_at DESC
            """).fetchall()
            
            return [dict(user) for user in users]
            
        except Exception as e:
            logger.error(f"Error getting all users: {e}")
            return []
        finally:
            conn.close()
    
    def get_all_roles(self) -> List[Dict[str, Any]]:
        """Get all available roles."""
        conn = self.get_connection()
        try:
            roles = conn.execute("""
                SELECT id, name, display_name, description
                FROM roles
                ORDER BY id
            """).fetchall()
            
            return [dict(role) for role in roles]
            
        except Exception as e:
            logger.error(f"Error getting all roles: {e}")
            return []
        finally:
            conn.close()
    
    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user by ID with role information."""
        conn = self.get_connection()
        try:
            user = conn.execute("""
                SELECT u.id as user_id, u.username, u.email, u.created_at, 
                       u.last_login, u.login_attempts, u.role_id,
                       r.name as role_name, r.display_name as role_display_name
                FROM users u
                JOIN roles r ON u.role_id = r.id
                WHERE u.id = ?
            """, (user_id,)).fetchone()
            
            return dict(user) if user else None
            
        except Exception as e:
            logger.error(f"Error getting user by ID {user_id}: {e}")
            return None
        finally:
            conn.close()
    
    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Get user by username with role information."""
        conn = self.get_connection()
        try:
            user = conn.execute("""
                SELECT u.id as user_id, u.username, u.email, u.created_at, 
                       u.last_login, u.login_attempts, u.role_id,
                       r.name as role_name, r.display_name as role_display_name
                FROM users u
                JOIN roles r ON u.role_id = r.id
                WHERE u.username = ?
            """, (username,)).fetchone()
            
            return dict(user) if user else None
            
        except Exception as e:
            logger.error(f"Error getting user by username {username}: {e}")
            return None
        finally:
            conn.close()
    
    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get user by email with role information."""
        conn = self.get_connection()
        try:
            user = conn.execute("""
                SELECT u.id as user_id, u.username, u.email, u.created_at, 
                       u.last_login, u.login_attempts, u.role_id,
                       r.name as role_name, r.display_name as role_display_name
                FROM users u
                JOIN roles r ON u.role_id = r.id
                WHERE u.email = ?
            """, (email,)).fetchone()
            
            return dict(user) if user else None
            
        except Exception as e:
            logger.error(f"Error getting user by email {email}: {e}")
            return None
        finally:
            conn.close()
    
    def update_user(self, user_id: int, username: str = None, email: str = None, role_id: int = None) -> bool:
        """Update user information."""
        conn = self.get_connection()
        try:
            updates = []
            params = []
            
            if username is not None:
                updates.append("username = ?")
                params.append(username)
            
            if email is not None:
                updates.append("email = ?")
                params.append(email)
            
            if role_id is not None:
                updates.append("role_id = ?")
                params.append(role_id)
            
            if not updates:
                return True  # No updates needed
            
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(user_id)
            
            query = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"
            conn.execute(query, params)
            conn.commit()
            
            return conn.total_changes > 0
            
        except Exception as e:
            logger.error(f"Error updating user {user_id}: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()
    
    def delete_user(self, user_id: int) -> bool:
        """Delete user account and related data."""
        conn = self.get_connection()
        try:
            # Check if this is a protected admin user
            user = conn.execute("""
                SELECT u.username, r.name as role_name 
                FROM users u 
                JOIN roles r ON u.role_id = r.id 
                WHERE u.id = ?
            """, (user_id,)).fetchone()
            
            if user and user['role_name'] in ['super_admin', 'admin'] and user['username'] in ['admin']:
                logger.error(f"Attempted to delete protected admin user: {user['username']} (id: {user_id})")
                raise ValueError(f"Cannot delete protected admin user: {user['username']}")
            
            # Delete in order due to foreign key constraints
            conn.execute("DELETE FROM user_channels WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            
            conn.commit()
            logger.info(f"Deleted user {user['username'] if user else user_id} (id: {user_id})")
            return conn.total_changes > 0
            
        except Exception as e:
            logger.error(f"Error deleting user {user_id}: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()
    
    def assign_channels_to_user(self, user_id: int, channel_names: List[str]) -> bool:
        """Assign multiple channels to a user (replaces existing assignments)."""
        conn = self.get_connection()
        try:
            # First, deactivate all existing assignments
            conn.execute("""
                UPDATE user_channels 
                SET is_active = 0, unassigned_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND is_active = 1
            """, (user_id,))
            
            # Then assign new channels
            for channel_name in channel_names:
                conn.execute("""
                    INSERT OR REPLACE INTO user_channels 
                    (user_id, channel_name, assigned_by, assigned_at, is_active)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP, 1)
                """, (user_id, channel_name, 1))  # Assuming admin user_id = 1
            
            # Auto-upgrade to streamer role if channels assigned and currently viewer
            user = conn.execute("SELECT role_id FROM users WHERE id = ?", (user_id,)).fetchone()
            if user and channel_names:  # If channels are being assigned
                viewer_role = conn.execute("SELECT id FROM roles WHERE name = 'viewer'").fetchone()
                streamer_role = conn.execute("SELECT id FROM roles WHERE name = 'streamer'").fetchone()
                
                if user['role_id'] == viewer_role['id']:
                    conn.execute("UPDATE users SET role_id = ? WHERE id = ?", 
                               (streamer_role['id'], user_id))
            
            conn.commit()
            return True
            
        except Exception as e:
            logger.error(f"Error assigning channels to user {user_id}: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()