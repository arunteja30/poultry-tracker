#!/usr/bin/env python3
"""
Test Firebase connection and authentication
"""

import os
import sys
sys.path.append('.')

def test_firebase_connection():
    print("🔧 Testing Firebase connection...")
    
    # Test different connection methods
    print("\n1. Checking environment variables...")
    service_key = os.environ.get('FIREBASE_SERVICE_ACCOUNT_KEY')
    service_path = os.environ.get('FIREBASE_SERVICE_ACCOUNT_PATH')
    dev_mode = os.environ.get('DEVELOPMENT_MODE')
    
    print(f"   FIREBASE_SERVICE_ACCOUNT_KEY: {'✅ Set' if service_key else '❌ Not set'}")
    print(f"   FIREBASE_SERVICE_ACCOUNT_PATH: {'✅ Set' if service_path else '❌ Not set'}")
    print(f"   DEVELOPMENT_MODE: {dev_mode or 'Not set'}")
    
    print("\n2. Checking for service account files...")
    files_to_check = [
        'firebase-service-account.json',
        'serviceAccountKey.json',
        'firebase-adminsdk.json'
    ]
    
    for filename in files_to_check:
        exists = os.path.exists(filename)
        print(f"   {filename}: {'✅ Found' if exists else '❌ Not found'}")
    
    print("\n3. Testing Firebase initialization...")
    try:
        from firebase_config_simple import firebase_config, user_model
        
        if firebase_config.admin_db:
            if hasattr(firebase_config.admin_db, 'reference'):
                print("✅ Connected to real Firebase!")
                
                # Test reading data
                try:
                    users = user_model.get_records("users")
                    print(f"✅ Successfully retrieved {len(users)} users from Firebase")
                    
                    for user_id, user_data in users.items():
                        print(f"   - {user_id}: {user_data.get('username')} ({user_data.get('role')})")
                        
                except Exception as read_error:
                    print(f"❌ Error reading from Firebase: {read_error}")
            else:
                print("⚠️  Using mock database")
        else:
            print("❌ No database connection")
            
    except Exception as e:
        print(f"❌ Firebase initialization error: {e}")
        
    print("\n🔧 Next steps:")
    print("1. Download your Firebase service account key from Firebase Console")
    print("2. Save it as 'firebase-service-account.json' in this directory")
    print("3. Or set FIREBASE_SERVICE_ACCOUNT_KEY environment variable")

if __name__ == '__main__':
    test_firebase_connection()
