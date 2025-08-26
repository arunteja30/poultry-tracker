from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
import pandas as pd
import os
from datetime import datetime, date
from io import BytesIO

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///poultry.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ---------------- Models ----------------
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
with app.app_context():
    if not os.path.exists('poultry.db'):
        db.create_all()

# ---------- Helpers ----------
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
@app.route('/')
def dashboard():
    cycle = get_active_cycle()
    summary = None
    fcr_series = []
    dates = []
    if cycle:
        today = date.today().isoformat()
        today_row = Daily.query.filter_by(cycle_id=cycle.id, entry_date=today).first()
        rows = Daily.query.filter_by(cycle_id=cycle.id).order_by(Daily.entry_date).all()
        total_consumed = sum(r.feed_bags_consumed for r in rows)
        total_mort = sum(r.mortality for r in rows)
        for r in rows:
            dates.append(r.entry_date)
            fcr_series.append(round(r.fcr,3) if r.fcr else None)
        summary = {
            "start_birds": cycle.start_birds,
            "current_birds": cycle.current_birds,
            "start_date": cycle.start_date,
            "days": (date.today() - datetime.fromisoformat(cycle.start_date).date()).days if cycle.start_date else 0,
            "bags_available": cycle.start_feed_bags,
            "feed_bags_consumed_total": total_consumed,
            "mortality_total": total_mort,
            "fcr_today": (today_row.fcr if today_row else None)
        }
    return render_template('dashboard.html', cycle=cycle, summary=summary, fcr_series=fcr_series, dates=dates)

@app.route('/setup', methods=['GET','POST'])
def setup():
    if request.method=='POST':
        start_birds = int(request.form.get('start_birds',0))
        start_feed_bags = float(request.form.get('start_feed_bags',0))
        start_date = request.form.get('start_date') or date.today().isoformat()
        start_time = request.form.get('start_time') or datetime.now().time().isoformat(timespec='minutes')
        driver = request.form.get('driver','')
        notes = request.form.get('notes','')
        c = Cycle(start_date=start_date, start_time=start_time, start_birds=start_birds, current_birds=start_birds, start_feed_bags=start_feed_bags, driver=driver, notes=notes)
        db.session.add(c)
        db.session.commit()
        return redirect(url_for('dashboard'))
    return render_template('setup.html')

@app.route('/daily', methods=['GET','POST'])
def daily():
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('setup'))
    if request.method=='POST':
        entry_date = request.form.get('entry_date') or date.today().isoformat()
        mortality = int(request.form.get('mortality',0))
        feed_bags_consumed = float(request.form.get('feed_bags_consumed',0))
        feed_bags_added = float(request.form.get('feed_bags_added',0))
        avg_weight = float(request.form.get('avg_weight',0) or 0)
        avg_feed_per_bird_g = float(request.form.get('avg_feed_per_bird_g',0) or 0)
        medicines = request.form.get('medicines','')
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
def daywise():
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('setup'))
    rows = Daily.query.filter_by(cycle_id=cycle.id).order_by(Daily.entry_date.desc()).all()
    return render_template('daywise.html', rows=rows, cycle=cycle)

@app.route('/stats')
def stats():
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('setup'))
    stats = calc_cumulative_stats(cycle.id)
    return render_template('stats.html', cycle=cycle, stats=stats)

@app.route('/medicines', methods=['GET','POST'])
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
                     attachment_filename=f"poultry_export_{cycle.id}.xlsx",
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
