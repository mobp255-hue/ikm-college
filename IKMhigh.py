#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
IKM High School - Complete School Management System
PostgreSQL, all templates, all routes, full CRUD, real-time chat.
Final production version – all fixes applied.
"""

import os
import datetime
import math
import secrets
import time
import re
from functools import wraps
from flask import (
    Flask, render_template_string, request, redirect, url_for,
    session, flash, abort, send_from_directory
)
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from markupsafe import escape

# ------------------------------
# App Configuration
# ------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))

# Use DATABASE_URL from environment, fallback for local testing
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    'postgresql://user:pass@localhost/db'  # local fallback
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = datetime.timedelta(hours=24)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = True   # HTTPS only on Render

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# School settings
SCHOOL_NAME = "IKM High School"
SCHOOL_SHORT = "IKM"
SCHOOL_LOGO = "https://i.imgur.com/Vdrn2CCh.jpg"
SCHOOL_ADDRESS = "123 Knowledge Street, Harare, Zimbabwe"
SCHOOL_PHONE = "+263 77 123 4567"
SCHOOL_EMAIL = "info@ikmhigh.ac.zw"
SCHOOL_MOTTO = "Knowledge · Integrity · Excellence"
ESTABLISHED = "2024"

# ------------------------------
# Database Models (PostgreSQL)
# ------------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(512), nullable=False)   # increased length
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)  # increased length
    role = db.Column(db.String(20), nullable=False, default='student')
    student_id = db.Column(db.String(20), unique=True, nullable=True)
    class_id = db.Column(db.Integer, db.ForeignKey('class.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

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

class Fee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    paid_date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(200))
    balance = db.Column(db.Float, default=0)

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

class SiteSetting(db.Model):
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(500))

# ------------------------------
# Database Initialization with Column Upgrades
# ------------------------------
def upgrade_columns():
    """Automatically alter column types to avoid truncation errors."""
    with app.app_context():
        inspector = inspect(db.engine)
        if 'user' in inspector.get_table_names():
            with db.engine.connect() as conn:
                # Check password_hash length
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
                    print("✅ Column types upgraded successfully.")

with app.app_context():
    upgrade_columns()
    db.create_all()
    # Create default admin if not exists
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', full_name='System Administrator', email='admin@ikmhigh.ac.zw', role='admin')
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
    # Set default logo & background
    if not SiteSetting.query.filter_by(key='logo_url').first():
        db.session.add(SiteSetting(key='logo_url', value=SCHOOL_LOGO))
    if not SiteSetting.query.filter_by(key='bg_url').first():
        db.session.add(SiteSetting(key='bg_url', value=''))
    db.session.commit()

# ------------------------------
# Helper Functions
# ------------------------------
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
                flash('You do not have permission to view this page.', 'danger')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated
    return decorator

def generate_csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(16)
    return session['csrf_token']

def validate_csrf_token(token):
    return token == session.get('csrf_token')

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def sanitize_html(content):
    return escape(content)

def validate_password(password):
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not re.search(r'[A-Z]', password):
        return False, "Must contain an uppercase letter."
    if not re.search(r'[a-z]', password):
        return False, "Must contain a lowercase letter."
    if not re.search(r'[0-9]', password):
        return False, "Must contain a number."
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return False, "Must contain a special character."
    return True, ""

def get_school_logo():
    setting = SiteSetting.query.filter_by(key='logo_url').first()
    return setting.value if setting else SCHOOL_LOGO

def update_school_logo(url):
    setting = SiteSetting.query.filter_by(key='logo_url').first()
    if setting:
        setting.value = url
    else:
        db.session.add(SiteSetting(key='logo_url', value=url))
    db.session.commit()

def get_background_url():
    setting = SiteSetting.query.filter_by(key='bg_url').first()
    return setting.value if setting else ''

def update_background_url(url):
    setting = SiteSetting.query.filter_by(key='bg_url').first()
    if setting:
        setting.value = url
    else:
        db.session.add(SiteSetting(key='bg_url', value=url))
    db.session.commit()

# ------------------------------
# Templates (all embedded)
# ------------------------------
BASE_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ SCHOOL_NAME }} - {{ title }}</title>
    <meta name="description" content="{{ SCHOOL_NAME }} - A school of Knowledge, Integrity, and Excellence. Providing quality education in Zimbabwe.">
    <meta name="keywords" content="school, education, Zimbabwe, {{ SCHOOL_NAME }}, high school, secondary, Harare">
    <meta name="author" content="{{ SCHOOL_NAME }}">
    <link rel="canonical" href="{{ request.url }}">
    <link rel="preconnect" href="https://cdn.jsdelivr.net">
    <link rel="preconnect" href="https://unpkg.com">
    <link rel="preconnect" href="https://cdnjs.cloudflare.com">
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
    <style>
        :root { --bg: #f0f4f8; --card-bg: rgba(255,255,255,0.85); --text: #1a1a2e; --primary: #0d6efd; --shadow: 0 8px 32px rgba(0,0,0,0.1); }
        body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); transition: background 0.3s, color 0.3s; }
        body.dark-mode { --bg: #1a1a2e; --card-bg: rgba(30,30,50,0.85); --text: #f0f4f8; }
        .glass { background: var(--card-bg); backdrop-filter: blur(12px); border: 1px solid rgba(255,255,255,0.2); box-shadow: var(--shadow); border-radius: 1rem; }
        .navbar-brand img { height: 50px; width: auto; }
        .hero { background: linear-gradient(135deg, #0d6efd, #0a58ca); color: white; padding: 80px 0; margin-bottom: 30px; position: relative; overflow: hidden; min-height: 350px; }
        .hero-bg { position: absolute; top: 0; left: 0; width: 100%; height: 100%; background-size: cover; background-position: center; opacity: 0.25; z-index: 0; }
        .hero .container { position: relative; z-index: 1; }
        .hero h1 { font-size: 3.5rem; font-weight: 700; text-shadow: 2px 2px 8px rgba(0,0,0,0.3); }
        .hero .lead { font-size: 1.5rem; text-shadow: 1px 1px 4px rgba(0,0,0,0.3); }
        .card-hover { transition: transform 0.3s ease, box-shadow 0.3s ease; }
        .card-hover:hover { transform: translateY(-5px); box-shadow: 0 10px 20px rgba(0,0,0,0.15); }
        .quote-container { background: rgba(255,255,255,0.85); backdrop-filter: blur(8px); border-radius: 15px; padding: 30px; text-align: center; color: #1a1a1a; border: 1px solid rgba(0,0,0,0.1); box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
        .quote-container blockquote { font-size: 1.4rem; font-style: italic; border-left: 4px solid #ffc107; padding-left: 20px; color: #1a1a1a; }
        .quote-container footer { background: transparent; color: #333; padding: 10px 0 0; }
        @keyframes fadeInUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
        .chat-box { height: 400px; overflow-y: auto; border: 1px solid #ddd; padding: 15px; background: #f9f9f9; border-radius: 8px; }
        .chat-msg { margin-bottom: 10px; }
        .chat-msg .user { font-weight: bold; }
        .chat-msg .time { font-size: 0.8rem; color: #888; }
        .chat-msg.self { background: #d1ecf1; padding: 5px 10px; border-radius: 10px; }
        .chat-msg.other { background: #f8d7da; padding: 5px 10px; border-radius: 10px; }
        .counter { font-size: 3rem; font-weight: 700; color: #ffc107; text-shadow: 2px 2px 4px rgba(0,0,0,0.2); }
        .counter-label { color: #f8f9fa; font-weight: 500; }
        .btn-toggle-dark { background: none; border: none; font-size: 1.5rem; color: white; cursor: pointer; }
        @media (max-width: 768px) { .hero h1 { font-size: 2.5rem; } .hero .lead { font-size: 1.2rem; } .counter { font-size: 2rem; } }
        .text-muted { color: #6c757d !important; }
        a { color: #0d6efd; }
        a:hover { color: #0a58ca; }
        a:focus-visible, button:focus-visible, input:focus-visible { outline: 3px solid #0d6efd; outline-offset: 2px; }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-primary sticky-top">
        <div class="container">
            <a class="navbar-brand" href="{{ url_for('home') }}">
                <img src="{{ SCHOOL_LOGO }}" alt="{{ SCHOOL_NAME }} Logo" height="50" width="50">
                {{ SCHOOL_NAME }}
            </a>
            <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarMain" aria-label="Toggle navigation">
                <span class="navbar-toggler-icon"></span>
            </button>
            <div class="collapse navbar-collapse" id="navbarMain">
                <ul class="navbar-nav ms-auto">
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('home') }}">Home</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('about') }}">About</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('academics') }}">Academics</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('student_life') }}">Student Life</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('admissions') }}">Admissions</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('news_list') }}">News</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('gallery') }}">Gallery</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('contact') }}">Contact</a></li>
                    <li class="nav-item"><a class="nav-link" href="{{ url_for('classroom') }}">Classrooms</a></li>
                    {% if session.user_id %}
                        <li class="nav-item dropdown">
                            <a class="nav-link dropdown-toggle" href="#" id="userDropdown" role="button" data-bs-toggle="dropdown" aria-expanded="false">
                                {{ session.full_name }}
                            </a>
                            <ul class="dropdown-menu dropdown-menu-end" aria-labelledby="userDropdown">
                                <li><a class="dropdown-item" href="{{ url_for('dashboard') }}">Dashboard</a></li>
                                <li><a class="dropdown-item" href="{{ url_for('chat') }}">Chat</a></li>
                                <li><hr class="dropdown-divider"></li>
                                <li><a class="dropdown-item" href="{{ url_for('logout') }}">Logout</a></li>
                            </ul>
                        </li>
                    {% else %}
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('login') }}">Login</a></li>
                    {% endif %}
                    <li class="nav-item">
                        <button class="btn-toggle-dark" id="darkModeToggle" aria-label="Toggle dark mode">
                            <i class="bi bi-moon-fill"></i>
                        </button>
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

    <main>
        {{ content | safe }}
    </main>

    <footer class="mt-5 py-4 bg-dark text-white">
        <div class="container">
            <div class="row">
                <div class="col-md-4">
                    <h5>{{ SCHOOL_NAME }}</h5>
                    <p>{{ SCHOOL_MOTTO }}</p>
                    <p>{{ SCHOOL_ADDRESS }}<br>Phone: {{ SCHOOL_PHONE }}<br>Email: <a href="mailto:{{ SCHOOL_EMAIL }}" class="text-white">{{ SCHOOL_EMAIL }}</a></p>
                </div>
                <div class="col-md-4">
                    <h5>Quick Links</h5>
                    <ul class="list-unstyled">
                        <li><a href="{{ url_for('about') }}" class="text-white">About Us</a></li>
                        <li><a href="{{ url_for('academics') }}" class="text-white">Academics</a></li>
                        <li><a href="{{ url_for('student_life') }}" class="text-white">Student Life</a></li>
                        <li><a href="{{ url_for('admissions') }}" class="text-white">Admissions</a></li>
                        <li><a href="{{ url_for('news_list') }}" class="text-white">News & Events</a></li>
                        <li><a href="{{ url_for('gallery') }}" class="text-white">Gallery</a></li>
                    </ul>
                </div>
                <div class="col-md-4">
                    <h5>Follow Us</h5>
                    <div class="footer-social">
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

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
    <script src="https://unpkg.com/aos@2.3.1/dist/aos.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/lightbox2/2.11.4/js/lightbox.min.js"></script>
    <script>
        AOS.init({ duration: 800, once: true });
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
        });

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

        function animateCounters() {
            const counters = document.querySelectorAll('.counter');
            counters.forEach(counter => {
                const target = parseInt(counter.getAttribute('data-target'));
                if (!target) return;
                const increment = Math.ceil(target / 80);
                let current = 0;
                const updateCounter = setInterval(() => {
                    current += increment;
                    if (current >= target) {
                        counter.textContent = target;
                        clearInterval(updateCounter);
                    } else {
                        counter.textContent = current;
                    }
                }, 25);
            });
        }

        if ('IntersectionObserver' in window) {
            const observer = new IntersectionObserver((entries) => {
                entries.forEach(entry => {
                    if (entry.isIntersecting) {
                        animateCounters();
                        observer.unobserve(entry.target);
                    }
                });
            }, { threshold: 0.3 });
            const statsSection = document.getElementById('stats-section');
            if (statsSection) {
                observer.observe(statsSection);
            }
        } else {
            document.addEventListener('DOMContentLoaded', animateCounters);
        }
    </script>
</body>
</html>
'''

# ----- CONTENT TEMPLATES -----
HOME_CONTENT = '''
{% set bg_url = get_background_url() %}
<section class="hero" style="{% if bg_url %}background: none;{% else %}background: linear-gradient(135deg, #0d6efd, #0a58ca);{% endif %}">
    {% if bg_url %}
    <div class="hero-bg" style="background-image: url('{{ bg_url }}'); opacity: 1;"></div>
    {% endif %}
    <div class="container text-center" data-aos="fade-up">
        <h1>Welcome to {{ SCHOOL_NAME }}</h1>
        <p class="lead">{{ SCHOOL_MOTTO }}</p>
        <p>Established {{ ESTABLISHED }}</p>
        <a href="{{ url_for('admissions') }}" class="btn btn-warning btn-lg mt-3"><i class="bi bi-pencil-square"></i> Apply Now</a>
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

<section class="bg-light py-5">
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
                        <a href="{{ url_for('news_detail', id=article.id) }}" class="btn btn-outline-primary">Read More</a>
                    </div>
                    <div class="card-footer text-muted small">
                        {{ article.date_posted.strftime('%Y-%m-%d') }}
                    </div>
                </div>
            </div>
            {% else %}
            <p class="text-center">No news yet. Check back later!</p>
            {% endfor %}
        </div>
    </div>
</section>
'''

ABOUT_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">About {{ SCHOOL_NAME }}</h1>
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
    <h1 data-aos="fade-right">Academics</h1>
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
    <h1 data-aos="fade-right">Student Life</h1>
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
    <h1 data-aos="fade-right">Admissions</h1>
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
                        <button type="submit" class="btn btn-primary">Submit Application</button>
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
    <h1 data-aos="fade-right">News & Events</h1>
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
                    <a href="{{ url_for('news_detail', id=article.id) }}" class="btn btn-outline-primary">Read More</a>
                </div>
                <div class="card-footer text-muted small">{{ article.date_posted.strftime('%Y-%m-%d') }}</div>
            </div>
        </div>
        {% else %}
        <p>No news articles yet.</p>
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
            <li class="breadcrumb-item"><a href="{{ url_for('news_list') }}">News</a></li>
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
    <h1 data-aos="fade-right">Photo Gallery</h1>
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
        <p class="text-center">No images in gallery yet.</p>
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
    <h1 data-aos="fade-right">Contact Us</h1>
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
                        <button type="submit" class="btn btn-primary">Send</button>
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
                    <h4><i class="bi bi-box-arrow-in-right"></i> Login to {{ SCHOOL_SHORT }}</h4>
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
                        <button type="submit" class="btn btn-primary w-100"><i class="bi bi-key"></i> Login</button>
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
    <h1 data-aos="fade-right">Welcome, {{ session.full_name }}</h1>
    <div class="row mt-4">
        <div class="col-md-3" data-aos="fade-up">
            <div class="card text-white bg-primary mb-3"><div class="card-body"><h5 class="card-title">My Profile</h5><p class="card-text">{{ session.full_name }}<br>{{ session.role|capitalize }}</p><a href="{{ url_for('profile') }}" class="btn btn-light btn-sm">View</a></div></div>
        </div>
        {% if session.role == 'student' %}
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="100">
            <div class="card text-white bg-success mb-3"><div class="card-body"><h5 class="card-title">My Results</h5><p class="card-text">View your academic performance.</p><a href="{{ url_for('student_results') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="200">
            <div class="card text-white bg-warning mb-3"><div class="card-body"><h5 class="card-title">Fee Account</h5><p class="card-text">Check your fee balance and history.</p><a href="{{ url_for('student_fees') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
        {% endif %}
        {% if session.role == 'admin' or session.role == 'teacher' %}
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="300">
            <div class="card text-white bg-danger mb-3"><div class="card-body"><h5 class="card-title">Admin Panel</h5><p class="card-text">Manage the school system.</p><a href="{{ url_for('admin_dashboard') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
        {% endif %}
        {% if session.role == 'admin' %}
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="400">
            <div class="card text-white bg-secondary mb-3"><div class="card-body"><h5 class="card-title">Change Password</h5><p class="card-text">Update your password securely.</p><a href="{{ url_for('admin_change_password') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="500">
            <div class="card text-white bg-info mb-3"><div class="card-body"><h5 class="card-title">Upload Logo</h5><p class="card-text">Change school logo.</p><a href="{{ url_for('admin_upload_logo') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="600">
            <div class="card text-white bg-dark mb-3"><div class="card-body"><h5 class="card-title">Upload Background</h5><p class="card-text">Change site background.</p><a href="{{ url_for('admin_upload_background') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
        {% endif %}
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="700">
            <div class="card text-white bg-dark mb-3"><div class="card-body"><h5 class="card-title">Chat</h5><p class="card-text">Group or private messages.</p><a href="{{ url_for('chat') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
        <div class="col-md-3" data-aos="fade-up" data-aos-delay="800">
            <div class="card text-white bg-primary mb-3"><div class="card-body"><h5 class="card-title">Classrooms</h5><p class="card-text">Join your class chat room.</p><a href="{{ url_for('classroom') }}" class="btn btn-light btn-sm">Go</a></div></div>
        </div>
    </div>
</div>
'''

STUDENT_RESULTS_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">My Results</h1>
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
        <p>No results posted yet.</p>
        {% endif %}
    </div>
</div>
'''

STUDENT_FEES_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">Fee Account</h1>
    <div class="mt-4" data-aos="fade-up">
        <div class="alert alert-info"><strong>Current Balance:</strong> ${{ balance }}</div>
        {% if fees %}
        <table class="table table-striped">
            <thead class="table-primary"><tr><th>Date</th><th>Description</th><th>Amount</th><th>Balance</th></tr></thead>
            <tbody>
                {% for f in fees %}
                <tr><td>{{ f.paid_date.strftime('%Y-%m-%d') }}</td><td>{{ f.description }}</td><td>${{ f.amount }}</td><td>${{ f.balance }}</td></tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}
        <p>No fee records.</p>
        {% endif %}
    </div>
</div>
'''

PROFILE_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">My Profile</h1>
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
    <h1 data-aos="fade-right">Chat</h1>
    <div class="row mt-4">
        <div class="col-md-6">
            <div class="card glass">
                <div class="card-header bg-primary text-white">Group Chat</div>
                <div class="card-body">
                    <div id="group-chat-box" class="chat-box">
                        {% for msg in group_messages %}
                        <div class="chat-msg {% if msg.sender_id == session.user_id %}self{% else %}other{% endif %}">
                            <span class="user">{{ msg.sender_name }}</span>
                            <span class="time">{{ msg.timestamp.strftime('%Y-%m-%d %H:%M') }}</span>
                            <p>{{ msg.message }}</p>
                        </div>
                        {% endfor %}
                    </div>
                    <form id="group-chat-form" class="mt-2">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <div class="input-group">
                            <input type="text" class="form-control" id="group-msg" placeholder="Type a message..." required>
                            <button class="btn btn-primary" type="submit">Send</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
        <div class="col-md-6">
            <div class="card glass">
                <div class="card-header bg-success text-white">Private Chat with Admin</div>
                <div class="card-body">
                    <div id="private-chat-box" class="chat-box">
                        {% for msg in private_messages %}
                        <div class="chat-msg {% if msg.sender_id == session.user_id %}self{% else %}other{% endif %}">
                            <span class="user">{{ msg.sender_name }}</span>
                            <span class="time">{{ msg.timestamp.strftime('%Y-%m-%d %H:%M') }}</span>
                            <p>{{ msg.message }}</p>
                        </div>
                        {% endfor %}
                    </div>
                    <form id="private-chat-form" class="mt-2">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <div class="input-group">
                            <input type="text" class="form-control" id="private-msg" placeholder="Message admin..." required>
                            <button class="btn btn-success" type="submit">Send</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    </div>
</div>
<script>
    var socket = io();
    var userId = "{{ session.user_id }}";

    socket.on('group_message', function(data) {
        var box = document.getElementById('group-chat-box');
        var div = document.createElement('div');
        div.className = 'chat-msg ' + (data.sender_id == userId ? 'self' : 'other');
        div.innerHTML = '<span class="user">' + data.sender_name + '</span> <span class="time">' + data.timestamp.slice(0,16) + '</span><p>' + data.message + '</p>';
        box.appendChild(div);
        box.scrollTop = box.scrollHeight;
    });

    socket.on('private_message', function(data) {
        var box = document.getElementById('private-chat-box');
        var div = document.createElement('div');
        div.className = 'chat-msg ' + (data.sender_id == userId ? 'self' : 'other');
        div.innerHTML = '<span class="user">' + data.sender_name + '</span> <span class="time">' + data.timestamp.slice(0,16) + '</span><p>' + data.message + '</p>';
        box.appendChild(div);
        box.scrollTop = box.scrollHeight;
    });

    document.getElementById('group-chat-form').addEventListener('submit', function(e) {
        e.preventDefault();
        var input = document.getElementById('group-msg');
        var msg = input.value.trim();
        if (msg) {
            socket.emit('group_message', { message: msg });
            input.value = '';
        }
    });

    document.getElementById('private-chat-form').addEventListener('submit', function(e) {
        e.preventDefault();
        var input = document.getElementById('private-msg');
        var msg = input.value.trim();
        if (msg) {
            socket.emit('private_message', { message: msg, receiver_id: 1 });
            input.value = '';
        }
    });
</script>
'''

CLASSROOM_CONTENT = '''
<div class="container my-5">
    <h1 data-aos="fade-right">Classroom Chats</h1>
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
                <div class="card-header bg-primary text-white">Select a class above to start chatting</div>
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
                    <a href="{{ url_for('classroom') }}" class="btn btn-light btn-sm float-end">Back</a>
                </div>
                <div class="card-body">
                    <div id="classroom-chat-box" class="chat-box">
                        {% for msg in messages %}
                        <div class="chat-msg {% if msg.sender_id == session.user_id %}self{% else %}other{% endif %}">
                            <span class="user">{{ msg.sender_name }}</span>
                            <span class="time">{{ msg.timestamp.strftime('%Y-%m-%d %H:%M') }}</span>
                            <p>{{ msg.message }}</p>
                        </div>
                        {% endfor %}
                    </div>
                    <form id="classroom-chat-form" class="mt-2">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <div class="input-group">
                            <input type="text" class="form-control" id="classroom-msg" placeholder="Type a message..." required>
                            <button class="btn btn-primary" type="submit">Send</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    </div>
</div>
<script>
    var socket = io();
    var userId = "{{ session.user_id }}";
    var room = "{{ room }}";

    socket.emit('join_room', { room: room });

    socket.on('classroom_message', function(data) {
        if (data.room !== room) return;
        var box = document.getElementById('classroom-chat-box');
        var div = document.createElement('div');
        div.className = 'chat-msg ' + (data.sender_id == userId ? 'self' : 'other');
        div.innerHTML = '<span class="user">' + data.sender_name + '</span> <span class="time">' + data.timestamp.slice(0,16) + '</span><p>' + data.message + '</p>';
        box.appendChild(div);
        box.scrollTop = box.scrollHeight;
    });

    document.getElementById('classroom-chat-form').addEventListener('submit', function(e) {
        e.preventDefault();
        var input = document.getElementById('classroom-msg');
        var msg = input.value.trim();
        if (msg) {
            socket.emit('classroom_message', { room: room, message: msg });
            input.value = '';
        }
    });
</script>
'''

# ------------------------------
# ADMIN TEMPLATES (all CRUD)
# ------------------------------
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
                    <div class="col-md-3"><select class="form-select" name="student_id" required><option value="">Student</option>{% for s in students %}<option value="{{ s.id }}">{{ s.full_name }}</option>{% endfor %}</select></div>
                    <div class="col-md-2"><input type="number" step="0.01" class="form-control" name="amount" placeholder="Amount" required></div>
                    <div class="col-md-2"><input type="date" class="form-control" name="paid_date" required></div>
                    <div class="col-md-3"><input type="text" class="form-control" name="description" placeholder="Description"></div>
                    <div class="col-md-2"><button type="submit" class="btn btn-primary">Add</button></div>
                </div>
            </form>
        </div>
    </div>
    <table class="table table-striped">
        <thead><tr><th>Student</th><th>Amount</th><th>Paid Date</th><th>Description</th><th>Balance</th><th>Actions</th></tr></thead>
        <tbody>
        {% for f in fees %}
        <tr>
            <td>{{ f.student_name }}</td><td>${{ f.amount }}</td><td>{{ f.paid_date.strftime('%Y-%m-%d') }}</td><td>{{ f.description or '-' }}</td><td>${{ f.balance }}</td>
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

# ------------------------------
# Render Helper
# ------------------------------
def render_page(title, content_template, **kwargs):
    kwargs.setdefault('csrf_token', generate_csrf_token)
    kwargs.setdefault('get_background_url', get_background_url)
    content_rendered = render_template_string(content_template, **kwargs)
    logo = get_school_logo()
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
        SCHOOL_LOGO=logo,
        request=request,
        url_for=url_for,
        session=session,
        csrf_token=generate_csrf_token
    )

# ------------------------------
# Routes: Public
# ------------------------------
@app.route('/')
def home():
    news = News.query.order_by(News.date_posted.desc()).limit(3).all()
    return render_page('Home', HOME_CONTENT, news=news)

@app.route('/about')
def about():
    return render_page('About', ABOUT_CONTENT)

@app.route('/academics')
def academics():
    return render_page('Academics', ACADEMICS_CONTENT)

@app.route('/student-life')
def student_life():
    return render_page('Student Life', STUDENT_LIFE_CONTENT)

@app.route('/admissions', methods=['GET', 'POST'])
def admissions():
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
            db.session.commit()
            flash('Application submitted successfully! We will contact you soon.', 'success')
            return redirect(url_for('admissions'))
        else:
            flash('Please fill in all fields.', 'danger')
    return render_page('Admissions', ADMISSIONS_CONTENT)

@app.route('/news')
def news_list():
    page = request.args.get('page', 1, type=int)
    per_page = 6
    total = News.query.count()
    total_pages = math.ceil(total / per_page)
    offset = (page - 1) * per_page
    news = News.query.order_by(News.date_posted.desc()).offset(offset).limit(per_page).all()
    return render_page('News', NEWS_LIST_CONTENT, news=news, current_page=page, total_pages=total_pages)

@app.route('/news/<int:id>')
def news_detail(id):
    article = News.query.get_or_404(id)
    return render_page('News Detail', NEWS_DETAIL_CONTENT, article=article)

@app.route('/gallery')
def gallery():
    page = request.args.get('page', 1, type=int)
    per_page = 9
    total = Gallery.query.count()
    total_pages = math.ceil(total / per_page)
    offset = (page - 1) * per_page
    images = Gallery.query.order_by(Gallery.uploaded_at.desc()).offset(offset).limit(per_page).all()
    return render_page('Gallery', GALLERY_CONTENT, images=images, current_page=page, total_pages=total_pages)

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        if not validate_csrf_token(request.form.get('csrf_token')):
            flash('Invalid CSRF token.', 'danger')
            return redirect(url_for('contact'))
        flash('Your message has been sent. We will get back to you soon.', 'success')
        return redirect(url_for('contact'))
    return render_page('Contact', CONTACT_CONTENT)

@app.route('/login', methods=['GET', 'POST'])
def login():
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
    return render_page('Login', LOGIN_CONTENT)

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out.', 'info')
    return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    return render_page('Dashboard', DASHBOARD_CONTENT)

@app.route('/profile')
@login_required
def profile():
    user = User.query.get(session['user_id'])
    class_name = ''
    if user.class_id:
        cls = Class.query.get(user.class_id)
        class_name = cls.name if cls else ''
    return render_page('Profile', PROFILE_CONTENT, user=user, class_name=class_name)

@app.route('/student/results')
@login_required
def student_results():
    if session['role'] != 'student':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    results = db.session.query(Result, Subject).join(Subject, Result.subject_id == Subject.id).filter(Result.student_id == session['user_id']).all()
    return render_page('My Results', STUDENT_RESULTS_CONTENT, results=results)

@app.route('/student/fees')
@login_required
def student_fees():
    if session['role'] != 'student':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    fees = Fee.query.filter_by(student_id=session['user_id']).order_by(Fee.paid_date.desc()).all()
    balance = fees[0].balance if fees else 0
    return render_page('My Fees', STUDENT_FEES_CONTENT, fees=fees, balance=balance)

@app.route('/chat')
@login_required
def chat():
    group_msgs = ChatMessage.query.filter_by(room='group').order_by(ChatMessage.timestamp.desc()).limit(50).all()
    group_msgs = list(reversed(group_msgs))
    private_msgs = ChatMessage.query.filter(
        ((ChatMessage.sender_id == session['user_id']) & (ChatMessage.receiver_id == 1)) |
        ((ChatMessage.sender_id == 1) & (ChatMessage.receiver_id == session['user_id']))
    ).order_by(ChatMessage.timestamp.desc()).limit(50).all()
    private_msgs = list(reversed(private_msgs))
    return render_page('Chat', CHAT_CONTENT, group_messages=group_msgs, private_messages=private_msgs)

@app.route('/classroom')
@login_required
def classroom():
    classes = Class.query.all()
    for c in classes:
        if c.teacher_id:
            teacher = User.query.get(c.teacher_id)
            c.teacher_name = teacher.full_name if teacher else ''
        else:
            c.teacher_name = ''
    return render_page('Classroom', CLASSROOM_CONTENT, classes=classes)

@app.route('/classroom/<room>')
@login_required
def classroom_room(room):
    messages = ChatMessage.query.filter_by(room=room).order_by(ChatMessage.timestamp.desc()).limit(50).all()
    messages = list(reversed(messages))
    return render_page('Classroom Room', CLASSROOM_ROOM_CONTENT, room=room, messages=messages)

# ------------------------------
# Routes: Admin (all CRUD)
# ------------------------------
@app.route('/admin')
@login_required
@role_required('admin')
def admin_dashboard():
    stats = {
        'students': User.query.filter_by(role='student').count(),
        'teachers': User.query.filter_by(role='teacher').count(),
        'applications': Application.query.count(),
        'news': News.query.count(),
    }
    return render_page('Admin Dashboard', ADMIN_DASHBOARD_CONTENT, stats=stats)

@app.route('/admin/change-password', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_change_password():
    if request.method == 'POST':
        if not validate_csrf_token(request.form.get('csrf_token')):
            flash('Invalid CSRF token.', 'danger')
            return redirect(url_for('admin_change_password'))
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
        db.session.commit()
        flash('Password changed successfully.', 'success')
        return redirect(url_for('admin_dashboard'))
    return render_page('Change Password', ADMIN_CHANGE_PASSWORD_CONTENT)

@app.route('/admin/upload-logo', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_upload_logo():
    if request.method == 'POST':
        if not validate_csrf_token(request.form.get('csrf_token')):
            flash('Invalid CSRF token.', 'danger')
            return redirect(url_for('admin_upload_logo'))
        if 'logo' not in request.files:
            flash('No file selected.', 'danger')
            return redirect(url_for('admin_upload_logo'))
        file = request.files['logo']
        if file.filename == '' or not allowed_file(file.filename):
            flash('Invalid file type. Please upload PNG, JPG, JPEG, GIF, or WEBP.', 'danger')
            return redirect(url_for('admin_upload_logo'))
        filename = secure_filename(file.filename)
        upload_dir = os.path.join(app.static_folder, 'uploads')
        os.makedirs(upload_dir, exist_ok=True)
        unique = f"logo_{int(time.time())}_{filename}"
        filepath = os.path.join(upload_dir, unique)
        file.save(filepath)
        logo_url = f"/static/uploads/{unique}"
        update_school_logo(logo_url)
        flash('Logo updated successfully.', 'success')
        return redirect(url_for('admin_dashboard'))
    logo_url = get_school_logo()
    return render_page('Upload Logo', ADMIN_LOGO_CONTENT, logo_url=logo_url)

@app.route('/admin/upload-background', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_upload_background():
    if request.method == 'POST':
        if not validate_csrf_token(request.form.get('csrf_token')):
            flash('Invalid CSRF token.', 'danger')
            return redirect(url_for('admin_upload_background'))
        if 'background' not in request.files:
            flash('No file selected.', 'danger')
            return redirect(url_for('admin_upload_background'))
        file = request.files['background']
        if file.filename == '' or not allowed_file(file.filename):
            flash('Invalid file type. Please upload PNG, JPG, JPEG, GIF, or WEBP.', 'danger')
            return redirect(url_for('admin_upload_background'))
        filename = secure_filename(file.filename)
        upload_dir = os.path.join(app.static_folder, 'uploads')
        os.makedirs(upload_dir, exist_ok=True)
        unique = f"bg_{int(time.time())}_{filename}"
        filepath = os.path.join(upload_dir, unique)
        file.save(filepath)
        bg_url = f"/static/uploads/{unique}"
        update_background_url(bg_url)
        flash('Background image updated successfully.', 'success')
        return redirect(url_for('admin_dashboard'))
    bg_url = get_background_url()
    return render_page('Upload Background', ADMIN_BG_CONTENT, bg_url=bg_url)

# ---------- Admin Users ----------
@app.route('/admin/users')
@login_required
@role_required('admin')
def admin_users():
    users = User.query.all()
    return render_page('Manage Users', ADMIN_USERS_CONTENT, users=users)

@app.route('/admin/users/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_users_add():
    if request.method == 'POST':
        if not validate_csrf_token(request.form.get('csrf_token')):
            flash('Invalid CSRF token.', 'danger')
            return redirect(url_for('admin_users_add'))
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
        except:
            db.session.rollback()
            flash('Username or email already exists.', 'danger')
    classes = Class.query.all()
    return render_page('Add User', ADMIN_USERS_ADD_CONTENT, classes=classes)

@app.route('/admin/users/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_users_edit(id):
    user = User.query.get_or_404(id)
    if request.method == 'POST':
        if not validate_csrf_token(request.form.get('csrf_token')):
            flash('Invalid CSRF token.', 'danger')
            return redirect(url_for('admin_users_edit', id=id))
        user.full_name = sanitize_html(request.form.get('full_name'))
        user.email = sanitize_html(request.form.get('email'))
        user.role = sanitize_html(request.form.get('role'))
        user.student_id = sanitize_html(request.form.get('student_id'))
        user.class_id = request.form.get('class_id')
        db.session.commit()
        flash('User updated.', 'success')
        return redirect(url_for('admin_users'))
    classes = Class.query.all()
    return render_page('Edit User', ADMIN_USERS_EDIT_CONTENT, user=user, classes=classes)

@app.route('/admin/users/delete/<int:id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_users_delete(id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        flash('Invalid CSRF token.', 'danger')
        return redirect(url_for('admin_users'))
    user = User.query.get_or_404(id)
    db.session.delete(user)
    db.session.commit()
    flash('User deleted.', 'success')
    return redirect(url_for('admin_users'))

# ---------- Admin Classes ----------
@app.route('/admin/classes')
@login_required
@role_required('admin')
def admin_classes():
    classes = Class.query.all()
    for c in classes:
        if c.teacher_id:
            teacher = User.query.get(c.teacher_id)
            c.teacher_name = teacher.full_name if teacher else ''
        else:
            c.teacher_name = ''
    return render_page('Manage Classes', ADMIN_CLASSES_CONTENT, classes=classes)

@app.route('/admin/classes/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_classes_add():
    if request.method == 'POST':
        if not validate_csrf_token(request.form.get('csrf_token')):
            flash('Invalid CSRF token.', 'danger')
            return redirect(url_for('admin_classes_add'))
        name = sanitize_html(request.form.get('name'))
        academic_year = sanitize_html(request.form.get('academic_year'))
        teacher_id = request.form.get('teacher_id')
        if not name or not academic_year:
            flash('Name and Academic Year are required.', 'danger')
            return redirect(url_for('admin_classes_add'))
        cls = Class(name=name, academic_year=academic_year, teacher_id=teacher_id or None)
        db.session.add(cls)
        db.session.commit()
        flash('Class added.', 'success')
        return redirect(url_for('admin_classes'))
    teachers = User.query.filter_by(role='teacher').all()
    return render_page('Add Class', ADMIN_CLASSES_ADD_CONTENT, teachers=teachers)

@app.route('/admin/classes/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_classes_edit(id):
    cls = Class.query.get_or_404(id)
    if request.method == 'POST':
        if not validate_csrf_token(request.form.get('csrf_token')):
            flash('Invalid CSRF token.', 'danger')
            return redirect(url_for('admin_classes_edit', id=id))
        cls.name = sanitize_html(request.form.get('name'))
        cls.academic_year = sanitize_html(request.form.get('academic_year'))
        cls.teacher_id = request.form.get('teacher_id') or None
        db.session.commit()
        flash('Class updated.', 'success')
        return redirect(url_for('admin_classes'))
    teachers = User.query.filter_by(role='teacher').all()
    return render_page('Edit Class', ADMIN_CLASSES_EDIT_CONTENT, cls=cls, teachers=teachers)

@app.route('/admin/classes/delete/<int:id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_classes_delete(id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        flash('Invalid CSRF token.', 'danger')
        return redirect(url_for('admin_classes'))
    cls = Class.query.get_or_404(id)
    db.session.delete(cls)
    db.session.commit()
    flash('Class deleted.', 'success')
    return redirect(url_for('admin_classes'))

# ---------- Admin Subjects ----------
@app.route('/admin/subjects')
@login_required
@role_required('admin')
def admin_subjects():
    subjects = Subject.query.all()
    return render_page('Manage Subjects', ADMIN_SUBJECTS_CONTENT, subjects=subjects)

@app.route('/admin/subjects/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_subjects_add():
    if request.method == 'POST':
        if not validate_csrf_token(request.form.get('csrf_token')):
            flash('Invalid CSRF token.', 'danger')
            return redirect(url_for('admin_subjects_add'))
        name = sanitize_html(request.form.get('name'))
        code = sanitize_html(request.form.get('code'))
        if not name or not code:
            flash('Name and Code are required.', 'danger')
            return redirect(url_for('admin_subjects_add'))
        try:
            subject = Subject(name=name, code=code)
            db.session.add(subject)
            db.session.commit()
            flash('Subject added.', 'success')
            return redirect(url_for('admin_subjects'))
        except:
            db.session.rollback()
            flash('Subject code already exists.', 'danger')
    return render_page('Add Subject', ADMIN_SUBJECTS_ADD_CONTENT)

@app.route('/admin/subjects/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_subjects_edit(id):
    subject = Subject.query.get_or_404(id)
    if request.method == 'POST':
        if not validate_csrf_token(request.form.get('csrf_token')):
            flash('Invalid CSRF token.', 'danger')
            return redirect(url_for('admin_subjects_edit', id=id))
        subject.name = sanitize_html(request.form.get('name'))
        subject.code = sanitize_html(request.form.get('code'))
        try:
            db.session.commit()
            flash('Subject updated.', 'success')
            return redirect(url_for('admin_subjects'))
        except:
            db.session.rollback()
            flash('Subject code already exists.', 'danger')
    return render_page('Edit Subject', ADMIN_SUBJECTS_EDIT_CONTENT, subject=subject)

@app.route('/admin/subjects/delete/<int:id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_subjects_delete(id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        flash('Invalid CSRF token.', 'danger')
        return redirect(url_for('admin_subjects'))
    subject = Subject.query.get_or_404(id)
    db.session.delete(subject)
    db.session.commit()
    flash('Subject deleted.', 'success')
    return redirect(url_for('admin_subjects'))

# ---------- Admin Results ----------
@app.route('/admin/results')
@login_required
@role_required('admin')
def admin_results():
    results = db.session.query(Result, User, Subject).join(User, Result.student_id == User.id).join(Subject, Result.subject_id == Subject.id).order_by(Result.year.desc(), Result.term.desc()).all()
    students = User.query.filter_by(role='student').all()
    subjects = Subject.query.all()
    return render_page('Manage Results', ADMIN_RESULTS_CONTENT, results=results, students=students, subjects=subjects)

@app.route('/admin/results/add', methods=['POST'])
@login_required
@role_required('admin')
def admin_results_add():
    if not validate_csrf_token(request.form.get('csrf_token')):
        flash('Invalid CSRF token.', 'danger')
        return redirect(url_for('admin_results'))
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
    db.session.commit()
    flash('Result added.', 'success')
    return redirect(url_for('admin_results'))

@app.route('/admin/results/delete/<int:id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_results_delete(id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        flash('Invalid CSRF token.', 'danger')
        return redirect(url_for('admin_results'))
    result = Result.query.get_or_404(id)
    db.session.delete(result)
    db.session.commit()
    flash('Result deleted.', 'success')
    return redirect(url_for('admin_results'))

# ---------- Admin Fees ----------
@app.route('/admin/fees')
@login_required
@role_required('admin')
def admin_fees():
    fees = db.session.query(Fee, User).join(User, Fee.student_id == User.id).order_by(Fee.paid_date.desc()).all()
    students = User.query.filter_by(role='student').all()
    return render_page('Manage Fees', ADMIN_FEES_CONTENT, fees=fees, students=students)

@app.route('/admin/fees/add', methods=['POST'])
@login_required
@role_required('admin')
def admin_fees_add():
    if not validate_csrf_token(request.form.get('csrf_token')):
        flash('Invalid CSRF token.', 'danger')
        return redirect(url_for('admin_fees'))
    student_id = request.form.get('student_id')
    amount = request.form.get('amount')
    paid_date = request.form.get('paid_date')
    description = sanitize_html(request.form.get('description'))
    if not all([student_id, amount, paid_date]):
        flash('Student, Amount, and Date are required.', 'danger')
        return redirect(url_for('admin_fees'))
    # Calculate new balance
    last_fee = Fee.query.filter_by(student_id=student_id).order_by(Fee.id.desc()).first()
    current_balance = last_fee.balance if last_fee else 0
    new_balance = current_balance - float(amount)
    fee = Fee(student_id=student_id, amount=amount, paid_date=datetime.datetime.strptime(paid_date, '%Y-%m-%d').date(), description=description, balance=new_balance)
    db.session.add(fee)
    db.session.commit()
    flash('Fee record added.', 'success')
    return redirect(url_for('admin_fees'))

@app.route('/admin/fees/delete/<int:id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_fees_delete(id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        flash('Invalid CSRF token.', 'danger')
        return redirect(url_for('admin_fees'))
    fee = Fee.query.get_or_404(id)
    db.session.delete(fee)
    db.session.commit()
    flash('Fee record deleted.', 'success')
    return redirect(url_for('admin_fees'))

# ---------- Admin News ----------
@app.route('/admin/news')
@login_required
@role_required('admin')
def admin_news():
    news = News.query.order_by(News.date_posted.desc()).all()
    return render_page('Manage News', ADMIN_NEWS_CONTENT, news=news)

@app.route('/admin/news/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_news_add():
    if request.method == 'POST':
        if not validate_csrf_token(request.form.get('csrf_token')):
            flash('Invalid CSRF token.', 'danger')
            return redirect(url_for('admin_news_add'))
        title = sanitize_html(request.form.get('title'))
        content = request.form.get('content')
        image_url = sanitize_html(request.form.get('image_url'))
        video_url = sanitize_html(request.form.get('video_url'))
        file = request.files.get('image')
        if file and allowed_file(file.filename):
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
        db.session.commit()
        flash('News article added.', 'success')
        return redirect(url_for('admin_news'))
    return render_page('Add News', ADMIN_NEWS_ADD_CONTENT)

@app.route('/admin/news/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_news_edit(id):
    article = News.query.get_or_404(id)
    if request.method == 'POST':
        if not validate_csrf_token(request.form.get('csrf_token')):
            flash('Invalid CSRF token.', 'danger')
            return redirect(url_for('admin_news_edit', id=id))
        title = sanitize_html(request.form.get('title'))
        content = request.form.get('content')
        image_url = sanitize_html(request.form.get('image_url'))
        video_url = sanitize_html(request.form.get('video_url'))
        file = request.files.get('image')
        if file and allowed_file(file.filename):
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
        db.session.commit()
        flash('News updated.', 'success')
        return redirect(url_for('admin_news'))
    return render_page('Edit News', ADMIN_NEWS_EDIT_CONTENT, article=article)

@app.route('/admin/news/delete/<int:id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_news_delete(id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        flash('Invalid CSRF token.', 'danger')
        return redirect(url_for('admin_news'))
    article = News.query.get_or_404(id)
    if article.image_url and article.image_url.startswith('/static/uploads/'):
        old_path = os.path.join('.', article.image_url.lstrip('/'))
        if os.path.exists(old_path):
            os.remove(old_path)
    db.session.delete(article)
    db.session.commit()
    flash('News deleted.', 'success')
    return redirect(url_for('admin_news'))

# ---------- Admin Applications ----------
@app.route('/admin/applications')
@login_required
@role_required('admin')
def admin_applications():
    apps = Application.query.order_by(Application.date_applied.desc()).all()
    return render_page('Manage Applications', ADMIN_APPLICATIONS_CONTENT, apps=apps)

# ---------- Admin Gallery ----------
@app.route('/admin/gallery')
@login_required
@role_required('admin')
def admin_gallery():
    images = Gallery.query.order_by(Gallery.uploaded_at.desc()).all()
    return render_page('Manage Gallery', ADMIN_GALLERY_CONTENT, images=images)

@app.route('/admin/gallery/add', methods=['POST'])
@login_required
@role_required('admin')
def admin_gallery_add():
    if not validate_csrf_token(request.form.get('csrf_token')):
        flash('Invalid CSRF token.', 'danger')
        return redirect(url_for('admin_gallery'))
    if 'image' not in request.files:
        flash('No image file provided.', 'danger')
        return redirect(url_for('admin_gallery'))
    file = request.files['image']
    if file.filename == '' or not allowed_file(file.filename):
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
    db.session.commit()
    flash('Image uploaded successfully.', 'success')
    return redirect(url_for('admin_gallery'))

@app.route('/admin/gallery/delete/<int:id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_gallery_delete(id):
    if not validate_csrf_token(request.form.get('csrf_token')):
        flash('Invalid CSRF token.', 'danger')
        return redirect(url_for('admin_gallery'))
    img = Gallery.query.get_or_404(id)
    if img.image_url.startswith('/static/gallery/'):
        old_path = os.path.join('.', img.image_url.lstrip('/'))
        if os.path.exists(old_path):
            os.remove(old_path)
    db.session.delete(img)
    db.session.commit()
    flash('Image deleted.', 'success')
    return redirect(url_for('admin_gallery'))

# ------------------------------
# Serve static files
# ------------------------------
@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

# ------------------------------
# SocketIO Events
# ------------------------------
@socketio.on('group_message')
def handle_group_message(data):
    if 'user_id' not in session:
        return
    msg = sanitize_html(data.get('message', ''))
    if not msg:
        return
    chat = ChatMessage(sender_id=session['user_id'], room='group', message=msg)
    db.session.add(chat)
    db.session.commit()
    emit('group_message', {
        'sender_id': session['user_id'],
        'sender_name': session['full_name'],
        'message': msg,
        'timestamp': datetime.datetime.utcnow().isoformat()
    }, broadcast=True)

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
    db.session.commit()
    # Send to sender
    emit('private_message', {
        'sender_id': session['user_id'],
        'sender_name': session['full_name'],
        'message': msg,
        'timestamp': datetime.datetime.utcnow().isoformat()
    }, room=str(session['user_id']))
    # Send to receiver
    emit('private_message', {
        'sender_id': session['user_id'],
        'sender_name': session['full_name'],
        'message': msg,
        'timestamp': datetime.datetime.utcnow().isoformat()
    }, room=str(receiver_id))

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
    db.session.commit()
    emit('classroom_message', {
        'sender_id': session['user_id'],
        'sender_name': session['full_name'],
        'room': room,
        'message': msg,
        'timestamp': datetime.datetime.utcnow().isoformat()
    }, room=room)

@socketio.on('join_room')
def on_join(data):
    room = data.get('room')
    if room:
        join_room(room)

@socketio.on('connect')
def on_connect():
    if 'user_id' in session:
        join_room(str(session['user_id']))

@socketio.on('disconnect')
def on_disconnect():
    if 'user_id' in session:
        leave_room(str(session['user_id']))

# ------------------------------
# Run Application
# ------------------------------
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)
    
    
