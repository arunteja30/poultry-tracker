from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify, flash, session
from flask_sqlalchemy import SQLAlchemy
import pandas as pd
import os
from datetime import datetime, date
from io import BytesIO
from functools import wraps
import hashlib

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///poultry.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'your-secret-key-change-this-in-production'  # Change this in production
db = SQLAlchemy(app)

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

class Daily(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cycle_id = db.Column(db.Integer)
    entry_date = db.Column(db.String(20))
    mortality = db.Column(db.Integer, default=0)
    feed_bags_consumed = db.Column(db.Float, default=0.0)
    feed_bags_added = db.Column(db.Float, default=0.0)
    avg_weight = db.Column(db.Float, default=0.0)      # kg
    avg_feed_per_bird_g = db.Column(db.Float, default=0.0)
    fcr = db.Column(db.Float, default=0.0)
    medicines = db.Column(db.String(250), default="")

class Medicine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120))
    price = db.Column(db.Float, default=0.0)
    qty = db.Column(db.Integer, default=0)

# ---------------- Safe DB creation ----------------
def init_database():
    """Initialize database tables and default users"""
    try:
        # Always ensure tables exist
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
    return Cycle.query.order_by(Cycle.id.desc()).first()

def calc_cumulative_stats(cycle_id):
    rows = Daily.query.filter_by(cycle_id=cycle_id).all()
    total_feed = sum(r.feed_bags_consumed for r in rows)
    avg_fcr = round(sum(r.fcr for r in rows if r.fcr>0)/max(1,len([r for r in rows if r.fcr>0])),3) if rows else 0
    avg_weight = round(sum(r.avg_weight for r in rows if r.avg_weight>0)/max(1,len([r for r in rows if r.avg_weight>0])),3) if rows else 0
    total_mort = sum(r.mortality for r in rows)
    return {"total_feed_bags": total_feed, "avg_fcr": avg_fcr, "avg_weight": avg_weight, "total_mortality": total_mort}

# ---------- Routes ----------
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
        feed_efficiency = round((total_consumed / cycle.current_birds), 2) if cycle.current_birds > 0 else 0
        
        # Calculate days running from cycle start date
        if cycle.start_date:
            try:
                cycle_start_date = datetime.fromisoformat(cycle.start_date).date()
                days_running = (date.today() - cycle_start_date).days + 1  # +1 to include start day
            except (ValueError, TypeError):
                # Fallback if date parsing fails
                days_running = 1
        else:
            days_running = 1
            
        avg_mortality_per_day = round((total_mort / max(days_running, 1)), 2)
        
        # Feed cost calculations (₹40 per kg, 50kg per bag = ₹2000 per bag)
        feed_cost_per_kg = 40
        feed_cost_per_bag = feed_cost_per_kg * 50  # ₹2000 per bag
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
            "bags_available": cycle.start_feed_bags,
            "feed_bags_consumed_total": total_consumed,
            "mortality_total": total_mort,
            "fcr_today": (today_row.fcr if today_row else None),
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
            # Mark current cycle as inactive or archive it
            existing_cycle.notes = f"Archived on {datetime.now().isoformat()} - {existing_cycle.notes}"
            
        start_birds = int(request.form.get('start_birds',0))
        start_feed_bags = float(request.form.get('start_feed_bags',0))
        start_date = request.form.get('start_date') or date.today().isoformat()
        start_time = request.form.get('start_time') or datetime.now().time().isoformat(timespec='minutes')
        driver = request.form.get('driver','')
        notes = request.form.get('notes','')
        
        c = Cycle(start_date=start_date, start_time=start_time, start_birds=start_birds, current_birds=start_birds, start_feed_bags=start_feed_bags, driver=driver, notes=notes)
        db.session.add(c)
        db.session.commit()
        
        if action == 'reset':
            flash('नया चक्र सफलतापूर्वक शुरू किया गया! / New cycle started successfully!', 'success')
        
        return redirect(url_for('dashboard'))
    
    return render_template('setup.html', existing_cycle=existing_cycle)

