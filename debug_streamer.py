#!/usr/bin/env python3
"""
Debug script to check streamer permissions and channel access
"""

import sqlite3
import json
import sys
import os

# Add the project root to the path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from utils.user_db import UserDatabase

def debug_streamer_access(username="skip_skipperson"):
    print(f"🔍 Debugging streamer access for: {username}")
    print("=" * 50)
    
    user_db_file = "users.db"
    messages_db_file = "messages.db"
    user_db = UserDatabase(user_db_file)
    
    # 1. Check if user exists and their role
    conn = sqlite3.connect(user_db_file)
    conn.row_factory = sqlite3.Row
    
    try:
        user = conn.execute("""
            SELECT u.id, u.username, u.status, r.name as role_name, r.permissions
            FROM users u
            JOIN roles r ON u.role_id = r.id
            WHERE u.username = ?
        """, (username,)).fetchone()
        
        if not user:
            print(f"❌ User '{username}' not found in database")
            return
        
        print(f"✅ User found:")
        print(f"   ID: {user['id']}")
        print(f"   Username: {user['username']}")
        print(f"   Status: {user['status']}")
        print(f"   Role: {user['role_name']}")
        
        # Parse permissions
        permissions = json.loads(user['permissions'])
        print(f"   Permissions: {permissions}")
        print(f"   Has channels.view_own: {'channels.view_own' in permissions}")
        print()
        
        # 2. Check channels owned by user
        print("🏠 Checking channel ownership:")
        
        # Check user_channels table
        assigned_channels = conn.execute("""
            SELECT channel_name FROM user_channels 
            WHERE user_id = ? AND is_active = 1
        """, (user['id'],)).fetchall()
        
        print(f"   Assigned channels: {[ch['channel_name'] for ch in assigned_channels]}")
        
        # Check channels by owner field (in messages.db)
        msg_conn = sqlite3.connect(messages_db_file)
        msg_conn.row_factory = sqlite3.Row
        
        owned_channels = msg_conn.execute("""
            SELECT channel_name, owner FROM channel_configs 
            WHERE owner = ? OR channel_name = ?
        """, (username, username)).fetchall()
        
        print(f"   Owned channels: {[(ch['channel_name'], ch['owner']) for ch in owned_channels]}")
        
        # 3. Test get_user_channels_from_db
        print("🔍 Testing get_user_channels_from_db:")
        user_channels = user_db.get_user_channels_from_db(user['id'])
        print(f"   Result: {user_channels}")
        print()
        
        # 4. Check if channel exists for username
        channel = msg_conn.execute("""
            SELECT * FROM channel_configs WHERE channel_name = ?
        """, (username,)).fetchone()
        
        msg_conn.close()
        
        if channel:
            print(f"✅ Channel '{username}' exists:")
            print(f"   Owner: {channel['owner']}")
            print(f"   TTS Enabled: {channel['tts_enabled']}")
            print(f"   Voice Enabled: {channel['voice_enabled']}")
            print(f"   Join Channel: {channel['join_channel']}")
        else:
            print(f"❌ Channel '{username}' does not exist")
            print("   Attempting to create channel...")
            result = user_db.ensure_streamer_channel_access(user['id'], username)
            print(f"   Creation result: {result}")
        
        print()
        
        # 5. Test permission update
        print("🔧 Testing permission update:")
        result = user_db.update_streamer_permissions()
        print(f"   Update result: {result}")
        
        # Re-check permissions after update
        user_updated = conn.execute("""
            SELECT r.permissions FROM users u
            JOIN roles r ON u.role_id = r.id
            WHERE u.username = ?
        """, (username,)).fetchone()
        
        if user_updated:
            updated_permissions = json.loads(user_updated['permissions'])
            print(f"   Updated permissions: {updated_permissions}")
            print(f"   Has channels.view_own: {'channels.view_own' in updated_permissions}")
        
    except Exception as e:
        print(f"❌ Error during debug: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

if __name__ == "__main__":
    import sys
    username = sys.argv[1] if len(sys.argv) > 1 else "skip_skipperson"
    debug_streamer_access(username)