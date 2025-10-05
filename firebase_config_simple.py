"""
Simplified Firebase Configuration using only Firebase Admin SDK
This version works without pyrebase and is more suitable for server-side applications
"""
import os
import json
from datetime import datetime

try:
    import bcrypt
    BCRYPT_AVAILABLE = True
except ImportError:
    print("Warning: bcrypt not available. Using simple password hashing.")
    BCRYPT_AVAILABLE = False
    import hashlib

try:
    import firebase_admin
    from firebase_admin import credentials, db as admin_db
    FIREBASE_ADMIN_AVAILABLE = True
except ImportError:
    print("Error: Firebase Admin SDK not available. Please install: pip install firebase-admin")
    FIREBASE_ADMIN_AVAILABLE = False
    firebase_admin = None
    admin_db = None

class SimpleFirebaseConfig:
    """Simplified Firebase configuration using only Admin SDK"""
    
    def __init__(self):
        self.admin_db = None
        self.database_url = "https://poultrymanagement-24d27-default-rtdb.asia-southeast1.firebasedatabase.app"
        self.initialize_firebase()
    
    def initialize_firebase(self):
        """Initialize Firebase Admin SDK"""
        if not FIREBASE_ADMIN_AVAILABLE:
            print("Firebase Admin SDK not available. Using mock database.")
            return
        
        try:
            # Check if Firebase is already initialized
            if not firebase_admin._apps:
                # Try to initialize with service account
                service_account_key = os.environ.get('FIREBASE_SERVICE_ACCOUNT_KEY')
                if service_account_key:
                    cred = credentials.Certificate(json.loads(service_account_key))
                else:
                    # Try to find service account file
                    key_file = os.environ.get('FIREBASE_SERVICE_ACCOUNT_PATH', 'firebase-service-account.json')
                    if os.path.exists(key_file):
                        cred = credentials.Certificate(key_file)
                    else:
                        # Check if we're in development mode (no Firebase credentials)
                        if os.environ.get('DEVELOPMENT_MODE', '').lower() == 'true':
                            print("🔧 Development mode detected - using mock database")
                            self.admin_db = None
                            return
                        
                        # For production - try default credentials
                        try:
                            cred = credentials.ApplicationDefault()
                        except Exception as cred_error:
                            print(f"No Firebase credentials found: {cred_error}")
                            print("💡 Tip: Set DEVELOPMENT_MODE=true for local development")
                            raise cred_error
                
                firebase_admin.initialize_app(cred, {
                    'databaseURL': self.database_url
                })
            
            self.admin_db = admin_db
            print("✅ Firebase initialized successfully")
            
        except Exception as e:
            print(f"Firebase initialization error: {e}")
            print("🔧 Running in mock mode - data will not be persisted to Firebase")
            self.admin_db = None

class MockDatabase:
    """Mock Firebase database for local development"""
    
    def __init__(self):
        self.data = {}
        self._load_default_data()
    
    def _load_default_data(self):
        """Load default data from JSON file into mock database"""
        import json
        import os
        
        json_file = 'firebase_default_data.json'
        if os.path.exists(json_file):
            try:
                with open(json_file, 'r') as f:
                    default_data = json.load(f)
                self.data.update(default_data)
                print(f"🔧 Loaded default data into mock database from {json_file}")
            except Exception as e:
                print(f"⚠️  Error loading default data: {e}")
        else:
            print(f"⚠️  Default data file {json_file} not found")
    
    def reference(self, path):
        return MockReference(self.data, path)

class MockReference:
    """Mock Firebase reference"""
    
    def __init__(self, data, path):
        self.data = data
        self.path = path
    
    def push(self, data_to_add):
        # Simulate push operation
        import uuid
        key = str(uuid.uuid4())
        path_parts = self.path.split('/')
        current = self.data
        for part in path_parts:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[key] = data_to_add
        return MockPushResult(key)
    
    def set(self, data_to_set):
        path_parts = self.path.split('/')
        current = self.data
        for part in path_parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[path_parts[-1]] = data_to_set
    
    def update(self, updates):
        path_parts = self.path.split('/')
        current = self.data
        for part in path_parts:
            if part not in current:
                current[part] = {}
            current = current[part]
        current.update(updates)
    
    def get(self):
        path_parts = self.path.split('/')
        current = self.data
        for part in path_parts:
            if part not in current:
                return None
            current = current[part]
        return current
    
    def delete(self):
        path_parts = self.path.split('/')
        current = self.data
        for part in path_parts[:-1]:
            if part not in current:
                return
            current = current[part]
        if path_parts[-1] in current:
            del current[path_parts[-1]]
    
    def order_by_child(self, child):
        return self
    
    def equal_to(self, value):
        return self

