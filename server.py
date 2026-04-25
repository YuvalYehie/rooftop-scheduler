#!/usr/bin/env python3
"""
Rooftop Scheduler — Tornado web server (PostgreSQL via Supabase)
"""
import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
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

from email_service import send_confirmation, send_magic_link, send_reminder  # noqa: E402

PORT    = int(os.environ.get("PORT", 3000))
BASE_DIR = Path(__file__).parent

# ── Database ────────────────────────────────────────────────────────────────
_db_lock = threading.Lock()
_db_conn = None

def get_db():
    global _db_conn
    with _db_lock:
        if _db_conn is None or _db_conn.closed:
            url = os.environ.get("DATABASE_URL")
            if not url:
                raise RuntimeError("DATABASE_URL environment variable is not set")
            _db_conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
            _db_conn.autocommit = False
            _init_schema(_db_conn)
    return _db_conn


def _init_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id            SERIAL PRIMARY KEY,
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
                created_at    TEXT NOT NULL DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'))
            )
        """)
    conn.commit()


def query(sql, params=(), one=False):
    """Run a SELECT and return dict(s)."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone() if one else cur.fetchall()


def execute(sql, params=()):
    """Run INSERT/UPDATE/DELETE. Returns fetchone() result if available (e.g. RETURNING)."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute(sql, params)
        try:
            result = cur.fetchone()
        except Exception:
            result = None
        db.commit()
        return result


def public_booking(b) -> dict:
    b = dict(b)
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


def check_conflict(start_time: str, end_time: str, exclude_id: int = -1):
    return query(
        "SELECT id, title, name, apartment, start_time, end_time FROM bookings "
        "WHERE start_time < %s AND end_time > %s AND id != %s LIMIT 1",
        (end_time, start_time, exclude_id),
        one=True,
    )


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


# ── Handlers ────────────────────────────────────────────────────────────────
class BookingsHandler(BaseHandler):
    def get(self):
        start = self.get_argument("start", None)
        end   = self.get_argument("end", None)
        if start and end:
            rows = query(
                "SELECT * FROM bookings WHERE start_time < %s AND end_time > %s ORDER BY start_time",
                (end, start),
            )
        else:
            now = datetime.now(timezone.utc).isoformat()
            rows = query(
                "SELECT * FROM bookings WHERE end_time >= %s ORDER BY start_time",
                (now,),
            )
        self.write_json([public_booking(r) for r in rows])

    def post(self):
        data = self.parse_json()
        for f in ["building","apartment","name","email","title","description","start_time","end_time"]:
            data[f] = str(data.get(f, "")).strip()

        err = validate(data)
        if err:
            return self.write_json({"error": err}, 400)

        conflict = check_conflict(data["start_time"], data["end_time"])
        if conflict:
            return self.write_json({
                "error": "Time slot already booked",
                "conflict": {k: conflict[k] for k in ("title","name","apartment","start_time","end_time")}
            }, 409)

        token = str(uuid.uuid4())
        result = execute(
            "INSERT INTO bookings (building,apartment,name,email,title,description,"
            "start_time,end_time,edit_token) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (data["building"], data["apartment"], data["name"], data["email"].lower(),
             data["title"], data["description"], data["start_time"], data["end_time"], token),
        )
        new_id = result["id"]
        booking = dict(query("SELECT * FROM bookings WHERE id=%s", (new_id,), one=True))

        threading.Thread(target=send_confirmation, args=(booking,), daemon=True).start()
        self.write_json({**public_booking(booking), "edit_token": token}, 201)


class BookingHandler(BaseHandler):
    def get(self, booking_id):
        row = query("SELECT * FROM bookings WHERE id=%s", (booking_id,), one=True)
        if not row:
            return self.write_json({"error": "Booking not found"}, 404)
        self.write_json(public_booking(row))

    def put(self, booking_id):
        data = self.parse_json()
        token = data.pop("edit_token", "").strip()
        if not token:
            return self.write_json({"error": "edit_token required"}, 401)

        existing = query("SELECT * FROM bookings WHERE id=%s", (booking_id,), one=True)
        if not existing:
            return self.write_json({"error": "Booking not found"}, 404)
        if existing["edit_token"] != token:
            return self.write_json({"error": "Invalid token"}, 403)

        for f in ["building","apartment","name","email","title","description","start_time","end_time"]:
            data[f] = str(data.get(f, "")).strip()

        err = validate(data, int(booking_id))
        if err:
            return self.write_json({"error": err}, 400)

        conflict = check_conflict(data["start_time"], data["end_time"], int(booking_id))
        if conflict:
            return self.write_json({
                "error": "Time slot already booked",
                "conflict": {k: conflict[k] for k in ("title","name","apartment","start_time","end_time")}
            }, 409)

        execute(
            "UPDATE bookings SET building=%s,apartment=%s,name=%s,email=%s,title=%s,"
            "description=%s,start_time=%s,end_time=%s WHERE id=%s AND edit_token=%s",
            (data["building"], data["apartment"], data["name"], data["email"].lower(),
             data["title"], data["description"], data["start_time"], data["end_time"],
             booking_id, token),
        )
        updated = query("SELECT * FROM bookings WHERE id=%s", (booking_id,), one=True)
        self.write_json({**public_booking(updated), "edit_token": token})

    def delete(self, booking_id):
        data = self.parse_json()
        token = data.get("edit_token", "").strip()
        if not token:
            return self.write_json({"error": "edit_token required"}, 401)

        existing = query("SELECT * FROM bookings WHERE id=%s", (booking_id,), one=True)
        if not existing:
            return self.write_json({"error": "Booking not found"}, 404)
        if existing["edit_token"] != token:
            return self.write_json({"error": "Invalid token"}, 403)

        execute("DELETE FROM bookings WHERE id=%s AND edit_token=%s", (booking_id, token))
        self.write_json({"success": True})


class ManageByTokenHandler(BaseHandler):
    def get(self, token):
        row = query("SELECT * FROM bookings WHERE edit_token=%s", (token,), one=True)
        if not row:
            return self.write_json({"error": "Booking not found or token invalid"}, 404)
        b = dict(row)
        self.write_json({**public_booking(b), "edit_token": b["edit_token"]})


class SendMagicLinkHandler(BaseHandler):
    def post(self):
        data = self.parse_json()
        booking_id = data.get("booking_id")
        if not booking_id:
            return self.write_json({"error": "Missing booking_id"}, 400)
        row = query("SELECT * FROM bookings WHERE id=%s", (booking_id,), one=True)
        if row:
            threading.Thread(
                target=send_magic_link, args=(dict(row),), daemon=True
            ).start()
        self.write_json({"success": True})


# ── Reminder scheduler ──────────────────────────────────────────────────────
def send_reminders():
    now    = datetime.now(timezone.utc)
    cutoff = (now + timedelta(hours=25)).isoformat()
    rows   = query(
        "SELECT * FROM bookings WHERE reminder_sent=0 AND start_time > %s AND start_time <= %s",
        (now.isoformat(), cutoff),
    )
    for row in rows:
        b = dict(row)
        try:
            send_reminder(b)
            execute("UPDATE bookings SET reminder_sent=1 WHERE id=%s", (b["id"],))
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
        (r"/api/send-magic-link", SendMagicLinkHandler),
        (r"/(.*)", tornado.web.StaticFileHandler, {
            "path": public_dir,
            "default_filename": "index.html",
        }),
    ], debug=False)


if __name__ == "__main__":
    get_db()  # connect & init schema on startup

    app = make_app()
    app.listen(PORT)

    scheduler = tornado.ioloop.PeriodicCallback(send_reminders, 3_600_000)
    scheduler.start()

    has_gmail = bool(os.environ.get("GMAIL_USER"))
    print(f"\n🏙️  Rooftop Scheduler  →  http://localhost:{PORT}")
    print(f"   Email : {'✅ Gmail configured' if has_gmail else '⚠️  No email configured'}")
    print(f"   DB    : Supabase (PostgreSQL)\n")

    tornado.ioloop.IOLoop.current().start()
