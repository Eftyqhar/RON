/* ══════════════════════════════════════════════════════════════════════
   R.O.N. HUD — client runtime

   Everything visible here is driven by real events from the Python side over
   SSE (`/events`): pipeline state, microphone RMS, system telemetry, tool
   activity. The only synthesised signal is the idle waveform, which breathes
   gently when no audio is flowing so the visualiser never looks dead.
   ══════════════════════════════════════════════════════════════════════ */

'use strict';

const root = document.documentElement;
const $ = (id) => document.getElementById(id);

/* ── state labels ─────────────────────────────────────────────────────── */

const STATE_LABEL = {
  idle: 'STANDBY',
  listening: 'LISTENING',
  thinking: 'PROCESSING',
  speaking: 'SPEAKING',
  executing: 'EXECUTING',
  error: 'SYSTEM ERROR',
  offline: 'OFFLINE',
};
const IDLE_DETAIL = 'ALL SYSTEMS NOMINAL';

const ui = {
  state: 'idle',
  micMuted: false,
  micOk: null,
  /* Audio level: `level` is what we draw, `target` is what the server last
     reported. Interpolating between them keeps the waveform smooth even though
     RMS frames arrive at ~20 Hz and we render at 60. */
  level: 0,
  target: 0,
  lastLevelAt: 0,
};

/* ── clock ────────────────────────────────────────────────────────────── */

const DAYS = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
                'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
const pad2 = (n) => String(n).padStart(2, '0');

function tickClock() {
  const d = new Date();
  $('clock').textContent = `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
  $('date').textContent = `${DAYS[d.getDay()]} ${pad2(d.getDate())} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}
tickClock();
setInterval(tickClock, 1000);

/* ── state transitions ────────────────────────────────────────────────── */

function setState(state, detail) {
  const next = STATE_LABEL[state] ? state : 'idle';
  const changed = next !== ui.state;
  ui.state = next;
  root.dataset.state = next;

  const label = STATE_LABEL[next];
  $('core-state').textContent = label;
  $('viz-state').textContent = next === 'listening' ? 'LISTENING…' : label;
  $('core-detail').textContent = (detail || '').toUpperCase() ||
    (next === 'idle' ? IDLE_DETAIL : label);

  if (changed) {
    const centre = document.querySelector('.core-center');
    centre.classList.remove('flash');
    void centre.offsetWidth;   // restart the animation
    centre.classList.add('flash');
    readAccent();              // the error state swaps the whole palette
  }
}

/* Canvas cannot read CSS custom properties, so the accent is sampled from the
   computed style whenever the palette could have changed. */
let accent = [56, 225, 240];
function readAccent() {
  const raw = getComputedStyle(root).getPropertyValue('--accent-rgb').trim();
  const parts = raw.split(',').map((n) => parseInt(n, 10));
  if (parts.length === 3 && parts.every((n) => Number.isFinite(n))) accent = parts;
}
readAccent();
const rgba = (a) => `rgba(${accent[0]},${accent[1]},${accent[2]},${a})`;

/* ── telemetry ────────────────────────────────────────────────────────── */

function setMeter(id, value) {
  const el = $(id);
  if (!el) return;
  const num = el.querySelector('b');
  const fill = el.querySelector('.fill');
  if (value === null || value === undefined || Number.isNaN(value)) {
    el.classList.add('na');
    num.innerHTML = 'N/A';
    fill.style.width = '0%';
    return;
  }
  const v = Math.max(0, Math.min(100, Number(value)));
  el.classList.remove('na');
  el.classList.toggle('hot', v >= 88);
  num.innerHTML = `${Math.round(v)}<i>%</i>`;
  fill.style.width = `${v}%`;
}

function applyMetrics(m) {
  if ('cpu' in m) setMeter('meter-cpu', m.cpu);
  if ('ram' in m) setMeter('meter-ram', m.ram);
  if ('gpu' in m) setMeter('meter-gpu', m.gpu);
  if ('disk' in m) setMeter('meter-disk', m.disk);
  if ('net_down' in m) $('net-down').textContent = Number(m.net_down).toFixed(1);
  if ('net_up' in m) $('net-up').textContent = Number(m.net_up).toFixed(1);
  if ('uptime' in m) {
    const s = Math.max(0, m.uptime | 0);
    $('uptime').textContent =
      `${pad2(Math.floor(s / 3600))}:${pad2(Math.floor(s / 60) % 60)}:${pad2(s % 60)}`;
  }
}

/* ── weather (panel 05) ─────────────────────────────────────────────────── */

// The eight icon groups weather.py can report. `clear` picks up a night variant.
const WX_ICONS = {
  clear: 'wx-clear', partly: 'wx-partly', cloud: 'wx-cloud', fog: 'wx-fog',
  drizzle: 'wx-drizzle', rain: 'wx-rain', snow: 'wx-snow', storm: 'wx-storm',
};

function wxSymbol(group, isDay) {
  if (group === 'clear' && isDay === false) return 'wx-clear-night';
  return WX_ICONS[group] || 'wx-cloud';
}

