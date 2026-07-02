#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
IKM High School - Complete School Management System
PRODUCTION VERSION - Windows Compatible, All Features, Fixed CSP, Fee Logic, Error Handling
"""

# =============================================================================
# IMPORTS
# =============================================================================
import os
import datetime
import math
import secrets
import time
import re
import logging
from functools import wraps
from flask import (
    Flask, render_template_string, request, redirect, url_for,
    session, flash, abort, send_from_directory, jsonify, g
)
from flask_socketio import SocketIO, emit
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text, Index
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from markupsafe import escape
import bleach
import psutil
from PIL import Image
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman

# =============================================================================
# LOGGING
# =============================================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =============================================================================
# APP CONFIGURATION
# =============================================================================
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    'postgresql://user:pass@localhost/db'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = datetime.timedelta(hours=24)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['PREFERRED_URL_SCHEME'] = 'https'

# ---- CSP with per‑request nonce ----
def generate_nonce():
    return secrets.token_urlsafe(16)

@app.context_processor
def inject_nonce():
    return {'nonce': generate_nonce()}

csp = {
    'default-src': ["'self'"],
    'script-src': ["'self'", "https://cdn.jsdelivr.net", "https://cdn.socket.io", "https://unpkg.com"],
    'style-src': ["'self'", "https://cdn.jsdelivr.net", "https://unpkg.com"],
    'img-src': ["'self'", "data:", "https://images.unsplash.com", "https://i.imgur.com"],
    'font-src': ["'self'", "https://cdn.jsdelivr.net"],
    'connect-src': ["'self'", "ws://localhost:5000"],
    'frame-src': ["'self'", "https://www.youtube.com"],
}

@app.before_request
def set_nonce():
    g.nonce = generate_nonce()
    nonce_src = f"'nonce-{g.nonce}'"
    csp['script-src'] = [src for src in csp['script-src'] if not src.startswith("'nonce-")] + [nonce_src]
    csp['style-src'] = [src for src in csp['style-src'] if not src.startswith("'nonce-")] + [nonce_src]

Talisman(app, content_security_policy=csp, force_https=True, force_https_permanent=True)

# ---- Rate Limiter ----
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",  # use Redis in production
)

# ---- SocketIO (threading mode – Windows compatible) ----
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', transports=['polling'])

db = SQLAlchemy(app)

# =============================================================================
# SCHOOL CONSTANTS
# =============================================================================
SCHOOL_NAME = "IKM High School"
SCHOOL_SHORT = "IKM"
SCHOOL_LOGO = "https://i.imgur.com/Vdrn2CCh.jpg"
SCHOOL_ADDRESS = "123 Knowledge Street, Harare, Zimbabwe"
SCHOOL_PHONE = "+263 77 123 4567"
SCHOOL_EMAIL = "info@ikmhigh.ac.zw"
SCHOOL_MOTTO = "Knowledge · Integrity · Excellence"
ESTABLISHED = "2024"

# =============================================================================
# DATABASE MODELS
# =============================================================================
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(512), nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    role = db.Column(db.String(20), nullable=False, default='student')
    student_id = db.Column(db.String(20), unique=True, nullable=True)
    class_id = db.Column(db.Integer, db.ForeignKey('class.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    last_seen = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    avatar_color = db.Column(db.String(7), default='#0d6efd')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Class(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    academic_year = db.Column(db.String(20), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

class Subject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False)

class Result(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id'), nullable=False)
    exam_type = db.Column(db.String(50), nullable=False)
    marks = db.Column(db.Integer)
    grade = db.Column(db.String(2))
    term = db.Column(db.Integer, nullable=False)
    year = db.Column(db.Integer, nullable=False)
    __table_args__ = (Index('idx_results_student_subject', 'student_id', 'subject_id'),)

class FeeTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    type = db.Column(db.String(20), nullable=False)  # 'charge' or 'payment'
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(200))
    date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

class News(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    image_url = db.Column(db.String(500))
    video_url = db.Column(db.String(500))
    date_posted = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'))

class Application(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_name = db.Column(db.String(100), nullable=False)
    parent_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    class_applied = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default='pending')
    date_applied = db.Column(db.DateTime, default=datetime.datetime.utcnow)

class Gallery(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100))
    image_url = db.Column(db.String(500), nullable=False)
    description = db.Column(db.String(200))
    uploaded_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    uploaded_by = db.Column(db.Integer, db.ForeignKey('user.id'))

class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    room = db.Column(db.String(50))
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    read_at = db.Column(db.DateTime, nullable=True)

class SiteSetting(db.Model):
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.Text)

class Translation(db.Model):
    key = db.Column(db.String(100), primary_key=True)
    en = db.Column(db.Text)
    fr = db.Column(db.Text)
    sn = db.Column(db.Text)

class AIConversation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    session_id = db.Column(db.String(50), nullable=False)
    question = db.Column(db.Text)
    answer = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)

class TimetableSlot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey('class.id'), nullable=False)
    day_of_week = db.Column(db.Integer, nullable=False)  # 0=Mon .. 4=Fri
    period = db.Column(db.Integer, nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id'), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    room = db.Column(db.String(20))

class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey('class.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False)
    remarks = db.Column(db.String(200))
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    __table_args__ = (Index('idx_attendance_student_date', 'student_id', 'date'),)

# =============================================================================
# DATABASE UPGRADE & INITIALISATION
# =============================================================================
def upgrade_columns():
    with app.app_context():
        inspector = inspect(db.engine)
        if 'user' in inspector.get_table_names():
            with db.engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT character_maximum_length
                    FROM information_schema.columns
                    WHERE table_name='user' AND column_name='password_hash'
                """))
                row = result.fetchone()
                if row and row[0] < 512:
                    conn.execute(text("ALTER TABLE \"user\" ALTER COLUMN password_hash TYPE VARCHAR(512)"))
                    conn.execute(text("ALTER TABLE \"user\" ALTER COLUMN email TYPE VARCHAR(255)"))
                    conn.commit()
        if 'chat_message' in inspector.get_table_names():
            with db.engine.connect() as conn:
                cols = inspector.get_columns('chat_message')
                if not any(c['name'] == 'read_at' for c in cols):
                    conn.execute(text("ALTER TABLE chat_message ADD COLUMN read_at TIMESTAMP"))
                    conn.commit()

with app.app_context():
    upgrade_columns()
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', full_name='System Administrator', email='admin@ikmhigh.ac.zw', role='admin')
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
    defaults = {
        'logo_url': SCHOOL_LOGO,
        'hero_bg': '',
        'content_bg': '',
        'footer_bg': '#1e2a3a',
        'primary_color': '#0d6efd',
        'secondary_color': '#6c757d',
        'font_family': 'Segoe UI, system-ui, sans-serif',
        'ai_enabled': 'true'
    }
    for key, val in defaults.items():
        if not SiteSetting.query.filter_by(key=key).first():
            db.session.add(SiteSetting(key=key, value=val))
    translations = {
        'welcome': {'en': 'Welcome to', 'fr': 'Bienvenue à', 'sn': 'Tigashirei ku'},
        'apply_now': {'en': 'Apply Now', 'fr': 'Postuler maintenant', 'sn': 'Nyorera zvino'},
        'read_more': {'en': 'Read More', 'fr': 'Lire la suite', 'sn': 'Verenga zvimwe'},
        'login': {'en': 'Login', 'fr': 'Connexion', 'sn': 'Pinda'},
        'logout': {'en': 'Logout', 'fr': 'Déconnexion', 'sn': 'Zvibuda'},
        'dashboard': {'en': 'Dashboard', 'fr': 'Tableau de bord', 'sn': 'Dhibhodhi'},
        'chat': {'en': 'Chat', 'fr': 'Discuter', 'sn': 'Taura'},
        'classrooms': {'en': 'Classrooms', 'fr': 'Salles de classe', 'sn': 'Makirasi'},
        'profile': {'en': 'Profile', 'fr': 'Profil', 'sn': 'Mufananidzo'},
        'results': {'en': 'Results', 'fr': 'Résultats', 'sn': 'Zvigumisiro'},
        'fees': {'en': 'Fees', 'fr': 'Frais', 'sn': 'Mari'},
        'contact': {'en': 'Contact', 'fr': 'Contact', 'sn': 'Bata'},
        'about': {'en': 'About', 'fr': 'À propos', 'sn': 'Nezve'},
        'academics': {'en': 'Academics', 'fr': 'Académiques', 'sn': 'Zvidzidzo'},
        'student_life': {'en': 'Student Life', 'fr': 'Vie étudiante', 'sn': 'Hupenyu hwevadzidzi'},
        'admissions': {'en': 'Admissions', 'fr': 'Admissions', 'sn': 'Kugamuchirwa'},
        'news': {'en': 'News', 'fr': 'Actualités', 'sn': 'Nhau'},
        'gallery': {'en': 'Gallery', 'fr': 'Galerie', 'sn': 'Mifananidzo'},
        'home': {'en': 'Home', 'fr': 'Accueil', 'sn': 'Pamba'},
        'quick_links': {'en': 'Quick Links', 'fr': 'Liens rapides', 'sn': 'Zvinongedzo'},
        'follow_us': {'en': 'Follow Us', 'fr': 'Suivez-nous', 'sn': 'Titevere'},
        'type_message': {'en': 'Type a message...', 'fr': 'Tapez un message...', 'sn': 'Nyora meseji...'},
        'send': {'en': 'Send', 'fr': 'Envoyer', 'sn': 'Tuma'},
        'message_admin': {'en': 'Message admin...', 'fr': 'Message à l\'admin...', 'sn': 'Meseji kuna admin...'},
        'select_class': {'en': 'Select a class above to start chatting', 'fr': 'Sélectionnez une classe ci-dessus pour commencer', 'sn': 'Sarudza kirasi pamusoro kuti utange kutaura'},
    }
    for key, data in translations.items():
        if not Translation.query.filter_by(key=key).first():
            t = Translation(key=key, en=data['en'], fr=data['fr'], sn=data['sn'])
            db.session.add(t)
    db.session.commit()

# =============================================================================
# HELPERS
# =============================================================================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def role_required(role):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'role' not in session or session['role'] != role:
                flash('You do not have permission.', 'danger')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated
    return decorator

def csrf_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method in ('POST', 'PUT', 'DELETE'):
            token = request.form.get('csrf_token') or request.headers.get('X-CSRFToken')
            if not token or token != session.get('csrf_token'):
                flash('Invalid CSRF token.', 'danger')
                return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated

def generate_csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(16)
    return session['csrf_token']

def validate_csrf_token(token):
    return token and token == session.get('csrf_token')

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def validate_image_content(file):
    try:
        img = Image.open(file)
        img.verify()
        file.seek(0)
        return True
    except:
        return False

def sanitize_html(content):
    return bleach.clean(content, tags=['p','br','strong','em','u','h1','h2','h3','h4','h5','h6','ul','ol','li','blockquote','a','img','span','div','table','tr','td','th'], attributes={'a': ['href','title','target'], 'img': ['src','alt','width','height']})

def validate_password(password):
    if len(password) < 8:
        return False, "At least 8 characters."
    if not re.search(r'[A-Z]', password):
        return False, "Uppercase letter missing."
    if not re.search(r'[a-z]', password):
        return False, "Lowercase letter missing."
    if not re.search(r'[0-9]', password):
        return False, "Number missing."
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return False, "Special character missing."
    return True, ""

def get_setting(key, default=''):
    setting = SiteSetting.query.filter_by(key=key).first()
    return setting.value if setting else default

def update_setting(key, value):
    setting = SiteSetting.query.filter_by(key=key).first()
    if setting:
        setting.value = value
    else:
        db.session.add(SiteSetting(key=key, value=value))
    db.session.commit()

def get_translation(key, lang='en'):
    t = Translation.query.filter_by(key=key).first()
    if t:
        if lang == 'fr' and t.fr:
            return t.fr
        elif lang == 'sn' and t.sn:
            return t.sn
        else:
            return t.en
    return key

def get_school_logo():
    return get_setting('logo_url', SCHOOL_LOGO)

def get_background_url():
    return get_setting('hero_bg', '')

def add_fee_charge(student_id, amount, description, date):
    fee = FeeTransaction(
        student_id=student_id,
        type='charge',
        amount=amount,
        description=description,
        date=date
    )
    db.session.add(fee)
    db.session.commit()

def add_fee_payment(student_id, amount, description, date):
    fee = FeeTransaction(
        student_id=student_id,
        type='payment',
        amount=amount,
        description=description,
        date=date
    )
    db.session.add(fee)
    db.session.commit()

def compute_fee_balance(student_id):
    charges = db.session.query(db.func.sum(FeeTransaction.amount)).filter_by(student_id=student_id, type='charge').scalar() or 0
    payments = db.session.query(db.func.sum(FeeTransaction.amount)).filter_by(student_id=student_id, type='payment').scalar() or 0
    return charges - payments

def ai_respond(question):
    q = question.lower().strip()
    responses = {
        'hello': "Hello! How can I help you today?",
        'hi': "Hi there! Welcome to IKM High School.",
        'help': "I can help with admissions, academics, school events, and more.",
        'admissions': "You can apply online via the Admissions page. We accept applications year-round.",
        'fees': "Fee details are available on the Fees page. Contact the accounts office for specific queries.",
        'contact': "You can reach us at +263 77 123 4567 or email info@ikmhigh.ac.zw.",
        'about': "IKM High School is a premier secondary school established in 2024.",
        'thank you': "You're welcome!",
        'bye': "Goodbye! Have a great day.",
        'default': "I'm not sure I understand. Please ask about admissions, fees, contact, about, or type 'help'."
    }
    for key in responses:
        if key in q:
            return responses[key]
    return responses['default']

# =============================================================================
# TEMPLATES (embedded as strings)
# =============================================================================

