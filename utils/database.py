import os
import json
import threading
from flask_sqlalchemy import SQLAlchemy

# Initialize SQLAlchemy
db = SQLAlchemy()
db_lock = threading.Lock()


def normalize_database_url(db_url):
    """Use psycopg2, the PostgreSQL DBAPI installed by requirements.txt."""
    if db_url.startswith("postgres://"):
        return db_url.replace("postgres://", "postgresql://", 1)
    if db_url.startswith("postgresql+psycopg://"):
        return db_url.replace("postgresql+psycopg://", "postgresql://", 1)
    if db_url.startswith("postgresql+psycopg2://"):
        return db_url.replace("postgresql+psycopg2://", "postgresql://", 1)
    return db_url


# Define File Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_FILE = os.path.join(BASE_DIR, 'users.json')
LOGS_FILE = os.path.join(BASE_DIR, 'student_logs.json')

def init_db(app):
    """Initializes PostgreSQL for Render while using models defined in app.py."""
    db_url = os.environ.get("DATABASE_URL", "sqlite:///local_test.db")
    db_url = normalize_database_url(db_url)
    
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    
    with app.app_context():
        db.create_all()

def load_json(filename):
    """Historical fallback reader used by app.py."""
    with db_lock:
        if not os.path.exists(filename): 
            return {} if filename == USER_FILE else []
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {} if filename == USER_FILE else []

def save_json(filename, data):
    """Maintained for backward compatibility during migration."""
    with db_lock:
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
            return True
        except Exception:
            return False