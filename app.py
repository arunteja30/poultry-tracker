
from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify, flash, session
from flask_sqlalchemy import SQLAlchemy
import pandas as pd
import os
from datetime import datetime, date
from io import BytesIO
from functools import wraps
import hashlib
from flask_migrate import Migrate

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///poultry.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'your-secret-key-change-this-in-production'  # Change this in production
db = SQLAlchemy(app)
migrate = Migrate(app, db)


# ---------------- Models ----------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), default='user')  # 'user' or 'admin'

    def set_password(self, password):
        self.password_hash = hashlib.sha256(password.encode()).hexdigest()

    def check_password(self, password):
        return self.password_hash == hashlib.sha256(password.encode()).hexdigest()

class Cycle(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    start_date = db.Column(db.String(50))
    start_time = db.Column(db.String(50))
    start_birds = db.Column(db.Integer)
    current_birds = db.Column(db.Integer)
    start_feed_bags = db.Column(db.Float)
    driver = db.Column(db.String(120))
    notes = db.Column(db.String(500))
    status = db.Column(db.String(20), default='active')  # 'active' or 'archived'
    end_date = db.Column(db.String(50))  # Date when cycle was completed/archived
    ext1 = db.Column(db.String(120), default="")   # Added extension column 1
    ext2 = db.Column(db.String(120), default="")   # Added extension column 2

class Daily(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cycle_id = db.Column(db.Integer)
    birds_survived = db.Column(db.Integer, default=0)  # New column
    entry_date = db.Column(db.String(20))
    mortality = db.Column(db.Integer, default=0)
    feed_bags_consumed = db.Column(db.Float, default=0)
    feed_bags_added = db.Column(db.Integer, default=0)
    avg_weight = db.Column(db.Float, default=0.0)      # kg
    avg_feed_per_bird_g = db.Column(db.Float, default=0.0)
    fcr = db.Column(db.Float, default=0.0)
    medicines = db.Column(db.String(250), default="")
    daily_notes = db.Column(db.String(500), default="")  # Added daily_notes column
    ext1 = db.Column(db.String(120), default="")   # Added extension column 1
    ext2 = db.Column(db.String(120), default="")   # Added extension column 2

class Medicine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120))
    price = db.Column(db.Float, default=0.0)
    qty = db.Column(db.Integer, default=0)
    ext1 = db.Column(db.String(120), default="")   # Added extension column 1
    ext2 = db.Column(db.String(120), default="")   # Added extension column 2


# ---------------- Safe DB creation ----------------

def init_database():
    """Initialize database tables and default users"""

    try:

        # Always ensure tables exist