// `bus.weather()` replaces rather than merges, so every payload is complete and
// this reads as a full repaint: nothing is left over from the previous one.
function applyWeather(w) {
  const panel = $('panel-weather');
  if (!panel) return;

  // Three states, not two. An empty payload means the poller has not reported
  // yet -- the first fetch takes a geocode plus a forecast -- and calling that
  // OFFLINE for the first few seconds of every boot would be a lie.
  const pending = !('ok' in w);
  const ok = !!w.ok;
  panel.dataset.ok = pending ? 'unknown' : String(ok);
  panel.dataset.stale = String(ok && !!w.stale);

  if (!ok) {
    $('wx-temp').textContent = '--°';
    $('wx-cond').textContent = pending ? 'STANDBY' : 'OFFLINE';
    $('wx-place').textContent = pending ? 'AWAITING FEED'
      : (w.error ? String(w.error).toUpperCase() : 'NO WEATHER FEED');
    $('wx-humidity').textContent = '—';
    $('wx-wind').textContent = '—';
    $('wx-rain').textContent = '—';
    $('wx-strip').innerHTML = '';
    $('wx-icon-use').setAttribute('href', '#wx-cloud');
    return;
  }

  // Bare degrees, as designed: the scale is a user-set global (RON_UNITS), so
  // stamping C or F on the headline number every refresh only adds noise.
  $('wx-temp').textContent = w.temp === null || w.temp === undefined
    ? '--°' : `${w.temp}°`;
  $('wx-cond').textContent = String(w.condition || '').toUpperCase() || '—';
  $('wx-icon-use').setAttribute('href', `#${wxSymbol(w.group, w.is_day)}`);

  const place = String(w.place || '').toUpperCase();
  $('wx-place').textContent = (w.feels === null || w.feels === undefined)
    ? place : `${place} · FEELS ${w.feels}°`;

  $('wx-humidity').textContent = w.humidity === null || w.humidity === undefined
    ? '—' : `${w.humidity}%`;
  $('wx-wind').textContent = w.wind === null || w.wind === undefined
    ? '—' : `${w.wind} ${w.wind_unit || 'KM/H'}`;
  $('wx-rain').textContent = `${w.rain_pct | 0}%`;

  // Rebuilt wholesale: five cells is small enough that diffing would cost more
  // than it saves, and this cannot leave a stale hour behind.
  const strip = $('wx-strip');
  strip.innerHTML = '';
  for (const h of (Array.isArray(w.hourly) ? w.hourly : []).slice(0, 5)) {
    const cell = document.createElement('div');
    cell.className = 'wx-cell';

    const hour = document.createElement('span');
    hour.textContent = h.hour;

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 32 32');
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    // The strip covers the next several hours, which may cross into the night;
    // without a per-cell day flag, `is_day` from the current reading is the
    // closest honest answer.
    use.setAttribute('href', `#${wxSymbol(h.group, w.is_day)}`);
    svg.appendChild(use);

    const temp = document.createElement('b');
    temp.className = 'mono';
    temp.textContent = `${h.temp}°`;

    cell.append(hour, svg, temp);
    strip.appendChild(cell);
  }
}

/* ── file search (overlay · panel 06) ───────────────────────────────────
   Driven by bus.search(): a `scanning` frame the instant a search starts,
   streaming progress, then a finished `done|empty|error` payload. The overlay
   is a fixed, hidden sibling of the grid, so opening it disturbs nothing. */

const search = {
  open: false,
  scanning: false,
  raf: 0,
  t0: 0,
};

function openSearch() {
  if (search.open) return;
  search.open = true;
  $('search-overlay').classList.add('open');
  $('search-overlay').setAttribute('aria-hidden', 'false');
}

function closeSearch() {
  search.open = false;
  search.scanning = false;
  const el = $('search-overlay');
  el.classList.remove('open');
  el.setAttribute('aria-hidden', 'true');
}

function fmtElapsed(ms) {
  const s = Math.max(0, Number(ms) || 0) / 1000;
  return `${s.toFixed(1)}s`;
}

function applySearch(s, fromSnapshot) {
  const el = $('search-overlay');
  if (!el) return;
  const status = s && s.status ? s.status : 'idle';

  // A late-connecting browser must not have a finished search pop open in its
  // face: only auto-open on a live event, or when a scan is genuinely still
  // running. A stale `done` frame just primes the panel, unseen.
  if (!fromSnapshot || status === 'scanning') openSearch();
  if (status === 'idle' && fromSnapshot) return;

  el.dataset.status = status;
  $('so-query').textContent = s.query ? String(s.query) : '—';
  $('so-mode').textContent = String(s.mode || 'keyword').toUpperCase();
  $('so-scanned').textContent = (Number(s.scanned) || 0).toLocaleString();
  $('so-matches').textContent = Number(s.count) || 0;

  if (status === 'scanning') {
    search.scanning = true;
    if (!search.t0) search.t0 = performance.now();
    $('so-elapsed').textContent = fmtElapsed(performance.now() - search.t0);
    $('so-sub').textContent = 'SCANNING DRIVES…';
    $('so-current').textContent = s.current ? shortenPath(String(s.current), 68) : ' ';
    startScanFx();
    return;
  }

  // A terminal frame: stop the animation and render results.
  search.scanning = false;
  search.t0 = 0;
  $('so-elapsed').textContent = fmtElapsed(s.elapsed_ms);
  $('so-current').textContent = ' ';

  const results = $('so-results');
  results.innerHTML = '';

  if (status === 'error') {
    $('so-sub').textContent = 'SEARCH FAILED';
    $('so-foot').textContent = (s.error ? String(s.error).toUpperCase() + ' · ' : '')
      + 'THE DISK WALK HIT A PROBLEM';
    results.innerHTML = '<p class="empty">NO RESULTS</p>';
    return;
  }

  const list = Array.isArray(s.results) ? s.results : [];
  $('so-sub').textContent = list.length
    ? `${s.count} MATCH${s.count === 1 ? '' : 'ES'}` + (s.truncated ? ' · CAPPED' : '')
    : 'NO MATCHES';
  $('so-foot').textContent = list.length
    ? 'CLICK A RESULT TO REVEAL IT · ESC TO CLOSE'
    : 'NOTHING MATCHED THAT QUERY · ESC TO CLOSE';

  if (!list.length) {
    results.innerHTML = '<p class="empty">NO MATCHES FOUND</p>';
    return;
  }
  for (const m of list) results.appendChild(searchRow(m));
}

function shortenPath(p, max) {
  return p.length <= max ? p : '…' + p.slice(p.length - max + 1);
}

function searchRow(m) {
  const row = document.createElement('div');
  row.className = 'so-item';
  row.dataset.kind = m.kind === 'dir' ? 'dir' : 'file';
  row.dataset.path = m.path || '';

  const tag = document.createElement('span');
  tag.className = 'so-tag';
  tag.textContent = m.kind === 'dir' ? 'DIR' : 'FILE';

  const mid = document.createElement('div');
  const name = document.createElement('span');
  name.className = 'so-name';
  name.textContent = m.name || m.path || '—';
  const path = document.createElement('span');
  path.className = 'so-path';
  path.textContent = m.dir || m.path || '';
  mid.append(name, path);

  const size = document.createElement('span');
  size.className = 'so-size';
  size.textContent = m.size || '';

  row.append(tag, mid, size);
  row.addEventListener('click', () => revealPath(m.path));
  return row;
}

// Reveal a result in the OS file manager. Best-effort: the server validates the
// path exists before opening anything, and a failure is silent on the HUD side.
async function revealPath(path) {
  if (!path) return;
  try { await post('/api/open', { path }); } catch (_) { /* server declined */ }
}