# ---- BASE TEMPLATE ----
BASE_TEMPLATE = '''
<!DOCTYPE html>
<html lang="{{ lang or 'en' }}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.5, user-scalable=yes">
    <title>{{ SCHOOL_NAME }} - {{ title }}</title>
    <meta name="description" content="{{ SCHOOL_NAME }} - A school of Knowledge, Integrity, and Excellence. Providing quality education in Zimbabwe.">
    <meta name="keywords" content="school, education, Zimbabwe, {{ SCHOOL_NAME }}, high school, secondary, Harare">
    <meta name="author" content="{{ SCHOOL_NAME }}">
    <link rel="canonical" href="{{ request.url }}">
    <link rel="preconnect" href="https://cdn.jsdelivr.net">
    <link rel="preconnect" href="https://unpkg.com">
    <style nonce="{{ g.nonce }}">
        :root { --primary: {{ primary_color }}; --secondary: {{ secondary_color }}; --font: {{ font_family }}; --primary-rgb: 13, 110, 253; }
        body { font-family: var(--font); background: #f0f4f8; color: #1a1a2e; transition: background 0.3s, color 0.3s; }
        body.dark-mode { background: #1a1a2e; color: #f0f4f8; }
        .skip-link { position: absolute; top: -100px; left: 0; padding: 10px; background: #fff; color: #000; z-index: 1000; }
        .skip-link:focus { top: 0; }
        .navbar-brand img { height: 50px; width: auto; }
        .hero { background: linear-gradient(135deg, var(--primary), #0a58ca); color: white; padding: 60px 0; margin-bottom: 30px; position: relative; overflow: hidden; min-height: 300px; }
        .hero-bg { position: absolute; top: 0; left: 0; width: 100%; height: 100%; background-size: cover; background-position: center; }
        .hero-bg::after { content: ""; position: absolute; top:0; left:0; width:100%; height:100%; background: linear-gradient(rgba(0,0,0,0.5), rgba(0,0,0,0.3)); }
        .hero .container { position: relative; z-index: 1; }
        .hero h1 { font-size: 2.8rem; font-weight: 700; text-shadow: 2px 2px 8px rgba(0,0,0,0.3); }
        .hero .lead { font-size: 1.2rem; }
        .card-hover { transition: transform 0.3s ease, box-shadow 0.3s ease; }
        .card-hover:hover { transform: translateY(-5px); box-shadow: 0 12px 24px rgba(var(--primary-rgb), 0.2); }
        .chat-box { height: 350px; overflow-y: auto; border: 1px solid #ddd; padding: 15px; background: #f9f9f9; border-radius: 8px; }
        .chat-msg { margin-bottom: 10px; }
        .chat-msg .user { font-weight: bold; }
        .chat-msg .time { font-size: 0.8rem; color: #888; }
        .chat-msg.self { background: #d1ecf1; padding: 5px 10px; border-radius: 10px; }
        .chat-msg.other { background: #f8d7da; padding: 5px 10px; border-radius: 10px; }
        .counter { font-size: 3rem; font-weight: 700; color: #ffc107; text-shadow: 2px 2px 4px rgba(0,0,0,0.2); }
        .counter-label { color: #f8f9fa; font-weight: 500; }
        .btn-toggle-dark { background: none; border: none; font-size: 1.5rem; color: white; cursor: pointer; }
        .glass { background: rgba(255, 255, 255, 0.9); backdrop-filter: blur(8px); border-radius: 1rem; box-shadow: 0 8px 32px rgba(0,0,0,0.1); }
        @media (max-width: 768px) { .hero h1 { font-size: 2rem; } .counter { font-size: 2rem; } }
        :focus-visible { outline: 3px solid var(--primary); outline-offset: 2px; }
        .ai-widget { position: fixed; bottom: 20px; right: 20px; z-index: 999; }
        .ai-widget .btn { border-radius: 50px; padding: 12px 24px; box-shadow: 0 4px 12px rgba(0,0,0,0.2); }
        .ai-chat-box { display: none; width: 320px; max-height: 400px; overflow-y: auto; background: white; border-radius: 16px; box-shadow: 0 8px 32px rgba(0,0,0,0.2); padding: 16px; position: fixed; bottom: 80px; right: 20px; z-index: 1000; }
        .ai-chat-box.open { display: block; }
        .dev-panel { position: fixed; bottom: 10px; left: 10px; background: rgba(0,0,0,0.8); color: #0f0; padding: 10px; border-radius: 8px; font-family: monospace; font-size: 12px; z-index: 9999; display: none; }
        .dev-panel.open { display: block; }
        .img-fluid { max-width: 100%; height: auto; }
        .gallery-thumb { width: 100%; height: auto; aspect-ratio: 4/3; object-fit: cover; }
        .video-container { position: relative; padding-bottom: 56.25%; height: 0; overflow: hidden; max-width: 100%; }
        .video-container iframe { position: absolute; top: 0; left: 0; width: 100%; height: 100%; }
        .pagination .page-link { color: var(--primary); }
        .pagination .active .page-link { background: var(--primary); border-color: var(--primary); color: white; }
        .tab-content { padding-top: 20px; }
        .img-placeholder { width: 100%; height: 200px; object-fit: cover; border-radius: 10px; }
        .student-council-img { width: 100%; height: 250px; object-fit: cover; border-radius: 10px; }
        .admin-sidebar { background: #f8f9fa; min-height: 100vh; }
        .admin-sidebar .nav-link { color: #333; }
        .admin-sidebar .nav-link.active { background: var(--primary); color: white; }
        .list-group-item-action { cursor: pointer; }
        .quote-container { background: rgba(255,255,255,0.85); backdrop-filter: blur(8px); border-radius: 15px; padding: 30px; text-align: center; color: #1a1a1a; border: 1px solid rgba(0,0,0,0.1); box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
        .quote-container blockquote { font-size: 1.4rem; font-style: italic; border-left: 4px solid #ffc107; padding-left: 20px; color: #1a1a1a; }
        .quote-container footer { background: transparent; color: #333; padding: 10px 0 0; }
        @keyframes fadeInUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
        [data-aos] { opacity: 0; transition: opacity 0.8s ease, transform 0.8s ease; }
        [data-aos].aos-animate { opacity: 1; transform: translateY(0); }
        body.dark-mode .card { background: var(--card-bg); }
        body.dark-mode .chat-box { background: #2a2a3e; border-color: #444; color: #eee; }
        body.dark-mode .table { color: #eee; }
        body.dark-mode .form-control { background: #2a2a3e; color: #eee; border-color: #555; }
        .empty-state { text-align: center; padding: 40px 0; }
        .empty-state i { font-size: 3rem; color: #adb5bd; }
        .empty-state p { font-size: 1.2rem; color: #6c757d; }
        @media (prefers-reduced-motion: reduce) { * { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; } }
        .table-responsive { overflow-x: auto; }
    </style>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet" media="print" onload="this.media='all'">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link href="https://unpkg.com/aos@2.3.1/dist/aos.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/lightbox2/2.11.4/css/lightbox.min.css" rel="stylesheet">
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "EducationalOrganization",
      "name": "{{ SCHOOL_NAME }}",
      "description": "A school of Knowledge, Integrity, and Excellence.",
      "address": "{{ SCHOOL_ADDRESS }}",
      "telephone": "{{ SCHOOL_PHONE }}",
      "email": "{{ SCHOOL_EMAIL }}",
      "foundingDate": "{{ ESTABLISHED }}"
    }
    </script>
</head>
<body>
    <a class="skip-link" href="#main-content">Skip to main content</a>
    <nav class="navbar navbar-expand-lg navbar-dark bg-primary sticky-top" aria-label="Main navigation">
        <div class="container">
            <a class="navbar-brand" href="{{ url_for('home') }}">
                <img src="{{ SCHOOL_LOGO }}" alt="{{ SCHOOL_NAME }} Logo" height="50" width="50" loading="lazy">
                {{ SCHOOL_NAME }}
            </a>
            <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarMain" aria-label="Toggle navigation">
                <span class="navbar-toggler-icon"></span>
            </button>
            <div class="collapse navbar-collapse" id="navbarMain">
                <ul class="navbar-nav ms-auto">
                    <li class="nav-item"><a class="nav-link {% if request.path == url_for('home') %}active{% endif %}" href="{{ url_for('home') }}" data-aos="fade-down">{{ _('home') }}</a></li>
                    <li class="nav-item"><a class="nav-link {% if request.path == url_for('about') %}active{% endif %}" href="{{ url_for('about') }}" data-aos="fade-down">{{ _('about') }}</a></li>
                    <li class="nav-item"><a class="nav-link {% if request.path == url_for('academics') %}active{% endif %}" href="{{ url_for('academics') }}" data-aos="fade-down">{{ _('academics') }}</a></li>
                    <li class="nav-item"><a class="nav-link {% if request.path == url_for('student_life') %}active{% endif %}" href="{{ url_for('student_life') }}" data-aos="fade-down">{{ _('student_life') }}</a></li>
                    <li class="nav-item"><a class="nav-link {% if request.path == url_for('admissions') %}active{% endif %}" href="{{ url_for('admissions') }}" data-aos="fade-down">{{ _('admissions') }}</a></li>
                    <li class="nav-item"><a class="nav-link {% if request.path == url_for('news_list') %}active{% endif %}" href="{{ url_for('news_list') }}" data-aos="fade-down">{{ _('news') }}</a></li>
                    <li class="nav-item"><a class="nav-link {% if request.path == url_for('gallery') %}active{% endif %}" href="{{ url_for('gallery') }}" data-aos="fade-down">{{ _('gallery') }}</a></li>
                    <li class="nav-item"><a class="nav-link {% if request.path == url_for('contact') %}active{% endif %}" href="{{ url_for('contact') }}" data-aos="fade-down">{{ _('contact') }}</a></li>
                    <li class="nav-item"><a class="nav-link {% if request.path == url_for('classroom') %}active{% endif %}" href="{{ url_for('classroom') }}" data-aos="fade-down">{{ _('classrooms') }}</a></li>
                    {% if session.user_id %}
                        <li class="nav-item dropdown">
                            <a class="nav-link dropdown-toggle" href="#" id="userDropdown" role="button" data-bs-toggle="dropdown" aria-expanded="false">
                                {{ session.full_name }}
                            </a>
                            <ul class="dropdown-menu dropdown-menu-end">
                                <li><a class="dropdown-item" href="{{ url_for('dashboard') }}">{{ _('dashboard') }}</a></li>
                                <li><a class="dropdown-item" href="{{ url_for('chat') }}">{{ _('chat') }}</a></li>
                                <li><hr class="dropdown-divider"></li>
                                <li><a class="dropdown-item" href="{{ url_for('logout') }}">{{ _('logout') }}</a></li>
                            </ul>
                        </li>
                    {% else %}
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('login') }}" data-aos="fade-down">{{ _('login') }}</a></li>
                    {% endif %}
                    <li class="nav-item">
                        <button class="btn-toggle-dark" id="darkModeToggle" aria-label="Toggle dark mode">
                            <i class="bi bi-moon-fill"></i>
                        </button>
                    </li>
                    <li class="nav-item dropdown">
                        <a class="nav-link dropdown-toggle" href="#" id="langDropdown" role="button" data-bs-toggle="dropdown">
                            <i class="bi bi-globe"></i> {{ lang.upper() }}
                        </a>
                        <ul class="dropdown-menu dropdown-menu-end">
                            <li><a class="dropdown-item" href="?lang=en">English</a></li>
                            <li><a class="dropdown-item" href="?lang=fr">Français</a></li>
                            <li><a class="dropdown-item" href="?lang=sn">ChiShona</a></li>
                        </ul>
                    </li>
                </ul>
            </div>
        </div>
    </nav>

    <div class="container mt-3">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">
                        {{ message|safe }}
                        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
                    </div>
                {% endfor %}
            {% endif %}
        {% endwith %}
    </div>

    <main id="main-content">
        {{ content | safe }}
    </main>

    <!-- AI Assistant -->
    <div class="ai-widget">
        <button class="btn btn-primary" id="aiToggle" aria-label="AI Assistant"><i class="bi bi-robot"></i> AI Help</button>
        <div class="ai-chat-box" id="aiChatBox">
            <div class="d-flex justify-content-between align-items-center mb-2">
                <strong>AI Assistant</strong>
                <button class="btn-close" id="aiClose" aria-label="Close"></button>
            </div>
            <div id="aiMessages" style="max-height:250px; overflow-y:auto; margin-bottom:10px;"></div>
            <div class="input-group">
                <input type="text" class="form-control" id="aiInput" placeholder="Ask me anything...">
                <button class="btn btn-primary" id="aiSend"><i class="bi bi-send"></i></button>
            </div>
        </div>
    </div>

    <!-- Developer Panel (double‑click footer to open) -->
    <div class="dev-panel" id="devPanel">
        <small><strong>Dev Console</strong></small><br>
        <button class="btn btn-sm btn-outline-light" onclick="clearCache()">Clear Cache</button>
        <button class="btn btn-sm btn-outline-light" onclick="runHealthCheck()">Health Check</button>
        <span id="healthStatus"></span>
        <br>
        <span id="perfStats"></span>
    </div>

    <footer style="background: {{ footer_bg }}; color: #ddd; padding: 40px 0 20px;">
        <div class="container">
            <div class="row">
                <div class="col-md-4">
                    <h5>{{ SCHOOL_NAME }}</h5>
                    <p>{{ SCHOOL_MOTTO }}</p>
                    <p>{{ SCHOOL_ADDRESS }}<br>Phone: {{ SCHOOL_PHONE }}<br>Email: <a href="mailto:{{ SCHOOL_EMAIL }}" class="text-white">{{ SCHOOL_EMAIL }}</a></p>
                </div>
                <div class="col-md-4">
                    <h5>{{ _('quick_links') or 'Quick Links' }}</h5>
                    <ul class="list-unstyled">
                        <li><a href="{{ url_for('about') }}" class="text-white">{{ _('about') }}</a></li>
                        <li><a href="{{ url_for('academics') }}" class="text-white">{{ _('academics') }}</a></li>
                        <li><a href="{{ url_for('student_life') }}" class="text-white">{{ _('student_life') }}</a></li>
                        <li><a href="{{ url_for('admissions') }}" class="text-white">{{ _('admissions') }}</a></li>
                        <li><a href="{{ url_for('news_list') }}" class="text-white">{{ _('news') }}</a></li>
                        <li><a href="{{ url_for('gallery') }}" class="text-white">{{ _('gallery') }}</a></li>
                    </ul>
                </div>
                <div class="col-md-4">
                    <h5>{{ _('follow_us') or 'Follow Us' }}</h5>
                    <div>
                        <a href="#" class="text-white me-3" aria-label="Facebook"><i class="bi bi-facebook"></i></a>
                        <a href="#" class="text-white me-3" aria-label="Twitter"><i class="bi bi-twitter-x"></i></a>
                        <a href="#" class="text-white me-3" aria-label="Instagram"><i class="bi bi-instagram"></i></a>
                        <a href="#" class="text-white" aria-label="YouTube"><i class="bi bi-youtube"></i></a>
                    </div>
                    <p class="mt-3">&copy; {{ SCHOOL_NAME }} {{ ESTABLISHED }}. All rights reserved.</p>
                </div>
            </div>
        </div>
    </footer>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js" defer></script>
    <script src="https://cdn.socket.io/4.7.2/socket.io.min.js" defer></script>
    <script src="https://unpkg.com/aos@2.3.1/dist/aos.js" defer></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/lightbox2/2.11.4/js/lightbox.min.js" defer></script>
    <script nonce="{{ g.nonce }}">
        var socket = io({ transports: ['polling'] });
        AOS.init({ duration: 800, once: true, offset: 100 });

        document.addEventListener('DOMContentLoaded', function() {
            const toggle = document.getElementById('darkModeToggle');
            const body = document.body;
            if (localStorage.getItem('darkMode') === 'true') {
                body.classList.add('dark-mode');
                toggle.innerHTML = '<i class="bi bi-sun-fill"></i>';
            }
            toggle.addEventListener('click', function() {
                body.classList.toggle('dark-mode');
                const isDark = body.classList.contains('dark-mode');
                localStorage.setItem('darkMode', isDark);
                toggle.innerHTML = isDark ? '<i class="bi bi-sun-fill"></i>' : '<i class="bi bi-moon-fill"></i>';
            });

            const token = '{{ csrf_token() }}';
            document.querySelectorAll('input[name="csrf_token"]').forEach(el => el.value = token);

            // AI Widget
            const aiToggle = document.getElementById('aiToggle');
            const aiBox = document.getElementById('aiChatBox');
            const aiInput = document.getElementById('aiInput');
            const aiSend = document.getElementById('aiSend');
            const aiMessages = document.getElementById('aiMessages');
            const aiClose = document.getElementById('aiClose');

            aiToggle.addEventListener('click', function() {
                aiBox.classList.toggle('open');
                if (aiBox.classList.contains('open')) {
                    if (!aiMessages.innerHTML) {
                        aiMessages.innerHTML = '<div><em>Hi! I\'m your AI assistant. Ask me anything about the school.</em></div>';
                    }
                    aiInput.focus();
                }
            });
            aiClose.addEventListener('click', function() { aiBox.classList.remove('open'); });

            function sendAI() {
                const msg = aiInput.value.trim();
                if (!msg) return;
                aiMessages.innerHTML += '<div><strong>You:</strong> ' + msg + '</div>';
                aiMessages.innerHTML += '<div class="text-muted"><em>AI is thinking...</em></div>';
                const thinking = aiMessages.lastElementChild;
                fetch('/ai/ask', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ question: msg })
                })
                .then(res => res.json())
                .then(data => {
                    thinking.remove();
                    aiMessages.innerHTML += '<div><strong>AI:</strong> ' + data.answer + '</div>';
                    aiMessages.scrollTop = aiMessages.scrollHeight;
                });
                aiInput.value = '';
                aiMessages.scrollTop = aiMessages.scrollHeight;
            }

            aiSend.addEventListener('click', sendAI);
            aiInput.addEventListener('keypress', function(e) { if (e.key === 'Enter') sendAI(); });

            // Developer panel
            document.querySelector('footer').addEventListener('dblclick', function() {
                document.getElementById('devPanel').classList.toggle('open');
            });

            window.clearCache = function() {
                if (confirm('Clear local cache?')) {
                    localStorage.clear();
                    location.reload();
                }
            };

            window.runHealthCheck = function() {
                document.getElementById('healthStatus').innerHTML = 'Checking...';
                fetch('/health')
                    .then(res => res.json())
                    .then(data => {
                        document.getElementById('healthStatus').innerHTML = '✅ Status: ' + data.status;
                        document.getElementById('perfStats').innerHTML = 'DB: ' + data.db + 'ms | Mem: ' + data.memory;
                    })
                    .catch(() => {
                        document.getElementById('healthStatus').innerHTML = '❌ Error';
                    });
            };

            // Animate counters
            function animateCounters() {
                document.querySelectorAll('.counter').forEach(c => {
                    const target = parseInt(c.dataset.target);
                    if (!target) return;
                    let current = 0;
                    const inc = Math.ceil(target / 80);
                    const timer = setInterval(() => {
                        current += inc;
                        if (current >= target) { c.textContent = target; clearInterval(timer); }
                        else c.textContent = current;
                    }, 25);
                });
            }
            if ('IntersectionObserver' in window) {
                const obs = new IntersectionObserver((entries) => {
                    entries.forEach(entry => {
                        if (entry.isIntersecting) { animateCounters(); obs.unobserve(entry.target); }
                    });
                }, { threshold: 0.3 });
                const stats = document.getElementById('stats-section');
                if (stats) obs.observe(stats);
            } else { animateCounters(); }

            // Rotating quotes
            (function() {
                const quotes = [
                    { text: "Education is the most powerful weapon which you can use to change the world.", author: "Nelson Mandela" },
                    { text: "The function of education is to teach one to think intensively and to think critically.", author: "Martin Luther King Jr." },
                    { text: "Education is not preparation for life; education is life itself.", author: "John Dewey" },
                    { text: "The roots of education are bitter, but the fruit is sweet.", author: "Aristotle" },
                    { text: "Live as if you were to die tomorrow. Learn as if you were to live forever.", author: "Mahatma Gandhi" }
                ];
                let idx = 0;
                const quoteEl = document.getElementById('rotating-quote');
                const authorEl = document.getElementById('rotating-author');
                if (quoteEl && authorEl) {
                    setInterval(() => {
                        idx = (idx + 1) % quotes.length;
                        quoteEl.textContent = quotes[idx].text;
                        authorEl.textContent = '— ' + quotes[idx].author;
                    }, 6000);
                }
            })();

            // Password toggle
            document.querySelectorAll('.toggle-password').forEach(function(btn) {
                btn.addEventListener('click', function() {
                    const input = this.closest('.input-group').querySelector('input');
                    if (input) {
                        const type = input.getAttribute('type') === 'password' ? 'text' : 'password';
                        input.setAttribute('type', type);
                        this.querySelector('i').classList.toggle('bi-eye');
                        this.querySelector('i').classList.toggle('bi-eye-slash');
                    }
                });
            });

            // Scroll to top
            const scrollBtn = document.createElement('button');
            scrollBtn.innerHTML = '<i class="bi bi-arrow-up"></i>';
            scrollBtn.className = 'btn btn-primary rounded-circle position-fixed';
            scrollBtn.style.cssText = 'bottom: 100px; right: 20px; z-index: 999; display: none; width: 50px; height: 50px; border-radius: 50%;';
            document.body.appendChild(scrollBtn);
            window.addEventListener('scroll', function() {
                if (window.scrollY > 300) { scrollBtn.style.display = 'block'; } else { scrollBtn.style.display = 'none'; }
            });
            scrollBtn.addEventListener('click', function() { window.scrollTo({ top: 0, behavior: 'smooth' }); });
        });

        // Socket events for chat
        socket.on('group_message', function(data) {
            var box = document.getElementById('group-chat-box');
            if (!box) return;
            var div = document.createElement('div');
            div.className = 'chat-msg ' + (data.sender_id == userId ? 'self' : 'other');
            div.dataset.msgId = data.id;
            div.innerHTML = '<span class="user">' + data.sender_name + '</span> <span class="time">' + data.timestamp.slice(0,16) + '</span><p>' + data.message + '</p><span class="msg-status"></span>';
            box.appendChild(div);
            box.scrollTop = box.scrollHeight;
            if (data.sender_id != userId) {
                socket.emit('mark_read', { message_id: data.id });
            }
        });

        socket.on('private_message', function(data) {
            var box = document.getElementById('private-chat-box');
            if (!box) return;
            var div = document.createElement('div');
            div.className = 'chat-msg ' + (data.sender_id == userId ? 'self' : 'other');
            div.dataset.msgId = data.id;
            div.innerHTML = '<span class="user">' + data.sender_name + '</span> <span class="time">' + data.timestamp.slice(0,16) + '</span><p>' + data.message + '</p><span class="msg-status"></span>';
            box.appendChild(div);
            box.scrollTop = box.scrollHeight;
            if (data.sender_id != userId) {
                socket.emit('mark_read', { message_id: data.id });
            }
        });

        socket.on('message_read', function(data) {
            var selector = '.chat-msg[data-msg-id="' + data.message_id + '"] .msg-status';
            document.querySelectorAll(selector).forEach(function(el) {
                el.innerHTML = '<i class="bi bi-check2-all text-primary" title="Read"></i>';
            });
        });

        var typingTimeout;
        function emitTyping(room, receiverId) {
            socket.emit('typing', { room: room, receiver_id: receiverId });
        }
        document.addEventListener('DOMContentLoaded', function() {
            var groupInput = document.getElementById('group-msg');
            if (groupInput) {
                groupInput.addEventListener('keydown', function() {
                    emitTyping('group', null);
                });
            }
            var privateInput = document.getElementById('private-msg');
            if (privateInput) {
                privateInput.addEventListener('keydown', function() {
                    emitTyping('private_admin', 1);
                });
            }
            socket.on('typing', function(data) {
                if (data.sender_id == userId) return;
                var room = data.room;
                var indicatorId = room === 'group' ? 'group-typing-indicator' : 'private-typing-indicator';
                var el = document.getElementById(indicatorId);
                if (el) {
                    el.innerHTML = data.sender_name + ' is typing...';
                    clearTimeout(el._timeout);
                    el._timeout = setTimeout(function() { el.innerHTML = ''; }, 3000);
                }
            });

            // Group chat form
            var groupForm = document.getElementById('group-chat-form');
            if (groupForm) {
                groupForm.addEventListener('submit', function(e) {
                    e.preventDefault();
                    var input = document.getElementById('group-msg');
                    var msg = input.value.trim();
                    if (msg) {
                        socket.emit('group_message', { message: msg });
                        input.value = '';
                    }
                });
            }
            var privateForm = document.getElementById('private-chat-form');
            if (privateForm) {
                privateForm.addEventListener('submit', function(e) {
                    e.preventDefault();
                    var input = document.getElementById('private-msg');
                    var msg = input.value.trim();
                    if (msg) {
                        socket.emit('private_message', { message: msg, receiver_id: 1 });
                        input.value = '';
                    }
                });
            }
        });
    </script>
</body>
</html>
'''

