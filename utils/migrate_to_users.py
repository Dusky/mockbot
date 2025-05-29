#!/usr/bin/env python3
"""
ANSV Bot - Migration Script: Single Admin to Multi-User System
Migrates from the current single admin password to a proper user management system.
"""

import os
import sys
import logging
from datetime import datetime

# Add the project root to the path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from utils.user_db import UserDatabase
from configparser import ConfigParser

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def get_current_admin_password():
    """Get the current admin password from settings.conf or settings.example.conf or default."""
    try:
        # Try settings.conf first
        if os.path.exists('settings.conf'):
            admin_config = ConfigParser()
            admin_config.read('settings.conf')
            
            if 'auth' in admin_config and 'admin_password' in admin_config['auth']:
                return admin_config['auth'].get('admin_password')
        
        # Try settings.example.conf as fallback
        if os.path.exists('settings.example.conf'):
            admin_config = ConfigParser()
            admin_config.read('settings.example.conf')
            
            if 'auth' in admin_config and 'admin_password' in admin_config['auth']:
                example_password = admin_config['auth'].get('admin_password')
                if example_password and example_password != 'your_admin_password_here':
                    logger.info("Using admin password from settings.example.conf")
                    return example_password
        
        # Default fallback
        logger.info("Using default admin password")
        return 'admin123'
    except Exception as e:
        logger.warning(f"Could not read admin password from config: {e}")
        return 'admin123'

def create_backup(db_path: str):
    """Create a backup of the current database."""
    if not os.path.exists(db_path):
        logger.info("No existing database to backup")
        return None
    
    backup_path = f"{db_path}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    try:
        import shutil
        shutil.copy2(db_path, backup_path)
        logger.info(f"Database backup created: {backup_path}")
        return backup_path
    except Exception as e:
        logger.error(f"Failed to create database backup: {e}")
        raise

def migrate_to_user_system(db_path: str = 'messages.db'):
    """Main migration function."""
    logger.info("🚀 Starting migration to user management system...")
    
    # Step 1: Create database backup
    logger.info("📋 Step 1: Creating database backup...")
    backup_path = create_backup(db_path)
    
    try:
        # Step 2: Initialize user database system
        logger.info("🗄️ Step 2: Initializing user database...")
        user_db = UserDatabase(db_path)
        
        # Step 3: Create default roles
        logger.info("👥 Step 3: Creating default roles...")
        user_db.create_default_roles()
        
        # Step 4: Get current admin password
        logger.info("🔐 Step 4: Retrieving current admin credentials...")
        current_password = get_current_admin_password()
        logger.info(f"Current admin password: {'*' * len(current_password)}")
        
        # Step 5: Create super admin user
        logger.info("👤 Step 5: Creating super admin user...")
        admin_email = "admin@ansv-bot.local"  # Default admin email
        
        try:
            admin_user_id = user_db.create_user(
                username='admin',
                password=current_password,
                role_name='super_admin',
                email=admin_email
            )
            logger.info(f"✅ Super admin user created with ID: {admin_user_id}")
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                logger.info("ℹ️ Admin user already exists, skipping creation")
            else:
                raise
        
        # Step 6: Create sample users (optional)
        logger.info("👥 Step 6: Creating sample users...")
        sample_users = [
            ('moderator', 'mod123', 'moderator', 'moderator@ansv-bot.local'),
            ('viewer', 'view123', 'viewer', 'viewer@ansv-bot.local')
        ]
        
        for username, password, role, email in sample_users:
            try:
                user_id = user_db.create_user(username, password, role, email)
                logger.info(f"✅ Sample user created: {username} (ID: {user_id})")
            except Exception as e:
                if "UNIQUE constraint failed" in str(e):
                    logger.info(f"ℹ️ User {username} already exists, skipping")
                else:
                    logger.warning(f"⚠️ Could not create sample user {username}: {e}")
        
        # Step 7: Test authentication
        logger.info("🧪 Step 7: Testing authentication system...")
        test_user = user_db.authenticate_user('admin', current_password)
        if test_user:
            logger.info("✅ Authentication test successful!")
            logger.info(f"   Username: {test_user['username']}")
            logger.info(f"   Role: {test_user['role_display_name']}")
            logger.info(f"   Permissions: {len(test_user['permissions'])} permissions")
        else:
            logger.error("❌ Authentication test failed!")
            raise Exception("Authentication system not working properly")
        
        # Step 8: Update settings file with migration info
        logger.info("📝 Step 8: Updating configuration...")
        update_settings_config()
        
        logger.info("🎉 Migration completed successfully!")
        logger.info("=" * 60)
        logger.info("USER ACCOUNTS CREATED:")
        logger.info("=" * 60)
        logger.info(f"Super Admin: admin / {current_password}")
        logger.info(f"Moderator:   moderator / mod123")
        logger.info(f"Viewer:      viewer / view123")
        logger.info("=" * 60)
        logger.info("✅ You can now use the new user management system!")
        logger.info("🔐 Login at /login to access the beta dashboard")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}")
        
        # Restore backup if something went wrong
        if backup_path and os.path.exists(backup_path):
            logger.info(f"🔄 Restoring database from backup: {backup_path}")
            try:
                import shutil
                shutil.copy2(backup_path, db_path)
                logger.info("✅ Database restored from backup")
            except Exception as restore_error:
                logger.error(f"❌ Failed to restore backup: {restore_error}")
        
        raise

