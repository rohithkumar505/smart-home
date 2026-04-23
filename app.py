# pyright: reportCallIssue=false, reportAttributeAccessIssue=false, reportArgumentType=false
# (Flask-SQLAlchemy: dynamic Model() kwargs and relationship attrs are not in stubs.)

import csv
import json
import os
import re
import threading
import time
import zipfile
from io import BytesIO, StringIO
from urllib.parse import urlparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone, time as dt_time

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    session,
    abort,
    send_file,
)
from fpdf import FPDF
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import joinedload
from werkzeug.security import generate_password_hash, check_password_hash
from voice_commands import parse_voice_intent

# Nominal draw (watts) per device type when ON — used to estimate load from real DB on/off state.
NOMINAL_WATTS_BY_TYPE = {
    'Light': 14,
    'Fan': 55,
    'AC': 1400,
    'Lock': 3,
    'Heater': 1500,
    'Other': 40,
}

ALLOWED_DEVICE_TYPES = frozenset(NOMINAL_WATTS_BY_TYPE.keys())

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY') or 'dev-secret-change-with-FLASK_SECRET_KEY'
# Use absolute path so the DB persists on Render's mounted disk
_db_path = os.path.join(app.instance_path, 'database.db')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or f'sqlite:///{_db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Server-side signed session (Flask) + Flask-Login user id in session
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_REFRESH_EACH_REQUEST'] = True
# "Remember me" persistence (Flask-Login cookie)
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)
app.config['REMEMBER_COOKIE_HTTPONLY'] = True
app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.session_protection = 'strong'

# --- Models ---

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    mode = db.Column(db.String(50), default='Day')  # legacy column; UI profile Day/Night/Away removed
    # Optional: user’s utility price per kWh (same currency for all UI display)
    energy_cost_per_kwh = db.Column(db.Float, nullable=True)

class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    devices = db.relationship('Device', backref='room', lazy=True, cascade='all, delete')

class Device(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50), nullable=False) # e.g., 'Light', 'Fan', 'AC', 'Lock'
    status = db.Column(db.Boolean, default=False) # True for ON, False for OFF
    room_id = db.Column(db.Integer, db.ForeignKey('room.id'), nullable=False)