/* A light particle-scan effect on #so-canvas, alive only while scanning. */
const soCanvas = $('so-canvas');
const soCtx = soCanvas ? soCanvas.getContext('2d') : null;
let soParticles = [];

function sizeScanCanvas() {
  if (!soCanvas) return;
  const r = dpr();
  soCanvas.width = Math.floor(window.innerWidth * r);
  soCanvas.height = Math.floor(window.innerHeight * r);
  soCtx.setTransform(r, 0, 0, r, 0, 0);
}

function startScanFx() {
  if (!soCtx || search.raf) return;
  sizeScanCanvas();
  if (!soParticles.length) {
    for (let i = 0; i < 60; i++) {
      soParticles.push({
        x: Math.random() * window.innerWidth,
        y: Math.random() * window.innerHeight,
        v: 0.3 + Math.random() * 1.2,
        r: 0.6 + Math.random() * 1.8,
      });
    }
  }
  const step = () => {
    if (!search.scanning) { stopScanFx(); return; }
    const W2 = window.innerWidth, H2 = window.innerHeight;
    soCtx.clearRect(0, 0, W2, H2);
    soCtx.fillStyle = 'rgba(56, 225, 240, .5)';
    for (const p of soParticles) {
      p.y += p.v;
      if (p.y > H2) { p.y = -4; p.x = Math.random() * W2; }
      soCtx.globalAlpha = 0.2 + (p.r / 2.4) * 0.6;
      soCtx.beginPath();
      soCtx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      soCtx.fill();
    }
    soCtx.globalAlpha = 1;
    search.raf = requestAnimationFrame(step);
  };
  search.raf = requestAnimationFrame(step);
}

function stopScanFx() {
  if (search.raf) { cancelAnimationFrame(search.raf); search.raf = 0; }
  if (soCtx) soCtx.clearRect(0, 0, window.innerWidth, window.innerHeight);
}


/* ═══ NETWORK SPEED OVERLAY ═══════════════════════════════════════════ */

const speed = {
  open: false,
  scanning: false,
  raf: 0,
  t0: 0,
};

function openSpeed() {
  if (speed.open) return;
  speed.open = true;
  $('speed-overlay').classList.add('open');
  $('speed-overlay').setAttribute('aria-hidden', 'false');
}

function closeSpeed() {
  speed.open = false;
  speed.scanning = false;
  const el = $('speed-overlay');
  el.classList.remove('open');
  el.setAttribute('aria-hidden', 'true');
}

function applySpeed(s, fromSnapshot) {
  const el = $('speed-overlay');
  if (!el) return;
  const status = s && s.status ? s.status : 'idle';

  // Only auto-open on a live event, or when a test is genuinely still
  // running. A stale `done` frame just primes the panel, unseen.
  if (!fromSnapshot || status === 'scanning') openSpeed();
  if (status === 'idle' && fromSnapshot) return;

  el.dataset.status = status;

  if (status === 'scanning') {
    speed.scanning = true;
    if (!speed.t0) speed.t0 = performance.now();
    $('ns-elapsed').textContent = fmtElapsed(performance.now() - speed.t0);

    const phase = String(s.phase || '—').toUpperCase();
    $('ns-phase').textContent = phase;
    $('ns-sub').textContent = 'MEASURING…';

    const done = Number(s.phase_done) || 0;
    const total = Number(s.phase_total) || 1;
    const pct = Math.min(100, Math.round(100 * done / total));
    $('ns-bar-fill').style.width = pct + '%';
    $('ns-phase-pct').textContent = pct + '%';

    // Numbers stream in as each phase lands; keep them visible.
    if (s.ping_ms != null) $('ns-ping').textContent = Number(s.ping_ms).toFixed(1);
    if (s.download_mbps != null) $('ns-down').textContent = Number(s.download_mbps).toFixed(2);
    if (s.upload_mbps != null) $('ns-up').textContent = Number(s.upload_mbps).toFixed(2);

    startSpeedFx();
    return;
  }

  // A terminal frame: stop the animation and render the final numbers.
  speed.scanning = false;
  speed.t0 = 0;
  $('ns-bar-fill').style.width = '100%';
  $('ns-phase-pct').textContent = '100%';
  $('ns-elapsed').textContent = fmtElapsed(s.elapsed_ms || (s.elapsed || 0) * 1000);

  if (s.ping_ms != null) $('ns-ping').textContent = Number(s.ping_ms).toFixed(1);
  if (s.download_mbps != null) $('ns-down').textContent = Number(s.download_mbps).toFixed(2);
  if (s.upload_mbps != null) $('ns-up').textContent = Number(s.upload_mbps).toFixed(2);

  if (status === 'error') {
    $('ns-sub').textContent = 'TEST FAILED';
    $('ns-foot').textContent = (s.error ? String(s.error).toUpperCase() + ' · ' : '')
      + 'THE SPEED TEST HIT A PROBLEM';
    $('ns-phase').textContent = 'ERROR';
    return;
  }

  $('ns-sub').textContent = 'COMPLETE';
  $('ns-foot').textContent = 'SPEED TEST FINISHED · ESC TO CLOSE';
  $('ns-phase').textContent = 'DONE';
}

const nsCanvas = $('ns-canvas');
const nsCtx = nsCanvas ? nsCanvas.getContext('2d') : null;
let nsParticles = [];

function sizeSpeedCanvas() {
  if (!nsCanvas) return;
  const r = dpr();
  nsCanvas.width = Math.floor(window.innerWidth * r);
  nsCanvas.height = Math.floor(window.innerHeight * r);
  nsCtx.setTransform(r, 0, 0, r, 0, 0);
}

function startSpeedFx() {
  if (!nsCtx || speed.raf) return;
  sizeSpeedCanvas();
  if (!nsParticles.length) {
    for (let i = 0; i < 50; i++) {
      nsParticles.push({
        x: Math.random() * window.innerWidth,
        y: Math.random() * window.innerHeight,
        v: 0.4 + Math.random() * 1.6,
        r: 0.6 + Math.random() * 1.6,
      });
    }
  }
  const step = () => {
    if (!speed.scanning) { stopSpeedFx(); return; }
    const W2 = window.innerWidth, H2 = window.innerHeight;
    nsCtx.clearRect(0, 0, W2, H2);
    nsCtx.fillStyle = 'rgba(56, 225, 240, .45)';
    for (const p of nsParticles) {
      p.y += p.v;
      if (p.y > H2) { p.y = -4; p.x = Math.random() * W2; }
      nsCtx.globalAlpha = 0.2 + (p.r / 2.2) * 0.6;
      nsCtx.beginPath();
      nsCtx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      nsCtx.fill();
    }
    nsCtx.globalAlpha = 1;
    speed.raf = requestAnimationFrame(step);
  };
  speed.raf = requestAnimationFrame(step);
}

