from app import app, init_database

with app.app_context():
    init_database()
