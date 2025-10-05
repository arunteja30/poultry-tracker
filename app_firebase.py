from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify, flash, session, make_response
from datetime import datetime, date
from io import BytesIO
from functools import wraps
import os
import json
import traceback
import pandas as pd
import secrets
import string
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch

# Import Firebase models
from firebase_config_simple import (
    company_model, user_model, cycle_model, daily_model, 
    medicine_model, feed_model, dispatch_model, weighing_model, expense_model
)

app = Flask(__name__, instance_relative_config=True)

# Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-change-this-in-production')

# Custom Jinja2 filters for safe date formatting
@app.template_filter('safe_strftime')
def safe_strftime(date_str, format='%Y-%m-%d %H:%M'):
    """Safely format date strings from Firebase"""
    if not date_str:
        return ''
    
    try:
        # If it's already a datetime object
        if hasattr(date_str, 'strftime'):
            return date_str.strftime(format)
        
        # If it's a string, try to parse it
        if isinstance(date_str, str):
            # Handle ISO format dates from Firebase
            if 'T' in date_str:
                # Remove timezone info if present and parse
                clean_date_str = date_str.split('+')[0].split('Z')[0]
                if '.' in clean_date_str:
                    # Handle microseconds
                    clean_date_str = clean_date_str.split('.')[0]
                dt = datetime.fromisoformat(clean_date_str)
                return dt.strftime(format)
            else:
                # Try to parse other formats
                dt = datetime.fromisoformat(date_str)
                return dt.strftime(format)
        
        return str(date_str)  # Fallback to string representation
    except (ValueError, AttributeError, TypeError):
        return str(date_str) if date_str else ''


# ---------- Helper Functions ----------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'error')
            return redirect(url_for('login'))
        
        user = get_current_user()
        if not user or user.get('role') not in ['admin', 'super_admin']:
            flash('Admin access required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def super_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'error')
            return redirect(url_for('login'))
        
        user = get_current_user()
        if not user or user.get('role') != 'super_admin':
            flash('Super admin access required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def get_current_user():
    if 'user_id' in session:
        user_data = user_model.get_record("users", session['user_id'])
        if user_data:
            return {'id': session['user_id'], **user_data}
    return None

def get_active_cycle(user=None):
    """Get active cycle for the current user's company"""
    if not user:
        user = get_current_user()
    
    if not user:
        return None
    
    # Super admin can see cycles based on selected company
    if user.get('role') == 'super_admin':
        selected_company_id = session.get('selected_company_id')
        if selected_company_id:
            return cycle_model.get_active_cycle_by_company(selected_company_id)
        else:
            return None
    
    if not user.get('company_id'):
        return None
        
    return cycle_model.get_active_cycle_by_company(user['company_id'])

def get_user_company_id():
    """Get the company ID for the current user"""
    user = get_current_user()
    if not user:
        return None
    
    # Super admin should have a way to select company
    if user.get('role') == 'super_admin':
        return session.get('selected_company_id')
    
    return user.get('company_id')

@app.context_processor
def inject_template_vars():
    """Make company data and other variables available to all templates"""
    def get_all_companies():
        # Only super admins can see all companies
        user = get_current_user()
        if user and user.get('role') == 'super_admin':
            companies = company_model.get_all_companies()
            return companies
        return []
    
    def get_current_company():
        company_id = get_user_company_id()
        if company_id:
            company_data = company_model.get_record("companies", company_id)
            if company_data:
                return {'id': company_id, **company_data}
        return None
    
    def get_user_by_id(user_id):
        if user_id:
            user_data = user_model.get_record("users", user_id)
            if user_data:
                return {'id': user_id, **user_data}
        return None
    
    def get_company_by_id(company_id):
        if company_id:
            company_data = company_model.get_record("companies", company_id)
            if company_data:
                return {'id': company_id, **company_data}
        return None
    
    return dict(
        get_all_companies=get_all_companies,
        get_current_company=get_current_company,
        get_user_by_id=get_user_by_id,
        get_company_by_id=get_company_by_id,
        current_user=get_current_user(),
        session=session  # Make session available to templates
    )

def calc_cumulative_stats(cycle_id):
    """Calculate cumulative statistics for a cycle"""
    daily_entries = daily_model.get_entries_by_cycle(cycle_id)
    cycle_data = cycle_model.get_record("cycles", cycle_id)
    
    total_feed = sum(entry.get('feed_bags_consumed', 0) for entry in daily_entries)
    fcr_entries = [entry.get('fcr', 0) for entry in daily_entries if entry.get('fcr', 0) > 0]
    avg_fcr = round(sum(fcr_entries) / max(1, len(fcr_entries)), 3) if fcr_entries else 0
    
    # Use latest entry's weight as the current average weight
    if daily_entries and cycle_data:
        latest_entry = max(daily_entries, key=lambda r: r.get('entry_date', ''))
        avg_weight = latest_entry.get('avg_weight', 0) if latest_entry.get('avg_weight', 0) > 0 else 0
    else:
        avg_weight = 0
    
    total_mortality = sum(entry.get('mortality', 0) for entry in daily_entries)
    
    # Calculate cumulative FCR
    cumulative_fcr = 0
    if cycle_data and cycle_data.get('current_birds', 0) > 0 and avg_weight > 0:
        total_feed_kg = total_feed * 50  # Convert bags to kg
        starting_weight_kg = 0.045  # 45g for day-old chicks
        total_weight_gained_kg = cycle_data['current_birds'] * (avg_weight - starting_weight_kg)
        if total_weight_gained_kg > 0:
            cumulative_fcr = round(total_feed_kg / total_weight_gained_kg, 3)

    return {
        "total_feed_bags": total_feed,
        "avg_fcr": avg_fcr,
        "cumulative_fcr": cumulative_fcr,
        "avg_weight": avg_weight,
        "total_mortality": total_mortality
    }

def convert_date_to_ddmmyyyy(date_str):
    """Convert YYYY-MM-DD to DD-MM-YYYY format"""
    if not date_str:
        return None
    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        return date_obj.strftime('%d-%m-%Y')
    except ValueError:
        return date_str

def create_comprehensive_pdf_report(cycle, title="Farm Report"):
    """Create a comprehensive PDF report that mirrors the original app.py layout"""
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
    
    # --- Add company name above main header ---
    company_id = get_user_company_id()
    company_data = company_model.get_record("companies", company_id) if company_id else {}
    company_name = company_data.get('name', 'Unknown') if company_data else 'Unknown'
    story.append(Paragraph(f"<b>{company_name.upper()}</b>", title_style))

    # Title and header info
    story.append(Paragraph(title, title_style))
    story.append(Paragraph(f"Cycle #{cycle.get('cycle_ext1', cycle['id'])} - {cycle.get('status', 'active').title()} - Generated on {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}", styles['Normal']))
    story.append(Spacer(1, 15))
    
    # Get all the data we need (same as the HTML templates use)
    daily_entries = daily_model.get_entries_by_cycle(cycle['id'])
    
    # Get feeds for this cycle
    all_feeds = feed_model.get_records("feeds")
    feeds = []
    for feed_id, feed_data in all_feeds.items():
        if feed_data.get('cycle_id') == cycle['id']:
            feeds.append({'id': feed_id, **feed_data})
    feeds.sort(key=lambda x: x.get('date', ''), reverse=True)
    
    # Get dispatches for this cycle
    all_dispatches = dispatch_model.get_records("bird_dispatches")
    dispatches = []
    for dispatch_id, dispatch_data in all_dispatches.items():
        if dispatch_data.get('cycle_id') == cycle['id']:
            dispatches.append({'id': dispatch_id, **dispatch_data})
    dispatches.sort(key=lambda x: x.get('dispatch_date', ''), reverse=True)
    
    # Get medicines for this cycle
    all_medicines = medicine_model.get_records("medicines")
    medicines = []
    for medicine_id, medicine_data in all_medicines.items():
        if medicine_data.get('cycle_id') == cycle['id']:
            medicines.append({'id': medicine_id, **medicine_data})
    
    # Get expenses for this cycle
    all_expenses = expense_model.get_records("expenses")
    expenses = []
    for expense_id, expense_data in all_expenses.items():
        if expense_data.get('cycle_id') == cycle['id']:
            expenses.append({'id': expense_id, **expense_data})
    expenses.sort(key=lambda x: x.get('date', ''), reverse=True)
    
    # Calculate stats (like in HTML)
    total_mortality = sum(entry.get('mortality', 0) for entry in daily_entries)
    total_feed_consumed = sum(entry.get('feed_bags_consumed', 0) for entry in daily_entries)
    total_feed_cost = sum(feed.get('total_cost', 0) for feed in feeds) if feeds else 0
    total_medical_cost = sum(med.get('price', 0) for med in medicines) if medicines else 0
    total_expense_cost = sum(exp.get('amount', 0) for exp in expenses) if expenses else 0
    survival_rate = (cycle.get('current_birds', 0) / cycle.get('start_birds', 1) * 100) if cycle.get('start_birds', 0) > 0 else 0
    
    # Calculate averages
    fcr_entries = [entry.get('fcr', 0) for entry in daily_entries if entry.get('fcr', 0) > 0]
    avg_fcr = sum(fcr_entries) / max(1, len(fcr_entries)) if fcr_entries else 0
    weight_entries = [entry.get('avg_weight', 0) for entry in daily_entries if entry.get('avg_weight', 0) > 0]
    avg_weight = sum(weight_entries) / max(1, len(weight_entries)) if weight_entries else 0
    
    # Cycle Overview (like the HTML card)
    story.append(Paragraph("🐔 Cycle Overview", heading_style))
    
    # Calculate duration
    duration_days = 0
    if cycle.get('start_date'):
        try:
            start_date = datetime.strptime(cycle['start_date'], '%Y-%m-%d').date()
            end_date = datetime.strptime(cycle['end_date'], '%Y-%m-%d').date() if cycle.get('end_date') else datetime.now().date()
            duration_days = (end_date - start_date).days + 1
        except:
            duration_days = 0
    
    overview_data = [
        ['Metric', 'Value', 'Metric', 'Value'],
        ['Start Date', convert_date_to_ddmmyyyy(cycle.get('start_date', '')) or 'Not set', 'End Date', convert_date_to_ddmmyyyy(cycle.get('end_date', '')) or 'Ongoing'],
        ['Start Time', cycle.get('start_time', 'N/A'), 'Duration', f"{duration_days} days"],
        ['Driver', cycle.get('driver', 'N/A'), 'Status', cycle.get('status', 'active').title()],
        ['Hatchery', cycle.get('hatchery', 'N/A'), 'Farmer Name', cycle.get('farmer_name', 'N/A')],
        ['Initial Birds', str(cycle.get('start_birds', 0)), 'Current Birds', str(cycle.get('current_birds', 0))],
        ['Initial Feed Bags', str(cycle.get('start_feed_bags', 0)), 'Notes', (cycle.get('notes', '')[:30] + '...' if cycle.get('notes', '') and len(cycle.get('notes', '')) > 30 else cycle.get('notes', 'None')) or 'None']
    ]
    
    overview_table = Table(overview_data, colWidths=[1.5*inch, 1.5*inch, 1.5*inch, 1.5*inch])
    overview_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
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
        ['Feed Cost', f"₹{total_feed_cost:.2f}", 'Feed Cost per Bird', f"₹{(total_feed_cost / cycle.get('current_birds', 1)):.2f}" if cycle.get('current_birds', 0) > 0 else 'N/A'],
        ['Avg Weight (kg)', f"{avg_weight:.3f}" if avg_weight > 0 else 'N/A', 'Mortality No.', str(total_mortality)],
        ['Total Bags Consumed', str(total_feed_consumed), 'Mortality Rate', f"{(total_mortality / cycle.get('start_birds', 1) * 100):.2f}%" if cycle.get('start_birds', 0) > 0 else 'N/A'],
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
    
    # Daily Entries (like the HTML table) - Enhanced with all columns
    if daily_entries:
        story.append(Paragraph(f"📅 Daily Entries ({len(daily_entries)} entries)", heading_style))
        
        # Show last 10 entries to avoid too long table but include all columns
        recent_entries = daily_entries[-10:] if len(daily_entries) > 10 else daily_entries
        
        # Enhanced daily data with all columns from HTML template - using Paragraph objects for headers
        header_style = ParagraphStyle(
            'HeaderStyle',
            parent=styles['Normal'],
            fontSize=6,
            alignment=1,  # Center alignment
            textColor=colors.whitesmoke,
            fontName='Helvetica-Bold'
        )
        
        # Create headers with line breaks using Paragraph objects
        daily_headers = [
            Paragraph('Day', header_style),
            Paragraph('Date', header_style),
            Paragraph('Deaths', header_style),
            Paragraph('Total<br/>Deaths', header_style),
            Paragraph('Death<br/>Rate(%)', header_style),
            Paragraph('Birds<br/>Alive', header_style),
            Paragraph('Avg Wt<br/>(g)', header_style),
            Paragraph('Feed<br/>Used', header_style),
            Paragraph('Feed<br/>Added', header_style),
            Paragraph('Total<br/>Used', header_style),
            Paragraph('Bags<br/>Left', header_style),
            Paragraph('Feed/Bird<br/>(g)', header_style),
            Paragraph('FCR', header_style),
            Paragraph('Medicine', header_style),
            Paragraph('Notes', header_style)
        ]
        
        daily_data = [daily_headers]
        
        total_days = len(daily_entries)
        for i, entry in enumerate(recent_entries):
            day_num = total_days - len(recent_entries) + i + 1
            daily_data.append([
                str(day_num),
                convert_date_to_ddmmyyyy(entry.get('entry_date', '')),
                str(entry.get('mortality', 0)),
                str(entry.get('total_mortality', 0)),
                f"{entry.get('mortality_rate', 0)}%",
                str(entry.get('birds_survived', 0)),
                f"{int(entry.get('avg_weight', 0) * 1000) if entry.get('avg_weight') else 0}",
                f"{entry.get('feed_bags_consumed', 0)}",
                f"{entry.get('feed_bags_added', 0)}",
                str(entry.get('total_bags_consumed', 0)),
                str(entry.get('remaining_bags', 0)),
                f"{entry.get('avg_feed_per_bird_g', 0):.1f}",
                f"{entry.get('fcr', 0):.3f}" if entry.get('fcr') else "0.000",
                (entry.get('medicines', '')[:15] + '...' if entry.get('medicines', '') and len(entry.get('medicines', '')) > 15 else entry.get('medicines', '')) or '-',
                (entry.get('daily_notes', '')[:15] + '...' if entry.get('daily_notes', '') and len(entry.get('daily_notes', '')) > 15 else entry.get('daily_notes', '')) or '-'
            ])
        
        # Adjust column widths for the enhanced table (total should be around 7.5 inches)
        daily_table = Table(daily_data, colWidths=[0.3*inch, 0.6*inch, 0.4*inch, 0.5*inch, 0.5*inch, 0.5*inch, 
                                                  0.5*inch, 0.4*inch, 0.4*inch, 0.4*inch, 0.4*inch, 
                                                  0.5*inch, 0.4*inch, 0.8*inch, 0.8*inch])
        daily_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 6),  # Smaller font to fit more columns
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(daily_table)
        
        if len(daily_entries) > 10:
            story.append(Paragraph(f"Showing last 10 entries out of {len(daily_entries)} total entries", styles['Italic']))
        
        story.append(Spacer(1, 15))
    
    # Feed Management (like HTML table)
    if feeds:
        story.append(Paragraph("🌾 Feed Management", heading_style))
        
        feed_data = [['Date', 'Feed Name', 'Bags', 'Weight/Bag', 'Total Cost']]
        total_cost = 0
        for feed in feeds:
            feed_data.append([
                convert_date_to_ddmmyyyy(feed.get('date', '')),
                (feed.get('feed_name', '')[:25] + '...' if len(feed.get('feed_name', '')) > 25 else feed.get('feed_name', '')),
                str(feed.get('feed_bags', 0)),
                f"{feed.get('bag_weight', 0)} kg",
                f"₹{feed.get('total_cost', 0):.2f}"
            ])
            total_cost += feed.get('total_cost', 0)
        
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
    
    # Medicine Summary (like HTML card) - Enhanced with date information
    if medicines:
        story.append(Paragraph("💊 Medicine Summary", heading_style))
        
        # Enhanced medicine data with date information
        medicine_data = [['Medicine Name', 'Date Added', 'Price per Unit', 'Quantity Unit', 'Total Value']]
        total_medicine_value = 0
        for med in medicines:
            value = med.get('price', 0) * med.get('qty', 1)
            # Format date if available
            date_str = convert_date_to_ddmmyyyy(med.get('created_date', '')) or 'N/A'
            medicine_data.append([
                (med.get('name', '')[:25] + '...' if len(med.get('name', '')) > 25 else med.get('name', '')),
                date_str,
                f"₹{med.get('price', 0):.2f}",
                str(med.get('qty', 1)),
                f"₹{value:.2f}"
            ])
            total_medicine_value += value
        
        medicine_data.append(['TOTAL', '', '', '', f"₹{total_medicine_value:.2f}"])
        
        medicine_table = Table(medicine_data, colWidths=[2*inch, 1*inch, 1*inch, 0.8*inch, 1.2*inch])
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
                (expense.get('name', '')[:25] + '...' if len(expense.get('name', '')) > 25 else expense.get('name', '')),
                convert_date_to_ddmmyyyy(expense.get('date', '')),
                f"₹{expense.get('amount', 0):.2f}",
                (expense.get('notes', '')[:30] + '...' if expense.get('notes', '') and len(expense.get('notes', '')) > 30 else expense.get('notes', '')) or '-'
            ])
            total_expenses += expense.get('amount', 0)
        
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
    
    # Dispatch History (like HTML table) - Enhanced with vendor and time details
    if dispatches:
        story.append(Paragraph("🚚 Dispatch History", heading_style))
        
        # Enhanced dispatch data with vendor and time information
        dispatch_data = [['Vehicle', 'Driver', 'Vendor', 'Date & Time', 'Birds', 'Weight (kg)', 'Avg/Bird (kg)', 'Status']]
        total_birds_dispatched = 0
        total_weight_dispatched = 0
        
        for dispatch in dispatches:
            # Format date and time
            date_time_str = f"{convert_date_to_ddmmyyyy(dispatch.get('dispatch_date', ''))}"
            if dispatch.get('dispatch_time'):
                date_time_str += f"\n{dispatch.get('dispatch_time')}"
            
            dispatch_data.append([
                dispatch.get('vehicle_no', ''),
                (dispatch.get('driver_name', '')[:12] + '...' if len(dispatch.get('driver_name', '')) > 12 else dispatch.get('driver_name', '')),
                (dispatch.get('vendor_name', '')[:12] + '...' if len(dispatch.get('vendor_name', '')) > 12 else dispatch.get('vendor_name', '')) if dispatch.get('vendor_name') else '-',
                date_time_str,
                str(dispatch.get('total_birds', '')) if dispatch.get('status') == 'completed' else 'In Progress',
                f"{dispatch.get('total_weight', 0):.1f}" if dispatch.get('status') == 'completed' else '-',
                f"{dispatch.get('avg_weight_per_bird', 0):.3f}" if dispatch.get('status') == 'completed' else '-',
                'Completed' if dispatch.get('status') == 'completed' else 'Active'
            ])
            
            if dispatch.get('status') == 'completed':
                total_birds_dispatched += dispatch.get('total_birds', 0)
                total_weight_dispatched += dispatch.get('total_weight', 0)
        
        # Add summary row
        dispatch_data.append([
            'TOTAL', '', '', '', 
            str(total_birds_dispatched), 
            f"{total_weight_dispatched:.1f}", 
            f"{(total_weight_dispatched/total_birds_dispatched):.3f}" if total_birds_dispatched > 0 else '0.000',
            'Summary'
        ])
        
        dispatch_table = Table(dispatch_data, colWidths=[0.8*inch, 1*inch, 1*inch, 1*inch, 0.7*inch, 0.8*inch, 0.8*inch, 0.8*inch])
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

