# Firebase Default Data Setup

This directory contains files to set up default data for the Firebase-based Poultry Tracker application.

## Files Created

1. **`firebase_default_data.json`** - Contains default data structure with sample companies, users, cycles, and other records
2. **`import_default_data.py`** - Python script to import/manage default data
3. **`generate_password_hash.py`** - Utility to generate bcrypt password hashes

## Default User Credentials

The system comes with three pre-configured users:

| Username | Password | Role | Access Level |
|----------|----------|------|--------------|
| `superadmin` | `super123` | Super Admin | Full system access, can manage companies and users |
| `demoadmin` | `super123` | Admin | Can manage cycles, data entry for Demo Farm |
| `demouser` | `super123` | User | Basic data entry for Demo Farm |

## Automatic Import

The system automatically imports default data when:
- No companies exist in the Firebase database
- The `firebase_default_data.json` file is present

## Manual Import Commands

```bash
# Show current data in Firebase
python import_default_data.py show

# Import default data (only if no data exists)
python import_default_data.py import

# Reset all data (⚠️ DANGER - deletes everything)
python import_default_data.py reset

# Reset and then import default data
python import_default_data.py reset-import
```

## Default Data Structure

The default data includes:

### Companies
- **Demo Farm** (code: DEMO) - Sample company for testing

### Users
- **superadmin** - System administrator
- **demoadmin** - Demo company administrator  
- **demouser** - Demo company user

### Sample Data
- 1 active cycle with 5000 birds
- 2 daily entries showing mortality and feed consumption
- Medicine records (vaccines, vitamins)
- Feed purchase records
- Expense records (labor, electricity)
- Sample bird dispatch record with weighing data

## Firebase Configuration

The system uses `firebase_config_simple.py` which:
- Attempts to connect to Firebase Realtime Database
- Falls back to mock database if no Firebase credentials
- Automatically imports default data on first run
- Supports both development and production modes

## Production Setup

For production use with real Firebase:

1. Set up Firebase project and Realtime Database
2. Configure authentication credentials:
   - Set `FIREBASE_SERVICE_ACCOUNT_KEY` environment variable, OR
   - Place service account JSON file as `firebase-service-account.json`
3. Update database URL in `firebase_config_simple.py` if needed
4. The system will automatically use real Firebase instead of mock database

## Security Notes

- Default passwords are `super123` for all users
- **Change these passwords immediately in production!**
- The superadmin account has full system access
- Consider disabling or removing demo accounts in production

## Development Mode

Set `DEVELOPMENT_MODE=true` environment variable to force mock database mode, useful for:
- Local development without Firebase setup
- Testing and debugging
- Offline development

The mock database stores data in memory only and will be lost when the application restarts.
