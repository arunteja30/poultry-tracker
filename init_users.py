#!/usr/bin/env python3
"""
Initialize user management system
Run this script to create User table and default users in production
"""

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
import hashlib
import os

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///poultry.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), default='user')  # 'user' or 'admin'
    
    def set_password(self, password):
        self.password_hash = hashlib.sha256(password.encode()).hexdigest()
    
    def check_password(self, password):
        return self.password_hash == hashlib.sha256(password.encode()).hexdigest()

def init_users():
    """Initialize user management system"""
    with app.app_context():
        try:
            # Create User table if it doesn't exist
            db.create_all()
            print("✓ User table created/verified")
            
            # Create admin user if it doesn't exist
            if not User.query.filter_by(username='admin').first():
                admin_user = User(username='admin', role='admin')
                admin_user.set_password('admin123')
                db.session.add(admin_user)
                print("✓ Created admin user: admin/admin123")
            else:
                print("✓ Admin user already exists")
            
            # Create regular user if it doesn't exist
            if not User.query.filter_by(username='user').first():
                regular_user = User(username='user', role='user')
                regular_user.set_password('user123')
                db.session.add(regular_user)
                print("✓ Created regular user: user/user123")
            else:
                print("✓ Regular user already exists")
            
            db.session.commit()
            print("✓ User initialization completed successfully!")
            
            # Test query
            user_count = User.query.count()
            print(f"✓ Total users in database: {user_count}")
            
        except Exception as e:
            print(f"✗ Error during user initialization: {e}")
            import traceback
            traceback.print_exc()
            db.session.rollback()
            return False
    
    return True

if __name__ == '__main__':
    print("Initializing user management system...")
    success = init_users()
    if success:
        print("\n🎉 User system initialized successfully!")
        print("You can now access /users with admin credentials")
    else:
        print("\n❌ Failed to initialize user system")
