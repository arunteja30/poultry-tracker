#!/usr/bin/env python3
"""
Debug login functionality
"""

import sys
sys.path.append('.')

from firebase_config_simple import user_model

def test_login():
    print("Testing login functionality...")
    
    # Try to get the superadmin user
    user = user_model.get_user_by_username('superadmin')
    
    if user:
        print(f"✅ Found user: {user['username']}")
        print(f"   Full name: {user.get('full_name')}")
        print(f"   Role: {user.get('role')}")
        print(f"   Password hash: {user.get('password_hash')[:20]}...")
        
        # Test password check
        password = 'super123'
        is_valid = user_model.check_password(user, password)
        
        if is_valid:
            print(f"✅ Password '{password}' is VALID")
        else:
            print(f"❌ Password '{password}' is INVALID")
            
        # Test wrong password
        wrong_password = 'wrongpassword'
        is_valid_wrong = user_model.check_password(user, wrong_password)
        
        if not is_valid_wrong:
            print(f"✅ Wrong password '{wrong_password}' correctly rejected")
        else:
            print(f"❌ Wrong password '{wrong_password}' incorrectly accepted")
            
    else:
        print("❌ User 'superadmin' not found")
        
        # List all users
        all_users = user_model.get_records("users")
        print(f"Available users: {list(all_users.keys())}")
        for user_id, user_data in all_users.items():
            print(f"  - {user_id}: {user_data.get('username')} ({user_data.get('role')})")

if __name__ == '__main__':
    test_login()
