/* ──────────────────────────────────────────────────────────────
   Rooftop Scheduler — frontend logic
   ────────────────────────────────────────────────────────────── */

// Token storage: keyed by booking ID
const TOKEN_KEY = 'rts_tokens'; // localStorage key

function saveToken(id, token) {
  const map = getTokenMap();
  map[id] = token;
  localStorage.setItem(TOKEN_KEY, JSON.stringify(map));
}

function getTokenMap() {
  try { return JSON.parse(localStorage.getItem(TOKEN_KEY) || '{}'); }
  catch { return {}; }
}

function getToken(id) {
  return getTokenMap()[id] || null;
}

// ── Date helpers ─────────────────────────────────────────────

function toLocalInput(iso) {
  // Convert ISO to datetime-local value (YYYY-MM-DDTHH:MM)
  const d = new Date(iso);
  const pad = n => String(n).padStart(2,'0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fmtDateTime(iso) {
  return new Date(iso).toLocaleString('en-US', {
    weekday:'short', month:'short', day:'numeric',
    year:'numeric', hour:'numeric', minute:'2-digit'
  });
}

function fmtDateOnly(iso) {
  return new Date(iso).toLocaleDateString('en-US', {
    weekday:'long', month:'long', day:'numeric', year:'numeric'
  });
}

// ── Calendar setup ───────────────────────────────────────────

let calendar;
let currentEditId   = null;
let currentEditToken = null;

document.addEventListener('DOMContentLoaded', () => {
  // Pre-fill min date for new bookings
  setMinDatetimes();

  calendar = new FullCalendar.Calendar(document.getElementById('calendar'), {
    initialView: window.innerWidth < 600 ? 'listMonth' : 'dayGridMonth',
    headerToolbar: {
      left:   'prev,next today',
      center: 'title',
      right:  'dayGridMonth,timeGridWeek,timeGridDay,listMonth'
    },
    buttonText: { listMonth: 'List' },
    height: 'auto',
    nowIndicator: true,
    events: fetchEvents,
    eventClick(info) { openDetailModal(info.event.id); },
    dateClick(info) {
      // Pre-fill the date when clicking a day
      const dt = info.dateStr + 'T12:00';
      document.getElementById('f-start').value = dt;
      const endDt = info.dateStr + 'T14:00';
      document.getElementById('f-end').value = endDt;
      openBookingModal();
    },
    eventDidMount(info) {
      // Tooltip
      info.el.title = `${info.event.title}\n${fmtDateTime(info.event.start)} → ${fmtDateTime(info.event.end)}`;
    }
  });

  calendar.render();

  // Handle deep-link from email: ?token=xxx
  const params = new URLSearchParams(window.location.search);
  const tok = params.get('token');
  if (tok) handleTokenDeepLink(tok);
});

async function fetchEvents(info, success, failure) {
  try {
    const res = await fetch(
      `/api/bookings?start=${info.startStr}&end=${info.endStr}`
    );
    const data = await res.json();
    success(data.map(b => ({
      id:    String(b.id),
      title: b.title,
      start: b.start_time,
      end:   b.end_time,
      backgroundColor: '#10b981',
      borderColor: '#059669',
      extendedProps: b,
    })));
  } catch (err) {
    failure(err);
  }
}

// ── Deep link from email ─────────────────────────────────────

async function handleTokenDeepLink(token) {
  try {
    const res  = await fetch(`/api/manage/${token}`);
    if (!res.ok) return;
    const data = await res.json();
    saveToken(data.id, token);
    // Clean URL
    history.replaceState({}, '', '/');
    // Open detail modal
    openDetailModal(data.id, data);
  } catch {}
}

// ── Min datetime constraints ─────────────────────────────────

function setMinDatetimes() {
  const now = toLocalInput(new Date().toISOString());
  const max = (() => {
    const d = new Date(); d.setDate(d.getDate()+30);
    return toLocalInput(d.toISOString());
  })();
  ['f-start','f-end','e-start','e-end'].forEach(id => {
    const el = document.getElementById(id);
    if (el) { el.min = now; el.max = max; }
  });
}

// ── Booking Modal ────────────────────────────────────────────

function openBookingModal() {
  setMinDatetimes();
  document.getElementById('bookingModal').classList.add('open');
  document.getElementById('bookingAlert').innerHTML = '';
  document.getElementById('bookingSuccess').style.display = 'none';
  document.getElementById('bookingForm').style.display = 'block';
  document.getElementById('bookingFooter').style.display = 'flex';
  document.getElementById('bookingModalTitle').textContent = 'Reserve the Rooftop';
}

function closeBookingModal() {
  document.getElementById('bookingModal').classList.remove('open');
  document.getElementById('bookingForm').reset();
  document.getElementById('bookingAlert').innerHTML = '';
  document.getElementById('bookingSuccess').style.display = 'none';
  document.getElementById('bookingForm').style.display = 'block';
  document.getElementById('bookingFooter').style.display = 'flex';
}

async function submitBooking(e) {
  e.preventDefault();
  const btn = document.getElementById('submitBtn');
  const alertBox = document.getElementById('bookingAlert');
  alertBox.innerHTML = '';

  const payload = {
    building:    document.getElementById('f-building').value.trim(),
    apartment:   document.getElementById('f-apartment').value.trim(),
    name:        document.getElementById('f-name').value.trim(),
    email:       document.getElementById('f-email').value.trim(),
    title:       document.getElementById('f-title').value.trim(),
    description: document.getElementById('f-desc').value.trim(),
    start_time:  new Date(document.getElementById('f-start').value).toISOString(),
    end_time:    new Date(document.getElementById('f-end').value).toISOString(),
  };

  if (!payload.building || !payload.apartment || !payload.name ||
      !payload.email || !payload.title || !payload.start_time || !payload.end_time) {
    alertBox.innerHTML = `<div class="alert alert-err">Please fill in all required fields.</div>`;
    return;
  }
  if (new Date(payload.end_time) <= new Date(payload.start_time)) {
    alertBox.innerHTML = `<div class="alert alert-err">End time must be after start time.</div>`;
    return;
  }

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Confirming…';

  try {
    const res  = await fetch('/api/bookings', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await res.json();

    if (!res.ok) {
      let msg = data.error || 'Something went wrong.';
      if (res.status === 409 && data.conflict) {
        const c = data.conflict;
        msg = `That time slot is already booked by <strong>${c.organizer}</strong> (Apt ${c.apartment}) for "<em>${c.title}</em>" — ${fmtDateTime(c.start_time)} to ${fmtDateTime(c.end_time)}.`;
      }
      alertBox.innerHTML = `<div class="alert alert-err">${msg}</div>`;
      return;
    }

    // Success
    saveToken(data.id, data.edit_token);
    const link = `${location.origin}/?token=${data.edit_token}`;
    document.getElementById('successLink').href = link;
    document.getElementById('successLink').textContent = link;
    document.getElementById('bookingForm').style.display = 'none';
    document.getElementById('bookingFooter').style.display = 'none';
    document.getElementById('bookingSuccess').style.display = 'block';
    calendar.refetchEvents();

  } catch (err) {
    alertBox.innerHTML = `<div class="alert alert-err">Network error — please try again.</div>`;
  } finally {
    btn.disabled = false;
    btn.innerHTML = 'Confirm Booking';
  }
}

// ── Detail Modal ─────────────────────────────────────────────

async function openDetailModal(id, prefetched) {
  let booking = prefetched;
  if (!booking) {
    try {
      const res = await fetch(`/api/bookings/${id}`);
      if (!res.ok) return;
      booking = await res.json();
    } catch { return; }
  }

  const token = getToken(booking.id || id);

  document.getElementById('detailTitle').textContent = booking.title;
  document.getElementById('detailBody').innerHTML = `
    <div class="detail-row"><span class="detail-label">Date</span><span class="detail-val">${fmtDateOnly(booking.start_time)}</span></div>
    <div class="detail-row"><span class="detail-label">Time</span><span class="detail-val">${formatTimeRange(booking.start_time, booking.end_time)}</span></div>
    <div class="detail-row"><span class="detail-label">Organizer</span><span class="detail-val">${esc(booking.name)}</span></div>
    <div class="detail-row"><span class="detail-label">Apartment</span><span class="detail-val">Bldg ${esc(booking.building)}, Apt ${esc(booking.apartment)}</span></div>
    ${booking.description ? `<div class="detail-row"><span class="detail-label">About</span><span class="detail-val">${esc(booking.description)}</span></div>` : ''}
    <div class="detail-row"><span class="detail-label">Booked</span><span class="detail-val" style="font-size:13px;color:var(--gray-500)">${fmtDateTime(booking.created_at)}</span></div>
    ${!token ? `
      <hr style="margin:16px 0;border:none;border-top:1px solid var(--gray-200)"/>
      <div class="token-box">
        <p>Have the management link for this booking? Enter your token below to edit or cancel.</p>
        <div style="display:flex;gap:8px">
          <input type="text" id="tokenInput" placeholder="Paste your token here…"
            style="flex:1;padding:9px 12px;border:1.5px solid var(--gray-200);border-radius:8px;font-size:14px"/>
          <button class="btn btn-ghost btn-sm" onclick="unlockWithToken(${booking.id})">Unlock</button>
        </div>
        <div id="tokenAlert" style="margin-top:8px"></div>
      </div>` : ''}
  `;

  const footer = document.getElementById('detailFooter');
  footer.innerHTML = token
    ? `<button class="btn btn-ghost" onclick="closeDetailModal()">Close</button>
       <button class="btn btn-amber btn-sm" onclick="openEditModal(${booking.id})">Edit</button>
       <button class="btn btn-red btn-sm" onclick="cancelBooking(${booking.id})">Cancel Booking</button>`
    : `<button class="btn btn-ghost" onclick="closeDetailModal()">Close</button>`;

  // Store reference for edit
  currentEditId    = booking.id;
  currentEditToken = token;
  window._detailBooking = booking;

  document.getElementById('detailModal').classList.add('open');
}

function closeDetailModal() {
  document.getElementById('detailModal').classList.remove('open');
  window._detailBooking = null;
}

async function unlockWithToken(id) {
  const input = document.getElementById('tokenInput');
  const tok   = input.value.trim();
  if (!tok) return;
  // Verify by trying to fetch manage endpoint
  try {
    const res  = await fetch(`/api/manage/${tok}`);
    const data = await res.json();
    if (!res.ok || data.id !== id) {
      document.getElementById('tokenAlert').innerHTML =
        `<div class="alert alert-err">Token not valid for this booking.</div>`;
      return;
    }
    saveToken(id, tok);
    closeDetailModal();
    openDetailModal(id);
  } catch {
    document.getElementById('tokenAlert').innerHTML =
      `<div class="alert alert-err">Could not verify token.</div>`;
  }
}

function formatTimeRange(start, end) {
  const fmt = iso => new Date(iso).toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit'});
  return `${fmt(start)} – ${fmt(end)}`;
}

function esc(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Cancel ───────────────────────────────────────────────────

async function cancelBooking(id) {
  const token = getToken(id);
  if (!token) return;
  if (!confirm('Are you sure you want to cancel this booking? This cannot be undone.')) return;

  try {
    const res = await fetch(`/api/bookings/${id}`, {
      method: 'DELETE',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ edit_token: token })
    });
    if (res.ok) {
      // Remove from local storage
      const map = getTokenMap();
      delete map[id];
      localStorage.setItem(TOKEN_KEY, JSON.stringify(map));
      closeDetailModal();
      calendar.refetchEvents();
    } else {
      const d = await res.json();
      alert('Error: ' + (d.error || 'Could not cancel booking.'));
    }
  } catch {
    alert('Network error — please try again.');
  }
}

// ── Edit Modal ───────────────────────────────────────────────

function openEditModal(id) {
  const b = window._detailBooking;
  if (!b) return;
  closeDetailModal();

  document.getElementById('e-building').value  = b.building;
  document.getElementById('e-apartment').value = b.apartment;
  document.getElementById('e-name').value      = b.name;
  document.getElementById('e-email').value     = b.email;
  document.getElementById('e-title').value     = b.title;
  document.getElementById('e-start').value     = toLocalInput(b.start_time);
  document.getElementById('e-end').value       = toLocalInput(b.end_time);
  document.getElementById('e-desc').value      = b.description || '';
  document.getElementById('editAlert').innerHTML = '';

  currentEditId    = b.id;
  currentEditToken = getToken(b.id);

  setMinDatetimes();
  document.getElementById('editModal').classList.add('open');
}

function closeEditModal() {
  document.getElementById('editModal').classList.remove('open');
}

async function submitEdit(e) {
  e.preventDefault();
  const btn = document.getElementById('editSubmitBtn');
  const alertBox = document.getElementById('editAlert');
  alertBox.innerHTML = '';

  const payload = {
    edit_token:  currentEditToken,
    building:    document.getElementById('e-building').value.trim(),
    apartment:   document.getElementById('e-apartment').value.trim(),
    name:        document.getElementById('e-name').value.trim(),
    email:       document.getElementById('e-email').value.trim(),
    title:       document.getElementById('e-title').value.trim(),
    description: document.getElementById('e-desc').value.trim(),
    start_time:  new Date(document.getElementById('e-start').value).toISOString(),
    end_time:    new Date(document.getElementById('e-end').value).toISOString(),
  };

  if (new Date(payload.end_time) <= new Date(payload.start_time)) {
    alertBox.innerHTML = `<div class="alert alert-err">End time must be after start time.</div>`;
    return;
  }

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Saving…';

  try {
    const res  = await fetch(`/api/bookings/${currentEditId}`, {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await res.json();

    if (!res.ok) {
      let msg = data.error || 'Something went wrong.';
      if (res.status === 409 && data.conflict) {
        const c = data.conflict;
        msg = `Conflicts with <strong>${c.organizer}</strong>'s booking "<em>${c.title}</em>" — ${fmtDateTime(c.start_time)} to ${fmtDateTime(c.end_time)}.`;
      }
      alertBox.innerHTML = `<div class="alert alert-err">${msg}</div>`;
      return;
    }

    closeEditModal();
    calendar.refetchEvents();

  } catch {
    alertBox.innerHTML = `<div class="alert alert-err">Network error — please try again.</div>`;
  } finally {
    btn.disabled = false;
    btn.innerHTML = 'Save Changes';
  }
}
