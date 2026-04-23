#!/usr/bin/env python3
"""Create or update the app SQLite file (all tables + migrations + default admin)."""

from app import app, db, init_database

if __name__ == '__main__':
    with app.app_context():
        init_database()
        url = str(db.engine.url)
    print("Database ready:", url)