# ---- PAGE CONTENT TEMPLATES ----
HOME_CONTENT = '''
{% set bg_url = get_background_url() %}
<section class="hero" style="{% if bg_url %}background: none;{% else %}background: linear-gradient(135deg, {{ primary_color }}, #0a58ca);{% endif %}">
    {% if bg_url %}
    <div class="hero-bg" style="background-image: url('{{ bg_url }}');"></div>
    {% endif %}
    <div class="container text-center" data-aos="fade-up">
        <h1>{{ _('welcome') }} {{ SCHOOL_NAME }}</h1>
        <p class="lead">{{ SCHOOL_MOTTO }}</p>
        <p>Established {{ ESTABLISHED }}</p>
        <a href="{{ url_for('admissions') }}" class="btn btn-warning btn-lg mt-3"><i class="bi bi-pencil-square"></i> {{ _('apply_now') }}</a>
    </div>
</section>

<section class="container my-5">
    <div class="row g-4">
        <div class="col-md-4" data-aos="fade-right">
            <div class="card card-hover h-100 text-center p-4 glass">
                <i class="bi bi-trophy fs-1 text-primary"></i>
                <h5 class="card-title mt-3">Excellence in Education</h5>
                <p class="card-text">We nurture academic excellence and holistic development.</p>
            </div>
        </div>
        <div class="col-md-4" data-aos="fade-up" data-aos-delay="100">
            <div class="card card-hover h-100 text-center p-4 glass">
                <i class="bi bi-people fs-1 text-success"></i>
                <h5 class="card-title mt-3">Dedicated Staff</h5>
                <p class="card-text">Our qualified teachers are committed to student success.</p>
            </div>
        </div>
        <div class="col-md-4" data-aos="fade-left" data-aos-delay="200">
            <div class="card card-hover h-100 text-center p-4 glass">
                <i class="bi bi-globe fs-1 text-info"></i>
                <h5 class="card-title mt-3">Global Outlook</h5>
                <p class="card-text">Preparing students for a interconnected world.</p>
            </div>
        </div>
    </div>
</section>

<section id="stats-section" class="bg-primary text-white py-5">
    <div class="container">
        <div class="row text-center">
            <div class="col-md-3" data-aos="fade-up">
                <div class="counter" data-target="2000">0</div>
                <p class="counter-label">Students</p>
            </div>
            <div class="col-md-3" data-aos="fade-up" data-aos-delay="100">
                <div class="counter" data-target="120">0</div>
                <p class="counter-label">Qualified Teachers</p>
            </div>
            <div class="col-md-3" data-aos="fade-up" data-aos-delay="200">
                <div class="counter" data-target="50">0</div>
                <p class="counter-label">Clubs & Activities</p>
            </div>
            <div class="col-md-3" data-aos="fade-up" data-aos-delay="300">
                <div class="counter" data-target="15">0</div>
                <p class="counter-label">Years of Excellence</p>
            </div>
        </div>
    </div>
</section>

<section class="bg-light py-5" style="background: {{ content_bg }};">
    <div class="container">
        <h2 class="text-center mb-4" data-aos="fade-up">Latest News</h2>
        <div class="row">
            {% for article in news %}
            <div class="col-md-4" data-aos="fade-up" data-aos-delay="{{ loop.index * 100 }}">
                <div class="card card-hover h-100">
                    {% if article.image_url %}
                    <img src="{{ article.image_url }}" class="card-img-top" alt="{{ article.title }}" style="height:200px; object-fit:cover;" loading="lazy">
                    {% endif %}
                    <div class="card-body">
                        <h5 class="card-title">{{ article.title }}</h5>
                        <p class="card-text">{{ article.content[:100] }}...</p>
                        <a href="{{ url_for('news_detail', id=article.id) }}" class="btn btn-outline-primary">{{ _('read_more') }}</a>
                    </div>
                    <div class="card-footer text-muted small">
                        {{ article.date_posted.strftime('%Y-%m-%d') }}
                    </div>
                </div>
            </div>
            {% else %}
            <div class="empty-state">
                <i class="bi bi-newspaper"></i>
                <p>No news yet. Check back later!</p>
            </div>
            {% endfor %}
        </div>
    </div>
</section>
'''

ABOUT_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">{{ _('about') }}</h1>
    <div class="row mt-4">
        <div class="col-md-6" data-aos="fade-up">
            <p><strong>Knowledge · Integrity · Excellence</strong></p>
            <p>{{ SCHOOL_NAME }} is a premier secondary school established in {{ ESTABLISHED }}. We are dedicated to providing quality education that empowers students to become responsible, innovative, and globally competitive citizens.</p>
            <p>Our curriculum combines rigorous academics with co-curricular activities to develop well-rounded individuals. We pride ourselves on a supportive environment where every student is valued.</p>
        </div>
        <div class="col-md-6" data-aos="fade-up" data-aos-delay="100">
            <div class="glass p-4">
                <h4>Our Mission</h4>
                <p>To foster academic excellence, integrity, and lifelong learning through innovative teaching and a nurturing community.</p>
                <h4>Our Vision</h4>
                <p>To be a center of educational excellence that inspires and equips students to shape a better future.</p>
            </div>
        </div>
    </div>
</div>
'''

ACADEMICS_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">{{ _('academics') }}</h1>
    <p class="lead" data-aos="fade-up">Our academic programs are designed to challenge and inspire.</p>
    <div class="row mt-4">
        <div class="col-md-6" data-aos="fade-up">
            <div class="card card-hover"><div class="card-body">
                <img src="https://images.unsplash.com/photo-1580582932707-520aed937b7b?w=600&h=300&fit=crop" class="img-fluid mb-3" alt="Classroom" loading="lazy" width="600" height="300">
                <h5 class="card-title">Ordinary Level (Form 1-4)</h5>
                <p class="card-text">We offer a broad curriculum including Sciences, Humanities, Commerce, and Technical subjects. Students are prepared for ZIMSEC O-Level examinations.</p>
            </div></div>
        </div>
        <div class="col-md-6" data-aos="fade-up" data-aos-delay="100">
            <div class="card card-hover"><div class="card-body">
                <img src="https://images.unsplash.com/photo-1509062522246-3755977927d7?w=600&h=300&fit=crop" class="img-fluid mb-3" alt="Science lab" loading="lazy" width="600" height="300">
                <h5 class="card-title">Advanced Level (Form 5-6)</h5>
                <p class="card-text">A‑Level programmes in Sciences, Arts, and Commercials. Students are prepared for ZIMSEC A-Level and tertiary education.</p>
            </div></div>
        </div>
        <div class="col-md-6" data-aos="fade-up" data-aos-delay="200">
            <div class="card card-hover"><div class="card-body">
                <img src="https://images.unsplash.com/photo-1574629810360-7efbbe195018?w=600&h=300&fit=crop" class="img-fluid mb-3" alt="Sports" loading="lazy" width="600" height="300">
                <h5 class="card-title">Co-Curricular Activities</h5>
                <p class="card-text">Sports, arts, clubs, and leadership opportunities that develop character and teamwork.</p>
            </div></div>
        </div>
        <div class="col-md-6" data-aos="fade-up" data-aos-delay="300">
            <div class="card card-hover"><div class="card-body">
                <img src="https://images.unsplash.com/photo-1517694712202-14dd9538aa97?w=600&h=300&fit=crop" class="img-fluid mb-3" alt="ICT" loading="lazy" width="600" height="300">
                <h5 class="card-title">ICT & Innovation</h5>
                <p class="card-text">Fully equipped computer labs and coding clubs to prepare students for the digital age.</p>
            </div></div>
        </div>
    </div>
</div>
'''

STUDENT_LIFE_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">{{ _('student_life') }}</h1>
    <p class="lead" data-aos="fade-up">Beyond academics, we offer a vibrant community that nurtures talents and builds character.</p>
    <ul class="nav nav-tabs" id="studentLifeTab" role="tablist">
        <li class="nav-item" role="presentation">
            <button class="nav-link active" id="clubs-tab" data-bs-toggle="tab" data-bs-target="#clubs" type="button" role="tab" aria-controls="clubs" aria-selected="true">Clubs & Societies</button>
        </li>
        <li class="nav-item" role="presentation">
            <button class="nav-link" id="sports-tab" data-bs-toggle="tab" data-bs-target="#sports" type="button" role="tab" aria-controls="sports" aria-selected="false">Sports</button>
        </li>
        <li class="nav-item" role="presentation">
            <button class="nav-link" id="council-tab" data-bs-toggle="tab" data-bs-target="#council" type="button" role="tab" aria-controls="council" aria-selected="false">Student Council</button>
        </li>
        <li class="nav-item" role="presentation">
            <button class="nav-link" id="houses-tab" data-bs-toggle="tab" data-bs-target="#houses" type="button" role="tab" aria-controls="houses" aria-selected="false">House System</button>
        </li>
    </ul>
    <div class="tab-content" id="studentLifeTabContent">
        <div class="tab-pane fade show active" id="clubs" role="tabpanel" aria-labelledby="clubs-tab">
            <div class="row mt-3">
                <div class="col-md-4"><img src="https://images.unsplash.com/photo-1524178232363-1fb2b075b655?w=400&h=250&fit=crop" class="img-fluid img-placeholder" alt="Debate Club" loading="lazy" width="400" height="250"><h5 class="mt-2">Debate Club</h5><p>Sharpen your public speaking and critical thinking.</p></div>
                <div class="col-md-4"><img src="https://images.unsplash.com/photo-1511379938547-c1f69419868d?w=400&h=250&fit=crop" class="img-fluid img-placeholder" alt="Music Club" loading="lazy" width="400" height="250"><h5 class="mt-2">Music & Arts</h5><p>Explore your creative side through music, drama, and visual arts.</p></div>
                <div class="col-md-4"><img src="https://images.unsplash.com/photo-1528605248644-14dd04022da1?w=400&h=250&fit=crop" class="img-fluid img-placeholder" alt="STEM Club" loading="lazy" width="400" height="250"><h5 class="mt-2">STEM Club</h5><p>Innovate and experiment in science, technology, engineering, and maths.</p></div>
            </div>
        </div>
        <div class="tab-pane fade" id="sports" role="tabpanel" aria-labelledby="sports-tab">
            <div class="row mt-3">
                <div class="col-md-4"><img src="https://images.unsplash.com/photo-1517466787929-bc90951d0974?w=400&h=250&fit=crop" class="img-fluid img-placeholder" alt="Football" loading="lazy" width="400" height="250"><h5 class="mt-2">Football</h5><p>Team spirit and fitness on the pitch.</p></div>
                <div class="col-md-4"><img src="https://images.unsplash.com/photo-1518611012118-696072aa579a?w=400&h=250&fit=crop" class="img-fluid img-placeholder" alt="Basketball" loading="lazy" width="400" height="250"><h5 class="mt-2">Basketball</h5><p>Speed, agility, and teamwork.</p></div>
                <div class="col-md-4"><img src="https://images.unsplash.com/photo-1531415074968-036ba1b575da?w=400&h=250&fit=crop" class="img-fluid img-placeholder" alt="Athletics" loading="lazy" width="400" height="250"><h5 class="mt-2">Athletics</h5><p>Track and field events to build endurance and discipline.</p></div>
            </div>
        </div>
        <div class="tab-pane fade" id="council" role="tabpanel" aria-labelledby="council-tab">
            <div class="row mt-3">
                <div class="col-md-6"><img src="https://images.unsplash.com/photo-1580582932707-520aed937b7b?w=600&h=300&fit=crop" class="img-fluid student-council-img" alt="Student leaders in uniforms" loading="lazy" width="600" height="300"><h5 class="mt-2">Student Leadership</h5><p>Elected representatives voice student opinions and lead school initiatives.</p></div>
                <div class="col-md-6"><img src="https://images.unsplash.com/photo-1509062522246-3755977927d7?w=600&h=300&fit=crop" class="img-fluid student-council-img" alt="Council meeting with students" loading="lazy" width="600" height="300"><h5 class="mt-2">Council Meetings</h5><p>Regular meetings to discuss school improvement and student welfare.</p></div>
            </div>
        </div>
        <div class="tab-pane fade" id="houses" role="tabpanel" aria-labelledby="houses-tab">
            <div class="row mt-3">
                <div class="col-md-3"><div class="p-3 border rounded text-center"><h4>🏛️ Lion</h4><p>Courage and strength</p></div></div>
                <div class="col-md-3"><div class="p-3 border rounded text-center"><h4>🦅 Eagle</h4><p>Vision and freedom</p></div></div>
                <div class="col-md-3"><div class="p-3 border rounded text-center"><h4>🐉 Dragon</h4><p>Wisdom and power</p></div></div>
                <div class="col-md-3"><div class="p-3 border rounded text-center"><h4>🐺 Wolf</h4><p>Loyalty and teamwork</p></div></div>
            </div>
            <p class="mt-3">House competitions foster unity and healthy rivalry throughout the school year.</p>
        </div>
    </div>
</div>
'''

ADMISSIONS_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">{{ _('admissions') }}</h1>
    <p class="lead" data-aos="fade-up">Apply to join {{ SCHOOL_NAME }}. We welcome students of all backgrounds.</p>
    <div class="row mt-4">
        <div class="col-md-6" data-aos="fade-right">
            <div class="card glass">
                <div class="card-header bg-primary text-white"><h5>Admission Application Form</h5></div>
                <div class="card-body">
                    <form method="POST" action="{{ url_for('admissions') }}">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <div class="mb-3"><label for="student_name" class="form-label">Student Name *</label><input type="text" class="form-control" id="student_name" name="student_name" required></div>
                        <div class="mb-3"><label for="parent_name" class="form-label">Parent/Guardian Name *</label><input type="text" class="form-control" id="parent_name" name="parent_name" required></div>
                        <div class="mb-3"><label for="email" class="form-label">Email *</label><input type="email" class="form-control" id="email" name="email" required></div>
                        <div class="mb-3"><label for="phone" class="form-label">Phone Number *</label><input type="tel" class="form-control" id="phone" name="phone" required></div>
                        <div class="mb-3"><label for="class_applied" class="form-label">Class Applying For *</label>
                            <select class="form-select" id="class_applied" name="class_applied" required>
                                <option value="">Select...</option>
                                <option value="Form 1">Form 1</option><option value="Form 2">Form 2</option>
                                <option value="Form 3">Form 3</option><option value="Form 4">Form 4</option>
                                <option value="Form 5">Form 5</option><option value="Form 6">Form 6</option>
                            </select>
                        </div>
                        <button type="submit" class="btn btn-primary">{{ _('apply_now') }}</button>
                    </form>
                </div>
            </div>
        </div>
        <div class="col-md-6" data-aos="fade-left" data-aos-delay="100">
            <div class="glass p-4">
                <h5>Why Choose {{ SCHOOL_NAME }}?</h5>
                <ul class="list-unstyled">
                    <li><i class="bi bi-check-circle-fill text-success"></i> Qualified and experienced teachers</li>
                    <li><i class="bi bi-check-circle-fill text-success"></i> Modern learning facilities</li>
                    <li><i class="bi bi-check-circle-fill text-success"></i> Small class sizes for individual attention</li>
                    <li><i class="bi bi-check-circle-fill text-success"></i> Strong academic track record</li>
                    <li><i class="bi bi-check-circle-fill text-success"></i> Vibrant co‑curricular activities</li>
                </ul>
                <p><strong>Deadline:</strong> Applications are open year-round.</p>
                <p>For inquiries, contact admissions at <a href="mailto:{{ SCHOOL_EMAIL }}">{{ SCHOOL_EMAIL }}</a>.</p>
            </div>
        </div>
    </div>
</div>
'''

