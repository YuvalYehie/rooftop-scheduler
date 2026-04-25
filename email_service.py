"""
Email service — supports Gmail (SMTP) and SendGrid (REST).
Priority: Gmail → SendGrid → log only.

Gmail setup (recommended):
  GMAIL_USER=you@gmail.com
  GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx   # from myaccount.google.com/apppasswords

SendGrid setup (alternative):
  SENDGRID_API_KEY=SG.xxxxx
  FROM_EMAIL=you@yourdomain.com
"""
import json
import os
import smtplib
import urllib.error
import urllib.request
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _fmt(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    return dt.strftime("%A, %B %-d %Y at %H:%M")


def _send_gmail(to_email: str, subject: str, text: str, html: str) -> bool:
    """Send via Gmail SMTP. Returns True on success."""
    gmail_user = os.environ.get("GMAIL_USER", "").strip()
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    if not gmail_user or not gmail_pass:
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Rooftop Scheduler <{gmail_user}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, to_email, msg.as_string())
        print(f"[Email ✅ Gmail] To: {to_email} | {subject}")
        return True
    except smtplib.SMTPAuthenticationError:
        print("[Email ❌] Gmail auth failed — check GMAIL_USER and GMAIL_APP_PASSWORD")
        return False
    except Exception as e:
        print(f"[Email ❌] Gmail error: {e}")
        return False


def _send_sendgrid(to_email: str, subject: str, text: str, html: str) -> bool:
    """Send via SendGrid REST API. Returns True on success."""
    api_key    = os.environ.get("SENDGRID_API_KEY", "").strip()
    from_email = os.environ.get("FROM_EMAIL", "").strip()
    from_name  = os.environ.get("FROM_NAME", "Rooftop Scheduler")
    if not api_key or not from_email:
        return False

    payload = json.dumps({
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": from_email, "name": from_name},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": text},
            {"type": "text/html",  "value": html},
        ],
    }).encode()

    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        print(f"[Email ✅ SendGrid] To: {to_email} | {subject}")
        return True
    except urllib.error.HTTPError as e:
        print(f"[Email ❌] SendGrid {e.code}: {e.read().decode()}")
        return False
    except Exception as e:
        print(f"[Email ❌] SendGrid error: {e}")
        return False


def _send(to_email: str, subject: str, text: str, html: str) -> None:
    """Try Gmail first, then SendGrid, then log."""
    if _send_gmail(to_email, subject, text, html):
        return
    if _send_sendgrid(to_email, subject, text, html):
        return
    print(f"[Email – not configured] To: {to_email} | {subject}")
    print("  Add GMAIL_USER + GMAIL_APP_PASSWORD to your .env to enable emails.")


def send_confirmation(booking: dict) -> None:
    app_url  = os.environ.get("APP_URL", "http://localhost:3000")
    building = os.environ.get("BUILDING_NAME", "Our Building")
    manage_url = f"{app_url}/?token={booking['edit_token']}"
    name  = booking["name"]
    title = booking["title"]
    start = _fmt(booking["start_time"])
    end   = _fmt(booking["end_time"])
    desc_row = (
        f"<tr><td style='padding:8px 0;color:#6b7280;vertical-align:top'>Notes</td>"
        f"<td>{booking['description']}</td></tr>"
        if booking.get("description") else ""
    )

    subject = f"\u2705 Rooftop Booking Confirmed \u2014 {title}"
    text = f"""Hi {name},

Your rooftop booking at {building} is confirmed!

Event:     {title}
Starts:    {start}
Ends:      {end}
Apartment: Building {booking['building']}, Apt {booking['apartment']}
{('Notes: ' + booking['description']) if booking.get('description') else ''}

To edit or cancel your booking, visit:
{manage_url}

Keep this link safe \u2014 it's your key to manage your reservation.

See you on the roof! \U0001f3d9\ufe0f
""".strip()

    html = f"""<!DOCTYPE html><html><body style="font-family:sans-serif;max-width:520px;margin:auto;padding:24px;color:#1a1a1a">
  <div style="background:#10b981;padding:20px 24px;border-radius:12px 12px 0 0">
    <h1 style="color:#fff;margin:0;font-size:22px">\u2705 Rooftop Booking Confirmed</h1>
  </div>
  <div style="border:1px solid #e5e7eb;border-top:none;padding:24px;border-radius:0 0 12px 12px">
    <p style="margin-top:0">Hi <strong>{name}</strong>,</p>
    <p>Your rooftop reservation at <strong>{building}</strong> is confirmed!</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <tr><td style="padding:8px 0;color:#6b7280;width:90px">Event</td><td><strong>{title}</strong></td></tr>
      <tr><td style="padding:8px 0;color:#6b7280">Starts</td><td>{start}</td></tr>
      <tr><td style="padding:8px 0;color:#6b7280">Ends</td><td>{end}</td></tr>
      <tr><td style="padding:8px 0;color:#6b7280">Apartment</td><td>Bldg {booking['building']}, Apt {booking['apartment']}</td></tr>
      {desc_row}
    </table>
    <a href="{manage_url}" style="display:inline-block;background:#10b981;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600">Manage My Booking</a>
    <p style="margin-top:20px;font-size:13px;color:#9ca3af">Keep this email \u2014 the link above lets you edit or cancel your reservation at any time.</p>
  </div>
</body></html>"""

    _send(booking["email"], subject, text, html)