def create_income_estimate_pdf_report(cycle, income_data, title="Income Estimate Report"):
    """Create a PDF report specifically for income estimates"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.5*inch, bottomMargin=0.5*inch, 
                          leftMargin=0.5*inch, rightMargin=0.5*inch)
    story = []
    styles = getSampleStyleSheet()
    
    # Custom styles
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
    
    # Company header
    company_id = get_user_company_id()
    company_data = company_model.get_record("companies", company_id) if company_id else {}
    company_name = company_data.get('name', 'Unknown') if company_data else 'Unknown'
    story.append(Paragraph(f"<b>{company_name.upper()}</b>", title_style))

    # Title and header info
    story.append(Paragraph(title, title_style))
    story.append(Paragraph(f"Cycle #{cycle.get('cycle_ext1', cycle['id'])} - Generated on {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}", styles['Normal']))
    story.append(Spacer(1, 20))
    
    # Cycle Overview
    story.append(Paragraph("Cycle Overview", heading_style))
    cycle_data = [
        ["Cycle Number", f"#{cycle.get('cycle_ext1', cycle['id'])}"],
        ["Start Date", cycle.get('start_date', 'N/A')],
        ["Initial Birds", f"{cycle.get('start_birds', 0):,}"],
        ["Current Birds", f"{cycle.get('current_birds', 0):,}"],
        ["Current Status", cycle.get('status', 'active').title()]
    ]
    
    cycle_table = Table(cycle_data, colWidths=[2.5*inch, 2.5*inch])
    cycle_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 10),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('BACKGROUND', (0,1), (-1,-1), colors.beige),
        ('GRID', (0,0), (-1,-1), 1, colors.black)
    ]))
    story.append(cycle_table)
    story.append(Spacer(1, 20))
    
    # Input Parameters Section
    story.append(Paragraph("Cost Parameters Used", heading_style))
    param_data = [
        ["Parameter", "Value", "Unit"],
        ["Chick Cost", f"₹{income_data.get('chick_price', 0):.2f}", "per bird"],
        ["Feed Cost", f"₹{income_data.get('feed_cost', 0):.2f}", "per kg"],
        ["Other Expenses", f"₹{income_data.get('other_expenses', 0):,.2f}", "total"],
        ["Market Price", f"₹{income_data.get('market_price_per_bird', 0):.2f}", "per kg"],
        ["Medicine Cost", f"₹{income_data.get('medicine_cost', 0):,.2f}", "total"],
        ["Vaccine Cost", f"₹{income_data.get('vaccine_cost', 0):,.2f}", "total"],
        ["Base PC Rate", f"₹{income_data.get('base_pc_rate', 0):.1f}", "per kg"],
        ["Base Income Rate", f"₹{income_data.get('base_income_rate', 0):.1f}", "per kg"]
    ]
    
    if income_data.get('custom_fcr'):
        param_data.append(["Custom FCR", f"{income_data['custom_fcr']:.2f}", "override"])
    
    param_table = Table(param_data, colWidths=[2*inch, 1.5*inch, 1.5*inch])
    param_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.darkblue),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 10),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('BACKGROUND', (0,1), (-1,-1), colors.lightblue),
        ('GRID', (0,0), (-1,-1), 1, colors.black)
    ]))
    story.append(param_table)
    story.append(Spacer(1, 20))
    
    # FCR-Based Calculation Section
    story.append(Paragraph("Method 1: FCR-Based Financial Analysis", heading_style))
    
    # Key Metrics
    metrics_data = [
        ["Metric", "Value"],
        ["Total Birds", f"{income_data.get('total_birds', 0):,}"],
        ["Average Weight per Bird", f"{income_data.get('avg_weight_per_bird', 0):.3f} kg"],
        ["Total Live Weight", f"{income_data.get('total_weight', 0):,.1f} kg"],
        ["FCR Used", f"{income_data.get('fcr_to_use', 0):.2f}"],
        ["Feed Needed", f"{income_data.get('feed_needed_kg', 0):,.1f} kg"]
    ]
    
    metrics_table = Table(metrics_data, colWidths=[2.5*inch, 2.5*inch])
    metrics_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.grey),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 10),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('BACKGROUND', (0,1), (-1,-1), colors.lightgrey),
        ('GRID', (0,0), (-1,-1), 1, colors.black)
    ]))
    story.append(metrics_table)
    story.append(Spacer(1, 15))
    
    # Cost Breakdown
    story.append(Paragraph("Cost Breakdown", heading_style))
    cost_data = [
        ["Cost Category", "Amount (₹)"],
        ["Chick Cost", f"₹{income_data.get('calculated_chick_cost_for_display', 0):,.2f}"],
        ["Feed Cost", f"₹{income_data.get('total_feed_cost', 0):,.2f}"],
        ["Medicine Cost (Actual)", f"₹{income_data.get('total_medical_cost', 0):,.2f}"],
        ["Other Expenses (Actual)", f"₹{income_data.get('total_expense_cost', 0):,.2f}"],
        ["Additional Expenses", f"₹{income_data.get('other_expenses', 0):,.2f}"],
        ["", ""],
        ["TOTAL COST", f"₹{income_data.get('total_cycle_cost', 0):,.2f}"]
    ]
    
    cost_table = Table(cost_data, colWidths=[2.5*inch, 2.5*inch])
    cost_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.green),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 10),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('BACKGROUND', (0,1), (-2,-2), colors.lightgreen),
        ('BACKGROUND', (0,-1), (-1,-1), colors.darkgreen),
        ('TEXTCOLOR', (0,-1), (-1,-1), colors.whitesmoke),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-2), 1, colors.black),
        ('GRID', (0,-1), (-1,-1), 2, colors.black)
    ]))
    story.append(cost_table)
    story.append(Spacer(1, 15))
    
    # Revenue and Profit Analysis (Market Price Method)
    story.append(Paragraph("Market Price Method - Financial Analysis", heading_style))
    profit_data = [
        ["Analysis", "Amount (₹)"],
        ["Estimated Income (Market Price)", f"₹{income_data.get('estimated_income', 0):,.2f}"],
        ["Total Cycle Cost", f"₹{income_data.get('total_cycle_cost', 0):,.2f}"],
        ["Estimated Profit/Loss", f"₹{income_data.get('estimated_profit', 0):,.2f}"]
    ]
    
    profit_table = Table(profit_data, colWidths=[2.5*inch, 2.5*inch])
    profit_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.blue),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 10),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('BACKGROUND', (0,1), (-1,-1), colors.lightblue),
        ('GRID', (0,0), (-1,-1), 1, colors.black),
        # Highlight profit row
        ('BACKGROUND', (0,-1), (-1,-1), colors.yellow if income_data.get('estimated_profit', 0) >= 0 else colors.lightcoral),
    ]))
    story.append(profit_table)
    story.append(Spacer(1, 20))
    
    # Production Cost (PC) Method Section
    story.append(Paragraph("Method 2: Production Cost (PC) Based Analysis", heading_style))
    
    # PC Method Explanation
    pc_explanation = """
    <b>Production Cost Method:</b> This method calculates profitability based on industry-standard PC rates.
    PC includes chick cost, feed cost, and medicine cost but excludes other expenses.
    The income rate is adjusted based on how the actual PC per kg compares to the base PC rate.
    """
    story.append(Paragraph(pc_explanation, styles['Normal']))
    story.append(Spacer(1, 10))
    
    # PC Cost Breakdown
    pc_cost_data = [
        ["PC Component", "Amount (₹)"],
        ["Chick Cost", f"₹{income_data.get('calculated_chick_cost_for_display', 0):,.2f}"],
        ["Feed Cost", f"₹{income_data.get('total_feed_cost', 0):,.2f}"],
        ["Medicine Cost (Actual)", f"₹{income_data.get('total_medical_cost', 0):,.2f}"],
        ["Vaccine Cost (Est.)", f"₹{income_data.get('vaccine_cost', 0):,.2f}"],
        ["", ""],
        ["Total Production Cost", f"₹{income_data.get('production_cost', 0):,.2f}"]
    ]
    
    pc_cost_table = Table(pc_cost_data, colWidths=[2.5*inch, 2.5*inch])
    pc_cost_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.orange),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 10),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('BACKGROUND', (0,1), (-2,-2), colors.wheat),
        ('BACKGROUND', (0,-1), (-1,-1), colors.darkorange),
        ('TEXTCOLOR', (0,-1), (-1,-1), colors.whitesmoke),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-2), 1, colors.black),
        ('GRID', (0,-1), (-1,-1), 2, colors.black)
    ]))
    story.append(pc_cost_table)
    story.append(Spacer(1, 15))
    
    # PC Analysis Results
    pc_analysis_data = [
        ["PC Analysis", "Value"],
        ["Production Cost per Kg", f"₹{income_data.get('pc_per_kg', 0):.2f}"],
        ["Base PC Rate", f"₹{income_data.get('base_pc_rate', 0):.1f}"],
        ["Base Income Rate", f"₹{income_data.get('base_income_rate', 0):.1f}"],
        ["Calculated Income Rate", f"₹{income_data.get('income_rate_per_kg', 0):.2f}"],
        ["", ""],
        ["PC-Based Total Income", f"₹{income_data.get('pc_based_income', 0):,.2f}"],
        ["PC-Based Profit/Loss", f"₹{income_data.get('pc_based_profit', 0):,.2f}"]
    ]
    
    pc_analysis_table = Table(pc_analysis_data, colWidths=[2.5*inch, 2.5*inch])
    pc_analysis_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.purple),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 10),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('BACKGROUND', (0,1), (-1,-1), colors.lavender),
        ('GRID', (0,0), (-1,-1), 1, colors.black),
        # Highlight profit row
        ('BACKGROUND', (0,-1), (-1,-1), colors.yellow if income_data.get('pc_based_profit', 0) >= 0 else colors.lightcoral),
    ]))
    story.append(pc_analysis_table)
    story.append(Spacer(1, 15))
    
    # Comparative Summary
    story.append(Paragraph("Comparative Summary", heading_style))
    is_profitable_market = income_data.get('estimated_profit', 0) >= 0
    is_profitable_pc = income_data.get('pc_based_profit', 0) >= 0
    
    total_birds = max(income_data.get('total_birds', 1), 1)  # Avoid division by zero
    
    # Create comparison table
    comparison_data = [
        ["Method", "Income", "Cost", "Profit/Loss", "Status"],
        [
            "Market Price Method", 
            f"₹{income_data.get('estimated_income', 0):,.2f}",
            f"₹{income_data.get('total_cycle_cost', 0):,.2f}",
            f"₹{income_data.get('estimated_profit', 0):,.2f}",
            "✓ Profitable" if is_profitable_market else "✗ Loss"
        ],
        [
            "PC Based Method", 
            f"₹{income_data.get('pc_based_income', 0):,.2f}",
            f"₹{income_data.get('production_cost', 0):,.2f}",
            f"₹{income_data.get('pc_based_profit', 0):,.2f}",
            "✓ Profitable" if is_profitable_pc else "✗ Loss"
        ]
    ]
    
    comparison_table = Table(comparison_data, colWidths=[1.5*inch, 1.2*inch, 1.2*inch, 1.2*inch, 1*inch])
    comparison_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.darkgreen),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 9),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('BACKGROUND', (0,1), (-1,1), colors.lightgreen if is_profitable_market else colors.lightcoral),
        ('BACKGROUND', (0,2), (-1,2), colors.lightgreen if is_profitable_pc else colors.lightcoral),
        ('GRID', (0,0), (-1,-1), 1, colors.black),
        ('FONTSIZE', (0,1), (-1,-1), 8),
    ]))
    story.append(comparison_table)
    story.append(Spacer(1, 15))
    
    # Additional metrics
    additional_metrics = f"""
    <b>Per Bird Analysis:</b><br/>
    • Market Method Cost per Bird: ₹{income_data.get('total_cycle_cost', 0)/total_birds:.2f}<br/>
    • PC Method Cost per Bird: ₹{income_data.get('production_cost', 0)/total_birds:.2f}<br/>
    • Market Revenue per Bird: ₹{income_data.get('estimated_income', 0)/total_birds:.2f}<br/>
    • PC Revenue per Bird: ₹{income_data.get('pc_based_income', 0)/total_birds:.2f}<br/>
    <br/>
    <b>Key Metrics:</b><br/>
    • Production Cost per Kg: ₹{income_data.get('pc_per_kg', 0):.2f}<br/>
    • Feed Cost per Kg of Live Weight: ₹{income_data.get('total_feed_cost', 0)/max(income_data.get('total_weight', 1), 1):.2f}<br/>
    • FCR Used: {income_data.get('fcr_to_use', 0):.2f}<br/>
    """
    
    story.append(Paragraph(additional_metrics, styles['Normal']))
    story.append(Spacer(1, 20))
    
    # Footer
    story.append(Paragraph(f"<i>Report generated on {datetime.now().strftime('%d-%m-%Y at %H:%M:%S')} by {company_name}</i>", 
                          styles['Normal']))
    
    # Build PDF
    doc.build(story)
    buffer.seek(0)
    return buffer

# ---------- Routes ----------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = user_model.get_user_by_username(username)
        
        if user and user_model.check_password(user, password):
            session['user_id'] = user['id']
            session['username'] = user.get('username')
            session['role'] = user.get('role', 'user')
            session['company_id'] = user.get('company_id')
            user_model.update_last_login(user['id'])
            flash(f'Welcome back, {user.get("full_name") or user.get("username")}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('home'))

@app.route('/contact')
def contact():
    """Contact page for getting started"""
    return render_template('contact.html')

@app.route('/switch_company/<company_id>')
@login_required
def switch_company(company_id):
    user = get_current_user()
    if user.get('role') != 'super_admin':
        flash('Access denied. Only super admin can switch companies.', 'error')
        return redirect(url_for('dashboard'))
    
    company = company_model.get_record("companies", company_id)
    if not company:
        flash('Invalid company selected.', 'error')
        return redirect(url_for('dashboard'))
    
    session['selected_company_id'] = company_id
    flash(f'Switched to {company.get("name")}', 'success')
    return redirect(url_for('dashboard'))

@app.route('/')
def home():
    """Landing page - redirect to dashboard if logged in, otherwise show welcome"""
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('welcome.html')

@app.route('/dashboard')
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
        daily_entries = daily_model.get_entries_by_cycle(cycle['id'])
        
        # Find today's entry
        today_entry = None
        for entry in daily_entries:
            if entry.get('entry_date') == today:
                today_entry = entry
                break
        
        # Basic calculations
        total_consumed = sum(entry.get('feed_bags_consumed', 0) for entry in daily_entries)
        total_mortality = sum(entry.get('mortality', 0) for entry in daily_entries)
        
        # Get feed data for total feed added calculation
        feed_records = feed_model.get_records("feeds", {'cycle_id': cycle['id']})
        total_feed_added = sum(feed.get('feed_bags', 0) for feed in feed_records.values())
        
        # Chart data
        for entry in daily_entries:
            dates.append(entry.get('entry_date', ''))
            fcr_series.append(entry.get('fcr', 0))
            mortality_series.append(entry.get('mortality', 0))
            feedbags_series.append(entry.get('feed_bags_consumed', 0))
            weight_series.append(entry.get('avg_weight', 0))
        
        # Advanced metrics
        stats = calc_cumulative_stats(cycle['id'])
        
        # Calculate total dispatched birds for current cycle
        dispatch_records = dispatch_model.get_records("bird_dispatches", {'cycle_id': cycle['id']})
        completed_dispatches = [d for d in dispatch_records.values() if d.get('status') == 'completed']
        total_birds_dispatched = sum(d.get('total_birds', 0) for d in completed_dispatches)
        
        # Fixed survival rate calculation: excludes dispatched birds from mortality
        # Survival Rate = (start_birds - deaths) / start_birds * 100
        # Current Birds = Start Birds - Deaths - Dispatched Birds
        # Therefore: Deaths = Start Birds - Current Birds - Dispatched Birds
        total_deaths = cycle.get('start_birds', 0) - cycle.get('current_birds', 0) - total_birds_dispatched
        if cycle.get('start_birds', 0) > 0:
            survival_rate = round(((cycle.get('start_birds', 0) - total_deaths) / cycle.get('start_birds', 0)) * 100, 2)
        else:
            survival_rate = 0
        
        feed_efficiency = round((total_consumed * 50 / cycle.get('current_birds', 1)), 2) if cycle.get('current_birds', 0) > 0 else 0
        
        # Calculate days running
        if cycle.get('start_date'):
            try:
                cycle_start_date = datetime.fromisoformat(cycle['start_date']).date()
                days_running = (date.today() - cycle_start_date).days + 1
            except (ValueError, TypeError):
                days_running = 1
        else:
            days_running = 1
        
        avg_mortality_per_day = round((total_mortality / max(days_running, 1)), 2)

        # Feed cost calculations
        feed_cost_per_kg = 45
        feed_cost_per_bag = feed_cost_per_kg * 50
        total_feed_cost = total_consumed * feed_cost_per_bag
        feed_cost_per_bird = round((total_feed_cost / cycle.get('current_birds', 1)), 2) if cycle.get('current_birds', 0) > 0 else 0
        
        # Performance indicators
        from datetime import timedelta
        last_week_date = (date.today() - timedelta(days=7)).isoformat()
        last_week_entries = [entry for entry in daily_entries 
                           if entry.get('entry_date', '') >= last_week_date]
        last_week_mortality = sum(entry.get('mortality', 0) for entry in last_week_entries)
        fcr_values = [entry.get('fcr', 0) for entry in last_week_entries if entry.get('fcr', 0) > 0]
        last_week_avg_fcr = round(sum(fcr_values) / max(len(fcr_values), 1), 3) if fcr_values else 0
        
        dashboard_metrics = {
            "survival_rate": survival_rate,
            "feed_efficiency": feed_efficiency,
            "avg_mortality_per_day": avg_mortality_per_day,
            "last_week_mortality": last_week_mortality,
            "last_week_avg_fcr": last_week_avg_fcr,
            "total_feed_cost": total_feed_cost,
            "feed_cost_per_bird": feed_cost_per_bird,
            "feed_cost_per_bag": feed_cost_per_bag,
            "total_feed_added": total_feed_added,
            "feed_utilization": round(((total_consumed / (cycle.get('start_feed_bags', 0) + total_feed_added)) * 100), 2) if (cycle.get('start_feed_bags', 0) + total_feed_added) > 0 else 0,
            "days_to_target": max(42 - days_running, 0),
            "today_mortality": today_entry.get('mortality', 0) if today_entry else 0,
            "today_feed_consumed": today_entry.get('feed_bags_consumed', 0) if today_entry else 0,
            "today_avg_weight": today_entry.get('avg_weight', 0) if today_entry else 0,
        }
        
        summary = {
            "start_birds": cycle.get('start_birds', 0),
            "current_birds": cycle.get('current_birds', 0),
            "start_date": cycle.get('start_date'),
            "days": days_running,
            "bags_available": total_feed_added,
            "feed_bags_consumed_total": total_consumed,
            "mortality_total": total_mortality,
            "fcr_today": 0,  # You can implement calc_todays_fcr if needed
            "cumulative_fcr": stats["cumulative_fcr"],
            "avg_fcr": stats["avg_fcr"],
            "avg_weight": stats["avg_weight"]
        }
    
    return render_template('dashboard.html', 
                         cycle=cycle, 
                         summary=summary, 
                         fcr_series=fcr_series, 
                         dates=dates, 
                         mortality_series=mortality_series, 
                         feedbags_series=feedbags_series, 
                         weight_series=weight_series, 
                         metrics=dashboard_metrics, 
                         cycle_id=cycle['id'] if cycle else None, 
                         cycle_number=cycle.get('cycle_ext1') if cycle else None)

@app.route('/setup', methods=['GET', 'POST'])
@admin_required
def setup():
    existing_cycle = get_active_cycle()
    
    if request.method == 'POST':
        user = get_current_user()
        company_id = get_user_company_id()
        
        # Find max cycle_number for this company
        cycles = cycle_model.get_records("cycles", {'company_id': company_id})
        cycle_numbers = [cycle.get('cycle_ext1', 0) for cycle in cycles.values() if cycle.get('cycle_ext1')]
        next_cycle_number = max(cycle_numbers, default=0) + 1
        
        start_birds = int(request.form.get('start_birds', 0))
        start_feed_bags = float(request.form.get('start_feed_bags', 0))
        hatchery = request.form.get('hatchery_name', '')
        farmer_name = request.form.get('farm_owner', '')
        start_date = request.form.get('start_date') or date.today().isoformat()
        start_time = request.form.get('start_time') or datetime.now().time().isoformat(timespec='minutes')
        driver = request.form.get('driver', '')
        notes = request.form.get('notes', '')
        
        # Archive existing active cycles for this company
        active_cycles = cycle_model.get_records("cycles")
        for cycle_id, cycle_data in active_cycles.items():
            if (cycle_data.get('company_id') == company_id and 
                cycle_data.get('status') == 'active'):
                cycle_model.archive_cycle(cycle_id)
        
        # Create new cycle
        cycle_id = cycle_model.create_cycle(
            company_id=company_id,
            cycle_ext1=next_cycle_number,
            start_date=start_date,
            start_time=start_time,
            start_birds=start_birds,
            current_birds=start_birds,
            start_feed_bags=start_feed_bags,
            driver=driver,
            hatchery=hatchery,
            farmer_name=farmer_name,
            notes=notes,
            status='active',
            created_by=user['id']
        )
        
        flash(f'New cycle #{next_cycle_number} created successfully!', 'success')
        return redirect(url_for('dashboard'))
    
    return render_template('setup.html', existing_cycle=existing_cycle)

@app.route('/daily', methods=['GET', 'POST'])
@login_required
def daily():
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('setup'))
    
    # Calculate bags available from Feed records
    feed_records = feed_model.get_records("feeds", {'cycle_id': cycle['id']})
    total_feed_bags = sum(feed.get('feed_bags', 0) for feed in feed_records.values())
    
    # Calculate total consumed from Daily entries
    daily_entries = daily_model.get_entries_by_cycle(cycle['id'])
    total_consumed = sum(entry.get('feed_bags_consumed', 0) for entry in daily_entries)
    
    bags_available = total_feed_bags - total_consumed
    
    if request.method == 'POST':
        entry_date = request.form.get('entry_date') or date.today().isoformat()
        mortality = int(request.form.get('mortality', 0))
        feed_bags_consumed = int(request.form.get('feed_bags_consumed', 0))
        avg_weight_grams = float(request.form.get('avg_weight_grams', 0) or 0)
        avg_weight = round(avg_weight_grams / 1000, 3) if avg_weight_grams > 0 else 0
        medicines = request.form.get('medicines', '')
        daily_notes = request.form.get('daily_notes', '').strip()
        
        # Calculate derived values
        live_after = cycle['current_birds'] - mortality
        previous_mortality = sum(entry.get('mortality', 0) for entry in daily_entries)
        total_mortality = previous_mortality + mortality
        
        previous_consumed = sum(entry.get('feed_bags_consumed', 0) for entry in daily_entries)
        total_bags_consumed = previous_consumed + feed_bags_consumed
        remaining_bags = total_feed_bags - total_bags_consumed
        
        mortality_rate = round((total_mortality / cycle['start_birds'] * 100), 2) if cycle['start_birds'] > 0 else 0
        
        # Validate inputs
        if not entry_date or not avg_weight_grams:
            flash('Please fill in all required fields.', 'error')
            return render_template('daily.html', cycle=cycle, bags_available=bags_available)
        
        if remaining_bags < 0:
            flash(f'Insufficient feed bags! Available: {bags_available}, Consumed: {feed_bags_consumed}', 'error')
            return render_template('daily.html', cycle=cycle, bags_available=bags_available)
        
        # Calculate avg_feed_per_bird_g
        if live_after > 0:
            previous_entries = [e for e in daily_entries if e.get('entry_date', '') < entry_date]
            cumulative_feed_consumed = sum(e.get('feed_bags_consumed', 0) for e in previous_entries) + feed_bags_consumed
            
            cycle_start_date = datetime.fromisoformat(cycle['start_date']).date()
            current_entry_date = datetime.fromisoformat(entry_date).date()
            days_elapsed = (current_entry_date - cycle_start_date).days + 1
            
            total_feed_grams = cumulative_feed_consumed * 50 * 1000  # bags to grams
            avg_feed_per_bird_g = round((total_feed_grams / live_after / days_elapsed), 1) if days_elapsed > 0 else 0
        else:
            avg_feed_per_bird_g = 0
        
        # Calculate FCR
        feed_kg = feed_bags_consumed * 50
        fcr = round((feed_kg / (avg_weight * live_after)), 3) if (avg_weight > 0 and live_after > 0) else 0
        
        user = get_current_user()
        company_id = get_user_company_id()
        
        # Create daily entry
        daily_entry_id = daily_model.create_daily_entry(
            company_id=company_id,
            cycle_id=cycle['id'],
            entry_date=entry_date,
            mortality=mortality,
            feed_bags_consumed=feed_bags_consumed,
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
            created_by=user['id']
        )
        
        # Update cycle's current birds
        cycle_model.update_record("cycles", cycle['id'], {
            'current_birds': live_after,
            'modified_by': user['id'],
            'modified_date': datetime.utcnow().isoformat()
        })
        
        flash(f'Daily entry saved! Consumed: {feed_bags_consumed} bags, Remaining: {remaining_bags} bags', 'success')
        return redirect(url_for('dashboard'))
    
    # Get medicines for current cycle
    medicine_records = medicine_model.get_records("medicines", {'cycle_id': cycle['id']})
    medicines = [{'id': k, **v} for k, v in medicine_records.items()]
    
    return render_template('daily.html', cycle=cycle, meds=medicines, bags_available=bags_available)

@app.route('/daywise')
@login_required
def daywise():
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('setup'))
    
    daily_entries = daily_model.get_entries_by_cycle(cycle['id'])
    
    # Enhance entries with feed_bags_added from Feed records
    for entry in daily_entries:
        entry_date = entry.get('entry_date')
        feed_records = feed_model.get_records("feeds")
        feed_added_on_date = sum(
            feed.get('feed_bags', 0) 
            for feed in feed_records.values() 
            if feed.get('date') == entry_date and feed.get('cycle_id') == cycle['id']
        )
        entry['feed_bags_added'] = feed_added_on_date
    
    # Recalculate cumulative values
    cumulative_mortality = 0
    birds_survived = cycle.get('start_birds', 0)
    for entry in daily_entries:
        cumulative_mortality += entry.get('mortality', 0)
        birds_survived -= entry.get('mortality', 0)
        entry['total_mortality'] = cumulative_mortality
        entry['birds_survived'] = birds_survived
    
    # Calculate weekly summaries
    weekly_summaries = []
    if daily_entries:
        for week_start in range(0, len(daily_entries), 7):
            week_end = min(week_start + 7, len(daily_entries))
            week_entries = daily_entries[week_start:week_end]
            
            if len(week_entries) > 0:
                week_num = (week_start // 7) + 1
                
                # Calculate averages and totals
                avg_mortality = round(sum(e.get('mortality', 0) for e in week_entries) / len(week_entries), 1)
                avg_feed_consumed = round(sum(e.get('feed_bags_consumed', 0) for e in week_entries) / len(week_entries), 1)
                
                weight_entries = [e.get('avg_weight', 0) for e in week_entries if e.get('avg_weight', 0) > 0]
                avg_weight = round(sum(weight_entries) / len(weight_entries), 3) if weight_entries else 0
                
                total_mortality = sum(e.get('mortality', 0) for e in week_entries)
                total_feed_consumed = sum(e.get('feed_bags_consumed', 0) for e in week_entries)
                total_feed_added = sum(e.get('feed_bags_added', 0) for e in week_entries)
                
                # Latest day values
                latest_day = week_entries[-1]
                latest_fcr = latest_day.get('fcr', 0)
                latest_birds_survived = latest_day.get('birds_survived', 0)
                latest_mortality_rate = latest_day.get('mortality_rate', 0)
                latest_remaining_bags = latest_day.get('remaining_bags', 0)
                days_in_week = len(week_entries)

                weekly_summary = {
                    'week_num': week_num,
                    'start_day': week_start + 1,
                    'end_day': week_end,
                    'days_in_week': len(week_entries),
                    'avg_mortality': avg_mortality,
                    'avg_feed_consumed': avg_feed_consumed,
                    'avg_weight': avg_weight,
                    'latest_fcr': latest_fcr,
                    'total_mortality': total_mortality,
                    'total_feed_consumed': total_feed_consumed,
                    'total_feed_added': total_feed_added,
                    'latest_mortality_rate': latest_mortality_rate,
                    'latest_birds_survived': latest_birds_survived,
                    'latest_remaining_bags': latest_remaining_bags
                }
                weekly_summaries.append(weekly_summary)
    
    return render_template('daywise.html', rows=daily_entries, cycle=cycle, weekly_summaries=weekly_summaries)

@app.route('/stats')
@login_required
def stats():
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('setup'))
    
    # Get basic stats
    stats = calc_cumulative_stats(cycle['id'])
    
    # Get daily entries for trend analysis
    daily_entries = daily_model.get_entries_by_cycle(cycle['id'])
    
    # Calculate additional statistics
    feed_records = feed_model.get_records("feeds", {'cycle_id': cycle['id']})
    total_feed_added = sum(feed.get('feed_bags', 0) for feed in feed_records.values())
    current_feed_bags = max(0, round(cycle.get('start_feed_bags', 0)))
    
    # Calculate cycle duration and performance metrics
    if cycle.get('start_date'):
        try:
            cycle_start_date = datetime.fromisoformat(cycle['start_date']).date()
            days_running = (date.today() - cycle_start_date).days + 1
        except (ValueError, TypeError):
            days_running = 1
    else:
        days_running = 1
    
    # Calculate total dispatched birds for current cycle
    dispatch_records = dispatch_model.get_records("bird_dispatches", {'cycle_id': cycle['id']})
    completed_dispatches = [d for d in dispatch_records.values() if d.get('status') == 'completed']
    total_birds_dispatched = sum(d.get('total_birds', 0) for d in completed_dispatches)
    
    # Performance metrics
    # Fixed survival rate calculation: excludes dispatched birds from mortality
    total_deaths = cycle.get('start_birds', 0) - cycle.get('current_birds', 0) - total_birds_dispatched
    if cycle.get('start_birds', 0) > 0:
        survival_rate = round(((cycle.get('start_birds', 0) - total_deaths) / cycle.get('start_birds', 0)) * 100, 2)
    else:
        survival_rate = 0
    mortality_rate = round(((stats["total_mortality"] / cycle.get('start_birds', 1)) * 100), 2) if cycle.get('start_birds', 0) > 0 else 0
    feed_efficiency = round((stats["total_feed_bags"] / cycle.get('current_birds', 1)), 2) if cycle.get('current_birds', 0) > 0 else 0
    avg_daily_mortality = round((stats["total_mortality"] / max(days_running, 1)), 2)
    
    # Cost calculations
    feed_cost_per_bag = 2000
    total_feed_cost = stats["total_feed_bags"] * feed_cost_per_bag
    feed_cost_per_bird = round((total_feed_cost / cycle.get('current_birds', 1)), 2) if cycle.get('current_birds', 0) > 0 else 0
    
    # Medicine costs for current cycle
    medicine_records = medicine_model.get_records("medicines", {'cycle_id': cycle['id']})
    total_medicine_cost = sum(med.get('price', 0) for med in medicine_records.values() if med.get('price'))
    
    # Weight gain analysis
    starting_weight_kg = 0.045  # 45g day-old chicks
    weight_gain_per_bird = stats["avg_weight"] - starting_weight_kg if stats["avg_weight"] > 0 else 0
    total_weight_gain = weight_gain_per_bird * cycle.get('current_birds', 0)
    
    # Prepare data for charts
    chart_data = {
        'survival_vs_mortality': {
            'labels': ['Live Birds', 'Mortality'],
            'data': [cycle.get('current_birds', 0), stats["total_mortality"]]
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
            'feed_efficiency_score': min(100, max(0, 100 - (feed_efficiency * 10))),
            'weight_gain_score': min(100, max(0, (weight_gain_per_bird / 0.002) * 100))
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
        'target_days': 42,
        'days_remaining': max(0, 42 - days_running)
    }
    
    return render_template('stats.html', cycle=cycle, stats=enhanced_stats, chart_data=chart_data)

# Add other routes like medicines, expenses, feed_management, etc. following the same pattern...
# I'll add a few more key routes:

@app.route('/medicines', methods=['GET', 'POST'])
@login_required
def medicines():
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('setup'))
    
    if request.method == 'POST':
        name = request.form.get('name')
        price = float(request.form.get('price', 0) or 0)
        qty = int(request.form.get('qty', 0) or 0)
        medicine_ext1 = request.form.get('medicine_ext1', '').strip()
        
        user = get_current_user()
        company_id = get_user_company_id()
        
        medicine_data = {
            'company_id': company_id,
            'cycle_id': cycle['id'],
            'name': name,
            'price': price,
            'qty': qty,
            'medicine_ext1': medicine_ext1,
            'created_by': user['id'],
            'created_date': datetime.utcnow().isoformat()
        }
        
        medicine_id = medicine_model.create_record("medicines", medicine_data)
        flash(f'Medicine "{name}" added successfully for cycle #{cycle["id"]}!', 'success')
        return redirect(url_for('medicines'))
    
    # Get medicines for current cycle
    medicine_records = medicine_model.get_records("medicines", {'cycle_id': cycle['id']})
    medicines = [{'id': k, **v} for k, v in medicine_records.items()]
    medicines.sort(key=lambda x: x.get('created_date', ''), reverse=True)
    
    total_amount = sum(med.get('price', 0) for med in medicines)
    
    return render_template('medicines.html', meds=medicines, total_amount=total_amount, cycle=cycle)

@app.route('/feed_management', methods=['GET', 'POST'])
@login_required
def feed_management():
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('setup'))
        
    if request.method == 'POST':
        bill_number = request.form.get('bill_number', '').strip()
        feed_date = request.form.get('date') or date.today().isoformat()
        feed_name = request.form.get('feed_name', '').strip()
        feed_bags = int(request.form.get('feed_bags', 0) or 0)
        bag_weight = float(request.form.get('bag_weight', 50.0) or 50.0)
        price_per_kg = float(request.form.get('price_per_kg', 0) or 0)
        
        total_feed_kg = feed_bags * bag_weight
        total_cost = total_feed_kg * price_per_kg
        
        user = get_current_user()
        company_id = get_user_company_id()
        
        feed_data = {
            'company_id': company_id,
            'cycle_id': cycle['id'],
            'bill_number': bill_number,
            'date': feed_date,
            'feed_name': feed_name,
            'feed_bags': feed_bags,
            'bag_weight': bag_weight,
            'total_feed_kg': total_feed_kg,
            'price_per_kg': price_per_kg,
            'total_cost': total_cost,
            'created_by': user['id'],
            'created_date': datetime.utcnow().isoformat()
        }
        
        feed_id = feed_model.create_record("feeds", feed_data)
        flash(f'Feed entry "{feed_name}" added successfully!', 'success')
        return redirect(url_for('feed_management'))

    # Filter feeds by current cycle
    feed_records = feed_model.get_records("feeds", {'cycle_id': cycle['id']})
    feeds = [{'id': k, **v} for k, v in feed_records.items()]
    feeds.sort(key=lambda x: x.get('date', ''), reverse=True)
    
    total_cost = sum(f.get('total_cost', 0) for f in feeds)
    return render_template('feed_management.html', feeds=feeds, total_cost=total_cost, cycle=cycle)

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
            flash('Please enter valid expense name and amount.', 'error')
            return redirect(url_for('expenses'))
        
        user = get_current_user()
        company_id = get_user_company_id()
        
        expense_data = {
            'company_id': company_id,
            'cycle_id': cycle['id'],
            'name': name,
            'date': expense_date,
            'amount': amount,
            'notes': notes,
            'created_by': user['id'],
            'created_date': datetime.utcnow().isoformat()
        }
        
        expense_id = expense_model.create_record("expenses", expense_data)
        flash(f'Expense "{name}" added successfully for cycle #{cycle["id"]}!', 'success')
        return redirect(url_for('expenses'))
    
    # Filter expenses by current cycle
    expense_records = expense_model.get_records("expenses", {'cycle_id': cycle['id']})
    expenses_list = [{'id': k, **v} for k, v in expense_records.items()]
    expenses_list.sort(key=lambda x: x.get('date', ''), reverse=True)
    
    total_amount = sum(exp.get('amount', 0) for exp in expenses_list)
    
    return render_template('expenses.html', expenses=expenses_list, total_amount=total_amount, date=date, cycle=cycle)

@app.route('/edit_expense/<expense_id>', methods=['GET', 'POST'])
@admin_required
def edit_expense(expense_id):
    """Edit expense record (admin only)"""
    expense_data = expense_model.get_record("expenses", expense_id)
    if not expense_data:
        flash('Expense not found.', 'error')
        return redirect(url_for('expenses'))
    
    expense = {'id': expense_id, **expense_data}
    cycle = get_active_cycle()
    
    if not cycle or expense.get('cycle_id') != cycle['id']:
        flash('Cannot edit expense from a different cycle.', 'error')
        return redirect(url_for('expenses'))
    
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        expense_date = request.form.get('date') or expense.get('date')
        amount = float(request.form.get('amount', 0) or 0)
        notes = request.form.get('notes', '').strip()
        
        if not name or amount <= 0:
            flash('Please enter valid expense name and amount.', 'error')
            return redirect(url_for('edit_expense', expense_id=expense_id))
        
        user = get_current_user()
        update_data = {
            'name': name,
            'date': expense_date,
            'amount': amount,
            'notes': notes,
            'modified_by': user['id'],
            'modified_date': datetime.utcnow().isoformat()
        }
        
        success = expense_model.update_record("expenses", expense_id, update_data)
        if success:
            flash(f'Expense "{name}" updated successfully!', 'success')
        else:
            flash('Error updating expense.', 'error')
        
        return redirect(url_for('expenses'))
    
    return render_template('edit_expense.html', expense=expense, cycle=cycle, date=date)

@app.route('/delete_expense/<expense_id>', methods=['POST'])
@admin_required
def delete_expense(expense_id):
    """Delete an expense (admin only)"""
    try:
        expense_data = expense_model.get_record("expenses", expense_id)
        if expense_data:
            expense_name = expense_data.get('name', 'Unknown')
            expense_model.delete_record("expenses", expense_id)
            flash(f'Expense "{expense_name}" deleted successfully!', 'success')
        else:
            flash('Expense not found.', 'error')
    except Exception as e:
        flash(f'Error deleting expense: {str(e)}', 'error')
    
    return redirect(url_for('expenses'))

@app.route('/edit_feed/<feed_id>', methods=['GET', 'POST'])
@admin_required
def edit_feed(feed_id):
    """Edit feed record (admin only)"""
    feed_data = feed_model.get_record("feeds", feed_id)
    if not feed_data:
        flash('Feed record not found.', 'error')
        return redirect(url_for('feed_management'))
    
    feed = {'id': feed_id, **feed_data}
    cycle = get_active_cycle()
    
    if not cycle or feed.get('cycle_id') != cycle['id']:
        flash('Cannot edit feed from a different cycle.', 'error')
        return redirect(url_for('feed_management'))
    
    if request.method == 'POST':
        bill_number = request.form.get('bill_number', '').strip()
        feed_date = request.form.get('date') or feed.get('date')
        feed_name = request.form.get('feed_name', '').strip()
        feed_bags = int(request.form.get('feed_bags', 0) or 0)
        bag_weight = float(request.form.get('bag_weight', 50.0) or 50.0)
        price_per_kg = float(request.form.get('price_per_kg', 0) or 0)
        
        if not feed_name or feed_bags <= 0:
            flash('Please enter valid feed name and number of bags.', 'error')
            return redirect(url_for('edit_feed', feed_id=feed_id))
        
        total_feed_kg = feed_bags * bag_weight
        total_cost = total_feed_kg * price_per_kg
        
        user = get_current_user()
        update_data = {
            'bill_number': bill_number,
            'date': feed_date,
            'feed_name': feed_name,
            'feed_bags': feed_bags,
            'bag_weight': bag_weight,
            'total_feed_kg': total_feed_kg,
            'price_per_kg': price_per_kg,
            'total_cost': total_cost,
            'modified_by': user['id'],
            'modified_date': datetime.utcnow().isoformat()
        }
        
        success = feed_model.update_record("feeds", feed_id, update_data)
        if success:
            flash(f'Feed entry "{feed_name}" updated successfully!', 'success')
        else:
            flash('Error updating feed record.', 'error')
        
        return redirect(url_for('feed_management'))
    
    return render_template('edit_feed.html', feed=feed, cycle=cycle, date=date)

@app.route('/delete_feed/<feed_id>', methods=['POST'])
@admin_required
def delete_feed(feed_id):
    """Delete a feed record (admin only)"""
    try:
        feed_data = feed_model.get_record("feeds", feed_id)
        if feed_data:
            feed_name = feed_data.get('feed_name', 'Unknown')
            bill_number = feed_data.get('bill_number', 'N/A')
            
            # Check if the feed belongs to the current active cycle
            cycle = get_active_cycle()
            if not cycle or feed_data.get('cycle_id') != cycle['id']:
                flash('Cannot delete feed from a different cycle.', 'error')
                return redirect(url_for('feed_management'))
            
            feed_model.delete_record("feeds", feed_id)
            flash(f'Feed entry "{feed_name}" (Bill: {bill_number}) has been deleted successfully.', 'success')
        else:
            flash('Feed record not found.', 'error')
    except Exception as e:
        flash(f'Error deleting feed record: {str(e)}', 'error')
    
    return redirect(url_for('feed_management'))

@app.route('/edit_medicine/<medicine_id>', methods=['GET', 'POST'])
@admin_required
def edit_medicine(medicine_id):
    """Edit medicine record (admin only)"""
    medicine_data = medicine_model.get_record("medicines", medicine_id)
    if not medicine_data:
        flash('Medicine not found.', 'error')
        return redirect(url_for('medicines'))
    
    medicine = {'id': medicine_id, **medicine_data}
    cycle = get_active_cycle()
    
    if not cycle or medicine.get('cycle_id') != cycle['id']:
        flash('Cannot edit medicine from a different cycle.', 'error')
        return redirect(url_for('medicines'))
    
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        price = float(request.form.get('price', 0) or 0)
        qty = int(request.form.get('qty', 0) or 0)
        medicine_ext1 = request.form.get('medicine_ext1', '').strip()
        
        if not name:
            flash('Medicine name is required.', 'error')
            return redirect(url_for('edit_medicine', medicine_id=medicine_id))
        
        user = get_current_user()
        update_data = {
            'name': name,
            'price': price,
            'qty': qty,
            'medicine_ext1': medicine_ext1,
            'modified_by': user['id'],
            'modified_date': datetime.utcnow().isoformat()
        }
        
        success = medicine_model.update_record("medicines", medicine_id, update_data)
        if success:
            flash(f'Medicine "{name}" updated successfully!', 'success')
        else:
            flash('Error updating medicine.', 'error')
        
        return redirect(url_for('medicines'))
    
    return render_template('edit_medicine.html', medicine=medicine, cycle=cycle)

@app.route('/delete_medicine/<medicine_id>', methods=['POST'])
@admin_required
def delete_medicine(medicine_id):
    """Delete a medicine record (admin only)"""
    try:
        medicine_data = medicine_model.get_record("medicines", medicine_id)
        if medicine_data:
            medicine_name = medicine_data.get('name', 'Unknown')
            medicine_model.delete_record("medicines", medicine_id)
            flash(f'Medicine "{medicine_name}" deleted successfully!', 'success')
        else:
            flash('Medicine not found.', 'error')
    except Exception as e:
        flash(f'Error deleting medicine: {str(e)}', 'error')
    
    return redirect(url_for('medicines'))

@app.route('/end_current_cycle', methods=['POST'])
@admin_required
def end_current_cycle():
    """End and archive the current cycle"""
    cycle = get_active_cycle()
    if cycle:
        cycle_model.update_record("cycles", cycle['id'], {
            'status': 'archived',
            'end_date': date.today().isoformat(),
            'notes': f"Ended on {datetime.now().isoformat()} - {cycle.get('notes', '')}"
        })
        flash('Current cycle ended and archived. All data has been preserved for historical records. You can now start a new cycle.', 'info')
    else:
        flash('No active cycle found to end.', 'error')
    return redirect(url_for('setup'))

@app.route('/reset_cycle', methods=['POST'])
@admin_required
def reset_cycle():
    """Reset (archive) the current cycle"""
    cycle = get_active_cycle()
    if cycle:
        cycle_model.update_record("cycles", cycle['id'], {
            'status': 'archived',
            'end_date': date.today().isoformat(),
            'notes': f"Archived on {datetime.now().isoformat()} - {cycle.get('notes', '')}"
        })
        flash('Cycle archived successfully!', 'info')
    
    return redirect(url_for('setup'))

@app.route('/delete_cycle/<cycle_id>', methods=['POST'])
@admin_required
def delete_cycle(cycle_id):
    """Delete a cycle permanently (admin only)"""
    try:
        cycle_data = cycle_model.get_record("cycles", cycle_id)
        if not cycle_data:
            flash('Cycle not found.', 'error')
            return redirect(url_for('cycle_history'))
        
        current_user = get_current_user()
        
        # Security check: ensure admin can only delete cycles from their company
        if current_user.get('role') != 'super_admin':
            company_id = get_user_company_id()
            if cycle_data.get('company_id') != company_id:
                flash('Access denied. You can only delete cycles from your company.', 'error')
                return redirect(url_for('cycle_history'))
        
        # Prevent deletion of active cycle
        if cycle_data.get('status') == 'active':
            flash('Cannot delete an active cycle. Please archive it first.', 'error')
            return redirect(url_for('cycle_history'))
        
        cycle_name = f"Cycle #{cycle_data.get('cycle_ext1', cycle_id)}"
        
        # Delete associated records first
        # Delete daily entries
        daily_entries = daily_model.get_records("daily_entries", {'cycle_id': cycle_id})
        for entry_id in daily_entries.keys():
            daily_model.delete_record("daily_entries", entry_id)
            
        # Delete medicine records
        medicine_records = medicine_model.get_records("medicines", {'cycle_id': cycle_id})
        for medicine_id in medicine_records.keys():
            medicine_model.delete_record("medicines", medicine_id)
            
        # Delete feed records
        feed_records = feed_model.get_records("feeds", {'cycle_id': cycle_id})
        for feed_id in feed_records.keys():
            feed_model.delete_record("feeds", feed_id)
            
        # Delete expense records
        expense_records = expense_model.get_records("expenses", {'cycle_id': cycle_id})
        for expense_id in expense_records.keys():
            expense_model.delete_record("expenses", expense_id)
            
        # Delete dispatch records
        dispatch_records = dispatch_model.get_records("bird_dispatches", {'cycle_id': cycle_id})
        for dispatch_id in dispatch_records.keys():
            dispatch_model.delete_record("bird_dispatches", dispatch_id)
            
        # Delete weighing records
        weighing_records = weighing_model.get_records("weighing_records", {'cycle_id': cycle_id})
        for weighing_id in weighing_records.keys():
            weighing_model.delete_record("weighing_records", weighing_id)
        
        # Finally delete the cycle itself
        cycle_model.delete_record("cycles", cycle_id)
        
        flash(f'{cycle_name} and all associated data have been deleted successfully.', 'success')
    except Exception as e:
        flash(f'Error deleting cycle: {str(e)}', 'error')
    
    return redirect(url_for('cycle_history'))

@app.route('/unarchive_cycle/<cycle_id>', methods=['POST'])
@admin_required
def unarchive_cycle(cycle_id):
    """Unarchive a cycle (make it active again)"""
    try:
        cycle_data = cycle_model.get_record("cycles", cycle_id)
        if not cycle_data:
            flash('Cycle not found.', 'error')
            return redirect(url_for('cycle_history'))
        
        current_user = get_current_user()
        
        # Security check: ensure admin can only unarchive cycles from their company
        if current_user.get('role') != 'super_admin':
            company_id = get_user_company_id()
            if cycle_data.get('company_id') != company_id:
                flash('Access denied. You can only unarchive cycles from your company.', 'error')
                return redirect(url_for('cycle_history'))
        
        # Check if there's already an active cycle
        active_cycle = get_active_cycle()
        if active_cycle:
            flash('Cannot unarchive cycle. There is already an active cycle. Please archive the current cycle first.', 'error')
            return redirect(url_for('cycle_history'))
        
        cycle_name = f"Cycle #{cycle_data.get('cycle_ext1', cycle_id)}"
        
        # Unarchive the cycle
        cycle_model.update_record("cycles", cycle_id, {
            'status': 'active',
            'end_date': None,
            'modified_by': current_user['id'],
            'modified_date': datetime.utcnow().isoformat()
        })
        
        flash(f'{cycle_name} has been unarchived and is now active.', 'success')
    except Exception as e:
        flash(f'Error unarchiving cycle: {str(e)}', 'error')
    
    return redirect(url_for('cycle_history'))

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
            flash('Vehicle number and driver name are required.', 'error')
            return redirect(url_for('bird_dispatch'))
        
        user = get_current_user()
        company_id = get_user_company_id()
        
        dispatch_data = {
            'company_id': company_id,
            'cycle_id': cycle['id'],
            'vehicle_no': vehicle_no,
            'driver_name': driver_name,
            'vendor_name': vendor_name,
            'dispatch_date': dispatch_date,
            'dispatch_time': dispatch_time,
            'notes': notes,
            'total_birds': 0,
            'total_weight': 0.0,
            'avg_weight_per_bird': 0.0,
            'status': 'active',
            'created_by': user['id'],
            'created_date': datetime.utcnow().isoformat()
        }
        
        dispatch_id = dispatch_model.create_record("bird_dispatches", dispatch_data)
        flash(f'Vehicle {vehicle_no} registered for dispatch!', 'success')
        return redirect(url_for('weighing_screen', dispatch_id=dispatch_id))
    
    # Get recent dispatches for current cycle
    dispatch_records = dispatch_model.get_records("bird_dispatches", {'cycle_id': cycle['id']})
    recent_dispatches = [{'id': k, **v} for k, v in dispatch_records.items()]
    recent_dispatches.sort(key=lambda x: x.get('created_date', ''), reverse=True)
    recent_dispatches = recent_dispatches[:10]  # Limit to 10 recent
    
    return render_template('bird_dispatch.html', cycle=cycle, recent_dispatches=recent_dispatches, date=date)

@app.route('/weighing_screen/<dispatch_id>')
@login_required
def weighing_screen(dispatch_id):
    """Weighing screen for recording bird weights"""
    dispatch_data = dispatch_model.get_record("bird_dispatches", dispatch_id)
    if not dispatch_data:
        flash('Dispatch record not found.', 'error')
        return redirect(url_for('bird_dispatch'))
    
    dispatch = {'id': dispatch_id, **dispatch_data}
    cycle = get_active_cycle()
    
    if not cycle or dispatch.get('cycle_id') != cycle['id']:
        flash('Invalid dispatch or cycle mismatch.', 'error')
        return redirect(url_for('bird_dispatch'))
    
    # Get existing weighing records
    weighing_records_data = weighing_model.get_records("weighing_records", {'dispatch_id': dispatch_id})
    weighing_records = [{'id': k, **v} for k, v in weighing_records_data.items()]
    weighing_records.sort(key=lambda x: x.get('serial_no', 0))
    
    # Calculate totals
    total_birds = sum(record.get('no_of_birds', 0) for record in weighing_records)
    total_weight = sum(record.get('weight', 0) for record in weighing_records)
    avg_weight_per_bird = round(total_weight / total_birds, 3) if total_birds > 0 else 0
    
    return render_template('weighing_screen.html', 
                         dispatch=dispatch, 
                         weighing_records=weighing_records,
                         total_birds=total_birds,
                         total_weight=total_weight,
                         avg_weight_per_bird=avg_weight_per_bird)

@app.route('/add_weighing_record/<dispatch_id>', methods=['POST'])
@login_required
def add_weighing_record(dispatch_id):
    """Add a weighing record"""
    dispatch_data = dispatch_model.get_record("bird_dispatches", dispatch_id)
    if not dispatch_data:
        flash('Dispatch record not found.', 'error')
        return redirect(url_for('bird_dispatch'))
    
    no_of_birds = int(request.form.get('no_of_birds', 0))
    weight = float(request.form.get('weight', 0))
    device_timestamp = request.form.get('device_timestamp', '')
    
    if no_of_birds <= 0 or weight <= 0:
        flash('Please enter valid number of birds and weight.', 'error')
        return redirect(url_for('weighing_screen', dispatch_id=dispatch_id))
    
    # Get next serial number
    weighing_records_data = weighing_model.get_records("weighing_records", {'dispatch_id': dispatch_id})
    serial_numbers = [record.get('serial_no', 0) for record in weighing_records_data.values()]
    next_serial = max(serial_numbers, default=0) + 1
    
    # Calculate average weight per bird for this record
    avg_weight_per_bird = round(weight / no_of_birds, 3)
    
    # Use device timestamp if provided, otherwise use server time
    if device_timestamp:
        timestamp_to_use = device_timestamp
    else:
        timestamp_to_use = datetime.now().isoformat()
    
    # Create weighing record
    user = get_current_user()
    company_id = get_user_company_id()
    
    record_data = {
        'company_id': company_id,
        'dispatch_id': dispatch_id,
        'serial_no': next_serial,
        'no_of_birds': no_of_birds,
        'weight': weight,
        'avg_weight_per_bird': avg_weight_per_bird,
        'timestamp': timestamp_to_use,
        'created_by': user['id'],
        'created_date': datetime.utcnow().isoformat()
    }
    
    record_id = weighing_model.create_record("weighing_records", record_data)
    flash(f'Weighing record #{next_serial} added successfully!', 'success')
    return redirect(url_for('weighing_screen', dispatch_id=dispatch_id))

@app.route('/complete_dispatch/<dispatch_id>', methods=['POST'])
@login_required
def complete_dispatch(dispatch_id):
    """Complete the dispatch and update cycle bird count"""
    dispatch_data = dispatch_model.get_record("bird_dispatches", dispatch_id)
    if not dispatch_data:
        flash('Dispatch record not found.', 'error')
        return redirect(url_for('bird_dispatch'))
    
    dispatch = {'id': dispatch_id, **dispatch_data}
    cycle = get_active_cycle()
    
    if not cycle or dispatch.get('cycle_id') != cycle['id']:
        flash('Invalid dispatch or cycle mismatch.', 'error')
        return redirect(url_for('bird_dispatch'))
    
    # Get all weighing records
    weighing_records_data = weighing_model.get_records("weighing_records", {'dispatch_id': dispatch_id})
    weighing_records = list(weighing_records_data.values())
    
    if not weighing_records:
        flash('No weighing records found. Please add weighing records first.', 'error')
        return redirect(url_for('weighing_screen', dispatch_id=dispatch_id))
    
    # Calculate totals
    total_birds = sum(record.get('no_of_birds', 0) for record in weighing_records)
    total_weight = sum(record.get('weight', 0) for record in weighing_records)
    avg_weight_per_bird = round(total_weight / total_birds, 3) if total_birds > 0 else 0
    
    # Update dispatch record
    dispatch_model.update_record("bird_dispatches", dispatch_id, {
        'total_birds': total_birds,
        'total_weight': total_weight,
        'avg_weight_per_bird': avg_weight_per_bird,
        'status': 'completed'
    })
    
    # Update cycle bird count
    new_bird_count = max(0, cycle.get('current_birds', 0) - total_birds)
    cycle_model.update_record("cycles", cycle['id'], {
        'current_birds': new_bird_count
    })
    
    flash(f'Dispatch completed! {total_birds} birds ({total_weight:.1f} kg) sent in vehicle {dispatch.get("vehicle_no")}. Remaining birds: {new_bird_count}', 'success')
    return redirect(url_for('bird_dispatch'))

@app.route('/cycle_history')
@login_required
def cycle_history():
    """View all cycles (active and archived) for comparison"""
    user = get_current_user()
    
    # Get cycles based on user role and selected company
    if user.get('role') == 'super_admin':
        selected_company_id = session.get('selected_company_id')
        if selected_company_id:
            all_cycles = cycle_model.get_records("cycles", {'company_id': selected_company_id})
            cycles = [{'id': k, **v} for k, v in all_cycles.items()]
        else:
            cycles = []  # No cycles if no company selected
    else:
        # Regular admins see only their company's cycles
        company_id = get_user_company_id()
        if not company_id:
            flash('No company associated with your account.', 'error')
            return redirect(url_for('dashboard'))
        all_cycles = cycle_model.get_records("cycles", {'company_id': company_id})
        cycles = [{'id': k, **v} for k, v in all_cycles.items()]
    
    cycles.sort(key=lambda x: x.get('created_date', ''), reverse=True)
    
    # Calculate statistics for each cycle and format for template
    cycle_data = []
    for cycle in cycles:
        cycle_id = cycle['id']
        
        # Calculate cycle duration
        start_date = None
        if cycle.get('start_date'):
            try:
                start_date = datetime.fromisoformat(cycle['start_date']).date()
            except (ValueError, TypeError):
                start_date = None
        
        end_date = date.today()  # Default to today if no end date
        if cycle.get('end_date'):
            try:
                end_date = datetime.fromisoformat(cycle['end_date']).date()
                duration_days = (end_date - start_date).days + 1 if start_date else 0
            except (ValueError, TypeError):
                duration_days = 0
        else:
            # Ongoing cycles
            duration_days = (date.today() - start_date).days + 1 if start_date else 0
        
        # Get daily entries for this specific cycle
        daily_entries = daily_model.get_entries_by_cycle(cycle_id)
        
        # Calculate cycle statistics
        total_mortality = sum(entry.get('mortality', 0) for entry in daily_entries)
        total_feed_consumed = sum(entry.get('feed_bags_consumed', 0) for entry in daily_entries)
        
        # Calculate total feed added from Feed records for this cycle
        feed_records = feed_model.get_records("feeds", {'cycle_id': cycle_id})
        total_feed_added = sum(feed.get('feed_bags', 0) for feed in feed_records.values())
        
        final_birds = cycle.get('current_birds', 0)
        
        # Calculate total dispatched birds for this cycle
        dispatch_records = dispatch_model.get_records("bird_dispatches", {'cycle_id': cycle_id})
        completed_dispatches = [d for d in dispatch_records.values() if d.get('status') == 'completed']
        total_birds_dispatched = sum(d.get('total_birds', 0) for d in completed_dispatches)
        total_weight_dispatched = sum(d.get('total_weight', 0) for d in completed_dispatches)
        total_dispatches = len(dispatch_records)
        
        # Performance metrics - Fixed survival rate calculation: excludes dispatched birds from mortality
        total_deaths = cycle.get('start_birds', 0) - cycle.get('current_birds', 0) - total_birds_dispatched
        if cycle.get('start_birds', 0) > 0:
            survival_rate = round(((cycle.get('start_birds', 0) - total_deaths) / cycle.get('start_birds', 0)) * 100, 2)
        else:
            survival_rate = 0
        
        # Calculate FCR and other stats
        stats = calc_cumulative_stats(cycle_id)
        avg_fcr = round(sum(entry.get('fcr', 0) for entry in daily_entries if entry.get('fcr', 0) > 0) / max(1, len([entry for entry in daily_entries if entry.get('fcr', 0) > 0])), 2) if daily_entries else 0
        
        # Get final weight (latest entry's average weight)
        final_weight = 0
        if daily_entries:
            latest_entry = max(daily_entries, key=lambda x: x.get('entry_date', ''))
            final_weight = latest_entry.get('avg_weight', 0) if latest_entry.get('avg_weight', 0) > 0 else 0
        
        # Feed per bird calculation
        feed_per_bird = round(total_feed_consumed / cycle.get('start_birds', 1), 2) if cycle.get('start_birds', 0) > 0 else 0
        mortality_rate = round((total_mortality / cycle.get('start_birds', 1)) * 100, 2) if cycle.get('start_birds', 0) > 0 else 0
        
        # Create the data structure expected by the template
        cycle_data_item = {
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
            'total_weight_dispatched': total_weight_dispatched,
            'fcr': stats.get('cumulative_fcr', 0)  # Keep backward compatibility
        }
        
        cycle_data.append(cycle_data_item)
    
    return render_template('cycle_history.html', cycle_data=cycle_data)

@app.route('/dispatch_history')
@login_required
def dispatch_history():
    """View dispatch history"""
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('setup'))
    
    # Get all dispatches for current cycle
    dispatch_records = dispatch_model.get_records("bird_dispatches", {'cycle_id': cycle['id']})
    dispatches = [{'id': k, **v} for k, v in dispatch_records.items()]
    dispatches.sort(key=lambda x: x.get('dispatch_date', ''), reverse=True)
    
    # Calculate summary statistics
    completed_dispatches = [d for d in dispatches if d.get('status') == 'completed']
    total_birds_dispatched = sum(d.get('total_birds', 0) for d in completed_dispatches)
    total_weight_dispatched = sum(d.get('total_weight', 0) for d in completed_dispatches)
    avg_weight_per_bird = round(total_weight_dispatched / total_birds_dispatched, 3) if total_birds_dispatched > 0 else 0
    
    # Create summary object expected by template
    summary = {
        'total_birds_dispatched': total_birds_dispatched,
        'total_weight_dispatched': total_weight_dispatched,
        'avg_weight_per_bird': avg_weight_per_bird,
        'completed_dispatches': len(completed_dispatches)
    }
    
    return render_template('dispatch_history.html', 
                         dispatches=dispatches, 
                         cycle=cycle,
                         summary=summary)

@app.route('/cycle_details/<cycle_id>')
@login_required
def cycle_details(cycle_id):
    """View detailed information for a specific cycle"""
    # Get the specific cycle
    cycle_data = cycle_model.get_record("cycles", cycle_id)
    if not cycle_data:
        flash('Cycle not found.', 'error')
        return redirect(url_for('cycle_history'))
    
    cycle = {'id': cycle_id, **cycle_data}
    user = get_current_user()
    
    # Security check: ensure user can access this cycle
    if user.get('role') == 'super_admin':
        # Super admin can access any cycle, but should be from selected company
        selected_company_id = session.get('selected_company_id')
        if selected_company_id and cycle.get('company_id') != selected_company_id:
            flash('Access denied. This cycle belongs to a different company.', 'error')
            return redirect(url_for('cycle_history'))
    else:
        # Regular users can only access cycles from their company
        company_id = get_user_company_id()
        if cycle.get('company_id') != company_id:
            flash('Access denied. You can only view cycles from your company.', 'error')
            return redirect(url_for('cycle_history'))
    
    # Get cycles for dropdown based on user role and selected company
    if user.get('role') == 'super_admin':
        selected_company_id = session.get('selected_company_id')
        if selected_company_id:
            all_cycles_data = cycle_model.get_records("cycles", {'company_id': selected_company_id})
        else:
            all_cycles_data = {}
    else:
        company_id = get_user_company_id()
        all_cycles_data = cycle_model.get_records("cycles", {'company_id': company_id})
    
    all_cycles = [{'id': k, **v} for k, v in all_cycles_data.items()]
    all_cycles.sort(key=lambda x: x.get('created_date', ''), reverse=True)
    
    # Get basic stats for this cycle
    stats = calc_cumulative_stats(cycle_id)
    
    # Get daily entries
    daily_entries = daily_model.get_entries_by_cycle(cycle_id)
    
    # Get medicines for this cycle
    medicine_records = medicine_model.get_records("medicines", {'cycle_id': cycle_id})
    medicines = list(medicine_records.values())
    total_medical_cost = sum(float(med.get('price', 0)) * int(med.get('qty', 0)) for med in medicines)
    
    # Get expenses for this cycle
    expense_records = expense_model.get_records("expenses", {'cycle_id': cycle_id})
    expenses = list(expense_records.values())
    total_expense_cost = sum(float(e.get('amount', 0)) for e in expenses)
    
    # Get feeds for this cycle
    feed_records = feed_model.get_records("feeds", {'cycle_id': cycle_id})
    feeds = list(feed_records.values())
    total_feed_cost = sum(float(f.get('total_cost', 0)) for f in feeds)
    
    # Calculate total bags consumed
    total_bags_consumed = sum(entry.get('feed_bags_consumed', 0) for entry in daily_entries)
    
    # Calculate feed to weight ratio
    if cycle.get('current_birds', 0) > 0 and stats.get('avg_weight', 0) > 0:
        total_weight = cycle['current_birds'] * stats['avg_weight']
        feed_to_weight_ratio = round((total_bags_consumed * 50) / total_weight, 2) if total_weight > 0 else None
    else:
        feed_to_weight_ratio = None
    
    # Calculate duration
    if cycle.get('start_date'):
        try:
            start_date_obj = datetime.fromisoformat(cycle['start_date']).date()
            if cycle.get('end_date'):
                end_date_obj = datetime.fromisoformat(cycle['end_date']).date()
                duration = (end_date_obj - start_date_obj).days + 1
                current_duration = duration
            else:
                current_duration = (date.today() - start_date_obj).days + 1
                duration = None
        except (ValueError, TypeError):
            duration = None
            current_duration = None
    else:
        duration = None
        current_duration = None
    
    # Get bird dispatches for this cycle
    dispatch_records = dispatch_model.get_records("bird_dispatches", {'cycle_id': cycle_id})
    bird_dispatches = [{'id': k, **v} for k, v in dispatch_records.items()]
    bird_dispatches.sort(key=lambda x: x.get('dispatch_date', ''), reverse=True)
    
    # Calculate dispatch summary
    completed_dispatches = [d for d in bird_dispatches if d.get('status') == 'completed']
    total_birds_dispatched = sum(d.get('total_birds', 0) for d in completed_dispatches)
    total_weight_dispatched = sum(d.get('total_weight', 0) for d in completed_dispatches)
    avg_weight_per_bird = round(total_weight_dispatched / total_birds_dispatched, 3) if total_birds_dispatched > 0 else 0
    
    dispatch_summary = {
        'total_birds_dispatched': total_birds_dispatched,
        'total_weight_dispatched': total_weight_dispatched,
        'avg_weight_per_bird': avg_weight_per_bird,
        'completed_dispatches': len(completed_dispatches)
    }
    
    # Get last entry's average weight
    last_avg_weight = 0
    if daily_entries:
        latest_entry = max(daily_entries, key=lambda x: x.get('entry_date', ''))
        last_avg_weight = latest_entry.get('avg_weight', 0) if latest_entry.get('avg_weight', 0) > 0 else 0
    
    # Calculate weekly summaries for daily entries (every 7 days)
    weekly_summaries = []
    if daily_entries:
        # Sort daily entries by date
        rows = sorted(daily_entries, key=lambda x: x.get('entry_date', ''))
        for week_start in range(0, len(rows), 7):
            week_rows = rows[week_start:week_start + 7]
            if not week_rows:
                continue
            week_num = (week_start // 7) + 1
            start_day = week_start + 1
            end_day = week_start + len(week_rows)
            
            # Calculate averages and totals
            avg_mortality = round(sum(r.get('mortality', 0) for r in week_rows) / len(week_rows), 1)
            total_mortality = sum(r.get('mortality', 0) for r in week_rows)
            latest_mortality_rate = round(week_rows[-1].get('mortality_rate', 0) or 0, 2)
            latest_birds_survived = int(week_rows[-1].get('birds_survived', 0) or 0)
            avg_feed_consumed = round(sum(r.get('feed_bags_consumed', 0) for r in week_rows) / len(week_rows), 1)
            total_feed_consumed = sum(r.get('feed_bags_consumed', 0) for r in week_rows)
            
            # Calculate total feed added from Feed records for this week
            week_feed_added = 0
            for row in week_rows:
                row_date = row.get('entry_date')
                if row_date:
                    day_feed_records = feed_model.get_records("feeds", {'cycle_id': cycle_id})
                    day_feed_added = sum(
                        feed.get('feed_bags', 0) 
                        for feed in day_feed_records.values() 
                        if feed.get('date') == row_date
                    )
                    week_feed_added += day_feed_added
            
            # Average weight for week (use only positive weights)
            weight_vals = [r.get('avg_weight', 0) for r in week_rows if r.get('avg_weight', 0) and r.get('avg_weight', 0) > 0]
            avg_weight = round(sum(weight_vals) / len(weight_vals), 3) if weight_vals else 0
            
            # Latest values from 7th day (or last day of week)
            latest_fcr = round(week_rows[-1].get('fcr', 0) or 0, 3)
            latest_remaining_bags = int(week_rows[-1].get('remaining_bags', 0) or 0)
            days_in_week = len(week_rows)

            weekly_summaries.append({
                'week_num': week_num,
                'start_day': start_day,
                'end_day': end_day,
                'avg_mortality': avg_mortality,
                'total_mortality': total_mortality,
                'latest_mortality_rate': latest_mortality_rate,
                'latest_birds_survived': latest_birds_survived,
                'avg_feed_consumed': avg_feed_consumed,
                'total_feed_consumed': total_feed_consumed,
                'total_feed_added': week_feed_added,
                'avg_weight': avg_weight,
                'latest_fcr': latest_fcr,
                'latest_remaining_bags': latest_remaining_bags,
                'days_in_week': days_in_week
            })
    
    # Prepare data for charts
    fcr_series = []
    weight_series = []
    dates = []
    mortality_series = []
    
    for entry in daily_entries:
        if entry.get('entry_date'):
            dates.append(entry['entry_date'])
            fcr_series.append(entry.get('fcr', 0))
            weight_series.append(entry.get('avg_weight', 0))
            mortality_series.append(entry.get('mortality', 0))
    
    return render_template('cycle_details.html',
                         cycle=cycle,
                         all_cycles=all_cycles,
                         stats=stats,
                         daily_entries=daily_entries,
                         weekly_summaries=weekly_summaries,
                         medicines=medicines,
                         total_medical_cost=total_medical_cost,
                         expenses=expenses,
                         total_expense_cost=total_expense_cost,
                         feeds=feeds,
                         total_feed_cost=total_feed_cost,
                         total_bags_consumed=total_bags_consumed,
                         feed_to_weight_ratio=feed_to_weight_ratio,
                         duration=duration,
                         current_duration=current_duration,
                         bird_dispatches=bird_dispatches,
                         dispatch_summary=dispatch_summary,
                         fcr_series=fcr_series,
                         weight_series=weight_series,
                         mortality_series=mortality_series,
                         dates=dates,
                         last_avg_weight=last_avg_weight)

# Additional routes that are referenced in templates but missing

@app.route('/users')
@admin_required
def users():
    """User management - redirect to user_management"""
    return redirect(url_for('user_management'))

@app.route('/user_management')
@admin_required
def user_management():
    """User management page for admin and super admin"""
    user = get_current_user()
    
    if user.get('role') == 'super_admin':
        # Super admin sees all companies
        companies = company_model.get_all_companies()
        
        # If super admin has selected a specific company, show only that company's users
        selected_company_id = session.get('selected_company_id')
        if selected_company_id:
            all_users = user_model.get_records("users", {'company_id': selected_company_id})
        else:
            # If no company selected, show all users
            all_users = user_model.get_records("users")
        users = [{'id': k, **v} for k, v in all_users.items()]
    else:
        # Farm admin sees only their company's users
        company_id = get_user_company_id()
        if not company_id:
            flash('No company associated with your account.', 'error')
            return redirect(url_for('dashboard'))
        
        # Get only the admin's company
        company_data = company_model.get_record("companies", company_id)
        companies = [{'id': company_id, **company_data}] if company_data else []
        
        # Filter users by company
        all_users = user_model.get_records("users", {'company_id': company_id})
        users = [{'id': k, **v} for k, v in all_users.items()]
    
    return render_template('user_management.html', users=users, companies=companies)

@app.route('/company_management')
@super_admin_required
def company_management():
    """Company management page for super admin"""
    companies = company_model.get_all_companies()
    return render_template('company_management.html', companies=companies)

@app.route('/create_company', methods=['POST'])
@super_admin_required
def create_company():
    """Create a new company"""
    name = request.form.get('name', '').strip()
    code = request.form.get('code', '').strip()
    address = request.form.get('address', '').strip()
    phone = request.form.get('phone', '').strip()
    contact_person = request.form.get('contact_person', '').strip()
    
    if not name or not code:
        flash('Company name and code are required.', 'error')
        return redirect(url_for('company_management'))
    
    # Check if code already exists
    existing_company = company_model.get_company_by_code(code)
    if existing_company:
        flash(f'Company code "{code}" already exists.', 'error')
        return redirect(url_for('company_management'))
    
    user = get_current_user()
    company_id = company_model.create_company(
        name=name,
        code=code,
        address=address,
        phone=phone,
        contact_person=contact_person,
        created_by=user['id']
    )
    
    flash(f'Company "{name}" created successfully!', 'success')
    return redirect(url_for('company_management'))

@app.route('/create_user', methods=['POST'])
@admin_required
def create_user():
    """Create a new user - admin can create users in their company"""
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    full_name = request.form.get('full_name', '').strip()
    email = request.form.get('email', '').strip()
    phone = request.form.get('phone', '').strip()
    role = request.form.get('role', 'user')
    company_id = request.form.get('company_id', '').strip()
    
    current_user = get_current_user()
    
    # Farm admin restrictions
    if current_user.get('role') != 'super_admin':
        # Farm admin can only create users in their own company
        company_id = get_user_company_id()
        if role == 'super_admin':
            flash('You cannot create super admin users.', 'error')
            return redirect(url_for('user_management'))
    
    if not username or not password:
        flash('Username and password are required.', 'error')
        return redirect(url_for('user_management'))
    
    # Check if username already exists
    existing_user = user_model.get_user_by_username(username)
    if existing_user:
        flash(f'Username "{username}" already exists.', 'error')
        return redirect(url_for('user_management'))
    
    user_id = user_model.create_user(
        username=username,
        password=password,
        full_name=full_name,
        email=email,
        phone=phone,
        role=role,
        company_id=company_id if company_id else None,
        created_by=current_user['id']
    )
    
    flash(f'User "{username}" created successfully!', 'success')
    return redirect(url_for('user_management'))

@app.route('/import_data', methods=['GET', 'POST'])
@admin_required
def import_data():
    """Data import functionality"""
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
                        imported_medicine_count = import_medicines_data(medicines_df, cycle)
                
                # Success message
                if imported_medicine_count > 0:
                    flash(f'Import successful! {imported_daily_count} daily entries and {imported_medicine_count} medicines imported.', 'success')
                else:
                    flash(f'Import successful! {imported_daily_count} daily entries imported.', 'success')
                
            except Exception as e:
                flash(f'Import failed: {str(e)}', 'error')
        
        else:
            flash('Please upload an Excel (.xlsx, .xls) or CSV (.csv) file.', 'error')
        
        return redirect(url_for('import_data'))
    
    return render_template('import_data.html')

def import_daily_data(df, cycle):
    """Import daily data from DataFrame"""
    imported_count = 0
    user = get_current_user()
    company_id = get_user_company_id()
    
    for _, row in df.iterrows():
        try:
            # Check if entry already exists
            entry_date_str = str(row.get('Date', '') or row.get('date', ''))
            if not entry_date_str:
                continue
                
            # Check if daily entry already exists for this date
            existing_entries = daily_model.get_records("daily_entries", {
                'cycle_id': cycle['id'], 
                'entry_date': entry_date_str
            })
            
            if not existing_entries:
                # Auto-calculate derived fields for imported data
                mortality = int(row.get('Mortality', 0) or row.get('mortality', 0))
                feed_bags_consumed = float(row.get('Feed Bags Consumed', 0) or row.get('feed_bags_consumed', 0))
                avg_weight = float(row.get('Avg Weight (kg)', 0) or row.get('avg_weight', 0))
                
                # Calculate live birds after mortality
                live_after = cycle.get('current_birds', 0) - mortality
                
                # Calculate FCR if weight and feed data available
                fcr = 0
                if avg_weight > 0 and live_after > 0 and feed_bags_consumed > 0:
                    feed_kg = feed_bags_consumed * 50
                    fcr = round((feed_kg / (avg_weight * live_after)), 3)
                
                daily_entry_data = {
                    'company_id': company_id,
                    'cycle_id': cycle['id'],
                    'entry_date': entry_date_str,
                    'mortality': mortality,
                    'feed_bags_consumed': feed_bags_consumed,
                    'avg_weight': avg_weight,
                    'avg_feed_per_bird_g': float(row.get('Avg Feed per Bird (g)', 0) or 0),
                    'fcr': fcr,
                    'medicines': str(row.get('Medicines', '') or row.get('medicines', '')),
                    'daily_notes': str(row.get('Notes', '') or row.get('notes', '')),
                    'birds_survived': live_after,
                    'created_by': user['id'],
                    'created_date': datetime.utcnow().isoformat()
                }
                
                daily_model.create_record("daily_entries", daily_entry_data)
                imported_count += 1
        except Exception as e:
            continue
    
    return imported_count

def import_medicines_data(df, cycle):
    """Import medicines data from DataFrame"""
    imported_count = 0
    user = get_current_user()
    company_id = get_user_company_id()
    
    for _, row in df.iterrows():
        try:
            # Skip the TOTAL row
            medicine_name = str(row.get('Medicine Name', '') or row.get('name', '')).strip()
            if medicine_name.upper() == 'TOTAL' or not medicine_name:
                continue
            
            # Check if medicine already exists for this cycle
            existing_meds = medicine_model.get_records("medicines", {
                'cycle_id': cycle['id'],
                'name': medicine_name
            })
            
            if not existing_meds:
                price = float(row.get('Price', 0) or row.get('price', 0))
                quantity = int(row.get('Quantity', 0) or row.get('qty', 0)) if pd.notna(row.get('Quantity', 0) or row.get('qty', 0)) else 0
                
                medicine_data = {
                    'company_id': company_id,
                    'cycle_id': cycle['id'],
                    'name': medicine_name,
                    'price': price,
                    'qty': quantity,
                    'created_by': user['id'],
                    'created_date': datetime.utcnow().isoformat()
                }
                
                medicine_model.create_record("medicines", medicine_data)
                imported_count += 1
                
        except Exception as e:
            continue
    
    return imported_count

@app.route('/export')
@login_required
def export():
    """Export current cycle data to PDF (matching original app.py format)"""
    cycle = get_active_cycle()
    if not cycle:
        flash('No active cycle to export.', 'error')
        return redirect(url_for('setup'))
    
    try:
        # Generate comprehensive PDF report
        pdf_buffer = create_comprehensive_pdf_report(cycle, "Complete Farm Data Report")
        
        # Generate filename with current date
        filename = f"complete_farm_data_{cycle['id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        
        return send_file(pdf_buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')
        
    except Exception as e:
        flash(f'Error exporting data: {str(e)}', 'error')
        return redirect(url_for('dashboard'))

@app.route('/income_estimate', methods=['GET', 'POST'])
@login_required
def income_estimate():
    """Income estimation page"""
    # Handle cycle selection from both GET and POST
    selected_cycle_id = request.args.get('cycle_id') or request.form.get('cycle_id', 'current')
    
    cycle = get_active_cycle()
    if not cycle:
        return redirect(url_for('setup'))
    
    # Calculate cycle statistics needed for income estimation
    cycle_stats = calc_cumulative_stats(cycle['id'])
    
    # Add total_birds to cycle_stats (needed by template)
    cycle_stats['total_birds'] = cycle.get('current_birds', 0)
    cycle_stats['fcr'] = cycle_stats.get('cumulative_fcr', 0)
    
    # Default values for the income estimation
    chick_price = 22  # Default chick cost per bird
    feed_cost = 45  # Default feed cost per kg
    bag_weight = 50  # Default bag weight in kg
    other_expenses = 18000  # Default other expenses
    market_price_per_bird = 130  # Default market price per kg
    custom_fcr = None  # User can override FCR
    medicine_cost = 18000  # Default medicine cost
    vaccine_cost = 1800  # Default vaccine cost
    base_pc_rate = 95  # Default PC rate
    base_income_rate = 6.5  # Default income rate
    using_defaults = True
    
    # If POST request, get values from form for calculation
    if request.method == 'POST':
        chick_price = float(request.form.get('chick_cost', chick_price))
        feed_cost = float(request.form.get('feed_cost', feed_cost))
        other_expenses = float(request.form.get('other_expenses', other_expenses))
        market_price_per_bird = float(request.form.get('market_price_per_bird', market_price_per_bird))
        custom_fcr = float(request.form.get('custom_fcr')) if request.form.get('custom_fcr') else None
        medicine_cost = float(request.form.get('medicine_cost', medicine_cost))
        vaccine_cost = float(request.form.get('vaccine_cost', vaccine_cost))
        base_pc_rate = float(request.form.get('base_pc_rate', base_pc_rate))
        base_income_rate = float(request.form.get('base_income_rate', base_income_rate))
        using_defaults = False
    
    # Template compatibility
    chick_cost = chick_price
    feed_per_kg_price = feed_cost
    
    # Calculate derived values
    default_birds_count = cycle_stats['total_birds'] if cycle_stats['total_birds'] > 0 else cycle.get('start_birds', 0)
    total_weight = (cycle_stats['total_birds'] * cycle_stats['avg_weight']) if (cycle_stats['total_birds'] and cycle_stats['avg_weight']) else 0
    
    # Calculate FCR to use (custom or system)
    fcr_to_use = custom_fcr if custom_fcr else cycle_stats['fcr'] if cycle_stats['fcr'] else 1.53
    
    # Calculate feed needed in kg
    feed_needed_kg = (total_weight * fcr_to_use) if total_weight > 0 else 0
    
    # Calculate total feed cost
    total_feed_cost = feed_needed_kg * feed_cost
    
    # Calculate chick cost
    calculated_chick_cost_for_display = default_birds_count * chick_price
    chick_production_cost = calculated_chick_cost_for_display
    
    # Get actual medical and expense costs from database
    medicine_records = medicine_model.get_records("medicines", {'cycle_id': cycle['id']})
    total_medical_cost = sum(float(med.get('price', 0)) * int(med.get('qty', 0)) for med in medicine_records.values())
    
    expense_records = expense_model.get_records("expenses", {'cycle_id': cycle['id']})
    total_expense_cost = sum(float(exp.get('amount', 0)) for exp in expense_records.values())
    
    # Calculate total cycle cost
    total_cycle_cost = total_feed_cost + calculated_chick_cost_for_display + total_medical_cost + total_expense_cost + other_expenses
    
    # Calculate estimated income (weight-based)
    estimated_income = total_weight * market_price_per_bird
    
    # Calculate PC (Production Cost) values - use actual costs where available
    production_cost = calculated_chick_cost_for_display + total_feed_cost + total_medical_cost + vaccine_cost
    pc_per_kg = production_cost / total_weight if total_weight > 0 else 0
    income_rate_per_kg = (base_income_rate - (pc_per_kg - base_pc_rate) * 0.5) if pc_per_kg > 0 else 0
    pc_based_income = total_weight * income_rate_per_kg if total_weight > 0 else 0
    
    # Calculate profits
    estimated_profit = estimated_income - total_cycle_cost
    pc_based_profit = pc_based_income - production_cost
    
    # Additional template variables for compatibility
    direct_feed_cost = total_feed_cost
    fallback_feed_cost = total_feed_cost
    cumu_stats = cycle_stats  # For template compatibility
    
    # Get all cycles for the user's company
    company_id = get_user_company_id()
    all_cycles_data = cycle_model.get_records("cycles", {'company_id': company_id}) if company_id else {}
    
    # Filter for current/active cycles only
    all_cycles = []
    for cycle_id, cycle_data in all_cycles_data.items():
        if cycle_data.get('status') == 'active':  # Only show active cycles
            cycle_data['id'] = cycle_id
            all_cycles.append(cycle_data)
    
    # Sort cycles by created_date (newest first)
    all_cycles.sort(key=lambda x: x.get('created_date', ''), reverse=True)
    
    # If a specific cycle is selected (not current), use that cycle for calculations
    if selected_cycle_id != 'current' and selected_cycle_id != 'all':
        selected_cycle = None
        for c in all_cycles:
            if c['id'] == selected_cycle_id:
                selected_cycle = c
                break
        if selected_cycle:
            # Recalculate stats for the selected cycle
            cycle_stats = calc_cumulative_stats(selected_cycle['id'])
            cycle_stats['total_birds'] = selected_cycle.get('current_birds', 0)
            cycle_stats['fcr'] = cycle_stats.get('cumulative_fcr', 0)
            
            # Update default_birds_count for selected cycle
            default_birds_count = cycle_stats['total_birds'] if cycle_stats['total_birds'] > 0 else selected_cycle.get('start_birds', 0)
            
            # Recalculate other values based on selected cycle
            total_weight = (cycle_stats['total_birds'] * cycle_stats['avg_weight']) if (cycle_stats['total_birds'] and cycle_stats['avg_weight']) else 0
            feed_needed_kg = (total_weight * fcr_to_use) if total_weight > 0 else 0
            total_feed_cost = feed_needed_kg * feed_cost
            calculated_chick_cost_for_display = default_birds_count * chick_price
            chick_production_cost = calculated_chick_cost_for_display
            
            # Get medical and expense costs for selected cycle
            medicine_records = medicine_model.get_records("medicines", {'cycle_id': selected_cycle['id']})
            total_medical_cost = sum(float(med.get('price', 0)) * int(med.get('qty', 0)) for med in medicine_records.values())
            
            expense_records = expense_model.get_records("expenses", {'cycle_id': selected_cycle['id']})
            total_expense_cost = sum(float(exp.get('amount', 0)) for exp in expense_records.values())
            
            # Recalculate totals
            total_cycle_cost = total_feed_cost + calculated_chick_cost_for_display + total_medical_cost + total_expense_cost + other_expenses
            estimated_income = total_weight * market_price_per_bird
            
            # Production cost calculation - use actual costs for selected cycle
            production_cost = calculated_chick_cost_for_display + total_feed_cost + total_medical_cost + vaccine_cost
            pc_per_kg = production_cost / total_weight if total_weight > 0 else 0
            income_rate_per_kg = (base_income_rate - (pc_per_kg - base_pc_rate) * 0.5) if pc_per_kg > 0 else 0
            pc_based_income = total_weight * income_rate_per_kg if total_weight > 0 else 0
            estimated_profit = estimated_income - total_cycle_cost
            pc_based_profit = pc_based_income - production_cost
            
            # Update compatibility variables
            direct_feed_cost = total_feed_cost
            fallback_feed_cost = total_feed_cost
    
    # selected_cycle_id is already set above based on form submission
    selected_cycle_expenses = []
    all_expenses = []
    estimated_income_all_cycles = estimated_income
    estimated_profit_all_cycles = estimated_profit
    all_cycles_total_cost = total_cycle_cost
    total_birds = cycle_stats['total_birds']
    
    # Prepare template variables - matching app.py structure
    template_vars = {
        'cycle': cycle,
        'cycle_stats': cycle_stats,
        'cumu_stats': cumu_stats,
        'chick_cost': chick_cost,
        'chick_price': chick_price,
        'feed_cost': feed_cost,
        'feed_per_kg_price': feed_per_kg_price,
        'bag_weight': bag_weight,
        'other_expenses': other_expenses,
        'market_price_per_bird': market_price_per_bird,
        'custom_fcr': custom_fcr,
        'medicine_cost': medicine_cost,
        'vaccine_cost': vaccine_cost,
        'base_pc_rate': base_pc_rate,
        'base_income_rate': base_income_rate,
        'using_defaults': using_defaults,
        'default_birds_count': default_birds_count,
        'total_birds': total_birds,
        'total_feed_cost': total_feed_cost,
        'total_medical_cost': total_medical_cost,
        'total_expense_cost': total_expense_cost,
        'calculated_chick_cost_for_display': calculated_chick_cost_for_display,
        'chick_production_cost': chick_production_cost,
        'total_cycle_cost': total_cycle_cost,
        'estimated_income': estimated_income,
        'estimated_profit': estimated_profit,
        'production_cost': production_cost,
        'pc_per_kg': pc_per_kg,
        'pc_based_income': pc_based_income,
        'pc_based_profit': pc_based_profit,
        'direct_feed_cost': direct_feed_cost,
        'fallback_feed_cost': fallback_feed_cost,
        'all_cycles': all_cycles,
        'selected_cycle_id': selected_cycle_id,
        'selected_cycle_expenses': selected_cycle_expenses,
        'all_expenses': all_expenses,
        'estimated_income_all_cycles': estimated_income_all_cycles,
        'estimated_profit_all_cycles': estimated_profit_all_cycles,
        'all_cycles_total_cost': all_cycles_total_cost,
    }
    
    return render_template('income_estimate.html', **template_vars)

@app.route('/export_income_estimate')
@admin_required
def export_income_estimate():
    """Export income estimate data to PDF"""
    cycle = get_active_cycle()
    if not cycle:
        flash('No active cycle to export.', 'error')
        return redirect(url_for('setup'))
    
    try:
        # Calculate income estimate data (same logic as income_estimate route)
        cycle_stats = calc_cumulative_stats(cycle['id'])
        cycle_stats['total_birds'] = cycle.get('current_birds', 0)
        cycle_stats['fcr'] = cycle_stats.get('cumulative_fcr', 0)
        
        # Default values
        chick_price = 22
        feed_cost = 45
        other_expenses = 18000
        market_price_per_bird = 130
        custom_fcr = None
        medicine_cost = 18000
        vaccine_cost = 1800
        base_pc_rate = 95
        base_income_rate = 6.5
        
        # Calculate derived values
        default_birds_count = cycle_stats['total_birds'] if cycle_stats['total_birds'] > 0 else cycle.get('start_birds', 0)
        total_weight = (cycle_stats['total_birds'] * cycle_stats['avg_weight']) if (cycle_stats['total_birds'] and cycle_stats['avg_weight']) else 0
        fcr_to_use = custom_fcr if custom_fcr else cycle_stats['fcr'] if cycle_stats['fcr'] else 1.53
        feed_needed_kg = (total_weight * fcr_to_use) if total_weight > 0 else 0
        total_feed_cost = feed_needed_kg * feed_cost
        calculated_chick_cost_for_display = default_birds_count * chick_price
        
        # Get actual costs from database
        medicine_records = medicine_model.get_records("medicines", {'cycle_id': cycle['id']})
        total_medical_cost = sum(float(med.get('price', 0)) * int(med.get('qty', 0)) for med in medicine_records.values())
        
        expense_records = expense_model.get_records("expenses", {'cycle_id': cycle['id']})
        total_expense_cost = sum(float(exp.get('amount', 0)) for exp in expense_records.values())
        
        # Calculate totals
        total_cycle_cost = total_feed_cost + calculated_chick_cost_for_display + total_medical_cost + total_expense_cost + other_expenses
        estimated_income = total_weight * market_price_per_bird
        
        # Production cost calculation - use actual costs where available, defaults for missing components
        production_cost = calculated_chick_cost_for_display + total_feed_cost + total_medical_cost + vaccine_cost
        pc_per_kg = production_cost / total_weight if total_weight > 0 else 0
        income_rate_per_kg = (base_income_rate - (pc_per_kg - base_pc_rate) * 0.5) if pc_per_kg > 0 else 0
        pc_based_income = total_weight * income_rate_per_kg if total_weight > 0 else 0
        estimated_profit = estimated_income - total_cycle_cost
        pc_based_profit = pc_based_income - production_cost
        
        # Prepare income data for PDF
        income_data = {
            'chick_price': chick_price,
            'feed_cost': feed_cost,
            'other_expenses': other_expenses,
            'market_price_per_bird': market_price_per_bird,
            'custom_fcr': custom_fcr,
            'medicine_cost': medicine_cost,
            'vaccine_cost': vaccine_cost,
            'base_pc_rate': base_pc_rate,
            'base_income_rate': base_income_rate,
            'income_rate_per_kg': income_rate_per_kg,
            'total_birds': cycle_stats['total_birds'],
            'avg_weight_per_bird': cycle_stats.get('avg_weight', 0),
            'total_weight': total_weight,
            'fcr_to_use': fcr_to_use,
            'feed_needed_kg': feed_needed_kg,
            'calculated_chick_cost_for_display': calculated_chick_cost_for_display,
            'total_feed_cost': total_feed_cost,
            'total_medical_cost': total_medical_cost,
            'total_expense_cost': total_expense_cost,
            'total_cycle_cost': total_cycle_cost,
            'estimated_income': estimated_income,
            'production_cost': production_cost,
            'pc_per_kg': pc_per_kg,
            'pc_based_income': pc_based_income,
            'estimated_profit': estimated_profit,
            'pc_based_profit': pc_based_profit
        }
        
        # Generate PDF report for income estimate only
        pdf_buffer = create_income_estimate_pdf_report(cycle, income_data, "Income Estimate Report")
        
        company_id = get_user_company_id()
        company_data = company_model.get_record("companies", company_id) if company_id else {}
        company_name = company_data.get('name', 'Unknown') if company_data else 'Unknown'
        
        filename = f"Income_Estimate_Cycle_{cycle.get('cycle_ext1', cycle['id'])}_{company_name}_{date.today().strftime('%Y%m%d')}.pdf"
        
        return send_file(pdf_buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')
        
    except Exception as e:
        print(f"Error generating income estimate PDF: {e}")
        flash(f'Error exporting income estimate: {str(e)}', 'error')
        return redirect(url_for('income_estimate'))

@app.route('/edit_user/<user_id>')
@admin_required
def edit_user(user_id):
    """Edit user page - admin can edit users in their company"""
    user_data = user_model.get_record("users", user_id)
    if not user_data:
        flash('User not found.', 'error')
        return redirect(url_for('user_management'))
    
    current_user = get_current_user()
    
    # Security check: ensure admin can only edit users from their company
    if current_user.get('role') == 'super_admin':
        # Super admin can edit any user
        companies = company_model.get_all_companies()
    else:
        # Farm admin can only edit users from their own company
        company_id = get_user_company_id()
        if user_data.get('company_id') != company_id:
            flash('Access denied. You can only edit users from your company.', 'error')
            return redirect(url_for('user_management'))
        
        # Get only the admin's company for the dropdown
        company_data = company_model.get_record("companies", company_id)
        companies = [{'id': company_id, **company_data}] if company_data else []
    
    user = {'id': user_id, **user_data}
    return render_template('edit_user.html', user=user, companies=companies)

# Placeholder routes for missing functionality
@app.route('/delete_user/<user_id>')
@admin_required
def delete_user(user_id):
    """Delete user (admin can delete users in their company)"""
    try:
        user_data = user_model.get_record("users", user_id)
        if not user_data:
            flash('User not found.', 'error')
            return redirect(url_for('user_management'))
        
        current_user = get_current_user()
        
        # Security check: ensure admin can only delete users from their company
        if current_user.get('role') != 'super_admin':
            company_id = get_user_company_id()
            if user_data.get('company_id') != company_id:
                flash('Access denied. You can only delete users from your company.', 'error')
                return redirect(url_for('user_management'))
        
        # Prevent admin from deleting themselves
        if user_id == current_user['id']:
            flash('You cannot delete your own account.', 'error')
            return redirect(url_for('user_management'))
            
        # Prevent single admin from being deleted
        if current_user.get('role') != 'super_admin':
            company_id = get_user_company_id()
            all_users = user_model.get_records("users", {'company_id': company_id})
            active_admins = [u for u in all_users.values() if u.get('role') in ['admin', 'super_admin'] and u.get('status') == 'active']
            
            if len(active_admins) <= 1 and user_data.get('role') in ['admin', 'super_admin']:
                flash('Cannot delete the only admin in the company. At least one admin must remain active.', 'error')
                return redirect(url_for('user_management'))
        
        username = user_data.get('username', 'Unknown')
        # Actually delete the user record
        user_model.delete_record("users", user_id)
        flash(f'User "{username}" has been deleted successfully.', 'success')
    except Exception as e:
        flash(f'Error deleting user: {str(e)}', 'error')
    
    return redirect(url_for('user_management'))

@app.route('/update_user_status/<user_id>/<status>')
@admin_required
def update_user_status(user_id, status):
    """Update user status (active/inactive) - admin can update users in their company"""
    if status not in ['active', 'inactive']:
        flash('Invalid status. Must be active or inactive.', 'error')
        return redirect(url_for('user_management'))
    
    try:
        user_data = user_model.get_record("users", user_id)
        if not user_data:
            flash('User not found.', 'error')
            return redirect(url_for('user_management'))
        
        current_user = get_current_user()
        
        # Security check: ensure admin can only update users from their company
        if current_user.get('role') != 'super_admin':
            company_id = get_user_company_id()
            if user_data.get('company_id') != company_id:
                flash('Access denied. You can only update users from your company.', 'error')
                return redirect(url_for('user_management'))
        # Prevent admin from deactivating themselves
        if user_id == current_user['id'] and status == 'inactive':
            flash('You cannot deactivate your own account.', 'error')
            return redirect(url_for('user_management'))
            
        # Prevent single admin from being deactivated
        if current_user.get('role') != 'super_admin' and status == 'inactive':
            company_id = get_user_company_id()
            # Check if this is the only active admin in the company
            all_users = user_model.get_records("users", {'company_id': company_id})
            active_admins = [u for u in all_users.values() if u.get('role') in ['admin', 'super_admin'] and u.get('status') == 'active']
            
            if len(active_admins) <= 1:
                flash('Cannot deactivate the only admin in the company. At least one admin must remain active.', 'error')
                return redirect(url_for('user_management'))
        
        username = user_data.get('username', 'Unknown')
        user_model.update_record("users", user_id, {
            'status': status,
            'modified_by': current_user['id'],
            'modified_date': datetime.utcnow().isoformat()
        })
        flash(f'User "{username}" status updated to {status}.', 'success')
    except Exception as e:
        flash(f'Error updating user status: {str(e)}', 'error')
    
    return redirect(url_for('user_management'))

@app.route('/edit_company/<company_id>', methods=['GET', 'POST'])
@super_admin_required
def edit_company(company_id):
    """Edit company"""
    company_data = company_model.get_record("companies", company_id)
    if not company_data:
        flash('Company not found.', 'error')
        return redirect(url_for('company_management'))
    
    company = {'id': company_id, **company_data}
    
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        code = request.form.get('code', '').strip()
        address = request.form.get('address', '').strip()
        phone = request.form.get('phone', '').strip()
        contact_person = request.form.get('contact_person', '').strip()
        
        if not name or not code:
            flash('Company name and code are required.', 'error')
            return render_template('edit_company.html', company=company)
        
        # Check if code already exists (excluding current company)
        all_companies = company_model.get_all_companies()
        for comp_id, comp_data in all_companies.items():
            if comp_id != company_id and comp_data.get('code') == code:
                flash(f'Company code "{code}" already exists.', 'error')
                return render_template('edit_company.html', company=company)
        
        try:
            user = get_current_user()
            company_model.update_record("companies", company_id, {
                'name': name,
                'code': code,
                'address': address,
                'phone': phone,
                'contact_person': contact_person,
                'modified_by': user['id'],
                'modified_date': datetime.utcnow().isoformat()
            })
            flash(f'Company "{name}" updated successfully!', 'success')
            return redirect(url_for('company_management'))
        except Exception as e:
            flash(f'Error updating company: {str(e)}', 'error')
    
    return render_template('edit_company.html', company=company)

@app.route('/delete_company/<company_id>', methods=['POST'])
@super_admin_required
def delete_company(company_id):
    """Delete/deactivate company (super admin only)"""
    try:
        company_data = company_model.get_record("companies", company_id)
        if company_data:
            company_name = company_data.get('name', 'Unknown')
            
            # Check if company has active users
            all_users = user_model.get_all_users()
            active_users = [user for user in all_users if user.get('company_id') == company_id and user.get('status') == 'active']
            
            if active_users:
                flash(f'Cannot delete company "{company_name}" as it has {len(active_users)} active users. Please deactivate users first.', 'error')
                return redirect(url_for('company_management'))
            
            # Deactivate company instead of deleting to preserve data integrity
            user = get_current_user()
            company_model.update_record("companies", company_id, {
                'status': 'inactive',
                'modified_by': user['id'],
                'modified_date': datetime.utcnow().isoformat()
            })
            flash(f'Company "{company_name}" has been deactivated successfully.', 'success')
        else:
            flash('Company not found.', 'error')
    except Exception as e:
        flash(f'Error deactivating company: {str(e)}', 'error')
    
    return redirect(url_for('company_management'))

@app.route('/export_cycle_details/<cycle_id>')
@login_required
def export_cycle_details(cycle_id):
    """Export complete cycle details as PDF (matching original app.py format)"""
    # Get the specific cycle
    cycle_data = cycle_model.get_record("cycles", cycle_id)
    if not cycle_data:
        flash('Cycle not found.', 'error')
        return redirect(url_for('cycle_history'))
    
    cycle = {'id': cycle_id, **cycle_data}
    
    # Check if user has access to this cycle
    company_id = get_user_company_id()
    if cycle.get('company_id') != company_id:
        flash('Access denied to this cycle.', 'error')
        return redirect(url_for('cycle_history'))
    
    try:
        # Generate PDF report using the comprehensive template format
        pdf_buffer = create_comprehensive_pdf_report(cycle, f"Complete Cycle {cycle_id} Details Report")
        
        # Generate filename with current date
        filename = f"cycle_{cycle_id}_complete_details_{datetime.now().strftime('%Y%m%d')}.pdf"
        
        return send_file(pdf_buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')
        
    except Exception as e:
        flash(f'Error generating PDF report: {str(e)}', 'error')
        return redirect(url_for('cycle_history'))

@app.route('/export_cycle/<cycle_id>')
@login_required
def export_cycle(cycle_id):
    """Export specific cycle data to Excel"""
    # Get the specific cycle
    cycle_data = cycle_model.get_record("cycles", cycle_id)
    if not cycle_data:
        flash('Cycle not found.', 'error')
        return redirect(url_for('cycle_history'))
    
    cycle = {'id': cycle_id, **cycle_data}
    
    # Check if user has access to this cycle
    company_id = get_user_company_id()
    if cycle.get('company_id') != company_id:
        flash('Access denied to this cycle.', 'error')
        return redirect(url_for('cycle_history'))
    
    try:
        # Create Excel file in memory
        output = BytesIO()
        
        # Get daily entries for this cycle
        daily_entries = daily_model.get_entries_by_cycle(cycle_id)
        
        # Create Excel writer
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            # Cycle Summary Sheet
            cycle_summary = [{
                'Cycle Number': cycle.get('cycle_ext1', cycle_id),
                'Start Date': cycle.get('start_date', ''),
                'End Date': cycle.get('end_date', ''),
                'Start Birds': cycle.get('start_birds', 0),
                'Current Birds': cycle.get('current_birds', 0),
                'Hatchery': cycle.get('hatchery', ''),
                'Farmer Name': cycle.get('farmer_name', ''),
                'Status': cycle.get('status', ''),
                'Notes': cycle.get('notes', '')
            }]
            
            cycle_df = pd.DataFrame(cycle_summary)
            cycle_df.to_excel(writer, sheet_name='Cycle Summary', index=False)
            
            # Daily Data Sheet
            daily_data = []
            for entry in daily_entries:
                daily_data.append({
                    'Date': entry.get('entry_date', ''),
                    'Mortality': entry.get('mortality', 0),
                    'Feed Bags Consumed': entry.get('feed_bags_consumed', 0),
                    'Avg Weight (kg)': entry.get('avg_weight', 0),
                    'FCR': entry.get('fcr', 0),
                    'Birds Survived': entry.get('birds_survived', 0),
                    'Medicines': entry.get('medicines', ''),
                    'Notes': entry.get('daily_notes', '')
                })
            
            if daily_data:
                daily_df = pd.DataFrame(daily_data)
                daily_df.to_excel(writer, sheet_name='Daily Data', index=False)
            
            # Medicines Sheet
            all_medicines = medicine_model.get_records("medicines")
            medicines_data = []
            for med_id, med_data in all_medicines.items():
                if med_data.get('cycle_id') == cycle_id:
                    medicines_data.append({
                        'Medicine Name': med_data.get('name', ''),
                        'Price': med_data.get('price', 0),
                        'Quantity': med_data.get('qty', 0),
                        'Notes': med_data.get('medicine_ext1', ''),
                        'Total Cost': med_data.get('price', 0) * med_data.get('qty', 0)
                    })
            
            if medicines_data:
                medicines_df = pd.DataFrame(medicines_data)
                medicines_df.to_excel(writer, sheet_name='Medicines', index=False)
            
            # Feed Records Sheet
            all_feeds = feed_model.get_records("feeds")
            feed_data = []
            for feed_id, feed in all_feeds.items():
                if feed.get('cycle_id') == cycle_id:
                    feed_data.append({
                        'Date': feed.get('date', ''),
                        'Bill Number': feed.get('bill_number', ''),
                        'Feed Name': feed.get('feed_name', ''),
                        'Feed Bags': feed.get('feed_bags', 0),
                        'Bag Weight (kg)': feed.get('bag_weight', 50),
                        'Total Weight (kg)': feed.get('total_feed_kg', 0),
                        'Price per kg': feed.get('price_per_kg', 0),
                        'Total Cost': feed.get('total_cost', 0)
                    })
            
            if feed_data:
                feed_df = pd.DataFrame(feed_data)
                feed_df.to_excel(writer, sheet_name='Feed Records', index=False)
            
            # Expenses Sheet
            all_expenses = expense_model.get_records("expenses")
            expenses_data = []
            for exp_id, expense in all_expenses.items():
                if expense.get('cycle_id') == cycle_id:
                    expenses_data.append({
                        'Date': expense.get('date', ''),
                        'Expense Name': expense.get('name', ''),
                        'Amount': expense.get('amount', 0),
                        'Notes': expense.get('notes', '')
                    })
            
            if expenses_data:
                expenses_df = pd.DataFrame(expenses_data)
                expenses_df.to_excel(writer, sheet_name='Expenses', index=False)
        
        output.seek(0)
        
        # Generate filename
        company_data = company_model.get_record("companies", company_id) if company_id else {}
        company_name = company_data.get('name', 'Unknown') if company_data else 'Unknown'
        cycle_number = cycle.get('cycle_ext1', cycle_id)
        filename = f"Cycle_{cycle_number}_Complete_{company_name}_{datetime.now().strftime('%Y%m%d')}.xlsx"
        
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        
    except Exception as e:
        flash(f'Error exporting cycle data: {str(e)}', 'error')
        return redirect(url_for('cycle_history'))

# Tips and Educational Content Routes
@app.route('/bedding_tips')
def bedding_tips():
    """Bedding tips page"""
    return render_template('tips_bedding.html')

@app.route('/herbal_treatment_tips')
def herbal_treatment_tips():
    """Herbal treatment tips page"""
    return render_template('tips_herbal.html')

@app.route('/growth_tips')
def growth_tips():
    """Growth tips page"""
    return render_template('tips_growth.html')

@app.route('/tips_medical')
def tips_medical():
    """Medical tips page"""
    return render_template('tips_medical.html')

@app.route('/tips_own_feed')
def tips_own_feed():
    """Own feed tips page"""
    return render_template('tip_own_feed.html')

@app.route('/tips_feeding')
def tips_feeding():
    """Feeding tips page"""
    return render_template('tips_feeding.html')

@app.route('/edit_daily/<entry_id>', methods=['GET', 'POST'])
@admin_required
def edit_daily(entry_id):
    """Edit daily entry"""
    # Get the daily entry
    daily_entry_data = daily_model.get_record("daily_entries", entry_id)
    if not daily_entry_data:
        flash('Daily entry not found.', 'error')
        return redirect(url_for('daywise'))
    
    daily_entry = {'id': entry_id, **daily_entry_data}
    
    # Get current cycle
    cycle = get_active_cycle()
    if not cycle:
        flash('No active cycle found.', 'error')
        return redirect(url_for('setup'))
    
    # Check if entry belongs to current cycle
    if daily_entry.get('cycle_id') != cycle['id']:
        flash('Cannot edit entry from a different cycle.', 'error')
        return redirect(url_for('daywise'))
    
    if request.method == 'POST':
        # Get form data
        entry_date = request.form.get('entry_date') or daily_entry.get('entry_date')
        mortality = int(request.form.get('mortality', 0))
        feed_bags_consumed = int(request.form.get('feed_bags_consumed', 0))
        avg_weight_grams = float(request.form.get('avg_weight_grams', 0) or 0)
        avg_weight = round(avg_weight_grams / 1000, 3) if avg_weight_grams > 0 else 0
        medicines = request.form.get('medicines', '')
        daily_notes = request.form.get('daily_notes', '').strip()
        
        # Update the daily entry
        user = get_current_user()
        update_data = {
            'entry_date': entry_date,
            'mortality': mortality,
            'feed_bags_consumed': feed_bags_consumed,
            'avg_weight': avg_weight,
            'medicines': medicines,
            'daily_notes': daily_notes,
            'modified_by': user['id'],
            'modified_date': datetime.utcnow().isoformat()
        }
        
        success = daily_model.update_record("daily_entries", entry_id, update_data)
        if success:
            flash('Daily entry updated successfully!', 'success')
        else:
            flash('Error updating daily entry.', 'error')
        
        return redirect(url_for('daywise'))
    
    # Get medicines for current cycle for dropdown
    medicine_records = medicine_model.get_records("medicines", {'cycle_id': cycle['id']})
    medicines = [{'id': k, **v} for k, v in medicine_records.items()]
    
    return render_template('edit_daily.html', 
                         entry=daily_entry, 
                         cycle=cycle, 
                         meds=medicines)

@app.route('/delete_daily/<entry_id>', methods=['POST'])
@admin_required
def delete_daily(entry_id):
    """Delete a daily entry (admin only)"""
    try:
        daily_entry_data = daily_model.get_record("daily_entries", entry_id)
        if not daily_entry_data:
            flash('Daily entry not found.', 'error')
            return redirect(url_for('daywise'))
        
        # Check if entry belongs to current cycle
        cycle = get_active_cycle()
        if not cycle or daily_entry_data.get('cycle_id') != cycle['id']:
            flash('Cannot delete entry from a different cycle.', 'error')
            return redirect(url_for('daywise'))
        
        entry_date = daily_entry_data.get('entry_date', 'Unknown')
        mortality = daily_entry_data.get('mortality', 0)
        
        # Delete the daily entry
        success = daily_model.delete_record("daily_entries", entry_id)
        if success:
            flash(f'Daily entry for {entry_date} deleted successfully!', 'success')
            
            # Update cycle bird count by adding back the mortality
            if mortality > 0:
                new_bird_count = cycle.get('current_birds', 0) + mortality
                cycle_model.update_record("cycles", cycle['id'], {
                    'current_birds': new_bird_count
                })
        else:
            flash('Error deleting daily entry.', 'error')
            
    except Exception as e:
        flash(f'Error deleting daily entry: {str(e)}', 'error')
    
    return redirect(url_for('daywise'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """User profile page"""
    user = get_current_user()
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        # Handle profile update
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        
        # Update user profile
        update_data = {
            'full_name': full_name,
            'email': email,
            'phone': phone,
            'modified_by': user['id'],
            'modified_date': datetime.utcnow().isoformat()
        }
        
        success = user_model.update_record("users", user['id'], update_data)
        if success:
            flash('Profile updated successfully!', 'success')
        else:
            flash('Error updating profile.', 'error')
        
        return redirect(url_for('profile'))
    
    # Get user's company information
    user_company = None
    if user.get('company_id'):
        company_data = company_model.get_record("companies", user['company_id'])
        if company_data:
            user_company = {'id': user['company_id'], **company_data}
    
    return render_template('profile.html', user=user, company=user_company)

@app.route('/edit_dispatch/<dispatch_id>', methods=['GET', 'POST'])
@admin_required
def edit_dispatch(dispatch_id):
    """Edit dispatch record (admin only)"""
    dispatch_data = dispatch_model.get_record("bird_dispatches", dispatch_id)
    if not dispatch_data:
        flash('Dispatch record not found.', 'error')
        return redirect(url_for('bird_dispatch'))
    
    dispatch = {'id': dispatch_id, **dispatch_data}
    cycle = get_active_cycle()
    
    if not cycle or dispatch.get('cycle_id') != cycle['id']:
        flash('Cannot edit dispatch from a different cycle.', 'error')
        return redirect(url_for('bird_dispatch'))
    
    if request.method == 'POST':
        vehicle_no = request.form.get('vehicle_no', '').strip()
        driver_name = request.form.get('driver_name', '').strip()
        vendor_name = request.form.get('vendor_name', '').strip()
        dispatch_date = request.form.get('dispatch_date') or dispatch.get('dispatch_date')
        dispatch_time = request.form.get('dispatch_time') or dispatch.get('dispatch_time')
        notes = request.form.get('notes', '').strip()
        
        if not vehicle_no or not driver_name:
            flash('Vehicle number and driver name are required.', 'error')
            return redirect(url_for('edit_dispatch', dispatch_id=dispatch_id))
        
        user = get_current_user()
        update_data = {
            'vehicle_no': vehicle_no,
            'driver_name': driver_name,
            'vendor_name': vendor_name,
            'dispatch_date': dispatch_date,
            'dispatch_time': dispatch_time,
            'notes': notes,
            'modified_by': user['id'],
            'modified_date': datetime.utcnow().isoformat()
        }
        
        success = dispatch_model.update_record("bird_dispatches", dispatch_id, update_data)
        if success:
            flash(f'Dispatch record for vehicle {vehicle_no} updated successfully!', 'success')
        else:
            flash('Error updating dispatch record.', 'error')
        
        return redirect(url_for('bird_dispatch'))
    
    return render_template('edit_dispatch.html', dispatch=dispatch, cycle=cycle, date=date)

@app.route('/delete_dispatch/<dispatch_id>', methods=['POST'])
@admin_required
def delete_dispatch(dispatch_id):
    """Delete a bird dispatch record (admin only)"""
    try:
        dispatch_data = dispatch_model.get_record("bird_dispatches", dispatch_id)
        if dispatch_data:
            # Check if dispatch is completed - if so, we need to restore bird count
            if dispatch_data.get('status') == 'completed':
                cycle_id = dispatch_data.get('cycle_id')
                total_birds = dispatch_data.get('total_birds', 0)
                
                if cycle_id and total_birds > 0:
                    # Restore birds to cycle
                    cycle_data = cycle_model.get_record("cycles", cycle_id)
                    if cycle_data:
                        new_bird_count = cycle_data.get('current_birds', 0) + total_birds
                        cycle_model.update_record("cycles", cycle_id, {
                            'current_birds': new_bird_count
                        })
            
            # Delete all associated weighing records first
            weighing_records = weighing_model.get_records("weighing_records", {'dispatch_id': dispatch_id})
            for weighing_id in weighing_records.keys():
                weighing_model.delete_record("weighing_records", weighing_id)
            
            # Delete the dispatch record
            vehicle_no = dispatch_data.get('vehicle_no', 'Unknown')
            dispatch_model.delete_record("bird_dispatches", dispatch_id)
            flash(f'Dispatch record for vehicle {vehicle_no} deleted successfully!', 'success')
        else:
            flash('Dispatch record not found.', 'error')
    except Exception as e:
        flash(f'Error deleting dispatch record: {str(e)}', 'error')
    
    return redirect(url_for('bird_dispatch'))

@app.route('/edit_weighing_record/<record_id>', methods=['GET', 'POST'])
@admin_required
def edit_weighing_record(record_id):
    """Edit weighing record (admin only)"""
    record_data = weighing_model.get_record("weighing_records", record_id)
    if not record_data:
        flash('Weighing record not found.', 'error')
        return redirect(url_for('bird_dispatch'))
    
    record = {'id': record_id, **record_data}
    dispatch_id = record.get('dispatch_id')
    
    # Get dispatch data for validation
    dispatch_data = dispatch_model.get_record("bird_dispatches", dispatch_id)
    if not dispatch_data:
        flash('Associated dispatch not found.', 'error')
        return redirect(url_for('bird_dispatch'))
    
    if request.method == 'POST':
        no_of_birds = int(request.form.get('no_of_birds', 0))
        weight = float(request.form.get('weight', 0))
        
        if no_of_birds <= 0 or weight <= 0:
            flash('Please enter valid number of birds and weight.', 'error')
            return redirect(url_for('edit_weighing_record', record_id=record_id))
        
        # Calculate average weight per bird
        avg_weight_per_bird = round(weight / no_of_birds, 3)
        
        user = get_current_user()
        update_data = {
            'no_of_birds': no_of_birds,
            'weight': weight,
            'avg_weight_per_bird': avg_weight_per_bird,
            'modified_by': user['id'],
            'modified_date': datetime.utcnow().isoformat()
        }
        
        success = weighing_model.update_record("weighing_records", record_id, update_data)
        if success:
            flash(f'Weighing record #{record.get("serial_no")} updated successfully!', 'success')
        else:
            flash('Error updating weighing record.', 'error')
        
        return redirect(url_for('weighing_screen', dispatch_id=dispatch_id))
    
    return render_template('edit_weighing_record.html', record=record, dispatch_id=dispatch_id)

@app.route('/delete_weighing_record/<record_id>', methods=['POST'])
@admin_required
def delete_weighing_record(record_id):
    """Delete weighing record (admin only)"""
    try:
        record_data = weighing_model.get_record("weighing_records", record_id)
        if not record_data:
            flash('Weighing record not found.', 'error')
            return redirect(url_for('bird_dispatch'))
        
        dispatch_id = record_data.get('dispatch_id')
        serial_no = record_data.get('serial_no', 'Unknown')
        
        success = weighing_model.delete_record("weighing_records", record_id)
        if success:
            flash(f'Weighing record #{serial_no} deleted successfully!', 'success')
        else:
            flash('Error deleting weighing record.', 'error')
        
        return redirect(url_for('weighing_screen', dispatch_id=dispatch_id))
        
    except Exception as e:
        flash(f'Error deleting weighing record: {str(e)}', 'error')
        return redirect(url_for('bird_dispatch'))

@app.route('/update_edit_user/<user_id>', methods=['POST'])
@admin_required
def update_edit_user(user_id):
    """Update user information - admin can update users in their company"""
    user_data = user_model.get_record("users", user_id)
    if not user_data:
        flash('User not found.', 'error')
        return redirect(url_for('user_management'))
    
    current_user = get_current_user()
    
    # Security check: ensure admin can only update users from their company
    if current_user.get('role') != 'super_admin':
        company_id = get_user_company_id()
        if user_data.get('company_id') != company_id:
            flash('Access denied. You can only edit users from your company.', 'error')
            return redirect(url_for('user_management'))
    
    # Get form data
    username = request.form.get('username', '').strip()
    full_name = request.form.get('full_name', '').strip()
    email = request.form.get('email', '').strip()
    phone = request.form.get('phone', '').strip()
    role = request.form.get('role', 'user')
    company_id_form = request.form.get('company_id', '').strip()
    status = request.form.get('status', 'active')
    
    # Farm admin restrictions
    if current_user.get('role') != 'super_admin':
        # Farm admin cannot change company_id or make users super_admin
        company_id_form = get_user_company_id()  # Force their own company
        if role == 'super_admin':
            flash('You cannot assign super admin role.', 'error')
            return redirect(url_for('edit_user', user_id=user_id))
    
    if not username:
        flash('Username is required.', 'error')
        return redirect(url_for('edit_user', user_id=user_id))
    
    # Check if username already exists (excluding current user)
    existing_user = user_model.get_user_by_username(username)
    if existing_user and existing_user.get('id') != user_id:
        flash(f'Username "{username}" already exists.', 'error')
        return redirect(url_for('edit_user', user_id=user_id))
    
    # Prevent admin from removing their own admin role if they're the only admin
    if user_id == current_user['id'] and current_user.get('role') == 'admin' and role != 'admin':
        company_id = get_user_company_id()
        all_users = user_model.get_records("users", {'company_id': company_id})
        active_admins = [u for u in all_users.values() if u.get('role') in ['admin', 'super_admin'] and u.get('status') == 'active']
        
        if len(active_admins) <= 1:
            flash('Cannot remove admin role. At least one admin must remain in the company.', 'error')
            return redirect(url_for('edit_user', user_id=user_id))
            
    # Prevent single admin from being deactivated
    if current_user.get('role') != 'super_admin' and status == 'inactive':
        company_id = get_user_company_id()
        all_users = user_model.get_records("users", {'company_id': company_id})
        active_admins = [u for u in all_users.values() if u.get('role') in ['admin', 'super_admin'] and u.get('status') == 'active']
        
        if len(active_admins) <= 1 and user_data.get('role') in ['admin', 'super_admin']:
            flash('Cannot deactivate the only admin in the company. At least one admin must remain active.', 'error')
            return redirect(url_for('edit_user', user_id=user_id))
    
    try:
        update_data = {
            'username': username,
            'full_name': full_name,
            'email': email,
            'phone': phone,
            'role': role,
            'company_id': company_id_form if company_id_form else None,
            'status': status,
            'modified_by': current_user['id'],
            'modified_date': datetime.utcnow().isoformat()
        }
        
        user_model.update_record("users", user_id, update_data)
        flash(f'User "{username}" updated successfully!', 'success')
        return redirect(url_for('user_management'))
        
    except Exception as e:
        flash(f'Error updating user: {str(e)}', 'error')
        return redirect(url_for('edit_user', user_id=user_id))


        
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