NEWS_LIST_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">{{ _('news') }}</h1>
    <div class="row mt-4">
        {% for article in news %}
        <div class="col-md-4" data-aos="fade-up" data-aos-delay="{{ loop.index * 100 }}">
            <div class="card card-hover h-100">
                {% if article.image_url %}
                <img src="{{ article.image_url }}" class="card-img-top" alt="{{ article.title }}" style="height:200px; object-fit:cover;" loading="lazy">
                {% endif %}
                <div class="card-body">
                    <h5 class="card-title">{{ article.title }}</h5>
                    <p class="card-text">{{ article.content[:150] }}...</p>
                    <a href="{{ url_for('news_detail', id=article.id) }}" class="btn btn-outline-primary">{{ _('read_more') }}</a>
                </div>
                <div class="card-footer text-muted small">{{ article.date_posted.strftime('%Y-%m-%d') }}</div>
            </div>
        </div>
        {% else %}
        <div class="empty-state">
            <i class="bi bi-newspaper"></i>
            <p>No news articles yet.</p>
        </div>
        {% endfor %}
    </div>
    {% if total_pages > 1 %}
    <nav aria-label="News pagination">
        <ul class="pagination justify-content-center">
            {% for p in range(1, total_pages+1) %}
            <li class="page-item {% if p == current_page %}active{% endif %}">
                <a class="page-link" href="{{ url_for('news_list', page=p) }}">{{ p }}</a>
            </li>
            {% endfor %}
        </ul>
    </nav>
    {% endif %}
</div>
'''

NEWS_DETAIL_CONTENT = '''
<div class="container my-5">
    <nav aria-label="breadcrumb">
        <ol class="breadcrumb">
            <li class="breadcrumb-item"><a href="{{ url_for('home') }}">Home</a></li>
            <li class="breadcrumb-item"><a href="{{ url_for('news_list') }}">{{ _('news') }}</a></li>
            <li class="breadcrumb-item active" aria-current="page">{{ article.title }}</li>
        </ol>
    </nav>
    <div class="row" data-aos="fade-up">
        <div class="col-lg-8 mx-auto">
            <h1>{{ article.title }}</h1>
            <p class="text-muted">{{ article.date_posted.strftime('%Y-%m-%d') }}</p>
            {% if article.image_url %}
            <img src="{{ article.image_url }}" class="img-fluid mb-3" alt="{{ article.title }}" loading="lazy">
            {% endif %}
            {% if article.video_url %}
            <div class="video-container mb-3">
                <iframe src="{{ article.video_url }}" frameborder="0" allowfullscreen title="Video"></iframe>
            </div>
            {% endif %}
            <div class="content">{{ article.content|safe }}</div>
            <a href="{{ url_for('news_list') }}" class="btn btn-secondary mt-3">Back to News</a>
        </div>
    </div>
</div>
'''

GALLERY_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">{{ _('gallery') }}</h1>
    <p class="lead" data-aos="fade-up">Moments captured at {{ SCHOOL_NAME }}</p>
    <div class="row mt-4">
        {% for image in images %}
        <div class="col-md-4 col-sm-6" data-aos="fade-up" data-aos-delay="{{ loop.index * 50 }}">
            <div class="card card-hover h-100">
                <a href="{{ image.image_url }}" data-lightbox="gallery" data-title="{{ image.title or 'Image' }}">
                    <img src="{{ image.image_url }}" class="card-img-top gallery-thumb" alt="{{ image.title or 'Gallery image' }}" loading="lazy" width="400" height="300">
                </a>
                <div class="card-body">
                    <h6 class="card-title">{{ image.title or 'Untitled' }}</h6>
                    <p class="card-text small">{{ image.description or '' }}</p>
                    <p class="card-text"><small class="text-muted">{{ image.uploaded_at.strftime('%Y-%m-%d') }}</small></p>
                </div>
            </div>
        </div>
        {% else %}
        <div class="empty-state">
            <i class="bi bi-camera"></i>
            <p>No images in gallery yet.</p>
        </div>
        {% endfor %}
    </div>
    {% if total_pages > 1 %}
    <nav aria-label="Gallery pagination">
        <ul class="pagination justify-content-center">
            {% for p in range(1, total_pages+1) %}
            <li class="page-item {% if p == current_page %}active{% endif %}">
                <a class="page-link" href="{{ url_for('gallery', page=p) }}">{{ p }}</a>
            </li>
            {% endfor %}
        </ul>
    </nav>
    {% endif %}
</div>
'''

CONTACT_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">{{ _('contact') }}</h1>
    <div class="row mt-4">
        <div class="col-md-6" data-aos="fade-up">
            <h5>Get in Touch</h5>
            <p><i class="bi bi-geo-alt"></i> {{ SCHOOL_ADDRESS }}</p>
            <p><i class="bi bi-telephone"></i> {{ SCHOOL_PHONE }}</p>
            <p><i class="bi bi-envelope"></i> <a href="mailto:{{ SCHOOL_EMAIL }}">{{ SCHOOL_EMAIL }}</a></p>
            <p><i class="bi bi-clock"></i> Mon-Fri: 7:30 AM - 4:00 PM</p>
        </div>
        <div class="col-md-6" data-aos="fade-up" data-aos-delay="100">
            <div class="card glass">
                <div class="card-body">
                    <h5>Send a Message</h5>
                    <form method="POST" action="{{ url_for('contact') }}">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <div class="mb-3"><label for="name" class="form-label">Your Name</label><input type="text" class="form-control" id="name" name="name" required></div>
                        <div class="mb-3"><label for="email" class="form-label">Email</label><input type="email" class="form-control" id="email" name="email" required></div>
                        <div class="mb-3"><label for="message" class="form-label">Message</label><textarea class="form-control" id="message" name="message" rows="4" required></textarea></div>
                        <button type="submit" class="btn btn-primary">{{ _('send') }}</button>
                    </form>
                </div>
            </div>
        </div>
    </div>
</div>
'''

LOGIN_CONTENT = '''
<div class="container my-5" style="max-width: 600px;">
    <div class="row g-4 align-items-center">
        <div class="col-md-6" data-aos="fade-right">
            <div class="quote-container">
                <blockquote>
                    <p id="rotating-quote">Education is the most powerful weapon which you can use to change the world.</p>
                    <footer id="rotating-author">— Nelson Mandela</footer>
                </blockquote>
            </div>
        </div>
        <div class="col-md-6" data-aos="fade-left">
            <div class="card shadow glass">
                <div class="card-header bg-primary text-white text-center">
                    <h4><i class="bi bi-box-arrow-in-right"></i> {{ _('login') }} to {{ SCHOOL_SHORT }}</h4>
                </div>
                <div class="card-body">
                    <form method="POST" action="{{ url_for('login') }}">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <div class="mb-3">
                            <label for="username" class="form-label"><i class="bi bi-person"></i> Username</label>
                            <input type="text" class="form-control" id="username" name="username" required autofocus>
                        </div>
                        <div class="mb-3">
                            <label for="password" class="form-label"><i class="bi bi-lock"></i> Password</label>
                            <div class="input-group">
                                <input type="password" class="form-control" id="password" name="password" required>
                                <button class="btn btn-outline-secondary toggle-password" type="button" tabindex="-1" aria-label="Toggle password visibility">
                                    <i class="bi bi-eye"></i>
                                </button>
                            </div>
                        </div>
                        <button type="submit" class="btn btn-primary w-100"><i class="bi bi-key"></i> {{ _('login') }}</button>
                    </form>
                    <p class="mt-3 text-center"><small>Contact admin if you forgot your password.</small></p>
                </div>
            </div>
        </div>
    </div>
</div>
'''

DASHBOARD_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">{{ _('dashboard') }}</h1>
    <div class="row mt-4">
        <div class="col-md-3" data-aos="fade-up">
            <div class="card text-white bg-primary mb-3"><div class="card-body"><h5 class="card-title">{{ _('profile') }}</h5><p class="card-text">{{ session.full_name }}<br>{{ session.role|capitalize }}</p><a href="{{ url_for('profile') }}" class="btn btn-light btn-sm">{{ _('profile') }}</a></div></div>
        </div>
        {% if session.role == 'student' %}
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="100">
            <div class="card text-white bg-success mb-3"><div class="card-body"><h5 class="card-title">{{ _('results') }}</h5><p class="card-text">View your academic performance.</p><a href="{{ url_for('student_results') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="200">
            <div class="card text-white bg-warning mb-3"><div class="card-body"><h5 class="card-title">{{ _('fees') }}</h5><p class="card-text">Check your fee balance and history.</p><a href="{{ url_for('student_fees') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="300">
            <div class="card text-white bg-info mb-3"><div class="card-body"><h5 class="card-title">Attendance</h5><p class="card-text">View your attendance record.</p><a href="{{ url_for('student_attendance') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="400">
            <div class="card text-white bg-secondary mb-3"><div class="card-body"><h5 class="card-title">Timetable</h5><p class="card-text">View your class timetable.</p><a href="{{ url_for('student_timetable') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
        {% endif %}
        {% if session.role == 'admin' or session.role == 'teacher' %}
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="500">
            <div class="card text-white bg-danger mb-3"><div class="card-body"><h5 class="card-title">Admin Panel</h5><p class="card-text">Manage the school system.</p><a href="{{ url_for('admin_dashboard') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
        {% endif %}
        {% if session.role == 'admin' %}
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="600">
            <div class="card text-white bg-secondary mb-3"><div class="card-body"><h5 class="card-title">Change Password</h5><p class="card-text">Update your password securely.</p><a href="{{ url_for('admin_change_password') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="700">
            <div class="card text-white bg-info mb-3"><div class="card-body"><h5 class="card-title">Upload Logo</h5><p class="card-text">Change school logo.</p><a href="{{ url_for('admin_upload_logo') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="800">
            <div class="card text-white bg-dark mb-3"><div class="card-body"><h5 class="card-title">Upload Background</h5><p class="card-text">Change site background.</p><a href="{{ url_for('admin_upload_background') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="900">
            <div class="card text-white bg-primary mb-3"><div class="card-body"><h5 class="card-title">Settings</h5><p class="card-text">Customise site appearance.</p><a href="{{ url_for('admin_settings') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="1000">
            <div class="card text-white bg-warning mb-3"><div class="card-body"><h5 class="card-title">Timetable</h5><p class="card-text">Manage class timetables.</p><a href="{{ url_for('admin_timetable') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="1100">
            <div class="card text-white bg-success mb-3"><div class="card-body"><h5 class="card-title">Attendance</h5><p class="card-text">Mark and view attendance.</p><a href="{{ url_for('admin_attendance') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
        {% endif %}
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="1200">
            <div class="card text-white bg-dark mb-3"><div class="card-body"><h5 class="card-title">{{ _('chat') }}</h5><p class="card-text">Group or private messages.</p><a href="{{ url_for('chat') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="1300">
            <div class="card text-white bg-primary mb-3"><div class="card-body"><h5 class="card-title">{{ _('classrooms') }}</h5><p class="card-text">Join your class chat room.</p><a href="{{ url_for('classroom') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
    </div>
</div>
'''

STUDENT_RESULTS_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">{{ _('results') }}</h1>
    <div class="mt-4" data-aos="fade-up">
        {% if results %}
        <table class="table table-striped table-bordered">
            <thead class="table-primary"><tr><th>Subject</th><th>Exam</th><th>Marks</th><th>Grade</th><th>Term</th><th>Year</th></tr></thead>
            <tbody>
                {% for r in results %}
                <tr><td>{{ r[0].subject_name }}</td><td>{{ r[0].exam_type }}</td><td>{{ r[0].marks }}</td><td>{{ r[0].grade or '-' }}</td><td>{{ r[0].term }}</td><td>{{ r[0].year }}</td></tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}
        <div class="empty-state">
            <i class="bi bi-clipboard-data"></i>
            <p>No results posted yet.</p>
        </div>
        {% endif %}
    </div>
</div>
'''

STUDENT_FEES_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">{{ _('fees') }}</h1>
    <div class="mt-4" data-aos="fade-up">
        <div class="alert alert-info"><strong>Current Balance:</strong> ${{ balance }}</div>
        {% if transactions %}
        <table class="table table-striped">
            <thead class="table-primary"><tr><th>Date</th><th>Type</th><th>Description</th><th>Amount</th></tr></thead>
            <tbody>
                {% for t in transactions %}
                <tr><td>{{ t.date.strftime('%Y-%m-%d') }}</td><td>{{ t.type|capitalize }}</td><td>{{ t.description }}</td><td>${{ t.amount }}</td></tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}
        <div class="empty-state">
            <i class="bi bi-wallet"></i>
            <p>No fee records.</p>
        </div>
        {% endif %}
    </div>
</div>
'''

PROFILE_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">{{ _('profile') }}</h1>
    <div class="card mt-4 glass">
        <div class="card-body">
            <p><strong>Full Name:</strong> {{ user.full_name }}</p>
            <p><strong>Username:</strong> {{ user.username }}</p>
            <p><strong>Email:</strong> {{ user.email }}</p>
            <p><strong>Role:</strong> {{ user.role|capitalize }}</p>
            {% if user.student_id %}
            <p><strong>Student ID:</strong> {{ user.student_id }}</p>
            <p><strong>Class:</strong> {{ class_name }}</p>
            {% endif %}
        </div>
    </div>
</div>
'''

CHAT_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">{{ _('chat') }}</h1>
    <div class="row mt-4">
        <div class="col-md-6">
            <div class="card glass">
                <div class="card-header bg-primary text-white">
                    Group Chat
                    <span class="badge bg-light text-dark float-end" id="group-typing-indicator"></span>
                </div>
                <div class="card-body">
                    <div id="group-chat-box" class="chat-box">
                        {% for msg in group_messages %}
                        <div class="chat-msg {% if msg.sender_id == session.user_id %}self{% else %}other{% endif %}" data-msg-id="{{ msg.id }}">
                            <span class="user">{{ msg.sender_name }}</span>
                            <span class="time">{{ msg.timestamp.strftime('%Y-%m-%d %H:%M') }}</span>
                            <p>{{ msg.message }}</p>
                            <span class="msg-status">
                                {% if msg.sender_id == session.user_id %}
                                    {% if msg.read_at %}
                                        <i class="bi bi-check2-all text-primary" title="Read"></i>
                                    {% else %}
                                        <i class="bi bi-check2 text-secondary" title="Sent"></i>
                                    {% endif %}
                                {% endif %}
                            </span>
                        </div>
                        {% endfor %}
                    </div>
                    <form id="group-chat-form" class="mt-2">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <div class="input-group">
                            <input type="text" class="form-control" id="group-msg" placeholder="{{ _('type_message') }}" required>
                            <button class="btn btn-primary" type="submit">{{ _('send') }}</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
        <div class="col-md-6">
            <div class="card glass">
                <div class="card-header bg-success text-white">
                    Private Chat with Admin
                    <span class="badge bg-light text-dark float-end" id="private-typing-indicator"></span>
                </div>
                <div class="card-body">
                    <div id="private-chat-box" class="chat-box">
                        {% for msg in private_messages %}
                        <div class="chat-msg {% if msg.sender_id == session.user_id %}self{% else %}other{% endif %}" data-msg-id="{{ msg.id }}">
                            <span class="user">{{ msg.sender_name }}</span>
                            <span class="time">{{ msg.timestamp.strftime('%Y-%m-%d %H:%M') }}</span>
                            <p>{{ msg.message }}</p>
                            <span class="msg-status">
                                {% if msg.sender_id == session.user_id %}
                                    {% if msg.read_at %}
                                        <i class="bi bi-check2-all text-primary" title="Read"></i>
                                    {% else %}
                                        <i class="bi bi-check2 text-secondary" title="Sent"></i>
                                    {% endif %}
                                {% endif %}
                            </span>
                        </div>
                        {% endfor %}
                    </div>
                    <form id="private-chat-form" class="mt-2">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <div class="input-group">
                            <input type="text" class="form-control" id="private-msg" placeholder="{{ _('message_admin') }}" required>
                            <button class="btn btn-success" type="submit">{{ _('send') }}</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    </div>
</div>
<script>
    var userId = "{{ session.user_id }}";
</script>
'''

CLASSROOM_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">{{ _('classrooms') }}</h1>
    <p class="lead" data-aos="fade-up">Join your class room and collaborate with teachers and classmates.</p>
    <div class="row mt-4">
        <div class="col-md-4">
            <div class="list-group">
                {% for class in classes %}
                <a href="{{ url_for('classroom_room', room=class.name) }}" class="list-group-item list-group-item-action">
                    {{ class.name }}
                    {% if class.teacher_name %} <span class="badge bg-info">Teacher: {{ class.teacher_name }}</span>{% endif %}
                </a>
                {% else %}
                <p>No classes available.</p>
                {% endfor %}
            </div>
        </div>
        <div class="col-md-8">
            <div class="card glass">
                <div class="card-header bg-primary text-white">{{ _('select_class') }}</div>
                <div class="card-body">
                    <p>Choose a class from the list to join the room and chat in real‑time.</p>
                </div>
            </div>
        </div>
    </div>
</div>
'''

CLASSROOM_ROOM_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">Classroom: {{ room }}</h1>
    <div class="row mt-4">
        <div class="col-md-12">
            <div class="card glass">
                <div class="card-header bg-primary text-white">
                    <i class="bi bi-chat-dots"></i> {{ room }} Chat Room
                    <span class="badge bg-light text-dark float-end" id="classroom-typing-indicator"></span>
                    <a href="{{ url_for('classroom') }}" class="btn btn-light btn-sm float-end me-2">Back</a>
                </div>
                <div class="card-body">
                    <div id="classroom-chat-box" class="chat-box">
                        {% for msg in messages %}
                        <div class="chat-msg {% if msg.sender_id == session.user_id %}self{% else %}other{% endif %}" data-msg-id="{{ msg.id }}">
                            <span class="user">{{ msg.sender_name }}</span>
                            <span class="time">{{ msg.timestamp.strftime('%Y-%m-%d %H:%M') }}</span>
                            <p>{{ msg.message }}</p>
                            <span class="msg-status">
                                {% if msg.sender_id == session.user_id and msg.read_at %}
                                    <i class="bi bi-check2-all text-primary" title="Read"></i>
                                {% elif msg.sender_id == session.user_id %}
                                    <i class="bi bi-check2 text-secondary" title="Sent"></i>
                                {% endif %}
                            </span>
                        </div>
                        {% endfor %}
                    </div>
                    <form id="classroom-chat-form" class="mt-2">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <div class="input-group">
                            <input type="text" class="form-control" id="classroom-msg" placeholder="{{ _('type_message') }}" required>
                            <button class="btn btn-primary" type="submit">{{ _('send') }}</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    </div>
</div>
<script>
    var room = "{{ room }}";
    var userId = "{{ session.user_id }}";
</script>
'''

