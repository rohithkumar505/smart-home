"""WSGI entry for Gunicorn (Render, Railway, Fly, VPS)."""
from app import app, init_database

with app.app_context():
    init_database()
