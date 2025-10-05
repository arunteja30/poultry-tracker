#!/usr/bin/env python3
"""
Firebase Default Data Import Script
This script imports default data from firebase_default_data.json into Firebase Realtime Database
"""

import json
import os
from firebase_config_simple import firebase_config

def import_default_data():
    """Import default data from JSON file into Firebase"""
    
    # Check if we have Firebase connection
    if not firebase_config.admin_db:
        print("❌ No Firebase connection available. Running in mock mode.")
        return False
    
    # Load the default data JSON
    json_file = 'firebase_default_data.json'
    if not os.path.exists(json_file):
        print(f"❌ Default data file '{json_file}' not found.")
        return False
    
    try:
        with open(json_file, 'r') as f:
            default_data = json.load(f)
        
        print("📂 Loading default data from firebase_default_data.json...")
        
        # Import each collection
        for collection_name, collection_data in default_data.items():
            print(f"📁 Importing {collection_name}...")
            
            # Check if collection already has data
            try:
                if hasattr(firebase_config.admin_db, 'reference'):
                    # Real Firebase
                    existing_data = firebase_config.admin_db.reference(collection_name).get()
                else:
                    # Mock database
                    existing_data = firebase_config.admin_db.reference(collection_name).get()
                
                if existing_data:
                    print(f"   ⚠️  Collection '{collection_name}' already has data. Skipping...")
                    continue
                
                # Import the data
                if hasattr(firebase_config.admin_db, 'reference'):
                    # Real Firebase
                    firebase_config.admin_db.reference(collection_name).set(collection_data)
                else:
                    # Mock database
                    firebase_config.admin_db.reference(collection_name).set(collection_data)
                
                print(f"   ✅ Imported {len(collection_data)} records to '{collection_name}'")
                
            except Exception as e:
                print(f"   ❌ Error importing '{collection_name}': {e}")
        
        print("\n🎉 Default data import completed!")
        print("\n📋 Default Login Credentials:")
        print("   Super Admin:")
        print("     Username: superadmin")
        print("     Password: super123")
        print("   Demo Admin:")
        print("     Username: demoadmin") 
        print("     Password: super123")
        print("   Demo User:")
        print("     Username: demouser")
        print("     Password: super123")
        
        return True
        
    except Exception as e:
        print(f"❌ Error importing default data: {e}")
        return False

def reset_firebase_data():
    """Reset Firebase data (delete all data) - USE WITH CAUTION!"""
    
    if not firebase_config.admin_db:
        print("❌ No Firebase connection available.")
        return False
    
    print("⚠️  WARNING: This will delete ALL data from Firebase!")
    confirm = input("Type 'DELETE ALL DATA' to confirm: ")
    
    if confirm != 'DELETE ALL DATA':
        print("❌ Operation cancelled.")
        return False
    
    try:
        # Delete all collections
        collections = ['companies', 'users', 'cycles', 'daily_entries', 'medicines', 
                      'feeds', 'expenses', 'bird_dispatches', 'weighing_records']
        
        for collection in collections:
            try:
                if hasattr(firebase_config.admin_db, 'reference'):
                    firebase_config.admin_db.reference(collection).delete()
                else:
                    firebase_config.admin_db.reference(collection).delete()
                print(f"   🗑️  Deleted collection: {collection}")
            except Exception as e:
                print(f"   ⚠️  Error deleting {collection}: {e}")
        
        print("\n🧹 All data deleted from Firebase!")
        return True
        
    except Exception as e:
        print(f"❌ Error resetting data: {e}")
        return False

def show_current_data():
    """Show current data in Firebase"""
    
    if not firebase_config.admin_db:
        print("❌ No Firebase connection available.")
        return
    
    collections = ['companies', 'users', 'cycles', 'daily_entries', 'medicines', 
                  'feeds', 'expenses', 'bird_dispatches', 'weighing_records']
    
    print("📊 Current Firebase Data:")
    print("-" * 50)
    
    for collection in collections:
        try:
            if hasattr(firebase_config.admin_db, 'reference'):
                data = firebase_config.admin_db.reference(collection).get()
            else:
                data = firebase_config.admin_db.reference(collection).get()
            
            count = len(data) if data else 0
            print(f"   {collection}: {count} records")
            
        except Exception as e:
            print(f"   {collection}: Error - {e}")

if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        
        if command == 'import':
            import_default_data()
        elif command == 'reset':
            reset_firebase_data()
        elif command == 'show':
            show_current_data()
        elif command == 'reset-import':
            if reset_firebase_data():
                print("\n" + "="*50)
                import_default_data()
        else:
            print("Usage: python import_default_data.py [import|reset|show|reset-import]")
    else:
        print("Firebase Default Data Management")
        print("=" * 40)
        print("Available commands:")
        print("  python import_default_data.py import       - Import default data")
        print("  python import_default_data.py reset        - Reset all data (DELETE)")
        print("  python import_default_data.py show         - Show current data")
        print("  python import_default_data.py reset-import - Reset and import")
        print("\nNote: All passwords for default users are 'super123'")