STUDENT_TIMETABLE_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">My Timetable</h1>
    <div class="card mt-4 glass">
        <div class="card-body">
            <div class="table-responsive">
                <table class="table table-bordered">
                    <thead class="table-primary">
                        <tr><th>Time</th><th>Monday</th><th>Tuesday</th><th>Wednesday</th><th>Thursday</th><th>Friday</th></tr>
                    </thead>
                    <tbody>
                        {% for period in range(1, 9) %}
                        <tr>
                            <td><strong>Period {{ period }}</strong></td>
                            {% for day in range(5) %}
                            <td>
                                {% for slot in slots if slot.period == period and slot.day_of_week == day %}
                                    <strong>{{ slot.subject_name }}</strong><br>
                                    <small>{{ slot.teacher_name or '' }}<br>{{ slot.room or '' }}</small>
                                {% endfor %}
                            </td>
                            {% endfor %}
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>
'''

STUDENT_ATTENDANCE_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">My Attendance</h1>
    <div class="card mt-4 glass">
        <div class="card-body">
            <div class="table-responsive">
                <table class="table table-striped">
                    <thead class="table-primary"><tr><th>Date</th><th>Status</th><th>Remarks</th></tr></thead>
                    <tbody>
                        {% for record in attendance %}
                        <tr>
                            <td>{{ record.date.strftime('%Y-%m-%d') }}</td>
                            <td><span class="badge bg-{% if record.status == 'present' %}success{% elif record.status == 'late' %}warning{% else %}danger{% endif %}">{{ record.status|capitalize }}</span></td>
                            <td>{{ record.remarks or '' }}</td>
                        </tr>
                        {% else %}
                        <tr><td colspan="3" class="text-center">No attendance records.</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>
'''

# ---- ADMIN TEMPLATES ----
ADMIN_DASHBOARD_CONTENT = '''
<div class="container-fluid my-4">
    <div class="row">
        <div class="col-md-2 admin-sidebar">
            <h5 class="mt-3">Admin Menu</h5>
            <ul class="nav nav-pills flex-column">
                <li class="nav-item"><a class="nav-link active" href="{{ url_for('admin_dashboard') }}">Dashboard</a></li>
                <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_users') }}">Users</a></li>
                <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_classes') }}">Classes</a></li>
                <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_subjects') }}">Subjects</a></li>
                <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_results') }}">Results</a></li>
                <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_fees') }}">Fees</a></li>
                <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_news') }}">News</a></li>
                <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_applications') }}">Applications</a></li>
                <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_gallery') }}">Gallery</a></li>
                <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_change_password') }}">Change Password</a></li>
                <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_upload_logo') }}">Upload Logo</a></li>
                <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_upload_background') }}">Upload Background</a></li>
                <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_settings') }}">Site Settings</a></li>
                <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_translations') }}">Translations</a></li>
                <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_timetable') }}">Timetable</a></li>
                <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_attendance') }}">Attendance</a></li>
            </ul>
        </div>
        <div class="col-md-10">
            <h1 data-aos="fade-right">Admin Dashboard</h1>
            <div class="row mt-4">
                <div class="col-md-3" data-aos="fade-up"><div class="card text-white bg-primary"><div class="card-body"><h5>Students</h5><h2>{{ stats.students }}</h2></div></div></div>
                <div class="col-md-3" data-aos="fade-up" data-aos-delay="100"><div class="card text-white bg-success"><div class="card-body"><h5>Teachers</h5><h2>{{ stats.teachers }}</h2></div></div></div>
                <div class="col-md-3" data-aos="fade-up" data-aos-delay="200"><div class="card text-white bg-warning"><div class="card-body"><h5>Applications</h5><h2>{{ stats.applications }}</h2></div></div></div>
                <div class="col-md-3" data-aos="fade-up" data-aos-delay="300"><div class="card text-white bg-info"><div class="card-body"><h5>News Articles</h5><h2>{{ stats.news }}</h2></div></div></div>
            </div>
        </div>
    </div>
</div>
'''

ADMIN_CHANGE_PASSWORD_CONTENT = '''
<div class="container my-5" style="max-width: 500px;">
    <h1 data-aos="fade-right">Change Password</h1>
    <div class="card mt-4 glass">
        <div class="card-body">
            <form method="POST" action="{{ url_for('admin_change_password') }}">
                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                <div class="mb-3"><label for="current_password" class="form-label">Current Password</label><input type="password" class="form-control" id="current_password" name="current_password" required></div>
                <div class="mb-3"><label for="new_password" class="form-label">New Password</label><input type="password" class="form-control" id="new_password" name="new_password" required><div class="form-text">Min 8 chars, include uppercase, lowercase, number, special.</div></div>
                <div class="mb-3"><label for="confirm_password" class="form-label">Confirm New Password</label><input type="password" class="form-control" id="confirm_password" name="confirm_password" required></div>
                <button type="submit" class="btn btn-primary">Update Password</button>
                <a href="{{ url_for('admin_dashboard') }}" class="btn btn-secondary">Cancel</a>
            </form>
        </div>
    </div>
</div>
'''

ADMIN_LOGO_CONTENT = '''
<div class="container my-5" style="max-width: 500px;">
    <h1 data-aos="fade-right">Upload School Logo</h1>
    <div class="card mt-4 glass">
        <div class="card-body">
            <form method="POST" action="{{ url_for('admin_upload_logo') }}" enctype="multipart/form-data">
                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                <div class="mb-3"><label for="logo" class="form-label">Select Logo Image</label><input class="form-control" type="file" name="logo" accept="image/*" required></div>
                <button type="submit" class="btn btn-primary">Upload Logo</button>
                <a href="{{ url_for('admin_dashboard') }}" class="btn btn-secondary">Cancel</a>
            </form>
            <hr><p class="mt-2"><strong>Current Logo:</strong></p><img src="{{ logo_url }}" alt="School Logo" style="max-height:100px;">
        </div>
    </div>
</div>
'''

ADMIN_BG_CONTENT = '''
<div class="container my-5" style="max-width: 500px;">
    <h1 data-aos="fade-right">Upload Background Image</h1>
    <div class="card mt-4 glass">
        <div class="card-body">
            <form method="POST" action="{{ url_for('admin_upload_background') }}" enctype="multipart/form-data">
                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                <div class="mb-3"><label for="background" class="form-label">Select Background Image</label><input class="form-control" type="file" name="background" accept="image/*" required></div>
                <button type="submit" class="btn btn-primary">Upload Background</button>
                <a href="{{ url_for('admin_dashboard') }}" class="btn btn-secondary">Cancel</a>
            </form>
            <hr><p class="mt-2"><strong>Current Background:</strong></p>
            {% if bg_url %}<img src="{{ bg_url }}" alt="Background" style="max-width:100%; max-height:200px; border-radius:8px;">{% else %}<p class="text-muted">No background set. Using default gradient.</p>{% endif %}
        </div>
    </div>
</div>
'''

ADMIN_SETTINGS_CONTENT = '''
<div class="container my-5" style="max-width: 700px;">
    <h1 data-aos="fade-right">Site Settings</h1>
    <div class="card mt-4 glass">
        <div class="card-body">
            <form method="POST" action="{{ url_for('admin_settings') }}">
                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                <div class="mb-3"><label class="form-label">Primary Color (Hex)</label><input type="text" class="form-control" name="primary_color" value="{{ primary_color }}"></div>
                <div class="mb-3"><label class="form-label">Secondary Color (Hex)</label><input type="text" class="form-control" name="secondary_color" value="{{ secondary_color }}"></div>
                <div class="mb-3"><label class="form-label">Footer Background (Hex or CSS color)</label><input type="text" class="form-control" name="footer_bg" value="{{ footer_bg }}"></div>
                <div class="mb-3"><label class="form-label">Content Background (CSS)</label><input type="text" class="form-control" name="content_bg" value="{{ content_bg }}"></div>
                <div class="mb-3"><label class="form-label">Font Family</label><input type="text" class="form-control" name="font_family" value="{{ font_family }}"></div>
                <button type="submit" class="btn btn-primary">Save Settings</button>
                <a href="{{ url_for('admin_dashboard') }}" class="btn btn-secondary">Cancel</a>
            </form>
        </div>
    </div>
</div>
'''

ADMIN_TRANSLATIONS_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">Manage Translations</h1>
    <div class="card mt-4 glass">
        <div class="card-body">
            <form method="POST" action="{{ url_for('admin_translations') }}">
                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                <table class="table table-striped">
                    <thead><tr><th>Key</th><th>English</th><th>Français</th><th>ChiShona</th></tr></thead>
                    <tbody>
                        {% for t in translations %}
                        <tr>
                            <td>{{ t.key }}</td>
                            <td><input type="text" class="form-control" name="en_{{ t.key }}" value="{{ t.en }}"></td>
                            <td><input type="text" class="form-control" name="fr_{{ t.key }}" value="{{ t.fr or '' }}"></td>
                            <td><input type="text" class="form-control" name="sn_{{ t.key }}" value="{{ t.sn or '' }}"></td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
                <button type="submit" class="btn btn-primary">Save Translations</button>
                <a href="{{ url_for('admin_dashboard') }}" class="btn btn-secondary">Cancel</a>
            </form>
        </div>
    </div>
</div>
'''

ADMIN_USERS_CONTENT = '''
<div class="container my-5">
    <h1>Manage Users</h1>
    <div class="mt-4">
        <a href="{{ url_for('admin_users_add') }}" class="btn btn-primary mb-3">Add User</a>
        <table class="table table-striped">
            <thead><tr><th>ID</th><th>Username</th><th>Full Name</th><th>Email</th><th>Role</th><th>Actions</th></tr></thead>
            <tbody>
                {% for user in users %}
                <tr>
                    <td>{{ user.id }}</td><td>{{ user.username }}</td><td>{{ user.full_name }}</td><td>{{ user.email }}</td><td>{{ user.role|capitalize }}</td>
                    <td>
                        <a href="{{ url_for('admin_users_edit', id=user.id) }}" class="btn btn-sm btn-warning">Edit</a>
                        <form method="POST" action="{{ url_for('admin_users_delete', id=user.id) }}" style="display:inline;" onsubmit="return confirm('Delete this user?')">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                            <button type="submit" class="btn btn-sm btn-danger">Delete</button>
                        </form>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>
'''

ADMIN_USERS_ADD_CONTENT = '''
<div class="container my-5">
    <h1>Add User</h1>
    <form method="POST">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <div class="mb-3"><label class="form-label">Username *</label><input type="text" class="form-control" name="username" required></div>
        <div class="mb-3"><label class="form-label">Password *</label><input type="password" class="form-control" name="password" required></div>
        <div class="mb-3"><label class="form-label">Full Name *</label><input type="text" class="form-control" name="full_name" required></div>
        <div class="mb-3"><label class="form-label">Email *</label><input type="email" class="form-control" name="email" required></div>
        <div class="mb-3"><label class="form-label">Role *</label>
            <select class="form-select" name="role" required>
                <option value="student">Student</option><option value="teacher">Teacher</option><option value="admin">Admin</option>
            </select>
        </div>
        <div class="mb-3"><label class="form-label">Student ID (if student)</label><input type="text" class="form-control" name="student_id"></div>
        <div class="mb-3"><label class="form-label">Class (if student)</label>
            <select class="form-select" name="class_id"><option value="">None</option>{% for c in classes %}<option value="{{ c.id }}">{{ c.name }}</option>{% endfor %}</select>
        </div>
        <button type="submit" class="btn btn-primary">Add User</button>
        <a href="{{ url_for('admin_users') }}" class="btn btn-secondary">Cancel</a>
    </form>
</div>
'''

ADMIN_USERS_EDIT_CONTENT = '''
<div class="container my-5">
    <h1>Edit User</h1>
    <form method="POST">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <div class="mb-3"><label class="form-label">Full Name *</label><input type="text" class="form-control" name="full_name" value="{{ user.full_name }}" required></div>
        <div class="mb-3"><label class="form-label">Email *</label><input type="email" class="form-control" name="email" value="{{ user.email }}" required></div>
        <div class="mb-3"><label class="form-label">Role *</label>
            <select class="form-select" name="role" required>
                <option value="student" {% if user.role=='student' %}selected{% endif %}>Student</option>
                <option value="teacher" {% if user.role=='teacher' %}selected{% endif %}>Teacher</option>
                <option value="admin" {% if user.role=='admin' %}selected{% endif %}>Admin</option>
            </select>
        </div>
        <div class="mb-3"><label class="form-label">Student ID</label><input type="text" class="form-control" name="student_id" value="{{ user.student_id or '' }}"></div>
        <div class="mb-3"><label class="form-label">Class</label>
            <select class="form-select" name="class_id"><option value="">None</option>{% for c in classes %}<option value="{{ c.id }}" {% if user.class_id==c.id %}selected{% endif %}>{{ c.name }}</option>{% endfor %}</select>
        </div>
        <button type="submit" class="btn btn-primary">Update</button>
        <a href="{{ url_for('admin_users') }}" class="btn btn-secondary">Cancel</a>
    </form>
</div>
'''

ADMIN_CLASSES_CONTENT = '''
<div class="container my-5">
    <h1>Manage Classes</h1>
    <a href="{{ url_for('admin_classes_add') }}" class="btn btn-primary mb-3">Add Class</a>
    <table class="table table-striped">
        <thead><tr><th>ID</th><th>Name</th><th>Academic Year</th><th>Teacher</th><th>Actions</th></tr></thead>
        <tbody>
        {% for c in classes %}
        <tr>
            <td>{{ c.id }}</td><td>{{ c.name }}</td><td>{{ c.academic_year }}</td><td>{{ c.teacher_name or 'Not assigned' }}</td>
            <td>
                <a href="{{ url_for('admin_classes_edit', id=c.id) }}" class="btn btn-sm btn-warning">Edit</a>
                <form method="POST" action="{{ url_for('admin_classes_delete', id=c.id) }}" style="display:inline;" onsubmit="return confirm('Delete this class?')">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                    <button type="submit" class="btn btn-sm btn-danger">Delete</button>
                </form>
            </td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
</div>
'''

ADMIN_CLASSES_ADD_CONTENT = '''
<div class="container my-5">
    <h1>Add Class</h1>
    <form method="POST">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <div class="mb-3"><label class="form-label">Class Name *</label><input type="text" class="form-control" name="name" required></div>
        <div class="mb-3"><label class="form-label">Academic Year *</label><input type="text" class="form-control" name="academic_year" required></div>
        <div class="mb-3"><label class="form-label">Class Teacher</label>
            <select class="form-select" name="teacher_id"><option value="">None</option>{% for t in teachers %}<option value="{{ t.id }}">{{ t.full_name }}</option>{% endfor %}</select>
        </div>
        <button type="submit" class="btn btn-primary">Add</button>
        <a href="{{ url_for('admin_classes') }}" class="btn btn-secondary">Cancel</a>
    </form>
</div>
'''

ADMIN_CLASSES_EDIT_CONTENT = '''
<div class="container my-5">
    <h1>Edit Class</h1>
    <form method="POST">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <div class="mb-3"><label class="form-label">Class Name *</label><input type="text" class="form-control" name="name" value="{{ cls.name }}" required></div>
        <div class="mb-3"><label class="form-label">Academic Year *</label><input type="text" class="form-control" name="academic_year" value="{{ cls.academic_year }}" required></div>
        <div class="mb-3"><label class="form-label">Class Teacher</label>
            <select class="form-select" name="teacher_id"><option value="">None</option>{% for t in teachers %}<option value="{{ t.id }}" {% if cls.teacher_id==t.id %}selected{% endif %}>{{ t.full_name }}</option>{% endfor %}</select>
        </div>
        <button type="submit" class="btn btn-primary">Update</button>
        <a href="{{ url_for('admin_classes') }}" class="btn btn-secondary">Cancel</a>
    </form>
</div>
'''

ADMIN_SUBJECTS_CONTENT = '''
<div class="container my-5">
    <h1>Manage Subjects</h1>
    <a href="{{ url_for('admin_subjects_add') }}" class="btn btn-primary mb-3">Add Subject</a>
    <table class="table table-striped">
        <thead><tr><th>ID</th><th>Name</th><th>Code</th><th>Actions</th></tr></thead>
        <tbody>
        {% for s in subjects %}
        <tr>
            <td>{{ s.id }}</td><td>{{ s.name }}</td><td>{{ s.code }}</td>
            <td>
                <a href="{{ url_for('admin_subjects_edit', id=s.id) }}" class="btn btn-sm btn-warning">Edit</a>
                <form method="POST" action="{{ url_for('admin_subjects_delete', id=s.id) }}" style="display:inline;" onsubmit="return confirm('Delete this subject?')">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                    <button type="submit" class="btn btn-sm btn-danger">Delete</button>
                </form>
            </td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
</div>
'''

ADMIN_SUBJECTS_ADD_CONTENT = '''
<div class="container my-5">
    <h1>Add Subject</h1>
    <form method="POST">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <div class="mb-3"><label class="form-label">Subject Name *</label><input type="text" class="form-control" name="name" required></div>
        <div class="mb-3"><label class="form-label">Subject Code *</label><input type="text" class="form-control" name="code" required></div>
        <button type="submit" class="btn btn-primary">Add</button>
        <a href="{{ url_for('admin_subjects') }}" class="btn btn-secondary">Cancel</a>
    </form>
</div>
'''

ADMIN_SUBJECTS_EDIT_CONTENT = '''
<div class="container my-5">
    <h1>Edit Subject</h1>
    <form method="POST">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <div class="mb-3"><label class="form-label">Subject Name *</label><input type="text" class="form-control" name="name" value="{{ subject.name }}" required></div>
        <div class="mb-3"><label class="form-label">Subject Code *</label><input type="text" class="form-control" name="code" value="{{ subject.code }}" required></div>
        <button type="submit" class="btn btn-primary">Update</button>
        <a href="{{ url_for('admin_subjects') }}" class="btn btn-secondary">Cancel</a>
    </form>