function stopSpeedFx() {
  if (speed.raf) { cancelAnimationFrame(speed.raf); speed.raf = 0; }
  if (nsCtx) nsCtx.clearRect(0, 0, window.innerWidth, window.innerHeight);
}


/* ═══ TIMER OVERLAY ═══════════════════════════════════════════════════ */

const timer = {
  open: false,
  running: false,
  raf: 0,
  t0: 0,
  duration: 0,
};

function openTimer() {
  if (timer.open) return;
  timer.open = true;
  $('timer-overlay').classList.add('open');
  $('timer-overlay').setAttribute('aria-hidden', 'false');
}

function closeTimer() {
  timer.open = false;
  timer.running = false;
  stopTimerAlert();
  const el = $('timer-overlay');
  el.classList.remove('open');
  el.setAttribute('aria-hidden', 'true');
}

function applyTimer(s, fromSnapshot) {
  const el = $('timer-overlay');
  if (!el) return;
  const status = s && s.status ? s.status : 'idle';

  // Auto-open on a live event (set/running/done). A stale snapshot of an
  // idle timer stays hidden.
  if (!fromSnapshot || status === 'set' || status === 'running') openTimer();
  if (status === 'idle' && fromSnapshot) return;

  el.dataset.status = status;

  const remaining = fmtClock(s.remaining_sec || 0);
  const duration = s.duration_fmt || '—';
  const elapsed = fmtElapsed((s.elapsed_sec || 0) * 1000);
  const durSec = s.duration_sec || 0;
  const remSec = s.remaining_sec || 0;
  const pct = durSec > 0 ? Math.min(100, Math.round(100 * (1 - remSec / durSec))) : 0;

  $('tm-remaining').textContent = remaining;
  $('tm-label').textContent = status === 'done' ? 'TIME UP' :
                               status === 'cancelled' ? 'CANCELLED' :
                               status === 'error' ? 'ERROR' : 'REMAINING';
  $('tm-duration').textContent = duration;
  $('tm-elapsed').textContent = elapsed;
  $('tm-bar-fill').style.width = pct + '%';
  $('tm-pct').textContent = pct + '%';

  if (status === 'set') {
    $('tm-sub').textContent = 'ARMING…';
    timer.running = true;
    if (!timer.t0) timer.t0 = performance.now();
    startTimerFx();
  } else if (status === 'running') {
    $('tm-sub').textContent = 'COUNTING DOWN…';
    timer.running = true;
    if (!timer.t0) timer.t0 = performance.now();
    startTimerFx();
  } else {
    // Terminal: done / cancelled / error.
    timer.running = false;
    if (status === 'done') {
      $('tm-sub').textContent = 'COMPLETE';
      $('tm-foot').textContent = 'TIMER ELAPSED · ESC TO CLOSE';
      timerAlert();
    } else if (status === 'cancelled') {
      $('tm-sub').textContent = 'CANCELLED';
      $('tm-foot').textContent = 'TIMER CANCELLED · ESC TO CLOSE';
      stopTimerAlert();
    } else {
      $('tm-sub').textContent = 'ERROR';
      $('tm-foot').textContent = (s.error ? String(s.error).toUpperCase() + ' · ' : '') +
                                  'ESC TO CLOSE';
      stopTimerAlert();
    }
    stopTimerFx();
  }
}

// Audible + visual alarm for when the timer elapses. Uses the Web Audio API so
// no external file is needed; falls back to a CSS flash if audio is blocked.
let _alarmInterval = null;
let _alarmFlash = null;

function timerAlert() {
  stopTimerAlert();
  // Sound: three rising beeps, repeated every 3 s.
  try {
    const actx = new (window.AudioContext || window.webkitAudioContext)();
    const beep = () => {
      try {
        const o = actx.createOscillator();
        const g = actx.createGain();
        o.type = 'square';
        o.frequency.value = 880;
        g.gain.setValueAtTime(0.25, actx.currentTime);
        g.gain.exponentialRampToValueAtTime(0.001, actx.currentTime + 0.4);
        o.connect(g); g.connect(actx.destination);
        o.start(); o.stop(actx.currentTime + 0.4);
      } catch(e) { void e; }
    };
    beep();
    _alarmInterval = setInterval(() => { beep(); beep(); }, 3000);
  } catch(e) { void e; }
  // Visual: flash the overlay border in amber.
  const overlay = $('timer-overlay');
  overlay.classList.add('tm-alert');
  _alarmFlash = setInterval(() => {
    if (overlay) overlay.classList.toggle('tm-alert-flash');
  }, 500);
}

function stopTimerAlert() {
  if (_alarmInterval) { clearInterval(_alarmInterval); _alarmInterval = null; }
  if (_alarmFlash) { clearInterval(_alarmFlash); _alarmFlash = null; }
  const overlay = $('timer-overlay');
  if (overlay) { overlay.classList.remove('tm-alert', 'tm-alert-flash'); }
}


