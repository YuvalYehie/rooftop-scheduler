#!/usr/bin/env python3
"""
Rooftop Scheduler — Tornado web server
"""
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tornado.ioloop
import tornado.web

# ── Load .env if present ────────────────────────────────────────────────────
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from email_service import send_confirmation, send_reminder  # noqa: E402

PORT = int(os.environ.get("PORT", 3000))
BASE_DIR = Path(__file__).parent

# ── Database ────────────────────────────────────────────────────────────────
_local = threading.local()

DB_PATH = os.environ.get("DB_PATH", str(BASE_DIR / "bookings.db"))

def get_db():
    if not hasattr(_local, "db"):
        db = sqlite3.connect(DB_PATH, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                building      TEXT NOT NULL,
                apartment     TEXT NOT NULL,
                name          TEXT NOT NULL,
                email         TEXT NOT NULL,
                title         TEXT NOT NULL,
                description   TEXT NOT NULL DEFAULT '',
                start_time    TEXT NOT NULL,
                end_time      TEXT NOT NULL,
                edit_token    TEXT NOT NULL UNIQUE,
                reminder_sent INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )
        """)
        db.commit()
        _local.db = db
    return _local.db


def row_to_dict(row):
    return dict(row) if row else None


def public_booking(b: dict) -> dict:
    """Strip secret fields before sending to clients."""
    return {k: v for k, v in b.items() if k not in ("edit_token", "reminder_sent")}


# ── Validation ──────────────────────────────────────────────────────────────
def validate(data: dict, booking_id: int = -1):
    required = ["building", "apartment", "name", "email", "title", "start_time", "end_time"]
    for f in required:
        if not data.get(f, "").strip():
            return f"Missing required field: {f}"
    try:
        start = datetime.fromisoformat(data["start_time"].replace("Z", "+00:00"))
        end   = datetime.fromisoformat(data["end_time"].replace("Z", "+00:00"))
    except ValueError:
        return "Invalid date format"
    if end <= start:
        return "End time must be after start time"
    now = datetime.now(timezone.utc)
    if start < now:
        return "Cannot book in the past"
    if start > now + timedelta(days=30):
        return "Bookings can only be made up to 30 days in advance"
    if "@" not in data["email"]:
        return "Invalid email address"
    return None


def check_conflict(db, start_time: str, end_time: str, exclude_id: int = -1):
    row = db.execute(
        "SELECT id, title, name, apartment, start_time, end_time FROM bookings "
        "WHERE start_time < ? AND end_time > ? AND id != ? LIMIT 1",
        (end_time, start_time, exclude_id),
    ).fetchone()
    return row_to_dict(row)


# ── Base handler ────────────────────────────────────────────────────────────
class BaseHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header("Content-Type", "application/json")

    def write_json(self, data, status=200):
        self.set_status(status)
        self.write(json.dumps(data, default=str))

    def parse_json(self):
        try:
            return json.loads(self.request.body)
        except Exception:
            return {}

    @property
    def db(self):
        return get_db()


# ── Handlers ────────────────────────────────────────────────────────────────
class BookingsHandler(BaseHandler):
    def get(self):
        start = self.get_argument("start", None)
        end   = self.get_argument("end", None)
        db    = self.db
        if start and end:
            rows = db.execute(
                "SELECT * FROM bookings WHERE start_time < ? AND end_time > ? ORDER BY start_time",
                (end, start),
            ).fetchall()
        else:
            now = datetime.now(timezone.utc).isoformat()
            rows = db.execute(
                "SELECT * FROM bookings WHERE end_time >= ? ORDER BY start_time",
                (now,),
            ).fetchall()
        self.write_json([public_booking(row_to_dict(r)) for r in rows])

    def post(self):
        data = self.parse_json()
        # Normalise strings
        for f in ["building","apartment","name","email","title","description","start_time","end_time"]:
            data[f] = str(data.get(f, "")).strip()

        err = validate(data)
        if err:
            return self.write_json({"error": err}, 400)

        conflict = check_conflict(self.db, data["start_time"], data["end_time"])
        if conflict:
            return self.write_json({
                "error": "Time slot already booked",
                "conflict": {
                    "title":      conflict["title"],
                    "organizer":  conflict["name"],
                    "apartment":  conflict["apartment"],
                    "start_time": conflict["start_time"],
                    "end_time":   conflict["end_time"],
                }
            }, 409)

        token = str(uuid.uuid4())
        db = self.db
        cur = db.execute(
            "INSERT INTO bookings (building,apartment,name,email,title,description,"
            "start_time,end_time,edit_token) VALUES (?,?,?,?,?,?,?,?,?)",
            (data["building"], data["apartment"], data["name"], data["email"].lower(),
             data["title"], data["description"], data["start_time"], data["end_time"], token),
        )
        db.commit()
        booking = row_to_dict(db.execute("SELECT * FROM bookings WHERE id=?", (cur.lastrowid,)).fetchone())

        # Send confirmation email in background thread
        threading.Thread(target=send_confirmation, args=(booking,), daemon=True).start()

        self.write_json({**public_booking(booking), "edit_token": token}, 201)


class BookingHandler(BaseHandler):
    def get(self, booking_id):
        row = self.db.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
        if not row:
            return self.write_json({"error": "Booking not found"}, 404)
        self.write_json(public_booking(row_to_dict(row)))

    def put(self, booking_id):
        data = self.parse_json()
        token = data.pop("edit_token", "").strip()
        if not token:
            return self.write_json({"error": "edit_token required"}, 401)

        db = self.db
        existing = row_to_dict(db.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone())
        if not existing:
            return self.write_json({"error": "Booking not found"}, 404)
        if existing["edit_token"] != token:
            return self.write_json({"error": "Invalid token"}, 403)

        for f in ["building","apartment","name","email","title","description","start_time","end_time"]:
            data[f] = str(data.get(f, "")).strip()

        err = validate(data, int(booking_id))
        if err:
            return self.write_json({"error": err}, 400)

        conflict = check_conflict(db, data["start_time"], data["end_time"], int(booking_id))
        if conflict:
            return self.write_json({
                "error": "Time slot already booked",
                "conflict": {
                    "title":      conflict["title"],
                    "organizer":  conflict["name"],
                    "apartment":  conflict["apartment"],
                    "start_time": conflict["start_time"],
                    "end_time":   conflict["end_time"],
                }
            }, 409)

        db.execute(
            "UPDATE bookings SET building=?,apartment=?,name=?,email=?,title=?,"
            "description=?,start_time=?,end_time=? WHERE id=? AND edit_token=?",
            (data["building"], data["apartment"], data["name"], data["email"].lower(),
             data["title"], data["description"], data["start_time"], data["end_time"],
             booking_id, token),
        )
        db.commit()
        updated = row_to_dict(db.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone())
        self.write_json({**public_booking(updated), "edit_token": token})

    def delete(self, booking_id):
        data = self.parse_json()
        token = data.get("edit_token", "").strip()
        if not token:
            return self.write_json({"error": "edit_token required"}, 401)

        db = self.db
        existing = row_to_dict(db.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone())
        if not existing:
            return self.write_json({"error": "Booking not found"}, 404)
        if existing["edit_token"] != token:
            return self.write_json({"error": "Invalid token"}, 403)

        db.execute("DELETE FROM bookings WHERE id=? AND edit_token=?", (booking_id, token))
        db.commit()
        self.write_json({"success": True})


class ManageByTokenHandler(BaseHandler):
    def get(self, token):
        row = self.db.execute("SELECT * FROM bookings WHERE edit_token=?", (token,)).fetchone()
        if not row:
            return self.write_json({"error": "Booking not found or token invalid"}, 404)
        b = row_to_dict(row)
        self.write_json({**public_booking(b), "edit_token": b["edit_token"]})


# ── Reminder scheduler ──────────────────────────────────────────────────────
def send_reminders():
    db = get_db()
    now = datetime.now(timezone.utc)
    cutoff = (now + timedelta(hours=25)).isoformat()
    rows = db.execute(
        "SELECT * FROM bookings WHERE reminder_sent=0 AND start_time > ? AND start_time <= ?",
        (now.isoformat(), cutoff),
    ).fetchall()
    for row in rows:
        b = row_to_dict(row)
        try:
            send_reminder(b)
            db.execute("UPDATE bookings SET reminder_sent=1 WHERE id=?", (b["id"],))
            db.commit()
            print(f"[cron] Reminder sent: booking {b['id']} — {b['title']}")
        except Exception as e:
            print(f"[cron] Reminder failed for {b['id']}: {e}")


# ── App & routes ────────────────────────────────────────────────────────────
def make_app():
    public_dir = str(BASE_DIR / "public")
    return tornado.web.Application([
        (r"/api/bookings",       BookingsHandler),
        (r"/api/bookings/(\d+)", BookingHandler),
        (r"/api/manage/([^/]+)", ManageByTokenHandler),
        (r"/(.*)", tornado.web.StaticFileHandler, {
            "path": public_dir,
            "default_filename": "index.html",
        }),
    ], debug=False)


if __name__ == "__main__":
    # Ensure DB is initialised
    get_db()

    app = make_app()
    app.listen(PORT)

    # Reminder check every hour (3 600 000 ms)
    scheduler = tornado.ioloop.PeriodicCallback(send_reminders, 3_600_000)
    scheduler.start()

    has_key = bool(os.environ.get("SENDGRID_API_KEY"))
    print(f"\n🏙️  Rooftop Scheduler  →  http://localhost:{PORT}")
    print(f"   Email : {'✅ SendGrid configured' if has_key else '⚠️  No SENDGRID_API_KEY — emails logged only'}")
    print(f"   DB    : {BASE_DIR / 'bookings.db'}\n")

    tornado.ioloop.IOLoop.current().start()