</div>
'''

ADMIN_RESULTS_CONTENT = '''
<div class="container my-5">
    <h1>Manage Results</h1>
    <div class="card mb-4">
        <div class="card-header">Add Result</div>
        <div class="card-body">
            <form method="POST" action="{{ url_for('admin_results_add') }}">
                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                <div class="row g-2">
                    <div class="col-md-3"><select class="form-select" name="student_id" required><option value="">Student</option>{% for s in students %}<option value="{{ s.id }}">{{ s.full_name }}</option>{% endfor %}</select></div>
                    <div class="col-md-3"><select class="form-select" name="subject_id" required><option value="">Subject</option>{% for s in subjects %}<option value="{{ s.id }}">{{ s.name }}</option>{% endfor %}</select></div>
                    <div class="col-md-2"><input type="text" class="form-control" name="exam_type" placeholder="Exam Type" required></div>
                    <div class="col-md-1"><input type="number" class="form-control" name="marks" placeholder="Marks" required></div>
                    <div class="col-md-1"><input type="text" class="form-control" name="grade" placeholder="Grade"></div>
                    <div class="col-md-1"><input type="number" class="form-control" name="term" placeholder="Term" required></div>
                    <div class="col-md-1"><input type="number" class="form-control" name="year" placeholder="Year" required></div>
                    <div class="col-md-2"><button type="submit" class="btn btn-primary">Add</button></div>
                </div>
            </form>
        </div>
    </div>
    <table class="table table-striped">
        <thead><tr><th>Student</th><th>Subject</th><th>Exam</th><th>Marks</th><th>Grade</th><th>Term</th><th>Year</th><th>Actions</th></tr></thead>
        <tbody>
        {% for r in results %}
        <tr>
            <td>{{ r.student_name }}</td><td>{{ r.subject_name }}</td><td>{{ r.exam_type }}</td><td>{{ r.marks }}</td><td>{{ r.grade or '-' }}</td><td>{{ r.term }}</td><td>{{ r.year }}</td>
            <td><form method="POST" action="{{ url_for('admin_results_delete', id=r.id) }}" style="display:inline;" onsubmit="return confirm('Delete this result?')"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><button type="submit" class="btn btn-sm btn-danger">Delete</button></form></td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
</div>
'''

ADMIN_FEES_CONTENT = '''
<div class="container my-5">
    <h1>Manage Fees</h1>
    <div class="card mb-4">
        <div class="card-header">Add Fee Record</div>
        <div class="card-body">
            <form method="POST" action="{{ url_for('admin_fees_add') }}">
                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                <div class="row g-2">
                    <div class="col-md-2"><select class="form-select" name="student_id" required><option value="">Student</option>{% for s in students %}<option value="{{ s.id }}">{{ s.full_name }}</option>{% endfor %}</select></div>
                    <div class="col-md-2"><input type="number" step="0.01" class="form-control" name="amount" placeholder="Amount" required></div>
                    <div class="col-md-2"><input type="date" class="form-control" name="paid_date" required></div>
                    <div class="col-md-2"><input type="text" class="form-control" name="description" placeholder="Description"></div>
                    <div class="col-md-2">
                        <select class="form-select" name="fee_type" required>
                            <option value="charge">Charge</option>
                            <option value="payment">Payment</option>
                        </select>
                    </div>
                    <div class="col-md-2"><button type="submit" class="btn btn-primary">Add</button></div>
                </div>
            </form>
        </div>
    </div>
    <table class="table table-striped">
        <thead><tr><th>Student</th><th>Amount</th><th>Paid Date</th><th>Description</th><th>Type</th><th>Actions</th></tr></thead>
        <tbody>
        {% for f in fees %}
        <tr>
            <td>{{ f.student_name }}</td><td>${{ f.amount }}</td><td>{{ f.paid_date.strftime('%Y-%m-%d') }}</td><td>{{ f.description or '-' }}</td><td>{{ f.type|capitalize }}</td>
            <td><form method="POST" action="{{ url_for('admin_fees_delete', id=f.id) }}" style="display:inline;" onsubmit="return confirm('Delete this fee record?')"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><button type="submit" class="btn btn-sm btn-danger">Delete</button></form></td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
</div>
'''

ADMIN_NEWS_CONTENT = '''
<div class="container my-5">
    <h1>Manage News</h1>
    <a href="{{ url_for('admin_news_add') }}" class="btn btn-primary mb-3">Add News</a>
    <table class="table table-striped">
        <thead><tr><th>Title</th><th>Image</th><th>Video</th><th>Date</th><th>Actions</th></tr></thead>
        <tbody>
        {% for a in news %}
        <tr>
            <td>{{ a.title }}</td>
            <td>{% if a.image_url %}<img src="{{ a.image_url }}" style="height:40px;">{% else %}-{% endif %}</td>
            <td>{% if a.video_url %}<i class="bi bi-play-circle"></i>{% else %}-{% endif %}</td>
            <td>{{ a.date_posted.strftime('%Y-%m-%d') }}</td>
            <td>
                <a href="{{ url_for('admin_news_edit', id=a.id) }}" class="btn btn-sm btn-warning">Edit</a>
                <form method="POST" action="{{ url_for('admin_news_delete', id=a.id) }}" style="display:inline;" onsubmit="return confirm('Delete this news?')">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                    <button type="submit" class="btn btn-sm btn-danger">Delete</button>
                </form>
            </td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
</div>
'''

ADMIN_NEWS_ADD_CONTENT = '''
<div class="container my-5">
    <h1>Add News Article</h1>
    <form method="POST" enctype="multipart/form-data">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <div class="mb-3"><label class="form-label">Title *</label><input type="text" class="form-control" name="title" required></div>
        <div class="mb-3"><label class="form-label">Content *</label><textarea class="form-control" name="content" rows="6" required></textarea></div>
        <div class="mb-3"><label class="form-label">Image (upload file)</label><input class="form-control" type="file" name="image" accept="image/*"></div>
        <div class="mb-3"><label class="form-label">OR Image URL (optional)</label><input type="text" class="form-control" name="image_url" placeholder="https://example.com/image.jpg"></div>
        <div class="mb-3"><label class="form-label">Video URL (optional) – YouTube/Vimeo embed link</label><input type="text" class="form-control" name="video_url" placeholder="https://www.youtube.com/embed/..."></div>
        <button type="submit" class="btn btn-primary">Publish</button>
        <a href="{{ url_for('admin_news') }}" class="btn btn-secondary">Cancel</a>
    </form>
</div>
'''

ADMIN_NEWS_EDIT_CONTENT = '''
<div class="container my-5">
    <h1>Edit News Article</h1>
    <form method="POST" enctype="multipart/form-data">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <div class="mb-3"><label class="form-label">Title *</label><input type="text" class="form-control" name="title" value="{{ article.title }}" required></div>
        <div class="mb-3"><label class="form-label">Content *</label><textarea class="form-control" name="content" rows="6" required>{{ article.content }}</textarea></div>
        <div class="mb-3"><label class="form-label">Image (upload new file – replaces current)</label><input class="form-control" type="file" name="image" accept="image/*"></div>
        <div class="mb-3"><label class="form-label">OR Image URL</label><input type="text" class="form-control" name="image_url" value="{{ article.image_url or '' }}" placeholder="https://example.com/image.jpg"></div>
        <div class="mb-3"><label class="form-label">Video URL</label><input type="text" class="form-control" name="video_url" value="{{ article.video_url or '' }}" placeholder="https://www.youtube.com/embed/..."></div>
        <button type="submit" class="btn btn-primary">Update</button>
        <a href="{{ url_for('admin_news') }}" class="btn btn-secondary">Cancel</a>
    </form>
</div>
'''

ADMIN_APPLICATIONS_CONTENT = '''
<div class="container my-5">
    <h1>Admission Applications</h1>
    <table class="table table-striped">
        <thead><tr><th>Name</th><th>Parent</th><th>Email</th><th>Phone</th><th>Class</th><th>Status</th><th>Date</th></tr></thead>
        <tbody>
        {% for a in apps %}
        <tr>
            <td>{{ a.student_name }}</td><td>{{ a.parent_name }}</td><td>{{ a.email }}</td><td>{{ a.phone }}</td><td>{{ a.class_applied }}</td>
            <td><span class="badge bg-{% if a.status=='pending' %}warning{% elif a.status=='approved' %}success{% else %}danger{% endif %}">{{ a.status }}</span></td>
            <td>{{ a.date_applied.strftime('%Y-%m-%d') }}</td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
</div>
'''

ADMIN_GALLERY_CONTENT = '''
<div class="container my-5">
    <h1>Manage Gallery</h1>
    <div class="card mb-4">
        <div class="card-header">Upload Image</div>
        <div class="card-body">
            <form method="POST" action="{{ url_for('admin_gallery_add') }}" enctype="multipart/form-data">
                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                <div class="mb-3"><label class="form-label">Title</label><input type="text" class="form-control" name="title"></div>
                <div class="mb-3"><label class="form-label">Description</label><textarea class="form-control" name="description" rows="2"></textarea></div>
                <div class="mb-3"><label class="form-label">Image File *</label><input class="form-control" type="file" name="image" accept="image/*" required></div>
                <button type="submit" class="btn btn-primary">Upload</button>
            </form>
        </div>
    </div>
    <table class="table table-striped">
        <thead><tr><th>Title</th><th>Image</th><th>Uploaded</th><th>Actions</th></tr></thead>
        <tbody>
        {% for img in images %}
        <tr>
            <td>{{ img.title or 'Untitled' }}</td>
            <td><img src="{{ img.image_url }}" style="height:50px; width:auto;" alt=""></td>
            <td>{{ img.uploaded_at.strftime('%Y-%m-%d') }}</td>
            <td><form method="POST" action="{{ url_for('admin_gallery_delete', id=img.id) }}" style="display:inline;" onsubmit="return confirm('Delete this image?')"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><button type="submit" class="btn btn-sm btn-danger">Delete</button></form></td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
</div>
'''

ADMIN_TIMETABLE_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">Manage Timetables</h1>
    <div class="card mt-4 glass">
        <div class="card-body">
            <form method="POST" action="{{ url_for('admin_timetable_add') }}">
                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                <div class="row g-2">
                    <div class="col-md-2">
                        <select class="form-select" name="class_id" required>
                            <option value="">Class</option>
                            {% for c in classes %}
                            <option value="{{ c.id }}">{{ c.name }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="col-md-2">
                        <select class="form-select" name="day" required>
                            <option value="">Day</option>
                            <option value="0">Monday</option><option value="1">Tuesday</option>
                            <option value="2">Wednesday</option><option value="3">Thursday</option>
                            <option value="4">Friday</option>
                        </select>
                    </div>
                    <div class="col-md-1">
                        <input type="number" class="form-control" name="period" placeholder="Period" required>
                    </div>
                    <div class="col-md-2">
                        <select class="form-select" name="subject_id" required>
                            <option value="">Subject</option>
                            {% for s in subjects %}
                            <option value="{{ s.id }}">{{ s.name }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="col-md-2">
                        <select class="form-select" name="teacher_id">
                            <option value="">Teacher</option>
                            {% for t in teachers %}
                            <option value="{{ t.id }}">{{ t.full_name }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="col-md-2">
                        <input type="text" class="form-control" name="room" placeholder="Room">
                    </div>
                    <div class="col-md-1">
                        <button type="submit" class="btn btn-primary">Add</button>
                    </div>
                </div>
            </form>
            <hr>
            <div class="table-responsive">
                <table class="table table-bordered">
                    <thead class="table-primary">
                        <tr><th>Class</th><th>Day</th><th>Period</th><th>Subject</th><th>Teacher</th><th>Room</th><th>Actions</th></tr>
                    </thead>
                    <tbody>
                        {% for slot in slots %}
                        <tr>
                            <td>{{ slot.class_name }}</td>
                            <td>{% set days = ['Monday','Tuesday','Wednesday','Thursday','Friday'] %}{{ days[slot.day_of_week] }}</td>
                            <td>{{ slot.period }}</td>
                            <td>{{ slot.subject_name }}</td>
                            <td>{{ slot.teacher_name or '-' }}</td>
                            <td>{{ slot.room or '-' }}</td>
                            <td>
                                <form method="POST" action="{{ url_for('admin_timetable_delete', id=slot.id) }}" style="display:inline;" onsubmit="return confirm('Delete this slot?')">
                                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                                    <button type="submit" class="btn btn-sm btn-danger">Delete</button>
                                </form>
                            </td>
                        </tr>
                        {% else %}
                        <tr><td colspan="7" class="text-center">No timetable slots.</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>
'''