// A compact clock face for the countdown: MM:SS (or HH:MM:SS when large).
function fmtClock(totalSec) {
  totalSec = Math.max(0, Math.round(totalSec || 0));
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const sec = totalSec % 60;
  if (h > 0) return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
  return `${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
}

const tmCanvas = $('tm-canvas');
const tmCtx = tmCanvas ? tmCanvas.getContext('2d') : null;
let tmParticles = [];

function sizeTimerCanvas() {
  if (!tmCanvas) return;
  const r = dpr();
  tmCanvas.width = Math.floor(window.innerWidth * r);
  tmCanvas.height = Math.floor(window.innerHeight * r);
  tmCtx.setTransform(r, 0, 0, r, 0, 0);
}

function startTimerFx() {
  if (!tmCtx || timer.raf) return;
  sizeTimerCanvas();
  if (!tmParticles.length) {
    for (let i = 0; i < 40; i++) {
      tmParticles.push({
        x: Math.random() * window.innerWidth,
        y: Math.random() * window.innerHeight,
        v: 0.2 + Math.random() * 0.8,
        r: 0.8 + Math.random() * 2.0,
      });
    }
  }
  const step = () => {
    if (!timer.running) { stopTimerFx(); return; }
    const W2 = window.innerWidth, H2 = window.innerHeight;
    tmCtx.clearRect(0, 0, W2, H2);
    tmCtx.fillStyle = 'rgba(56, 225, 240, .35)';
    for (const p of tmParticles) {
      p.y += p.v;
      if (p.y > H2) { p.y = -4; p.x = Math.random() * W2; }
      tmCtx.globalAlpha = 0.15 + (p.r / 2.8) * 0.5;
      tmCtx.beginPath();
      tmCtx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      tmCtx.fill();
    }
    tmCtx.globalAlpha = 1;
    timer.raf = requestAnimationFrame(step);
  };
  timer.raf = requestAnimationFrame(step);
}

function stopTimerFx() {
  if (timer.raf) { cancelAnimationFrame(timer.raf); timer.raf = 0; }
  if (tmCtx) tmCtx.clearRect(0, 0, window.innerWidth, window.innerHeight);
}




function setChip(id, ok, label) {
  const el = $(id);
  if (!el) return;
  el.dataset.ok = ok === null || ok === undefined ? 'unknown' : String(!!ok);
  if (label) {
    const span = el.querySelector('span');
    if (span) span.textContent = label;
  }
}

function setModule(key, ok, label) {
  const li = document.querySelector(`.modules li[data-key="${key}"]`);
  if (!li) return;
  li.dataset.ok = ok === null || ok === undefined ? 'unknown' : String(!!ok);
  li.querySelector('b').textContent = label;
}

function applyMeta(meta) {
  if ('model' in meta) $('model-name').textContent = `MODEL ${String(meta.model).toUpperCase()}`;
  if ('host' in meta) $('host').textContent = meta.host;
  if ('mic_device' in meta) $('mic-device').textContent = String(meta.mic_device).toUpperCase();
  if ('tts_voice' in meta) $('tts-voice').textContent = String(meta.tts_voice).toUpperCase();
  if ('threshold' in meta) $('threshold').textContent = meta.threshold;
  if ('mic_ok' in meta) ui.micOk = meta.mic_ok;
  if ('mic_muted' in meta) ui.micMuted = !!meta.mic_muted;

  if ('mic_ok' in meta || 'mic_muted' in meta) {
    const live = ui.micOk !== false && !ui.micMuted;
    setChip('chip-mic', ui.micOk === false ? false : (ui.micMuted ? null : true),
            ui.micMuted ? 'MUTE' : 'MIC');
    setModule('voice', ui.micOk === false ? false : (ui.micMuted ? null : true),
              ui.micOk === false ? 'OFFLINE' : (ui.micMuted ? 'MUTED' : 'ONLINE'));
    $('btn-mic').classList.toggle('active', live);
    const flag = $('mic-flag');
    flag.classList.toggle('off', !live);
    $('mic-flag-text').textContent = ui.micOk === false ? 'MICROPHONE OFFLINE'
      : ui.micMuted ? 'MICROPHONE MUTED' : 'MICROPHONE ACTIVE';
  }

  if ('api_ok' in meta) {
    setChip('chip-sys', meta.api_ok);
    setModule('core', meta.api_ok, meta.api_ok ? 'ONLINE' : 'FAULT');
  }
  if ('tools_ok' in meta) setModule('tools', meta.tools_ok, meta.tools_ok ? 'ONLINE' : 'FAULT');
  if ('audio_ok' in meta) setModule('audio', meta.audio_ok, meta.audio_ok ? 'ONLINE' : 'OFFLINE');
  if ('network_ok' in meta) {
    setChip('chip-net', meta.network_ok);
    setModule('network', meta.network_ok, meta.network_ok ? 'ONLINE' : 'OFFLINE');
    $('net-state').textContent = meta.network_ok ? 'CONNECTED' : 'OFFLINE';
    $('net-state').style.color = meta.network_ok ? '' : 'var(--warn)';
  }
  if ('weather_ok' in meta) {
    setModule('weather', meta.weather_ok, meta.weather_ok ? 'ONLINE' : 'OFFLINE');
  }
}

/* ── feeds ────────────────────────────────────────────────────────────── */

function nearBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 48;
}

/* Scrollback state. The live feed only carries what `bus` still holds in memory
   (a dozen turns); everything older is fetched from /api/history on demand.
   `oldestId` is the lowest turn id on screen, which is where the next page
   starts — no offsets, so a turn arriving mid-fetch cannot shift the window. */
const scrollback = { oldestId: null, more: true, loading: false, paged: false };

function setMarker(text) {
  const box = $('conversation');
  let el = box.querySelector('.scrollback');
  if (!text) { if (el) el.remove(); return; }
  if (!el) {
    el = document.createElement('p');
    el.className = 'scrollback';
    box.prepend(el);
  }
  el.textContent = text;
}

function addTurn(entry, prepend) {
  const box = $('conversation');
  const empty = box.querySelector('.empty');
  if (empty) empty.remove();
  const stick = !prepend && nearBottom(box);

  if (typeof entry.id === 'number' &&
      (scrollback.oldestId === null || entry.id < scrollback.oldestId)) {
    scrollback.oldestId = entry.id;
  }

  const div = document.createElement('div');
  div.className = `turn ${entry.role === 'ron' ? 'ron' : 'user'}`;
  const who = document.createElement('div');
  who.className = 'who';
  who.textContent = entry.role === 'ron' ? 'RON' : 'YOU';
  const p = document.createElement('p');
  p.textContent = entry.text;            // textContent, never innerHTML
  div.append(who, p);

  if (prepend) {
    div.classList.add('past');           // no slide-in for a whole page at once
    // Below the marker, so that stays pinned to the very top of the panel.
    const marker = box.querySelector('.scrollback');
    if (marker) marker.after(div); else box.prepend(div);
  } else {
    box.append(div);
  }

  // Cap the panel so a session left open for days cannot grow without bound.
  // Only live turns evict: trimming right after a fetch would undo the page we
  // just paid for. The cap lifts once scrollback is in play, since 14 entries is
  // a sensible live window but a uselessly short scroll history.
  if (!prepend) {
    const cap = scrollback.paged ? 400 : 14;
    while (box.children.length > cap) box.firstElementChild.remove();
  }
  if (stick) box.scrollTop = box.scrollHeight;
}

async function loadOlder() {
  if (scrollback.loading || !scrollback.more) return;
  scrollback.loading = true;
  scrollback.paged = true;
  const box = $('conversation');
  const prevH = box.scrollHeight;
  const prevTop = box.scrollTop;
  setMarker('RECALLING EARLIER EXCHANGES…');
  try {
    // No `before` on the first page when the panel started empty: there is
    // nothing on screen for it to duplicate, so the newest page is what we want.
    const url = '/api/history?limit=50' +
      (scrollback.oldestId === null ? '' : `&before=${scrollback.oldestId}`);
    const res = await fetch(url);
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    const turns = data.turns || [];
    scrollback.more = !!data.more && turns.length > 0;

    if (!turns.length) {
      // Say "nothing older" only when there is something for it to sit above;
      // an empty panel already reads as empty.
      setMarker(box.querySelector('.empty') ? '' : 'BEGINNING OF RECORD');
    } else {
      setMarker(scrollback.more ? '' : 'BEGINNING OF RECORD');
      // Newest first, each inserted above the last, so the page lands in
      // chronological order beneath what was already there.
      [...turns].reverse().forEach((t) => addTurn(t, true));
      // Hold the reader's place: the panel just grew upwards by exactly the
      // height of what we inserted.
      box.scrollTop = prevTop + (box.scrollHeight - prevH);
    }
  } catch {
    setMarker('COULD NOT REACH THE RECORD');
  } finally {
    scrollback.loading = false;
  }
}

$('conversation').addEventListener('scroll', () => {
  if ($('conversation').scrollTop < 40) loadOlder();
});

const GLYPH = { ok: '✓', pending: '▸', fail: '✕', info: '·' };

function addActivity(entry) {
  const box = $('activity');
  const empty = box.querySelector('.empty');
  if (empty) empty.remove();
  const stick = nearBottom(box);

  const div = document.createElement('div');
  div.className = `act ${entry.status || 'ok'}`;
  const i = document.createElement('i');
  i.textContent = GLYPH[entry.status] || GLYPH.ok;
  const span = document.createElement('span');
  span.textContent = entry.text;
  const time = document.createElement('time');
  const d = new Date((entry.t || Date.now() / 1000) * 1000);
  time.textContent = `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
  div.append(i, span, time);
  box.append(div);

  while (box.children.length > 60) box.firstElementChild.remove();
  if (stick) box.scrollTop = box.scrollHeight;
}