def update_settings_config():
    """Update settings.conf with migration information."""
    config_path = 'settings.conf'
    
    try:
        config = ConfigParser()
        if os.path.exists(config_path):
            config.read(config_path)
        
        # Add user system section
        if 'user_system' not in config:
            config.add_section('user_system')
        
        config['user_system']['migrated'] = 'true'
        config['user_system']['migration_date'] = datetime.now().isoformat()
        config['user_system']['version'] = '1.0'
        
        # Write back to file
        with open(config_path, 'w') as configfile:
            config.write(configfile)
        
        logger.info(f"Configuration updated: {config_path}")
        
    except Exception as e:
        logger.warning(f"Could not update settings config: {e}")

def verify_migration(db_path: str = 'messages.db'):
    """Verify that the migration was successful."""
    logger.info("🔍 Verifying migration...")
    
    try:
        user_db = UserDatabase(db_path)
        
        # Test database connection
        conn = user_db.get_connection()
        
        # Check tables exist
        tables = ['users', 'roles', 'user_sessions', 'audit_log']
        for table in tables:
            result = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", 
                (table,)
            ).fetchone()
            if result:
                logger.info(f"✅ Table '{table}' exists")
            else:
                logger.error(f"❌ Table '{table}' missing")
                return False
        
        # Check roles exist
        roles = conn.execute("SELECT name FROM roles").fetchall()
        role_names = [role['name'] for role in roles]
        expected_roles = ['super_admin', 'admin', 'moderator', 'viewer']
        
        for role in expected_roles:
            if role in role_names:
                logger.info(f"✅ Role '{role}' exists")
            else:
                logger.error(f"❌ Role '{role}' missing")
                return False
        
        # Check admin user exists
        admin_user = conn.execute(
            "SELECT username FROM users WHERE username = 'admin'"
        ).fetchone()
        if admin_user:
            logger.info("✅ Admin user exists")
        else:
            logger.error("❌ Admin user missing")
            return False
        
        conn.close()
        logger.info("✅ Migration verification successful!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Migration verification failed: {e}")
        return False

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Migrate ANSV Bot to user management system')
    parser.add_argument('--db', default='messages.db', help='Database file path')
    parser.add_argument('--verify-only', action='store_true', help='Only verify existing migration')
    
    args = parser.parse_args()
    
    try:
        if args.verify_only:
            success = verify_migration(args.db)
        else:
            success = migrate_to_user_system(args.db)
            if success:
                verify_migration(args.db)
        
        sys.exit(0 if success else 1)
        
    except KeyboardInterrupt:
        logger.info("Migration cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Migration failed with error: {e}")
        sys.exit(1)