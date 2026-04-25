require('dotenv').config();
const express  = require('express');
const path     = require('path');
const cron     = require('node-cron');
const { v4: uuidv4 } = require('uuid');
const { stmts } = require('./database');
const { sendConfirmation, sendReminder } = require('./emailService');

const app  = express();
const PORT = process.env.PORT || 3000;

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// ─── Helpers ────────────────────────────────────────────────────────────────

function validate(body) {
  const required = ['building','apartment','name','email','title','start_time','end_time'];
  for (const f of required) {
    if (!body[f] || String(body[f]).trim() === '')
      return `Missing required field: ${f}`;
  }
  const start = new Date(body.start_time);
  const end   = new Date(body.end_time);
  if (isNaN(start) || isNaN(end)) return 'Invalid date format';
  if (end <= start) return 'End time must be after start time';
  if (start < new Date()) return 'Cannot book in the past';
  const maxDate = new Date();
  maxDate.setDate(maxDate.getDate() + 30);
  if (start > maxDate) return 'Bookings can only be made up to 30 days in advance';
  if (!body.email.includes('@')) return 'Invalid email address';
  return null;
}

// Strip the edit_token before sending to clients (except the creator)
function publicBooking(b) {
  const { edit_token, reminder_sent, ...pub } = b;
  return pub;
}

// ─── Routes ─────────────────────────────────────────────────────────────────

// GET /api/bookings?start=ISO&end=ISO
app.get('/api/bookings', (req, res) => {
  const { start, end } = req.query;
  try {
    const now = new Date().toISOString();
    const rows = (start && end)
      ? stmts.getRange.all(end, start)
      : stmts.getAll.all(now);
    res.json(rows.map(publicBooking));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// GET /api/bookings/:id  — full details (no token)
app.get('/api/bookings/:id', (req, res) => {
  const row = stmts.getById.get(req.params.id);
  if (!row) return res.status(404).json({ error: 'Booking not found' });
  res.json(publicBooking(row));
});

// POST /api/bookings — create
app.post('/api/bookings', async (req, res) => {
  const err = validate(req.body);
  if (err) return res.status(400).json({ error: err });

  const { building, apartment, name, email, title, description='', start_time, end_time } = req.body;

  // Conflict check
  const conflict = stmts.checkConflict.get(end_time, start_time, -1);
  if (conflict) {
    return res.status(409).json({
      error: 'Time slot already booked',
      conflict: {
        title:      conflict.title,
        organizer:  conflict.name,
        apartment:  conflict.apartment,
        start_time: conflict.start_time,
        end_time:   conflict.end_time,
      }
    });
  }

  const edit_token = uuidv4();
  try {
    const result = stmts.insert.run({
      building: building.trim(),
      apartment: apartment.trim(),
      name: name.trim(),
      email: email.trim().toLowerCase(),
      title: title.trim(),
      description: (description || '').trim(),
      start_time,
      end_time,
      edit_token,
    });

    const booking = stmts.getById.get(result.lastInsertRowid);

    // Send confirmation email (non-blocking)
    sendConfirmation(booking).catch(console.error);

    res.status(201).json({ ...publicBooking(booking), edit_token });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// PUT /api/bookings/:id — update (requires edit_token)
app.put('/api/bookings/:id', async (req, res) => {
  const { edit_token, ...body } = req.body;
  if (!edit_token) return res.status(401).json({ error: 'edit_token required' });

  const existing = stmts.getById.get(req.params.id);
  if (!existing) return res.status(404).json({ error: 'Booking not found' });
  if (existing.edit_token !== edit_token) return res.status(403).json({ error: 'Invalid token' });

  const err = validate(body);
  if (err) return res.status(400).json({ error: err });

  const { building, apartment, name, email, title, description='', start_time, end_time } = body;

  // Conflict check (exclude self)
  const conflict = stmts.checkConflict.get(end_time, start_time, existing.id);
  if (conflict) {
    return res.status(409).json({
      error: 'Time slot already booked',
      conflict: {
        title:      conflict.title,
        organizer:  conflict.name,
        apartment:  conflict.apartment,
        start_time: conflict.start_time,
        end_time:   conflict.end_time,
      }
    });
  }

  stmts.update.run({
    id: existing.id,
    edit_token,
    building: building.trim(),
    apartment: apartment.trim(),
    name: name.trim(),
    email: email.trim().toLowerCase(),
    title: title.trim(),
    description: (description || '').trim(),
    start_time,
    end_time,
  });

  const updated = stmts.getById.get(existing.id);
  res.json({ ...publicBooking(updated), edit_token });
});

// DELETE /api/bookings/:id — cancel (requires edit_token)
app.delete('/api/bookings/:id', (req, res) => {
  const { edit_token } = req.body;
  if (!edit_token) return res.status(401).json({ error: 'edit_token required' });

  const existing = stmts.getById.get(req.params.id);
  if (!existing) return res.status(404).json({ error: 'Booking not found' });
  if (existing.edit_token !== edit_token) return res.status(403).json({ error: 'Invalid token' });

  stmts.delete.run(existing.id, edit_token);
  res.json({ success: true });
});

// GET /api/manage/:token — look up a booking by token (for deep-link from email)
app.get('/api/manage/:token', (req, res) => {
  const row = stmts.getByToken.get(req.params.token);
  if (!row) return res.status(404).json({ error: 'Booking not found or token invalid' });
  res.json({ ...publicBooking(row), edit_token: row.edit_token });
});

// ─── Cron: send reminders every hour ────────────────────────────────────────
cron.schedule('0 * * * *', async () => {
  const pending = stmts.getPendingReminders.all();
  for (const booking of pending) {
    try {
      await sendReminder(booking);
      stmts.markReminderSent.run(booking.id);
      console.log(`[cron] Reminder sent for booking ${booking.id} — ${booking.title}`);
    } catch (err) {
      console.error(`[cron] Failed reminder for booking ${booking.id}:`, err.message);
    }
  }
});

// ─── Start ───────────────────────────────────────────────────────────────────
app.listen(PORT, () => {
  console.log(`\n🏙️  Rooftop Scheduler running at http://localhost:${PORT}`);
  console.log(`   Email: ${process.env.SENDGRID_API_KEY ? '✅ SendGrid configured' : '⚠️  No SENDGRID_API_KEY — emails will be logged only'}`);
  console.log(`   DB: bookings.db\n`);
});