/* ── SSE link ─────────────────────────────────────────────────────────── */

let source = null;
let hydrated = false;

function connect() {
  if (source) source.close();
  source = new EventSource('/events');

  source.onopen = () => {
    root.dataset.link = 'up';
    $('system-status').textContent = 'SYSTEM ONLINE';
  };

  source.onerror = () => {
    // EventSource retries on its own; just reflect the outage.
    root.dataset.link = 'down';
    $('system-status').textContent = 'LINK LOST';
  };

  source.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    root.dataset.link = 'up';

    switch (msg.kind) {
      case 'snapshot':
        setState(msg.state, msg.detail);
        applyMeta(msg.meta || {});
        applyMetrics(msg.metrics || {});
        applyWeather(msg.weather || {});
        applySearch(msg.search || {}, true);
        applySpeed(msg.netspeed || {}, true);
        applyTimer(msg.timer || {}, true);
        $('conversation').innerHTML = '';
        $('activity').innerHTML = '';
        // A reconnect replaces the panel, so the paging window has to be
        // rebuilt with it rather than left pointing at an id we just discarded.
        scrollback.oldestId = null;
        scrollback.more = true;
        scrollback.paged = false;
        // Not `forEach(addTurn)`: forEach passes the index as the second
        // argument, which every entry after the first would read as `prepend`.
        (msg.transcript || []).forEach((e) => addTurn(e));
        (msg.activity || []).forEach((e) => addActivity(e));
        if (!(msg.transcript || []).length) {
          $('conversation').innerHTML = '<p class="empty">AWAITING FIRST EXCHANGE</p>';
          // Nothing on screen to page back from, and nothing to duplicate:
          // pull the last conversation in so a restart resumes where it left off.
          loadOlder();
        } else if (scrollback.oldestId === null) {
          // Live entries with no id mean the database is unavailable, so there
          // is no record to page into and a fetch would only duplicate these.
          scrollback.more = false;
        }
        if (!(msg.activity || []).length) {
          $('activity').innerHTML = '<p class="empty">NO ACTIVITY LOGGED</p>';
        }
        hydrated = true;
        finishBoot();
        break;
      case 'state':
        setState(msg.state, msg.detail);
        if (typeof msg.amplitude === 'number') pushLevel(msg.amplitude);
        break;
      case 'level':
        pushLevel(msg.level);
        break;
      case 'transcript':
        addTurn(msg.entry);
        break;
      case 'activity':
        addActivity(msg.entry);
        break;
      case 'metrics':
        applyMetrics(msg.metrics || {});
        break;
      case 'meta':
        applyMeta(msg.meta || {});
        break;
      case 'weather':
        applyWeather(msg.weather || {});
        break;
      case 'search':
        applySearch(msg.search || {}, false);
        break;
      case 'netspeed':
        applySpeed(msg.netspeed || {}, false);
        break;
      case 'timer':
        applyTimer(msg.timer || {}, false);
        break;
    }
  };
}

function pushLevel(v) {
  ui.target = Math.max(0, Math.min(1, Number(v) || 0));
  ui.lastLevelAt = performance.now();
}

/* ── controls ─────────────────────────────────────────────────────────── */

async function post(url, body) {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return res.ok;
  } catch {
    return false;
  }
}

const input = $('cmd-input');

$('cmdline').addEventListener('submit', (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  post('/api/command', { text });
});

function toggleMic() {
  post('/api/control', { action: ui.micMuted ? 'mic_on' : 'mic_off' });
}
$('btn-mic').addEventListener('click', toggleMic);
$('chip-mic').addEventListener('click', toggleMic);
$('chip-mic').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleMic(); }
});
$('core').addEventListener('click', toggleMic);

/* Power needs a deliberate second press rather than a modal dialog — a confirm()
   box would break the illusion, and an accidental single click should not end
   the session. */