class MockPushResult:
    """Mock Firebase push result"""
    
    def __init__(self, key):
        self.key = key

# Global Firebase instance
firebase_config = SimpleFirebaseConfig()

# Create mock database if Firebase is not available
if not firebase_config.admin_db:
    mock_db = MockDatabase()
    firebase_config.admin_db = mock_db
    print("🔧 Mock database initialized - data will be stored in memory only")

class FirebaseModel:
    """Base class for Firebase models using Admin SDK only"""
    
    def __init__(self):
        self.admin_db = firebase_config.admin_db
        
    def generate_key(self):
        """Generate a unique key for new records"""
        import uuid
        return str(uuid.uuid4())
    
    def create_record(self, collection, data):
        """Create a new record"""
        try:
            if hasattr(self.admin_db, 'reference'):
                # Real Firebase Admin SDK
                ref = self.admin_db.reference(collection)
                new_ref = ref.push(data)
                return new_ref.key
            else:
                # Mock database
                ref = self.admin_db.reference(collection)
                result = ref.push(data)
                return result.key
        except Exception as e:
            print(f"Error creating record: {e}")
            return None
    
    def get_record(self, collection, record_id):
        """Get a single record by ID"""
        try:
            if hasattr(self.admin_db, 'reference'):
                return self.admin_db.reference(f"{collection}/{record_id}").get()
            else:
                return self.admin_db.reference(f"{collection}/{record_id}").get()
        except Exception as e:
            print(f"Error getting record: {e}")
            return None
    
    def get_records(self, collection, filters=None):
        """Get multiple records with optional filters"""
        try:
            if hasattr(self.admin_db, 'reference'):
                ref = self.admin_db.reference(collection)
                data = ref.get() or {}
            else:
                ref = self.admin_db.reference(collection)
                data = ref.get() or {}
            
            if not filters:
                return data
            
            # Apply filters
            filtered_data = {}
            for record_id, record_data in data.items():
                match = True
                for key, value in filters.items():
                    if record_data.get(key) != value:
                        match = False
                        break
                if match:
                    filtered_data[record_id] = record_data
            
            return filtered_data
        except Exception as e:
            print(f"Error getting records: {e}")
            return {}
    
    def update_record(self, collection, record_id, data):
        """Update a record"""
        try:
            if hasattr(self.admin_db, 'reference'):
                self.admin_db.reference(f"{collection}/{record_id}").update(data)
            else:
                self.admin_db.reference(f"{collection}/{record_id}").update(data)
            return True
        except Exception as e:
            print(f"Error updating record: {e}")
            return False
    
    def delete_record(self, collection, record_id):
        """Delete a record"""
        try:
            if hasattr(self.admin_db, 'reference'):
                self.admin_db.reference(f"{collection}/{record_id}").delete()
            else:
                self.admin_db.reference(f"{collection}/{record_id}").delete()
            return True
        except Exception as e:
            print(f"Error deleting record: {e}")
            return False

# Password hashing functions
def hash_password(password):
    """Hash a password"""
    if BCRYPT_AVAILABLE:
        return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    else:
        # Fallback to simple hashing (NOT SECURE - for development only)
        return hashlib.sha256(password.encode('utf-8')).hexdigest()

def check_password(password, hashed):
    """Check if password matches hash"""
    if BCRYPT_AVAILABLE:
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    else:
        # Fallback comparison
        return hashlib.sha256(password.encode('utf-8')).hexdigest() == hashed

