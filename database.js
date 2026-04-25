const Database = require('better-sqlite3');
const path = require('path');

const db = new Database(path.join(__dirname, 'bookings.db'));

// Enable WAL mode for better concurrent read performance
db.pragma('journal_mode = WAL');

db.exec(`
  CREATE TABLE IF NOT EXISTS bookings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    building    TEXT NOT NULL,
    apartment   TEXT NOT NULL,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    start_time  TEXT NOT NULL,
    end_time    TEXT NOT NULL,
    edit_token  TEXT NOT NULL UNIQUE,
    reminder_sent INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
  )
`);

// Prepared statements
const stmts = {
  getAll: db.prepare(`
    SELECT id, building, apartment, name, email, title, description,
           start_time, end_time, created_at
    FROM bookings
    WHERE end_time >= ?
    ORDER BY start_time ASC
  `),

  getRange: db.prepare(`
    SELECT id, building, apartment, name, email, title, description,
           start_time, end_time, created_at
    FROM bookings
    WHERE start_time < ? AND end_time > ?
    ORDER BY start_time ASC
  `),

  getById: db.prepare(`
    SELECT * FROM bookings WHERE id = ?
  `),

  getByToken: db.prepare(`
    SELECT * FROM bookings WHERE edit_token = ?
  `),

  // Conflict check: any booking that overlaps [start, end)?
  // Overlap when: existing.start < new.end AND existing.end > new.start
  checkConflict: db.prepare(`
    SELECT id, title, name, apartment, start_time, end_time
    FROM bookings
    WHERE start_time < ? AND end_time > ? AND id != ?
    LIMIT 1
  `),

  insert: db.prepare(`
    INSERT INTO bookings (building, apartment, name, email, title, description,
                          start_time, end_time, edit_token)
    VALUES (@building, @apartment, @name, @email, @title, @description,
            @start_time, @end_time, @edit_token)
  `),

  update: db.prepare(`
    UPDATE bookings
    SET building=@building, apartment=@apartment, name=@name, email=@email,
        title=@title, description=@description,
        start_time=@start_time, end_time=@end_time
    WHERE id=@id AND edit_token=@edit_token
  `),

  delete: db.prepare(`
    DELETE FROM bookings WHERE id=? AND edit_token=?
  `),

  // For reminders: events starting in the next 25h that haven't been reminded yet
  getPendingReminders: db.prepare(`
    SELECT * FROM bookings
    WHERE reminder_sent = 0
      AND start_time > datetime('now')
      AND start_time <= datetime('now', '+25 hours')
  `),

  markReminderSent: db.prepare(`
    UPDATE bookings SET reminder_sent=1 WHERE id=?
  `),
};

module.exports = { db, stmts };
