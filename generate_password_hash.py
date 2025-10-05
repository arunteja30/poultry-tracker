#!/usr/bin/env python3
"""
Generate bcrypt hash for default passwords
"""

try:
    import bcrypt
    
    password = 'super123'
    password_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    
    print(f"Password: {password}")
    print(f"Bcrypt Hash: {hashed.decode('utf-8')}")
    
    # Verify the hash works
    if bcrypt.checkpw(password_bytes, hashed):
        print("✅ Hash verification successful!")
    else:
        print("❌ Hash verification failed!")
        
except ImportError:
    # Fallback to simple SHA256 if bcrypt not available
    import hashlib
    
    password = 'super123'
    hash_obj = hashlib.sha256(password.encode('utf-8'))
    hashed = hash_obj.hexdigest()
    
    print(f"Password: {password}")
    print(f"SHA256 Hash: {hashed}")
    print("⚠️  Using SHA256 fallback (bcrypt not available)")