const power = $('btn-power');
let armed = 0;
power.addEventListener('click', () => {
  const now = performance.now();
  if (armed && now - armed < 3000) {
    power.classList.remove('active');
    armed = 0;
    post('/api/control', { action: 'shutdown' });
    return;
  }
  armed = now;
  power.classList.add('active');
  power.title = 'Press again to confirm shutdown';
  setTimeout(() => {
    if (armed && performance.now() - armed >= 3000) {
      armed = 0;
      power.classList.remove('active');
      power.title = 'Shut down RON';
    }
  }, 3100);
});

document.addEventListener('keydown', (e) => {
  // The search overlay sits on top of everything, so it gets Escape first —
  // otherwise this branch blurs the input and leaves the overlay stuck open.
  if (search.open && e.key === 'Escape') { e.preventDefault(); closeSearch(); return; }
  if (speed.open && e.key === 'Escape') { e.preventDefault(); closeSpeed(); return; }
  if (timer.open && e.key === 'Escape') { e.preventDefault(); closeTimer(); return; }
  const typing = document.activeElement === input;
  if (e.key === 'Escape') { input.blur(); return; }
  if (typing) return;
  if (e.key === '/' || e.key === 'Enter') { e.preventDefault(); input.focus(); return; }
  if (e.key === 'm' || e.key === 'M') { toggleMic(); }
});
$('so-close').addEventListener('click', closeSearch);
$('ns-close').addEventListener('click', closeSpeed);
$('tm-close').addEventListener('click', closeTimer);

/* ══ RENDERING ═══════════════════════════════════════════════════════════ */

const dpr = () => Math.min(window.devicePixelRatio || 1, 2);

/* ── background: drifting particles over a pre-rendered static layer ──── */

const bg = $('bg-canvas');
const bgx = bg.getContext('2d');
/* Concentric geometry and the faint technical annotations never move, so they
   are rendered once per resize and blitted, rather than re-stroked each frame. */
const still = document.createElement('canvas');
const stillx = still.getContext('2d');

let W = 0, H = 0, coreCx = 0, coreCy = 0;
const particles = [];
const POINTS = [];

function seedParticles() {
  particles.length = 0;
  const count = Math.round(Math.min(110, (W * H) / 22000));
  for (let i = 0; i < count; i++) {
    particles.push({
      x: Math.random() * W,
      y: Math.random() * H,
      vx: (Math.random() - 0.5) * 0.14,
      vy: -0.05 - Math.random() * 0.16,
      r: 0.5 + Math.random() * 1.3,
      a: 0.05 + Math.random() * 0.22,
      ph: Math.random() * Math.PI * 2,
    });
  }
}

function seedPoints() {
  POINTS.length = 0;
  const labels = ['0x1F4A', 'SYS·04', 'Δ 0.017', 'REF 220', 'CH·07',
                  'N 41.2', '0b1011', 'SEQ 88', 'λ 0.44', 'IDX 12'];
  for (let i = 0; i < 12; i++) {
    POINTS.push({
      x: 0.06 + Math.random() * 0.88,
      y: 0.12 + Math.random() * 0.78,
      label: labels[i % labels.length],
      r: 3 + Math.random() * 9,
    });
  }
}

function drawStill() {
  still.width = Math.floor(W * dpr());
  still.height = Math.floor(H * dpr());
  stillx.setTransform(dpr(), 0, 0, dpr(), 0, 0);
  stillx.clearRect(0, 0, W, H);

  // concentric HUD geometry behind the core
  const base = Math.min(W, H);
  stillx.lineWidth = 1;
  [0.30, 0.44, 0.62, 0.82, 1.05].forEach((k, i) => {
    stillx.beginPath();
    stillx.arc(coreCx, coreCy, base * k, 0, Math.PI * 2);
    stillx.strokeStyle = rgba(i % 2 ? 0.020 : 0.034);
    stillx.stroke();
  });

  // a couple of very faint radial spokes
  stillx.strokeStyle = rgba(0.018);
  for (let i = 0; i < 12; i++) {
    const a = (i / 12) * Math.PI * 2 + 0.26;
    stillx.beginPath();
    stillx.moveTo(coreCx + Math.cos(a) * base * 0.46, coreCy + Math.sin(a) * base * 0.46);
    stillx.lineTo(coreCx + Math.cos(a) * base * 1.05, coreCy + Math.sin(a) * base * 1.05);
    stillx.stroke();
  }

  // scattered technical annotations
  stillx.font = '9px "JetBrains Mono", Consolas, monospace';
  POINTS.forEach((p) => {
    const x = p.x * W, y = p.y * H;
    stillx.strokeStyle = rgba(0.075);
    stillx.beginPath();
    stillx.arc(x, y, p.r, 0, Math.PI * 2);
    stillx.stroke();
    stillx.beginPath();
    stillx.moveTo(x - p.r - 4, y); stillx.lineTo(x - p.r - 12, y);
    stillx.moveTo(x + p.r + 4, y); stillx.lineTo(x + p.r + 12, y);
    stillx.stroke();
    stillx.fillStyle = rgba(0.09);
    stillx.fillText(p.label, x + p.r + 16, y + 3);
  });
}

/* ── waveform ─────────────────────────────────────────────────────────── */

const wave = $('wave-canvas');
const wx = wave.getContext('2d');
const BARS = 128;
const history = new Float32Array(BARS);
let waveW = 0, waveH = 0, lastPush = 0;

/* ── radial collar around the core ────────────────────────────────────── */

const RADIAL = 64;
const radialVals = new Float32Array(RADIAL);
const radialNodes = [];

function buildRadial() {
  const g = $('radial-bars');
  g.innerHTML = '';
  radialNodes.length = 0;
  const R0 = 190;
  for (let i = 0; i < RADIAL; i++) {
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', 250);
    line.setAttribute('y1', 250 - R0);
    line.setAttribute('x2', 250);
    line.setAttribute('y2', 250 - R0 - 1);
    line.setAttribute('transform', `rotate(${(i / RADIAL) * 360} 250 250)`);
    g.appendChild(line);
    radialNodes.push(line);
  }
}
buildRadial();

/* ── layout ───────────────────────────────────────────────────────────── */

