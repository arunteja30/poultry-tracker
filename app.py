import re
import traceback
from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify, flash, session, make_response
from flask_sqlalchemy import SQLAlchemy
import pandas as pd
import os
from datetime import datetime, date
from io import BytesIO
from functools import wraps
import hashlib
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch

app = Flask(__name__, instance_relative_config=True)
database_url = os.environ.get("url-here")

# if database_url:
#     # Render gives a Postgres connection string starting with "postgres://"
#     # SQLAlchemy prefers "postgresql://", so we fix it
#     if database_url.startswith("postgres://"):
#         database_url = database_url.replace("postgres://", "postgresql://", 1)
#
#     app.config["SQLALCHEMY_DATABASE_URI"] = database_url
# else:
#     # Local development fallback (SQLite file)
#     app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///poultry.db"

SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'your-secret-key-change-this-in-production'  # Change this in production
db = SQLAlchemy(app)

# ---------------- Models ----------------
class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False)  # Short code like 'COMP1', 'COMP2'
    address = db.Column(db.String(500))
    phone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    contact_person = db.Column(db.String(120))
    status = db.Column(db.String(20), default='active')  # 'active' or 'inactive'
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer)  # User ID who created
    notes = db.Column(db.String(500))

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), default='user')  # 'user', 'admin', 'super_admin'
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=True)  # Nullable for super_admin
    full_name = db.Column(db.String(120))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    status = db.Column(db.String(20), default='active')  # 'active' or 'inactive'
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer)  # User ID who created
    last_login = db.Column(db.DateTime)
    
    def set_password(self, password):
        self.password_hash = hashlib.sha256(password.encode()).hexdigest()
    
    def check_password(self, password):
        return self.password_hash == hashlib.sha256(password.encode()).hexdigest()
    
    def get_company(self):
        if self.company_id:
            return Company.query.get(self.company_id)
        return None