class Log(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    action = db.Column(db.String(255), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

class Schedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'), nullable=False)
    action = db.Column(db.Boolean, nullable=False)  # True = ON, False = OFF
    time = db.Column(db.String(5), nullable=False)  # HH:MM (server local time)
    active = db.Column(db.Boolean, default=True)
    last_fired_at = db.Column(db.DateTime, nullable=True)
    device = db.relationship('Device', backref=db.backref('schedules', lazy=True))

class Prediction(db.Model):
    """Predicted daily automations inferred from repeated log activity."""
    __tablename__ = 'prediction'
    __table_args__ = (
        db.UniqueConstraint('user_id', 'device_id', 'action', 'predicted_time', name='uq_prediction_user_device_action_time'),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'), nullable=False, index=True)
    action = db.Column(db.Boolean, nullable=False)  # True = ON, False = OFF
    predicted_time = db.Column(db.String(5), nullable=False)  # HH:MM
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
    # Bootstrap-style category for toast styling: info | success | warning | danger
    category = db.Column(db.String(16), default='info')


class EnergySnapshot(db.Model):
    """Recorded total estimated watts whenever device power state changes (from real device rows)."""
    id = db.Column(db.Integer, primary_key=True)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    total_watts = db.Column(db.Integer, nullable=False)


class CommandRecord(db.Model):
    """Stored voice-style commands and outcomes."""
    __tablename__ = 'command_record'
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    raw_text = db.Column(db.String(500), nullable=False)
    action = db.Column(db.String(8), nullable=True)  # 'on' or 'off'
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'), nullable=True)
    success = db.Column(db.Boolean, nullable=False, default=False)
    response_message = db.Column(db.String(500), nullable=False, default='')
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    device = db.relationship('Device', backref=db.backref('command_records', lazy='dynamic'))


class CustomMode(db.Model):
    """User-defined preset (e.g. Night Mode): name + per-device ON/OFF targets."""
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
    """One device target inside a custom mode."""
    __tablename__ = 'custom_mode_device'
    __table_args__ = (db.UniqueConstraint('custom_mode_id', 'device_id', name='uq_custom_mode_device'),)
    id = db.Column(db.Integer, primary_key=True)
    custom_mode_id = db.Column(db.Integer, db.ForeignKey('custom_mode.id'), nullable=False)
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'), nullable=False)
    want_on = db.Column(db.Boolean, nullable=False)
    custom_mode = db.relationship('CustomMode', back_populates='assignments')
    device = db.relationship('Device', backref=db.backref('custom_mode_slots', lazy=True))


class UserDashboardLayout(db.Model):
    """Per-user ordering of device cards on the main dashboard (JSON list of device ids)."""
    __tablename__ = 'user_dashboard_layout'
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), primary_key=True)
    device_order_json = db.Column(db.Text, nullable=False, default='[]')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    user = db.relationship('User', backref=db.backref('dashboard_layout', uselist=False))


class AutomationRule(db.Model):
    """
    IF (optional device state) AND (optional local-time window) THEN set one device or all of a type.
    Conditions are AND-combined. Time window supports overnight (e.g. after 22:00 before 06:00).
    """
    __tablename__ = 'automation_rule'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    name = db.Column(db.String(150), default='')
    active = db.Column(db.Boolean, default=True, nullable=False)
    # Conditions (all that are set must pass)
    cond_device_id = db.Column(db.Integer, db.ForeignKey('device.id', ondelete='SET NULL'), nullable=True, index=True)
    cond_device_want_on = db.Column(db.Boolean, default=True, nullable=False)
    cond_time_after = db.Column(db.String(5), nullable=True)   # HH:MM local
    cond_time_before = db.Column(db.String(5), nullable=True)
    # Action: exactly one of action_device_id or action_device_type
    action_device_id = db.Column(db.Integer, db.ForeignKey('device.id', ondelete='SET NULL'), nullable=True, index=True)
    action_device_type = db.Column(db.String(50), nullable=True)
    action_set_on = db.Column(db.Boolean, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    cond_device = db.relationship('Device', foreign_keys=[cond_device_id], backref=db.backref('automation_rules_if', lazy='dynamic'))
    action_device = db.relationship('Device', foreign_keys=[action_device_id], backref=db.backref('automation_rules_then', lazy='dynamic'))


@app.before_request
def _bootstrap_energy_history_once():
    """One-time baseline snapshot so charts reflect existing devices without a server restart."""
    if request.endpoint == 'static' or not request.endpoint:
        return
    if app.config.get('_ENERGY_HISTORY_BOOTSTRAP'):
        return
    app.config['_ENERGY_HISTORY_BOOTSTRAP'] = True
    try:
        if EnergySnapshot.query.count() == 0 and Device.query.count() > 0:
            record_energy_snapshot()
    except Exception:
        app.config['_ENERGY_HISTORY_BOOTSTRAP'] = False


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def add_log(action, user_id=None):
    log = Log(action=action, user_id=user_id)
    db.session.add(log)
    db.session.commit()


def parse_log_for_ui(action):
    """
    Derive (device_label, state, full_action) for the logs table.
    state is 'on', 'off', or 'neutral' (row styling / filter).
    """
    a = (action or '').strip()
    if not a:
        return '—', 'neutral', ''

    # Turned ON Device in Room
    m = re.match(r'^Turned\s+(ON|OFF)\s+(.+?)\s+in\s+(.+)$', a, re.I)
    if m:
        st = m.group(1).lower()
        return f"{m.group(2).strip()} · {m.group(3).strip()}", st, a

    # Command: … → Turned ON Name (Room).
    m = re.search(r'→\s*Turned\s+(ON|OFF)\s+([^(]+)\s*\(([^)]+)\)', a, re.I)
    if m:
        st = m.group(1).lower()
        return f"{m.group(2).strip()} · {m.group(3).strip()}", st, a

    # Command: … → Name is already ON/OFF.
    m = re.search(r'→\s*(.+?)\s+is already\s+(ON|OFF)\.?\s*$', a, re.I)
    if m:
        return m.group(1).strip(), m.group(2).lower(), a

    # Command executed: Turned ON Name (legacy)
    m = re.search(r'Command\s+executed:\s*Turned\s+(ON|OFF)\s+(.+)$', a, re.I)
    if m:
        return m.group(2).strip(), m.group(1).lower(), a

    # Added new device: Name in Room
    m = re.match(r'^Added new device:\s*(.+?)\s+in\s+(.+)$', a, re.I)
    if m:
        return f"{m.group(1).strip()} · {m.group(2).strip()}", 'neutral', a

    # Applied mode "Name": set N device(s).
    m = re.match(r'^Applied mode "([^"]+)":', a)
    if m:
        return m.group(1).strip(), 'neutral', a

    return '—', 'neutral', a


def ensure_notification_category_column():
    """SQLite: add category if DB existed before that column."""
    try:
        insp = inspect(db.engine)
        cols = {c['name'] for c in insp.get_columns('notification')}
        if 'category' in cols:
            return
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE notification ADD COLUMN category VARCHAR(16) DEFAULT 'info'"))
    except Exception:
        pass


def notification_to_dict(notif):
    if not notif:
        return None
    return {
        'id': notif.id,
        'message': notif.message,
        'category': (getattr(notif, 'category', None) or 'info'),
    }


def add_notification(message, user_id, category='info'):
    """Persist a user notification; returns the saved row (or None if nothing saved)."""
    ensure_notification_category_column()
    if category not in ('info', 'success', 'warning', 'danger'):
        category = 'info'
    msg = (message or '').strip()
    if not msg or not user_id:
        return None
    if len(msg) > 255:
        msg = msg[:252] + '…'
    notif = Notification(message=msg, user_id=user_id, category=category, read=False)
    db.session.add(notif)
    db.session.commit()
    return notif


def nominal_watts_for_device(device):
    return int(NOMINAL_WATTS_BY_TYPE.get(device.type, 40))


def compute_total_on_watts():
    total = 0
    for d in Device.query.filter_by(status=True).all():
        total += nominal_watts_for_device(d)
    return int(total)


def record_energy_snapshot():
    """Persist current estimated load from all devices (ON only)."""
    w = compute_total_on_watts()
    db.session.add(EnergySnapshot(total_watts=w))
    db.session.commit()


def ensure_user_energy_cost_column():
    """SQLite: add energy_cost_per_kwh to user if missing."""
    try:
        insp = inspect(db.engine)
        cols = {c['name'] for c in insp.get_columns('user')}
        if 'energy_cost_per_kwh' in cols:
            return
        with db.engine.begin() as conn:
            conn.execute(text('ALTER TABLE user ADD COLUMN energy_cost_per_kwh FLOAT'))
    except Exception:
        pass


def _local_day_start_utc_naive(target_date):
    """Local calendar date → UTC-naive instant at that local midnight (matches stored snapshot times)."""
    if isinstance(target_date, datetime):
        target_date = target_date.date()
    local_tz = datetime.now().astimezone().tzinfo
    start = datetime.combine(target_date, dt_time.min, tzinfo=local_tz)
    return start.astimezone(timezone.utc).replace(tzinfo=None)


def estimate_kwh_for_local_date(target_date, end_utc_cap=None):
    """
    Estimated kWh for one local day from EnergySnapshot trapezoids + constant tail.
    Past days: full local day. Today: from local midnight to now (or end_utc_cap).
    """
    if isinstance(target_date, datetime):
        target_date = target_date.date()
    today = datetime.now().astimezone().date()
    start_utc = _local_day_start_utc_naive(target_date)
    if target_date < today:
        end_utc = _local_day_start_utc_naive(target_date + timedelta(days=1))
    elif target_date == today:
        end_utc = end_utc_cap if end_utc_cap is not None else datetime.utcnow()
    else:
        return 0.0
    if end_utc <= start_utc:
        return 0.0
    rows = (
        EnergySnapshot.query.filter(
            EnergySnapshot.recorded_at >= start_utc,
            EnergySnapshot.recorded_at <= end_utc,
        )
        .order_by(EnergySnapshot.recorded_at.asc())
        .all()
    )
    if not rows:
        return 0.0
    kwh = 0.0
    t_first, w_first = rows[0].recorded_at, rows[0].total_watts
    if t_first > start_utc:
        h = (t_first - start_utc).total_seconds() / 3600.0
        if h > 0:
            kwh += (w_first / 2.0) / 1000.0 * h
    for i in range(len(rows) - 1):
        t_a, t_b = rows[i].recorded_at, rows[i + 1].recorded_at
        w_a, w_b = rows[i].total_watts, rows[i + 1].total_watts
        h = (t_b - t_a).total_seconds() / 3600.0
        if h > 0:
            kwh += ((w_a + w_b) / 2.0) / 1000.0 * h
    t_last, w_last = rows[-1].recorded_at, rows[-1].total_watts
    if end_utc > t_last:
        h = (end_utc - t_last).total_seconds() / 3600.0
        if h > 0:
            kwh += (w_last / 1000.0) * h
    return max(0.0, kwh)


def average_daily_kwh_recent(days=7):
    """Mean kWh per day over the last `days` local days (incl. today)."""
    if days < 1:
        days = 1
    today = datetime.now().astimezone().date()
    total = 0.0
    for i in range(days):
        d = today - timedelta(days=i)
        total += estimate_kwh_for_local_date(d)
    return total / float(days)


def energy_cost_display_for_user(user):
    """
    kWh from snapshot history; cost = kWh * user’s energy_cost_per_kwh.
    Daily = today; monthly est. = 7-day average daily kWh * 30 * rate.
    """
    ensure_user_energy_cost_column()
    rate = getattr(user, 'energy_cost_per_kwh', None)
    has_rate = rate is not None and float(rate) > 0.0
    today = datetime.now().astimezone().date()
    kwh_today = estimate_kwh_for_local_date(today)
    avg_d = average_daily_kwh_recent(7)
    if not has_rate:
        return {
            'kwh_today': round(kwh_today, 3),
            'cost_today': None,
            'cost_month_est': None,
            'rate': None,
            'has_rate': False,
            'avg_daily_kwh': round(avg_d, 3),
        }
    r = float(rate)
    return {
        'kwh_today': round(kwh_today, 3),
        'cost_today': round(kwh_today * r, 2),
        'cost_month_est': round(avg_d * 30.0 * r, 2),
        'rate': r,
        'has_rate': True,
        'avg_daily_kwh': round(avg_d, 3),
    }


def build_smart_recommendations(devices, on_devices, current_estimated_kw):
    """
    Rule-based recommendations from device mix, time of day, and snapshot kWh patterns.
    Returns a list of dicts: title, body, variant, icon.
    """
    n = len(devices) if devices else 0
    if n == 0:
        return []

    now_local = datetime.now().astimezone()
    hour = now_local.hour
    is_night = hour >= 23 or hour < 6
    today = now_local.date()
    kwh_today = estimate_kwh_for_local_date(today)
    avg_d = average_daily_kwh_recent(7)

    recs = []

    if avg_d > 0.02 and kwh_today > avg_d * 1.2:
        recs.append(
            {
                'title': 'Usage above your recent average',
                'body': (
                    f"Today's estimated use is about {kwh_today:.2f} kWh so far, "
                    f"compared with ~{avg_d:.2f} kWh per day over the last week."
                ),
                'variant': 'warning',
                'icon': 'graph-up-arrow',
            }
        )

    if n >= 3 and on_devices >= (n + 1) // 2:
        recs.append(
            {
                'title': 'Turn off unused devices',
                'body': f'{on_devices} of {n} devices are on. Switch off what you are not using in empty rooms.',
                'variant': 'warning',
                'icon': 'power',
            }
        )

    if current_estimated_kw >= 0.35:
        recs.append(
            {
                'title': 'Reduce energy usage',
                'body': (
                    f"Estimated load is about {current_estimated_kw:.2f} kW. "
                    'Turning off extra lights, fans, or climate gear will lower it quickly.'
                ),
                'variant': 'warning',
                'icon': 'lightning-charge',
            }
        )
    elif current_estimated_kw >= 0.2 and on_devices >= 2:
        recs.append(
            {
                'title': 'Reduce energy usage',
                'body': (
                    f'Load is around {current_estimated_kw:.2f} kW. Small cutbacks add up on your energy use.'
                ),
                'variant': 'info',
                'icon': 'lightning-charge',
            }
        )

    lights_on = [d for d in devices if d.type == 'Light' and d.status]
    if is_night and len(lights_on) >= 2:
        recs.append(
            {
                'title': 'Trim overnight lighting',
                'body': f'{len(lights_on)} lights are on during quiet hours. Consider night lights or a single path light.',
                'variant': 'info',
                'icon': 'moon',
            }
        )

    climate_on = [d for d in devices if d.status and d.type in ('AC', 'Heater')]
    if climate_on and current_estimated_kw >= 0.25:
        names = ', '.join(c.name for c in climate_on[:3])
        tail = '…' if len(climate_on) > 3 else ''
        recs.append(
            {
                'title': 'Climate is driving a large share of load',
                'body': f'Heating and cooling for {names}{tail} can dominate usage. Adjust setpoints or schedules when you can.',
                'variant': 'info',
                'icon': 'snow',
            }
        )

    if on_devices == 0:
        recs.append(
            {
                'title': 'Nice — everything is off',
                'body': 'With no active devices, standby and snapshot energy are minimal right now.',
                'variant': 'success',
                'icon': 'check2-circle',
            }
        )
    elif not recs:
        recs.append(
            {
                'title': 'Usage looks steady',
                'body': 'No strong savings tips from current patterns. Keep an eye on devices left on in empty rooms.',
                'variant': 'success',
                'icon': 'heart',
            }
        )

    return recs[:6]


def build_system_health(devices, on_devices):
    """
    Dashboard system health summary: fleet size, active count, and qualitative status.
    """
    total = len(devices) if devices else 0
    if total == 0:
        return {
            'total_devices': 0,
            'active_devices': 0,
            'status_label': 'Setup required',
            'status_detail': 'Add rooms and devices to start monitoring your home.',
            'status_variant': 'warning',
        }
    snap_n = EnergySnapshot.query.count()
    if snap_n == 0:
        return {
            'total_devices': total,
            'active_devices': on_devices,
            'status_label': 'Online',
            'status_detail': 'Controller is running. Energy history will appear after the first power change is recorded.',
            'status_variant': 'info',
        }
    return {
        'total_devices': total,
        'active_devices': on_devices,
        'status_label': 'Operational',
        'status_detail': f'{on_devices} of {total} device(s) powered on. Load and usage monitoring active.',
        'status_variant': 'success',
    }


def ensure_schedule_last_fired_column():
    """SQLite: add last_fired_at if DB existed before that column."""
    try:
        insp = inspect(db.engine)
        cols = {c['name'] for c in insp.get_columns('schedule')}
        if 'last_fired_at' in cols:
            return
        with db.engine.begin() as conn:
            conn.execute(text('ALTER TABLE schedule ADD COLUMN last_fired_at DATETIME'))
    except Exception:
        pass


def ensure_prediction_table():
    """Create prediction table for existing DBs if missing."""
    try:
        Prediction.__table__.create(bind=db.engine, checkfirst=True)
    except Exception:
        pass


def ensure_user_dashboard_layout_table():
    try:
        UserDashboardLayout.__table__.create(bind=db.engine, checkfirst=True)
    except Exception:
        pass


def ensure_automation_rule_table():
    try:
        AutomationRule.__table__.create(bind=db.engine, checkfirst=True)
    except Exception:
        pass


def _hhmm_to_minutes(hhmm):
    """Parse HH:MM to minutes 0..1439, or None."""
    if not hhmm or not str(hhmm).strip():
        return None
    p = parse_schedule_time(str(hhmm).strip())
    if not p:
        return None
    return int(p[:2]) * 60 + int(p[3:5])


def automation_has_any_condition(rule):
    if rule.cond_device_id is not None:
        return True
    if (rule.cond_time_after or '').strip() or (rule.cond_time_before or '').strip():
        return True
    return False


def automation_time_window_ok(rule, now_m):
    """Local minutes since midnight; optional after/before window (supports overnight)."""
    ta = _hhmm_to_minutes(rule.cond_time_after) if rule.cond_time_after else None
    tb = _hhmm_to_minutes(rule.cond_time_before) if rule.cond_time_before else None
    if ta is None and tb is None:
        return True
    if ta is not None and tb is None:
        return now_m >= ta
    if ta is None and tb is not None:
        return now_m < tb
    if ta is not None and tb is not None:
        if ta < tb:
            return ta <= now_m < tb
        return now_m >= ta or now_m < tb
    return True


def automation_device_condition_ok(rule):
    if rule.cond_device_id is None:
        return True
    d = db.session.get(Device, rule.cond_device_id)
    if not d:
        return False
    return bool(d.status) == bool(rule.cond_device_want_on)


def _apply_one_automation_rule(rule):
    """If conditions already met, act on target device(s). Returns True if any device state changed."""
    want = bool(rule.action_set_on)
    changed = False
    targets = []
    if rule.action_device_id is not None:
        d = db.session.get(Device, rule.action_device_id)
        if d:
            targets = [d]
    elif rule.action_device_type:
        targets = list(Device.query.filter_by(type=rule.action_device_type).all())
    for d in targets:
        if bool(d.status) != want:
            d.status = want
            changed = True
    if not changed:
        return False
    nm = (rule.name or '').strip() or f'Rule #{rule.id}'
    st = 'ON' if want else 'OFF'
    if rule.action_device_id is not None and targets:
        d0 = targets[0]
        msg = f'Automation “{nm}”: turned {st} {d0.name} in {d0.room.name}'
    elif rule.action_device_type and targets:
        n = len(targets)
        msg = f'Automation “{nm}”: turned {st} all {rule.action_device_type} device(s) ({n})'
    else:
        msg = f'Automation “{nm}”: applied'
    if len(msg) > 255:
        msg = msg[:252] + '…'
    add_log(msg, user_id=rule.user_id)
    return True


def tick_automation_rules():
    ensure_automation_rule_table()
    now = datetime.now()
    now_m = now.hour * 60 + now.minute
    for rule in (
        AutomationRule.query.filter_by(active=True)
        .order_by(AutomationRule.id)
        .all()
    ):
        if not rule.user_id:
            continue
        if not automation_has_any_condition(rule):
            continue
        if not automation_time_window_ok(rule, now_m):
            continue
        if not automation_device_condition_ok(rule):
            continue
        if _apply_one_automation_rule(rule):
            record_energy_snapshot()


def describe_automation_rule(rule):
    """One-line description for the automation UI."""
    parts = []
    if rule.cond_device_id and rule.cond_device:
        w = 'ON' if rule.cond_device_want_on else 'OFF'
        parts.append(f'{rule.cond_device.name} is {w}')
    ta = (rule.cond_time_after or '').strip()
    tb = (rule.cond_time_before or '').strip()
    if ta and tb:
        parts.append(f'local {ta}–{tb}')
    elif ta:
        parts.append(f'local time after {ta}')
    elif tb:
        parts.append(f'local time before {tb}')
    cond = ' AND '.join(parts) if parts else '—'
    st = 'ON' if rule.action_set_on else 'OFF'
    if rule.action_device_id and rule.action_device:
        act = f'turn {rule.action_device.name} {st}'
    elif rule.action_device_type:
        act = f'turn all {rule.action_device_type} {st}'
    else:
        act = '(no target)'
    return f'If {cond} → {act}'


app.jinja_env.filters['automation_rule_desc'] = describe_automation_rule


def ordered_dashboard_devices(user_id, devices):
    """
    Apply saved order from UserDashboardLayout for this user.
    Unknown or removed ids in JSON are ignored; new devices are appended in stable id order.
    """
    devs = list(devices)
    if not devs:
        return []
    ensure_user_dashboard_layout_table()
    row = db.session.get(UserDashboardLayout, user_id)
    if not row or not (row.device_order_json or '').strip():
        return devs
    try:
        order = json.loads(row.device_order_json)
    except (json.JSONDecodeError, TypeError):
        return devs
    if not isinstance(order, list):
        return devs
    by_id = {d.id: d for d in devs}
    out = []
    seen = set()
    for raw in order:
        try:
            did = int(raw)
        except (TypeError, ValueError):
            continue
        if did in by_id and did not in seen:
            out.append(by_id[did])
            seen.add(did)
    for d in sorted(devs, key=lambda x: x.id):
        if d.id not in seen:
            out.append(d)
    return out


def _prediction_device_lookup():
    rows = (
        Device.query.join(Room, Device.room_id == Room.id)
        .with_entities(Device.id, Device.name, Room.name)
        .all()
    )
    by_name_room = {}
    by_id = {}
    for device_id, device_name, room_name in rows:
        dk = str(device_name or '').strip().lower()
        rk = str(room_name or '').strip().lower()
        if dk and rk:
            by_name_room[(dk, rk)] = device_id
        by_id[device_id] = {'name': device_name, 'room': room_name}
    return by_name_room, by_id


def _extract_prediction_event(log_row, by_name_room):
    """Return (device_id, want_on, timestamp) from a log row when parseable."""
    a = (log_row.action or '').strip()
    if not a:
        return None

    patterns = (
        re.match(r'^Turned\s+(ON|OFF)\s+(.+?)\s+in\s+(.+)$', a, re.I),
        re.search(r'→\s*Turned\s+(ON|OFF)\s+([^(]+)\s*\(([^)]+)\)', a, re.I),
        re.search(r'Schedule at \d{1,2}:\d{2}:\s*turned\s+(ON|OFF)\s+(.+?)\s+in\s+(.+)$', a, re.I),
    )
    for m in patterns:
        if not m:
            continue
        want_on = m.group(1).strip().upper() == 'ON'
        device_name = m.group(2).strip().lower()
        room_name = m.group(3).strip().lower()
        device_id = by_name_room.get((device_name, room_name))
        if device_id:
            return device_id, want_on, log_row.timestamp
    return None


def _bucket_minutes_15(hour, minute):
    total = int(hour) * 60 + int(minute)
    return (total // 15) * 15


def _minutes_to_hhmm(minutes):
    h = (minutes // 60) % 24
    m = minutes % 60
    return f'{h:02d}:{m:02d}'


def _hhmm_to_display(hhmm):
    parts = (hhmm or '').split(':')
    if len(parts) != 2:
        return hhmm
    try:
        h = int(parts[0])
        m = int(parts[1])
    except ValueError:
        return hhmm
    dt = datetime(2000, 1, 1, h % 24, m % 60)
    return dt.strftime('%I:%M %p').lstrip('0').replace(':00 ', ' ')


def refresh_predictive_automations(user_id, window_days=45):
    """
    Mine logs for repeated daily actions:
    same device + same action + same 15-minute bucket across >=3 days.
    """
    ensure_prediction_table()
    since = datetime.utcnow() - timedelta(days=window_days)
    logs = (
        Log.query.filter(
            Log.user_id == user_id,
            Log.timestamp >= since,
        )
        .order_by(Log.timestamp.asc())
        .all()
    )
    if not logs:
        return (
            Prediction.query.filter_by(user_id=user_id)
            .options(joinedload(Prediction.device).joinedload(Device.room))
            .order_by(Prediction.auto_enabled.desc(), Prediction.confidence.desc(), Prediction.sample_days.desc())
            .all()
        )

    by_name_room, by_id = _prediction_device_lookup()
    events = []
    for row in logs:
        parsed = _extract_prediction_event(row, by_name_room)
        if parsed:
            events.append(parsed)
    if not events:
        return (
            Prediction.query.filter_by(user_id=user_id)
            .options(joinedload(Prediction.device).joinedload(Device.room))
            .order_by(Prediction.auto_enabled.desc(), Prediction.confidence.desc(), Prediction.sample_days.desc())
            .all()
        )

    total_days_by_key = defaultdict(set)
    bucket_days = defaultdict(set)
    for device_id, want_on, ts in events:
        t = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
        loc = t.astimezone()
        day = loc.date().isoformat()
        dkey = (device_id, want_on)
        bmin = _bucket_minutes_15(loc.hour, loc.minute)
        total_days_by_key[dkey].add(day)
        bucket_days[(device_id, want_on, bmin)].add(day)

    best_by_device_action = {}
    for (device_id, want_on, bmin), days in bucket_days.items():
        day_count = len(days)
        if day_count < 3:
            continue
        dkey = (device_id, want_on)
        total_days = max(1, len(total_days_by_key[dkey]))
        confidence = day_count / float(total_days)
        if confidence < 0.6:
            continue
        candidate = {
            'device_id': device_id,
            'action': want_on,
            'predicted_time': _minutes_to_hhmm(bmin),
            'confidence': round(confidence, 3),
            'sample_days': day_count,
        }
        current = best_by_device_action.get(dkey)
        if (not current) or (
            candidate['sample_days'] > current['sample_days']
            or (
                candidate['sample_days'] == current['sample_days']
                and candidate['confidence'] > current['confidence']
            )
        ):
            best_by_device_action[dkey] = candidate

    existing = Prediction.query.filter_by(user_id=user_id).all()
    existing_by_key = {
        (p.device_id, bool(p.action), p.predicted_time): p
        for p in existing
    }

    now = datetime.utcnow()
    candidate_keys = set()
    changed = False
    for _, c in best_by_device_action.items():
        if c['device_id'] not in by_id:
            continue
        k = (c['device_id'], bool(c['action']), c['predicted_time'])
        candidate_keys.add(k)
        row = existing_by_key.get(k)
        if row:
            if (
                row.confidence != c['confidence']
                or row.sample_days != c['sample_days']
                or row.last_detected_at is None
            ):
                row.confidence = c['confidence']
                row.sample_days = c['sample_days']
                row.last_detected_at = now
                changed = True
        else:
            db.session.add(
                Prediction(
                    user_id=user_id,
                    device_id=c['device_id'],
                    action=bool(c['action']),
                    predicted_time=c['predicted_time'],
                    confidence=c['confidence'],
                    sample_days=c['sample_days'],
                    auto_enabled=False,
                    last_detected_at=now,
                )
            )
            changed = True

    for row in existing:
        k = (row.device_id, bool(row.action), row.predicted_time)
        if k not in candidate_keys and not row.auto_enabled:
            db.session.delete(row)
            changed = True

    if changed:
        db.session.commit()

    return (
        Prediction.query.filter_by(user_id=user_id)
        .options(joinedload(Prediction.device).joinedload(Device.room))
        .order_by(
            Prediction.auto_enabled.desc(),
            Prediction.confidence.desc(),
            Prediction.sample_days.desc(),
            Prediction.id.desc(),
        )
        .all()
    )


def build_prediction_cards(predictions, limit=None):
    """UI rows for stored `Prediction` rows (same order as input list)."""
    cards = []
    for p in predictions or []:
        if not p.device:
            continue
        action_word = 'on' if p.action else 'off'
        time_disp = _hhmm_to_display(p.predicted_time)
        cards.append(
            {
                'id': p.id,
                'device': p.device.name,
                'room': p.device.room.name if p.device.room else 'Unknown room',
                'action': action_word,
                'time': p.predicted_time,
                'time_display': time_disp,
                'confidence_pct': int(round((p.confidence or 0) * 100)),
                'sample_days': p.sample_days,
                'auto_enabled': bool(p.auto_enabled),
                'suggestion': f'You usually turn {action_word} {p.device.name} at {time_disp}.',
            }
        )
    if limit is not None and len(cards) > limit:
        return cards[:limit]
    return cards


def tick_schedules():
    """Run due schedules (server local HH:MM). Called from background thread."""
    ensure_schedule_last_fired_column()
    now = datetime.now()
    hhmm = now.strftime('%H:%M')
    for sch in Schedule.query.filter_by(active=True).all():
        if sch.time != hhmm:
            continue
        if sch.last_fired_at and (now - sch.last_fired_at).total_seconds() < 90:
            continue
        device = db.session.get(Device, sch.device_id)
        if not device:
            continue
        want = bool(sch.action)
        device.status = want
        sch.last_fired_at = now
        st = 'ON' if want else 'OFF'
        pred_row = Prediction.query.filter_by(schedule_id=sch.id).first()
        log_uid = pred_row.user_id if pred_row else None
        db.session.add(
            Log(
                action=f'Schedule at {hhmm}: turned {st} {device.name} in {device.room.name}',
                user_id=log_uid,
            )
        )
        db.session.commit()
        record_energy_snapshot()


_schedule_lock = threading.Lock()
_schedule_thread_started = False


def start_schedule_background_runner():
    """Single daemon thread; wakes every 30s to match HH:MM schedules."""

    def loop():
        while True:
            time.sleep(30)
            try:
                with app.app_context():
                    tick_schedules()
                    tick_automation_rules()
            except Exception:
                app.logger.exception('Schedule runner tick failed')

    global _schedule_thread_started
    with _schedule_lock:
        if _schedule_thread_started:
            return
        _schedule_thread_started = True
    t = threading.Thread(target=loop, name='schedule-runner', daemon=True)
    t.start()


@app.before_request
def _start_schedule_runner_once():
    if request.endpoint == 'static':
        return
    start_schedule_background_runner()


def weekly_peak_kw_series():
    """Last 7 calendar days: max recorded watts per day → kW (from EnergySnapshot)."""
    today = datetime.utcnow().date()
    start_date = today - timedelta(days=6)
    start_dt = datetime.combine(start_date, datetime.min.time())
    rows = (
        db.session.query(
            func.date(EnergySnapshot.recorded_at).label('d'),
            func.max(EnergySnapshot.total_watts).label('peak'),
        )
        .filter(EnergySnapshot.recorded_at >= start_dt)
        .group_by(func.date(EnergySnapshot.recorded_at))
        .all()
    )
    by_day = {}
    for r in rows:
        day_key = r.d if isinstance(r.d, str) else (r.d.isoformat() if hasattr(r.d, 'isoformat') else str(r.d))
        by_day[str(day_key)] = int(r.peak or 0)
    labels = []
    kw_values = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        labels.append(day.strftime('%a'))
        key = day.isoformat()
        w = by_day.get(key, 0)
        kw_values.append(round(w / 1000.0, 3))
    return labels, kw_values


def room_on_power_breakdown():
    """Current ON devices only: room name → total watts from DB."""
    acc = defaultdict(int)
    for d in Device.query.filter_by(status=True).all():
        acc[d.room.name] += nominal_watts_for_device(d)
    items = sorted(acc.items(), key=lambda x: -x[1])
    labels = [name for name, _ in items]
    watts = [w for _, w in items]
    return labels, watts


def chart_palette(n):
    base = ['#4361ee', '#3f37c9', '#4cc9f0', '#f72585', '#7209b7', '#4ade80', '#fbbf24', '#94a3b8']
    return [base[i % len(base)] for i in range(n)]


def _usage_device_id_from_log_action(action, by_key):
    """
    Map a log line to a device id for usage stats.
    Skips 'Command:' lines — those are counted via CommandRecord to avoid double-counting.
    """
    a = (action or '').strip()
    if not a or a.startswith('Command:'):
        return None
    m = re.match(r'^Turned\s+(ON|OFF)\s+(.+?)\s+in\s+(.+)$', a, re.I)
    if m:
        return by_key.get((m.group(2).strip().lower(), m.group(3).strip().lower()))
    m = re.search(
        r'Schedule at \d{1,2}:\d{2}:\s*turned\s+(ON|OFF)\s+(.+?)\s+in\s+(.+)$',
        a,
        re.I,
    )
    if m:
        return by_key.get((m.group(2).strip().lower(), m.group(3).strip().lower()))
    return None


def device_usage_analytics(top_chart=12):
    """
    Per-device event counts: successful CommandRecord rows plus Log lines for
    dashboard toggles and schedules (excluding Command: logs).
    """
    devices = (
        Device.query.join(Room, Device.room_id == Room.id)
        .order_by(Room.name, Device.name)
        .all()
    )
    by_key = {(d.name.strip().lower(), d.room.name.strip().lower()): d.id for d in devices}
    counts = {d.id: 0 for d in devices}

    cmd_rows = (
        db.session.query(CommandRecord.device_id, func.count(CommandRecord.id))
        .filter(CommandRecord.success.is_(True), CommandRecord.device_id.isnot(None))
        .group_by(CommandRecord.device_id)
        .all()
    )
    for did, c in cmd_rows:
        if did in counts:
            counts[did] += int(c)

    for (action,) in Log.query.with_entities(Log.action).all():
        did = _usage_device_id_from_log_action(action, by_key)
        if did and did in counts:
            counts[did] += 1

    total_events = int(sum(counts.values()))
    most_device = None
    most_n = 0
    for d in devices:
        n = counts.get(d.id, 0)
        if n > most_n:
            most_n = n
            most_device = d

    chart_labels = []
    chart_values = []
    items_sorted = sorted(((d, counts[d.id]) for d in devices), key=lambda x: (-x[1], x[0].name.lower()))
    top = items_sorted[:top_chart]
    rest = items_sorted[top_chart:]
    rest_sum = sum(c for _, c in rest)
    for d, c in top:
        chart_labels.append(f'{d.name} · {d.room.name}')
        chart_values.append(c)
    if rest_sum > 0:
        chart_labels.append('Other devices')
        chart_values.append(rest_sum)
    chart_colors = chart_palette(len(chart_labels)) if chart_labels else []

    per_device_rows = [
        {
            'device_id': d.id,
            'name': d.name,
            'room': d.room.name,
            'type': d.type,
            'power_state': 'on' if d.status else 'off',
            'usage_events': c,
        }
        for d, c in items_sorted
    ]

    return {
        'usage_chart_labels': chart_labels,
        'usage_chart_values': chart_values,
        'usage_chart_colors': chart_colors,
        'total_usage_events': total_events,
        'most_used_device': most_device,
        'most_used_count': most_n,
        'on_device_count': Device.query.filter_by(status=True).count(),
        'per_device_rows': per_device_rows,
    }


def usage_heatmap_from_logs(window_days=42):
    """
    7 x 24 grid: weekday (Mon=0) x hour (local) counts from Log lines that map to a device
    (dashboard toggles + schedule runs — same filter as the usage bar; excludes 'Command:' lines).
    """
    mat = [[0] * 24 for _ in range(7)]
    if window_days < 1:
        window_days = 1
    since = datetime.utcnow() - timedelta(days=window_days)
    devices = (
        Device.query.join(Room, Device.room_id == Room.id)
        .all()
    )
    by_key = {(d.name.strip().lower(), d.room.name.strip().lower()): d.id for d in devices}
    if not by_key:
        return {
            'matrix': mat,
            'max_count': 0,
            'peak_wd': 0,
            'peak_hr': 0,
            'peak_count': 0,
            'peak_label': '—',
            'window_days': window_days,
        }

    rows = (
        Log.query.filter(Log.timestamp >= since)
        .with_entities(Log.timestamp, Log.action)
        .all()
    )
    for ts, action in rows:
        did = _usage_device_id_from_log_action(action, by_key)
        if did is None:
            continue
        if ts is None:
            continue
        t_utc = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
        loc = t_utc.astimezone()
        wd = int(loc.weekday())  # Mon=0
        h = int(loc.hour)
        if 0 <= wd < 7 and 0 <= h < 24:
            mat[wd][h] += 1

    max_v = 0
    peak_w, peak_h = 0, 0
    for w in range(7):
        for h in range(24):
            c = mat[w][h]
            if c > max_v:
                max_v = c
                peak_w, peak_h = w, h
    wday_names_short = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    if max_v == 0:
        pl = '—'
    else:
        pl = f'{wday_names_short[peak_w]} · {peak_h:02d}:00–{peak_h:02d}:59 (local), {max_v} event' + (
            's' if max_v != 1 else ''
        )
    return {
        'matrix': mat,
        'max_count': max_v,
        'peak_wd': peak_w,
        'peak_hr': peak_h,
        'peak_count': max_v,
        'peak_label': pl,
        'window_days': window_days,
    }


def _ascii_for_pdf(s):
    if s is None:
        return ''
    return str(s).encode('ascii', 'replace').decode('ascii')


def _report_energy_daily_rows(num_days=30):
    if num_days < 1:
        num_days = 1
    today = datetime.now().astimezone().date()
    out = []
    for j in range(num_days):
        d = today - timedelta(days=num_days - 1 - j)
        kwh = estimate_kwh_for_local_date(d)
        out.append((d.isoformat(), round(kwh, 4)))
    return out


def _fetch_logs_for_report(max_rows=5000, days=90):
    since = datetime.utcnow() - timedelta(days=days)
    return (
        Log.query.filter(Log.timestamp >= since)
        .order_by(Log.timestamp.desc())
        .limit(max_rows)
        .all()
    )


def _build_report_csv_zip(usage, weekly_labels, weekly_kw, energy_rows, log_rows, total_on_w, snapshot_c, username):
    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        sio = StringIO()
        w = csv.writer(sio)
        w.writerow(['key', 'value'])
        w.writerow(['generated_utc', datetime.utcnow().isoformat() + 'Z'])
        w.writerow(['user', username])
        w.writerow(['total_usage_events', usage.get('total_usage_events', 0)])
        w.writerow(['on_device_count', usage.get('on_device_count', 0)])
        w.writerow(['avg_daily_kwh_7d', round(average_daily_kwh_recent(7), 4)])
        w.writerow(
            [
                'kwh_today',
                round(estimate_kwh_for_local_date(datetime.now().astimezone().date()), 4),
            ]
        )
        w.writerow(['energy_snapshots_stored', snapshot_c])
        w.writerow(['total_on_watts_estimated', int(total_on_w)])
        w.writerow([])
        w.writerow(['weekday_chart_label', 'peak_estimated_kw'])
        for lab, k in zip(weekly_labels, weekly_kw):
            w.writerow([lab, k])
        zf.writestr('summary.csv', sio.getvalue())

        sio = StringIO()
        w = csv.writer(sio)
        w.writerow(['device_id', 'device_name', 'room', 'type', 'power_state', 'usage_event_count'])
        for row in usage.get('per_device_rows', []):
            w.writerow(
                [
                    row['device_id'],
                    row['name'],
                    row['room'],
                    row['type'],
                    row['power_state'],
                    row['usage_events'],
                ]
            )
        zf.writestr('device_usage.csv', sio.getvalue())

        sio = StringIO()
        w = csv.writer(sio)
        w.writerow(['date_local', 'estimated_kwh'])
        for dstr, kwh in energy_rows:
            w.writerow([dstr, kwh])
        zf.writestr('energy_daily_kwh.csv', sio.getvalue())

        if log_rows:
            sio = StringIO()
            w = csv.writer(sio)
            w.writerow(['timestamp_utc', 'user_id', 'action'])
            for log in log_rows:
                ts = log.timestamp.isoformat() if log.timestamp else ''
                w.writerow(
                    [
                        ts,
                        log.user_id if log.user_id is not None else '',
                        (log.action or '').replace('\n', ' ').replace('\r', ' '),
                    ]
                )
            zf.writestr('activity_logs.csv', sio.getvalue())

    buf.seek(0)
    return buf


def _build_report_pdf(usage, weekly_labels, weekly_kw, energy_rows, log_rows, room_labels, room_watts, total_on_w, snapshot_c, username, pdf_log_limit=300):
    pdf = FPDF()
    pdf.set_auto_page_break(True, 14)
    pdf.add_page()
    ew = pdf.epw
    pdf.set_font('helvetica', 'B', 15)
    pdf.cell(0, 8, _ascii_for_pdf('Smart home report'), ln=1, align='C')
    pdf.set_font('helvetica', '', 10)
    pdf.cell(0, 5, _ascii_for_pdf(f'User: {username}'), ln=1, align='C')
    gen = f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
    pdf.cell(0, 5, _ascii_for_pdf(gen), ln=1, align='C')
    pdf.ln(2)

    pdf.set_font('helvetica', 'B', 12)
    pdf.cell(0, 7, _ascii_for_pdf('Summary'))
    pdf.ln()
    pdf.set_font('helvetica', '', 9)
    for line in [
        f"Total usage events: {usage.get('total_usage_events', 0)}",
        f"Devices on now: {usage.get('on_device_count', 0)}",
        f"7-day avg daily kWh: {round(average_daily_kwh_recent(7), 4)}",
        f"Today est. kWh: {round(estimate_kwh_for_local_date(datetime.now().astimezone().date()), 4)}",
        f'Snapshot records: {snapshot_c}',
        f'Current on-load estimate: {int(total_on_w)} W',
    ]:
        pdf.cell(0, 5, _ascii_for_pdf(line), ln=1)

    pdf.ln(1)
    pdf.set_font('helvetica', 'B', 12)
    pdf.cell(0, 7, _ascii_for_pdf('Daily peak load (last 7 days, kW)'))
    pdf.ln()
    pdf.set_font('helvetica', '', 8)
    for lab, k in zip(weekly_labels, weekly_kw):
        pdf.cell(0, 4, _ascii_for_pdf(f'{lab}: {k} kW'), ln=1)

    if room_labels and room_watts and int(total_on_w) > 0:
        pdf.ln(1)
        pdf.set_font('helvetica', 'B', 12)
        pdf.cell(0, 7, _ascii_for_pdf('Load by room (W, devices ON)'))
        pdf.ln()
        pdf.set_font('helvetica', '', 8)
        for name, rw in zip(room_labels, room_watts):
            pdf.cell(0, 4, _ascii_for_pdf(f'{name}: {int(rw)} W'), ln=1)

    pdf.ln(1)
    pdf.set_font('helvetica', 'B', 12)
    pdf.cell(0, 7, _ascii_for_pdf('Device usage (event counts)'))
    pdf.ln()
    pdf.set_font('helvetica', '', 7)
    for row in usage.get('per_device_rows', []):
        line = (
            f"{row['device_id']}\t{row['name']}\t{row['room']}\t{row['type']}\t"
            f"{row['power_state']}\t{row['usage_events']}"
        )
        pdf.multi_cell(ew, 3, _ascii_for_pdf(line))
    pdf.ln(1)
    pdf.set_font('helvetica', 'B', 12)
    pdf.cell(0, 7, _ascii_for_pdf('Energy (estimated kWh by local day)'))
    pdf.ln()
    pdf.set_font('helvetica', '', 6.5)
    for dstr, kwh in energy_rows:
        if pdf.get_y() > 275:
            pdf.add_page()
        pdf.cell(0, 3, _ascii_for_pdf(f'{dstr}:  {kwh} kWh'), ln=1)

    if log_rows:
        pdf.add_page()
        ew2 = pdf.epw
        pdf.set_font('helvetica', 'B', 11)
        title = f'Activity logs (newest {min(pdf_log_limit, len(log_rows))} of {len(log_rows)} in this export; 90-day window, 5000 max rows)'
        pdf.multi_cell(ew2, 5, _ascii_for_pdf(title))
        pdf.ln(1)
        pdf.set_font('helvetica', '', 6.5)
        for log in log_rows[:pdf_log_limit]:
            ts = log.timestamp.isoformat() if log.timestamp else ''
            act = (log.action or '')[:220].replace('\n', ' ').replace('\r', ' ')
            uid = log.user_id
            u_s = f'u{uid}' if uid is not None else '—'
            one = f'{ts}  [{u_s}]  {act}'
            if pdf.get_y() > 280:
                pdf.add_page()
                ew2 = pdf.epw
                pdf.set_font('helvetica', '', 6.5)
            pdf.multi_cell(ew2, 2.5, _ascii_for_pdf(one))

    raw = pdf.output(dest='S')
    if isinstance(raw, (bytes, bytearray)):
        out = BytesIO(bytes(raw))
    else:
        out = BytesIO(str(raw).encode('latin-1', 'replace'))
    return out


def normalize_command_text(s):
    if not s:
        return ''
    s = s.strip().lower()
    s = re.sub(r"[^\w\s']", ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


# (phrase substring in normalized command → Device.type value). Longer phrases first.
_TYPE_PHRASES_RAW = [
    ('air conditioner', 'AC'),
    ('ceiling fan', 'Fan'),
    ('exhaust fan', 'Fan'),
    ('smart lock', 'Lock'),
    ('door lock', 'Lock'),
    ('strip light', 'Light'),
    ('chandelier', 'Light'),
    ('bulbs', 'Light'),
    ('bulb', 'Light'),
    ('ambient light', 'Light'),
    ('lights', 'Light'),
    ('heater', 'Heater'),
    ('radiator', 'Heater'),
    ('thermostat', 'AC'),
    ('hvac', 'AC'),
    ('cooler', 'AC'),
    ('light', 'Light'),
    ('lamp', 'Light'),
    ('sconce', 'Light'),
    ('sconces', 'Light'),
    ('heat', 'Heater'),
    ('fans', 'Fan'),
    ('ventilation', 'Fan'),
    ('lock', 'Lock'),
    ('fan', 'Fan'),
    ('ac', 'AC'),
    ('outlet', 'Other'),
    ('plug', 'Other'),
]
TYPE_PHRASES_TO_MODEL = sorted(_TYPE_PHRASES_RAW, key=lambda x: -len(x[0]))


def _phrase_matches_target(phrase, t):
    """Whole-word / whole-phrase match in normalized command target."""
    if not phrase or not t:
        return False
    if t == phrase:
        return True
    return bool(re.search(rf'(^|\s){re.escape(phrase)}(\s|$)', t))


def parse_turn_command(raw):
    """
    Parse voice-style ON/OFF intent. Delegates to voice_commands (wake words,
    courtesy, 100+ prefixes, natural regex). Returns: ok, want_on, target, error.
    """
    n = normalize_command_text(raw)
    if not n:
        return {
            'ok': False,
            'want_on': None,
            'target': '',
            'error': 'Say something out loud — e.g. “Hey home, turn on the kitchen light.”',
        }
    return parse_voice_intent(n)


def resolve_device_from_target(target, devices):
    """
    Match target string to a Device using name overlap, then type keywords (light, fan, …).
    Returns (device_or_None, error_message_or_None).
    """
    if not devices:
        return None, 'Add at least one device before sending commands.'

    t = normalize_command_text(target)
    if not t:
        return None, 'Say what to control after the action, e.g. Turn off fan.'

    tokens = [w for w in t.split() if len(w) >= 2]

    best = None
    best_score = 0
    for d in devices:
        dn = normalize_command_text(d.name)
        score = 0
        if t == dn:
            score += 100
        elif t in dn or dn in t:
            score += 35
        for w in tokens:
            if len(w) >= 3 and w in dn:
                score += 6
            elif w in dn:
                score += 3
        if score > best_score:
            best_score = score
            best = d

    if best and best_score >= 35:
        return best, None
    if best and best_score >= 6 and tokens:
        return best, None

    matched_type = None
    for phrase, model_type in TYPE_PHRASES_TO_MODEL:
        if _phrase_matches_target(phrase, t):
            matched_type = model_type
            break

    if matched_type:
        cands = [d for d in devices if d.type == matched_type]
        if len(cands) == 1:
            return cands[0], None
        if len(cands) > 1:
            best2 = None
            s2 = -1
            for d in cands:
                dn = normalize_command_text(d.name)
                sc = sum(3 for w in tokens if w in dn)
                if sc > s2:
                    s2 = sc
                    best2 = d
            if best2 and s2 >= 3:
                return best2, None
            return cands[0], None
        return None, f'No {matched_type} device is registered.'

    if best and best_score >= 3:
        return best, None

    return None, f'Could not match "{target}" to a device. Try its exact name or a type: light, fan, AC, lock, heater.'


# --- Routes ---

@app.route('/')
@login_required
def dashboard():
    ensure_notification_category_column()
    show = (request.args.get('show') or '').strip().lower()
    if show not in ('', 'active'):
        show = ''
    device_filter_active_only = show == 'active'
    rooms = Room.query.all()
    devices = ordered_dashboard_devices(current_user.id, Device.query.all())
    on_devices = sum(1 for d in devices if d.status)
    logs = (
        Log.query.order_by(Log.timestamp.desc()).limit(5).all()
        if current_user.username == 'admin'
        else []
    )
    predictions = refresh_predictive_automations(current_user.id)
    prediction_cards = build_prediction_cards(predictions, limit=8)
    power_labels, power_kw = weekly_peak_kw_series()
    current_estimated_kw = round(compute_total_on_watts() / 1000.0, 3)
    ensure_user_energy_cost_column()
    energy_cost = energy_cost_display_for_user(current_user)
    smart_recs = build_smart_recommendations(devices, on_devices, current_estimated_kw)
    system_health = build_system_health(devices, on_devices)
    return render_template(
        'dashboard.html',
        rooms=rooms,
        devices=devices,
        on_devices=on_devices,
        logs=logs,
        prediction_cards=prediction_cards,
        power_chart_labels=power_labels,
        power_chart_kw=power_kw,
        current_estimated_kw=current_estimated_kw,
        energy_cost=energy_cost,
        smart_recommendations=smart_recs,
        system_health=system_health,
        device_filter_active_only=device_filter_active_only,
        dashboard_show=show,
    )

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = request.form.get('remember') == 'on'
        user = User.query.filter_by(username=username).first()
        if user and password is not None and check_password_hash(user.password, password):
            session.permanent = remember
            login_user(user, remember=remember)
            add_log(f"User {user.username} logged in.", user.id)
            next_url = request.form.get('next') or request.args.get('next')
            if next_url and next_url.startswith('/') and not next_url.startswith('//'):
                return redirect(next_url)
            return redirect(url_for('dashboard'))
        flash('Invalid username or password.', 'danger')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('register'))
        if password is None:
            flash('Password is required.', 'danger')
            return redirect(url_for('register'))
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(username=username, password=hashed_password)
        db.session.add(new_user)
        db.session.commit()
        flash('Account created successfully! You can now log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout', methods=['GET', 'POST'])
@login_required
def logout():
    add_log(f"User {current_user.username} logged out.", current_user.id)
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

@app.route('/rooms', methods=['GET', 'POST'])
@login_required
def rooms():
    if request.method == 'POST':
        room_name = request.form.get('name')
        if room_name:
            new_room = Room(name=room_name)
            db.session.add(new_room)
            db.session.commit()
            add_log(f"Added new room: {room_name}", current_user.id)
            add_notification(f'Room added: {room_name}', current_user.id, 'success')
            flash('Room added successfully!', 'success')
        return redirect(url_for('rooms'))
    all_rooms = Room.query.all()
    return render_template('rooms.html', rooms=all_rooms)

@app.route('/devices')
@login_required
def devices():
    all_devices = Device.query.all()
    rooms = Room.query.all()
    return render_template('devices.html', devices=all_devices, rooms=rooms)

@app.route('/devices/add', methods=['GET', 'POST'])
@login_required
def add_device():
    rooms = Room.query.order_by(Room.name).all()
    if request.method == 'POST':
        if not rooms:
            flash('Create at least one room before adding devices.', 'warning')
            return redirect(url_for('rooms'))
        name = (request.form.get('name') or '').strip()
        dtype = request.form.get('type')
        room_id = request.form.get('room_id')
        room = db.session.get(Room, int(room_id)) if room_id and str(room_id).isdigit() else None
        if name and dtype in ALLOWED_DEVICE_TYPES and room:
            new_device = Device(name=name, type=dtype, room_id=room.id)
            db.session.add(new_device)
            db.session.commit()
            add_log(f"Added new device: {name} in {room.name}", current_user.id)
            add_notification(f'Device added: {name} in {room.name}', current_user.id, 'success')
            record_energy_snapshot()
            flash(f'“{name}” is now on your dashboard.', 'success')
            return redirect(url_for('dashboard'))
        flash('Choose a name, type, and room to continue.', 'danger')
        return redirect(url_for('add_device'))
    return render_template('add_device.html', rooms=rooms)

@app.route('/toggle_device/<int:device_id>', methods=['POST'])
@login_required
def toggle_device(device_id):
    device = db.session.get(Device, device_id)
    if device is None:
        abort(404)
    device.status = not device.status
    db.session.commit()
    
    status_text = "ON" if device.status else "OFF"
    add_log(f"Turned {status_text} {device.name} in {device.room.name}", current_user.id)
    record_energy_snapshot()
    n = add_notification(
        f'{device.name} turned {status_text} ({device.room.name})',
        current_user.id,
        'success',
    )
    return jsonify(
        {
            'success': True,
            'status': device.status,
            'notification': notification_to_dict(n),
        }
    )


@app.route('/api/devices/status')
@login_required
def api_devices_status():
    """JSON snapshot of all device power state for real-time UI sync."""
    try:
        devs = Device.query.all()
        on_ct = sum(1 for d in devs if d.status)
        current_estimated_kw = round(compute_total_on_watts() / 1000.0, 3)
        smart_recs = build_smart_recommendations(devs, on_ct, current_estimated_kw)
        return jsonify({
            'ok': True,
            'devices': [{'id': d.id, 'status': bool(d.status)} for d in devs],
            'on_count': on_ct,
            'total_count': len(devs),
            'smart_recommendations': smart_recs
        })
    except Exception as exc:
        app.logger.error('api_devices_status error: %s', exc, exc_info=True)
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({'ok': False, 'devices': [], 'on_count': 0, 'total_count': 0}), 200


@app.post('/api/dashboard/layout')
@login_required
def save_dashboard_layout():
    """Persist the order of dashboard device card columns for the current user."""
    ensure_user_dashboard_layout_table()
    data = request.get_json(silent=True) or {}
    order = data.get('order')
    if not isinstance(order, list):
        return jsonify({'ok': False, 'error': 'order must be a list'}), 400
    all_ids = {r[0] for r in Device.query.with_entities(Device.id).all()}
    if not all_ids and not order:
        return jsonify({'ok': True})
    try:
        wanted = [int(x) for x in order]
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'invalid device ids'}), 400
    if set(wanted) != all_ids or len(wanted) != len(all_ids):
        return jsonify({'ok': False, 'error': 'order must list each device exactly once'}), 400
    row = db.session.get(UserDashboardLayout, current_user.id)
    if row is None:
        row = UserDashboardLayout(user_id=current_user.id, device_order_json='[]')
        db.session.add(row)
    row.device_order_json = json.dumps(wanted)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/ai_chat', methods=['POST'])
@login_required
def ai_chat():
    """
    AI assistant — voice/text queries AND device control.
    Control is attempted FIRST so short speech like 'fan on', 'off the light' always works.
    Wrapped in try/except so any unexpected error returns a safe JSON response (no crash).
    """
    try:
        data = request.get_json(silent=True) or {}
        raw = (data.get('message') or '').strip()
        if not raw:
            return jsonify({'reply': 'Say something! Try "Turn on the fan" or "What\'s on?"', 'type': 'info', 'changed': False})

        devices = Device.query.all()
        on_devs  = [d for d in devices if d.status]
        off_devs = [d for d in devices if not d.status]
        lower = raw.lower()
        now_local = datetime.now().astimezone()
        hour = now_local.hour

        def _do_toggle(device, want_on, raw_cmd):
            """Toggle a device and return a jsonify response."""
            if bool(device.status) == want_on:
                state = 'ON' if want_on else 'OFF'
                return jsonify({'reply': f'ℹ️ **{device.name}** is already **{state}**.', 'type': 'info',
                                'changed': False, 'device_id': device.id, 'status': device.status})
            device.status = want_on
            state = 'ON' if want_on else 'OFF'
            msg = f'Turned {state} {device.name} in {device.room.name}.'
            db.session.add(Log(action=f'Command: {raw_cmd} → {msg}', user_id=current_user.id))
            db.session.add(CommandRecord(raw_text=raw_cmd[:500], action='on' if want_on else 'off',
                                         device_id=device.id, success=True, response_message=msg[:500],
                                         user_id=current_user.id))
            db.session.commit()
            record_energy_snapshot()
            n = add_notification(msg, current_user.id, 'success')
            emoji = '✅' if want_on else '🔴'
            reply = f'{emoji} Done! **{device.name}** ({device.room.name}) is now **{state}**.'
            return jsonify({'reply': reply, 'type': 'success', 'changed': True,
                            'device_id': device.id, 'status': want_on,
                            'on_count': sum(1 for d in Device.query.all() if d.status),
                            'notification': notification_to_dict(n)})

        # ── STEP 1: Try device control FIRST ─────────────────────────
        # This means "fan on", "turn off the light", "on AC" etc. always take priority.
        parsed = parse_turn_command(raw)
        if parsed.get('ok'):
            device, resolve_err = resolve_device_from_target(parsed['target'], devices)
            if device:
                return _do_toggle(device, parsed['want_on'], raw)
            # parsed ok but no device found — show friendly error and don't fall to query intents
            if parsed['target']:
                return jsonify({
                    'reply': f'🔍 I couldn\'t find a device matching **"{parsed["target"]}"**. '
                             f'Available: {", ".join(d.name for d in devices[:6]) or "no devices yet"}.',
                    'type': 'warning', 'changed': False
                })

        # ── STEP 2: Pure query intents ────────────────────────────────

        # Greetings
        if re.search(r'\b(hi|hello|hey|howdy|sup|good\s*(morning|evening|night|afternoon))\b', lower):
            greeting = 'Good morning' if hour < 12 else ('Good afternoon' if hour < 17 else 'Good evening')
            return jsonify({
                'reply': (f"{greeting}, {current_user.username}! \U0001f44b "
                          "I'm your Smart Home AI. Say 'turn on the fan', 'what's on?', "
                          "'show energy' or anything — I understand natural speech!"),
                'type': 'success', 'changed': False
            })

        # What devices are ON?
        if re.search(r'\b(what|which|show|list|tell me).*(on|active|running|powered)\b', lower):
            if not on_devs:
                return jsonify({'reply': '✅ All devices are currently **OFF**. Your home is in standby mode.', 'type': 'success', 'changed': False})
            lines = '\n'.join(f'• {d.name} ({d.room.name})' for d in on_devs)
            return jsonify({'reply': f'💡 **{len(on_devs)} device(s) currently ON:**\n{lines}', 'type': 'info', 'changed': False})

        # What devices are OFF?
        if re.search(r'\b(what|which|show|list|tell me).*(off|inactive|standby)\b', lower):
            if not off_devs:
                return jsonify({'reply': '⚡ All devices are currently **ON**!', 'type': 'warning', 'changed': False})
            lines = '\n'.join(f'• {d.name} ({d.room.name})' for d in off_devs)
            return jsonify({'reply': f'🔌 **{len(off_devs)} device(s) currently OFF:**\n{lines}', 'type': 'info', 'changed': False})

        # Full status / summary
        if re.search(r'\b(status|summary|overview|all devices|device list|show all|list all|report)\b', lower):
            kw = round(compute_total_on_watts() / 1000.0, 2)
            on_lines  = '\n'.join(f'  ✅ {d.name} — {d.room.name} [{d.type}]' for d in on_devs) or '  (none)'
            off_lines = '\n'.join(f'  🔴 {d.name} — {d.room.name} [{d.type}]' for d in off_devs) or '  (none)'
            return jsonify({
                'reply': (f'🏠 **Home Status — {len(devices)} devices | Load: ~{kw} kW**\n\n'
                          f'**ON ({len(on_devs)}):**\n{on_lines}\n\n'
                          f'**OFF ({len(off_devs)}):**\n{off_lines}'),
                'type': 'info', 'changed': False
            })

        # Energy
        if re.search(r'\b(energy|power|kw|watt|consumption|usage|electricity|load)\b', lower):
            kw = round(compute_total_on_watts() / 1000.0, 2)
            kwh_today = round(estimate_kwh_for_local_date(now_local.date()), 3)
            avg_d = round(average_daily_kwh_recent(7), 3)
            return jsonify({'reply': (f'⚡ **Energy Report:**\n'
                                      f'• Current load: **{kw} kW**\n'
                                      f'• Today: **{kwh_today} kWh**\n'
                                      f'• 7-day avg: **{avg_d} kWh/day**\n'
                                      f'• ON: {len(on_devs)} of {len(devices)} devices'),
                            'type': 'info', 'changed': False})

        # Tips
        if re.search(r'\b(tip|recommend|advice|suggest|save|reduce|help)\b', lower):
            kw = round(compute_total_on_watts() / 1000.0, 2)
            recs = build_smart_recommendations(devices, len(on_devs), kw)
            if not recs:
                return jsonify({'reply': '👍 Usage looks great! No specific tips right now.', 'type': 'success', 'changed': False})
            lines = '\n'.join(f'• **{r["title"]}**: {r["body"]}' for r in recs[:3])
            return jsonify({'reply': f'💡 **Smart Tips:**\n{lines}', 'type': 'info', 'changed': False})

        # Rooms
        if re.search(r'\b(room|rooms|which room|what room|how many rooms)\b', lower):
            rooms = Room.query.all()
            lines = ', '.join(f'{r.name} ({len(r.devices)} device{"s" if len(r.devices)!=1 else ""})' for r in rooms)
            return jsonify({'reply': f'🏠 **Rooms ({len(rooms)}):** {lines or "No rooms yet."}', 'type': 'info', 'changed': False})

        # How many devices
        if re.search(r'\bhow many (device|light|fan|ac|lock|heater)', lower):
            for dtype in ('Light', 'Fan', 'AC', 'Lock', 'Heater'):
                if dtype.lower() in lower:
                    count = sum(1 for d in devices if d.type == dtype)
                    on_c  = sum(1 for d in devices if d.type == dtype and d.status)
                    return jsonify({'reply': f'🔢 **{count} {dtype}(s)** — {on_c} ON, {count-on_c} OFF.', 'type': 'info', 'changed': False})
            return jsonify({'reply': f'🔢 **{len(devices)} device(s)** — {len(on_devs)} ON, {len(off_devs)} OFF.', 'type': 'info', 'changed': False})

        # Time
        if re.search(r'\b(time|clock|what time|schedule)\b', lower):
            ts = now_local.strftime('%I:%M %p, %A %d %B %Y')
            return jsonify({'reply': f'🕐 It is **{ts}**.', 'type': 'info', 'changed': False})

        # ── STEP 3: Bulk ON / OFF ─────────────────────────────────────
        if re.search(r'\b(all|everything|every device|entire|whole)\b', lower) and re.search(r'\b(on|off|start|stop|activate|deactivate)\b', lower):
            want_on = bool(re.search(r'\b(on|start|activate|enable)\b', lower)) and not bool(re.search(r'\boff\b|\bstop\b|\bdeactivate\b', lower))
            changed_devs = [d for d in devices if bool(d.status) != want_on]
            for d in changed_devs:
                d.status = want_on
            if changed_devs:
                db.session.commit()
                record_energy_snapshot()
                state = 'ON' if want_on else 'OFF'
                names = ', '.join(d.name for d in changed_devs[:5])
                suffix = '…' if len(changed_devs) > 5 else ''
                msg = f'Turned {state} {len(changed_devs)} device(s): {names}{suffix}.'
                add_log(msg, user_id=current_user.id)
                add_notification(msg, current_user.id, 'success' if want_on else 'warning')
                emoji = '⚡' if want_on else '🌙'
                return jsonify({'reply': f'{emoji} **{msg}**', 'type': 'success', 'changed': True,
                                'on_count': sum(1 for d in devices if d.status)})
            state = 'ON' if want_on else 'OFF'
            return jsonify({'reply': f'ℹ️ All devices are already **{state}**.', 'type': 'info', 'changed': False})

        # ── STEP 4: Fallback — friendly help ─────────────────────────
        return jsonify({
            'reply': (
                "🤖 I didn't quite catch that. Try:\n\n"
                "• **Control**: \"fan on\", \"turn off light\", \"switch on AC\"\n"
                "• **Bulk**: \"turn everything off\", \"all lights on\"\n"
                "• **Status**: \"what's on?\", \"show all devices\"\n"
                "• **Energy**: \"show energy usage\"\n"
                "• **Tips**: \"give me tips\"\n"
                "• **Rooms**: \"show rooms\""
            ),
            'type': 'info', 'changed': False
        })
    except Exception as exc:
        app.logger.error('ai_chat error: %s', exc, exc_info=True)
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({
            'reply': '⚠️ Something went wrong on my end. Please try again.',
            'type': 'danger', 'changed': False
        }), 200


@app.route('/command', methods=['POST'])
@login_required
def process_command():
    data = request.get_json(silent=True) or {}
    raw = (data.get('command') or '').strip()
    devices = Device.query.all()

    def persist_failure(message):
        db.session.add(
            CommandRecord(
                raw_text=(raw or '(empty)')[:500],
                action=None,
                device_id=None,
                success=False,
                response_message=message[:500],
                user_id=current_user.id,
            )
        )
        db.session.add(
            Log(
                action=f"Command failed: {raw or '(empty)'} — {message}"[:255],
                user_id=current_user.id,
            )
        )
        db.session.commit()
        n = add_notification(f'Command failed: {message}', current_user.id, 'danger')
        return jsonify(
            {
                'success': False,
                'message': message,
                'changed': False,
                'notification': notification_to_dict(n),
            }
        )

    if not raw:
        return persist_failure('Enter a command, e.g. Turn on light.')

    parsed = parse_turn_command(raw)
    if not parsed['ok']:
        return persist_failure(parsed.get('error') or 'Could not understand that command.')

    device, resolve_err = resolve_device_from_target(parsed['target'], devices)
    if not device:
        return persist_failure(resolve_err or 'No matching device.')

    want_on = parsed['want_on']
    action_str = 'on' if want_on else 'off'

    if device.status == want_on:
        msg = f'{device.name} is already {"ON" if want_on else "OFF"}.'
        db.session.add(
            CommandRecord(
                raw_text=raw[:500],
                action=action_str,
                device_id=device.id,
                success=True,
                response_message=msg[:500],
                user_id=current_user.id,
            )
        )
        db.session.add(
            Log(
                action=f"Command: {raw} → {msg}"[:255],
                user_id=current_user.id,
            )
        )
        db.session.commit()
        n = add_notification(msg, current_user.id, 'info')
        return jsonify(
            {
                'success': True,
                'message': msg,
                'device_id': device.id,
                'status': device.status,
                'changed': False,
                'notification': notification_to_dict(n),
            }
        )

    device.status = want_on
    msg = f'Turned {"ON" if want_on else "OFF"} {device.name} ({device.room.name}).'
    db.session.add(
        CommandRecord(
            raw_text=raw[:500],
            action=action_str,
            device_id=device.id,
            success=True,
            response_message=msg[:500],
            user_id=current_user.id,
        )
    )
    db.session.add(
        Log(
            action=f"Command: {raw} → {msg}"[:255],
            user_id=current_user.id,
        )
    )
    db.session.commit()
    record_energy_snapshot()
    n = add_notification(msg, current_user.id, 'success')
    return jsonify(
        {
            'success': True,
            'message': msg,
            'device_id': device.id,
            'status': device.status,
            'changed': True,
            'notification': notification_to_dict(n),
        }
    )

@app.route('/logs')
@login_required
def view_logs():
    if current_user.username != 'admin':
        flash('Activity logs are available to administrators only.', 'danger')
        return redirect(url_for('dashboard'))

    q = (request.args.get('q') or '').strip()
    state_filter = (request.args.get('state') or 'all').lower()
    if state_filter not in ('all', 'on', 'off'):
        state_filter = 'all'

    query = Log.query.order_by(Log.timestamp.desc())
    if q:
        like = f'%{q}%'
        query = query.filter(Log.action.ilike(like))

    logs = query.all()
    rows = []
    for log in logs:
        device_label, st, _ = parse_log_for_ui(log.action)
        if state_filter == 'on' and st != 'on':
            continue
        if state_filter == 'off' and st != 'off':
            continue
        rows.append({'log': log, 'device': device_label, 'state': st})

    return render_template(
        'logs.html',
        rows=rows,
        search_q=q,
        state_filter=state_filter,
    )

@app.route('/predictions')
@login_required
def predictions():
    """Full list of stored predictive automations (refreshed from logs on each visit)."""
    ensure_notification_category_column()
    preds = refresh_predictive_automations(current_user.id)
    prediction_cards = build_prediction_cards(preds)
    return render_template('predictions.html', prediction_cards=prediction_cards)


@app.route('/analytics')
@login_required
def analytics():
    weekly_labels, weekly_kw = weekly_peak_kw_series()
    room_labels, room_watts = room_on_power_breakdown()
    room_colors = chart_palette(len(room_labels)) if room_labels else []
    total_on_w = sum(room_watts) if room_watts else 0
    usage = device_usage_analytics()
    device_total = Device.query.count()
    room_total = Room.query.count()
    cmd_total = CommandRecord.query.filter(CommandRecord.success.is_(True)).count()
    hm = usage_heatmap_from_logs(42)
    return render_template(
        'analytics.html',
        weekly_labels=weekly_labels,
        weekly_kw=weekly_kw,
        room_labels=room_labels,
        room_watts=room_watts,
        room_colors=room_colors,
        total_on_watts=total_on_w,
        snapshot_count=EnergySnapshot.query.count(),
        usage_chart_labels=usage['usage_chart_labels'],
        usage_chart_values=usage['usage_chart_values'],
        usage_chart_colors=usage['usage_chart_colors'],
        total_usage_events=usage['total_usage_events'],
        most_used_device=usage['most_used_device'],
        most_used_count=usage['most_used_count'],
        on_device_count=usage['on_device_count'],
        device_total=device_total,
        room_total=room_total,
        successful_commands_total=cmd_total,
        heatmap_matrix=hm['matrix'],
        heatmap_max=hm['max_count'],
        heatmap_peak_label=hm['peak_label'],
        heatmap_days=hm['window_days'],
    )


@app.route('/analytics/export')
@login_required
def analytics_export():
    """Download device usage + energy as ZIP/PDF; raw activity_logs attachment is admin-only."""
    fmt = (request.args.get('format') or 'csv').lower()
    weekly_labels, weekly_kw = weekly_peak_kw_series()
    room_labels, room_watts = room_on_power_breakdown()
    total_on_w = sum(room_watts) if room_watts else 0
    usage = device_usage_analytics()
    snapshot_c = EnergySnapshot.query.count()
    energy_rows = _report_energy_daily_rows(30)
    log_rows = (
        _fetch_logs_for_report(5000, 90)
        if current_user.username == 'admin'
        else []
    )
    stamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')

    if fmt == 'csv':
        buf = _build_report_csv_zip(
            usage, weekly_labels, weekly_kw, energy_rows, log_rows, total_on_w, snapshot_c, current_user.username
        )
        return send_file(
            buf,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f'smart_home_report_{stamp}.zip',
        )
    if fmt == 'pdf':
        buf = _build_report_pdf(
            usage,
            weekly_labels,
            weekly_kw,
            energy_rows,
            log_rows,
            room_labels,
            room_watts,
            total_on_w,
            snapshot_c,
            current_user.username,
        )
        return send_file(
            buf,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'smart_home_report_{stamp}.pdf',
        )
    flash('Use format=csv or format=pdf.', 'warning')
    return redirect(url_for('analytics'))


def parse_schedule_time(raw):
    """Normalize browser time input to HH:MM, or None if invalid."""
    if not raw:
        return None
    raw = raw.strip()
    parts = raw.replace('.', ':').split(':')
    if len(parts) < 2:
        return None
    try:
        h = int(parts[0])
        m = int(parts[1][:2])
    except ValueError:
        return None
    if not (0 <= h < 24 and 0 <= m < 60):
        return None
    return f'{h:02d}:{m:02d}'


@app.route('/schedules', methods=['GET', 'POST'])
@login_required
def schedules():
    ensure_schedule_last_fired_column()
    devices = (
        Device.query.join(Room, Device.room_id == Room.id)
        .order_by(Room.name, Device.name)
        .all()
    )
    if request.method == 'POST':
        device_id = request.form.get('device_id', '').strip()
        action_raw = (request.form.get('action') or '').lower().strip()
        time_raw = request.form.get('time') or ''
        if not device_id.isdigit():
            flash('Choose a device.', 'danger')
            return redirect(url_for('schedules'))
        dev = Device.query.filter_by(id=int(device_id)).first()
        if not dev:
            flash('Device not found.', 'danger')
            return redirect(url_for('schedules'))
        if action_raw not in ('on', 'off'):
            flash('Action must be ON or OFF.', 'danger')
            return redirect(url_for('schedules'))
        want_on = action_raw == 'on'
        hhmm = parse_schedule_time(time_raw)
        if not hhmm:
            flash('Enter a valid time.', 'danger')
            return redirect(url_for('schedules'))
        sch = Schedule(device_id=dev.id, action=want_on, time=hhmm, active=True)
        db.session.add(sch)
        db.session.commit()
        add_notification(
            f'Schedule saved: {dev.name} → {"ON" if want_on else "OFF"} at {hhmm}',
            current_user.id,
            'info',
        )
        flash(f'Saved schedule: {dev.name} → {"ON" if want_on else "OFF"} at {hhmm}.', 'success')
        return redirect(url_for('schedules'))

    rows = Schedule.query.order_by(Schedule.time, Schedule.id).all()
    return render_template('schedules.html', devices=devices, schedules=rows)


@app.post('/schedules/<int:sid>/delete')
@login_required
def delete_schedule(sid):
    sch = db.session.get(Schedule, sid)
    if sch is None:
        abort(404)
    db.session.delete(sch)
    db.session.commit()
    add_notification('A schedule was removed.', current_user.id, 'info')
    flash('Schedule removed.', 'info')
    return redirect(url_for('schedules'))


@app.post('/schedules/<int:sid>/toggle')
@login_required
def toggle_schedule(sid):
    sch = db.session.get(Schedule, sid)
    if sch is None:
        abort(404)
    sch.active = not sch.active
    db.session.commit()
    flash('Schedule updated.', 'success')
    return redirect(url_for('schedules'))


@app.route('/automation', methods=['GET', 'POST'])
@login_required
def automation_rules():
    ensure_automation_rule_table()
    devices = (
        Device.query.join(Room, Device.room_id == Room.id)
        .order_by(Room.name, Device.name)
        .options(joinedload(Device.room))
        .all()
    )
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()[:150]
        active = request.form.get('active') == 'on'
        cid = (request.form.get('cond_device_id') or '').strip()
        cond_device_id = int(cid) if cid.isdigit() else None
        cond_device_want_on = (request.form.get('cond_device_want') or 'on').lower() != 'off'
        ta = parse_schedule_time(request.form.get('cond_time_after') or '')
        tb = parse_schedule_time(request.form.get('cond_time_before') or '')
        action_kind = (request.form.get('action_kind') or 'device').lower()
        aid = (request.form.get('action_device_id') or '').strip()
        action_device_id = int(aid) if aid.isdigit() else None
        atype = (request.form.get('action_device_type') or '').strip()
        action_set_on = (request.form.get('action_want') or 'off').lower() == 'on'

        if cond_device_id is not None and not db.session.get(Device, cond_device_id):
            flash('Invalid device in condition.', 'danger')
            return redirect(url_for('automation_rules'))
        if not (cond_device_id is not None or ta or tb):
            flash('Add at least one condition: device state and/or a time window.', 'danger')
            return redirect(url_for('automation_rules'))
        if action_kind == 'device':
            if not action_device_id or not db.session.get(Device, action_device_id):
                flash('Choose a target device.', 'danger')
                return redirect(url_for('automation_rules'))
            act_id, act_type = action_device_id, None
        else:
            if atype not in ALLOWED_DEVICE_TYPES:
                flash('Choose a valid device type for the action.', 'danger')
                return redirect(url_for('automation_rules'))
            act_id, act_type = None, atype

        rule = AutomationRule(
            user_id=current_user.id,
            name=name,
            active=active,
            cond_device_id=cond_device_id,
            cond_device_want_on=cond_device_want_on,
            cond_time_after=ta,
            cond_time_before=tb,
            action_device_id=act_id,
            action_device_type=act_type,
            action_set_on=action_set_on,
        )
        db.session.add(rule)
        db.session.commit()
        flash('Automation rule saved.', 'success')
        return redirect(url_for('automation_rules'))

    rules = (
        AutomationRule.query.filter_by(user_id=current_user.id)
        .options(
            joinedload(AutomationRule.cond_device).joinedload(Device.room),
            joinedload(AutomationRule.action_device).joinedload(Device.room),
        )
        .order_by(AutomationRule.id.desc())
        .all()
    )
    return render_template(
        'automation.html',
        devices=devices,
        rules=rules,
        device_types=sorted(ALLOWED_DEVICE_TYPES),
    )


@app.post('/automation/<int:rid>/delete')
@login_required
def delete_automation_rule(rid):
    ensure_automation_rule_table()
    r = AutomationRule.query.filter_by(id=rid, user_id=current_user.id).first_or_404()
    db.session.delete(r)
    db.session.commit()
    flash('Rule deleted.', 'info')
    return redirect(url_for('automation_rules'))


@app.post('/automation/<int:rid>/toggle')
@login_required
def toggle_automation_rule(rid):
    ensure_automation_rule_table()
    r = AutomationRule.query.filter_by(id=rid, user_id=current_user.id).first_or_404()
    r.active = not r.active
    db.session.commit()
    flash('Rule updated.', 'success')
    return redirect(url_for('automation_rules'))


@app.post('/predictions/<int:pid>/enable')
@login_required
def enable_prediction(pid):
    ensure_prediction_table()
    p = Prediction.query.filter_by(id=pid, user_id=current_user.id).first_or_404()

    existing = Schedule.query.filter_by(
        device_id=p.device_id,
        action=bool(p.action),
        time=p.predicted_time,
    ).first()
    if existing:
        existing.active = True
        p.schedule_id = existing.id
        p.auto_enabled = True
    else:
        sch = Schedule(
            device_id=p.device_id,
            action=bool(p.action),
            time=p.predicted_time,
            active=True,
        )
        db.session.add(sch)
        db.session.flush()
        p.schedule_id = sch.id
        p.auto_enabled = True

    db.session.commit()
    st = 'ON' if p.action else 'OFF'
    tlabel = _hhmm_to_display(p.predicted_time)
    dname = p.device.name if p.device else 'device'
    add_log(f'Predictive automation enabled: {dname} → {st} at {p.predicted_time}', current_user.id)
    add_notification(
        f'Auto-enabled: {dname} → {st} at {tlabel}.',
        current_user.id,
        'success',
    )
    flash(f'Auto-enabled prediction: {dname} → {st} at {tlabel}.', 'success')
    ref_path = (urlparse(request.referrer or '').path or '').rstrip('/')
    if ref_path.endswith('/predictions'):
        return redirect(url_for('predictions'))
    return redirect(url_for('dashboard'))


@app.route('/modes', methods=['GET', 'POST'])
@login_required
def saved_modes():
    if request.method == 'POST':
        raw = (request.form.get('name') or '').strip()
        if not raw:
            flash('Enter a name for the mode.', 'danger')
            return redirect(url_for('saved_modes'))
        if len(raw) > 100:
            raw = raw[:100]
        exists = CustomMode.query.filter_by(user_id=current_user.id, name=raw).first()
        if exists:
            flash('You already have a mode with that name.', 'danger')
            return redirect(url_for('saved_modes'))
        m = CustomMode(user_id=current_user.id, name=raw)
        db.session.add(m)
        db.session.commit()
        flash(f'Created “{raw}”. Pick which devices this mode controls, then Apply from the list.', 'success')
        return redirect(url_for('edit_saved_mode', mid=m.id))

    modes = (
        CustomMode.query.filter_by(user_id=current_user.id)
        .options(
            joinedload(CustomMode.assignments).joinedload(CustomModeDevice.device),
        )
        .order_by(CustomMode.name)
        .all()
    )
    return render_template('modes.html', modes=modes)


@app.route('/modes/<int:mid>/edit', methods=['GET', 'POST'])
@login_required
def edit_saved_mode(mid):
    mode = CustomMode.query.filter_by(id=mid, user_id=current_user.id).first_or_404()
    devices = (
        Device.query.join(Room, Device.room_id == Room.id)
        .order_by(Room.name, Device.name)
        .all()
    )
    assign_by_device = {a.device_id: a for a in mode.assignments}

    if request.method == 'POST':
        for d in devices:
            if request.form.get(f'include_{d.id}') == '1':
                want_on = (request.form.get(f'target_{d.id}', 'off') or 'off').lower() == 'on'
                row = assign_by_device.get(d.id)
                if row:
                    row.want_on = want_on
                else:
                    db.session.add(
                        CustomModeDevice(custom_mode_id=mode.id, device_id=d.id, want_on=want_on)
                    )
            else:
                row = assign_by_device.get(d.id)
                if row:
                    db.session.delete(row)
        db.session.commit()
        flash('Device assignments saved.', 'success')
        return redirect(url_for('saved_modes'))

    return render_template(
        'mode_edit.html',
        mode=mode,
        devices=devices,
        assign_by_device=assign_by_device,
    )


@app.post('/modes/<int:mid>/apply')
@login_required
def apply_saved_mode(mid):
    mode = (
        CustomMode.query.filter_by(id=mid, user_id=current_user.id)
        .options(joinedload(CustomMode.assignments).joinedload(CustomModeDevice.device).joinedload(Device.room))
        .first_or_404()
    )
    if not mode.assignments:
        flash('This mode has no devices yet. Edit the mode to add some.', 'warning')
        return redirect(url_for('saved_modes'))
    n = 0
    for row in mode.assignments:
        dev = row.device
        if not dev:
            continue
        dev.status = bool(row.want_on)
        n += 1
    db.session.commit()
    add_log(f'Applied mode “{mode.name}”: set {n} device(s).', current_user.id)
    record_energy_snapshot()
    add_notification(f'Applied mode "{mode.name}" ({n} device(s)).', current_user.id, 'success')
    flash(f'Applied “{mode.name}” to {n} device(s).', 'success')
    return redirect(url_for('saved_modes'))


@app.post('/modes/<int:mid>/delete')
@login_required
def delete_saved_mode(mid):
    mode = CustomMode.query.filter_by(id=mid, user_id=current_user.id).first_or_404()
    label = mode.name
    db.session.delete(mode)
    db.session.commit()
    add_notification(f'Deleted mode "{label}".', current_user.id, 'info')
    flash(f'Deleted mode “{label}”.', 'info')
    return redirect(url_for('saved_modes'))


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    ensure_user_energy_cost_column()
    if request.method == 'POST':
        if request.form.get('clear_rate') == '1':
            current_user.energy_cost_per_kwh = None
            db.session.commit()
            flash('Energy cost per kWh cleared.', 'info')
            return redirect(url_for('settings'))
        raw = (request.form.get('energy_cost_per_kwh') or '').strip()
        if raw == '':
            current_user.energy_cost_per_kwh = None
        else:
            try:
                v = float(raw)
                if v < 0 or v > 1000:
                    raise ValueError
                current_user.energy_cost_per_kwh = v
            except (ValueError, TypeError):
                flash('Enter a non-negative cost per kWh (e.g. 0.12).', 'danger')
                return redirect(url_for('settings'))
        db.session.commit()
        flash('Energy cost setting saved.', 'success')
        return redirect(url_for('settings'))
    return render_template('settings.html', energy_rate=current_user.energy_cost_per_kwh)

@app.route('/notifications/read/<int:notif_id>', methods=['POST'])
@login_required
def read_notification(notif_id):
    notif = db.session.get(Notification, notif_id)
    if notif and notif.user_id == current_user.id:
        notif.read = True
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'success': False}), 400