function resize() {
  W = window.innerWidth;
  H = window.innerHeight;
  const ratio = dpr();

  bg.width = Math.floor(W * ratio);
  bg.height = Math.floor(H * ratio);
  bgx.setTransform(ratio, 0, 0, ratio, 0, 0);

  const box = document.querySelector('.core').getBoundingClientRect();
  coreCx = box.left + box.width / 2;
  coreCy = box.top + box.height / 2;

  const wbox = wave.getBoundingClientRect();
  waveW = Math.max(1, Math.floor(wbox.width));
  waveH = Math.max(1, Math.floor(wbox.height));
  wave.width = Math.floor(waveW * ratio);
  wave.height = Math.floor(waveH * ratio);
  wx.setTransform(ratio, 0, 0, ratio, 0, 0);

  seedParticles();
  if (!POINTS.length) seedPoints();
  drawStill();
}
window.addEventListener('resize', resize);
// The search overlay's canvas is sized against the window too.
if (soCanvas) window.addEventListener('resize', sizeScanCanvas);

/* ── frame loop ───────────────────────────────────────────────────────── */

const ENERGY = {           // how lively the visualiser is per state
  idle: 0.20, listening: 1.0, thinking: 0.42,
  speaking: 1.0, executing: 0.5, error: 0.3, offline: 0.05,
};

let lastAmp = -1;

function frame(now) {
  const t = now / 1000;

  /* Level: real data when it is flowing, a slow breath when it is not. The
     500 ms cutoff is comfortably longer than the 50 ms RMS interval, so a live
     microphone never falls through to the synthetic path. */
  const fresh = now - ui.lastLevelAt < 500;
  if (!fresh) {
    const e = ENERGY[ui.state] ?? 0.2;
    ui.target = e * (0.10 + 0.06 * (Math.sin(t * 1.1) * 0.5 + 0.5)
                          + 0.03 * (Math.sin(t * 2.7 + 1.3) * 0.5 + 0.5));
  }
  ui.level += (ui.target - ui.level) * (fresh ? 0.30 : 0.06);

  const amp = Math.round(ui.level * 1000) / 1000;
  if (Math.abs(amp - lastAmp) > 0.004) {
    root.style.setProperty('--amp', amp);
    lastAmp = amp;
    $('level-readout').textContent = `LVL ${amp.toFixed(2)}`;
  }

  /* ── background ── */
  bgx.clearRect(0, 0, W, H);
  bgx.drawImage(still, 0, 0, W, H);
  for (const p of particles) {
    p.x += p.vx; p.y += p.vy;
    if (p.y < -8) { p.y = H + 8; p.x = Math.random() * W; }
    if (p.x < -8) p.x = W + 8;
    if (p.x > W + 8) p.x = -8;
    const twinkle = 0.65 + 0.35 * Math.sin(t * 1.4 + p.ph);
    bgx.beginPath();
    bgx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
    bgx.fillStyle = rgba(p.a * twinkle);
    bgx.fill();
  }

  /* ── waveform ── */
  if (now - lastPush > 30) {
    lastPush = now;
    history.copyWithin(0, 1);
    history[BARS - 1] = ui.level;
  }

  wx.clearRect(0, 0, waveW, waveH);
  const mid = waveH / 2;
  const slot = waveW / BARS;
  const bw = Math.max(1.2, slot * 0.42);
  const maxH = mid * 0.94;

  // centre axis
  wx.fillStyle = rgba(0.10);
  wx.fillRect(0, mid - 0.5, waveW, 1);

  wx.shadowColor = rgba(0.55);
  wx.shadowBlur = 7;
  for (let i = 0; i < BARS; i++) {
    // window function: the trace fades into the panel edges instead of
    // stopping dead against them
    const edge = Math.sin((i / (BARS - 1)) * Math.PI) ** 0.55;
    const texture = 0.72 + 0.28 * Math.abs(Math.sin(i * 1.93 + t * 0.6));
    const h = Math.max(1, history[i] ** 0.82 * maxH * edge * texture);
    const x = i * slot + (slot - bw) / 2;
    const a = 0.22 + 0.62 * edge * Math.min(1, history[i] * 2.4 + 0.12);
    wx.fillStyle = rgba(a);
    wx.fillRect(x, mid - h, bw, h);
    wx.fillStyle = rgba(a * 0.42);          // dimmer mirrored half
    wx.fillRect(x, mid + 1, bw, h * 0.66);
  }
  wx.shadowBlur = 0;

  /* ── radial collar ── */
  const energy = ui.level;
  for (let i = 0; i < RADIAL; i++) {
    const wob = 0.42
      + 0.34 * Math.sin(i * 0.71 + t * 2.3)
      + 0.24 * Math.sin(i * 1.87 - t * 1.4);
    const want = Math.max(0, energy * wob) * 34;
    radialVals[i] += (want - radialVals[i]) * 0.22;
    radialNodes[i].setAttribute('y2', 250 - 190 - Math.max(0.6, radialVals[i]));
  }

  requestAnimationFrame(frame);
}

/* ── boot ─────────────────────────────────────────────────────────────── */

const BOOT_LINES = [
  'INITIALISING R.O.N. CORE',
  'MOUNTING VOICE ENGINE',
  'LINKING TOOL SUBSYSTEM',
  'CALIBRATING AUDIO INTERFACE',
  'HUD ONLINE',
];
const bootStart = performance.now();
let bootDone = false;

function runBoot() {
  const log = $('boot-log');
  BOOT_LINES.forEach((line, i) => {
    setTimeout(() => {
      const b = document.createElement('div');
      b.innerHTML = '';
      b.textContent = line;
      if (i === BOOT_LINES.length - 1) b.innerHTML = `<b>${line}</b>`;
      log.appendChild(b);
      while (log.children.length > 4) log.firstElementChild.remove();
    }, 170 * i);
  });
}

function finishBoot() {
  if (bootDone) return;
  bootDone = true;
  // Hold the overlay for at least the length of the boot log so a fast local
  // connection does not flash it on and off.
  const wait = Math.max(0, 1150 - (performance.now() - bootStart));
  setTimeout(() => $('boot').classList.add('done'), wait);
}

/* ── go ───────────────────────────────────────────────────────────────── */

runBoot();
resize();
// Fonts land after first paint and change the core's measured box slightly.
if (document.fonts && document.fonts.ready) document.fonts.ready.then(resize);
requestAnimationFrame(frame);
connect();
// If the server never answers, drop the overlay anyway rather than trapping the
// user behind a spinner.
setTimeout(finishBoot, 4000);
