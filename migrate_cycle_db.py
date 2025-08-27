#!/usr/bin/env python3
"""
Database migration script to add new columns to existing Cycle table
Run this script to update your database schema for cycle history functionality
"""

import sqlite3
import sys
import os

def migrate_database():
    # Check both possible database locations
    db_paths = [
        os.path.join(os.path.dirname(__file__), 'instance', 'poultry.db'),
        os.path.join(os.path.dirname(__file__), 'poultry.db')
    ]
    
    db_path = None
    for path in db_paths:
        if os.path.exists(path):
            db_path = path
            break
    
    if db_path is None:
        print("No database file found. Checked:")
        for path in db_paths:
            print(f"  - {path}")
        return False
    
    print(f"Using database: {db_path}")
    
    try:
        # Connect to the database
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if columns already exist
        cursor.execute("PRAGMA table_info(cycle)")
        columns = [column[1] for column in cursor.fetchall()]
        
        needs_migration = False
        
        # Add status column if it doesn't exist
        if 'status' not in columns:
            print("Adding 'status' column to cycle table...")
            cursor.execute("ALTER TABLE cycle ADD COLUMN status TEXT DEFAULT 'active'")
            # Set all existing cycles to 'active'
            cursor.execute("UPDATE cycle SET status = 'active' WHERE status IS NULL")
            needs_migration = True
        else:
            print("✓ 'status' column already exists")
        
        # Add end_date column if it doesn't exist
        if 'end_date' not in columns:
            print("Adding 'end_date' column to cycle table...")
            cursor.execute("ALTER TABLE cycle ADD COLUMN end_date TEXT")
            needs_migration = True
        else:
            print("✓ 'end_date' column already exists")
        
        if needs_migration:
            # Commit the changes
            conn.commit()
            print("✅ Database migration completed successfully!")
        else:
            print("✅ Database is already up to date!")
        
        # Close the connection
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Error during migration: {e}")
        return False

if __name__ == "__main__":
    print("🔄 Starting database migration for cycle history feature...")
    success = migrate_database()
    
    if success:
        print("\n✅ Migration completed! You can now restart your Flask application.")
    else:
        print("\n❌ Migration failed! Please check the error messages above.")
        sys.exit(1)