# Model classes
class CompanyModel(FirebaseModel):
    """Company model for Firebase"""
    
    def __init__(self):
        super().__init__()
        self.collection = "companies"
    
    def create_company(self, name, code, **kwargs):
        """Create a new company"""
        data = {
            'name': name,
            'code': code,
            'address': kwargs.get('address', ''),
            'phone': kwargs.get('phone', ''),
            'email': kwargs.get('email', ''),
            'contact_person': kwargs.get('contact_person', ''),
            'status': kwargs.get('status', 'active'),
            'created_date': datetime.utcnow().isoformat(),
            'created_by': kwargs.get('created_by'),
            'notes': kwargs.get('notes', ''),
            'company_ext1': kwargs.get('company_ext1'),
            'company_ext2': kwargs.get('company_ext2'),
            'company_ext3': kwargs.get('company_ext3'),
            'company_ext4': kwargs.get('company_ext4')
        }
        return self.create_record(self.collection, data)
    
    def get_company_by_code(self, code):
        """Get company by code"""
        companies = self.get_records(self.collection)
        for company_id, company_data in companies.items():
            if company_data.get('code') == code:
                return {'id': company_id, **company_data}
        return None
    
    def get_all_companies(self):
        """Get all companies"""
        companies = self.get_records(self.collection)
        return [{'id': k, **v} for k, v in companies.items()]

class UserModel(FirebaseModel):
    """User model for Firebase"""
    
    def __init__(self):
        super().__init__()
        self.collection = "users"
    
    def create_user(self, username, password, **kwargs):
        """Create a new user"""
        password_hash = hash_password(password)
        
        data = {
            'username': username,
            'password_hash': password_hash,
            'role': kwargs.get('role', 'user'),
            'company_id': kwargs.get('company_id'),
            'full_name': kwargs.get('full_name', ''),
            'email': kwargs.get('email', ''),
            'phone': kwargs.get('phone', ''),
            'status': kwargs.get('status', 'active'),
            'created_date': datetime.utcnow().isoformat(),
            'created_by': kwargs.get('created_by'),
            'modified_by': kwargs.get('modified_by'),
            'modified_date': kwargs.get('modified_date'),
            'last_login': None
        }
        return self.create_record(self.collection, data)
    
    def get_user_by_username(self, username):
        """Get user by username"""
        users = self.get_records(self.collection)
        for user_id, user_data in users.items():
            if user_data.get('username') == username:
                return {'id': user_id, **user_data}
        return None
    
    def check_password(self, user_data, password):
        """Check if password matches"""
        return check_password(password, user_data['password_hash'])
    
    def update_last_login(self, user_id):
        """Update user's last login time"""
        return self.update_record(self.collection, user_id, {
            'last_login': datetime.utcnow().isoformat()
        })

class CycleModel(FirebaseModel):
    """Cycle model for Firebase"""
    
    def __init__(self):
        super().__init__()
        self.collection = "cycles"
    
    def create_cycle(self, company_id, **kwargs):
        """Create a new cycle"""
        data = {
            'company_id': company_id,
            'cycle_number': kwargs.get('cycle_number'),
            'start_date': kwargs.get('start_date'),
            'start_time': kwargs.get('start_time'),
            'start_birds': kwargs.get('start_birds', 0),
            'current_birds': kwargs.get('current_birds', 0),
            'start_feed_bags': kwargs.get('start_feed_bags', 0),
            'driver': kwargs.get('driver', ''),
            'hatchery': kwargs.get('hatchery', ''),
            'farmer_name': kwargs.get('farmer_name', ''),
            'notes': kwargs.get('notes', ''),
            'status': kwargs.get('status', 'active'),
            'end_date': kwargs.get('end_date'),
            'created_by': kwargs.get('created_by'),
            'created_date': datetime.utcnow().isoformat(),
            'modified_by': kwargs.get('modified_by'),
            'modified_date': kwargs.get('modified_date'),
            'cycle_ext1': kwargs.get('cycle_ext1'),
            'cycle_ext2': kwargs.get('cycle_ext2'),
            'cycle_ext3': kwargs.get('cycle_ext3'),
            'cycle_ext4': kwargs.get('cycle_ext4')
        }
        return self.create_record(self.collection, data)
    
    def get_active_cycle_by_company(self, company_id):
        """Get active cycle for a company"""
        cycles = self.get_records(self.collection)
        for cycle_id, cycle_data in cycles.items():
            if (cycle_data.get('company_id') == company_id and 
                cycle_data.get('status') == 'active'):
                return {'id': cycle_id, **cycle_data}
        return None
    
    def archive_cycle(self, cycle_id):
        """Archive a cycle"""
        from datetime import date
        return self.update_record(self.collection, cycle_id, {
            'status': 'archived',
            'end_date': date.today().isoformat()
        })

