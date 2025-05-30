#!/usr/bin/env python3
"""
Emergency Admin User Creation Tool

This script creates an admin user for system recovery purposes.
Only use this if you've lost access to all admin accounts.

Usage: python3 create_admin.py
"""

import sys
import os
import getpass
import hashlib
import secrets
import string

# Add the project root to the path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from utils.user_db import UserDatabase
from utils.security import PasswordValidator, UsernameValidator

def generate_secure_password(length=16):
    """Generate a secure random password that meets security requirements."""
    # Ensure we have all required character types
    password = []
    
    # Add at least one of each required type
    password.append(secrets.choice(string.ascii_uppercase))  # Uppercase
    password.append(secrets.choice(string.ascii_lowercase))  # Lowercase
    password.append(secrets.choice(string.digits))          # Number
    password.append(secrets.choice("!@#$%^&*()"))           # Special char
    
    # Fill the rest with random characters
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()"
    for _ in range(length - 4):
        password.append(secrets.choice(alphabet))
    
    # Shuffle to avoid predictable patterns
    secrets.SystemRandom().shuffle(password)
    return ''.join(password)

def create_emergency_admin():
    """Create an emergency admin user with secure prompts."""
    
    print("=" * 60)
    print("🚨 EMERGENCY ADMIN USER CREATION TOOL 🚨")
    print("=" * 60)
    print()
    print("⚠️  WARNING: This tool creates a super admin user with full system access.")
    print("⚠️  Only use this for emergency recovery if you've lost admin access.")
    print()
    
    # Security confirmation
    confirm = input("Are you sure you want to create an emergency admin user? (type 'YES' to confirm): ")
    if confirm != 'YES':
        print("❌ Operation cancelled.")
        return False
    
    print()
    
    # Check if admin users already exist
    user_db = UserDatabase('users.db')
    conn = user_db.get_connection()
    try:
        existing_admins = conn.execute("""
            SELECT u.username FROM users u 
            JOIN roles r ON u.role_id = r.id 
            WHERE r.name IN ('super_admin', 'admin')
        """).fetchall()
        
        if existing_admins:
            print("⚠️  Existing admin users found:")
            for admin in existing_admins:
                print(f"   - {admin['username']}")
            print()
            confirm2 = input("Admin users already exist. Continue anyway? (type 'YES' to confirm): ")
            if confirm2 != 'YES':
                print("❌ Operation cancelled.")
                return False
            print()
    finally:
        conn.close()
    
    # Get username
    while True:
        username = input("Enter admin username (default: admin): ").strip()
        if not username:
            username = "admin"
        
        # Enhanced username validation
        is_valid, errors = UsernameValidator.validate_username(username)
        if not is_valid:
            print("❌ Username validation failed:")
            for error in errors:
                print(f"   - {error}")
            continue
        
        break
    
    # Get password with options
    print()
    print("Password options:")
    print("1. Enter your own password")
    print("2. Generate a secure random password")
    
    while True:
        choice = input("Choose option (1 or 2): ").strip()
        if choice in ['1', '2']:
            break
        print("❌ Please enter 1 or 2.")
    
    if choice == '1':
        # Manual password entry
        while True:
            password = getpass.getpass("Enter admin password: ")
            
            # Enhanced password validation
            is_valid, errors = PasswordValidator.validate_password(password, username)
            if not is_valid:
                print("❌ Password validation failed:")
                for error in errors:
                    print(f"   - {error}")
                continue
            
            password_confirm = getpass.getpass("Confirm admin password: ")
            if password != password_confirm:
                print("❌ Passwords do not match.")
                continue
            
            break
    else:
        # Generate secure password
        password = generate_secure_password()
        print(f"🔐 Generated secure password: {password}")
        print("⚠️  IMPORTANT: Save this password immediately!")
        input("Press Enter when you have saved the password...")
    
    print()
    
    try:
        # Create the admin user
        print("Creating admin user...")
        admin_id = user_db.create_user(username, password, 'super_admin')
        
        if admin_id:
            print(f"✅ Successfully created admin user:")
            print(f"   Username: {username}")
            print(f"   User ID: {admin_id}")
            print(f"   Role: super_admin")
            print()
            print("🎉 You can now log in to the web interface with these credentials.")
            return True
        else:
            print("❌ Failed to create admin user.")
            return False
            
    except Exception as e:
        print(f"❌ Error creating admin user: {e}")
        return False

def main():
    """Main function."""
    if not os.path.exists('users.db'):
        print("❌ users.db not found. Please run this script from the project root directory.")
        return False
    
    try:
        success = create_emergency_admin()
        if success:
            print("✅ Emergency admin creation completed successfully.")
        else:
            print("❌ Emergency admin creation failed.")
        return success
    except KeyboardInterrupt:
        print("\n❌ Operation cancelled by user.")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

if __name__ == "__main__":
    sys.exit(0 if main() else 1)