#         db.drop_all()
        db.create_all()
        print("Database tables created successfully")

        # Check if default users exist, create them if they don't
        if not User.query.filter_by(username='admin').first():
            admin_user = User(username='admin', role='admin')
            admin_user.set_password('admin123')
            db.session.add(admin_user)
            print("Created admin user: admin/admin123")

        if not User.query.filter_by(username='user').first():
            regular_user = User(username='user', role='user')
            regular_user.set_password('user123')
            db.session.add(regular_user)
            print("Created regular user: user/user123")

        db.session.commit()
        print("Default users created successfully")
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
        if not user or user.role != 'admin':
            flash('Access denied. Admin privileges required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def get_current_user():
    if 'user_id' in session:
        return User.query.get(session['user_id'])
    return None

def get_active_cycle():
    return Cycle.query.filter_by(status='active').order_by(Cycle.id.desc()).first()

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

@app.route('/end_current_cycle', methods=['POST'])
@admin_required
def end_current_cycle():
    cycle = get_active_cycle()
    if cycle:
        cycle.status = 'archived'
        cycle.end_date = date.today().isoformat()
        cycle.notes = f"Ended on {datetime.now().isoformat()} - {cycle.notes or ''}"
        db.session.commit()
        flash('Current cycle ended and archived. You can now start a new cycle.', 'info')
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
            session['user_id'] = user.id
            session['username'] = user.username
            session['role'] = user.role
            flash(f'Welcome {user.username}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'error')

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('login'))

@app.route('/')
@login_required
def dashboard():
    cycle = get_active_cycle()
    summary = None
    fcr_series = []
    dates = []
    dashboard_metrics = {}
    if not cycle:
        return redirect(url_for('no_cycle'))

    if cycle:
        today = date.today().isoformat()
        today_row = Daily.query.filter_by(cycle_id=cycle.id, entry_date=today).first()
        rows = Daily.query.filter_by(cycle_id=cycle.id).order_by(Daily.entry_date).all()

        # Basic calculations
        total_consumed = sum(r.feed_bags_consumed for r in rows)
        total_mort = sum(r.mortality for r in rows)
        total_feed_added = sum(r.feed_bags_added for r in rows)

        # Chart data
        for r in rows:
            dates.append(r.entry_date)
            fcr_series.append(round(r.fcr,3) if r.fcr else None)

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

        summary = {
            "start_birds": cycle.start_birds,
            "current_birds": cycle.current_birds,
            "start_date": cycle.start_date,
            "days": days_running,
            "bags_available": round(cycle.start_feed_bags),
            "feed_bags_consumed_total": total_consumed,
            "mortality_total": total_mort,
            "fcr_today": calc_todays_fcr(cycle.id),
            "cumulative_fcr": stats["cumulative_fcr"],
            "avg_fcr": stats["avg_fcr"],
            "avg_weight": stats["avg_weight"]
        }
    return render_template('dashboard.html', cycle=cycle, summary=summary, fcr_series=fcr_series, dates=dates, metrics=dashboard_metrics)



@app.route('/setup', methods=['GET','POST'])
@admin_required
def setup():
    existing_cycle = get_active_cycle()
    if request.method=='POST':
        action = request.form.get('action', 'new')

        if action == 'reset' and existing_cycle:
            # Archive current cycle
            existing_cycle.status = 'archived'
            existing_cycle.end_date = date.today().isoformat()
            existing_cycle.notes = f"Archived on {datetime.now().isoformat()} - {existing_cycle.notes}"

        start_birds = int(request.form.get('start_birds',0))
        start_feed_bags = float(request.form.get('start_feed_bags',0))
        start_date = request.form.get('start_date') or date.today().isoformat()
        start_time = request.form.get('start_time') or datetime.now().time().isoformat(timespec='minutes')
        driver = request.form.get('driver','')
        notes = request.form.get('notes','')

        c = Cycle(start_date=start_date, start_time=start_time, start_birds=start_birds, current_birds=start_birds, start_feed_bags=start_feed_bags, driver=driver, notes=notes, status='active', ext1="", ext2="")
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
                    medicines=str(row.get('medicines', '')),
                    birds_survived=live_after,
                    daily_notes=str(row.get('daily_notes', '')),
                    ext1="",
                    ext2=""
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
                notes = str(row.get('notes', '')).strip()
                quantity = int(row.get('quantity', 0)) if pd.notna(row.get('quantity', 0)) else 0

                medicine = Medicine(
                    name=medicine_name,
                    price=price,
                    qty=quantity,
                    notes=notes,
                    ext1="",
                    ext2=""
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
        db.session.commit()
        flash('Current cycle archived. You can now start a new cycle. / वर्तमान चक्र संग्रहीत किया गया। अब आप नया चक्र शुरू कर सकते हैं।', 'info')

    return redirect(url_for('setup'))

def get_latest_daily(cycle_id):
    """Get the latest Daily entry for a cycle by date."""
    return Daily.query.filter_by(cycle_id=cycle_id).order_by(Daily.entry_date.desc()).first()

@app.route('/daily', methods=['GET','POST'])
@login_required
def daily():
    cycle = get_active_cycle()
    latest_daily = get_latest_daily(cycle.id) if cycle else None
    if not cycle:
        return redirect(url_for('no_cycle'))
    if request.method=='POST':
        entry_date = request.form.get('entry_date') or date.today().isoformat()
        mortality = int(request.form.get('mortality',0))
        feed_bags_consumed = float(request.form.get('feed_bags_consumed',0))
        feed_bags_added = float(request.form.get('feed_bags_added',0))
        avg_weight_grams = float(request.form.get('avg_weight_grams',0) or 0)
        daily_notes = request.form.get('daily_notes') or ''
        ext1 = request.form.get('ext1') or ''
        ext2 = request.form.get('ext2') or ''

        if latest_daily != None:
           avg_weight = round(((avg_weight_grams / 1000)+latest_daily.avg_weight if latest_daily.avg_weight else 0)/2, 3) if avg_weight_grams > 0 else 0  # Convert grams to kg
        else:
           avg_weight = round(avg_weight_grams / 1000, 3) if avg_weight_grams > 0 else 0

        medicines = request.form.get('medicines','')

        # Validate form data
        if not entry_date or not avg_weight_grams:
            error_message = '⚠️ Please fill in all required fields. / దయచేసి అన్ని అవసరమైన ఫీల్డ్‌లను పూరించండి।'
            meds = Medicine.query.order_by(Medicine.name).all()
            return render_template('daily.html', cycle=cycle, meds=meds,
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
        bags_after_consumption = cycle.start_feed_bags + feed_bags_added - feed_bags_consumed
        if bags_after_consumption < 0:
            shortage = abs(bags_after_consumption)
            flash('⚠️ Insufficient feed bags! You need {round(shortage)} more bags. Current available: {round(cycle.start_feed_bags)}, trying to consume: {round(feed_bags_consumed)}. Please add new bags first. / ⚠️ अपर्याप्त फ़ीड बैग! आपको {round(shortage)} और बैग चाहिए। वर्तमान उपलब्ध: {round(cycle.start_feed_bags)}, उपयोग करने की कोशिश: {round(feed_bags_consumed)}। कृपया पहले नए बैग जोड़ें। / ⚠️ తగినంత ఫీడ్ బ్యాగులు లేవు! మీకు {round(shortage)} మరిన్ని బ్యాగులు కావాలి। అందుబాటులో: {round(cycle.start_feed_bags)}, వాడటానికి ప్రయత్నిస్తున్నారు: {round(feed_bags_consumed)}. దయచేసి మరిన్ని బ్యాగులు జోడించండి।', 'error')
            meds = Medicine.query.order_by(Medicine.name).all()
            return render_template('daily.html', cycle=cycle, meds=meds,
                                   error_data={
                                       'entry_date': entry_date,
                                       'mortality': mortality,
                                       'feed_bags_consumed': feed_bags_consumed,
                                       'feed_bags_added': feed_bags_added,
                                       'avg_weight_grams': avg_weight_grams,
                                       'medicines': medicines
                                   })
        elif bags_after_consumption < 0:
            flash('⚠️ Error: Must maintain at least 1 feed bag in inventory! Current available: {round(cycle.start_feed_bags)}, trying to consume: {round(feed_bags_consumed)}, bags added: {round(feed_bags_added)}. This would leave only {round(bags_after_consumption)} bags. / ⚠️ त्रुटि: इन्वेंटरी में कम से कम 1 फ़ीड बैग बनाए रखना चाहिए! / ⚠️ లోపం: ఇన్వెంటరీలో కనీసం 1 ఫీడ్ బ్యాగ్ ఉంచాలి! ప్రస్తుతం అందుబాటులో: {round(cycle.start_feed_bags)}, వాడటానికి ప్రయత్నిస్తున్నారు: {round(feed_bags_consumed)}, జోడించిన బ్యాగులు: {round(feed_bags_added)}. దీని వలన కేవలం {round(bags_after_consumption)} బ్యాగులు మిగిలిపోతాయి।', 'error')
            meds = Medicine.query.order_by(Medicine.name).all()
            return render_template('daily.html', cycle=cycle, meds=meds,
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

        # Auto-calculate average feed per bird based on cumulative consumption and current live birds
        live_after = cycle.current_birds - mortality
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
        row = Daily(
            cycle_id=cycle.id,
            entry_date=entry_date,
            mortality=mortality,
            feed_bags_consumed=feed_bags_consumed,
            feed_bags_added=feed_bags_added,
            avg_weight=avg_weight,
            avg_feed_per_bird_g=avg_feed_per_bird_g,
            fcr=fcr,
            daily_notes=daily_notes,
            medicines=medicines,
            ext1=ext1,
            ext2=ext2,
            birds_survived=live_after
        )
        cycle.current_birds = live_after
        cycle.start_feed_bags = cycle.start_feed_bags + feed_bags_added - feed_bags_consumed
        db.session.add(row)
        db.session.commit()

        # Success message
        if feed_bags_added > 0:
            flash(f'✅ Daily entry saved successfully! Added {feed_bags_added:.1f} bags, consumed {feed_bags_consumed:.1f} bags. Remaining: {cycle.start_feed_bags:.1f} bags. / ✅ दैनिक प्रविष्टि सफलतापूर्वक सहेजी गई! {feed_bags_added:.1f} बैग जोड़े गए, {feed_bags_consumed:.1f} बैग उपयोग किए गए। बचे हुए: {cycle.start_feed_bags:.1f} बैग। / ✅ రోజువారీ ఎంట్రీ విజయవంతంగా సేవ్ చేయబడింది! {feed_bags_added:.1f} బ్యాగులు జోడించబడ్డాయి, {feed_bags_consumed:.1f} బ్యాగులు వాడబడ్డాయి। మిగిలినవి: {cycle.start_feed_bags:.1f} బ్యాగులు।', 'success')
        else:
            flash(f'✅ Daily entry saved successfully! Consumed {feed_bags_consumed:.1f} bags. Remaining: {cycle.start_feed_bags:.1f} bags. / ✅ दैनिक प्रविष्टि सफलतापूर्वक सहेजी गई! {feed_bags_consumed:.1f} बैग उपयोग किए गए। बचे हुए: {cycle.start_feed_bags:.1f} बैग। / ✅ రోజువారీ ఎంట్రీ విజయవంతంగా సేవ్ చేయబడింది! {feed_bags_consumed:.1f} బ్యాగులు వాడబడ్డాయి। మిగిలినవి: {cycle.start_feed_bags:.1f} బ్యాగులు।', 'success')

        return redirect(url_for('load_daywise'))
    meds = Medicine.query.order_by(Medicine.name).all()
    return render_template('daily.html', cycle=cycle, meds=meds)

@app.route('/daywise')
@login_required
def daywise():
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('no_cycle'))
    rows = Daily.query.filter_by(cycle_id=cycle.id).order_by(Daily.entry_date.asc()).all()
    return render_template('daywise.html', rows=rows, cycle=cycle)

@app.route('/stats')
@login_required
def stats():
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('no_cycle'))

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

    # Medicine costs
    medicines = Medicine.query.all()
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
    if request.method=='POST':
        name = request.form.get('name')
        price = float(request.form.get('price',0) or 0)
        qty = int(request.form.get('qty',0) or 0)
        notes = str(request.form.get('ext1', '')).strip()
        m = Medicine(name=name, price=price, qty=qty, ext1=notes)
        db.session.add(m)
        db.session.commit()
        return redirect(url_for('medicines'))
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('no_cycle'))
    meds = Medicine.query.order_by(Medicine.id.desc()).all()
    total_amount = sum(med.price for med in meds)
    return render_template('medicines.html', meds=meds, total_amount=total_amount)

# ---------------- In-memory Excel export for Render ----------------
@app.route('/export')
@admin_required
def export():
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('no_cycle'))

    # Get daily data
    daily_rows = Daily.query.filter_by(cycle_id=cycle.id).order_by(Daily.entry_date).all()
    daily_data = [{
        'date': r.entry_date,
        'mortality': r.mortality,
        'feed_bags_consumed': r.feed_bags_consumed,
        'feed_bags_added': r.feed_bags_added,
        'avg_weight': r.avg_weight,
        'avg_feed_per_bird_g': r.avg_feed_per_bird_g,
        'fcr': r.fcr,
        'medicines': r.medicines
    } for r in daily_rows]

    # Get medicines data
    medicines = Medicine.query.order_by(Medicine.id.desc()).all()
    medicines_data = [{
        'medicine_name': med.name,
        'price': med.price,
        'quantity': med.qty if med.qty is not None else 0,
        'total_value': med.price * (med.qty if med.qty is not None else 1)
    } for med in medicines]

    # Add medicines total
    total_medicine_cost = sum(med.price for med in medicines if med.price)
    total_medicine_value = sum(med.price * (med.qty if med.qty is not None else 1) for med in medicines if med.price)
    medicines_data.append({
        'medicine_name': 'TOTAL',
        'price': total_medicine_cost,
        'quantity': '',
        'total_value': total_medicine_value
    })

    # Get cycle summary data
    cycle_summary = [{
        'metric': 'Cycle ID',
        'value': cycle.id
    }, {
        'metric': 'Start Date',
        'value': cycle.start_date or ''
    }, {
        'metric': 'Initial Birds',
        'value': cycle.start_birds or 0
    }, {
        'metric': 'Initial Feed Bags',
        'value': cycle.start_feed_bags or 0
    }, {
        'metric': 'Driver',
        'value': cycle.driver or ''
    }, {
        'metric': 'Notes',
        'value': cycle.notes or ''
    }]

    # Calculate current stats
    current_birds = cycle.start_birds - sum(r.mortality for r in daily_rows)
    total_feed_consumed = sum(r.feed_bags_consumed for r in daily_rows)
    total_feed_added = sum(r.feed_bags_added for r in daily_rows)
    current_feed_bags = cycle.start_feed_bags + total_feed_added - total_feed_consumed

    cycle_summary.extend([{
        'metric': 'Current Live Birds',
        'value': current_birds
    }, {
        'metric': 'Total Mortality',
        'value': sum(r.mortality for r in daily_rows)
    }, {
        'metric': 'Total Feed Consumed (bags)',
        'value': total_feed_consumed
    }, {
        'metric': 'Current Feed Bags',
        'value': current_feed_bags
    }, {
        'metric': 'Survival Rate (%)',
        'value': round((current_birds / cycle.start_birds) * 100, 2) if cycle.start_birds > 0 else 0
    }, {
        'metric': 'Total Medicine Cost (₹)',
        'value': total_medicine_cost
    }])

    # Create DataFrames
    daily_df = pd.DataFrame(daily_data)
    medicines_df = pd.DataFrame(medicines_data)
    summary_df = pd.DataFrame(cycle_summary)

    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        # Write each sheet
        summary_df.to_excel(writer, index=False, sheet_name='Cycle Summary')
        daily_df.to_excel(writer, index=False, sheet_name='Daily Data')
        medicines_df.to_excel(writer, index=False, sheet_name='Medicines')

        # Get workbook and worksheets for formatting
        workbook = writer.book

        # Format medicines sheet - highlight total row
        medicines_sheet = writer.sheets['Medicines']
        total_format = workbook.add_format({
            'bold': True,
            'bg_color': '#D3D3D3',
            'border': 1
        })

        # Apply formatting to the last row (total row) in medicines
        if len(medicines_data) > 0:
            last_row = len(medicines_data)
            medicines_sheet.set_row(last_row, None, total_format)

    output.seek(0)

    return send_file(output,
                     download_name=f"complete_farm_data_{cycle.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                     as_attachment=True,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/export_cycle/<int:cycle_id>')
@admin_required
def export_cycle(cycle_id):
    """Export data for a specific cycle"""
    cycle = Cycle.query.get_or_404(cycle_id)
    # Only allow export if cycle is active or archived, no redirect needed
    rows = Daily.query.filter_by(cycle_id=cycle_id).order_by(Daily.entry_date).all()

    data = [{
        'date': r.entry_date,
        'mortality': r.mortality,
        'feed_bags_consumed': r.feed_bags_consumed,
        'feed_bags_added': r.feed_bags_added,
        'avg_weight': r.avg_weight,
        'avg_feed_per_bird_g': r.avg_feed_per_bird_g,
        'fcr': r.fcr,
        'medicines': r.medicines
    } for r in rows]

    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Daily Data')

        # Add cycle summary sheet
        cycle_summary = pd.DataFrame([{
            'Cycle ID': cycle.id,
            'Start Date': cycle.start_date,
            'End Date': cycle.end_date or 'Ongoing',
            'Start Birds': cycle.start_birds,
            'Final Birds': cycle.current_birds,
            'Initial Feed Bags': cycle.start_feed_bags,
            'Driver': cycle.driver or '',
            'Status': cycle.status,
            'Notes': cycle.notes or ''
        }])
        cycle_summary.to_excel(writer, index=False, sheet_name='Cycle Summary')

    output.seek(0)
    return send_file(output,
                     download_name=f"cycle_{cycle_id}_export.xlsx",
                     as_attachment=True,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

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
        return redirect(url_for('no_cycle'))

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
    try:
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

    except Exception as e:
        # Log the full error for debugging
        print(f"Error in users route: {str(e)}")
        import traceback
        traceback.print_exc()
        flash(f'System error: {str(e)}', 'error')
        return redirect(url_for('dashboard'))

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
        start_date_obj = None
        if cycle.start_date:
            if isinstance(cycle.start_date, str):
                start_date_obj = datetime.strptime(cycle.start_date, '%Y-%m-%d').date()
            else:
                start_date_obj = cycle.start_date

        if cycle.end_date:
            if isinstance(cycle.end_date, str):
                end_date_obj = datetime.strptime(cycle.end_date, '%Y-%m-%d').date()
            else:
                end_date_obj = cycle.end_date
        else:
            end_date_obj = date.today()

        duration_days = (end_date_obj - start_date_obj).days + 1 if start_date_obj else 0

        # Get daily entries for this cycle
        daily_entries = Daily.query.filter_by(cycle_id=cycle.id).all()

        # Calculate cycle statistics
        total_mortality = sum(entry.mortality for entry in daily_entries)
        total_feed_consumed = sum(entry.feed_bags_consumed for entry in daily_entries)
        total_feed_added = sum(entry.feed_bags_added for entry in daily_entries)
        final_birds = cycle.current_birds
        survival_rate = round((final_birds / cycle.start_birds * 100), 2) if cycle.start_birds > 0 else 0

        # Calculate average FCR
        valid_fcr_entries = [entry.fcr for entry in daily_entries if entry.fcr and entry.fcr > 0]
        avg_fcr = round(sum(valid_fcr_entries) / len(valid_fcr_entries), 3) if valid_fcr_entries else 0

        # Calculate final weight
        weight_entries = [entry.avg_weight for entry in daily_entries if entry.avg_weight > 0]
        final_weight = max(weight_entries) if weight_entries else 0

        # Feed efficiency (total feed per bird)
        feed_per_bird = round(total_feed_consumed / final_birds, 2) if final_birds > 0 else 0

        cycle_info = {
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
            'total_entries': len(daily_entries)
        }
        cycle_data.append(cycle_info)

    # cycle_history shows all cycles, no redirect needed
    return render_template('cycle_history.html', cycle_data=cycle_data)

@app.route('/cycle_details/<int:cycle_id>')
@login_required
def cycle_details(cycle_id):
    """View detailed information for a specific cycle"""
    cycle = Cycle.query.get_or_404(cycle_id)

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

    # Get all daily entries for this cycle
    daily_entries = Daily.query.filter_by(cycle_id=cycle_id).order_by(Daily.entry_date).all()

    # Calculate detailed statistics
    stats = calc_cumulative_stats(cycle_id)

    # Prepare chart data
    dates = []
    fcr_series = []
    weight_series = []
    mortality_series = []

    for entry in daily_entries:
        dates.append(entry.entry_date)
        fcr_series.append(round(entry.fcr, 3) if entry.fcr else None)
        weight_series.append(entry.avg_weight if entry.avg_weight else None)
        mortality_series.append(entry.mortality)

    return render_template('cycle_details.html',
                         cycle=cycle,
                         daily_entries=daily_entries,
                         stats=stats,
                         dates=dates,
                         fcr_series=fcr_series,
                         weight_series=weight_series,
                         mortality_series=mortality_series,
                         duration=duration,
                         current_duration=current_duration)

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
    # Delete related medicines if you have a cycle_id field in Medicine (add if needed)
    if hasattr(Medicine, 'cycle_id'):
        Medicine.query.filter_by(cycle_id=cycle_id).delete()
    # If you have other related tables with cycle_id, delete them here as needed
    db.session.delete(cycle)
    db.session.commit()
    flash('Cycle deleted successfully!', 'success')
    return redirect(url_for('setup'))

@app.route('/no_cycle')
def no_cycle():
    return render_template('no_cycle.html')

@app.route('/daywise')
def load_daywise():
    return render_template('daywise.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)