def send_magic_link(booking: dict) -> None:
    app_url    = os.environ.get("APP_URL", "http://localhost:3000")
    manage_url = f"{app_url}/?token={booking['edit_token']}"
    name  = booking["name"]
    title = booking["title"]
    start = _fmt(booking["start_time"])

    subject = f"\U0001f511 Your booking management link \u2014 {title}"
    text = f"""Hi {name},

You requested a link to manage your rooftop booking.

Event:  {title}
Starts: {start}

Click the link below to edit or cancel your booking:
{manage_url}

If you didn't request this, you can safely ignore this email.
""".strip()

    html = f"""<!DOCTYPE html><html><body style="font-family:sans-serif;max-width:520px;margin:auto;padding:24px;color:#1a1a1a">
  <div style="background:#6366f1;padding:20px 24px;border-radius:12px 12px 0 0">
    <h1 style="color:#fff;margin:0;font-size:22px">\U0001f511 Booking Management Link</h1>
  </div>
  <div style="border:1px solid #e5e7eb;border-top:none;padding:24px;border-radius:0 0 12px 12px">
    <p style="margin-top:0">Hi <strong>{name}</strong>,</p>
    <p>You requested a link to manage your rooftop booking.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <tr><td style="padding:8px 0;color:#6b7280;width:90px">Event</td><td><strong>{title}</strong></td></tr>
      <tr><td style="padding:8px 0;color:#6b7280">Starts</td><td>{start}</td></tr>
    </table>
    <a href="{manage_url}" style="display:inline-block;background:#6366f1;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600">Edit / Cancel My Booking</a>
    <p style="margin-top:20px;font-size:13px;color:#9ca3af">If you didn't request this, you can safely ignore this email.</p>
  </div>
</body></html>"""

    _send(booking["email"], subject, text, html)


def send_reminder(booking: dict) -> None:
    app_url    = os.environ.get("APP_URL", "http://localhost:3000")
    manage_url = f"{app_url}/?token={booking['edit_token']}"
    name  = booking["name"]
    title = booking["title"]
    start = _fmt(booking["start_time"])
    end   = _fmt(booking["end_time"])

    subject = f"\u23f0 Reminder: Your rooftop event is tomorrow \u2014 {title}"
    text = f"""Hi {name},

Just a reminder that your rooftop event is coming up soon!

Event:  {title}
Starts: {start}
Ends:   {end}

Need to make changes? Visit:
{manage_url}

Enjoy your event! \U0001f306
""".strip()

    html = f"""<!DOCTYPE html><html><body style="font-family:sans-serif;max-width:520px;margin:auto;padding:24px;color:#1a1a1a">
  <div style="background:#f59e0b;padding:20px 24px;border-radius:12px 12px 0 0">
    <h1 style="color:#fff;margin:0;font-size:22px">\u23f0 Event Reminder</h1>
  </div>
  <div style="border:1px solid #e5e7eb;border-top:none;padding:24px;border-radius:0 0 12px 12px">
    <p style="margin-top:0">Hi <strong>{name}</strong>,</p>
    <p>Your rooftop event is <strong>coming up soon</strong>!</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <tr><td style="padding:8px 0;color:#6b7280;width:90px">Event</td><td><strong>{title}</strong></td></tr>
      <tr><td style="padding:8px 0;color:#6b7280">Starts</td><td>{start}</td></tr>
      <tr><td style="padding:8px 0;color:#6b7280">Ends</td><td>{end}</td></tr>
    </table>
    <a href="{manage_url}" style="display:inline-block;background:#f59e0b;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600">Manage My Booking</a>
  </div>
</body></html>"""

    _send(booking["email"], subject, text, html)
