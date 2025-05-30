#!/usr/bin/env python3
"""
Debug script to test session and user data
"""

import sys
import os

# Add the project root to the path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from utils.user_db import UserDatabase
from utils.auth import init_auth, login_user, get_current_user, get_user_channels, can_access_channel

def test_session_flow():
    """Test the complete session and authentication flow"""
    print("🧪 Testing session flow for skip user...")
    print("=" * 50)
    
    # Initialize
    user_db = UserDatabase('users.db')
    init_auth(user_db)
    
    # Test login
    print("1. Testing login...")
    success, error, user_data = login_user('skip', 'stream123', '127.0.0.1', 'test-agent')
    print(f"   Login success: {success}")
    print(f"   Error: {error}")
    print(f"   User data: {user_data}")
    
    if success and user_data:
        print(f"\n2. User data contents:")
        for key, value in user_data.items():
            print(f"   {key}: {value}")
        
        print(f"\n3. Testing channel access...")
        channels = user_db.get_user_channels_from_db(user_data['id'])
        print(f"   Direct DB channels: {channels}")
        
        print(f"\n4. Testing auth functions...")
        # This won't work without a real Flask request context
        print("   (Note: auth functions require Flask request context)")
    
    return success, user_data

if __name__ == "__main__":
    test_session_flow()