@app.route('/admin_db')
@login_required
def admin_db():
    if current_user.username != 'admin':
        flash('Access denied. Admins only.', 'danger')
        return redirect(url_for('dashboard'))

    users = User.query.order_by(User.id).all()
    rooms = Room.query.order_by(Room.id).all()
    devices = Device.query.options(joinedload(Device.room)).order_by(Device.id).all()
    logs = Log.query.order_by(Log.timestamp.desc()).limit(500).all()
    return render_template(
        'admin_db.html',
        users=users,
        rooms=rooms,
        devices=devices,
        logs=logs,
    )

def init_database():
    """
    Create or update the SQLite file (database.db): all tables, schema patches,
    default admin user, optional energy snapshot baseline.
    Safe to run multiple times.
    """
    db.create_all()
    ensure_schedule_last_fired_column()
    ensure_notification_category_column()
    ensure_prediction_table()
    ensure_user_dashboard_layout_table()
    ensure_automation_rule_table()
    ensure_user_energy_cost_column()
    if not User.query.filter_by(username='admin').first():
        hashed_pw = generate_password_hash('admin', method='pbkdf2:sha256')
        admin = User(username='admin', password=hashed_pw)
        db.session.add(admin)
        db.session.commit()
    if EnergySnapshot.query.count() == 0 and Device.query.count() > 0:
        record_energy_snapshot()