class DailyModel(FirebaseModel):
    """Daily entries model for Firebase"""
    
    def __init__(self):
        super().__init__()
        self.collection = "daily_entries"
    
    def create_daily_entry(self, company_id, cycle_id, **kwargs):
        """Create a new daily entry"""
        data = {
            'company_id': company_id,
            'cycle_id': cycle_id,
            'entry_date': kwargs.get('entry_date'),
            'mortality': kwargs.get('mortality', 0),
            'feed_bags_consumed': kwargs.get('feed_bags_consumed', 0),
            'birds_survived': kwargs.get('birds_survived', 0),
            'feed_bags_added': kwargs.get('feed_bags_added', 0),
            'avg_weight': kwargs.get('avg_weight', 0.0),
            'avg_feed_per_bird_g': kwargs.get('avg_feed_per_bird_g', 0.0),
            'fcr': kwargs.get('fcr', 0.0),
            'medicines': kwargs.get('medicines', ''),
            'daily_notes': kwargs.get('daily_notes', ''),
            'mortality_rate': kwargs.get('mortality_rate', 0.0),
            'total_mortality': kwargs.get('total_mortality', 0.0),
            'remaining_bags': kwargs.get('remaining_bags', 0.0),
            'total_bags_consumed': kwargs.get('total_bags_consumed', 0.0),
            'created_by': kwargs.get('created_by'),
            'created_date': datetime.utcnow().isoformat(),
            'modified_by': kwargs.get('modified_by'),
            'modified_date': kwargs.get('modified_date'),
            'daily_ext1': kwargs.get('daily_ext1'),
            'daily_ext2': kwargs.get('daily_ext2'),
            'daily_ext3': kwargs.get('daily_ext3'),
            'daily_ext4': kwargs.get('daily_ext4')
        }
        return self.create_record(self.collection, data)
    
    def get_entries_by_cycle(self, cycle_id):
        """Get all daily entries for a cycle"""
        entries = self.get_records(self.collection)
        cycle_entries = []
        for entry_id, entry_data in entries.items():
            if entry_data.get('cycle_id') == cycle_id:
                cycle_entries.append({'id': entry_id, **entry_data})
        return sorted(cycle_entries, key=lambda x: x.get('entry_date', ''))

# Additional models
class MedicineModel(FirebaseModel):
    def __init__(self):
        super().__init__()
        self.collection = "medicines"

class FeedModel(FirebaseModel):
    def __init__(self):
        super().__init__()
        self.collection = "feeds"

class BirdDispatchModel(FirebaseModel):
    def __init__(self):
        super().__init__()
        self.collection = "bird_dispatches"

class WeighingRecordModel(FirebaseModel):
    def __init__(self):
        super().__init__()
        self.collection = "weighing_records"

class ExpenseModel(FirebaseModel):
    def __init__(self):
        super().__init__()
        self.collection = "expenses"

# Initialize models
company_model = CompanyModel()
user_model = UserModel()
cycle_model = CycleModel()
daily_model = DailyModel()
medicine_model = MedicineModel()
feed_model = FeedModel()
dispatch_model = BirdDispatchModel()
weighing_model = WeighingRecordModel()
expense_model = ExpenseModel()

print("Firebase models initialized successfully!")

try:
    existing_companies = company_model.get_all_companies()
    if not existing_companies:
        print("🔧 No companies found. Attempting to import default data...")
        # Import default data function
        try:
            from import_default_data import import_default_data
            import_default_data()
        except ImportError as ie:
            print(f"⚠️  Could not import default data module: {ie}")
        except Exception as de:
            print(f"⚠️  Error importing default data: {de}")
except Exception as e:
    print(f"⚠️  Could not check for existing data: {e}")

print("Firebase models initialized successfully!")
