require('dotenv').config();
const sgMail = require('@sendgrid/mail');

const configured = !!process.env.SENDGRID_API_KEY;
if (configured) sgMail.setApiKey(process.env.SENDGRID_API_KEY);

const APP_URL     = process.env.APP_URL     || 'http://localhost:3000';
const FROM_EMAIL  = process.env.FROM_EMAIL  || 'noreply@example.com';
const FROM_NAME   = process.env.FROM_NAME   || 'Rooftop Scheduler';
const BUILDING    = process.env.BUILDING_NAME || 'Our Building';

function fmtDate(iso) {
  return new Date(iso).toLocaleString('en-US', {
    weekday: 'long', year: 'numeric', month: 'long',
    day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

async function send(msg) {
  if (!configured) {
    console.log('[Email skipped — no SENDGRID_API_KEY]');
    console.log('  To:', msg.to, '|', msg.subject);
    return;
  }
  try {
    await sgMail.send({ ...msg, from: { email: FROM_EMAIL, name: FROM_NAME } });
  } catch (err) {
    console.error('[SendGrid error]', err.response?.body || err.message);
  }
}

async function sendConfirmation(booking) {
  const manageUrl = `${APP_URL}/?token=${booking.edit_token}`;
  const subject   = `✅ Rooftop Booking Confirmed — ${booking.title}`;

  const text = `
Hi ${booking.name},

Your rooftop booking at ${BUILDING} is confirmed!

Event: ${booking.title}
When:  ${fmtDate(booking.start_time)} → ${fmtDate(booking.end_time)}
Apt:   Building ${booking.building}, Apt ${booking.apartment}

${booking.description ? `Notes: ${booking.description}\n` : ''}
To edit or cancel your booking, visit:
${manageUrl}

Keep this link safe — it's your key to manage your reservation.

See you on the roof! 🏙️
`.trim();

  const html = `
<!DOCTYPE html>
<html>
<body style="font-family:sans-serif;max-width:520px;margin:auto;padding:24px;color:#1a1a1a">
  <div style="background:#10b981;padding:20px 24px;border-radius:12px 12px 0 0">
    <h1 style="color:#fff;margin:0;font-size:22px">✅ Rooftop Booking Confirmed</h1>
  </div>
  <div style="border:1px solid #e5e7eb;border-top:none;padding:24px;border-radius:0 0 12px 12px">
    <p style="margin-top:0">Hi <strong>${booking.name}</strong>,</p>
    <p>Your rooftop reservation at <strong>${BUILDING}</strong> is confirmed!</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <tr><td style="padding:8px 0;color:#6b7280;width:90px">Event</td><td><strong>${booking.title}</strong></td></tr>
      <tr><td style="padding:8px 0;color:#6b7280">Starts</td><td>${fmtDate(booking.start_time)}</td></tr>
      <tr><td style="padding:8px 0;color:#6b7280">Ends</td><td>${fmtDate(booking.end_time)}</td></tr>
      <tr><td style="padding:8px 0;color:#6b7280">Location</td><td>Bldg ${booking.building}, Apt ${booking.apartment}</td></tr>
      ${booking.description ? `<tr><td style="padding:8px 0;color:#6b7280;vertical-align:top">Notes</td><td>${booking.description}</td></tr>` : ''}
    </table>
    <a href="${manageUrl}" style="display:inline-block;background:#10b981;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;margin-top:8px">Manage My Booking</a>
    <p style="margin-top:20px;font-size:13px;color:#9ca3af">Keep this email — the link above is your key to edit or cancel your reservation.</p>
  </div>
</body>
</html>`;

  await send({ to: booking.email, subject, text, html });
}

async function sendReminder(booking) {
  const manageUrl = `${APP_URL}/?token=${booking.edit_token}`;
  const subject   = `⏰ Reminder: Your rooftop event is tomorrow — ${booking.title}`;

  const text = `
Hi ${booking.name},

Just a reminder that your rooftop event is coming up soon!

Event: ${booking.title}
When:  ${fmtDate(booking.start_time)} → ${fmtDate(booking.end_time)}

${booking.description ? `Notes: ${booking.description}\n` : ''}
Need to make changes? Visit:
${manageUrl}

Enjoy your event! 🌆
`.trim();

  const html = `
<!DOCTYPE html>
<html>
<body style="font-family:sans-serif;max-width:520px;margin:auto;padding:24px;color:#1a1a1a">
  <div style="background:#f59e0b;padding:20px 24px;border-radius:12px 12px 0 0">
    <h1 style="color:#fff;margin:0;font-size:22px">⏰ Event Reminder</h1>
  </div>
  <div style="border:1px solid #e5e7eb;border-top:none;padding:24px;border-radius:0 0 12px 12px">
    <p style="margin-top:0">Hi <strong>${booking.name}</strong>,</p>
    <p>Your rooftop event is <strong>coming up soon</strong>!</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <tr><td style="padding:8px 0;color:#6b7280;width:90px">Event</td><td><strong>${booking.title}</strong></td></tr>
      <tr><td style="padding:8px 0;color:#6b7280">Starts</td><td>${fmtDate(booking.start_time)}</td></tr>
      <tr><td style="padding:8px 0;color:#6b7280">Ends</td><td>${fmtDate(booking.end_time)}</td></tr>
    </table>
    <a href="${manageUrl}" style="display:inline-block;background:#f59e0b;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600">Manage My Booking</a>
  </div>
</body>
</html>`;

  await send({ to: booking.email, subject, text, html });
}

module.exports = { sendConfirmation, sendReminder };