ADMIN_ATTENDANCE_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">Manage Attendance</h1>
    <div class="card mt-4 glass">
        <div class="card-body">
            <form method="POST" action="{{ url_for('admin_attendance_mark') }}">
                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                <div class="row g-2 mb-3">
                    <div class="col-md-3">
                        <select class="form-select" name="class_id" required>
                            <option value="">Class</option>
                            {% for c in classes %}
                            <option value="{{ c.id }}">{{ c.name }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="col-md-2">
                        <input type="date" class="form-control" name="date" required>
                    </div>
                    <div class="col-md-2">
                        <button type="submit" class="btn btn-primary">Load Students</button>
                    </div>
                </div>
                <div id="attendance-list">
                    <p class="text-muted">Select a class and date to mark attendance.</p>
                </div>
            </form>
        </div>
    </div>
    <div class="card mt-4 glass">
        <div class="card-header bg-primary text-white">Attendance Records</div>
        <div class="card-body">
            <div class="table-responsive">
                <table class="table table-striped">
                    <thead class="table-primary"><tr><th>Student</th><th>Date</th><th>Status</th><th>Remarks</th></tr></thead>
                    <tbody>
                        {% for record in records %}
                        <tr>
                            <td>{{ record.student_name }}</td>
                            <td>{{ record.date.strftime('%Y-%m-%d') }}</td>
                            <td><span class="badge bg-{% if record.status == 'present' %}success{% elif record.status == 'late' %}warning{% else %}danger{% endif %}">{{ record.status|capitalize }}</span></td>
                            <td>{{ record.remarks or '' }}</td>
                        </tr>
                        {% else %}
                        <tr><td colspan="4" class="text-center">No attendance records.</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>
'''

# =============================================================================
# RENDER HELPER
# =============================================================================
def render_page(title, content_template, lang='en', **kwargs):
    kwargs.setdefault('csrf_token', generate_csrf_token)
    kwargs.setdefault('get_background_url', get_background_url)
    kwargs.setdefault('primary_color', get_setting('primary_color', '#0d6efd'))
    kwargs.setdefault('secondary_color', get_setting('secondary_color', '#6c757d'))
    kwargs.setdefault('footer_bg', get_setting('footer_bg', '#1e2a3a'))
    kwargs.setdefault('content_bg', get_setting('content_bg', ''))
    kwargs.setdefault('font_family', get_setting('font_family', 'Segoe UI, system-ui, sans-serif'))
    kwargs.setdefault('SCHOOL_LOGO', get_school_logo())
    kwargs.setdefault('_', lambda key: get_translation(key, lang))
    kwargs.setdefault('lang', lang)
    kwargs.setdefault('g', g)
    content_rendered = render_template_string(content_template, **kwargs)
    return render_template_string(
        BASE_TEMPLATE,
        title=title,
        content=content_rendered,
        SCHOOL_NAME=SCHOOL_NAME,
        SCHOOL_MOTTO=SCHOOL_MOTTO,
        ESTABLISHED=ESTABLISHED,
        SCHOOL_ADDRESS=SCHOOL_ADDRESS,
        SCHOOL_PHONE=SCHOOL_PHONE,
        SCHOOL_EMAIL=SCHOOL_EMAIL,
        SCHOOL_SHORT=SCHOOL_SHORT,
        SCHOOL_LOGO=get_school_logo(),
        primary_color=get_setting('primary_color', '#0d6efd'),
        secondary_color=get_setting('secondary_color', '#6c757d'),
        footer_bg=get_setting('footer_bg', '#1e2a3a'),
        font_family=get_setting('font_family', 'Segoe UI, system-ui, sans-serif'),
        request=request,
        url_for=url_for,
        session=session,
        csrf_token=generate_csrf_token,
        lang=lang,
        _=lambda key: get_translation(key, lang),
        g=g
    )

# =============================================================================
# PUBLIC ROUTES
# =============================================================================
@app.route('/')
def home():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    news = News.query.order_by(News.date_posted.desc()).limit(3).all()
    return render_page('Home', HOME_CONTENT, lang=lang, news=news)

@app.route('/about')
def about():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    return render_page('About', ABOUT_CONTENT, lang=lang)

@app.route('/academics')
def academics():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    return render_page('Academics', ACADEMICS_CONTENT, lang=lang)

@app.route('/student-life')
def student_life():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    return render_page('Student Life', STUDENT_LIFE_CONTENT, lang=lang)

@app.route('/admissions', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def admissions():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    if request.method == 'POST':
        if not validate_csrf_token(request.form.get('csrf_token')):
            flash('Invalid CSRF token.', 'danger')
            return redirect(url_for('admissions'))
        student_name = sanitize_html(request.form.get('student_name'))
        parent_name = sanitize_html(request.form.get('parent_name'))
        email = sanitize_html(request.form.get('email'))
        phone = sanitize_html(request.form.get('phone'))
        class_applied = sanitize_html(request.form.get('class_applied'))
        if all([student_name, parent_name, email, phone, class_applied]):
            app = Application(
                student_name=student_name, parent_name=parent_name,
                email=email, phone=phone, class_applied=class_applied
            )
            db.session.add(app)
            try:
                db.session.commit()
                flash('Application submitted successfully! We will contact you soon.', 'success')
            except Exception as e:
                db.session.rollback()
                logger.error(f"Application submission error: {e}")
                flash('An error occurred. Please try again.', 'danger')
            return redirect(url_for('admissions'))
        else:
            flash('Please fill in all fields.', 'danger')
    return render_page('Admissions', ADMISSIONS_CONTENT, lang=lang)

@app.route('/news')
def news_list():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    page = request.args.get('page', 1, type=int)
    per_page = 6
    total = News.query.count()
    total_pages = math.ceil(total / per_page)
    offset = (page - 1) * per_page
    news = News.query.order_by(News.date_posted.desc()).offset(offset).limit(per_page).all()
    return render_page('News', NEWS_LIST_CONTENT, lang=lang, news=news, current_page=page, total_pages=total_pages)

@app.route('/news/<int:id>')
def news_detail(id):
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    article = News.query.get_or_404(id)
    return render_page('News Detail', NEWS_DETAIL_CONTENT, lang=lang, article=article)

@app.route('/gallery')
def gallery():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    page = request.args.get('page', 1, type=int)
    per_page = 9
    total = Gallery.query.count()
    total_pages = math.ceil(total / per_page)
    offset = (page - 1) * per_page
    images = Gallery.query.order_by(Gallery.uploaded_at.desc()).offset(offset).limit(per_page).all()
    return render_page('Gallery', GALLERY_CONTENT, lang=lang, images=images, current_page=page, total_pages=total_pages)

@app.route('/contact', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def contact():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    if request.method == 'POST':
        if not validate_csrf_token(request.form.get('csrf_token')):
            flash('Invalid CSRF token.', 'danger')
            return redirect(url_for('contact'))
        flash('Your message has been sent. We will get back to you soon.', 'success')
        return redirect(url_for('contact'))
    return render_page('Contact', CONTACT_CONTENT, lang=lang)

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def login():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    if request.method == 'POST':
        if not validate_csrf_token(request.form.get('csrf_token')):
            flash('Invalid CSRF token.', 'danger')
            return redirect(url_for('login'))
        username = sanitize_html(request.form.get('username'))
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session.permanent = True
            session['user_id'] = user.id
            session['username'] = user.username
            session['full_name'] = user.full_name
            session['role'] = user.role
            session['student_id'] = user.student_id
            flash('Login successful.', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password.', 'danger')
    return render_page('Login', LOGIN_CONTENT, lang=lang)

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out.', 'info')
    return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    return render_page('Dashboard', DASHBOARD_CONTENT, lang=lang)

@app.route('/profile')
@login_required
def profile():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    user = User.query.get(session['user_id'])
    class_name = ''
    if user.class_id:
        cls = Class.query.get(user.class_id)
        class_name = cls.name if cls else ''
    return render_page('Profile', PROFILE_CONTENT, lang=lang, user=user, class_name=class_name)

@app.route('/student/results')
@login_required
def student_results():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    if session['role'] != 'student':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    results = db.session.query(Result, Subject).join(Subject, Result.subject_id == Subject.id).filter(Result.student_id == session['user_id']).all()
    return render_page('My Results', STUDENT_RESULTS_CONTENT, lang=lang, results=results)

@app.route('/student/fees')
@login_required
def student_fees():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    if session['role'] != 'student':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    transactions = FeeTransaction.query.filter_by(student_id=session['user_id']).order_by(FeeTransaction.date.desc()).all()
    balance = compute_fee_balance(session['user_id'])
    return render_page('My Fees', STUDENT_FEES_CONTENT, lang=lang, transactions=transactions, balance=balance)

@app.route('/student/timetable')
@login_required
def student_timetable():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    if session['role'] != 'student':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    user = User.query.get(session['user_id'])
    if not user.class_id:
        flash('You are not assigned to a class.', 'warning')
        return redirect(url_for('dashboard'))
    slots = db.session.query(TimetableSlot, Subject, User).join(Subject, TimetableSlot.subject_id == Subject.id).outerjoin(User, TimetableSlot.teacher_id == User.id).filter(TimetableSlot.class_id == user.class_id).all()
    return render_page('My Timetable', STUDENT_TIMETABLE_CONTENT, lang=lang, slots=slots)

@app.route('/student/attendance')
@login_required
def student_attendance():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    if session['role'] != 'student':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    attendance = Attendance.query.filter_by(student_id=session['user_id']).order_by(Attendance.date.desc()).all()
    return render_page('My Attendance', STUDENT_ATTENDANCE_CONTENT, lang=lang, attendance=attendance)

@app.route('/chat')
@login_required
def chat():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    group_msgs = ChatMessage.query.filter_by(room='group').order_by(ChatMessage.timestamp.desc()).limit(50).all()
    group_msgs = list(reversed(group_msgs))
    private_msgs = ChatMessage.query.filter(
        ((ChatMessage.sender_id == session['user_id']) & (ChatMessage.receiver_id == 1)) |
        ((ChatMessage.sender_id == 1) & (ChatMessage.receiver_id == session['user_id']))
    ).order_by(ChatMessage.timestamp.desc()).limit(50).all()
    private_msgs = list(reversed(private_msgs))
    return render_page('Chat', CHAT_CONTENT, lang=lang, group_messages=group_msgs, private_messages=private_msgs)

@app.route('/classroom')
@login_required
def classroom():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    classes = Class.query.all()
    for c in classes:
        if c.teacher_id:
            teacher = User.query.get(c.teacher_id)
            c.teacher_name = teacher.full_name if teacher else ''
        else:
            c.teacher_name = ''
    return render_page('Classroom', CLASSROOM_CONTENT, lang=lang, classes=classes)

@app.route('/classroom/<room>')
@login_required
def classroom_room(room):
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    messages = ChatMessage.query.filter_by(room=room).order_by(ChatMessage.timestamp.desc()).limit(50).all()
    messages = list(reversed(messages))
    return render_page('Classroom Room', CLASSROOM_ROOM_CONTENT, lang=lang, room=room, messages=messages)

# =============================================================================
# AI ROUTE
# =============================================================================
@app.route('/ai/ask', methods=['POST'])
@limiter.limit("10 per minute")
def ai_ask():
    data = request.get_json()
    question = data.get('question', '')
    if not question:
        return jsonify({'answer': 'Please ask a question.'})
    answer = ai_respond(question)
    try:
        conv = AIConversation(
            user_id=session.get('user_id'),
            session_id=session.get('session_id', 'anonymous'),
            question=question,
            answer=answer
        )
        db.session.add(conv)
        db.session.commit()
    except Exception as e:
        logger.error(f"AI conversation save error: {e}")
        db.session.rollback()
    return jsonify({'answer': answer})

# =============================================================================
# HEALTH CHECK
# =============================================================================
@app.route('/health')
def health_check():
    import time
    start = time.time()
    try:
        db.session.execute(text('SELECT 1'))
        db_time = int((time.time() - start) * 1000)
        status = 'ok'
    except Exception as e:
        db_time = 0
        status = 'error'
        logger.error(f"Health check DB error: {e}")
    memory = 'N/A'
    try:
        memory = f"{psutil.virtual_memory().available // (1024 * 1024)} MB"
    except:
        pass
    return jsonify({
        'status': status,
        'db': db_time,
        'memory': memory
    })

# =============================================================================
# ADMIN ROUTES (full CRUD with CSRF and error handling)
# =============================================================================

@app.route('/admin')
@login_required
@role_required('admin')
def admin_dashboard():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    stats = {
        'students': User.query.filter_by(role='student').count(),
        'teachers': User.query.filter_by(role='teacher').count(),
        'applications': Application.query.count(),
        'news': News.query.count(),
    }
    return render_page('Admin Dashboard', ADMIN_DASHBOARD_CONTENT, lang=lang, stats=stats)

@app.route('/admin/settings', methods=['GET', 'POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_settings():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    if request.method == 'POST':
        try:
            update_setting('primary_color', sanitize_html(request.form.get('primary_color', '#0d6efd')))
            update_setting('secondary_color', sanitize_html(request.form.get('secondary_color', '#6c757d')))
            update_setting('footer_bg', sanitize_html(request.form.get('footer_bg', '#1e2a3a')))
            update_setting('content_bg', sanitize_html(request.form.get('content_bg', '')))
            update_setting('font_family', sanitize_html(request.form.get('font_family', 'Segoe UI, system-ui, sans-serif')))
            flash('Settings updated.', 'success')
        except Exception as e:
            db.session.rollback()
            logger.error(f"Settings update error: {e}")
            flash('An error occurred.', 'danger')
        return redirect(url_for('admin_settings'))
    return render_page('Site Settings', ADMIN_SETTINGS_CONTENT, lang=lang,
        primary_color=get_setting('primary_color', '#0d6efd'),
        secondary_color=get_setting('secondary_color', '#6c757d'),
        footer_bg=get_setting('footer_bg', '#1e2a3a'),
        content_bg=get_setting('content_bg', ''),
        font_family=get_setting('font_family', 'Segoe UI, system-ui, sans-serif')
    )

@app.route('/admin/translations', methods=['GET', 'POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_translations():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    if request.method == 'POST':
        try:
            for key in request.form:
                if key.startswith('en_'):
                    t_key = key[3:]
                    t = Translation.query.filter_by(key=t_key).first()
                    if t:
                        t.en = sanitize_html(request.form.get(key, ''))
                        t.fr = sanitize_html(request.form.get(f'fr_{t_key}', ''))
                        t.sn = sanitize_html(request.form.get(f'sn_{t_key}', ''))
            db.session.commit()
            flash('Translations updated.', 'success')
        except Exception as e:
            db.session.rollback()
            logger.error(f"Translation update error: {e}")
            flash('An error occurred.', 'danger')
        return redirect(url_for('admin_translations'))
    translations = Translation.query.all()
    return render_page('Manage Translations', ADMIN_TRANSLATIONS_CONTENT, lang=lang, translations=translations)

@app.route('/admin/change-password', methods=['GET', 'POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_change_password():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    if request.method == 'POST':
        current = request.form.get('current_password')
        new = request.form.get('new_password')
        confirm = request.form.get('confirm_password')
        if new != confirm:
            flash('New passwords do not match.', 'danger')
            return redirect(url_for('admin_change_password'))
        valid, msg = validate_password(new)
        if not valid:
            flash(msg, 'danger')
            return redirect(url_for('admin_change_password'))
        user = User.query.get(session['user_id'])
        if not user.check_password(current):
            flash('Current password is incorrect.', 'danger')
            return redirect(url_for('admin_change_password'))
        user.set_password(new)
        try:
            db.session.commit()
            flash('Password changed successfully.', 'success')
            return redirect(url_for('admin_dashboard'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Password change error: {e}")
            flash('An error occurred.', 'danger')
    return render_page('Change Password', ADMIN_CHANGE_PASSWORD_CONTENT, lang=lang)

@app.route('/admin/upload-logo', methods=['GET', 'POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_upload_logo():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    if request.method == 'POST':
        if 'logo' not in request.files:
            flash('No file selected.', 'danger')
            return redirect(url_for('admin_upload_logo'))
        file = request.files['logo']
        if file.filename == '' or not allowed_file(file.filename) or not validate_image_content(file):
            flash('Invalid file. Please upload a valid image (PNG, JPG, JPEG, GIF, WEBP).', 'danger')
            return redirect(url_for('admin_upload_logo'))
        filename = secure_filename(file.filename)
        upload_dir = os.path.join(app.static_folder, 'uploads')
        os.makedirs(upload_dir, exist_ok=True)
        unique = f"logo_{int(time.time())}_{filename}"
        filepath = os.path.join(upload_dir, unique)
        file.save(filepath)
        logo_url = f"/static/uploads/{unique}"
        update_setting('logo_url', logo_url)
        flash('Logo updated successfully.', 'success')
        return redirect(url_for('admin_dashboard'))
    logo_url = get_school_logo()
    return render_page('Upload Logo', ADMIN_LOGO_CONTENT, lang=lang, logo_url=logo_url)

@app.route('/admin/upload-background', methods=['GET', 'POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_upload_background():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    if request.method == 'POST':
        if 'background' not in request.files:
            flash('No file selected.', 'danger')
            return redirect(url_for('admin_upload_background'))
        file = request.files['background']
        if file.filename == '' or not allowed_file(file.filename) or not validate_image_content(file):
            flash('Invalid file. Please upload a valid image.', 'danger')
            return redirect(url_for('admin_upload_background'))
        filename = secure_filename(file.filename)
        upload_dir = os.path.join(app.static_folder, 'uploads')
        os.makedirs(upload_dir, exist_ok=True)
        unique = f"bg_{int(time.time())}_{filename}"
        filepath = os.path.join(upload_dir, unique)
        file.save(filepath)
        bg_url = f"/static/uploads/{unique}"
        update_setting('hero_bg', bg_url)
        flash('Background image updated successfully.', 'success')
        return redirect(url_for('admin_dashboard'))
    bg_url = get_background_url()
    return render_page('Upload Background', ADMIN_BG_CONTENT, lang=lang, bg_url=bg_url)

# ---- Admin Users CRUD ----
@app.route('/admin/users')
@login_required
@role_required('admin')
def admin_users():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    page = request.args.get('page', 1, type=int)
    per_page = 20
    total = User.query.count()
    total_pages = math.ceil(total / per_page)
    offset = (page - 1) * per_page
    users = User.query.offset(offset).limit(per_page).all()
    return render_page('Manage Users', ADMIN_USERS_CONTENT, lang=lang, users=users, current_page=page, total_pages=total_pages)

@app.route('/admin/users/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_users_add():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    if request.method == 'POST':
        username = sanitize_html(request.form.get('username'))
        password = request.form.get('password')
        full_name = sanitize_html(request.form.get('full_name'))
        email = sanitize_html(request.form.get('email'))
        role = sanitize_html(request.form.get('role'))
        student_id = sanitize_html(request.form.get('student_id'))
        class_id = request.form.get('class_id')
        if not all([username, password, full_name, email, role]):
            flash('Please fill in required fields.', 'danger')
            return redirect(url_for('admin_users_add'))
        valid, msg = validate_password(password)
        if not valid:
            flash(msg, 'danger')
            return redirect(url_for('admin_users_add'))
        user = User(username=username, full_name=full_name, email=email, role=role, student_id=student_id, class_id=class_id)
        user.set_password(password)
        db.session.add(user)
        try:
            db.session.commit()
            flash('User added.', 'success')
            return redirect(url_for('admin_users'))
        except IntegrityError:
            db.session.rollback()
            flash('Username or email already exists.', 'danger')
        except Exception as e:
            db.session.rollback()
            logger.error(f"User add error: {e}")
            flash('An error occurred.', 'danger')
    classes = Class.query.all()
    return render_page('Add User', ADMIN_USERS_ADD_CONTENT, lang=lang, classes=classes)

@app.route('/admin/users/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_users_edit(id):
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    user = User.query.get_or_404(id)
    if request.method == 'POST':
        user.full_name = sanitize_html(request.form.get('full_name'))
        user.email = sanitize_html(request.form.get('email'))
        user.role = sanitize_html(request.form.get('role'))
        user.student_id = sanitize_html(request.form.get('student_id'))
        user.class_id = request.form.get('class_id')
        try:
            db.session.commit()
            flash('User updated.', 'success')
            return redirect(url_for('admin_users'))
        except IntegrityError:
            db.session.rollback()
            flash('Email already exists.', 'danger')
        except Exception as e:
            db.session.rollback()
            logger.error(f"User edit error: {e}")
            flash('An error occurred.', 'danger')
    classes = Class.query.all()
    return render_page('Edit User', ADMIN_USERS_EDIT_CONTENT, lang=lang, user=user, classes=classes)

@app.route('/admin/users/delete/<int:id>', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_users_delete(id):
    user = User.query.get_or_404(id)
    db.session.delete(user)
    try:
        db.session.commit()
        flash('User deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"User delete error: {e}")
        flash('An error occurred.', 'danger')
    return redirect(url_for('admin_users'))

# ---- Admin Classes CRUD ----
@app.route('/admin/classes')
@login_required
@role_required('admin')
def admin_classes():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    classes = Class.query.all()
    for c in classes:
        if c.teacher_id:
            teacher = User.query.get(c.teacher_id)
            c.teacher_name = teacher.full_name if teacher else ''
        else:
            c.teacher_name = ''
    return render_page('Manage Classes', ADMIN_CLASSES_CONTENT, lang=lang, classes=classes)

@app.route('/admin/classes/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_classes_add():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    if request.method == 'POST':
        name = sanitize_html(request.form.get('name'))
        academic_year = sanitize_html(request.form.get('academic_year'))
        teacher_id = request.form.get('teacher_id')
        if not name or not academic_year:
            flash('Name and Academic Year are required.', 'danger')
            return redirect(url_for('admin_classes_add'))
        cls = Class(name=name, academic_year=academic_year, teacher_id=teacher_id or None)
        db.session.add(cls)
        try:
            db.session.commit()
            flash('Class added.', 'success')
            return redirect(url_for('admin_classes'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Class add error: {e}")
            flash('An error occurred.', 'danger')
    teachers = User.query.filter_by(role='teacher').all()
    return render_page('Add Class', ADMIN_CLASSES_ADD_CONTENT, lang=lang, teachers=teachers)

@app.route('/admin/classes/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_classes_edit(id):
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    cls = Class.query.get_or_404(id)
    if request.method == 'POST':
        cls.name = sanitize_html(request.form.get('name'))
        cls.academic_year = sanitize_html(request.form.get('academic_year'))
        cls.teacher_id = request.form.get('teacher_id') or None
        try:
            db.session.commit()
            flash('Class updated.', 'success')
            return redirect(url_for('admin_classes'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Class edit error: {e}")
            flash('An error occurred.', 'danger')
    teachers = User.query.filter_by(role='teacher').all()
    return render_page('Edit Class', ADMIN_CLASSES_EDIT_CONTENT, lang=lang, cls=cls, teachers=teachers)

@app.route('/admin/classes/delete/<int:id>', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_classes_delete(id):
    cls = Class.query.get_or_404(id)
    db.session.delete(cls)
    try:
        db.session.commit()
        flash('Class deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Class delete error: {e}")
        flash('An error occurred.', 'danger')
    return redirect(url_for('admin_classes'))

# ---- Admin Subjects CRUD ----
@app.route('/admin/subjects')
@login_required
@role_required('admin')
def admin_subjects():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    subjects = Subject.query.all()
    return render_page('Manage Subjects', ADMIN_SUBJECTS_CONTENT, lang=lang, subjects=subjects)

@app.route('/admin/subjects/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_subjects_add():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    if request.method == 'POST':
        name = sanitize_html(request.form.get('name'))
        code = sanitize_html(request.form.get('code'))
        if not name or not code:
            flash('Name and Code are required.', 'danger')
            return redirect(url_for('admin_subjects_add'))
        subject = Subject(name=name, code=code)
        db.session.add(subject)
        try:
            db.session.commit()
            flash('Subject added.', 'success')
            return redirect(url_for('admin_subjects'))
        except IntegrityError:
            db.session.rollback()
            flash('Subject code already exists.', 'danger')
        except Exception as e:
            db.session.rollback()
            logger.error(f"Subject add error: {e}")
            flash('An error occurred.', 'danger')
    return render_page('Add Subject', ADMIN_SUBJECTS_ADD_CONTENT, lang=lang)

@app.route('/admin/subjects/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_subjects_edit(id):
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    subject = Subject.query.get_or_404(id)
    if request.method == 'POST':
        subject.name = sanitize_html(request.form.get('name'))
        subject.code = sanitize_html(request.form.get('code'))
        try:
            db.session.commit()
            flash('Subject updated.', 'success')
            return redirect(url_for('admin_subjects'))
        except IntegrityError:
            db.session.rollback()
            flash('Subject code already exists.', 'danger')
        except Exception as e:
            db.session.rollback()
            logger.error(f"Subject edit error: {e}")
            flash('An error occurred.', 'danger')
    return render_page('Edit Subject', ADMIN_SUBJECTS_EDIT_CONTENT, lang=lang, subject=subject)

@app.route('/admin/subjects/delete/<int:id>', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_subjects_delete(id):
    subject = Subject.query.get_or_404(id)
    db.session.delete(subject)
    try:
        db.session.commit()
        flash('Subject deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Subject delete error: {e}")
        flash('An error occurred.', 'danger')
    return redirect(url_for('admin_subjects'))

# ---- Admin Results ----
@app.route('/admin/results')
@login_required
@role_required('admin')
def admin_results():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    page = request.args.get('page', 1, type=int)
    per_page = 20
    total = db.session.query(Result).count()
    total_pages = math.ceil(total / per_page)
    offset = (page - 1) * per_page
    results = db.session.query(Result, User, Subject).join(User, Result.student_id == User.id).join(Subject, Result.subject_id == Subject.id).order_by(Result.year.desc(), Result.term.desc()).offset(offset).limit(per_page).all()
    students = User.query.filter_by(role='student').all()
    subjects = Subject.query.all()
    return render_page('Manage Results', ADMIN_RESULTS_CONTENT, lang=lang, results=results, students=students, subjects=subjects, current_page=page, total_pages=total_pages)

@app.route('/admin/results/add', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_results_add():
    student_id = request.form.get('student_id')
    subject_id = request.form.get('subject_id')
    exam_type = sanitize_html(request.form.get('exam_type'))
    marks = request.form.get('marks')
    grade = sanitize_html(request.form.get('grade'))
    term = request.form.get('term')
    year = request.form.get('year')
    if not all([student_id, subject_id, exam_type, marks, term, year]):
        flash('All fields except grade are required.', 'danger')
        return redirect(url_for('admin_results'))
    result = Result(student_id=student_id, subject_id=subject_id, exam_type=exam_type, marks=marks, grade=grade, term=term, year=year)
    db.session.add(result)
    try:
        db.session.commit()
        flash('Result added.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Result add error: {e}")
        flash('An error occurred.', 'danger')
    return redirect(url_for('admin_results'))

@app.route('/admin/results/delete/<int:id>', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_results_delete(id):
    result = Result.query.get_or_404(id)
    db.session.delete(result)
    try:
        db.session.commit()
        flash('Result deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Result delete error: {e}")
        flash('An error occurred.', 'danger')
    return redirect(url_for('admin_results'))

# ---- Admin Fees ----
@app.route('/admin/fees')
@login_required
@role_required('admin')
def admin_fees():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    page = request.args.get('page', 1, type=int)
    per_page = 20
    total = db.session.query(FeeTransaction).count()
    total_pages = math.ceil(total / per_page)
    offset = (page - 1) * per_page
    fees = db.session.query(FeeTransaction, User).join(User, FeeTransaction.student_id == User.id).order_by(FeeTransaction.date.desc()).offset(offset).limit(per_page).all()
    students = User.query.filter_by(role='student').all()
    return render_page('Manage Fees', ADMIN_FEES_CONTENT, lang=lang, fees=fees, students=students, current_page=page, total_pages=total_pages)

@app.route('/admin/fees/add', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_fees_add():
    student_id = request.form.get('student_id')
    amount = request.form.get('amount')
    date_str = request.form.get('paid_date')
    description = sanitize_html(request.form.get('description'))
    fee_type = request.form.get('fee_type')
    if not all([student_id, amount, date_str, fee_type]):
        flash('All fields are required.', 'danger')
        return redirect(url_for('admin_fees'))
    date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
    try:
        if fee_type == 'charge':
            add_fee_charge(int(student_id), float(amount), description, date_obj)
        else:
            add_fee_payment(int(student_id), float(amount), description, date_obj)
        flash('Fee record added.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Fee add error: {e}")
        flash('An error occurred.', 'danger')
    return redirect(url_for('admin_fees'))

@app.route('/admin/fees/delete/<int:id>', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_fees_delete(id):
    fee = FeeTransaction.query.get_or_404(id)
    db.session.delete(fee)
    try:
        db.session.commit()
        flash('Fee record deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Fee delete error: {e}")
        flash('An error occurred.', 'danger')
    return redirect(url_for('admin_fees'))

# ---- Admin News ----
@app.route('/admin/news')
@login_required
@role_required('admin')
def admin_news():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    page = request.args.get('page', 1, type=int)
    per_page = 10
    total = News.query.count()
    total_pages = math.ceil(total / per_page)
    offset = (page - 1) * per_page
    news = News.query.order_by(News.date_posted.desc()).offset(offset).limit(per_page).all()
    return render_page('Manage News', ADMIN_NEWS_CONTENT, lang=lang, news=news, current_page=page, total_pages=total_pages)

@app.route('/admin/news/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_news_add():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    if request.method == 'POST':
        title = sanitize_html(request.form.get('title'))
        content = sanitize_html(request.form.get('content'))
        image_url = sanitize_html(request.form.get('image_url'))
        video_url = sanitize_html(request.form.get('video_url'))
        file = request.files.get('image')
        if file and allowed_file(file.filename) and validate_image_content(file):
            filename = secure_filename(file.filename)
            upload_dir = os.path.join(app.static_folder, 'uploads')
            os.makedirs(upload_dir, exist_ok=True)
            unique = f"{int(time.time())}_{filename}"
            filepath = os.path.join(upload_dir, unique)
            file.save(filepath)
            image_url = f"/static/uploads/{unique}"
        if not title or not content:
            flash('Title and Content are required.', 'danger')
            return redirect(url_for('admin_news_add'))
        news = News(title=title, content=content, image_url=image_url, video_url=video_url, author_id=session['user_id'])
        db.session.add(news)
        try:
            db.session.commit()
            flash('News article added.', 'success')
            return redirect(url_for('admin_news'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"News add error: {e}")
            flash('An error occurred.', 'danger')
    return render_page('Add News', ADMIN_NEWS_ADD_CONTENT, lang=lang)

@app.route('/admin/news/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_news_edit(id):
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    article = News.query.get_or_404(id)
    if request.method == 'POST':
        title = sanitize_html(request.form.get('title'))
        content = sanitize_html(request.form.get('content'))
        image_url = sanitize_html(request.form.get('image_url'))
        video_url = sanitize_html(request.form.get('video_url'))
        file = request.files.get('image')
        if file and allowed_file(file.filename) and validate_image_content(file):
            if article.image_url and article.image_url.startswith('/static/uploads/'):
                old_path = os.path.join('.', article.image_url.lstrip('/'))
                if os.path.exists(old_path):
                    os.remove(old_path)
            filename = secure_filename(file.filename)
            upload_dir = os.path.join(app.static_folder, 'uploads')
            os.makedirs(upload_dir, exist_ok=True)
            unique = f"{int(time.time())}_{filename}"
            filepath = os.path.join(upload_dir, unique)
            file.save(filepath)
            image_url = f"/static/uploads/{unique}"
        if not title or not content:
            flash('Title and Content are required.', 'danger')
            return redirect(url_for('admin_news_edit', id=id))
        article.title = title
        article.content = content
        article.image_url = image_url
        article.video_url = video_url
        try:
            db.session.commit()
            flash('News updated.', 'success')
            return redirect(url_for('admin_news'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"News edit error: {e}")
            flash('An error occurred.', 'danger')
    return render_page('Edit News', ADMIN_NEWS_EDIT_CONTENT, lang=lang, article=article)

@app.route('/admin/news/delete/<int:id>', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_news_delete(id):
    article = News.query.get_or_404(id)
    if article.image_url and article.image_url.startswith('/static/uploads/'):
        old_path = os.path.join('.', article.image_url.lstrip('/'))
        if os.path.exists(old_path):
            os.remove(old_path)
    db.session.delete(article)
    try:
        db.session.commit()
        flash('News deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"News delete error: {e}")
        flash('An error occurred.', 'danger')
    return redirect(url_for('admin_news'))

# ---- Admin Applications ----
@app.route('/admin/applications')
@login_required
@role_required('admin')
def admin_applications():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    page = request.args.get('page', 1, type=int)
    per_page = 20
    total = Application.query.count()
    total_pages = math.ceil(total / per_page)
    offset = (page - 1) * per_page
    apps = Application.query.order_by(Application.date_applied.desc()).offset(offset).limit(per_page).all()
    return render_page('Manage Applications', ADMIN_APPLICATIONS_CONTENT, lang=lang, apps=apps, current_page=page, total_pages=total_pages)

# ---- Admin Gallery ----
@app.route('/admin/gallery')
@login_required
@role_required('admin')
def admin_gallery():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    page = request.args.get('page', 1, type=int)
    per_page = 12
    total = Gallery.query.count()
    total_pages = math.ceil(total / per_page)
    offset = (page - 1) * per_page
    images = Gallery.query.order_by(Gallery.uploaded_at.desc()).offset(offset).limit(per_page).all()
    return render_page('Manage Gallery', ADMIN_GALLERY_CONTENT, lang=lang, images=images, current_page=page, total_pages=total_pages)

@app.route('/admin/gallery/add', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_gallery_add():
    if 'image' not in request.files:
        flash('No image file provided.', 'danger')
        return redirect(url_for('admin_gallery'))
    file = request.files['image']
    if file.filename == '' or not allowed_file(file.filename) or not validate_image_content(file):
        flash('Invalid file type. Please upload PNG, JPG, JPEG, GIF, or WEBP.', 'danger')
        return redirect(url_for('admin_gallery'))
    filename = secure_filename(file.filename)
    gallery_dir = os.path.join(app.static_folder, 'gallery')
    os.makedirs(gallery_dir, exist_ok=True)
    unique = f"{int(time.time())}_{filename}"
    filepath = os.path.join(gallery_dir, unique)
    file.save(filepath)
    image_url = f"/static/gallery/{unique}"
    title = sanitize_html(request.form.get('title'))
    description = sanitize_html(request.form.get('description'))
    img = Gallery(title=title, image_url=image_url, description=description, uploaded_by=session['user_id'])
    db.session.add(img)
    try:
        db.session.commit()
        flash('Image uploaded successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Gallery add error: {e}")
        flash('An error occurred.', 'danger')
    return redirect(url_for('admin_gallery'))

@app.route('/admin/gallery/delete/<int:id>', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_gallery_delete(id):
    img = Gallery.query.get_or_404(id)
    if img.image_url.startswith('/static/gallery/'):
        old_path = os.path.join('.', img.image_url.lstrip('/'))
        if os.path.exists(old_path):
            os.remove(old_path)
    db.session.delete(img)
    try:
        db.session.commit()
        flash('Image deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Gallery delete error: {e}")
        flash('An error occurred.', 'danger')
    return redirect(url_for('admin_gallery'))

# ---- Admin Timetable ----
@app.route('/admin/timetable')
@login_required
@role_required('admin')
def admin_timetable():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    slots_raw = db.session.query(TimetableSlot, Class, Subject, User).join(Class, TimetableSlot.class_id == Class.id).join(Subject, TimetableSlot.subject_id == Subject.id).outerjoin(User, TimetableSlot.teacher_id == User.id).all()
    slots = []
    for slot, cls, subj, user in slots_raw:
        slot.class_name = cls.name
        slot.subject_name = subj.name
        slot.teacher_name = user.full_name if user else None
        slots.append(slot)
    classes = Class.query.all()
    subjects = Subject.query.all()
    teachers = User.query.filter_by(role='teacher').all()
    return render_page('Manage Timetable', ADMIN_TIMETABLE_CONTENT, lang=lang, slots=slots, classes=classes, subjects=subjects, teachers=teachers)

@app.route('/admin/timetable/add', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_timetable_add():
    class_id = request.form.get('class_id')
    day = request.form.get('day')
    period = request.form.get('period')
    subject_id = request.form.get('subject_id')
    teacher_id = request.form.get('teacher_id')
    room = request.form.get('room')
    if not all([class_id, day, period, subject_id]):
        flash('Class, Day, Period, and Subject are required.', 'danger')
        return redirect(url_for('admin_timetable'))
    slot = TimetableSlot(
        class_id=class_id,
        day_of_week=int(day),
        period=int(period),
        subject_id=subject_id,
        teacher_id=teacher_id or None,
        room=room
    )
    db.session.add(slot)
    try:
        db.session.commit()
        flash('Timetable slot added.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Timetable add error: {e}")
        flash('An error occurred.', 'danger')
    return redirect(url_for('admin_timetable'))

@app.route('/admin/timetable/delete/<int:id>', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_timetable_delete(id):
    slot = TimetableSlot.query.get_or_404(id)
    db.session.delete(slot)
    try:
        db.session.commit()
        flash('Slot deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Timetable delete error: {e}")
        flash('An error occurred.', 'danger')
    return redirect(url_for('admin_timetable'))

# ---- Admin Attendance ----
@app.route('/admin/attendance')
@login_required
@role_required('admin')
def admin_attendance():
    lang = request.args.get('lang', session.get('lang', 'en'))
    session['lang'] = lang
    classes = Class.query.all()
    records_raw = db.session.query(Attendance, User).join(User, Attendance.student_id == User.id).order_by(Attendance.date.desc()).limit(100).all()
    records = []
    for att, user in records_raw:
        att.student_name = user.full_name
        records.append(att)
    return render_page('Manage Attendance', ADMIN_ATTENDANCE_CONTENT, lang=lang, classes=classes, records=records)

@app.route('/admin/attendance/mark', methods=['POST'])
@login_required
@role_required('admin')
@csrf_required
def admin_attendance_mark():
    class_id = request.form.get('class_id')
    date_str = request.form.get('date')
    if not class_id or not date_str:
        flash('Class and date are required.', 'danger')
        return redirect(url_for('admin_attendance'))
    date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
    students = User.query.filter_by(class_id=class_id, role='student').all()
    for student in students:
        status = request.form.get(f'status_{student.id}')
        remarks = request.form.get(f'remarks_{student.id}')
        if status:
            att = Attendance.query.filter_by(student_id=student.id, date=date_obj).first()
            if att:
                att.status = status
                att.remarks = remarks
                att.teacher_id = session['user_id']
            else:
                att = Attendance(
                    student_id=student.id,
                    class_id=class_id,
                    date=date_obj,
                    status=status,
                    remarks=remarks,
                    teacher_id=session['user_id']
                )
                db.session.add(att)
    try:
        db.session.commit()
        flash('Attendance marked successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Attendance mark error: {e}")
        flash('An error occurred.', 'danger')
    return redirect(url_for('admin_attendance'))

# =============================================================================
# SOCKETIO EVENTS
# =============================================================================
@socketio.on('group_message')
def handle_group_message(data):
    if 'user_id' not in session:
        return
    msg = sanitize_html(data.get('message', ''))
    if not msg:
        return
    chat = ChatMessage(sender_id=session['user_id'], room='group', message=msg)
    db.session.add(chat)
    try:
        db.session.commit()
        emit('group_message', {
            'id': chat.id,
            'sender_id': session['user_id'],
            'sender_name': session['full_name'],
            'message': msg,
            'timestamp': chat.timestamp.isoformat()
        }, broadcast=True)
    except Exception as e:
        db.session.rollback()
        logger.error(f"Group message error: {e}")

@socketio.on('private_message')
def handle_private_message(data):
    if 'user_id' not in session:
        return
    msg = sanitize_html(data.get('message', ''))
    receiver_id = data.get('receiver_id')
    if not msg or not receiver_id:
        return
    chat = ChatMessage(sender_id=session['user_id'], receiver_id=receiver_id, room='private_admin', message=msg)
    db.session.add(chat)
    try:
        db.session.commit()
        payload = {
            'id': chat.id,
            'sender_id': session['user_id'],
            'sender_name': session['full_name'],
            'message': msg,
            'timestamp': chat.timestamp.isoformat()
        }
        emit('private_message', payload, room=str(session['user_id']))
        emit('private_message', payload, room=str(receiver_id))
    except Exception as e:
        db.session.rollback()
        logger.error(f"Private message error: {e}")

@socketio.on('classroom_message')
def handle_classroom_message(data):
    if 'user_id' not in session:
        return
    room = data.get('room')
    msg = sanitize_html(data.get('message', ''))
    if not room or not msg:
        return
    chat = ChatMessage(sender_id=session['user_id'], room=room, message=msg)
    db.session.add(chat)
    try:
        db.session.commit()
        payload = {
            'id': chat.id,
            'sender_id': session['user_id'],
            'sender_name': session['full_name'],
            'room': room,
            'message': msg,
            'timestamp': chat.timestamp.isoformat()
        }
        emit('classroom_message', payload, room=room)
    except Exception as e:
        db.session.rollback()
        logger.error(f"Classroom message error: {e}")

@socketio.on('typing')
def handle_typing(data):
    if 'user_id' not in session:
        return
    room = data.get('room')
    receiver_id = data.get('receiver_id')
    if room:
        emit('typing', {
            'sender_id': session['user_id'],
            'sender_name': session['full_name'],
            'room': room,
            'receiver_id': receiver_id
        }, room=room if room != 'group' else None, broadcast=(room == 'group' or room is None))
    elif receiver_id:
        emit('typing', {
            'sender_id': session['user_id'],
            'sender_name': session['full_name'],
            'room': 'private_admin',
            'receiver_id': receiver_id
        }, room=str(session['user_id']))
        emit('typing', {
            'sender_id': session['user_id'],
            'sender_name': session['full_name'],
            'room': 'private_admin',
            'receiver_id': receiver_id
        }, room=str(receiver_id))

@socketio.on('mark_read')
def handle_mark_read(data):
    if 'user_id' not in session:
        return
    msg_id = data.get('message_id')
    if not msg_id:
        return
    chat = ChatMessage.query.get(msg_id)
    if chat and chat.receiver_id == session['user_id']:
        chat.read_at = datetime.datetime.utcnow()
        try:
            db.session.commit()
            emit('message_read', {'message_id': msg_id}, room=str(chat.sender_id))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Mark read error: {e}")

# =============================================================================
# RUN APPLICATION
# =============================================================================
if __name__ == '__main__':
    os.makedirs('static/uploads', exist_ok=True)
    os.makedirs('static/gallery', exist_ok=True)
    socketio.run(app, host='0.0.0.0', port=5000)

    
    
