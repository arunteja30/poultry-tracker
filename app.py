from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
import os

# -----------------------
# Initialize Flask app
# -----------------------
app = Flask(__name__)

# Config: SQLite database in project folder
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'poultry.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize database
db = SQLAlchemy(app)

# -----------------------
# Models
# -----------------------
class Bird(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    weight = db.Column(db.Float)
    alive = db.Column(db.Boolean, default=True)

class Medicine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    quantity = db.Column(db.Integer, default=0)
    price = db.Column(db.Float, default=0.0)

# -----------------------
# Ensure DB tables are created (safe for Render)
# -----------------------
with app.app_context():
    db.create_all()

# -----------------------
# Routes
# -----------------------
@app.route('/')
def home():
    birds = Bird.query.all()
    total_birds = len(birds)
    return render_template('index.html', total_birds=total_birds, birds=birds)

@app.route('/add_bird', methods=['POST'])
def add_bird():
    weight = request.form.get('weight', type=float)
    if weight:
        new_bird = Bird(weight=weight)
        db.session.add(new_bird)
        db.session.commit()
    return redirect(url_for('home'))

@app.route('/medicines')
def medicines():
    all_meds = Medicine.query.all()
    return render_template('medicines.html', medicines=all_meds)

@app.route('/add_medicine', methods=['POST'])
def add_medicine():
    name = request.form.get('name')
    quantity = request.form.get('quantity', type=int)
    price = request.form.get('price', type=float)
    if name:
        med = Medicine(name=name, quantity=quantity, price=price)
        db.session.add(med)
        db.session.commit()
    return redirect(url_for('medicines'))

# -----------------------
# Run app
# -----------------------
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)), debug=True)