class Cycle(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    start_date = db.Column(db.String(50))
    start_time = db.Column(db.String(50))
    start_birds = db.Column(db.Integer)
    current_birds = db.Column(db.Integer)
    start_feed_bags = db.Column(db.Float)
    driver = db.Column(db.String(120))
    notes = db.Column(db.String(500))
    status = db.Column(db.String(20), default='active')  # 'active' or 'archived'
    end_date = db.Column(db.String(50))  # Date when cycle was completed/archived
    # Audit fields
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    modified_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    modified_date = db.Column(db.DateTime)

class Daily(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    cycle_id = db.Column(db.Integer, db.ForeignKey('cycle.id'))
    entry_date = db.Column(db.String(20))
    mortality = db.Column(db.Integer, default=0)
    feed_bags_consumed = db.Column(db.Integer, default=0)
    birds_survived = db.Column(db.Integer, default=0)
    feed_bags_added = db.Column(db.Integer, default=0)
    avg_weight = db.Column(db.Float, default=0.0)      # kg
    avg_feed_per_bird_g = db.Column(db.Float, default=0.0)
    fcr = db.Column(db.Float, default=0.0)
    medicines = db.Column(db.String(250), default="")
    daily_notes = db.Column(db.String(500), nullable=True)
    # New fields for analytics
    mortality_rate = db.Column(db.Float, default=0.0)
    total_mortality = db.Column(db.Integer, default=0.0)
    remaining_bags = db.Column(db.Float, default=0.0)
    total_bags_consumed = db.Column(db.Float, default=0.0)
    # Audit fields
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    modified_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    modified_date = db.Column(db.DateTime)
    # Extension fields for future use (avoid migration problems)
    daily_ext1 = db.Column(db.String(500), nullable=True)  # For daily screen extensions
    daily_ext2 = db.Column(db.String(500), nullable=True)
    daily_ext3 = db.Column(db.Float, nullable=True)
    daily_ext4 = db.Column(db.Float, nullable=True)

class Medicine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    cycle_id = db.Column(db.Integer, db.ForeignKey('cycle.id'), nullable=True)  # Allow null for existing data
    name = db.Column(db.String(120))
    price = db.Column(db.Float, default=0.0)
    qty = db.Column(db.Integer, default=0)
    # Audit fields
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    modified_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    modified_date = db.Column(db.DateTime)
    # Extension fields for medicines screen
    medicine_ext1 = db.Column(db.String(500), nullable=True)  # For medicines screen extensions
    medicine_ext2 = db.Column(db.String(500), nullable=True)
    medicine_ext3 = db.Column(db.Float, nullable=True)
    medicine_ext4 = db.Column(db.Float, nullable=True)


class Feed(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    cycle_id = db.Column(db.Integer, db.ForeignKey('cycle.id'), nullable=True)  # Allow null for existing data
    bill_number = db.Column(db.String(50))
    date = db.Column(db.String(20))
    feed_name = db.Column(db.String(120))
    feed_bags = db.Column(db.Integer, default=0)
    bag_weight = db.Column(db.Float, default=50.0)
    total_feed_kg = db.Column(db.Float, default=0.0)
    price_per_kg = db.Column(db.Float, default=0.0)
    total_cost = db.Column(db.Float, default=0.0)
    # Audit fields
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    modified_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    modified_date = db.Column(db.DateTime)
    # Extension fields for feed management screen
    feed_ext1 = db.Column(db.String(500), nullable=True)  # For feed management screen extensions
    feed_ext2 = db.Column(db.String(500), nullable=True)
    feed_ext3 = db.Column(db.Float, nullable=True)
    feed_ext4 = db.Column(db.Float, nullable=True)

class BirdDispatch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    cycle_id = db.Column(db.Integer, db.ForeignKey('cycle.id'), nullable=False)
    vehicle_no = db.Column(db.String(50), nullable=False)
    driver_name = db.Column(db.String(120), nullable=False)
    dispatch_date = db.Column(db.String(20), nullable=False)
    dispatch_time = db.Column(db.String(20), nullable=False)
    vendor_name = db.Column(db.String(120))
    total_birds = db.Column(db.Integer, default=0)
    total_weight = db.Column(db.Float, default=0.0)  # kg
    avg_weight_per_bird = db.Column(db.Float, default=0.0)  # kg
    notes = db.Column(db.String(500))
    status = db.Column(db.String(20), default='active')  # 'active', 'completed'
    # Audit fields
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    modified_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    modified_date = db.Column(db.DateTime)
    # Extension fields for bird dispatch screen
    dispatch_ext1 = db.Column(db.String(500), nullable=True)  # For bird dispatch screen extensions
    dispatch_ext2 = db.Column(db.String(500), nullable=True)
    dispatch_ext3 = db.Column(db.Float, nullable=True)
    dispatch_ext4 = db.Column(db.Float, nullable=True)

class WeighingRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    dispatch_id = db.Column(db.Integer, db.ForeignKey('bird_dispatch.id'), nullable=False)
    serial_no = db.Column(db.Integer, nullable=False)
    no_of_birds = db.Column(db.Integer, nullable=False)
    weight = db.Column(db.Float, nullable=False)  # kg
    avg_weight_per_bird = db.Column(db.Float, default=0.0)  # kg
    timestamp = db.Column(db.String(20), nullable=False)
    # Audit fields
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    # Extension fields for weighing screen
    weighing_ext1 = db.Column(db.String(500), nullable=True)  # For weighing screen extensions
    weighing_ext2 = db.Column(db.String(500), nullable=True)
    weighing_ext3 = db.Column(db.Float, nullable=True)
    weighing_ext4 = db.Column(db.Float, nullable=True)

class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    cycle_id = db.Column(db.Integer, db.ForeignKey('cycle.id'), nullable=True)  # Allow null for existing data
    name = db.Column(db.String(120), nullable=False)
    date = db.Column(db.String(20), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    notes = db.Column(db.String(500))
    # Audit fields
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_date = db.Column(db.DateTime, default=datetime.utcnow)
    modified_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    modified_date = db.Column(db.DateTime)
    # Extension fields for expenses screen
    expense_ext1 = db.Column(db.String(500), nullable=True)  # For expenses screen extensions
    expense_ext2 = db.Column(db.String(500), nullable=True)
    expense_ext3 = db.Column(db.Float, nullable=True)
    expense_ext4 = db.Column(db.Float, nullable=True)

# ---------------- Safe DB creation ----------------
def init_database():
    """Initialize database tables, default companies and users"""
    try:
        # Always ensure tables exist
        db.create_all()
        print("Database tables created successfully")
        
        
        # Create super admin user (can manage all companies)
        if not User.query.filter_by(username='superadmin').first():
                   super_admin = User(
                       username='superadmin',
                       role='super_admin',
                       company_id=None,  # Super admin is not tied to any specific company
                       full_name='Super Administrator',
                       email='superadmin@example.com',
                       phone='9999999999',
                       status='active',
                       created_date=datetime.utcnow()
                   )
                   super_admin.set_password('super123')
                   db.session.add(super_admin)
                   print("Created super admin user: superadmin/super123")
            
            
        db.session.commit()
        print("Default companies and users created successfully")
        
        # Print summary
        print("\n=== MULTI-COMPANY SETUP COMPLETE ===")
        print("Super Admin: superadmin/super123 (can manage all companies)")
        print("=======================================\n")
        
    except Exception as e:
        db.session.rollback()
        print(f"Database initialization error: {e}")
        import traceback
        traceback.print_exc()

with app.app_context():
    init_database()

# ---------- Helpers ----------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        user = User.query.get(session['user_id'])
        if not user or user.role not in ['admin', 'super_admin']:
            flash('Access denied. Admin privileges required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def super_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        user = User.query.get(session['user_id'])
        if not user or user.role != 'super_admin':
            flash('Access denied. Super Admin privileges required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def get_current_user():
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        if user:
            # Update last login
            user.last_login = datetime.utcnow()
            db.session.commit()
        return user
    return None

def get_active_cycle(user=None):
    """Get active cycle for the current user's company"""
    if not user:
        user = get_current_user()
    
    if not user:
        return None
    
    # Super admin can see all cycles, but should select a company
    if user.role == 'super_admin':
        # For super admin, get the first active cycle (or implement company selection)
        return Cycle.query.filter_by(status='active').order_by(Cycle.id.desc()).first()
    
    if not user.company_id:
        return None
        
    return Cycle.query.filter_by(company_id=user.company_id, status='active').order_by(Cycle.id.desc()).first()

def get_user_company_id():
    """Get the company ID for the current user"""
    user = get_current_user()
    if not user:
        return None
    
    # Super admin should have a way to select company, for now return None
    if user.role == 'super_admin':
        return session.get('selected_company_id')  # We'll implement company selection later
    
    return user.company_id

@app.context_processor
def inject_template_vars():
    """Make company data and other variables available to all templates"""
    def get_all_companies():
        return Company.query.filter_by(status='active').order_by(Company.name).all()
    
    def get_current_company():
        user = get_current_user()
        if not user:
            return None
        
        if user.role == 'super_admin':
            company_id = session.get('selected_company_id')
            if company_id:
                return Company.query.get(company_id)
        
        return user.get_company()
    
    def get_user_by_id(user_id):
        """Get user information by ID for audit trail display"""
        if user_id:
            return User.query.get(user_id)
        return None
    
    return dict(
        get_all_companies=get_all_companies,
        get_current_company=get_current_company,
        get_user_by_id=get_user_by_id
    )

def calc_cumulative_stats(cycle_id):
    rows = Daily.query.filter_by(cycle_id=cycle_id).all()
    cycle = Cycle.query.get(cycle_id)
    
    total_feed = sum(r.feed_bags_consumed for r in rows)
    avg_fcr = round(sum(r.fcr for r in rows if r.fcr>0)/max(1,len([r for r in rows if r.fcr>0])),3) if rows else 0
    avg_weight = round(sum(r.avg_weight for r in rows if r.avg_weight>0)/max(1,len([r for r in rows if r.avg_weight>0])),3) if rows else 0
    total_mort = sum(r.mortality for r in rows)
    
    # Calculate cumulative FCR = total feed consumed (kg) / total weight gained (kg)
    cumulative_fcr = 0
    if cycle and cycle.current_birds > 0 and avg_weight > 0:
        total_feed_kg = total_feed * 50  # Convert bags to kg (50kg per bag)
        # Assume starting weight is 0.045 kg (45g) for day-old chicks
        starting_weight_kg = 0.045
        total_weight_gained_kg = cycle.current_birds * (avg_weight - starting_weight_kg)
        if total_weight_gained_kg > 0:
            cumulative_fcr = round(total_feed_kg / total_weight_gained_kg, 3)
    
    return {
        "total_feed_bags": total_feed, 
        "avg_fcr": avg_fcr, 
        "cumulative_fcr": cumulative_fcr,
        "avg_weight": avg_weight, 
        "total_mortality": total_mort
    }

def calc_todays_fcr(cycle_id):
    """Calculate today's FCR by comparing today's values with yesterday's values"""
    today = date.today().isoformat()
    yesterday = (date.today() - pd.Timedelta(days=1)).isoformat()
    
    today_row = Daily.query.filter_by(cycle_id=cycle_id, entry_date=today).first()
    yesterday_row = Daily.query.filter_by(cycle_id=cycle_id, entry_date=yesterday).first()
    
    if not today_row:
        return None
        
    cycle = Cycle.query.get(cycle_id)
    if not cycle:
        return None
    
    # Calculate today's feed consumption and weight gain
    todays_feed_kg = today_row.feed_bags_consumed * 50  # Convert bags to kg
    
    if yesterday_row and yesterday_row.avg_weight > 0 and today_row.avg_weight > 0:
        # Calculate weight gain from yesterday to today
        weight_gain_per_bird = today_row.avg_weight - yesterday_row.avg_weight
        total_weight_gain_kg = cycle.current_birds * weight_gain_per_bird
        
        if total_weight_gain_kg > 0:
            todays_fcr = round(todays_feed_kg / total_weight_gain_kg, 3)
            return todays_fcr
    
    # Fallback: if no yesterday data, use traditional FCR calculation for today
    if today_row.avg_weight > 0 and cycle.current_birds > 0:
        # Estimate weight gain assuming 45g starting weight and linear growth
        cycle_start_date = datetime.fromisoformat(cycle.start_date).date() if cycle.start_date else date.today()
        days_running = (date.today() - cycle_start_date).days + 1
        
        if days_running > 1:
            # Estimate daily weight gain
            starting_weight_kg = 0.045
            total_weight_gain = today_row.avg_weight - starting_weight_kg
            daily_weight_gain = total_weight_gain / days_running
            estimated_todays_gain = cycle.current_birds * daily_weight_gain
            
            if estimated_todays_gain > 0:
                return round(todays_feed_kg / estimated_todays_gain, 3)
    
    return None

# ---------- Routes ----------

@app.route('/feed_management', methods=['GET', 'POST'])
@login_required
def feed_management():
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('setup'))
        
    if request.method == 'POST':
        bill_number = request.form.get('bill_number', '')
        feed_date = request.form.get('date', date.today().isoformat())
        feed_name = request.form.get('feed_name', '')
        feed_bags = int(request.form.get('feed_bags', 0) or 0)
        bag_weight = float(request.form.get('bag_weight', 50) or 50)
        price_per_kg = float(request.form.get('price_per_kg', 45) or 45)
        total_feed_kg = feed_bags * bag_weight
        total_cost = round(total_feed_kg * price_per_kg, 2)
        
        user = get_current_user()
        company_id = get_user_company_id()
        
        feed = Feed(
            company_id=company_id,
            cycle_id=cycle.id,
            bill_number=bill_number,
            date=feed_date,
            feed_name=feed_name,
            feed_bags=feed_bags,
            bag_weight=bag_weight,
            total_feed_kg=total_feed_kg,
            price_per_kg=price_per_kg,
            total_cost=total_cost,
            created_by=user.id,
            created_date=datetime.utcnow()
        )
        db.session.add(feed)
        db.session.commit()
        flash(f'Feed purchase added successfully for cycle #{cycle.id}!', 'success')
        return redirect(url_for('feed_management'))

    # Filter feeds by current cycle
    feeds = Feed.query.filter_by(cycle_id=cycle.id).order_by(Feed.date.desc()).all()
    total_cost = sum(f.total_cost for f in feeds)
    return render_template('feed_management.html', feeds=feeds, total_cost=total_cost, cycle=cycle)

@app.route('/delete_feed/<int:feed_id>', methods=['POST'])
@admin_required
def delete_feed(feed_id):
    feed = Feed.query.get_or_404(feed_id)
    
    # Check if the feed belongs to the current active cycle
    cycle = get_active_cycle()
    if not cycle or feed.cycle_id != cycle.id:
        flash('Cannot delete feed from a different cycle.', 'error')
        return redirect(url_for('feed_management'))
    
    # Store feed info for flash message
    feed_name = feed.feed_name
    bill_number = feed.bill_number
    
    try:
        db.session.delete(feed)
        db.session.commit()
        flash(f'Feed entry "{feed_name}" (Bill: {bill_number}) has been deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Error deleting feed entry. Please try again.', 'error')
    
    return redirect(url_for('feed_management'))

@app.route('/end_current_cycle', methods=['POST'])
@admin_required
def end_current_cycle():
    cycle = get_active_cycle()
    if cycle:
        cycle.status = 'archived'
        cycle.end_date = date.today().isoformat()
        cycle.notes = f"Ended on {datetime.now().isoformat()} - {cycle.notes or ''}"
        
        # Feed management data is preserved (not deleted) for historical records
        # This maintains consistency with daily data preservation
        
        db.session.commit()
        flash('Current cycle ended and archived. All data has been preserved for historical records. You can now start a new cycle.', 'info')
    else:
        flash('No active cycle found to end.', 'error')
    return redirect(url_for('setup'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            # Update last login time
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            session['user_id'] = user.id
            session['username'] = user.username
            session['role'] = user.role
            session['company_id'] = user.company_id
            
            # For super admin, allow company selection
            if user.role == 'super_admin':
                session['selected_company_id'] = None  # Super admin starts with no company selected
                flash(f'Welcome Super Admin {user.full_name or user.username}! Please select a company to manage or create a new one.', 'success')
           else:
                session['selected_company_id'] = user.company_id
                company = user.get_company()
                company_name = company.name if company else 'Unknown Company'
                flash(f'Welcome {user.full_name or user.username} ({company_name})!', 'success')
            
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('login'))

@app.route('/switch_company/<int:company_id>')
@login_required
def switch_company(company_id):
    user = get_current_user()
    if user.role != 'super_admin':
        flash('Access denied. Only super admin can switch companies.', 'error')
        return redirect(url_for('dashboard'))
    
    company = Company.query.get(company_id)
    if not company:
        flash('Invalid company selected.', 'error')
        return redirect(url_for('dashboard'))
    
    session['selected_company_id'] = company_id
    flash(f'Switched to {company.name}', 'success')
    return redirect(url_for('dashboard'))

@app.route('/')
@login_required
def dashboard():
    cycle = get_active_cycle()
    summary = None
    fcr_series = []
    dates = []
    mortality_series = []
    feedbags_series = []
    weight_series = []
    dashboard_metrics = {}
    
    if cycle:
        today = date.today().isoformat()
        today_row = Daily.query.filter_by(cycle_id=cycle.id, entry_date=today).first()
        rows = Daily.query.filter_by(cycle_id=cycle.id).order_by(Daily.entry_date).all()
        
        # Basic calculations
        total_consumed = sum(r.feed_bags_consumed for r in rows)
        total_mort = sum(r.mortality for r in rows)
        total_feed_added = sum(r.feed_bags_added for r in rows)
        
        # Chart data - arrays already initialized above
        
        for r in rows:
            dates.append(r.entry_date)
            fcr_series.append(round(r.fcr,3) if r.fcr else None)
            mortality_series.append(r.mortality)
            feedbags_series.append(r.feed_bags_consumed)
            weight_series.append(int(r.avg_weight * 1000) if r.avg_weight else 0)  # Convert kg to grams
            
        # Advanced metrics
        stats = calc_cumulative_stats(cycle.id)
        survival_rate = round(((cycle.current_birds / cycle.start_birds) * 100), 2) if cycle.start_birds > 0 else 0
        feed_efficiency = round((total_consumed*50 / cycle.current_birds), 2) if cycle.current_birds > 0 else 0
        
        # Calculate days running from cycle start date
        if cycle.start_date:
            try:
                cycle_start_date = datetime.fromisoformat(cycle.start_date).date()
                days_running = (date.today() - cycle_start_date).days
            except (ValueError, TypeError):
                # Fallback if date parsing fails
                days_running = 1
        else:
            days_running = 1
            
        avg_mortality_per_day = round((total_mort / max(days_running, 1)), 2)

        # Feed cost calculations (₹45 per kg, 50kg per bag = ₹2250 per bag)
        feed_cost_per_kg = 45
        feed_cost_per_bag = feed_cost_per_kg * 50  # ₹2250 per bag
        total_feed_cost = total_consumed * feed_cost_per_bag
        feed_cost_per_bird = round((total_feed_cost / cycle.current_birds), 2) if cycle.current_birds > 0 else 0
        
        # Performance indicators
        last_week_rows = [r for r in rows if r.entry_date >= (date.today() - pd.Timedelta(days=7)).isoformat()]
        last_week_mortality = sum(r.mortality for r in last_week_rows)
        last_week_avg_fcr = round(sum(r.fcr for r in last_week_rows if r.fcr > 0) / max(len([r for r in last_week_rows if r.fcr > 0]), 1), 3) if last_week_rows else 0
        
        dashboard_metrics = {
            # Performance Metrics
            "survival_rate": survival_rate,
            "feed_efficiency": feed_efficiency,
            "avg_mortality_per_day": avg_mortality_per_day,
            "last_week_mortality": last_week_mortality,
            "last_week_avg_fcr": last_week_avg_fcr,
            
            # Financial Metrics
            "total_feed_cost": total_feed_cost,
            "feed_cost_per_bird": feed_cost_per_bird,
            "feed_cost_per_bag": feed_cost_per_bag,
            
            # Operational Metrics
            "total_feed_added": total_feed_added,
            "feed_utilization": round(((total_consumed / (cycle.start_feed_bags + total_feed_added)) * 100), 2) if (cycle.start_feed_bags + total_feed_added) > 0 else 0,
            "days_to_target": max(42 - days_running, 0),  # Assuming 42-day cycle
            
            # Today's metrics
            "today_mortality": today_row.mortality if today_row else 0,
            "today_feed_consumed": today_row.feed_bags_consumed if today_row else 0,
            "today_avg_weight": today_row.avg_weight if today_row else 0,
        }
        
        bags_available = db.session.query(db.func.sum(Feed.feed_bags)).filter_by(cycle_id=cycle.id).scalar() or 0
        summary = {
            "start_birds": cycle.start_birds,
            "current_birds": cycle.current_birds,
            "start_date": cycle.start_date,
            "days": days_running,
            "bags_available": bags_available,
            "feed_bags_consumed_total": sum(r.feed_bags_consumed for r in rows),
            "mortality_total": total_mort,
            "fcr_today": calc_todays_fcr(cycle.id),
            "cumulative_fcr": stats["cumulative_fcr"],
            "avg_fcr": stats["avg_fcr"],
            "avg_weight": stats["avg_weight"]
        }
    return render_template('dashboard.html', cycle=cycle, summary=summary, fcr_series=fcr_series, dates=dates, mortality_series=mortality_series, feedbags_series=feedbags_series, weight_series=weight_series, metrics=dashboard_metrics)

@app.route('/setup', methods=['GET','POST'])
@admin_required
def setup():
    existing_cycle = get_active_cycle()
    if request.method=='POST':
        action = request.form.get('action', 'new')
        user = get_current_user()
        company_id = get_user_company_id()
        
        if action == 'reset' and existing_cycle:
            # Archive current cycle
            existing_cycle.status = 'archived'
            existing_cycle.end_date = date.today().isoformat()
            existing_cycle.notes = f"Archived on {datetime.now().isoformat()} - {existing_cycle.notes}"
            existing_cycle.modified_by = user.id
            existing_cycle.modified_date = datetime.utcnow()
            
            # Feed management data is preserved (not deleted) for historical records
            # This maintains consistency with daily data preservation
            
        start_birds = int(request.form.get('start_birds',0))
        start_feed_bags = float(request.form.get('start_feed_bags',0))
        start_date = request.form.get('start_date') or date.today().isoformat()
        start_time = request.form.get('start_time') or datetime.now().time().isoformat(timespec='minutes')
        driver = request.form.get('driver','')
        notes = request.form.get('notes','')
        
        c = Cycle(
            company_id=company_id,
            start_date=start_date, 
            start_time=start_time, 
            start_birds=start_birds, 
            current_birds=start_birds, 
            start_feed_bags=start_feed_bags, 
            driver=driver, 
            notes=notes, 
            status='active',
            created_by=user.id,
            created_date=datetime.utcnow()
        )
        db.session.add(c)
        db.session.commit()
        
        if action == 'reset':
            flash('New cycle started successfully!', 'success')
        
        return redirect(url_for('dashboard'))
    
    return render_template('setup.html', existing_cycle=existing_cycle)

@app.route('/import_data', methods=['GET', 'POST'])
@admin_required
def import_data():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file selected', 'error')
            return redirect(request.url)
        
        file = request.files['file']
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(request.url)
        
        if file and file.filename.endswith(('.xlsx', '.xls', '.csv')):
            try:
                # Check if this is an exported Excel file with multiple sheets
                imported_daily_count = 0
                imported_medicine_count = 0
                
                if file.filename.endswith('.csv'):
                    # CSV - only daily data
                    df = pd.read_csv(file)
                    cycle = get_active_cycle()
                    if not cycle:
                        flash('Please setup a cycle first', 'error')
                        return redirect(url_for('setup'))
                    
                    imported_daily_count = import_daily_data(df, cycle)
                
                else:
                    # Excel file - check for multiple sheets
                    excel_file = pd.ExcelFile(file)
                    sheet_names = excel_file.sheet_names
                    
                    cycle = get_active_cycle()
                    if not cycle:
                        flash('Please setup a cycle first', 'error')
                        return redirect(url_for('setup'))
                    
                    # Import Daily Data
                    if 'Daily Data' in sheet_names:
                        daily_df = pd.read_excel(file, sheet_name='Daily Data')
                        imported_daily_count = import_daily_data(daily_df, cycle)
                    elif len(sheet_names) == 1:
                        # Single sheet Excel file - assume it's daily data
                        daily_df = pd.read_excel(file)
                        imported_daily_count = import_daily_data(daily_df, cycle)
                    
                    # Import Medicines Data
                    if 'Medicines' in sheet_names:
                        medicines_df = pd.read_excel(file, sheet_name='Medicines')
                        imported_medicine_count = import_medicines_data(medicines_df)
                
                db.session.commit()
                
                # Success message
                if imported_medicine_count > 0:
                    flash(f'✅ Import successful! {imported_daily_count} daily entries and {imported_medicine_count} medicines imported. / ✅ आयात सफल! {imported_daily_count} दैनिक प्रविष्टियां और {imported_medicine_count} दवाएं आयात की गईं। / ✅ దిగుమతి విజయవంతం! {imported_daily_count} రోజువారీ ఎంట్రీలు మరియు {imported_medicine_count} మందులు దిగుమతి చేయబడ్డాయి।', 'success')
                else:
                    flash(f'✅ Import successful! {imported_daily_count} daily entries imported. / ✅ आयात सफल! {imported_daily_count} दैनिक प्रविष्टियां आयात की गईं। / ✅ దిగుమతి విజయవంతం! {imported_daily_count} రోజువారీ ఎంట్రీలు దిగుమతి చేయబడ్డాయి।', 'success')
                
            except Exception as e:
                flash(f'Import failed: {str(e)} / आयात असफल: {str(e)} / దిగుమతి విఫలమైంది: {str(e)}', 'error')
        
        else:
            flash('केवल Excel (.xlsx, .xls) या CSV फ़ाइलें समर्थित हैं / Only Excel (.xlsx, .xls) or CSV files are supported / Excel (.xlsx, .xls) లేదా CSV ఫైల్‌లు మాత్రమే సపోర్ట్ చేయబడతాయి', 'error')
        
        return redirect(url_for('import_data'))
    
    return render_template('import_data.html')

def import_daily_data(df, cycle):
    """Import daily data from DataFrame"""
    imported_count = 0
    for _, row in df.iterrows():
        try:
            # Check if entry already exists
            existing = Daily.query.filter_by(
                cycle_id=cycle.id, 
                entry_date=str(row.get('date', ''))
            ).first()
            
            if not existing:
                # Auto-calculate avg_feed_per_bird_g for imported data
                entry_date_str = str(row.get('date', date.today().isoformat()))
                mortality = int(row.get('mortality', 0))
                feed_bags_consumed = float(row.get('feed_bags_consumed', 0))
                
                # Calculate live birds after mortality
                live_after = cycle.current_birds - mortality
                
                if live_after > 0:
                    # Get cumulative feed consumption up to this date
                    previous_rows = Daily.query.filter_by(cycle_id=cycle.id).filter(Daily.entry_date < entry_date_str).all()
                    cumulative_feed_consumed = sum(r.feed_bags_consumed for r in previous_rows) + feed_bags_consumed
                    
                    # Calculate days elapsed
                    cycle_start_date = datetime.fromisoformat(cycle.start_date).date()
                    current_entry_date = datetime.fromisoformat(entry_date_str).date()
                    days_elapsed = (current_entry_date - cycle_start_date).days + 1
                    
                    # Calculate avg feed per bird in grams
                    total_feed_grams = cumulative_feed_consumed * 50 * 1000  # bags to grams
                    auto_avg_feed_per_bird_g = round((total_feed_grams / live_after / days_elapsed), 1) if days_elapsed > 0 else 0
                else:
                    auto_avg_feed_per_bird_g = 0
                
                daily_entry = Daily(
                    cycle_id=cycle.id,
                    entry_date=entry_date_str,
                    mortality=mortality,
                    feed_bags_consumed=feed_bags_consumed,
                    feed_bags_added=float(row.get('feed_bags_added', 0)),
                    avg_weight=float(row.get('avg_weight', 0)),
                    avg_feed_per_bird_g=auto_avg_feed_per_bird_g,
                    fcr=float(row.get('fcr', 0)),
                    medicines=str(row.get('medicines', ''))
                )
                db.session.add(daily_entry)
                imported_count += 1
        except Exception as e:
            continue
    
    return imported_count

def import_medicines_data(df):
    """Import medicines data from DataFrame"""
    imported_count = 0
    for _, row in df.iterrows():
        try:
            # Skip the TOTAL row
            medicine_name = str(row.get('medicine_name', '')).strip()
            if medicine_name.upper() == 'TOTAL' or not medicine_name:
                continue
            
            # Check if medicine already exists
            existing = Medicine.query.filter_by(name=medicine_name).first()
            
            if not existing:
                price = float(row.get('price', 0))
                quantity = int(row.get('quantity', 0)) if pd.notna(row.get('quantity', 0)) else 0
                
                medicine = Medicine(
                    name=medicine_name,
                    price=price,
                    qty=quantity
                )
                db.session.add(medicine)
                imported_count += 1
            else:
                # Update existing medicine
                existing.price = float(row.get('price', existing.price))
                if pd.notna(row.get('quantity', 0)):
                    existing.qty = int(row.get('quantity', existing.qty))
                
        except Exception as e:
            continue
    
    return imported_count

@app.route('/reset_cycle', methods=['POST'])
@admin_required
def reset_cycle():
    cycle = get_active_cycle()
    if cycle:
        # Archive current cycle
        cycle.status = 'archived'
        cycle.end_date = date.today().isoformat()
        cycle.notes = f"Archived on {datetime.now().isoformat()} - {cycle.notes}"
        
        # Feed management data is preserved (not deleted) for historical records
        # This maintains consistency with daily data preservation
        
        db.session.commit()
        flash('Current cycle archived and all data preserved for historical records. You can now start a new cycle. / वर्तमान चक्र संग्रहीत किया गया और सभी डेटा ऐतिहासिक रिकॉर्ड के लिए संरक्षित किया गया। अब आप नया चक्र शुरू कर सकते हैं।', 'info')
    
    return redirect(url_for('setup'))

def get_latest_daily(cycle_id):
    """Get the latest Daily entry for a cycle by date."""
    return Daily.query.filter_by(cycle_id=cycle_id).order_by(Daily.entry_date.desc()).first()

@app.route('/daily', methods=['GET','POST'])
@login_required
def daily():
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('setup'))
        
    latest_daily = get_latest_daily(cycle.id)
    
    # Calculate total bags available from Feed table for current cycle only
    total_feed_bags = db.session.query(db.func.sum(Feed.feed_bags)).filter_by(cycle_id=cycle.id).scalar() or 0
    
    # Calculate total bags consumed from Daily entries
    total_consumed = db.session.query(db.func.sum(Daily.feed_bags_consumed)).filter_by(cycle_id=cycle.id).scalar() or 0
    
    # Available bags = Total feed bags - Total consumed
    bags_available = total_feed_bags - total_consumed
    
    # Calculate bags added from Feed table for today's date and current cycle
    today_str = date.today().isoformat()
    # Sum all feed_bags added today from Feed table for current cycle
    feed_bags_added = db.session.query(db.func.sum(Feed.feed_bags)).filter(Feed.date == today_str, Feed.cycle_id == cycle.id).scalar() or 0

    if request.method=='POST':
        entry_date = request.form.get('entry_date') or date.today().isoformat()
        mortality = int(request.form.get('mortality',0))
        feed_bags_consumed = float(request.form.get('feed_bags_consumed',0))
        # feed_bags_added = float(request.form.get('feed_bags_added',0))
        avg_weight_grams = float(request.form.get('avg_weight_grams',0) or 0)
        
        avg_weight = round(avg_weight_grams / 1000, 3) if avg_weight_grams > 0 else 0

        medicines = request.form.get('medicines','')
        daily_notes = request.form.get('daily_notes', '').strip()
        # Calculate live_after for this entry
        live_after = cycle.current_birds - mortality
        
        # Calculate total mortality for this cycle (including previous entries)
        previous_mortality = db.session.query(db.func.sum(Daily.mortality)).filter_by(cycle_id=cycle.id).scalar() or 0
        total_mortality = previous_mortality + mortality
        
        # Calculate total bags consumed for this cycle (including previous entries)
        previous_consumed = db.session.query(db.func.sum(Daily.feed_bags_consumed)).filter_by(cycle_id=cycle.id).scalar() or 0
        total_bags_consumed = previous_consumed + feed_bags_consumed

        # Calculate remaining bags: Total feed bags - Total consumed
        remaining_bags = total_feed_bags - total_bags_consumed
        
        # Calculate mortality rate
        mortality_rate = round((total_mortality / cycle.start_birds * 100), 2) if cycle.start_birds > 0 else 0

        # Validate form data
        if not entry_date or not avg_weight_grams:
            error_message = '⚠️ Please fill in all required fields. / దయచేసి అన్ని అవసరమైన ఫీల్డ్‌లను పూరించండి।'
            meds = Medicine.query.filter_by(cycle_id=cycle.id).order_by(Medicine.name).all()
            return render_template('daily.html', cycle=cycle, meds=meds, bags_available=remaining_bags,
                                   error_data={
                                       'entry_date': entry_date,
                                       'mortality': mortality,
                                       'feed_bags_consumed': feed_bags_consumed,
                                       'feed_bags_added': feed_bags_added,
                                       'avg_weight_grams': avg_weight_grams,
                                       'medicines': medicines,
                                       'error_message': error_message
                                   })

        # Check if feed bags consumed exceeds available bags or leaves less than 1 bag
        bags_after_consumption = remaining_bags
        if bags_after_consumption < 0:
            shortage = abs(bags_after_consumption)
            flash(f'⚠️ Insufficient feed bags! You need {round(shortage)} more bags. Current available: {round(bags_available)}, trying to consume: {round(feed_bags_consumed)}. Please add new bags first. / ⚠️ अपर्याप्त फ़ीड बैग! आपको {round(shortage)} और बैग चाहिए। वर्तमान उपलब्ध: {round(bags_available)}, उपयोग करने की कोशिश: {round(feed_bags_consumed)}। कृपया पहले नए बैग जोड़ें। / ⚠️ తగినంత ఫీడ్ బ్యాగులు లేవు! మీకు {round(shortage)} మరిన్ని బ్యాగులు కావాలి। అందుబాటులో: {round(bags_available)}, వాడటానికి ప్రయత్నిస్తున్నారు: {round(feed_bags_consumed)}. దయచేసి మరిన్ని బ్యాగులు జోడించండి।', 'error')
            meds = Medicine.query.filter_by(cycle_id=cycle.id).order_by(Medicine.name).all()
            return render_template('daily.html', cycle=cycle, meds=meds, bags_available=bags_available,
                                   error_data={
                                       'entry_date': entry_date,
                                       'mortality': mortality,
                                       'feed_bags_consumed': feed_bags_consumed,
                                       'feed_bags_added': feed_bags_added,
                                       'avg_weight_grams': avg_weight_grams,
                                       'medicines': medicines
                                   })
        elif bags_after_consumption < 0:
            flash(f'⚠️ Error: Must maintain at least 1 feed bag in inventory! Current available: {round(bags_available)}, trying to consume: {round(feed_bags_consumed)}, bags added: {round(feed_bags_added)}. This would leave only {round(bags_after_consumption)} bags. / ⚠️ त्रुटि: इन्वेंटरी में कम से कम 1 फ़ीड बैग बनाए रखना चाहिए! / ⚠️ లోపం: ఇన్వెంటరీలో కనీసం 1 ఫీడ్ బ్యాగ్ ఉంచాలి! ప్రస్తుతం అందుబాటులో: {round(bags_available)}, వాడటానికి ప్రయత్నిస్తున్నారు: {round(feed_bags_consumed)}, జోడించిన బ్యాగులు: {round(feed_bags_added)}. దీని వలన కేవలం {round(bags_after_consumption)} బ్యాగులు మిగిలిపోతాయి।', 'error')
            meds = Medicine.query.filter_by(cycle_id=cycle.id).order_by(Medicine.name).all()
            return render_template('daily.html', cycle=cycle, meds=meds, bags_available=bags_available,
                                   error_data={
                                       'entry_date': entry_date,
                                       'mortality': mortality,
                                       'feed_bags_consumed': feed_bags_consumed,
                                       'feed_bags_added': feed_bags_added,
                                       'avg_weight_grams': avg_weight_grams,
                                       'medicines': medicines
                                   })

        # Warn if bags are getting low (less than 3 days worth)
        if bags_after_consumption <= 3:
            flash(f'🟡 Warning: Feed bags are running low! Only {round(bags_after_consumption)} bags remaining. Consider adding new bags soon. / 🟡 चेतावनी: फ़ीड बैग कम हो रहे हैं! केवल {round(bags_after_consumption)} बैग बचे हैं। जल्द ही नए बैग जोड़ने पर विचार करें। / 🟡 హెచ్చరిక: ఫీడ్ బ్యాగులు తక్కువగా అవుతున్నాయి! కేవలం {round(bags_after_consumption)} బ్యాగులు మిగిలి ఉన్నాయి। త్వరలో కొత్త బ్యాగులు జోడించడాన్ని పరిగణించండి।', 'warning')
    
        if live_after > 0:
            # Get all previous entries for cumulative calculation
            previous_rows = Daily.query.filter_by(cycle_id=cycle.id).filter(Daily.entry_date < entry_date).all()
            cumulative_feed_consumed = sum(r.feed_bags_consumed for r in previous_rows) + feed_bags_consumed

            # Calculate days elapsed from cycle start to current entry
            cycle_start_date = datetime.fromisoformat(cycle.start_date).date()
            current_entry_date = datetime.fromisoformat(entry_date).date()
            days_elapsed = (current_entry_date - cycle_start_date).days + 1  # +1 to include current day

            # Calculate average feed per bird in grams
            total_feed_kg = cumulative_feed_consumed * 50  # Convert bags to kg
            total_feed_grams = total_feed_kg * 1000  # Convert kg to grams
            avg_feed_per_bird_g = round((total_feed_grams / live_after / days_elapsed), 1) if days_elapsed > 0 else 0
        else:
            avg_feed_per_bird_g = 0
        try:
            feed_kg = feed_bags_consumed * 50
            live_after = cycle.current_birds - mortality
            fcr = round((total_feed_kg / (avg_weight * live_after)),3) if (avg_weight>0 and live_after>0) else 0
        except Exception:
            fcr = 0
            
        user = get_current_user()
        company_id = get_user_company_id()
        
        row = Daily(
            company_id=company_id,
            cycle_id=cycle.id,
            entry_date=entry_date,
            mortality=mortality,
            feed_bags_consumed=feed_bags_consumed,
            feed_bags_added=feed_bags_added,
            avg_weight=avg_weight,
            avg_feed_per_bird_g=avg_feed_per_bird_g,
            birds_survived=live_after,
            fcr=fcr,
            medicines=medicines,
            daily_notes=daily_notes,
            mortality_rate=mortality_rate,
            total_mortality=total_mortality,
            remaining_bags=remaining_bags,
            total_bags_consumed=total_bags_consumed,
            created_by=user.id,
            created_date=datetime.utcnow()
        )
        cycle.current_birds = cycle.current_birds - mortality
        # Do not update cycle.start_feed_bags here, as available bags is now managed by Feed model
        db.session.add(row)
        db.session.commit()

        # Success message
        if feed_bags_added > 0:
            flash(f'✅ Daily entry saved successfully! Added {feed_bags_added:.1f} bags, consumed {feed_bags_consumed:.1f} bags. Remaining: {bags_after_consumption:.1f} bags. / ✅ दैनिक प्रविष्टि सफलतापूर्वक सहेजी गई! {feed_bags_added:.1f} बैग जोड़े गए, {feed_bags_consumed:.1f} बैग उपयोग किए गए। बचे हुए: {bags_after_consumption:.1f} बैग। / ✅ రోజువారీ ఎంట్రీ విజయవంతంగా సేవ్ చేయబడింది! {feed_bags_added:.1f} బ్యాగులు జోడించబడ్డాయి, {feed_bags_consumed:.1f} బ్యాగులు వాడబడ్డాయి। మిగిలినవి: {bags_after_consumption:.1f} బ్యాగులు।', 'success')
        else:
            flash(f'✅ Daily entry saved successfully! Consumed {feed_bags_consumed:.1f} bags. Remaining: {bags_after_consumption:.1f} bags. / ✅ दैनिक प्रविष्टि सफलतापूर्वक सहेजी गई! {feed_bags_consumed:.1f} बैग उपयोग किए गए। बचे हुए: {bags_after_consumption:.1f} बैग। / ✅ రోజువారీ ఎంట్రీ విజయవంతంగా సేవ్ చేయబడింది! {feed_bags_consumed:.1f} బ్యాగులు వాడబడ్డాయి। మిగిలినవి: {bags_after_consumption:.1f} బ్యాగులు।', 'success')

        return redirect(url_for('dashboard'))
    meds = Medicine.query.filter_by(cycle_id=cycle.id).order_by(Medicine.name).all()
    return render_template('daily.html', cycle=cycle, meds=meds, bags_available=bags_available)

@app.route('/daywise')
@login_required
def daywise():
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('setup'))
    rows = Daily.query.filter_by(cycle_id=cycle.id).order_by(Daily.entry_date.asc()).all()
    
    # Enhance rows with feed_bags_added from Feed table for each entry date
    for row in rows:
        # Match Daily entry_date with Feed.date for current cycle
        feed_added_on_date = db.session.query(db.func.sum(Feed.feed_bags)).filter(
            Feed.date == row.entry_date, 
            Feed.cycle_id == cycle.id
        ).scalar() or 0
        
        # Override the feed_bags_added field with actual data from Feed management
        row.feed_bags_added = feed_added_on_date
    
    return render_template('daywise.html', rows=rows, cycle=cycle)

@app.route('/stats')
@login_required
def stats():
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('setup'))
    
    # Get basic stats
    stats = calc_cumulative_stats(cycle.id)
    
    # Get daily entries for trend analysis
    daily_entries = Daily.query.filter_by(cycle_id=cycle.id).order_by(Daily.entry_date).all()
    
    # Calculate additional statistics for enhanced dashboard
    total_feed_added = sum(entry.feed_bags_added for entry in daily_entries)
    # Use the cycle's current feed bags value which is updated with each daily entry
    current_feed_bags = max(0, round(cycle.start_feed_bags))  # Round to integer for display
    
    # Calculate cycle duration and performance metrics
    if cycle.start_date:
        try:
            cycle_start_date = datetime.fromisoformat(cycle.start_date).date()
            days_running = (date.today() - cycle_start_date).days + 1
        except (ValueError, TypeError):
            days_running = 1
    else:
        days_running = 1
    
    # Performance metrics
    survival_rate = round(((cycle.current_birds / cycle.start_birds) * 100), 2) if cycle.start_birds > 0 else 0
    mortality_rate = round(((stats["total_mortality"] / cycle.start_birds) * 100), 2) if cycle.start_birds > 0 else 0
    feed_efficiency = round((stats["total_feed_bags"] / cycle.current_birds), 2) if cycle.current_birds > 0 else 0
    avg_daily_mortality = round((stats["total_mortality"] / max(days_running, 1)), 2)
    
    # Cost calculations (₹40 per kg, 50kg per bag = ₹2000 per bag)
    feed_cost_per_bag = 2000
    total_feed_cost = stats["total_feed_bags"] * feed_cost_per_bag
    feed_cost_per_bird = round((total_feed_cost / cycle.current_birds), 2) if cycle.current_birds > 0 else 0
    
    # Medicine costs for current cycle
    medicines = Medicine.query.filter_by(cycle_id=cycle.id).all()
    total_medicine_cost = sum(med.price for med in medicines if med.price)
    
    # Weight gain analysis
    starting_weight_kg = 0.045  # 45g day-old chicks
    weight_gain_per_bird = stats["avg_weight"] - starting_weight_kg if stats["avg_weight"] > 0 else 0
    total_weight_gain = weight_gain_per_bird * cycle.current_birds
    
    # Prepare data for charts
    chart_data = {
        'survival_vs_mortality': {
            'labels': ['Live Birds', 'Mortality'],
            'data': [cycle.current_birds, stats["total_mortality"]]
        },
        'feed_distribution': {
            'labels': ['Consumed', 'Remaining'],
            'data': [round(stats["total_feed_bags"]), current_feed_bags]
        },
        'cost_breakdown': {
            'labels': ['Feed Cost', 'Medicine Cost'],
            'data': [total_feed_cost, total_medicine_cost]
        },
        'performance_scores': {
            'survival_rate': survival_rate,
            'feed_efficiency_score': min(100, max(0, 100 - (feed_efficiency * 10))),  # Lower feed per bird is better
            'weight_gain_score': min(100, max(0, (weight_gain_per_bird / 0.002) * 100))  # 2kg target weight gain
        }
    }
    
    # Enhanced stats object
    enhanced_stats = {
        **stats,
        'cycle_duration': days_running,
        'survival_rate': survival_rate,
        'mortality_rate': mortality_rate,
        'feed_efficiency': feed_efficiency,
        'avg_daily_mortality': avg_daily_mortality,
        'total_feed_cost': total_feed_cost,
        'feed_cost_per_bird': feed_cost_per_bird,
        'total_medicine_cost': total_medicine_cost,
        'current_feed_bags': current_feed_bags,
        'total_feed_added': total_feed_added,
        'weight_gain_per_bird': round(weight_gain_per_bird, 3),
        'total_weight_gain': round(total_weight_gain, 2),
        'target_days': 42,  # Standard broiler cycle
        'days_remaining': max(0, 42 - days_running)
    }
    
    return render_template('stats.html', cycle=cycle, stats=enhanced_stats, chart_data=chart_data)

@app.route('/medicines', methods=['GET','POST'])
@login_required
def medicines():
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('setup'))
        
    if request.method=='POST':
        name = request.form.get('name')
        price = float(request.form.get('price',0) or 0)
        qty = int(request.form.get('qty',0) or 0)
        
        user = get_current_user()
        company_id = get_user_company_id()
        
        m = Medicine(
            company_id=company_id,
            cycle_id=cycle.id, 
            name=name, 
            price=price, 
            qty=qty,
            created_by=user.id,
            created_date=datetime.utcnow()
        )
        db.session.add(m)
        db.session.commit()
        flash(f'Medicine "{name}" added successfully for cycle #{cycle.id}!', 'success')
        return redirect(url_for('medicines'))
        
    # Filter medicines by current cycle
    meds = Medicine.query.filter_by(cycle_id=cycle.id).order_by(Medicine.id.desc()).all()
    total_amount = sum(med.price for med in meds)
    return render_template('medicines.html', meds=meds, total_amount=total_amount, cycle=cycle)

@app.route('/expenses', methods=['GET', 'POST'])
@login_required
def expenses():
    """Expense management"""
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('setup'))
        
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        expense_date = request.form.get('date') or date.today().isoformat()
        amount = float(request.form.get('amount', 0) or 0)
        notes = request.form.get('notes', '').strip()
        
        if not name or amount <= 0:
            flash('Name and valid amount are required!', 'error')
            return redirect(url_for('expenses'))
        
        user = get_current_user()
        company_id = get_user_company_id()
        
        expense = Expense(
            company_id=company_id,
            cycle_id=cycle.id, 
            name=name, 
            date=expense_date, 
            amount=amount, 
            notes=notes,
            created_by=user.id,
            created_date=datetime.utcnow()
        )
        db.session.add(expense)
        db.session.commit()
        flash(f'Expense "{name}" added successfully for cycle #{cycle.id}!', 'success')
        return redirect(url_for('expenses'))
    
    # Filter expenses by current cycle
    all_expenses = Expense.query.filter_by(cycle_id=cycle.id).order_by(Expense.date.desc(), Expense.id.desc()).all()
    total_amount = sum(expense.amount for expense in all_expenses)
    
    return render_template('expenses.html', expenses=all_expenses, total_amount=total_amount, date=date, cycle=cycle)

@app.route('/delete_expense/<int:expense_id>', methods=['POST'])
@admin_required
def delete_expense(expense_id):
    """Delete an expense (admin only)"""
    try:
        expense = Expense.query.get_or_404(expense_id)
        expense_name = expense.name
        db.session.delete(expense)
        db.session.commit()
        flash(f'Expense "{expense_name}" deleted successfully!', 'success')
    except Exception as e:
        flash(f'Error deleting expense: {str(e)}', 'error')
    
    return redirect(url_for('expenses'))

@app.route('/bird_dispatch', methods=['GET', 'POST'])
@login_required
def bird_dispatch():
    """Bird dispatch/lifting management"""
    cycle = get_active_cycle()
    if not cycle:
        flash('No active cycle found. Please setup a cycle first.', 'error')
        return redirect(url_for('setup'))
    
    if request.method == 'POST':
        vehicle_no = request.form.get('vehicle_no', '').strip()
        driver_name = request.form.get('driver_name', '').strip()
        vendor_name = request.form.get('vendor_name', '').strip()
        dispatch_date = request.form.get('dispatch_date') or date.today().isoformat()
        dispatch_time = request.form.get('dispatch_time') or datetime.now().time().isoformat(timespec='minutes')
        notes = request.form.get('notes', '').strip()
        
        if not vehicle_no or not driver_name:
            flash('Vehicle number and driver name are required!', 'error')
            return redirect(url_for('bird_dispatch'))
        
        user = get_current_user()
        company_id = get_user_company_id()
        
        # Create new dispatch record
        dispatch = BirdDispatch(
            company_id=company_id,
            cycle_id=cycle.id,
            vehicle_no=vehicle_no,
            driver_name=driver_name,
            vendor_name=vendor_name,
            dispatch_date=dispatch_date,
            dispatch_time=dispatch_time,
            notes=notes,
            status='active',
            created_by=user.id,
            created_date=datetime.utcnow()
        )
        db.session.add(dispatch)
        db.session.commit()
        
        flash(f'Vehicle {vehicle_no} registered for dispatch!', 'success')
        return redirect(url_for('weighing_screen', dispatch_id=dispatch.id))
    
    # Get recent dispatches for current cycle
    recent_dispatches = BirdDispatch.query.filter_by(cycle_id=cycle.id).order_by(BirdDispatch.id.desc()).limit(10).all()
    
    return render_template('bird_dispatch.html', cycle=cycle, recent_dispatches=recent_dispatches, date=date)

@app.route('/weighing_screen/<int:dispatch_id>')
@login_required
def weighing_screen(dispatch_id):
    """Weighing screen for recording bird weights"""
    dispatch = BirdDispatch.query.get_or_404(dispatch_id)
    cycle = get_active_cycle()
    
    if not cycle or dispatch.cycle_id != cycle.id:
        flash('Invalid dispatch record!', 'error')
        return redirect(url_for('bird_dispatch'))
    
    # Get existing weighing records
    weighing_records = WeighingRecord.query.filter_by(dispatch_id=dispatch_id).order_by(WeighingRecord.serial_no).all()
    
    # Calculate totals
    total_birds = sum(record.no_of_birds for record in weighing_records)
    total_weight = sum(record.weight for record in weighing_records)
    avg_weight_per_bird = round(total_weight / total_birds, 3) if total_birds > 0 else 0
    
    return render_template('weighing_screen.html', 
                         dispatch=dispatch, 
                         weighing_records=weighing_records,
                         total_birds=total_birds,
                         total_weight=total_weight,
                         avg_weight_per_bird=avg_weight_per_bird)

@app.route('/add_weighing_record/<int:dispatch_id>', methods=['POST'])
@login_required
def add_weighing_record(dispatch_id):
    """Add a weighing record"""
    BirdDispatch.query.get_or_404(dispatch_id)  # Verify dispatch exists
    
    no_of_birds = int(request.form.get('no_of_birds', 0))
    weight = float(request.form.get('weight', 0))
    device_timestamp = request.form.get('device_timestamp', '')
    
    if no_of_birds <= 0 or weight <= 0:
        flash('Please enter valid number of birds and weight!', 'error')
        return redirect(url_for('weighing_screen', dispatch_id=dispatch_id))
    
    # Get next serial number
    last_record = WeighingRecord.query.filter_by(dispatch_id=dispatch_id).order_by(WeighingRecord.serial_no.desc()).first()
    next_serial = (last_record.serial_no + 1) if last_record else 1
    
    # Calculate average weight per bird for this record
    avg_weight_per_bird = round(weight / no_of_birds, 3)
    
    # Use device timestamp if provided, otherwise use server time
    if device_timestamp:
        try:
            # Parse and format the device timestamp
            from datetime import datetime as dt
            device_dt = dt.fromisoformat(device_timestamp.replace('Z', '+00:00'))
            timestamp_to_use = device_dt.isoformat(timespec='seconds')
        except ValueError:
            # Fallback to server time if device timestamp is invalid
            timestamp_to_use = datetime.now().isoformat(timespec='seconds')
    else:
        timestamp_to_use = datetime.now().isoformat(timespec='seconds')
    
    # Create weighing record
    user = get_current_user()
    company_id = get_user_company_id()
    
    record = WeighingRecord(
        company_id=company_id,
        dispatch_id=dispatch_id,
        serial_no=next_serial,
        no_of_birds=no_of_birds,
        weight=weight,
        avg_weight_per_bird=avg_weight_per_bird,
        timestamp=timestamp_to_use,
        created_by=user.id,
        created_date=datetime.utcnow()
    )
    
    db.session.add(record)
    db.session.commit()
    
    flash(f'Weighing record #{next_serial} added successfully!', 'success')
    return redirect(url_for('weighing_screen', dispatch_id=dispatch_id))

@app.route('/complete_dispatch/<int:dispatch_id>', methods=['POST'])
@login_required
def complete_dispatch(dispatch_id):
    """Complete the dispatch and update cycle bird count"""
    dispatch = BirdDispatch.query.get_or_404(dispatch_id)
    cycle = get_active_cycle()
    
    if not cycle or dispatch.cycle_id != cycle.id:
        flash('Invalid dispatch record!', 'error')
        return redirect(url_for('bird_dispatch'))
    
    # Get all weighing records
    weighing_records = WeighingRecord.query.filter_by(dispatch_id=dispatch_id).all()
    
    if not weighing_records:
        flash('No weighing records found! Please add weighing records before completing dispatch.', 'error')
        return redirect(url_for('weighing_screen', dispatch_id=dispatch_id))
    
    # Calculate totals
    total_birds = sum(record.no_of_birds for record in weighing_records)
    total_weight = sum(record.weight for record in weighing_records)
    avg_weight_per_bird = round(total_weight / total_birds, 3) if total_birds > 0 else 0
    
    # Update dispatch record
    dispatch.total_birds = total_birds
    dispatch.total_weight = total_weight
    dispatch.avg_weight_per_bird = avg_weight_per_bird
    dispatch.status = 'completed'
    
    # Update cycle bird count
    cycle.current_birds = max(0, cycle.current_birds - total_birds)
    
    db.session.commit()
    
    flash(f'Dispatch completed! {total_birds} birds ({total_weight:.1f} kg) sent in vehicle {dispatch.vehicle_no}. Remaining birds: {cycle.current_birds}', 'success')
    return redirect(url_for('bird_dispatch'))

@app.route('/dispatch_history')
@login_required
def dispatch_history():
    """View dispatch history"""
    cycle = get_active_cycle()
    if not cycle:
        flash('No active cycle found.', 'error')
        return redirect(url_for('setup'))
    
    # Get all dispatches for current cycle
    dispatches = BirdDispatch.query.filter_by(cycle_id=cycle.id).order_by(BirdDispatch.id.desc()).all()
    
    # Calculate summary statistics
    total_birds_dispatched = sum(d.total_birds for d in dispatches if d.status == 'completed')
    total_weight_dispatched = sum(d.total_weight for d in dispatches if d.status == 'completed')
    completed_dispatches = len([d for d in dispatches if d.status == 'completed'])
    
    summary = {
        'total_birds_dispatched': total_birds_dispatched,
        'total_weight_dispatched': total_weight_dispatched,
        'completed_dispatches': completed_dispatches,
        'avg_weight_per_bird': round(total_weight_dispatched / total_birds_dispatched, 3) if total_birds_dispatched > 0 else 0
    }
    
    return render_template('dispatch_history.html', 
                         cycle=cycle, 
                         dispatches=dispatches, 
                         summary=summary,
                         date=date)

@app.route('/delete_weighing_record/<int:record_id>', methods=['POST'])
@admin_required
def delete_weighing_record(record_id):
    """Delete a weighing record"""
    record = WeighingRecord.query.get_or_404(record_id)
    dispatch_id = record.dispatch_id
    
    db.session.delete(record)
    db.session.commit()
    
    flash('Weighing record deleted successfully!', 'success')
    return redirect(url_for('weighing_screen', dispatch_id=dispatch_id))

@app.route('/delete_dispatch/<int:dispatch_id>', methods=['POST'])
@admin_required
def delete_dispatch(dispatch_id):
    """Delete a dispatch and all its weighing records"""
    dispatch = BirdDispatch.query.get_or_404(dispatch_id)
    
    # If dispatch is completed, we need to add the birds back to the cycle
    if dispatch.status == 'completed':
        cycle = get_active_cycle()
        if cycle:
            cycle.current_birds += dispatch.total_birds
    
    # Delete all weighing records for this dispatch
    WeighingRecord.query.filter_by(dispatch_id=dispatch.id).delete()
    
    # Delete the dispatch
    db.session.delete(dispatch)
    db.session.commit()
    
    flash(f'Dispatch for vehicle {dispatch.vehicle_no} deleted successfully!', 'success')
    return redirect(url_for('dispatch_history'))

# ---------------- PDF Export Helper Functions ----------------
def create_pdf_report(cycle, title="Farm Report"):
    """Create a comprehensive PDF report that mirrors the HTML layout"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.5*inch, bottomMargin=0.5*inch, 
                          leftMargin=0.5*inch, rightMargin=0.5*inch)
    story = []
    styles = getSampleStyleSheet()
    
    # Custom styles to match web application
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=16,
        spaceAfter=20,
        alignment=1,  # Center alignment
        textColor=colors.darkblue
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=12,
        spaceAfter=10,
        spaceBefore=15,
        textColor=colors.darkblue
    )
    
    subheading_style = ParagraphStyle(
        'SubHeading',
        parent=styles['Heading3'],
        fontSize=10,
        spaceAfter=8,
        spaceBefore=10,
        textColor=colors.black
    )
    
    # Title and header info
    story.append(Paragraph(title, title_style))
    story.append(Paragraph(f"Cycle #{cycle.id} - {cycle.status.title()} - Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles['Normal']))
    story.append(Spacer(1, 15))
    
    # Get all the data we need (same as the HTML templates use)
    daily_entries = Daily.query.filter_by(cycle_id=cycle.id).order_by(Daily.entry_date).all()
    feeds = Feed.query.filter_by(cycle_id=cycle.id).order_by(Feed.date.desc()).all()
    dispatches = BirdDispatch.query.filter_by(cycle_id=cycle.id).order_by(BirdDispatch.dispatch_date.desc()).all()
    medicines = Medicine.query.filter_by(cycle_id=cycle.id).all()
    expenses = Expense.query.filter_by(cycle_id=cycle.id).order_by(Expense.date.desc()).all()
    
    # Calculate stats (like in HTML)
    total_mortality = sum(entry.mortality for entry in daily_entries)
    total_feed_consumed = sum(entry.feed_bags_consumed for entry in daily_entries)
    total_feed_cost = sum(feed.total_cost for feed in feeds) if feeds else 0
    total_medical_cost = sum(med.price * (med.qty or 1) for med in medicines) if medicines else 0
    total_expense_cost = sum(exp.amount for exp in expenses) if expenses else 0
    survival_rate = (cycle.current_birds / cycle.start_birds * 100) if cycle.start_birds > 0 else 0
    avg_fcr = sum(entry.fcr for entry in daily_entries if entry.fcr > 0) / max(1, len([entry for entry in daily_entries if entry.fcr > 0]))
    avg_weight = sum(entry.avg_weight for entry in daily_entries if entry.avg_weight > 0) / max(1, len([entry for entry in daily_entries if entry.avg_weight > 0]))
    
    # Cycle Overview (like the HTML card)
    story.append(Paragraph("🐔 Cycle Overview", heading_style))
    
    overview_data = [
        ['Metric', 'Value', 'Metric', 'Value'],
        ['Start Date', cycle.start_date or 'Not set', 'End Date', cycle.end_date or 'Ongoing'],
        ['Start Time', cycle.start_time or 'N/A', 'Duration', f"{((datetime.now().date() - datetime.strptime(cycle.start_date, '%Y-%m-%d').date()).days + 1) if cycle.start_date else 0} days"],
        ['Driver', cycle.driver or 'N/A', 'Status', cycle.status.title()],
        ['Initial Birds', str(cycle.start_birds or 0), 'Current Birds', str(cycle.current_birds or 0)],
        ['Initial Feed Bags', str(cycle.start_feed_bags or 0), 'Notes', cycle.notes[:30] + '...' if cycle.notes and len(cycle.notes) > 30 else cycle.notes or 'None']
    ]
    
    overview_table = Table(overview_data, colWidths=[1.5*inch, 1.5*inch, 1.5*inch, 1.5*inch])
    overview_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('BACKGROUND', (0, 1), (-1, -1), colors.lightgrey),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),  # First column bold
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),  # Third column bold
    ]))
    story.append(overview_table)
    story.append(Spacer(1, 15))
    
    # Key Metrics (like the HTML metric tiles)
    story.append(Paragraph("📊 Key Performance Metrics", heading_style))
    
    metrics_data = [
        ['Metric', 'Value', 'Metric', 'Value'],
        ['Survival Rate', f"{survival_rate:.1f}%", 'Avg FCR', f"{avg_fcr:.2f}" if avg_fcr > 0 else 'N/A'],
        ['Feed Cost', f"₹{total_feed_cost:.2f}", 'Feed Cost per Bird', f"₹{(total_feed_cost / cycle.current_birds):.2f}" if cycle.current_birds > 0 else 'N/A'],
        ['Avg Weight (kg)', f"{avg_weight:.3f}" if avg_weight > 0 else 'N/A', 'Mortality No.', str(total_mortality)],
        ['Total Bags Consumed', str(total_feed_consumed), 'Mortality Rate', f"{(total_mortality / cycle.start_birds * 100):.2f}%" if cycle.start_birds > 0 else 'N/A'],
        ['Medical Expenses', f"₹{total_medical_cost:.2f}", 'Other Expenses', f"₹{total_expense_cost:.2f}"]
    ]
    
    metrics_table = Table(metrics_data, colWidths=[1.5*inch, 1.5*inch, 1.5*inch, 1.5*inch])
    metrics_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.darkgreen),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('BACKGROUND', (0, 1), (-1, -1), colors.lightblue),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
    ]))
    story.append(metrics_table)
    story.append(Spacer(1, 15))
    
    # Daily Entries (like the HTML table)
    if daily_entries:
        story.append(Paragraph(f"📅 Daily Entries ({len(daily_entries)} entries)", heading_style))
        
        # Show last 15 entries to avoid too long table
        recent_entries = daily_entries[-15:] if len(daily_entries) > 15 else daily_entries
        
        daily_data = [['Day', 'Date', 'Mortality', 'Feed Consumed', 'Avg Weight (g)', 'FCR', 'Medicines']]
        
        total_days = len(daily_entries)
        for i, entry in enumerate(recent_entries):
            day_num = total_days - len(recent_entries) + i + 1
            daily_data.append([
                str(day_num),
                entry.entry_date,
                str(entry.mortality),
                f"{entry.feed_bags_consumed} bags",
                f"{int(entry.avg_weight * 1000) if entry.avg_weight else 0}",
                f"{entry.fcr:.2f}" if entry.fcr else "0.00",
                entry.medicines[:20] + '...' if entry.medicines and len(entry.medicines) > 20 else entry.medicines or '-'
            ])
        
        daily_table = Table(daily_data, colWidths=[0.5*inch, 0.8*inch, 0.7*inch, 0.9*inch, 0.8*inch, 0.5*inch, 1.7*inch])
        daily_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 7),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(daily_table)
        
        if len(daily_entries) > 15:
            story.append(Paragraph(f"Showing last 15 entries out of {len(daily_entries)} total entries", styles['Italic']))
        
        story.append(Spacer(1, 15))
    
    # Feed Management (like HTML table)
    if feeds:
        story.append(Paragraph("🌾 Feed Management", heading_style))
        
        feed_data = [['Date', 'Feed Name', 'Bags', 'Weight/Bag', 'Total Cost']]
        total_cost = 0
        for feed in feeds:
            feed_data.append([
                feed.date,
                feed.feed_name[:25] + '...' if len(feed.feed_name) > 25 else feed.feed_name,
                str(feed.feed_bags),
                f"{feed.bag_weight} kg",
                f"₹{feed.total_cost:.2f}"
            ])
            total_cost += feed.total_cost
        
        feed_data.append(['', 'TOTAL', '', '', f"₹{total_cost:.2f}"])
        
        feed_table = Table(feed_data, colWidths=[1*inch, 2*inch, 0.8*inch, 1*inch, 1.2*inch])
        feed_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkgreen),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('BACKGROUND', (0, 1), (-1, -2), colors.lightgreen),
            ('BACKGROUND', (0, -1), (-1, -1), colors.yellow),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        story.append(feed_table)
        story.append(Spacer(1, 15))
    
    # Medicine Summary (like HTML card)
    if medicines:
        story.append(Paragraph("💊 Medicine Summary", heading_style))
        
        medicine_data = [['Medicine Name', 'Price per Unit', 'Quantity', 'Total Value']]
        total_medicine_value = 0
        for med in medicines:
            value = med.price * (med.qty or 1)
            medicine_data.append([
                med.name[:30] + '...' if len(med.name) > 30 else med.name,
                f"₹{med.price:.2f}",
                str(med.qty or 1),
                f"₹{value:.2f}"
            ])
            total_medicine_value += value
        
        medicine_data.append(['TOTAL', '', '', f"₹{total_medicine_value:.2f}"])
        
        medicine_table = Table(medicine_data, colWidths=[2.5*inch, 1*inch, 1*inch, 1.5*inch])
        medicine_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.purple),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('BACKGROUND', (0, 1), (-1, -2), colors.lavender),
            ('BACKGROUND', (0, -1), (-1, -1), colors.yellow),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        story.append(medicine_table)
        story.append(Spacer(1, 15))
    
    # Expenses Summary (like HTML table)
    if expenses:
        story.append(Paragraph("💰 Expenses Summary", heading_style))
        
        expense_data = [['Expense Name', 'Date', 'Amount', 'Notes']]
        total_expenses = 0
        for expense in expenses:
            expense_data.append([
                expense.name[:25] + '...' if len(expense.name) > 25 else expense.name,
                expense.date,
                f"₹{expense.amount:.2f}",
                expense.notes[:30] + '...' if expense.notes and len(expense.notes) > 30 else expense.notes or '-'
            ])
            total_expenses += expense.amount
        
        expense_data.append(['TOTAL', '', f"₹{total_expenses:.2f}", ''])
        
        expense_table = Table(expense_data, colWidths=[1.8*inch, 1*inch, 1*inch, 2.2*inch])
        expense_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.red),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('BACKGROUND', (0, 1), (-1, -2), colors.mistyrose),
            ('BACKGROUND', (0, -1), (-1, -1), colors.yellow),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        story.append(expense_table)
        story.append(Spacer(1, 15))
    
    # Dispatch History (like HTML table)
    if dispatches:
        story.append(Paragraph("🚚 Dispatch History", heading_style))
        
        dispatch_data = [['Vehicle', 'Driver', 'Date', 'Birds', 'Weight (kg)', 'Avg/Bird (kg)', 'Status']]
        total_birds_dispatched = 0
        total_weight_dispatched = 0
        
        for dispatch in dispatches:
            dispatch_data.append([
                dispatch.vehicle_no,
                dispatch.driver_name[:15] + '...' if len(dispatch.driver_name) > 15 else dispatch.driver_name,
                dispatch.dispatch_date,
                str(dispatch.total_birds) if dispatch.status == 'completed' else 'In Progress',
                f"{dispatch.total_weight:.1f}" if dispatch.status == 'completed' else '-',
                f"{dispatch.avg_weight_per_bird:.3f}" if dispatch.status == 'completed' else '-',
                dispatch.status.title()
            ])
            
            if dispatch.status == 'completed':
                total_birds_dispatched += dispatch.total_birds
                total_weight_dispatched += dispatch.total_weight
        
        # Add summary row
        dispatch_data.append([
            'TOTAL', '', '', 
            str(total_birds_dispatched), 
            f"{total_weight_dispatched:.1f}", 
            f"{(total_weight_dispatched/total_birds_dispatched):.3f}" if total_birds_dispatched > 0 else '0.000',
            'Summary'
        ])
        
        dispatch_table = Table(dispatch_data, colWidths=[0.8*inch, 1*inch, 0.8*inch, 0.8*inch, 0.8*inch, 0.8*inch, 1*inch])
        dispatch_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.orange),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 7),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('BACKGROUND', (0, 1), (-1, -2), colors.lightyellow),
            ('BACKGROUND', (0, -1), (-1, -1), colors.yellow),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(dispatch_table)
        story.append(Spacer(1, 15))
    
    # Financial Summary (like the HTML cards)
    story.append(Paragraph("💹 Financial Summary", heading_style))
    
    financial_data = [
        ['Category', 'Amount (₹)'],
        ['Feed Costs', f"₹{total_feed_cost:.2f}"],
        ['Medicine Costs', f"₹{total_medical_cost:.2f}"],
        ['Other Expenses', f"₹{total_expense_cost:.2f}"],
        ['TOTAL COSTS', f"₹{(total_feed_cost + total_medical_cost + total_expense_cost):.2f}"]
    ]
    
    financial_table = Table(financial_data, colWidths=[3*inch, 2*inch])
    financial_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('BACKGROUND', (0, 1), (-1, -2), colors.lightsteelblue),
        ('BACKGROUND', (0, -1), (-1, -1), colors.gold),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    story.append(financial_table)
    
    # Build PDF
    doc.build(story)
    buffer.seek(0)
    return buffer

# ---------------- In-memory PDF export for Render ----------------
@app.route('/export')
@admin_required
def export():
    cycle = get_active_cycle()
    if not cycle:
        flash('No active cycle found', 'error')
        return redirect(url_for('setup'))
    
    # Generate PDF report
    pdf_buffer = create_pdf_report(cycle, "Complete Farm Data Report")
    
    # Generate filename with current date
    filename = f"complete_farm_data_{cycle.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    
    return send_file(pdf_buffer,
                     download_name=filename,
                     as_attachment=True,
                     mimetype='application/pdf')

@app.route('/export_cycle/<int:cycle_id>')
@admin_required
def export_cycle(cycle_id):
    """Export data for a specific cycle as PDF"""
    cycle = Cycle.query.get_or_404(cycle_id)
    
    # Generate PDF report for specific cycle
    pdf_buffer = create_pdf_report(cycle, f"Cycle {cycle_id} Details Report")
    
    # Generate filename with current date
    filename = f"cycle_{cycle_id}_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    
    return send_file(pdf_buffer,
                     download_name=filename,
                     as_attachment=True,
                     mimetype='application/pdf')

@app.route('/api/current_cycle')
def api_current_cycle():
    cycle = get_active_cycle()
    if not cycle:
        return jsonify({})
    return jsonify({
        'id': cycle.id,
        'start_date': cycle.start_date,
        'start_birds': cycle.start_birds,
        'current_birds': cycle.current_birds,
        'start_feed_bags': cycle.start_feed_bags
    })

@app.route('/recalculate_feed_averages', methods=['POST'])
@admin_required
def recalculate_feed_averages():
    """Utility route to recalculate avg_feed_per_bird_g for all existing entries"""
    cycle = get_active_cycle()
    if not cycle:
        flash('No active cycle found', 'error')
        return redirect(url_for('setup'))
    
    try:
        # Get all entries ordered by date
        rows = Daily.query.filter_by(cycle_id=cycle.id).order_by(Daily.entry_date).all()
        
        for i, row in enumerate(rows):
            # Calculate cumulative feed consumption up to this entry
            previous_rows = rows[:i]  # All entries before current
            cumulative_feed_consumed = sum(r.feed_bags_consumed for r in previous_rows) + row.feed_bags_consumed
            
            # Calculate live birds (we need to work backwards from current to this point)
            birds_at_this_point = cycle.start_birds
            for prev_row in previous_rows:
                birds_at_this_point -= prev_row.mortality
            birds_at_this_point -= row.mortality  # After current entry's mortality
            
            if birds_at_this_point > 0:
                # Calculate days elapsed
                cycle_start_date = datetime.fromisoformat(cycle.start_date).date()
                entry_date = datetime.fromisoformat(row.entry_date).date()
                days_elapsed = (entry_date - cycle_start_date).days + 1
                
                # Calculate avg feed per bird in grams
                total_feed_grams = cumulative_feed_consumed * 50 * 1000  # bags to grams
                row.avg_feed_per_bird_g = round((total_feed_grams / birds_at_this_point / days_elapsed), 1) if days_elapsed > 0 else 0
            else:
                row.avg_feed_per_bird_g = 0
        
        db.session.commit()
        flash('Average feed per bird values recalculated successfully!', 'success')
        
    except Exception as e:
        flash(f'Error recalculating feed averages: {str(e)}', 'error')
    
    return redirect(url_for('daywise'))

@app.route('/edit_daily/<int:entry_id>', methods=['GET', 'POST'])
@admin_required
def edit_daily(entry_id):
    """Edit a daily entry (admin only)"""
    entry = Daily.query.get_or_404(entry_id)
    cycle = get_active_cycle()
    
    if not cycle or entry.cycle_id != cycle.id:
        flash('Entry not found or not part of current cycle', 'error')
        return redirect(url_for('daywise'))
    
    if request.method == 'POST':
        try:
            # Get form data
            mortality = int(request.form.get('mortality', 0))
            feed_bags_consumed = float(request.form.get('feed_bags_consumed', 0))
            feed_bags_added = float(request.form.get('feed_bags_added', 0))
            avg_weight = float(request.form.get('avg_weight', 0))
            medicines = request.form.get('medicines', '').strip()
            daily_notes = request.form.get('daily_notes', '').strip()
            
            # Calculate differences for cycle updates
            mortality_diff = mortality - entry.mortality
            feed_consumed_diff = feed_bags_consumed - entry.feed_bags_consumed
            feed_added_diff = feed_bags_added - entry.feed_bags_added
            
            # Update cycle current_birds and start_feed_bags
            cycle.current_birds -= mortality_diff
            cycle.start_feed_bags = cycle.start_feed_bags - feed_consumed_diff + feed_added_diff
            
            # Update the entry
            entry.mortality = mortality
            entry.feed_bags_consumed = feed_bags_consumed
            entry.feed_bags_added = feed_bags_added
            entry.avg_weight = avg_weight
            entry.medicines = medicines
            entry.daily_notes = daily_notes
            
            # Recalculate derived fields
            entry.avg_feed_per_bird_g = (feed_bags_consumed * 50 * 1000) / cycle.current_birds if cycle.current_birds > 0 else 0
            entry.fcr = round(feed_bags_consumed / (avg_weight * cycle.current_birds / 1000), 2) if avg_weight > 0 and cycle.current_birds > 0 else 0
            
            db.session.commit()
            flash('Daily entry updated successfully!', 'success')
            return redirect(url_for('daywise'))
            
        except Exception as e:
            flash(f'Error updating entry: {str(e)}', 'error')
    
    # For GET request, render edit form with current values
    return render_template('edit_daily.html', entry=entry, cycle=cycle)

@app.route('/delete_daily/<int:entry_id>', methods=['POST'])
@admin_required
def delete_daily(entry_id):
    """Delete a daily entry (admin only)"""
    try:
        entry = Daily.query.get_or_404(entry_id)
        cycle = get_active_cycle()
        
        if cycle and entry.cycle_id == cycle.id:
            # Restore bird count and feed bags before deleting
            cycle.current_birds += entry.mortality
            cycle.start_feed_bags += entry.feed_bags_consumed - entry.feed_bags_added
            
            db.session.delete(entry)
            db.session.commit()
            flash('Daily entry deleted successfully!', 'success')
        else:
            flash('Entry not found or not part of current cycle', 'error')
            
    except Exception as e:
        flash(f'Error deleting entry: {str(e)}', 'error')
    
    return redirect(url_for('daywise'))

@app.route('/delete_medicine/<int:medicine_id>', methods=['POST'])
@admin_required
def delete_medicine(medicine_id):
    """Delete a medicine (admin only)"""
    try:
        medicine = Medicine.query.get_or_404(medicine_id)
        db.session.delete(medicine)
        db.session.commit()
        flash('Medicine deleted successfully!', 'success')
    except Exception as e:
        flash(f'Error deleting medicine: {str(e)}', 'error')
    
    return redirect(url_for('medicines'))

@app.route('/users', methods=['GET', 'POST'])
@admin_required
def users():
    """User management page (admin only)"""
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'create':
            username = request.form.get('username')
            password = request.form.get('password')
            role = request.form.get('role', 'user')
            
            if not username or not password:
                flash('Username and password are required / उपयोगकर्ता नाम और पासवर्ड आवश्यक हैं', 'error')
                return redirect(url_for('users'))
            
            # Check if username already exists
            existing_user = User.query.filter_by(username=username).first()
            if existing_user:
                flash('Username already exists / उपयोगकर्ता नाम पहले से मौजूद है', 'error')
                return redirect(url_for('users'))
            
            try:
                new_user = User(username=username, role=role)
                new_user.set_password(password)
                db.session.add(new_user)
                db.session.commit()
                flash(f'User {username} created successfully! / उपयोगकर्ता {username} सफलतापूर्वक बनाया गया!', 'success')
            except Exception as e:
                db.session.rollback()
                flash(f'Error creating user: {str(e)} / उपयोगकर्ता बनाने में त्रुटि: {str(e)}', 'error')
        
        elif action == 'update':
            user_id = request.form.get('user_id')
            new_username = request.form.get('new_username')
            new_password = request.form.get('new_password')
            new_role = request.form.get('new_role')
            
            try:
                user = User.query.get_or_404(user_id)
                
                # Check if new username already exists (excluding current user)
                if new_username and new_username != user.username:
                    existing_user = User.query.filter_by(username=new_username).first()
                    if existing_user:
                        flash('Username already exists / उपयोगकर्ता नाम पहले से मौजूद है', 'error')
                        return redirect(url_for('users'))
                    user.username = new_username
                
                if new_password:
                    user.set_password(new_password)
                
                if new_role:
                    user.role = new_role
                
                db.session.commit()
                
                # Update session if user changed their own info
                if user.id == session.get('user_id'):
                    session['username'] = user.username
                    session['role'] = user.role
                
                flash(f'User {user.username} updated successfully! / उपयोगकर्ता {user.username} सफलतापूर्वक अपडेट किया गया!', 'success')
            except Exception as e:
                db.session.rollback()
                flash(f'Error updating user: {str(e)} / उपयोगकर्ता अपडेट करने में त्रुटि: {str(e)}', 'error')
        
        return redirect(url_for('users'))
    
    # GET request - fetch all users
    all_users = User.query.order_by(User.username).all()
    return render_template('users.html', users=all_users)

@app.route('/delete_user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    """Delete a user (admin only)"""
    try:
        user = User.query.get_or_404(user_id)
        
        # Prevent admin from deleting themselves
        if user.id == session.get('user_id'):
            flash('You cannot delete your own account / आप अपना खाता नहीं हटा सकते', 'error')
            return redirect(url_for('users'))
        
        # Prevent deletion of the last admin
        admin_count = User.query.filter_by(role='admin').count()
        if user.role == 'admin' and admin_count <= 1:
            flash('Cannot delete the last admin user / अंतिम व्यवस्थापक उपयोगकर्ता को नहीं हटा सकते', 'error')
            return redirect(url_for('users'))
        
        username = user.username
        db.session.delete(user)
        db.session.commit()
        flash(f'User {username} deleted successfully! / उपयोगकर्ता {username} सफलतापूर्वक हटाया गया!', 'success')
        
    except Exception as e:
        flash(f'Error deleting user: {str(e)} / उपयोगकर्ता हटाने में त्रुटि: {str(e)}', 'error')
    
    return redirect(url_for('users'))

@app.route('/cycle_history')
@login_required
def cycle_history():
    """View all cycles (active and archived) for comparison"""
    # Get all cycles ordered by most recent first
    cycles = Cycle.query.order_by(Cycle.id.desc()).all()
    
    cycle_data = []
    for cycle in cycles:
        # Calculate cycle duration
        start_date = None
        if cycle.start_date:
            if isinstance(cycle.start_date, str):
                start_date = datetime.strptime(cycle.start_date, '%Y-%m-%d').date()
            else:
                start_date = cycle.start_date
        
        end_date = date.today()  # Default to today if no end date
        if cycle.end_date:
            if isinstance(cycle.end_date, str):
                end_date = datetime.strptime(cycle.end_date, '%Y-%m-%d').date()
            else:
                end_date = cycle.end_date
            duration_days = (end_date - start_date).days + 1 if start_date else 0
        else:
            duration_days = 0  # Ongoing cycles
        
        # Get daily entries for this cycle
        daily_entries = Daily.query.filter_by(cycle_id=cycle.id).all()
        
        # Calculate cycle statistics
        total_mortality = sum(entry.mortality for entry in daily_entries)
        total_feed_consumed = sum(entry.feed_bags_consumed for entry in daily_entries)
        total_feed_added = sum(entry.feed_bags_added for entry in daily_entries)
        final_birds = cycle.current_birds
        survival_rate = round((final_birds / cycle.start_birds) * 100, 2) if cycle.start_birds > 0 else 0
        avg_fcr = round(sum(entry.fcr for entry in daily_entries if entry.fcr > 0) / max(1, len([entry for entry in daily_entries if entry.fcr > 0])), 2) if daily_entries else 0
        final_weight = daily_entries[-1].avg_weight if daily_entries and daily_entries[-1].avg_weight > 0 else 0
        feed_per_bird = round(total_feed_consumed / cycle.start_birds, 2) if cycle.start_birds > 0 else 0
        mortality_rate = round((total_mortality / cycle.start_birds * 100), 2) if cycle.start_birds > 0 else 0

        # Get dispatch data for this cycle
        dispatches = BirdDispatch.query.filter_by(cycle_id=cycle.id).all()
        total_dispatches = len(dispatches)
        total_birds_dispatched = sum(d.total_birds for d in dispatches if d.status == 'completed')
        total_weight_dispatched = sum(d.total_weight for d in dispatches if d.status == 'completed')

        cycle_data.append({
            'cycle': cycle,
            'duration_days': duration_days,
            'total_mortality': total_mortality,
            'total_feed_consumed': total_feed_consumed,
            'total_feed_added': total_feed_added,
            'final_birds': final_birds,
            'survival_rate': survival_rate,
            'avg_fcr': avg_fcr,
            'final_weight': final_weight,
            'feed_per_bird': feed_per_bird,
            'mortality_rate': mortality_rate,
            'total_dispatches': total_dispatches,
            'total_birds_dispatched': total_birds_dispatched,
            'total_weight_dispatched': total_weight_dispatched
        })
    
    return render_template('cycle_history.html', cycle_data=cycle_data)

@app.route('/cycle_details/<int:cycle_id>')
@login_required
def cycle_details(cycle_id):
    """View detailed information for a specific cycle"""
    cycle = Cycle.query.get_or_404(cycle_id)
    cycles = Cycle.query.all()

    # Calculate duration
    if cycle.start_date:
        if isinstance(cycle.start_date, str):
            start_date = datetime.strptime(cycle.start_date, '%Y-%m-%d').date()
        else:
            start_date = cycle.start_date
            
        if cycle.end_date:
            if isinstance(cycle.end_date, str):
                end_date = datetime.strptime(cycle.end_date, '%Y-%m-%d').date()
            else:
                end_date = cycle.end_date
            duration = (end_date - start_date).days + 1
            current_duration = duration
        else:
            duration = None
            current_duration = (date.today() - start_date).days + 1
    else:
        duration = None
        current_duration = 0
    
    # Get all daily entries for this specific cycle
    daily_entries = Daily.query.filter_by(cycle_id=cycle_id).order_by(Daily.entry_date).all()
    
    # Calculate detailed statistics
    stats = calc_cumulative_stats(cycle_id)
    
    # Prepare chart data
    dates = []
    fcr_series = []
    weight_series = []
    mortality_series = []

    # Process other cycles for comparison data (but don't overwrite daily_entries)
    cycle_data = []
    for other_cycle in cycles:
        # Calculate cycle duration
        start_date_obj = None
        end_date_obj = None
        if other_cycle.start_date:
            try:
                start_date_obj = datetime.fromisoformat(other_cycle.start_date).date()
            except Exception:
                start_date_obj = None
        if other_cycle.end_date:
            try:
                end_date_obj = datetime.fromisoformat(other_cycle.end_date).date()
            except Exception:
                end_date_obj = date.today()
        else:
            end_date_obj = date.today()
        duration_days = (end_date_obj - start_date_obj).days + 1 if start_date_obj else 0

        # Get daily entries for this other cycle (use different variable name)
        other_cycle_entries = Daily.query.filter_by(cycle_id=other_cycle.id).all()

        # Calculate cycle statistics
        total_mortality = sum(entry.mortality for entry in other_cycle_entries)
        total_feed_consumed = sum(entry.feed_bags_consumed for entry in other_cycle_entries)
        total_feed_added = sum(entry.feed_bags_added for entry in other_cycle_entries)
        final_birds = other_cycle.current_birds
        survival_rate = round((final_birds / other_cycle.start_birds) * 100, 2) if other_cycle.start_birds > 0 else 0
        avg_fcr = round(sum(entry.fcr for entry in other_cycle_entries if entry.fcr > 0) / max(1, len([entry for entry in other_cycle_entries if entry.fcr > 0])), 2) if other_cycle_entries else 0
        final_weight = other_cycle_entries[-1].avg_weight if other_cycle_entries and other_cycle_entries[-1].avg_weight > 0 else 0
        feed_per_bird = round(total_feed_consumed / other_cycle.start_birds, 2) if other_cycle.start_birds > 0 else 0
        mortality_rate = round((total_mortality / other_cycle.start_birds * 100), 2) if other_cycle.start_birds > 0 else 0

        cycle_data.append({
            'cycle': other_cycle,
            'duration_days': duration_days,
            'total_mortality': total_mortality,
            'total_feed_consumed': total_feed_consumed,
            'total_feed_added': total_feed_added,
            'final_birds': final_birds,
            'survival_rate': survival_rate,
            'avg_fcr': avg_fcr,
            'final_weight': final_weight,
            'feed_per_bird': feed_per_bird,
            'mortality_rate': mortality_rate
        })

    # Prepare chart data for FCR and weight trends
    fcr_series = [entry.fcr for entry in daily_entries if entry.fcr > 0]
    dates = [entry.entry_date for entry in daily_entries]
    weight_series = [entry.avg_weight for entry in daily_entries if entry.avg_weight > 0]

    # Get feeds for this specific cycle
    feeds = Feed.query.filter_by(cycle_id=cycle.id).order_by(Feed.date.desc()).all()

    # Calculate total feed cost and total bags consumed
    total_bags_consumed = sum(entry.feed_bags_consumed for entry in daily_entries)
    total_feed_cost = sum(feed.total_cost for feed in feeds) if feeds else 0

    # Feed to weight ratio
    feed_to_weight_ratio = None
    if cycle.current_birds > 0 and stats["avg_weight"] > 0:
        feed_to_weight_ratio = round((total_bags_consumed * 50) / (cycle.current_birds * stats["avg_weight"]), 3)

    # Get bird dispatch data for this cycle
    bird_dispatches = BirdDispatch.query.filter_by(cycle_id=cycle_id).order_by(BirdDispatch.dispatch_date.desc(), BirdDispatch.dispatch_time.desc()).all()
    
    # Calculate dispatch summary statistics
    total_birds_dispatched = sum(dispatch.total_birds for dispatch in bird_dispatches if dispatch.status == 'completed')
    total_weight_dispatched = sum(dispatch.total_weight for dispatch in bird_dispatches if dispatch.status == 'completed')
    avg_dispatch_weight_per_bird = round(total_weight_dispatched / total_birds_dispatched, 3) if total_birds_dispatched > 0 else 0
    completed_dispatches = len([d for d in bird_dispatches if d.status == 'completed'])
    active_dispatches = len([d for d in bird_dispatches if d.status == 'active'])
    
    dispatch_summary = {
        'total_birds_dispatched': total_birds_dispatched,
        'total_weight_dispatched': total_weight_dispatched,
        'avg_weight_per_bird': avg_dispatch_weight_per_bird,
        'completed_dispatches': completed_dispatches,
        'active_dispatches': active_dispatches,
        'total_dispatches': len(bird_dispatches)
    }

    # Calculate total medical expenses from Medicine model for this cycle
    medicines = Medicine.query.filter_by(cycle_id=cycle.id).all()
    total_medical_cost = sum(med.price for med in medicines) if medicines else 0

    # Get expenses data for this cycle
    expenses = Expense.query.filter_by(cycle_id=cycle.id).order_by(Expense.date.desc(), Expense.id.desc()).all()
    total_expense_cost = sum(exp.amount for exp in expenses)

    return render_template(
        'cycle_details.html',
        cycle=cycle,
        all_cycles=cycles,  # Add all_cycles for dropdown
        stats=stats,
        daily_entries=daily_entries,
        feeds=feeds,
        total_feed_cost=total_feed_cost,
        total_bags_consumed=total_bags_consumed,
        feed_to_weight_ratio=feed_to_weight_ratio,
        duration=duration,
        current_duration=current_duration,
        fcr_series=fcr_series,
        dates=dates,
        weight_series=weight_series,
        bird_dispatches=bird_dispatches,
        dispatch_summary=dispatch_summary,
        medicines=medicines,
        total_medical_cost=total_medical_cost,
        expenses=expenses,
        total_expense_cost=total_expense_cost
    )

@app.route('/tips/bedding')
def bedding_tips():
    return render_template('tips_bedding.html')

@app.route('/tips/herbal')
def herbal_treatment_tips():
    return render_template('tips_herbal.html')

@app.route('/tips/growth')
def growth_tips():
    return render_template('tips_growth.html')

@app.route('/tips/medical')
def tips_medical():
    return render_template('tips_medical.html')

@app.route('/tips/own_feed')
def tips_own_feed():
    return render_template('tip_own_feed.html')

@app.route('/unarchive_cycle/<int:cycle_id>', methods=['POST'])
@admin_required
def unarchive_cycle(cycle_id):
    """Unarchive a cycle and make it active (admin only)"""
    try:
        cycle = Cycle.query.get_or_404(cycle_id)
        
        # Check if there's already an active cycle
        active_cycle = get_active_cycle()
        if active_cycle:
            flash('Cannot unarchive: There is already an active cycle. Please archive the current cycle first. / सक्रिय चक्र पहले से मौजूद है। पहले वर्तमान चक्र को संग्रहीत करें। / ఇప్పటికే యాక్టివ్ సైకిల్ ఉంది. మొదట ప్రస్తుత సైకిల్‌ను ఆర్కైవ్ చేయండి।', 'error')
            return redirect(url_for('cycle_history'))
        
        # Unarchive the cycle
        cycle.status = 'active'
        cycle.end_date = None  # Clear end date since it's active again
        cycle.notes = f"Unarchived on {datetime.now().isoformat()} - {cycle.notes or ''}"
        
        db.session.commit()
        flash(f'✅ Cycle #{cycle_id} has been unarchived and is now active! / ✅ चक्र #{cycle_id} को अनआर्काइव किया गया और अब सक्रिय है! / ✅ సైకిల్ #{cycle_id} అన్‌ఆర్కైవ్ చేయబడింది మరియు ఇప్పుడు యాక్టివ్!', 'success')
        
    except Exception as e:
        flash(f'Error unarchiving cycle: {str(e)} / चक्र अनआर्काइव करने में त्रुटि: {str(e)} / సైకిల్ అన్‌ఆర్కైవ్ చేయడంలో లోపం: {str(e)}', 'error')
    
    return redirect(url_for('cycle_history'))
   
@app.route('/delete_cycle/<int:cycle_id>', methods=['POST'])
@admin_required
def delete_cycle(cycle_id):
    cycle = Cycle.query.get_or_404(cycle_id)
    Daily.query.filter_by(cycle_id=cycle_id).delete()
    # If you have other related tables, delete them here as needed
    db.session.delete(cycle)
    db.session.commit()
    flash('Cycle deleted successfully!', 'success')
    return redirect(url_for('setup'))

@app.route('/export_dispatch_excel')
@login_required
def export_dispatch_excel():
    """Export dispatch history to PDF file"""
    cycle = get_active_cycle()
    if not cycle:
        flash('No active cycle found!', 'error')
        return redirect(url_for('dispatch_history'))
    
    # Generate PDF report for dispatch history
    pdf_buffer = create_pdf_report(cycle, f"Dispatch History - Cycle {cycle.id}")
    
    # Generate filename with current date
    filename = f'dispatch_history_cycle_{cycle.id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pdf'
    
    return send_file(pdf_buffer,
                     download_name=filename,
                     as_attachment=True,
                     mimetype='application/pdf')

@app.route('/income_estimate', methods=['GET', 'POST'])
def income_estimate():
    cycle = get_active_cycle()
    all_cycles = Cycle.query.all()
    chick_cost = feed_cost = other_expenses = chick_price = 0.0
    feed_per_kg_price = 45  # Default feed price per kg (updated default)
    bag_weight = 50  # Default bag weight in kg
    market_price_per_bird = 0.0
    custom_fcr = None  # Custom FCR from user input
    
    if request.method == 'POST':
        chick_cost = float(request.form.get('chick_cost', 0))
        feed_cost = float(request.form.get('feed_cost', 0))  # Use form feed_cost
        feed_per_kg_price = feed_cost  # Use the feed_cost from form as price per kg
        other_expenses = float(request.form.get('other_expenses', 0))
        chick_price = chick_cost  # Use chick_cost from form as chick_price internally
        bag_weight = float(request.form.get('bag_weight', 50))
        market_price_per_bird = float(request.form.get('market_price_per_bird', 0))
        
        # Get custom FCR if provided
        custom_fcr_input = request.form.get('custom_fcr', '').strip()
        if custom_fcr_input:
            try:
                custom_fcr = float(custom_fcr_input)
            except ValueError:
                custom_fcr = None
    else:
        # For GET requests, set chick_price same as chick_cost
        chick_price = chick_cost
        feed_cost = feed_per_kg_price  # Set feed_cost for template
    # Helper to get stats
    def get_stats(cycle, use_custom_fcr=None, use_form_feed_price=None):
        if not cycle:
            return {'total_birds': 0, 'avg_weight': 0, 'total_feed': 0, 'fcr': 0}
        entries = Daily.query.filter_by(cycle_id=cycle.id).all()
        avg_weight = sum(e.avg_weight for e in entries if e.avg_weight) / max(len(entries),1)
        
        # Use form feed price if provided for total_feed calculation
        if use_form_feed_price and use_form_feed_price > 0:
            total_feed_bags = sum(e.feed_bags_consumed for e in entries if e.feed_bags_consumed)
            total_feed = total_feed_bags * bag_weight * use_form_feed_price
        else:
            total_feed = sum(e.feed_bags_consumed for e in entries if e.feed_bags_consumed) * bag_weight
        
        # Use custom FCR if provided, otherwise calculate from entries
        if use_custom_fcr is not None:
            fcr = use_custom_fcr
        else:
            fcr = sum(e.fcr for e in entries if e.fcr) / max(1, len(entries))
        
        return {
            'total_birds': cycle.current_birds,
            'avg_weight': avg_weight,
            'total_feed': total_feed,
            'fcr': fcr
        }
    selected_cycle_id = request.args.get('cycle_id', 'all')
    if selected_cycle_id == 'all':
        selected_cycle = None
    else:
        try:
            selected_cycle = Cycle.query.get(int(selected_cycle_id))
        except:
            selected_cycle = None
    # Use selected cycle for stats
    cycle_for_stats = selected_cycle if selected_cycle else cycle
    cycle_stats = get_stats(cycle_for_stats, custom_fcr, feed_per_kg_price)
    # Calculate direct feed cost from Feed table - NOT USED for calculations anymore
    direct_feed_cost = 0
    if cycle:
        feeds = Feed.query.filter_by(cycle_id=cycle.id).all()
        direct_feed_cost = sum(feed.total_cost for feed in feeds if hasattr(feed, 'total_cost') and feed.total_cost)
    
    # Calculate fallback feed cost using form values (this is what we'll use)
    fallback_feed_cost = 0
    if cycle and feed_per_kg_price > 0:
        daily_entries = Daily.query.filter_by(cycle_id=cycle.id).all()
        total_feed_bags = sum(entry.feed_bags_consumed for entry in daily_entries)
        fallback_feed_cost = total_feed_bags * bag_weight * feed_per_kg_price  # Uses form price
    
    # Always use calculated cost with form values instead of database cost
    # If custom FCR is provided, calculate feed cost using FCR formula instead of actual bags
    if custom_fcr and cycle_stats and cycle_stats.get('total_birds') and cycle_stats.get('avg_weight'):
        # Calculate using FCR formula: Total Live Weight × Custom FCR × Feed Cost per kg
        total_live_weight = cycle_stats['total_birds'] * cycle_stats['avg_weight']
        theoretical_feed_consumed_kg = total_live_weight * custom_fcr
        total_feed_cost = theoretical_feed_consumed_kg * feed_per_kg_price
    else:
        # Use actual feed bags consumed calculation
        total_feed_cost = fallback_feed_cost
    # Calculate costs based on selected cycle or current cycle
    cycle_for_costs = cycle_for_stats if cycle_for_stats else cycle
    
    # Calculate total medical expenses for selected cycle
    total_medical_cost = 0
    if cycle_for_costs:
        medicines = Medicine.query.filter_by(cycle_id=cycle_for_costs.id).all()
        total_medical_cost = sum(med.price for med in medicines if med.price)
    
    # Calculate total expenses for selected cycle
    total_expense_cost = 0
    if cycle_for_costs:
        expenses = Expense.query.filter_by(cycle_id=cycle_for_costs.id).all()
        total_expense_cost = sum(exp.amount for exp in expenses if exp.amount)
    
    # Calculate chick cost from input and cycle start birds
    chick_production_cost = 0
    if cycle_for_costs and chick_price > 0:
        chick_production_cost = (cycle_for_costs.start_birds or 0) * chick_price
    
    # Calculate feed cost for selected cycle (using form values instead of database)
    selected_cycle_feed_cost = 0
    if cycle_for_costs and feed_per_kg_price > 0:
        # If custom FCR is provided, use FCR formula, otherwise use actual feed bags
        if custom_fcr and cycle_for_costs == cycle_for_stats:  # Only use custom FCR for the selected cycle
            # Calculate using FCR formula
            if cycle_stats and cycle_stats.get('total_birds') and cycle_stats.get('avg_weight'):
                total_live_weight = cycle_stats['total_birds'] * cycle_stats['avg_weight']
                theoretical_feed_consumed_kg = total_live_weight * custom_fcr
                selected_cycle_feed_cost = theoretical_feed_consumed_kg * feed_per_kg_price
        else:
            # Use actual feed bags consumed calculation
            daily_entries = Daily.query.filter_by(cycle_id=cycle_for_costs.id).all()
            total_bags = sum(entry.feed_bags_consumed for entry in daily_entries)
            selected_cycle_feed_cost = total_bags * bag_weight * feed_per_kg_price  # Uses form feed price
    
    # Cumulative stats and income calculation for all cycles
    cumu_stats = {'total_birds': 0, 'avg_weight': 0, 'total_feed': 0, 'fcr': 0}
    total_entries = 0
    all_cycles_total_cost = 0
    all_cycles_total_income = 0
    previous_cycles_market_prices = []
    
    for c in all_cycles:
        s = get_stats(c, custom_fcr if c == cycle_for_stats else None, feed_per_kg_price)  # Use form feed price for all cycles
        cumu_stats['total_birds'] += s['total_birds']
        cumu_stats['avg_weight'] += s['avg_weight'] * (len(Daily.query.filter_by(cycle_id=c.id).all()))
        cumu_stats['total_feed'] += s['total_feed']
        cumu_stats['fcr'] += s['fcr'] * (len(Daily.query.filter_by(cycle_id=c.id).all()))
        total_entries += len(Daily.query.filter_by(cycle_id=c.id).all())
        
        # Calculate costs for this cycle
        cycle_medicines = Medicine.query.filter_by(cycle_id=c.id).all()
        cycle_medical_cost = sum(med.price for med in cycle_medicines if med.price)
        
        cycle_expenses = Expense.query.filter_by(cycle_id=c.id).all()
        cycle_expense_cost = sum(exp.amount for exp in cycle_expenses if exp.amount)
        
        cycle_chick_cost = (c.start_birds or 0) * chick_price if chick_price > 0 else 0
        
        # Calculate feed cost for this cycle (always using form feed price)
        cycle_feeds = Feed.query.filter_by(cycle_id=c.id).all()
        cycle_feed_direct = sum(feed.total_cost for feed in cycle_feeds if hasattr(feed, 'total_cost') and feed.total_cost)
        
        # Always use form values for calculation instead of database values
        cycle_daily_entries = Daily.query.filter_by(cycle_id=c.id).all()
        cycle_total_bags = sum(entry.feed_bags_consumed for entry in cycle_daily_entries)
        cycle_feed_cost = cycle_total_bags * bag_weight * feed_per_kg_price  # Always uses form price
        
        cycle_total_cost = cycle_feed_cost + cycle_chick_cost + cycle_medical_cost + cycle_expense_cost
        all_cycles_total_cost += cycle_total_cost
        
        # For income calculation: use current market price for active cycle, average for others
        if c.status == 'active' and market_price_per_bird > 0:
            # Market price is per kg, so multiply by average weight
            cycle_avg_weight = s['avg_weight'] if s['avg_weight'] > 0 else 0
            cycle_income = s['total_birds'] * market_price_per_bird * cycle_avg_weight
            previous_cycles_market_prices.append(market_price_per_bird)
        else:
            # Use average of previous market prices if available
            if previous_cycles_market_prices:
                avg_market_price = sum(previous_cycles_market_prices) / max(len(previous_cycles_market_prices), 1)
                cycle_avg_weight = s['avg_weight'] if s['avg_weight'] > 0 else 0
                cycle_income = s['total_birds'] * avg_market_price * cycle_avg_weight
            else:
                cycle_income = 0
        
        all_cycles_total_income += cycle_income
    
    if total_entries > 0:
        cumu_stats['avg_weight'] /= total_entries
        cumu_stats['fcr'] /= total_entries
    else:
        # Handle case when there are no entries
        cumu_stats['avg_weight'] = 0
        cumu_stats['fcr'] = 0
    
    # Calculate estimated income for selected cycle
    estimated_income = 0
    if cycle_for_stats and market_price_per_bird > 0:
        # Market price is per kg, so multiply by average weight
        avg_weight = cycle_stats.get('avg_weight', 0)
        estimated_income = (cycle_for_stats.current_birds or 0) * market_price_per_bird * avg_weight
    
    # Calculate estimated income for all cycles
    estimated_income_all_cycles = all_cycles_total_income
    
    # Calculate profit for selected cycle
    total_cycle_cost = selected_cycle_feed_cost + chick_production_cost + total_medical_cost + total_expense_cost + other_expenses
    estimated_profit = estimated_income - total_cycle_cost
    
    # Calculate profit for all cycles
    estimated_profit_all_cycles = all_cycles_total_income - (all_cycles_total_cost + other_expenses)
    
    # Get expenses list for selected cycle
    selected_cycle_expenses = []
    if cycle_for_costs:
        selected_cycle_expenses = Expense.query.filter_by(cycle_id=cycle_for_costs.id).order_by(Expense.date.desc()).all()
    
    # Get all expenses for all cycles
    all_expenses = []
    for c in all_cycles:
        cycle_expenses = Expense.query.filter_by(cycle_id=c.id).all()
        all_expenses.extend(cycle_expenses)
    return render_template('income_estimate.html',
        cycle_stats=cycle_stats,
        cumu_stats=cumu_stats,
        chick_cost=chick_cost,
        feed_cost=feed_cost,
        other_expenses=other_expenses,
        total_birds=cycle_for_stats.current_birds if cycle_for_stats else 0,
        total_feed_cost=selected_cycle_feed_cost,
        total_medical_cost=total_medical_cost,
        total_expense_cost=total_expense_cost,
        chick_production_cost=chick_production_cost,
        direct_feed_cost=direct_feed_cost,
        fallback_feed_cost=fallback_feed_cost,
        chick_price=chick_price,
        feed_per_kg_price=feed_per_kg_price,
        bag_weight=bag_weight,
        market_price_per_bird=market_price_per_bird,
        estimated_income=estimated_income,
        estimated_profit=estimated_profit,
        estimated_income_all_cycles=estimated_income_all_cycles,
        estimated_profit_all_cycles=estimated_profit_all_cycles,
        all_cycles_total_cost=all_cycles_total_cost,
        total_cycle_cost=total_cycle_cost,
        all_cycles=all_cycles,
        selected_cycle_id=selected_cycle_id,
        selected_cycle_expenses=selected_cycle_expenses,
        all_expenses=all_expenses,
        custom_fcr=custom_fcr  # Add custom FCR to template
    )

@app.route('/export_cycle_details/<int:cycle_id>')
@admin_required
def export_cycle_details(cycle_id):
    """Export complete cycle details as PDF"""
    cycle = Cycle.query.get_or_404(cycle_id)
    
    # Generate PDF report for cycle details
    pdf_buffer = create_pdf_report(cycle, f"Complete Cycle {cycle_id} Details Report")
    
    # Generate filename with current date
    filename = f"cycle_{cycle_id}_complete_details_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    
    return send_file(pdf_buffer,
                     download_name=filename,
                     as_attachment=True,
                     mimetype='application/pdf')

@app.route('/export_income_estimate')
@admin_required
def export_income_estimate():
    """Export income estimate as PDF file"""
    cycle = get_active_cycle()
    if not cycle:
        flash('No active cycle found', 'error')
        return redirect(url_for('setup'))
    
    # Generate PDF report for income estimate
    pdf_buffer = create_pdf_report(cycle, f"Income Estimate Report - Cycle {cycle.id}")
    
    # Generate filename with current date
    filename = f"income_estimate_cycle_{cycle.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    
    return send_file(pdf_buffer,
                     download_name=filename,
                     as_attachment=True,
                     mimetype='application/pdf')

@app.route('/export_all_cycles')
@admin_required
def export_all_cycles():
    """Export data for all cycles in the system"""
    all_cycles = Cycle.query.order_by(Cycle.id.desc()).all()
    
    if not all_cycles:
        flash('No cycles found to export', 'error')
        return redirect(url_for('cycle_history'))
    
    # Prepare cycles summary data
    cycles_data = []
    
    for cycle in all_cycles:
        # Get basic stats for each cycle
        daily_entries = Daily.query.filter_by(cycle_id=cycle.id).all()
        dispatches = BirdDispatch.query.filter_by(cycle_id=cycle.id).all()
        feeds = Feed.query.filter_by(cycle_id=cycle.id).all()
        medicines = Medicine.query.filter_by(cycle_id=cycle.id).all()
        expenses = Expense.query.filter_by(cycle_id=cycle.id).all()
        
        total_mortality = sum(entry.mortality for entry in daily_entries)
        total_feed_consumed = sum(entry.feed_bags_consumed for entry in daily_entries)
        total_feed_cost = sum(feed.total_cost for feed in feeds) if feeds else 0
        total_medicine_cost = sum(med.price * (med.qty or 1) for med in medicines) if medicines else 0
        total_expense_cost = sum(exp.amount for exp in expenses) if expenses else 0
        total_dispatched = sum(d.total_birds for d in dispatches if d.status == 'completed')
        
        survival_rate = round((cycle.current_birds / cycle.start_birds) * 100, 2) if cycle.start_birds > 0 else 0
        
        # Calculate estimated income (using default values)
        estimated_income = 0
        if daily_entries:
            latest_entry = daily_entries[-1] if daily_entries else None
            if latest_entry and latest_entry.avg_weight:
                estimated_income = cycle.current_birds * latest_entry.avg_weight * 180  # ₹180 per kg

        cycles_data.append({
            'cycle_id': cycle.id,
            'start_date': cycle.start_date or '',
            'end_date': cycle.end_date or '',
            'status': cycle.status,
            'start_birds': cycle.start_birds,
            'current_birds': cycle.current_birds,
            'total_mortality': total_mortality,
            'survival_rate_percent': survival_rate,
            'total_feed_consumed_bags': total_feed_consumed,
            'total_feed_cost': total_feed_cost,
            'total_medicine_cost': total_medicine_cost,
            'total_other_expenses': total_expense_cost,
            'total_costs': total_feed_cost + total_medicine_cost + total_expense_cost,
            'birds_dispatched': total_dispatched,
            'estimated_income': round(estimated_income, 2),
            'estimated_profit': round(estimated_income - (total_feed_cost + total_medicine_cost + total_expense_cost), 2),
            'driver': cycle.driver or '',
            'notes': cycle.notes or ''
        })
    
    # Use the first/most recent cycle for the PDF structure, but title indicates all cycles
    primary_cycle = all_cycles[0]
    
    # Generate PDF report for all cycles
    pdf_buffer = create_pdf_report(primary_cycle, "All Cycles Summary Report")
    
    # Generate filename with current date
    filename = f"all_cycles_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    
    return send_file(pdf_buffer,
                     download_name=filename,
                     as_attachment=True,
                     mimetype='application/pdf')

# ==================== COMPANY MANAGEMENT ROUTES (SUPER ADMIN ONLY) ====================

@app.route('/company_management')
@super_admin_required
def company_management():
    """Company management for super admin"""
    companies = Company.query.order_by(Company.name).all()
    return render_template('company_management.html', companies=companies)

@app.route('/create_company', methods=['POST'])
@super_admin_required
def create_company():
    """Create a new company"""
    name = request.form.get('name', '').strip()
    code = request.form.get('code', '').strip()
    address = request.form.get('address', '').strip()
    phone = request.form.get('phone', '').strip()
    email = request.form.get('email', '').strip()
    contact_person = request.form.get('contact_person', '').strip()
    notes = request.form.get('notes', '').strip()
    
    if not name or not code:
        flash('Company name and code are required!', 'error')
        return redirect(url_for('company_management'))
    
    # Check if code already exists
    existing = Company.query.filter_by(code=code).first()
    if existing:
        flash('Company code already exists!', 'error')
        return redirect(url_for('company_management'))
    
    user = get_current_user()
    company = Company(
        name=name,
        code=code,
        address=address,
        phone=phone,
        email=email,
        contact_person=contact_person,
        status='active',
        created_date=datetime.utcnow(),
        created_by=user.id,
        notes=notes
    )
    
    db.session.add(company)
    db.session.commit()
    
    flash(f'Company "{name}" created successfully!', 'success')
    return redirect(url_for('company_management'))

@app.route('/user_management')
@super_admin_required  
def user_management():
    """User management for super admin"""
    users = User.query.join(Company).order_by(Company.name, User.username).all()
    companies = Company.query.filter_by(status='active').order_by(Company.name).all()
    return render_template('user_management.html', users=users, companies=companies)

@app.route('/create_user', methods=['POST'])
@super_admin_required
def create_user():
    """Create a new user"""
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    role = request.form.get('role', '').strip()
    company_id = request.form.get('company_id')
    full_name = request.form.get('full_name', '').strip()
    email = request.form.get('email', '').strip()
    phone = request.form.get('phone', '').strip()
    
    if not username or not password or not role or not company_id:
        flash('Username, password, role, and company are required!', 'error')
        return redirect(url_for('user_management'))
    
    # Check if username already exists
    existing = User.query.filter_by(username=username).first()
    if existing:
        flash('Username already exists!', 'error')
        return redirect(url_for('user_management'))
    
    current_user = get_current_user()
    new_user = User(
        username=username,
        role=role,
        company_id=int(company_id),
        full_name=full_name,
        email=email,
        phone=phone,
        status='active',
        created_date=datetime.utcnow(),
        created_by=current_user.id
    )
    new_user.set_password(password)
    
    db.session.add(new_user)
    db.session.commit()
    
    flash(f'User "{username}" created successfully!', 'success')
    return redirect(url_for('user_management'))

@app.route('/update_user_status/<int:user_id>/<status>')
@super_admin_required
def update_user_status(user_id, status):
    """Update user status (active/inactive)"""
    user = User.query.get_or_404(user_id)
    
    if status not in ['active', 'inactive']:
        flash('Invalid status!', 'error')
        return redirect(url_for('user_management'))
    
    user.status = status
    current_user = get_current_user()
    user.modified_by = current_user.id
    user.modified_date = datetime.utcnow()
    
    db.session.commit()
    
    flash(f'User status updated to {status}!', 'success')
    return redirect(url_for('user_management'))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
