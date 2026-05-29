"""
MongoDB storage layer with a SQLAlchemy-like API for the Smart Home app.
Set MONGODB_URI (MongoDB Atlas connection string) to use this backend.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Optional

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

_client: Optional[MongoClient] = None
_db: Optional[Database] = None


def get_db() -> Database:
    global _client, _db
    if _db is not None:
        return _db
    uri = (os.environ.get('MONGODB_URI') or os.environ.get('MONGO_URI') or '').strip()
    if not uri:
        raise RuntimeError('MONGODB_URI is not set')
    _client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    _db = _client.get_default_database()
    if _db is None:
        _db = _client['smart_home']
    return _db


def _next_id(collection: str) -> int:
    db = get_db()
    doc = db.counters.find_one_and_update(
        {'_id': collection},
        {'$inc': {'seq': 1}},
        upsert=True,
        return_document=True,
    )
    return int(doc['seq'])


def _dt(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace('Z', '+00:00').replace('+00:00', ''))
        except ValueError:
            return v
    return v


class Column:
    def __init__(self, name: str):
        self.name = name

    def __eq__(self, other):
        return ('eq', self.name, other)

    def __ne__(self, other):
        return ('ne', self.name, other)

    def __ge__(self, other):
        return ('ge', self.name, other)

    def __le__(self, other):
        return ('le', self.name, other)

    def __gt__(self, other):
        return ('gt', self.name, other)

    def __lt__(self, other):
        return ('lt', self.name, other)

    def is_(self, val):
        return ('eq', self.name, val)

    def isnot(self, val):
        return ('ne', self.name, val)

    def desc(self):
        return (self.name, DESCENDING)

    def asc(self):
        return (self.name, ASCENDING)


class _OrExpr:
    def __init__(self, left, right):
        self.parts = []
        for p in (left, right):
            if isinstance(p, _OrExpr):
                self.parts.extend(p.parts)
            else:
                self.parts.append(p)

    def __or__(self, other):
        return _OrExpr(self, other)


def _or_(*args):
    parts = []
    for a in args:
        if isinstance(a, _OrExpr):
            parts.extend(a.parts)
        else:
            parts.append(a)
    o = _OrExpr(None, None)
    o.parts = parts
    return o


class func:
    @staticmethod
    def date(col):
        return ('date', col.name if isinstance(col, Column) else col)

    @staticmethod
    def max(col):
        return ('max', col.name if isinstance(col, Column) else col)

    @staticmethod
    def count(col):
        return ('count', col.name if isinstance(col, Column) else col)


class Doc:
    __collection__: str = ''
    id = Column('id')

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    @classmethod
    def query(cls):
        return Query(cls)

    def _to_doc(self) -> dict:
        skip = {'query', '_room', '_device', '_assignments', '_cond_device', '_action_device', '_user'}
        out = {}
        for k, v in self.__dict__.items():
            if k.startswith('_') or k in skip:
                continue
            out[k] = v
        return out

    @classmethod
    def _from_doc(cls, doc: dict):
        if not doc:
            return None
        obj = cls()
        for k, v in doc.items():
            if k == '_id':
                continue
            setattr(obj, k, v)
        return obj


class User(Doc):
    __collection__ = 'users'
    username = Column('username')
    password = Column('password')
    mode = Column('mode')
    energy_cost_per_kwh = Column('energy_cost_per_kwh')

    def get_id(self):
        return str(self.id)

    @property
    def is_authenticated(self):
        return True

    @property
    def is_active(self):
        return True

    @property
    def is_anonymous(self):
        return False


class Room(Doc):
    __collection__ = 'rooms'
    name = Column('name')

    @property
    def devices(self):
        return Device.query.filter_by(room_id=self.id).all()


class Device(Doc):
    __collection__ = 'devices'
    name = Column('name')
    type = Column('type')
    status = Column('status')
    room_id = Column('room_id')

    @property
    def room(self):
        if getattr(self, '_room', None) is not None:
            return self._room
        self._room = db.session.get(Room, self.room_id)
        return self._room


class Log(Doc):
    __collection__ = 'logs'
    timestamp = Column('timestamp')
    action = Column('action')
    user_id = Column('user_id')


class Schedule(Doc):
    __collection__ = 'schedules'
    device_id = Column('device_id')
    action = Column('action')
    time = Column('time')
    active = Column('active')
    last_fired_at = Column('last_fired_at')

    @property
    def device(self):
        return db.session.get(Device, self.device_id)


class Prediction(Doc):
    __collection__ = 'predictions'
    user_id = Column('user_id')
    device_id = Column('device_id')
    action = Column('action')
    predicted_time = Column('predicted_time')
    confidence = Column('confidence')
    sample_days = Column('sample_days')
    auto_enabled = Column('auto_enabled')
    schedule_id = Column('schedule_id')
    last_detected_at = Column('last_detected_at')
    created_at = Column('created_at')
    updated_at = Column('updated_at')

    @property
    def device(self):
        return db.session.get(Device, self.device_id)


class Notification(Doc):
    __collection__ = 'notifications'
    message = Column('message')
    timestamp = Column('timestamp')
    read = Column('read')
    user_id = Column('user_id')
    category = Column('category')


class EnergySnapshot(Doc):
    __collection__ = 'energy_snapshots'
    recorded_at = Column('recorded_at')
    total_watts = Column('total_watts')


class CommandRecord(Doc):
    __collection__ = 'command_records'
    created_at = Column('created_at')
    raw_text = Column('raw_text')
    action = Column('action')
    device_id = Column('device_id')
    success = Column('success')
    response_message = Column('response_message')
    user_id = Column('user_id')

    @property
    def device(self):
        return db.session.get(Device, self.device_id) if self.device_id else None


class CustomMode(Doc):
    __collection__ = 'custom_modes'
    user_id = Column('user_id')
    name = Column('name')

    @property
    def assignments(self):
        return CustomModeDevice.query.filter_by(custom_mode_id=self.id).all()

    @property
    def user(self):
        return db.session.get(User, self.user_id)


class CustomModeDevice(Doc):
    __collection__ = 'custom_mode_devices'
    custom_mode_id = Column('custom_mode_id')
    device_id = Column('device_id')
    want_on = Column('want_on')

    @property
    def custom_mode(self):
        return db.session.get(CustomMode, self.custom_mode_id)

    @property
    def device(self):
        return db.session.get(Device, self.device_id)


class UserDashboardLayout(Doc):
    __collection__ = 'user_dashboard_layouts'
    user_id = Column('user_id')
    device_order_json = Column('device_order_json')
    updated_at = Column('updated_at')

    @property
    def user(self):
        return db.session.get(User, self.user_id)


class AutomationRule(Doc):
    __collection__ = 'automation_rules'
    user_id = Column('user_id')
    name = Column('name')
    active = Column('active')
    cond_device_id = Column('cond_device_id')
    cond_device_want_on = Column('cond_device_want_on')
    cond_time_after = Column('cond_time_after')
    cond_time_before = Column('cond_time_before')
    action_device_id = Column('action_device_id')
    action_device_type = Column('action_device_type')
    action_set_on = Column('action_set_on')
    created_at = Column('created_at')
    last_fired_at = Column('last_fired_at')

    @property
    def cond_device(self):
        return db.session.get(Device, self.cond_device_id) if self.cond_device_id else None

    @property
    def action_device(self):
        return db.session.get(Device, self.action_device_id) if self.action_device_id else None


def _match(doc: dict, filters: list) -> bool:
    for f in filters:
        if f[0] == 'eq':
            if doc.get(f[1]) != f[2]:
                return False
        elif f[0] == 'ne':
            if doc.get(f[1]) == f[2]:
                return False
        elif f[0] == 'ge':
            if doc.get(f[1]) is None or doc.get(f[1]) < f[2]:
                return False
        elif f[0] == 'le':
            if doc.get(f[1]) is None or doc.get(f[1]) > f[2]:
                return False
        elif f[0] == 'gt':
            if doc.get(f[1]) is None or doc.get(f[1]) <= f[2]:
                return False
        elif f[0] == 'lt':
            if doc.get(f[1]) is None or doc.get(f[1]) >= f[2]:
                return False
        elif f[0] == 'or':
            if not any(_match(doc, [p]) for p in f[1]):
                return False
    return True


class Query:
    def __init__(self, model: type[Doc]):
        self.model = model
        self._filters: list = []
        self._order: list = []
        self._limit: Optional[int] = None
        self._join_room = False
        self._entities: Optional[list] = None

    def filter_by(self, **kwargs):
        q = self._clone()
        for k, v in kwargs.items():
            q._filters.append(('eq', k, v))
        return q

    def filter(self, *args):
        q = self._clone()
        for arg in args:
            if isinstance(arg, _OrExpr):
                q._filters.append(('or', arg.parts))
            elif isinstance(arg, tuple) and len(arg) == 3:
                q._filters.append(arg)
        return q

    def order_by(self, *args):
        q = self._clone()
        for arg in args:
            if isinstance(arg, tuple) and len(arg) == 2:
                q._order.append(arg)
            elif isinstance(arg, Column):
                q._order.append((arg.name, ASCENDING))
            elif hasattr(arg, 'name'):
                q._order.append((arg.name, ASCENDING))
        return q

    def limit(self, n):
        q = self._clone()
        q._limit = n
        return q

    def join(self, other, on_clause=None):
        q = self._clone()
        if other is Room:
            q._join_room = True
        return q

    def with_entities(self, *cols):
        q = self._clone()
        q._entities = list(cols)
        return q

    def options(self, *args):
        return self

    def _clone(self):
        q = Query(self.model)
        q._filters = list(self._filters)
        q._order = list(self._order)
        q._limit = self._limit
        q._join_room = self._join_room
        q._entities = self._entities
        return q

    def _fetch(self) -> list[dict]:
        coll = get_db()[self.model.__collection__]
        mongo_filter = {}
        for f in self._filters:
            if f[0] == 'eq':
                mongo_filter[f[1]] = f[2]
            elif f[0] == 'ge':
                mongo_filter.setdefault(f[1], {})
                if isinstance(mongo_filter[f[1]], dict):
                    mongo_filter[f[1]]['$gte'] = f[2]
            elif f[0] == 'ne':
                mongo_filter[f[1]] = {'$ne': f[2]}
        docs = list(coll.find(mongo_filter))
        docs = [d for d in docs if _match(d, self._filters)]
        if self._join_room and self.model is Device:
            rooms = {r['id']: r for r in get_db().rooms.find()}
            for d in docs:
                d['_room_name'] = rooms.get(d.get('room_id'), {}).get('name', '')
            key_fn = lambda d: (d.get('_room_name', ''), d.get('name', ''))
            docs.sort(key=key_fn)
        elif self._order:
            for field, direction in reversed(self._order):
                docs.sort(key=lambda d, f=field: d.get(f) or 0, reverse=(direction == DESCENDING))
        elif self.model is Room:
            docs.sort(key=lambda d: d.get('name', ''))
        elif self.model is User:
            docs.sort(key=lambda d: d.get('id', 0))
        if self._limit is not None:
            docs = docs[: self._limit]
        return docs

    def first(self):
        rows = self.limit(1)._fetch()
        return self.model._from_doc(rows[0]) if rows else None

    def all(self):
        if self._entities:
            docs = self._fetch()
            out = []
            for d in docs:
                row = []
                for col in self._entities:
                    if isinstance(col, Column):
                        if col.name == 'action' and self.model is Log:
                            row.append((d.get('action'),))
                        else:
                            row.append(d.get(col.name))
                    elif col is Device.id:
                        row.append(d.get('id'))
                    elif col is Device.name:
                        row.append(d.get('name'))
                    elif col is Room.name:
                        if self.model is Device:
                            room = db.session.get(Room, d.get('room_id'))
                            row.append(room.name if room else '')
                        else:
                            row.append(d.get('name'))
                if len(row) == 1 and isinstance(row[0], tuple):
                    out.append(row[0])
                elif len(row) == 3:
                    out.append(tuple(row))
                else:
                    out.append(tuple(row) if len(row) > 1 else row[0])
            return out
        return [self.model._from_doc(d) for d in self._fetch()]

    def count(self):
        return len(self._fetch())

    def delete(self):
        docs = self._fetch()
        ids = [d['id'] for d in docs]
        if ids:
            get_db()[self.model.__collection__].delete_many({'id': {'$in': ids}})


class MongoSession:
    def __init__(self):
        self._pending: list[tuple[str, Doc]] = []
        self._deletes: list[tuple[str, int]] = []

    def get(self, model: type[Doc], pk):
        if model is UserDashboardLayout:
            doc = get_db()[model.__collection__].find_one({'user_id': int(pk)})
        else:
            doc = get_db()[model.__collection__].find_one({'id': int(pk)})
        return model._from_doc(doc)

    def add(self, obj: Doc):
        coll = obj.__class__.__collection__
        if getattr(obj, 'id', None) is None:
            obj.id = _next_id(coll)
        if isinstance(obj, Log) and getattr(obj, 'timestamp', None) is None:
            obj.timestamp = datetime.utcnow()
        if isinstance(obj, Notification):
            if getattr(obj, 'timestamp', None) is None:
                obj.timestamp = datetime.utcnow()
            if getattr(obj, 'category', None) is None:
                obj.category = 'info'
        if isinstance(obj, EnergySnapshot) and getattr(obj, 'recorded_at', None) is None:
            obj.recorded_at = datetime.utcnow()
        if isinstance(obj, CommandRecord) and getattr(obj, 'created_at', None) is None:
            obj.created_at = datetime.utcnow()
        if isinstance(obj, AutomationRule) and getattr(obj, 'created_at', None) is None:
            obj.created_at = datetime.utcnow()
        if isinstance(obj, Prediction):
            now = datetime.utcnow()
            if getattr(obj, 'created_at', None) is None:
                obj.created_at = now
            if getattr(obj, 'updated_at', None) is None:
                obj.updated_at = now
        if isinstance(obj, UserDashboardLayout):
            if getattr(obj, 'updated_at', None) is None:
                obj.updated_at = datetime.utcnow()
            obj.id = obj.user_id
        self._pending.append((coll, obj))

    def delete(self, obj: Doc):
        if getattr(obj, 'id', None) is not None:
            self._deletes.append((obj.__class__.__collection__, obj.id))

    def commit(self):
        dbi = get_db()
        for coll, obj in self._pending:
            doc = obj._to_doc()
            dbi[coll].replace_one({'id': obj.id}, doc, upsert=True)
        for coll, oid in self._deletes:
            dbi[coll].delete_one({'id': oid})
        self._pending.clear()
        self._deletes.clear()

    def rollback(self):
        self._pending.clear()
        self._deletes.clear()

    def query(self, *entities):
        return AggregateQuery(entities)


class AggregateQuery:
    def __init__(self, entities):
        self.entities = entities
        self._filters = []
        self._group = None

    def filter(self, *args):
        q = AggregateQuery(self.entities)
        q._filters = list(self._filters) + list(args)
        q._group = self._group
        return q

    def group_by(self, *args):
        q = AggregateQuery(self.entities)
        q._filters = list(self._filters)
        q._group = args[0] if args else None
        return q

    def all(self):
        if CommandRecord in [e for e in self.entities if isinstance(e, type)] or (
            len(self.entities) == 2 and hasattr(self.entities[1], '__name__')
        ):
            return _command_record_counts(self._filters)
        if EnergySnapshot in [e for e in self.entities if isinstance(e, type)] or any(
            isinstance(e, tuple) and e[0] == 'date' for e in self.entities
        ):
            return _energy_daily_peaks(self._filters)
        return []


def _command_record_counts(filters):
    rows = list(get_db().command_records.find({'success': True, 'device_id': {'$ne': None}}))
    for f in filters:
        if isinstance(f, tuple) and f[0] == 'eq':
            rows = [r for r in rows if r.get(f[1]) == f[2]]
    counts = defaultdict(int)
    for r in rows:
        if r.get('device_id') is not None:
            counts[r['device_id']] += 1
    return [(did, cnt) for did, cnt in counts.items()]


def _energy_daily_peaks(filters):
    start_dt = None
    for f in filters:
        if isinstance(f, tuple) and f[0] == 'ge':
            start_dt = f[2]
    rows = list(get_db().energy_snapshots.find())
    if start_dt:
        rows = [r for r in rows if _dt(r.get('recorded_at')) and _dt(r.get('recorded_at')) >= start_dt]
    by_day = defaultdict(int)
    for r in rows:
        dt = _dt(r.get('recorded_at'))
        if not dt:
            continue
        day = dt.date().isoformat()
        by_day[day] = max(by_day[day], int(r.get('total_watts') or 0))

    class Row:
        def __init__(self, d, peak):
            self.d = d
            self.peak = peak

    return [Row(d, p) for d, p in sorted(by_day.items())]


class MongoDB:
    Model = Doc
    session = MongoSession()

    def create_all(self):
        init_database()

    def Column(self, *args, **kwargs):
        return Column(args[0] if args else kwargs.get('name', 'id'))

    def backref(self, name, **kwargs):
        return None

    def relationship(self, *args, **kwargs):
        return None

    def UniqueConstraint(self, *args, **kwargs):
        return None

    def ForeignKey(self, *args, **kwargs):
        return None

    def Integer(self):
        return None

    def String(self, *args, **kwargs):
        return None

    def Boolean(self, **kwargs):
        return None

    def Float(self, **kwargs):
        return None

    def DateTime(self, **kwargs):
        return None

    def Text(self):
        return None


db = MongoDB()


def init_database():
    """Create indexes and seed default admin user."""
    dbi = get_db()
    indexes = {
        'users': [('username', ASCENDING)],
        'devices': [('room_id', ASCENDING)],
        'logs': [('timestamp', DESCENDING)],
        'notifications': [('user_id', ASCENDING), ('read', ASCENDING)],
        'schedules': [('device_id', ASCENDING), ('active', ASCENDING)],
        'predictions': [('user_id', ASCENDING)],
        'energy_snapshots': [('recorded_at', DESCENDING)],
        'command_records': [('device_id', ASCENDING)],
        'custom_modes': [('user_id', ASCENDING)],
        'automation_rules': [('user_id', ASCENDING), ('active', ASCENDING)],
    }
    for coll, specs in indexes.items():
        for spec in specs:
            if isinstance(spec, tuple):
                dbi[coll].create_index([spec])
            else:
                dbi[coll].create_index(spec)

    from werkzeug.security import generate_password_hash

    if not User.query.filter_by(username='admin').first():
        admin = User(
            username='admin',
            password=generate_password_hash('admin', method='pbkdf2:sha256'),
            mode='Day',
        )
        db.session.add(admin)
        db.session.commit()


class _FilterExpr(tuple):
    def __or__(self, other):
        return _OrExpr(self, other)


def _patch_columns():
    for name in ('__eq__', '__ne__', '__ge__', '__le__', '__gt__', '__lt__'):
        op = getattr(Column, name)

        def make(op=op):
            def method(self, other):
                return _FilterExpr(op(self, other))

            return method

        setattr(Column, name, make())


_patch_columns()

# Export or_ for app compatibility
db.or_ = _or_