@app.route('/import_data', methods=['GET', 'POST'])
@admin_required
def import_data():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('कोई फ़ाइल नहीं चुनी गई / No file selected', 'error')
            return redirect(request.url)
        
        file = request.files['file']
        if file.filename == '':
            flash('कोई फ़ाइल नहीं चुनी गई / No file selected', 'error')
            return redirect(request.url)
        
        if file and file.filename.endswith(('.xlsx', '.xls', '.csv')):
            try:
                # Read the uploaded file
                if file.filename.endswith('.csv'):
                    df = pd.read_csv(file)
                else:
                    df = pd.read_excel(file)
                
                cycle = get_active_cycle()
                if not cycle:
                    flash('पहले एक चक्र सेटअप करें / Please setup a cycle first', 'error')
                    return redirect(url_for('setup'))
                
                # Import daily data
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
                                avg_feed_per_bird_g=auto_avg_feed_per_bird_g,  # Use auto-calculated value
                                fcr=float(row.get('fcr', 0)),
                                medicines=str(row.get('medicines', ''))
                            )
                            db.session.add(daily_entry)
                            imported_count += 1
                    except Exception as e:
                        continue
                
                db.session.commit()
                flash(f'{imported_count} entries imported successfully! / {imported_count} प्रविष्टियां सफलतापूर्वक आयात की गईं!', 'success')
                
            except Exception as e:
                flash(f'Import failed: {str(e)} / आयात असफल: {str(e)}', 'error')
        
        else:
            flash('केवल Excel (.xlsx, .xls) या CSV फ़ाइलें समर्थित हैं / Only Excel (.xlsx, .xls) or CSV files are supported', 'error')
        
        return redirect(url_for('import_data'))
    
    return render_template('import_data.html')

@app.route('/reset_cycle', methods=['POST'])
@admin_required
def reset_cycle():
    cycle = get_active_cycle()
    if cycle:
        # Archive current data
        cycle.notes = f"Archived on {datetime.now().isoformat()} - {cycle.notes}"
        db.session.commit()
        flash('Current cycle archived. You can now start a new cycle. / वर्तमान चक्र संग्रहीत किया गया। अब आप नया चक्र शुरू कर सकते हैं।', 'info')
    
    return redirect(url_for('setup'))

@app.route('/daily', methods=['GET','POST'])
@login_required
def daily():
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('setup'))
    if request.method=='POST':
        entry_date = request.form.get('entry_date') or date.today().isoformat()
        mortality = int(request.form.get('mortality',0))
        feed_bags_consumed = float(request.form.get('feed_bags_consumed',0))
        feed_bags_added = float(request.form.get('feed_bags_added',0))
        avg_weight_grams = float(request.form.get('avg_weight_grams',0) or 0)
        avg_weight = round(avg_weight_grams / 1000, 3) if avg_weight_grams > 0 else 0  # Convert grams to kg
        medicines = request.form.get('medicines','')
        
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
            fcr = round((feed_kg / (avg_weight * live_after)),3) if (avg_weight>0 and live_after>0) else 0
        except Exception:
            fcr = 0
        row = Daily(cycle_id=cycle.id, entry_date=entry_date, mortality=mortality, feed_bags_consumed=feed_bags_consumed, feed_bags_added=feed_bags_added, avg_weight=avg_weight, avg_feed_per_bird_g=avg_feed_per_bird_g, fcr=fcr, medicines=medicines)
        cycle.current_birds = cycle.current_birds - mortality
        cycle.start_feed_bags = cycle.start_feed_bags + feed_bags_added - feed_bags_consumed
        db.session.add(row)
        db.session.commit()
        return redirect(url_for('dashboard'))
    meds = Medicine.query.order_by(Medicine.name).all()
    return render_template('daily.html', cycle=cycle, meds=meds)

@app.route('/daywise')
@login_required
def daywise():
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('setup'))
    rows = Daily.query.filter_by(cycle_id=cycle.id).order_by(Daily.entry_date.desc()).all()
    return render_template('daywise.html', rows=rows, cycle=cycle)

@app.route('/stats')
@login_required
def stats():
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('setup'))
    stats = calc_cumulative_stats(cycle.id)
    return render_template('stats.html', cycle=cycle, stats=stats)

@app.route('/medicines', methods=['GET','POST'])
@login_required
def medicines():
    if request.method=='POST':
        name = request.form.get('name')
        price = float(request.form.get('price',0) or 0)
        qty = int(request.form.get('qty',0) or 0)
        m = Medicine(name=name, price=price, qty=qty)
        db.session.add(m)
        db.session.commit()
        return redirect(url_for('medicines'))
    meds = Medicine.query.order_by(Medicine.id.desc()).all()
    return render_template('medicines.html', meds=meds)

# ---------------- In-memory Excel export for Render ----------------
@app.route('/export')
@login_required
def export():
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('setup'))
    rows = Daily.query.filter_by(cycle_id=cycle.id).order_by(Daily.entry_date).all()
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
    output.seek(0)

    return send_file(output, 
                     download_name=f"poultry_export_{cycle.id}.xlsx",
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
