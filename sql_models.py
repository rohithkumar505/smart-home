"""SQLAlchemy models for local SQLite / Postgres deployments."""
from datetime import datetime

from flask_login import UserMixin


def define_models(db):
    class User(UserMixin, db.Model):
        id = db.Column(db.Integer, primary_key=True)
        username = db.Column(db.String(150), unique=True, nullable=False)
        password = db.Column(db.String(150), nullable=False)
        mode = db.Column(db.String(50), default='Day')
        energy_cost_per_kwh = db.Column(db.Float, nullable=True)

    class Room(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(100), nullable=False)
        devices = db.relationship('Device', backref='room', lazy=True, cascade='all, delete')

    class Device(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(100), nullable=False)
        type = db.Column(db.String(50), nullable=False)
        status = db.Column(db.Boolean, default=False)
        room_id = db.Column(db.Integer, db.ForeignKey('room.id'), nullable=False)

    class Log(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        timestamp = db.Column(db.DateTime, default=datetime.utcnow)
        action = db.Column(db.String(255), nullable=False)
        user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    class Schedule(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        device_id = db.Column(db.Integer, db.ForeignKey('device.id'), nullable=False)
        action = db.Column(db.Boolean, nullable=False)
        time = db.Column(db.String(5), nullable=False)
        active = db.Column(db.Boolean, default=True)
        last_fired_at = db.Column(db.DateTime, nullable=True)
        device = db.relationship('Device', backref=db.backref('schedules', lazy=True))

    class Prediction(db.Model):
        __tablename__ = 'prediction'
        __table_args__ = (
            db.UniqueConstraint(
                'user_id', 'device_id', 'action', 'predicted_time',
                name='uq_prediction_user_device_action_time',
            ),
        )
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
        device_id = db.Column(db.Integer, db.ForeignKey('device.id'), nullable=False, index=True)
        action = db.Column(db.Boolean, nullable=False)
        predicted_time = db.Column(db.String(5), nullable=False)
        confidence = db.Column(db.Float, nullable=False, default=0.0)
        sample_days = db.Column(db.Integer, nullable=False, default=0)
        auto_enabled = db.Column(db.Boolean, nullable=False, default=False)
        schedule_id = db.Column(db.Integer, db.ForeignKey('schedule.id'), nullable=True)
        last_detected_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
        device = db.relationship('Device', backref=db.backref('predictions', lazy=True))
        schedule = db.relationship('Schedule', backref=db.backref('prediction_links', lazy=True))

    class Notification(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        message = db.Column(db.String(255), nullable=False)
        timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
        read = db.Column(db.Boolean, default=False)
        user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
        category = db.Column(db.String(16), default='info')

    class EnergySnapshot(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        recorded_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
        total_watts = db.Column(db.Integer, nullable=False)

    class CommandRecord(db.Model):
        __tablename__ = 'command_record'
        id = db.Column(db.Integer, primary_key=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
        raw_text = db.Column(db.String(500), nullable=False)
        action = db.Column(db.String(8), nullable=True)
        device_id = db.Column(db.Integer, db.ForeignKey('device.id'), nullable=True)
        success = db.Column(db.Boolean, nullable=False, default=False)
        response_message = db.Column(db.String(500), nullable=False, default='')
        user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
        device = db.relationship('Device', backref=db.backref('command_records', lazy='dynamic'))

    class CustomMode(db.Model):
        __tablename__ = 'custom_mode'
        __table_args__ = (db.UniqueConstraint('user_id', 'name', name='uq_custom_mode_user_name'),)
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
        name = db.Column(db.String(100), nullable=False)
        user = db.relationship('User', backref=db.backref('custom_modes', lazy=True))
        assignments = db.relationship(
            'CustomModeDevice',
            back_populates='custom_mode',
            lazy=True,
            cascade='all, delete-orphan',
        )

    class CustomModeDevice(db.Model):
        __tablename__ = 'custom_mode_device'
        __table_args__ = (db.UniqueConstraint('custom_mode_id', 'device_id', name='uq_custom_mode_device'),)
        id = db.Column(db.Integer, primary_key=True)
        custom_mode_id = db.Column(db.Integer, db.ForeignKey('custom_mode.id'), nullable=False)
        device_id = db.Column(db.Integer, db.ForeignKey('device.id'), nullable=False)
        want_on = db.Column(db.Boolean, nullable=False)
        custom_mode = db.relationship('CustomMode', back_populates='assignments')
        device = db.relationship('Device', backref=db.backref('custom_mode_slots', lazy=True))

    class UserDashboardLayout(db.Model):
        __tablename__ = 'user_dashboard_layout'
        user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), primary_key=True)
        device_order_json = db.Column(db.Text, nullable=False, default='[]')
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
        user = db.relationship('User', backref=db.backref('dashboard_layout', uselist=False))

    class AutomationRule(db.Model):
        __tablename__ = 'automation_rule'
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
        name = db.Column(db.String(150), default='')
        active = db.Column(db.Boolean, default=True, nullable=False)
        cond_device_id = db.Column(db.Integer, db.ForeignKey('device.id', ondelete='SET NULL'), nullable=True, index=True)
        cond_device_want_on = db.Column(db.Boolean, default=True, nullable=False)
        cond_time_after = db.Column(db.String(5), nullable=True)
        cond_time_before = db.Column(db.String(5), nullable=True)
        action_device_id = db.Column(db.Integer, db.ForeignKey('device.id', ondelete='SET NULL'), nullable=True, index=True)
        action_device_type = db.Column(db.String(50), nullable=True)
        action_set_on = db.Column(db.Boolean, nullable=False)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
        last_fired_at = db.Column(db.DateTime, nullable=True)
        cond_device = db.relationship(
            'Device', foreign_keys=[cond_device_id], backref=db.backref('automation_rules_if', lazy='dynamic'),
        )
        action_device = db.relationship(
            'Device', foreign_keys=[action_device_id], backref=db.backref('automation_rules_then', lazy='dynamic'),
        )

    return (
        User, Room, Device, Log, Schedule, Prediction, Notification,
        EnergySnapshot, CommandRecord, CustomMode, CustomModeDevice,
        UserDashboardLayout, AutomationRule,
    )