# ── Global error handlers — server NEVER crashes, always returns JSON/HTML ──

@app.errorhandler(404)
def not_found_error(e):
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({'ok': False, 'error': 'Not found', 'code': 404}), 404
    return render_template('base.html'), 404


@app.errorhandler(500)
def internal_error(e):
    app.logger.error('Unhandled 500: %s', e, exc_info=True)
    try:
        db.session.rollback()
    except Exception:
        pass
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({'ok': False, 'error': 'Internal server error', 'code': 500}), 500
    return render_template('base.html'), 500


@app.errorhandler(Exception)
def unhandled_exception(e):
    """Catch-all: prevent the server from dying on any unhandled exception."""
    app.logger.error('Unhandled exception: %s', e, exc_info=True)
    try:
        db.session.rollback()
    except Exception:
        pass
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({'ok': False, 'error': str(e) or 'Unexpected error', 'code': 500}), 500
    try:
        return render_template('base.html'), 500
    except Exception:
        return '<h1>500 — Internal Server Error</h1><p>Please refresh and try again.</p>', 500


if __name__ == '__main__':
    with app.app_context():
        os.makedirs(app.instance_path, exist_ok=True)
        init_database()
    _port = int(os.environ.get('PORT', 5005))
    _debug = os.environ.get('FLASK_ENV', 'development') != 'production'
    app.run(debug=_debug, host='0.0.0.0', port=_port)
