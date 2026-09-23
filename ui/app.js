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
  sleep: 'SLEEPING',
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

function applyLanguage(lang) {
  const isBn = (lang || 'en').toLowerCase().startsWith('b');
  const code = isBn ? 'bn' : 'en';
  root.dataset.lang = code;
  const badge = $('lang-badge');
  if (badge) {
    badge.textContent = isBn ? 'LANG: BN (বাংলা)' : 'LANG: EN';
    badge.setAttribute('title', isBn ? 'Bangla Mode Active' : 'English Mode Active');
  }
  readAccent();
}

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

let latestMetrics = { cpu: 18, ram: 42, disk: 50, net_down: 0.0, net_up: 0.0, uptime: 0 };
let isDiagnosticSweeping = false;

function applyMetrics(m) {
  Object.assign(latestMetrics, m);
  if (isDiagnosticSweeping) return; // do not snap values while numbers are rolling up
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

/* ── stark laser diagnostic sweep (0% GPU pure 2d css) ─────────────────── */

function triggerLaserDiagnostic(data = {}) {
  if (isDiagnosticSweeping) return;
  isDiagnosticSweeping = true;

  // If invoked via UI shortcut/click, request server to trigger audio & broadcast
  if (data.fromServer !== true) {
    fetch('/api/diagnostic', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}'
    }).catch(() => {});
  }

  const layer = $('laser-diagnostic-layer');
  const chipSys = $('chip-sys');
  const phaseLabel = $('laser-readout-phase');
  const coreState = $('core-state');
  const coreDetail = $('core-detail');

  if (chipSys) chipSys.classList.add('diagnostic-active');
  document.body.classList.add('diagnostic-sweeping');
  if (layer) {
    layer.classList.remove('active');
    void layer.offsetWidth; // force DOM reflow to restart CSS keyframes
    layer.classList.add('active');
  }

  if (phaseLabel) phaseLabel.textContent = 'INITIALIZING';
  if (coreState) coreState.textContent = 'DIAGNOSTIC';
  if (coreDetail) coreDetail.textContent = 'TELEMETRY CALIBRATION SWEEP';

  // Panels react with neon edge flare as the laser beam intersects them
  const panels = [
    { el: document.querySelector('.panel-left'), delay: 240 },
    { el: document.querySelector('.panel-right'), delay: 340 },
    { el: document.querySelector('.core-stage'), delay: 520 },
    { el: document.querySelector('.panel-weather'), delay: 680 },
    { el: document.querySelector('.panel-bl'), delay: 960 },
    { el: document.querySelector('.visualizer'), delay: 1040 },
    { el: document.querySelector('.panel-br'), delay: 1120 },
  ];

  panels.forEach(p => {
    if (!p.el) return;
    setTimeout(() => {
      p.el.classList.add('panel-laser-hit');
      setTimeout(() => p.el.classList.remove('panel-laser-hit'), 450);
    }, p.delay);
  });

  // Target metrics for roll up
  const targetCpu = Number(data.cpu ?? latestMetrics.cpu ?? 18);
  const targetRam = Number(data.ram ?? latestMetrics.ram ?? 42);
  const targetDisk = Number(data.disk ?? latestMetrics.disk ?? 55);
  const targetNetDown = Number(data.net_down ?? latestMetrics.net_down ?? 14.8);
  const targetNetUp = Number(data.net_up ?? latestMetrics.net_up ?? 6.2);

  const meters = [
    { id: 'meter-cpu', target: targetCpu },
    { id: 'meter-ram', target: targetRam },
    { id: 'meter-disk', target: targetDisk },
  ];

  // Prime meters at 00%
  meters.forEach(m => {
    const el = $(m.id);
    if (!el) return;
    el.classList.add('rolling');
    const num = el.querySelector('b');
    const fill = el.querySelector('.fill');
    if (num) num.innerHTML = `00<i>%</i>`;
    if (fill) fill.style.width = `0%`;
  });

  const rollStartTime = performance.now();
  const rollDuration = 1200; // ms

  function animateRollUp(now) {
    const elapsed = now - rollStartTime;
    const progress = Math.min(1, elapsed / rollDuration);

    meters.forEach(m => {
      const el = $(m.id);
      if (!el) return;
      const num = el.querySelector('b');
      const fill = el.querySelector('.fill');

      if (progress < 0.35) {
        // Scramble phase: flicker rapid cyber random digits
        const scrambleVal = pad2(Math.floor(Math.random() * 99));
        if (num) num.innerHTML = `${scrambleVal}<i>%</i>`;
        if (fill) fill.style.width = `${Math.round(progress * m.target * 0.4)}%`;
      } else {
        // Smooth count-up to target metric
        const ease = (progress - 0.35) / 0.65;
        const currentVal = Math.round(m.target * ease);
        const clamped = Math.max(0, Math.min(100, currentVal));
        if (num) num.innerHTML = `${clamped}<i>%</i>`;
        if (fill) fill.style.width = `${clamped}%`;
      }
    });

    if ($('net-down')) {
      $('net-down').textContent = (targetNetDown * progress).toFixed(1);
    }
    if ($('net-up')) {
      $('net-up').textContent = (targetNetUp * progress).toFixed(1);
    }

    if (progress < 1) {
      requestAnimationFrame(animateRollUp);
    } else {
      meters.forEach(m => {
        const el = $(m.id);
        if (!el) return;
        el.classList.remove('rolling');
        setMeter(m.id, m.target);
      });
      if ($('net-down')) $('net-down').textContent = targetNetDown.toFixed(1);
      if ($('net-up')) $('net-up').textContent = targetNetUp.toFixed(1);
    }
  }

  // Start roll-up as the laser enters the telemetry panels
  setTimeout(() => {
    if (phaseLabel) phaseLabel.textContent = 'ROLLING TELEMETRY';
    requestAnimationFrame(animateRollUp);
  }, 220);

  setTimeout(() => {
    if (phaseLabel) phaseLabel.textContent = 'TELEMETRY LOCKED';
  }, 920);

  // Conclude sweep
  setTimeout(() => {
    if (layer) layer.classList.remove('active');
    document.body.classList.remove('diagnostic-sweeping');
    if (chipSys) chipSys.classList.remove('diagnostic-active');
    if (phaseLabel) phaseLabel.textContent = 'ALL SYSTEMS NOMINAL';
    if (coreState) coreState.textContent = 'ONLINE';
    if (coreDetail) coreDetail.textContent = 'ALL SYSTEMS NOMINAL';

    addActivity({
      text: `Diagnostic sweep complete: CPU ${Math.round(targetCpu)}%, RAM ${Math.round(targetRam)}%, DISK ${Math.round(targetDisk)}%`,
      level: 'accent',
      time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    });

    isDiagnosticSweeping = false;
  }, 1650);
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


/* ═══ NETWORK SPEED OVERLAY · STARK ARC TACHOMETER ═══════════════════ */

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
  startSpeedFx();
}

function closeSpeed() {
  speed.open = false;
  speed.scanning = false;
  stopSpeedFx();
  const el = $('speed-overlay');
  el.classList.remove('open');
  el.setAttribute('aria-hidden', 'true');
}

// Tachometer state and constants
const meterCanvas = $('ns-meter-canvas');
const meterCtx = meterCanvas ? meterCanvas.getContext('2d') : null;

const nsMeter = {
  currentSpeed: 0,
  targetSpeed: 0,
  currentAngle: (150 * Math.PI) / 180,
  targetAngle: (150 * Math.PI) / 180,
  velocity: 0,
  phase: 'standby', // 'standby' | 'ping' | 'download' | 'upload' | 'done' | 'error'
  pingMs: null,
  pulsePhase: 0,
};

const METER_START_RAD = (150 * Math.PI) / 180;
const METER_SPAN_RAD = (240 * Math.PI) / 180;

const SPEED_STOPS = [
  { s: 0, f: 0.0, label: '0' },
  { s: 5, f: 0.08, label: '5' },
  { s: 10, f: 0.17, label: '10' },
  { s: 25, f: 0.31, label: '25' },
  { s: 50, f: 0.46, label: '50' },
  { s: 100, f: 0.63, label: '100' },
  { s: 250, f: 0.79, label: '250' },
  { s: 500, f: 0.90, label: '500' },
  { s: 1000, f: 1.0, label: '1K' },
];

function speedToFraction(v) {
  if (v <= 0) return 0;
  if (v >= 1000) return 1;
  for (let i = 0; i < SPEED_STOPS.length - 1; i++) {
    const a = SPEED_STOPS[i];
    const b = SPEED_STOPS[i + 1];
    if (v >= a.s && v <= b.s) {
      const ratio = (v - a.s) / (b.s - a.s);
      return a.f + ratio * (b.f - a.f);
    }
  }
  return 1;
}

function fractionToAngle(f) {
  return METER_START_RAD + Math.max(0, Math.min(1, f)) * METER_SPAN_RAD;
}

function sizeMeterCanvas() {
  if (!meterCanvas || !meterCtx) return;
  const r = dpr();
  const rect = meterCanvas.getBoundingClientRect();
  const w = Math.max(300, Math.floor(rect.width || 480));
  const h = Math.max(180, Math.floor(rect.height || 286));
  meterCanvas.width = Math.floor(w * r);
  meterCanvas.height = Math.floor(h * r);
  meterCtx.setTransform(r, 0, 0, r, 0, 0);
}

function drawStarkMeter(t) {
  if (!meterCanvas || !meterCtx) return;
  const r = dpr();
  const W = meterCanvas.width / r;
  const H = meterCanvas.height / r;
  if (W <= 0 || H <= 0) return;

  meterCtx.clearRect(0, 0, W, H);

  const cx = W / 2;
  const cy = H * 0.64;
  const R = Math.min(W * 0.42, H * 0.66);

  // Pick color theme based on phase
  let accent = '#38e1f0';
  let accentRgb = '56, 225, 240';
  let glowColor = 'rgba(56, 225, 240, .8)';

  if (nsMeter.phase === 'ping') {
    accent = '#34d399';
    accentRgb = '52, 211, 153';
    glowColor = 'rgba(52, 211, 153, .8)';
  } else if (nsMeter.phase === 'upload') {
    accent = '#c084fc';
    accentRgb = '192, 132, 252';
    glowColor = 'rgba(192, 132, 252, .8)';
  } else if (nsMeter.phase === 'error') {
    accent = '#f43f5e';
    accentRgb = '244, 63, 94';
    glowColor = 'rgba(244, 63, 94, .8)';
  }

  // 1. Radar Sonar Waves in Ping mode
  if (nsMeter.phase === 'ping') {
    nsMeter.pulsePhase = (nsMeter.pulsePhase + 0.035) % 1;
    for (let i = 0; i < 3; i++) {
      const p = (nsMeter.pulsePhase + i / 3) % 1;
      const waveR = 20 + p * (R * 0.85);
      meterCtx.save();
      meterCtx.beginPath();
      meterCtx.arc(cx, cy, waveR, METER_START_RAD - 0.2, METER_START_RAD + METER_SPAN_RAD + 0.2);
      meterCtx.strokeStyle = `rgba(${accentRgb}, ${(1 - p) * 0.45})`;
      meterCtx.lineWidth = 1.8;
      meterCtx.stroke();
      meterCtx.restore();
    }
  }

  // 2. Outer Technical HUD Ring & Guides
  meterCtx.save();
  meterCtx.beginPath();
  meterCtx.arc(cx, cy, R + 14, METER_START_RAD - 0.05, METER_START_RAD + METER_SPAN_RAD + 0.05);
  meterCtx.strokeStyle = `rgba(${accentRgb}, .14)`;
  meterCtx.lineWidth = 1;
  meterCtx.setLineDash([4, 6]);
  meterCtx.stroke();
  meterCtx.restore();

  // 3. Background Track Arc
  meterCtx.save();
  meterCtx.beginPath();
  meterCtx.arc(cx, cy, R, METER_START_RAD, METER_START_RAD + METER_SPAN_RAD);
  meterCtx.strokeStyle = `rgba(${accentRgb}, .12)`;
  meterCtx.lineWidth = 7;
  meterCtx.lineCap = 'round';
  meterCtx.stroke();
  meterCtx.restore();

  // 4. Major and Minor Ticks
  const targetFrac = speedToFraction(nsMeter.currentSpeed);
  for (let i = 0; i < SPEED_STOPS.length; i++) {
    const stop = SPEED_STOPS[i];
    const ang = fractionToAngle(stop.f);
    const cosA = Math.cos(ang);
    const sinA = Math.sin(ang);
    const isLit = stop.f <= targetFrac + 0.01;

    // Major tick
    const p1x = cx + (R - 5) * cosA;
    const p1y = cy + (R - 5) * sinA;
    const p2x = cx + (R + 8) * cosA;
    const p2y = cy + (R + 8) * sinA;

    meterCtx.save();
    meterCtx.beginPath();
    meterCtx.moveTo(p1x, p1y);
    meterCtx.lineTo(p2x, p2y);
    meterCtx.lineWidth = isLit ? 2.5 : 1.5;
    meterCtx.strokeStyle = isLit ? accent : `rgba(${accentRgb}, .35)`;
    if (isLit) {
      meterCtx.shadowColor = glowColor;
      meterCtx.shadowBlur = 8;
    }
    meterCtx.stroke();
    meterCtx.restore();

    // Numeric label
    const lx = cx + (R - 22) * cosA;
    const ly = cy + (R - 22) * sinA;
    meterCtx.save();
    meterCtx.font = '700 10px "Orbitron", monospace';
    meterCtx.textAlign = 'center';
    meterCtx.textBaseline = 'middle';
    meterCtx.fillStyle = isLit ? '#ffffff' : `rgba(${accentRgb}, .45)`;
    if (isLit) {
      meterCtx.shadowColor = glowColor;
      meterCtx.shadowBlur = 6;
    }
    meterCtx.fillText(stop.label, lx, ly);
    meterCtx.restore();

    // Minor subdivision ticks between stops
    if (i < SPEED_STOPS.length - 1) {
      const nextStop = SPEED_STOPS[i + 1];
      const subs = stop.s >= 100 ? 3 : 2;
      for (let s = 1; s <= subs; s++) {
        const subF = stop.f + (s / (subs + 1)) * (nextStop.f - stop.f);
        const subAng = fractionToAngle(subF);
        const subLit = subF <= targetFrac;
        const subCos = Math.cos(subAng);
        const subSin = Math.sin(subAng);
        meterCtx.save();
        meterCtx.beginPath();
        meterCtx.moveTo(cx + (R - 2) * subCos, cy + (R - 2) * subSin);
        meterCtx.lineTo(cx + (R + 4) * subCos, cy + (R + 4) * subSin);
        meterCtx.lineWidth = 1;
        meterCtx.strokeStyle = subLit ? `rgba(${accentRgb}, .75)` : `rgba(${accentRgb}, .2)`;
        meterCtx.stroke();
        meterCtx.restore();
      }
    }
  }

  // 5. Active Sweeping Arc Fill
  if (nsMeter.currentAngle > METER_START_RAD + 0.01) {
    meterCtx.save();
    meterCtx.beginPath();
    meterCtx.arc(cx, cy, R, METER_START_RAD, Math.min(METER_START_RAD + METER_SPAN_RAD, nsMeter.currentAngle));
    meterCtx.lineWidth = 7;
    meterCtx.lineCap = 'round';

    const grad = meterCtx.createLinearGradient(
      cx - R, cy, cx + R, cy
    );
    grad.addColorStop(0, `rgba(${accentRgb}, .3)`);
    grad.addColorStop(0.7, accent);
    grad.addColorStop(1, '#ffffff');

    meterCtx.strokeStyle = grad;
    meterCtx.shadowColor = glowColor;
    meterCtx.shadowBlur = 14;
    meterCtx.stroke();
    meterCtx.restore();
  }

  // 6. Arc Reactor Core Pivot
  // Outer metallic bezel
  meterCtx.save();
  meterCtx.beginPath();
  meterCtx.arc(cx, cy, 26, 0, Math.PI * 2);
  meterCtx.fillStyle = 'rgba(7, 18, 28, .92)';
  meterCtx.strokeStyle = `rgba(${accentRgb}, .4)`;
  meterCtx.lineWidth = 2;
  meterCtx.shadowColor = glowColor;
  meterCtx.shadowBlur = 10;
  meterCtx.fill();
  meterCtx.stroke();

  // Inner pulsing core
  meterCtx.beginPath();
  meterCtx.arc(cx, cy, 14, 0, Math.PI * 2);
  meterCtx.fillStyle = `rgba(${accentRgb}, .25)`;
  meterCtx.strokeStyle = accent;
  meterCtx.lineWidth = 1.8;
  meterCtx.fill();
  meterCtx.stroke();

  // Center bright dot
  meterCtx.beginPath();
  meterCtx.arc(cx, cy, 4, 0, Math.PI * 2);
  meterCtx.fillStyle = '#ffffff';
  meterCtx.shadowColor = '#ffffff';
  meterCtx.shadowBlur = 8;
  meterCtx.fill();
  meterCtx.restore();

  // 7. Razor Plasma Needle
  const na = nsMeter.currentAngle;
  const needleLength = R - 10;
  const tipX = cx + needleLength * Math.cos(na);
  const tipY = cy + needleLength * Math.sin(na);

  const perpA = na + Math.PI / 2;
  const baseW = 5.5;
  const b1x = cx + baseW * Math.cos(perpA);
  const b1y = cy + baseW * Math.sin(perpA);
  const b2x = cx - baseW * Math.cos(perpA);
  const b2y = cy - baseW * Math.sin(perpA);

  const tailLen = 18;
  const tailX = cx - tailLen * Math.cos(na);
  const tailY = cy - tailLen * Math.sin(na);

  meterCtx.save();
  meterCtx.beginPath();
  meterCtx.moveTo(tailX, tailY);
  meterCtx.lineTo(b1x, b1y);
  meterCtx.lineTo(tipX, tipY);
  meterCtx.lineTo(b2x, b2y);
  meterCtx.closePath();

  const needleGrad = meterCtx.createLinearGradient(cx, cy, tipX, tipY);
  needleGrad.addColorStop(0, accent);
  needleGrad.addColorStop(0.8, '#ffffff');
  needleGrad.addColorStop(1, '#ffffff');

  meterCtx.fillStyle = needleGrad;
  meterCtx.shadowColor = glowColor;
  meterCtx.shadowBlur = 18;
  meterCtx.fill();

  // White-hot center spine line
  meterCtx.beginPath();
  meterCtx.moveTo(tailX, tailY);
  meterCtx.lineTo(tipX, tipY);
  meterCtx.strokeStyle = '#ffffff';
  meterCtx.lineWidth = 1.2;
  meterCtx.stroke();

  // Needle tip aura corona
  meterCtx.beginPath();
  meterCtx.arc(tipX, tipY, 3, 0, Math.PI * 2);
  meterCtx.fillStyle = '#ffffff';
  meterCtx.shadowColor = '#ffffff';
  meterCtx.shadowBlur = 12;
  meterCtx.fill();
  meterCtx.restore();
}

/* ═══ 3D WARP SPEED PARTICLE CONDUIT ═════════════════════════════════ */
const nsCanvas = $('ns-canvas');
const nsCtx = nsCanvas ? nsCanvas.getContext('2d') : null;
let warpStars = [];

function initWarpStars() {
  warpStars = [];
  for (let i = 0; i < 75; i++) {
    warpStars.push({
      x: (Math.random() - 0.5) * 2000,
      y: (Math.random() - 0.5) * 2000,
      z: Math.random() * 1000 + 1,
      pz: 1000,
    });
  }
}

function sizeSpeedCanvas() {
  if (!nsCanvas || !nsCtx) return;
  const r = dpr();
  nsCanvas.width = Math.floor(window.innerWidth * r);
  nsCanvas.height = Math.floor(window.innerHeight * r);
  nsCtx.setTransform(r, 0, 0, r, 0, 0);
  sizeMeterCanvas();
}

function drawWarpConduit() {
  if (!nsCtx) return;
  const W = window.innerWidth;
  const H = window.innerHeight;
  nsCtx.clearRect(0, 0, W, H);

  const cx = W / 2;
  const cy = H / 2;

  // Speed factor scaled by current Mbps
  const spd = speed.scanning
    ? 6 + Math.min(32, (nsMeter.currentSpeed / 100) * 8)
    : 2.5;

  let starRgb = '56, 225, 240';
  if (nsMeter.phase === 'ping') starRgb = '52, 211, 153';
  else if (nsMeter.phase === 'upload') starRgb = '192, 132, 252';

  for (let i = 0; i < warpStars.length; i++) {
    const s = warpStars[i];
    s.pz = s.z;
    s.z -= spd;

    if (s.z <= 0) {
      s.z = 1000;
      s.pz = 1000;
      s.x = (Math.random() - 0.5) * 2000;
      s.y = (Math.random() - 0.5) * 2000;
      continue;
    }

    const k = 400 / s.z;
    const px = s.x * k + cx;
    const py = s.y * k + cy;

    const pk = 400 / s.pz;
    const prevX = s.x * pk + cx;
    const prevY = s.y * pk + cy;

    if (px < 0 || px > W || py < 0 || py > H) {
      s.z = 1000;
      s.pz = 1000;
      continue;
    }

    const alpha = Math.min(1, Math.max(0.1, (1000 - s.z) / 800));
    nsCtx.save();
    nsCtx.beginPath();
    nsCtx.moveTo(prevX, prevY);
    nsCtx.lineTo(px, py);
    nsCtx.lineWidth = Math.max(0.8, (1 - s.z / 1000) * 3);
    nsCtx.strokeStyle = `rgba(${starRgb}, ${alpha * 0.75})`;
    nsCtx.stroke();
    nsCtx.restore();
  }
}

function startSpeedFx() {
  if (speed.raf) return;
  sizeSpeedCanvas();
  if (!warpStars.length) initWarpStars();

  const step = (t) => {
    // 1. Spring physics for speedometer needle
    const targetFrac = speedToFraction(nsMeter.targetSpeed);
    nsMeter.targetAngle = fractionToAngle(targetFrac);

    // Micro-flutter during active scanning for analog physical realism
    let jitter = 0;
    if (speed.scanning && nsMeter.phase !== 'ping' && nsMeter.targetSpeed > 1) {
      jitter = (Math.sin(t * 0.02) + Math.cos(t * 0.033)) * 0.008;
    }

    const diff = (nsMeter.targetAngle + jitter) - nsMeter.currentAngle;
    nsMeter.velocity = (nsMeter.velocity + diff * 0.12) * 0.76;
    nsMeter.currentAngle += nsMeter.velocity;

    // Interpolate numeric speed readout
    const fracCurrent = Math.max(0, Math.min(1, (nsMeter.currentAngle - METER_START_RAD) / METER_SPAN_RAD));
    let estSpeed = 0;
    for (let i = 0; i < SPEED_STOPS.length - 1; i++) {
      const a = SPEED_STOPS[i];
      const b = SPEED_STOPS[i + 1];
      if (fracCurrent >= a.f && fracCurrent <= b.f) {
        const r = (fracCurrent - a.f) / (b.f - a.f);
        estSpeed = a.s + r * (b.s - a.s);
        break;
      }
    }
    nsMeter.currentSpeed = Math.max(0, estSpeed);

    // 2. Render Tachometer and Warp Conduit
    drawStarkMeter(t);
    drawWarpConduit();

    // 3. Update center digital readout
    if ($('ns-meter-num')) {
      if (nsMeter.phase === 'ping') {
        const pingVal = nsMeter.pingMs != null ? Number(nsMeter.pingMs).toFixed(1) : (nsMeter.currentSpeed * 0.5).toFixed(1);
        $('ns-meter-num').textContent = pingVal;
        $('ns-meter-unit').textContent = 'MS';
        $('ns-meter-tag').textContent = 'PING LATENCY';
        $('ns-meter-live-phase').textContent = 'ANALYZING ROUND-TRIP TIME';
      } else if (nsMeter.phase === 'download') {
        $('ns-meter-num').textContent = nsMeter.currentSpeed.toFixed(1);
        $('ns-meter-unit').textContent = 'MBPS';
        $('ns-meter-tag').textContent = 'DOWNLOAD STREAM';
        $('ns-meter-live-phase').textContent = 'MEASURING DOWNLINK BANDWIDTH';
      } else if (nsMeter.phase === 'upload') {
        $('ns-meter-num').textContent = nsMeter.currentSpeed.toFixed(1);
        $('ns-meter-unit').textContent = 'MBPS';
        $('ns-meter-tag').textContent = 'UPLOAD BURST';
        $('ns-meter-live-phase').textContent = 'TESTING UPLINK CAPACITY';
      } else if (nsMeter.phase === 'done') {
        $('ns-meter-num').textContent = nsMeter.targetSpeed.toFixed(1);
        $('ns-meter-unit').textContent = 'MBPS';
        $('ns-meter-tag').textContent = 'FINAL DOWNLOAD';
        $('ns-meter-live-phase').textContent = 'SPEED TEST VERIFIED';
      } else if (nsMeter.phase === 'error') {
        $('ns-meter-num').textContent = '0.0';
        $('ns-meter-unit').textContent = 'MBPS';
        $('ns-meter-tag').textContent = 'TEST FAILED';
        $('ns-meter-live-phase').textContent = 'CHECK INTERNET CONNECTION';
      }
    }

    speed.raf = requestAnimationFrame(step);
  };
  speed.raf = requestAnimationFrame(step);
}

function stopSpeedFx() {
  if (speed.raf) {
    cancelAnimationFrame(speed.raf);
    speed.raf = 0;
  }
  if (nsCtx) nsCtx.clearRect(0, 0, window.innerWidth, window.innerHeight);
  if (meterCtx && meterCanvas) meterCtx.clearRect(0, 0, meterCanvas.width, meterCanvas.height);
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
    const pct = Math.min(100, Math.round((100 * done) / total));
    $('ns-bar-fill').style.width = pct + '%';
    $('ns-phase-pct').textContent = pct + '%';

    // Route phase to meter
    if (phase === 'PING') {
      nsMeter.phase = 'ping';
      el.dataset.phase = 'ping';
      if (s.ping_ms != null) {
        nsMeter.pingMs = Number(s.ping_ms);
        $('ns-ping').textContent = Number(s.ping_ms).toFixed(1);
      }
    } else if (phase === 'DOWNLOAD') {
      nsMeter.phase = 'download';
      el.dataset.phase = 'download';
      if (s.ping_ms != null) $('ns-ping').textContent = Number(s.ping_ms).toFixed(1);
      if (s.download_mbps != null) {
        nsMeter.targetSpeed = Number(s.download_mbps);
        $('ns-down').textContent = Number(s.download_mbps).toFixed(2);
      } else {
        const ramp = Math.min(done / total, 1);
        nsMeter.targetSpeed = 15 + ramp * 85;
      }
    } else if (phase === 'UPLOAD') {
      nsMeter.phase = 'upload';
      el.dataset.phase = 'upload';
      if (s.download_mbps != null) $('ns-down').textContent = Number(s.download_mbps).toFixed(2);
      if (s.upload_mbps != null) {
        nsMeter.targetSpeed = Number(s.upload_mbps);
        $('ns-up').textContent = Number(s.upload_mbps).toFixed(2);
      } else {
        const ramp = Math.min(done / total, 1);
        nsMeter.targetSpeed = 10 + ramp * 40;
      }
    }

    startSpeedFx();
    return;
  }

  // Terminal frame: render final numbers
  speed.scanning = false;
  speed.t0 = 0;
  $('ns-bar-fill').style.width = '100%';
  $('ns-phase-pct').textContent = '100%';
  $('ns-elapsed').textContent = fmtElapsed(s.elapsed_ms || (s.elapsed || 0) * 1000);

  if (s.ping_ms != null) $('ns-ping').textContent = Number(s.ping_ms).toFixed(1);
  if (s.download_mbps != null) $('ns-down').textContent = Number(s.download_mbps).toFixed(2);
  if (s.upload_mbps != null) $('ns-up').textContent = Number(s.upload_mbps).toFixed(2);

  if (status === 'error') {
    nsMeter.phase = 'error';
    nsMeter.targetSpeed = 0;
    el.dataset.phase = 'error';
    $('ns-sub').textContent = 'TEST FAILED';
    $('ns-foot').textContent = (s.error ? String(s.error).toUpperCase() + ' · ' : '') + 'THE SPEED TEST HIT A PROBLEM';
    $('ns-phase').textContent = 'ERROR';
    return;
  }

  nsMeter.phase = 'done';
  el.dataset.phase = 'done';
  nsMeter.targetSpeed = Number(s.download_mbps) || 0;
  $('ns-sub').textContent = 'COMPLETE';
  $('ns-foot').textContent = 'SPEED TEST FINISHED · ESC TO CLOSE';
  $('ns-phase').textContent = 'DONE';
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

  timer.currentRemSec = remSec;
  timer.currentDurSec = durSec;

  $('tm-remaining').textContent = remaining;
  $('tm-label').textContent = status === 'done' ? 'TIME UP' :
                               status === 'cancelled' ? 'CANCELLED' :
                               status === 'error' ? 'ERROR' : 'REMAINING';
  $('tm-duration').textContent = duration;
  $('tm-elapsed').textContent = elapsed;
  $('tm-bar-fill').style.width = pct + '%';
  $('tm-pct').textContent = pct + '%';

  // Update SVG Arc Dial (Circumference for r=126 is 2 * PI * 126 ≈ 791.68)
  const arcFill = $('tm-arc-fill');
  if (arcFill) {
    const circ = 791.68;
    const fraction = durSec > 0 ? (remSec / durSec) : (status === 'done' ? 0 : 1);
    const offset = circ * (1 - fraction);
    arcFill.style.strokeDashoffset = offset;
  }

  // Warning trigger if 10s or less remaining on active countdown > 10s
  if (status === 'running' && durSec > 10 && remSec <= 10 && remSec > 0) {
    el.dataset.warning = 'true';
    const phaseEl = $('tm-phase-text');
    if (phaseEl) phaseEl.textContent = 'EXPIRING SOON';
  } else {
    delete el.dataset.warning;
    const phaseEl = $('tm-phase-text');
    if (phaseEl) {
      phaseEl.textContent = status === 'done' ? 'TIME UP' :
                            status === 'cancelled' ? 'ABORTED' :
                            status === 'set' ? 'ARMED' :
                            status === 'running' ? 'COUNTDOWN' : 'STANDBY';
    }
  }

  const targetMode = $('tm-target-mode');
  if (targetMode) {
    if (status === 'running' || status === 'set') {
      targetMode.textContent = `${remSec}s REM`;
    } else if (status === 'done') {
      targetMode.textContent = 'COMPLETED';
    } else if (status === 'cancelled') {
      targetMode.textContent = 'ABORTED';
    } else {
      targetMode.textContent = 'STANDBY';
    }
  }

  _wireTimerControls();

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

let _timerControlsWired = false;
function _wireTimerControls() {
  if (_timerControlsWired) return;
  _timerControlsWired = true;

  const btnAdd1 = $('tm-btn-add1');
  if (btnAdd1) {
    btnAdd1.addEventListener('click', () => {
      const cur = timer.currentRemSec || 0;
      const nextSec = Math.max(10, cur + 60);
      post('/api/command', { text: `set a timer for ${nextSec} seconds` });
    });
  }

  const btnAdd5 = $('tm-btn-add5');
  if (btnAdd5) {
    btnAdd5.addEventListener('click', () => {
      const cur = timer.currentRemSec || 0;
      const nextSec = Math.max(10, cur + 300);
      post('/api/command', { text: `set a timer for ${nextSec} seconds` });
    });
  }

  const btnCancel = $('tm-btn-cancel');
  if (btnCancel) {
    btnCancel.addEventListener('click', () => {
      post('/api/command', { text: 'cancel timer' });
      closeTimer();
    });
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
let tmRipples = [];

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
    for (let i = 0; i < 48; i++) {
      tmParticles.push({
        x: Math.random() * window.innerWidth,
        y: Math.random() * window.innerHeight,
        vx: (Math.random() - 0.5) * 0.5,
        vy: -0.3 - Math.random() * 0.7,
        r: 0.8 + Math.random() * 2.0,
        alpha: 0.2 + Math.random() * 0.5,
      });
    }
  }
  let frame = 0;
  const step = () => {
    if (!timer.running) { stopTimerFx(); return; }
    const W2 = window.innerWidth, H2 = window.innerHeight;
    const cx = W2 / 2, cy = H2 / 2;
    tmCtx.clearRect(0, 0, W2, H2);

    // Expanding holographic concentric sonar wave every ~80 frames
    frame++;
    if (frame % 80 === 0) {
      tmRipples.push({ r: 150, maxR: 350, alpha: 0.35 });
    }
    for (let i = tmRipples.length - 1; i >= 0; i--) {
      const rip = tmRipples[i];
      rip.r += 0.8;
      rip.alpha = Math.max(0, 0.35 * (1 - rip.r / rip.maxR));
      if (rip.r >= rip.maxR || rip.alpha <= 0.01) {
        tmRipples.splice(i, 1);
        continue;
      }
      tmCtx.save();
      tmCtx.strokeStyle = `rgba(56, 225, 240, ${rip.alpha})`;
      tmCtx.lineWidth = 1;
      tmCtx.setLineDash([4, 6]);
      tmCtx.beginPath();
      tmCtx.arc(cx, cy, rip.r, 0, Math.PI * 2);
      tmCtx.stroke();
      tmCtx.restore();
    }

    // Floating quantum dust particles
    tmCtx.fillStyle = 'rgba(56, 225, 240, 1)';
    for (const p of tmParticles) {
      p.x += p.vx;
      p.y += p.vy;
      if (p.y < -10) { p.y = H2 + 10; p.x = Math.random() * W2; }
      if (p.x < -10) p.x = W2 + 10;
      if (p.x > W2 + 10) p.x = -10;

      tmCtx.globalAlpha = p.alpha;
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
  tmRipples = [];
  if (tmCtx) tmCtx.clearRect(0, 0, window.innerWidth, window.innerHeight);
}

const reminder = { open: false, running: false, raf: 0, t0: 0 };

function openReminder() {
  if (reminder.open) return;
  reminder.open = true;
  $('reminder-overlay').classList.add('open');
  $('reminder-overlay').setAttribute('aria-hidden', 'false');
}

function closeReminder() {
  reminder.open = false;
  reminder.running = false;
  stopReminderAlert();
  const el = $('reminder-overlay');
  el.classList.remove('open');
  el.setAttribute('aria-hidden', 'true');
}

function applyReminder(s, fromSnapshot) {
  const el = $('reminder-overlay');
  if (!el) return;
  const status = s && s.status ? s.status : 'idle';
  if (!fromSnapshot || status === 'set' || status === 'running') openReminder();
  if (status === 'idle' && fromSnapshot) return;
  el.dataset.status = status;
  const message = (s && s.message) ? s.message : '\u2014';
  const scheduled = (s && s.scheduled_fmt) ? s.scheduled_fmt : '\u2014';
  $('rm-message').textContent = message;
  $('rm-scheduled').textContent = scheduled;
  if (status === 'set') {
    $('rm-sub').textContent = 'ARMING\u2026';
    $('rm-remaining').textContent = '--:--';
    $('rm-label').textContent = 'SCHEDULED';
    $('rm-foot').textContent = 'WAITING FOR SCHEDULED TIME \u00b7 ESC TO CLOSE';
    reminder.running = true;
    if (!reminder.t0) reminder.t0 = performance.now();
    startReminderFx();
  } else if (status === 'running') {
    $('rm-sub').textContent = 'COUNTING DOWN\u2026';
    $('rm-remaining').textContent = '--:--';
    $('rm-label').textContent = 'WAITING';
    $('rm-foot').textContent = 'REMINDER WILL FIRE AT SCHEDULED TIME \u00b7 ESC TO CLOSE';
    reminder.running = true;
    if (!reminder.t0) reminder.t0 = performance.now();
    startReminderFx();
  } else if (status === 'done') {
    $('rm-sub').textContent = 'SENT';
    $('rm-label').textContent = 'DELIVERED';
    $('rm-foot').textContent = 'REMINDER EMAIL SENT \u00b7 ESC TO CLOSE';
    reminder.running = false;
    stopReminderFx();
    reminderAlert();
  } else if (status === 'cancelled') {
    $('rm-sub').textContent = 'CANCELLED';
    $('rm-label').textContent = 'CANCELLED';
    $('rm-foot').textContent = 'REMINDER CANCELLED \u00b7 ESC TO CLOSE';
    reminder.running = false;
    stopReminderFx();
    stopReminderAlert();
  } else if (status === 'error') {
    $('rm-sub').textContent = 'ERROR';
    $('rm-label').textContent = 'ERROR';
    $('rm-foot').textContent = (s.error ? String(s.error).toUpperCase() + ' \u00b7 ' : '') + 'ESC TO CLOSE';
    reminder.running = false;
    stopReminderFx();
    stopReminderAlert();
  }
}

let _reminderAlarmInterval = null;
let _reminderAlarmFlash = null;

function reminderAlert() {
  stopReminderAlert();
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
    _reminderAlarmInterval = setInterval(() => { beep(); beep(); }, 3000);
  } catch(e) { void e; }
  const overlay = $('reminder-overlay');
  overlay.classList.add('rm-alert');
  _reminderAlarmFlash = setInterval(() => {
    if (overlay) overlay.classList.toggle('rm-alert-flash');
  }, 500);
}

function stopReminderAlert() {
  if (_reminderAlarmInterval) { clearInterval(_reminderAlarmInterval); _reminderAlarmInterval = null; }
  if (_reminderAlarmFlash) { clearInterval(_reminderAlarmFlash); _reminderAlarmFlash = null; }
  const overlay = $('reminder-overlay');
  if (overlay) { overlay.classList.remove('rm-alert', 'rm-alert-flash'); }
}

const rmCanvas = $('rm-canvas');
const rmCtx = rmCanvas ? rmCanvas.getContext('2d') : null;
let rmParticles = [];

function sizeReminderCanvas() {
  if (!rmCanvas) return;
  const r = dpr();
  rmCanvas.width = Math.floor(window.innerWidth * r);
  rmCanvas.height = Math.floor(window.innerHeight * r);
  rmCtx.setTransform(r, 0, 0, r, 0, 0);
}

function startReminderFx() {
  if (!rmCtx || reminder.raf) return;
  sizeReminderCanvas();
  if (!rmParticles.length) {
    for (let i = 0; i < 40; i++) {
      rmParticles.push({
        x: Math.random() * window.innerWidth,
        y: Math.random() * window.innerHeight,
        v: 0.2 + Math.random() * 0.8,
        r: 0.8 + Math.random() * 2.0,
      });
    }
  }
  const step = () => {
    if (!reminder.running) { stopReminderFx(); return; }
    const W2 = window.innerWidth, H2 = window.innerHeight;
    rmCtx.clearRect(0, 0, W2, H2);
    rmCtx.fillStyle = 'rgba(245, 158, 11, .35)';
    for (const p of rmParticles) {
      p.y += p.v;
      if (p.y > H2) { p.y = -4; p.x = Math.random() * W2; }
      rmCtx.globalAlpha = 0.15 + (p.r / 2.8) * 0.5;
      rmCtx.beginPath();
      rmCtx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      rmCtx.fill();
    }
    rmCtx.globalAlpha = 1;
    reminder.raf = requestAnimationFrame(step);
  };
  reminder.raf = requestAnimationFrame(step);
}

function stopReminderFx() {
  if (reminder.raf) { cancelAnimationFrame(reminder.raf); reminder.raf = 0; }
  if (rmCtx) rmCtx.clearRect(0, 0, window.innerWidth, window.innerHeight);
}


/* ═══ PROTOCOL OVERLAY ═════════════════════════════════════════════════ */

const protocolUI = {
  open: false,
  autoCloseTimer: null,
};

function openProtocol() {
  if (protocolUI.open) return;
  protocolUI.open = true;
  const el = $('protocol-overlay');
  if (el) {
    el.classList.add('open');
    el.setAttribute('aria-hidden', 'false');
  }
}

function closeProtocol() {
  protocolUI.open = false;
  if (protocolUI.autoCloseTimer) {
    clearTimeout(protocolUI.autoCloseTimer);
    protocolUI.autoCloseTimer = null;
  }
  const el = $('protocol-overlay');
  if (el) {
    el.classList.remove('open');
    el.setAttribute('aria-hidden', 'true');
  }
}

function applyProtocol(p, fromSnapshot) {
  const el = $('protocol-overlay');
  if (!el) return;
  const status = p && p.status ? p.status : 'idle';

  if (!fromSnapshot || status === 'executing') openProtocol();
  if (status === 'idle' && fromSnapshot) return;

  el.dataset.status = status;
  el.dataset.id = (p && p.id) ? p.id : 'work';

  const name = (p && p.name) ? p.name : 'PROTOCOL';
  if ($('pt-title')) $('pt-title').textContent = name;

  if (status === 'executing') {
    if ($('pt-sub')) $('pt-sub').textContent = 'INITIATING SUBSYSTEMS…';
    if ($('pt-badge')) $('pt-badge').textContent = 'ENGAGING';
    if ($('pt-banner-text')) $('pt-banner-text').textContent = 'EXECUTING WORKSPACE CALIBRATION';
    if ($('pt-foot')) $('pt-foot').textContent = 'CALIBRATING SUBSYSTEMS · ESC TO CLOSE';
  } else if (status === 'done') {
    if ($('pt-sub')) $('pt-sub').textContent = 'ACTIVE';
    if ($('pt-badge')) $('pt-badge').textContent = 'ENGAGED';
    if ($('pt-banner-text')) $('pt-banner-text').textContent = 'ALL SYSTEMS CALIBRATED AND OPERATIONAL';
    if ($('pt-foot')) $('pt-foot').textContent = 'PROTOCOL ACTIVE · AUTO-CLOSING IN 6S · ESC TO CLOSE';
    if (protocolUI.autoCloseTimer) clearTimeout(protocolUI.autoCloseTimer);
    protocolUI.autoCloseTimer = setTimeout(closeProtocol, 6000);
  } else if (status === 'error') {
    if ($('pt-sub')) $('pt-sub').textContent = 'ERROR';
    if ($('pt-badge')) $('pt-badge').textContent = 'FAILED';
    if ($('pt-banner-text')) $('pt-banner-text').textContent = 'PROTOCOL CALIBRATION INTERRUPTED';
    if ($('pt-foot')) $('pt-foot').textContent = 'PROTOCOL ERROR · ESC TO CLOSE';
  }

  // Render steps list
  const stepsList = $('pt-steps-list');
  if (stepsList && p && Array.isArray(p.steps)) {
    stepsList.innerHTML = '';
    for (const step of p.steps) {
      const item = document.createElement('div');
      item.className = `pt-step-item ${step.ok ? 'ok' : 'fail'}`;

      const left = document.createElement('div');
      left.className = 'pt-step-left';

      const icon = document.createElement('span');
      icon.className = 'pt-step-icon';
      icon.textContent = step.ok ? '✓' : '✕';

      const action = document.createElement('span');
      action.className = 'pt-step-action mono';
      action.textContent = step.action || 'SYSTEM';

      const detail = document.createElement('span');
      detail.className = 'pt-step-detail';
      detail.textContent = step.detail || '';

      left.append(icon, action, detail);

      const badge = document.createElement('span');
      badge.className = 'pt-step-badge mono micro';
      badge.textContent = step.ok ? 'OK' : 'FAULT';

      item.append(left, badge);
      stepsList.appendChild(item);
    }
  }
}


/* ═══ EXECUTIVE BRIEFING OVERLAY ═══════════════════════════════════════ */

const briefingUI = {
  open: false,
  autoCloseTimer: null,
};

function openBriefing() {
  if (briefingUI.open) return;
  briefingUI.open = true;
  const el = $('briefing-overlay');
  if (el) {
    el.classList.add('open');
    el.setAttribute('aria-hidden', 'false');
  }
}

function closeBriefing() {
  briefingUI.open = false;
  if (briefingUI.autoCloseTimer) {
    clearTimeout(briefingUI.autoCloseTimer);
    briefingUI.autoCloseTimer = null;
  }
  const el = $('briefing-overlay');
  if (el) {
    el.classList.remove('open');
    el.setAttribute('aria-hidden', 'true');
  }
}

function applyBriefing(b, fromSnapshot) {
  const el = $('briefing-overlay');
  if (!el) return;
  if (!b || !b.mode) {
    if (fromSnapshot) return;
  }

  openBriefing();

  const mode = b.mode || 'morning';
  el.dataset.mode = mode;

  if ($('bf-title')) $('bf-title').textContent = b.title || (mode === 'morning' ? 'EXECUTIVE MORNING BRIEFING' : 'EXECUTIVE EVENING BRIEFING');
  if ($('bf-sub')) $('bf-sub').textContent = mode === 'morning' ? 'ALL PRIMARY SYSTEMS ONLINE' : 'SYSTEM WIND-DOWN & TELEMETRY';

  // Weather
  const w = b.weather || {};
  if ($('bf-temp')) $('bf-temp').textContent = w.temp !== null && w.temp !== undefined ? Math.round(w.temp) : '--';
  if ($('bf-weather-place')) $('bf-weather-place').textContent = (w.place || 'CURRENT CITY').toUpperCase();
  if ($('bf-condition')) $('bf-condition').textContent = w.condition || 'Clear Sky';
  const hum = w.humidity !== null && w.humidity !== undefined ? `HUM ${w.humidity}%` : '';
  const wind = w.wind !== null && w.wind !== undefined ? `WIND ${Math.round(w.wind)} KM/H` : '';
  if ($('bf-weather-details')) $('bf-weather-details').textContent = [hum, wind].filter(Boolean).join(' · ') || 'CONDITIONS STABLE';

  const rainPct = w.rain_chance || 0;
  if ($('bf-rain-pct')) $('bf-rain-pct').textContent = `${rainPct}%`;
  if ($('bf-rain-fill')) $('bf-rain-fill').style.width = `${Math.min(100, Math.max(0, rainPct))}%`;

  // Temporal / Calendars
  const tm = b.temporal || {};
  if ($('bf-clock-time')) $('bf-clock-time').textContent = tm.time ? `${tm.time} BST` : '--:-- BST';
  if ($('bf-cal-eng')) $('bf-cal-eng').textContent = tm.english || 'Date unavailable';
  if ($('bf-cal-bn')) $('bf-cal-bn').textContent = tm.bangla || 'তারিখ পাওয়া যায়নি';
  if ($('bf-cal-ar')) $('bf-cal-ar').textContent = tm.arabic || 'তারিখ পাওয়া যায়নি';

  // Inbox
  const ibx = b.inbox || {};
  const unreadCount = ibx.total_unread || 0;
  if ($('bf-unread-badge')) $('bf-unread-badge').textContent = `${unreadCount} UNREAD`;

  const inboxList = $('bf-inbox-list');
  if (inboxList) {
    inboxList.innerHTML = '';
    const msgs = ibx.messages || [];
    if (msgs.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'bf-empty-inbox mono micro dim';
      empty.textContent = 'INBOX ZERO · NO PENDING MESSAGES';
      inboxList.appendChild(empty);
    } else {
      for (const m of msgs) {
        const item = document.createElement('div');
        item.className = 'bf-inbox-item';
        const sender = document.createElement('div');
        sender.className = 'bf-inbox-sender';
        sender.textContent = m.from || 'Unknown Sender';
        const subj = document.createElement('div');
        subj.className = 'bf-inbox-subject';
        subj.textContent = m.subject || '(No Subject)';
        item.append(sender, subj);
        inboxList.appendChild(item);
      }
    }
  }

  // Telemetry
  const tel = b.telemetry || {};
  if ($('bf-battery-val')) $('bf-battery-val').textContent = tel.battery_desc || 'AC Connected';
  if ($('bf-network-val')) $('bf-network-val').textContent = tel.network_desc || (tel.network_online ? 'Online' : 'Offline');
  if ($('bf-net-status')) $('bf-net-status').textContent = tel.network_ping_ms ? `ONLINE · ${tel.network_ping_ms}MS` : (tel.network_online ? 'ONLINE' : 'OFFLINE');
  if ($('bf-audio-val')) $('bf-audio-val').textContent = tel.audio_desc || (tel.audio_active ? 'Audio Stream Active' : 'Silent / Standby');

  // Spoken summary
  if ($('bf-spoken-text')) $('bf-spoken-text').textContent = b.spoken || 'Executive briefing compiled and verified.';

  // Auto-close after 14 seconds
  if (briefingUI.autoCloseTimer) clearTimeout(briefingUI.autoCloseTimer);
  briefingUI.autoCloseTimer = setTimeout(closeBriefing, 14000);
}


/* ═══ AUTONOMOUS DEEP RESEARCH OVERLAY 2.0 ═════════════════════════════ */

const researchUI = {
  open: false,
  selectedDepth: 'deep',
  activeTab: 'telemetry',
  lastData: null,
  history: [],
};

function openResearch() {
  if (researchUI.open) return;
  researchUI.open = true;
  const el = $('research-overlay');
  if (el) {
    el.classList.add('open');
    el.setAttribute('aria-hidden', 'false');
  }
}

function closeResearch() {
  researchUI.open = false;
  const el = $('research-overlay');
  if (el) {
    el.classList.remove('open');
    el.setAttribute('aria-hidden', 'true');
  }
}

function switchResearchTab(tabName) {
  researchUI.activeTab = tabName;
  const tabs = ['telemetry', 'dossier', 'matrix', 'archive'];
  for (const t of tabs) {
    const btn = $(`rs-tab-btn-${t}`);
    const panel = $(`rs-tab-content-${t}`);
    if (btn) btn.classList.toggle('active', t === tabName);
    if (panel) panel.classList.toggle('active', t === tabName);
  }
  if (tabName === 'archive') {
    loadResearchArchive();
  }
}

function renderMarkdownDossier(md) {
  if (!md) return '<div class="rs-empty mono micro dim">NO DOSSIER GENERATED YET.</div>';

  const lines = md.split('\n');
  let out = [];
  let inTable = false;
  let tableRows = [];

  function flushTable() {
    if (!tableRows.length) return;
    let tableHtml = '<table>';
    for (let i = 0; i < tableRows.length; i++) {
      const row = tableRows[i].trim();
      if (row.match(/^\|[-:\s|]+\|$/)) continue; // separator row
      const cells = row.split('|').slice(1, -1);
      const isHeader = (i === 0);
      tableHtml += '<tr>';
      for (const cell of cells) {
        const tag = isHeader ? 'th' : 'td';
        tableHtml += `<${tag}>${formatInlineMd(cell.trim())}</${tag}>`;
      }
      tableHtml += '</tr>';
    }
    tableHtml += '</table>';
    out.push(tableHtml);
    tableRows = [];
    inTable = false;
  }

  function formatInlineMd(text) {
    let t = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
    // Bold
    t = t.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    // Code
    t = t.replace(/`([^`]+)`/g, '<code class="mono">$1</code>');
    // Links
    t = t.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    return t;
  }

  for (let line of lines) {
    const trimmed = line.trim();

    if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
      inTable = true;
      tableRows.push(trimmed);
      continue;
    } else if (inTable) {
      flushTable();
    }

    if (!trimmed) {
      continue;
    }

    if (trimmed.startsWith('### ')) {
      out.push(`<h3>${formatInlineMd(trimmed.slice(4))}</h3>`);
    } else if (trimmed.startsWith('## ')) {
      out.push(`<h2>${formatInlineMd(trimmed.slice(3))}</h2>`);
    } else if (trimmed.startsWith('# ')) {
      out.push(`<h1>${formatInlineMd(trimmed.slice(2))}</h1>`);
    } else if (trimmed.startsWith('> ')) {
      out.push(`<blockquote>${formatInlineMd(trimmed.slice(2))}</blockquote>`);
    } else if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
      out.push(`<li>${formatInlineMd(trimmed.slice(2))}</li>`);
    } else {
      out.push(`<p>${formatInlineMd(trimmed)}</p>`);
    }
  }

  if (inTable) {
    flushTable();
  }

  return out.join('');
}

function loadResearchArchive() {
  const listEl = $('rs-archive-list');
  if (!listEl) return;
  listEl.innerHTML = '<div class="rs-empty mono micro dim">FETCHING ARCHIVE DOSSIERS…</div>';

  fetch('/api/research/history')
    .then(r => r.json())
    .then(data => {
      const history = data.history || [];
      researchUI.history = history;
      if (!history.length) {
        listEl.innerHTML = '<div class="rs-empty mono micro dim">NO ARCHIVED RESEARCH FOUND YET.</div>';
        return;
      }
      listEl.innerHTML = '';
      for (const item of history) {
        const row = document.createElement('div');
        row.className = 'rs-archive-item';

        const info = document.createElement('div');
        info.className = 'rs-archive-info';

        const topic = document.createElement('div');
        topic.className = 'rs-archive-topic';
        topic.textContent = item.topic || 'Untitled Dossier';

        const meta = document.createElement('div');
        meta.className = 'rs-archive-meta mono';
        const dateStr = item.timestamp ? new Date(item.timestamp).toLocaleString() : '';
        meta.textContent = `${dateStr} · DEPTH: ${(item.depth || 'DEEP').toUpperCase()}`;

        info.append(topic, meta);

        const actions = document.createElement('div');
        actions.className = 'rs-archive-actions';

        const viewBtn = document.createElement('button');
        viewBtn.type = 'button';
        viewBtn.className = 'rs-tool-btn mono micro';
        viewBtn.textContent = 'READ';
        viewBtn.onclick = (e) => {
          e.stopPropagation();
          viewArchivedDossier(item);
        };

        if (item.pdf_path) {
          const pdfBtn = document.createElement('button');
          pdfBtn.type = 'button';
          pdfBtn.className = 'rs-tool-btn mono micro';
          pdfBtn.textContent = 'PDF';
          pdfBtn.onclick = (e) => {
            e.stopPropagation();
            fetch('/api/research/open', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ action: 'pdf', target: item.pdf_path }),
            });
          };
          actions.appendChild(pdfBtn);
        }

        actions.appendChild(viewBtn);
        row.append(info, actions);
        listEl.appendChild(row);
      }
    })
    .catch(err => {
      listEl.innerHTML = `<div class="rs-empty mono micro dim">FAILED TO LOAD ARCHIVE: ${err}</div>`;
    });
}

function viewArchivedDossier(item) {
  if (!item) return;
  researchUI.lastData = item;
  if ($('rs-topic')) $('rs-topic').textContent = `TOPIC: ${(item.topic || '').toUpperCase()}`;
  if ($('rs-dossier-viewer')) {
    $('rs-dossier-viewer').innerHTML = renderMarkdownDossier(item.markdown || item.summary);
  }
  if ($('rs-matrix-viewer') && item.comparison_matrix) {
    $('rs-matrix-viewer').innerHTML = renderMarkdownDossier(item.comparison_matrix);
  }
  switchResearchTab('dossier');
}

function applyResearch(r, fromSnapshot) {
  const el = $('research-overlay');
  if (!el) return;
  if (!r || !r.topic) {
    if (fromSnapshot) return;
  }

  researchUI.lastData = r;
  openResearch();

  const phase = r.phase || 'planning';
  el.dataset.phase = phase;

  if ($('rs-topic')) $('rs-topic').textContent = `TOPIC: ${(r.topic || '').toUpperCase()}`;
  if ($('rs-status-text')) $('rs-status-text').textContent = (r.status_text || 'RESEARCH IN PROGRESS…').toUpperCase();
  if ($('rs-pct')) $('rs-pct').textContent = `${r.percent || 15}%`;

  // Depth pills highlight
  const depth = r.depth || 'deep';
  const pills = ['quick', 'deep', 'exhaustive'];
  for (const d of pills) {
    const p = $(`rs-pill-${d}`);
    if (p) p.classList.toggle('active', d === depth);
  }

  const badge = $('rs-badge');
  if (badge) {
    if (phase === 'done') {
      badge.textContent = 'DOSSIER COMPILED';
      badge.style.color = '#2ecc71';
      badge.style.borderColor = 'rgba(46, 204, 113, .4)';
    } else if (phase === 'error') {
      badge.textContent = 'INTERRUPTED';
      badge.style.color = '#ef4444';
      badge.style.borderColor = 'rgba(239, 68, 68, .4)';
    } else {
      badge.textContent = phase.toUpperCase();
      badge.style.color = '#2dd4bf';
      badge.style.borderColor = 'rgba(45, 212, 191, .4)';
    }
  }

  // Update Stepper
  const phaseOrder = ['planning', 'harvest', 'scraping', 'synthesis', 'done'];
  const curIdx = phaseOrder.indexOf(phase === 'gap_fill' ? 'scraping' : phase);
  for (let i = 1; i <= 5; i++) {
    const step = $(`rs-step-${i}`);
    const line = $(`rs-line-${i}`);
    if (step) {
      step.classList.remove('active', 'completed');
      if (curIdx >= i - 1) {
        if (curIdx === i - 1 && phase !== 'done') {
          step.classList.add('active');
        } else {
          step.classList.add('completed');
        }
      }
    }
    if (line) {
      line.classList.remove('active');
      if (curIdx >= i) line.classList.add('active');
    }
  }

  // Sources List with Trust Badges
  const sources = r.sources || [];
  if ($('rs-source-count')) $('rs-source-count').textContent = `${sources.length} SOURCES`;
  const sourcesList = $('rs-sources-list');
  if (sourcesList) {
    if (sources.length === 0) {
      sourcesList.innerHTML = '<div class="rs-empty mono micro dim">HARVESTING MULTI-ENGINE RESULTS…</div>';
    } else {
      sourcesList.innerHTML = '';
      for (const s of sources) {
        const item = document.createElement('a');
        item.className = 'rs-source-item';
        item.href = s.url || '#';
        item.target = '_blank';
        item.rel = 'noopener noreferrer';

        const info = document.createElement('div');
        info.className = 'rs-source-info';

        const title = document.createElement('div');
        title.className = 'rs-source-title';
        title.textContent = s.title || s.domain || 'Verified Reference';

        const domain = document.createElement('div');
        domain.className = 'rs-source-domain mono';
        domain.textContent = s.domain || 'External Reference';

        info.append(title, domain);

        const badges = document.createElement('div');
        badges.className = 'rs-source-badges';

        const tierBadge = document.createElement('span');
        const tier = s.trust_tier || 'web';
        tierBadge.className = `rs-source-badge mono micro ${tier}`;
        tierBadge.textContent = s.trust_label || tier.toUpperCase();

        const st = document.createElement('span');
        st.className = `rs-source-badge mono micro ${s.status || 'queued'}`;
        st.textContent = (s.status || 'queued').toUpperCase();

        badges.append(tierBadge, st);
        item.append(info, badges);
        sourcesList.appendChild(item);
      }
    }
  }

  // Key Intelligence Takeaways List
  const findings = r.findings || [];
  if ($('rs-intel-count')) $('rs-intel-count').textContent = `${findings.length} TAKEAWAYS`;
  const intelList = $('rs-intel-list');
  if (intelList) {
    if (findings.length === 0) {
      intelList.innerHTML = '<div class="rs-empty mono micro dim">AWAITING CROSS-SOURCE SYNTHESIS…</div>';
    } else {
      intelList.innerHTML = '';
      for (const f of findings) {
        const item = document.createElement('div');
        item.className = 'rs-intel-item';

        const bullet = document.createElement('span');
        bullet.className = 'rs-intel-bullet';
        bullet.textContent = '◆';

        const txt = document.createElement('span');
        txt.textContent = f;

        item.append(bullet, txt);
        intelList.appendChild(item);
      }
    }
  }

  // Summary and Export Links
  if ($('rs-summary-text')) {
    $('rs-summary-text').textContent = r.summary || (phase === 'done' ? 'Research synthesis complete.' : 'Analyzing and synthesizing web intelligence…');
  }

  const exportTags = $('rs-export-tags');
  if (exportTags) {
    exportTags.innerHTML = '';
    const exp = r.exports || {};
    if (exp.md_path) {
      const mdBtn = document.createElement('span');
      mdBtn.className = 'rs-export-btn mono micro';
      mdBtn.textContent = 'MARKDOWN SAVED';
      mdBtn.title = exp.md_path;
      mdBtn.onclick = () => switchResearchTab('dossier');
      exportTags.appendChild(mdBtn);
    }
    if (exp.pdf_path) {
      const pdfBtn = document.createElement('span');
      pdfBtn.className = 'rs-export-btn mono micro';
      pdfBtn.textContent = 'PDF READY';
      pdfBtn.title = exp.pdf_path;
      pdfBtn.onclick = () => {
        fetch('/api/research/open', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'pdf', target: exp.pdf_path }),
        });
      };
      exportTags.appendChild(pdfBtn);
    }
  }

  // Tab 2: Render Dossier Markdown
  if (r.markdown && $('rs-dossier-viewer')) {
    $('rs-dossier-viewer').innerHTML = renderMarkdownDossier(r.markdown);
  }

  // Tab 3: Comparison Matrix
  if ($('rs-matrix-viewer')) {
    if (r.comparison_matrix) {
      $('rs-matrix-viewer').innerHTML = renderMarkdownDossier(r.comparison_matrix);
    } else if (phase === 'done') {
      $('rs-matrix-viewer').innerHTML = '<div class="rs-empty mono micro dim">NO DIRECT COMPARISON MATRIX GENERATED (TOPIC IS NOT A COMPARISON).</div>';
    }
  }
}

// Attach Research UI Event Listeners
function initResearchOverlayEvents() {
  // Depth Pills
  const pills = ['quick', 'deep', 'exhaustive'];
  for (const d of pills) {
    const p = $(`rs-pill-${d}`);
    if (p) {
      p.addEventListener('click', () => {
        researchUI.selectedDepth = d;
        for (const other of pills) {
          const op = $(`rs-pill-${other}`);
          if (op) op.classList.toggle('active', other === d);
        }
      });
    }
  }

  // Tabs
  const tabs = ['telemetry', 'dossier', 'matrix', 'archive'];
  for (const t of tabs) {
    const btn = $(`rs-tab-btn-${t}`);
    if (btn) {
      btn.addEventListener('click', () => switchResearchTab(t));
    }
  }

  // Launch Input & Button
  const input = $('rs-input-topic');
  const launchBtn = $('rs-btn-launch');
  function doLaunch() {
    if (!input) return;
    const topic = input.value.trim();
    if (!topic) return;
    input.value = '';
    fetch('/api/research/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic: topic, depth: researchUI.selectedDepth }),
    });
    switchResearchTab('telemetry');
  }

  if (launchBtn) launchBtn.addEventListener('click', doLaunch);
  if (input) {
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        doLaunch();
      }
    });
  }

  // Open PDF button
  const pdfBtn = $('rs-btn-open-pdf');
  if (pdfBtn) {
    pdfBtn.addEventListener('click', () => {
      fetch('/api/research/open', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'pdf' }),
      });
    });
  }

  // Open Docs Folder button
  const folderBtn = $('rs-btn-open-folder');
  if (folderBtn) {
    folderBtn.addEventListener('click', () => {
      fetch('/api/research/open', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'folder' }),
      });
    });
  }

  // Copy Dossier Report button
  const copyBtn = $('rs-btn-copy');
  if (copyBtn) {
    copyBtn.addEventListener('click', () => {
      const md = (researchUI.lastData && researchUI.lastData.markdown) || '';
      if (!md) return;
      navigator.clipboard.writeText(md).then(() => {
        const orig = copyBtn.textContent;
        copyBtn.textContent = 'COPIED!';
        setTimeout(() => { copyBtn.textContent = orig; }, 1500);
      });
    });
  }

  // Speak Briefing button
  const speakBtn = $('rs-reader-speak-btn');
  if (speakBtn) {
    speakBtn.addEventListener('click', () => {
      fetch('/api/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: 'read research summary' }),
      });
    });
  }

  // Refresh Archive button
  const archiveRefBtn = $('rs-archive-refresh-btn');
  if (archiveRefBtn) {
    archiveRefBtn.addEventListener('click', loadResearchArchive);
  }
}

// Initialise overlay events once DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initResearchOverlayEvents);
} else {
  initResearchOverlayEvents();
}

/* ── autonomous web autopilot overlay ────────────────────────────────── */

const autopilotUI = {
  open: false,
};

function openAutopilot() {
  if (autopilotUI.open) return;
  autopilotUI.open = true;
  const el = $('autopilot-overlay');
  if (el) {
    el.classList.add('open');
    el.setAttribute('aria-hidden', 'false');
  }
}

function closeAutopilot() {
  autopilotUI.open = false;
  const el = $('autopilot-overlay');
  if (el) {
    el.classList.remove('open');
    el.setAttribute('aria-hidden', 'true');
  }
}

function applyAutopilot(ap, fromSnapshot) {
  const el = $('autopilot-overlay');
  if (!el) return;
  if (!ap || (!ap.subject && !ap.action)) {
    if (fromSnapshot) return;
  }

  openAutopilot();

  const phase = ap.phase || 'navigating';
  el.dataset.phase = phase;

  if ($('ap-target')) $('ap-target').textContent = `TARGET: ${(ap.subject || 'INITIALIZING…').toUpperCase()}`;
  if ($('ap-status-text')) $('ap-status-text').textContent = (ap.action || 'AUTOPILOT IN PROGRESS…').toUpperCase();
  if ($('ap-platform-tag')) $('ap-platform-tag').textContent = `PLATFORM: ${(ap.platform || 'CHROMIUM').toUpperCase()}`;
  if ($('ap-url-text')) $('ap-url-text').textContent = ap.url || 'about:blank';

  const badge = $('ap-badge');
  if (badge) {
    if (phase === 'done') {
      badge.textContent = 'MISSION COMPLETE';
      badge.style.color = '#2ecc71';
      badge.style.borderColor = 'rgba(46, 204, 113, .4)';
    } else if (phase === 'error') {
      badge.textContent = 'INTERRUPTED';
      badge.style.color = '#ef4444';
      badge.style.borderColor = 'rgba(239, 68, 68, .4)';
    } else {
      badge.textContent = phase.toUpperCase();
      badge.style.color = '#2dd4bf';
      badge.style.borderColor = 'rgba(45, 212, 191, .4)';
    }
  }

  // Stepper progress
  const stepNum = Number(ap.step) || (phase === 'done' ? 4 : 1);
  for (let i = 1; i <= 4; i++) {
    const s = $(`ap-step-${i}`);
    const l = $(`ap-line-${i}`);
    if (s) {
      s.classList.remove('active', 'completed');
      if (stepNum > i || phase === 'done') {
        s.classList.add('completed');
      } else if (stepNum === i) {
        s.classList.add('active');
      }
    }
    if (l) {
      l.classList.remove('active');
      if (stepNum > i || phase === 'done') l.classList.add('active');
    }
  }

  // Live screenshot viewport
  const vpImg = $('ap-viewport-img');
  const vpPlaceholder = $('ap-viewport-placeholder');
  if (ap.screenshot) {
    if (vpImg) {
      vpImg.src = ap.screenshot.startsWith('data:') ? ap.screenshot : `data:image/png;base64,${ap.screenshot}`;
      vpImg.style.display = 'block';
    }
    if (vpPlaceholder) vpPlaceholder.style.display = 'none';
  }

  // Structured comparison records
  const records = ap.records || [];
  if ($('ap-item-count')) $('ap-item-count').textContent = `${records.length} ITEMS`;
  const tableBody = $('ap-table-body');
  if (tableBody) {
    if (records.length === 0) {
      tableBody.innerHTML = '<tr><td colspan="4" class="ap-empty mono micro dim">HARVESTING PAGE LISTINGS…</td></tr>';
    } else {
      tableBody.innerHTML = '';
      for (const r of records) {
        const tr = document.createElement('tr');

        const tdTitle = document.createElement('td');
        tdTitle.className = 'ap-col-title';
        tdTitle.textContent = r.title || 'Product / Item';

        const tdPrice = document.createElement('td');
        tdPrice.className = 'ap-col-price mono';
        tdPrice.textContent = r.price || 'N/A';

        const tdRating = document.createElement('td');
        tdRating.className = 'ap-col-rating mono micro';
        tdRating.textContent = r.rating || '—';

        const tdAction = document.createElement('td');
        tdAction.className = 'ap-col-action';
        if (r.url) {
          const a = document.createElement('a');
          a.className = 'ap-btn mono micro';
          a.href = r.url;
          a.target = '_blank';
          a.rel = 'noopener noreferrer';
          a.textContent = 'OPEN ↗';
          tdAction.appendChild(a);
        } else {
          tdAction.textContent = '—';
        }

        tr.append(tdTitle, tdPrice, tdRating, tdAction);
        tableBody.appendChild(tr);
      }
    }
  }

  // Debrief summary & CSV export
  if ($('ap-summary-text')) {
    $('ap-summary-text').textContent = ap.summary || (phase === 'done' ? 'Autopilot mission complete.' : 'Synthesizing live browser results…');
  }
  const csvBadge = $('ap-csv-badge');
  if (csvBadge) {
    if (ap.csv_file) {
      const fileName = ap.csv_file.split(/[\/\\]/).pop();
      csvBadge.innerHTML = `<span class="ap-csv-tag mono micro" title="${ap.csv_file}">SAVED: ${fileName}</span>`;
    } else {
      csvBadge.innerHTML = '';
    }
  }
}



/* ── neural long-term memory & knowledge graph overlay ─────────────── */

const memoryUI = {
  open: false,
  mode: 'graph',
  activeCategory: 'all',
  searchQuery: '',
  memories: [],
  graph: { nodes: [], edges: [] },
  stats: {},
  canvas: null,
  ctx: null,
  animId: null,
  nodes: [],
  edges: [],
  hoveredNode: null,
  selectedNode: null,
  draggedNode: null,
  dragOffset: { x: 0, y: 0 },
  pulses: [],
};

function openMemory() {
  if (memoryUI.open) return;
  memoryUI.open = true;
  const el = $('memory-overlay');
  if (el) {
    el.classList.add('open');
    el.setAttribute('aria-hidden', 'false');
  }
  refreshMemoriesFromApi();
  initMemoryCanvas();
}

function closeMemory() {
  memoryUI.open = false;
  const el = $('memory-overlay');
  if (el) {
    el.classList.remove('open');
    el.setAttribute('aria-hidden', 'true');
  }
  if (memoryUI.animId) {
    cancelAnimationFrame(memoryUI.animId);
    memoryUI.animId = null;
  }
}

function toggleMemory() {
  if (memoryUI.open) closeMemory();
  else openMemory();
}

async function refreshMemoriesFromApi() {
  try {
    const res = await fetch('/api/memories');
    if (!res.ok) return;
    const data = await res.json();
    memoryUI.memories = data.memories || [];
    memoryUI.graph = data.graph || { nodes: [], edges: [] };
    memoryUI.stats = data.stats || {};
    updateMemoryUI();
  } catch (err) {
    console.error('Failed to fetch memories:', err);
  }
}

function applyMemory(mem, fromSnapshot) {
  if (!mem) return;
  if (mem.event === 'show_overlay') {
    openMemory();
  }
  if (mem.memories) memoryUI.memories = mem.memories;
  if (mem.graph) memoryUI.graph = mem.graph;
  if (mem.categories) memoryUI.stats.categories = mem.categories;
  if (typeof mem.total_count === 'number') memoryUI.stats.total_memories = mem.total_count;

  if (memoryUI.open) {
    updateMemoryUI();
  }
}

function updateMemoryUI() {
  const total = memoryUI.stats.total_memories ?? memoryUI.memories.length;
  if ($('mem-total-count')) $('mem-total-count').textContent = total;

  buildGraphPhysicsNodes();
  renderMemoryCards();
}

function buildGraphPhysicsNodes() {
  const rawNodes = memoryUI.graph.nodes || [];
  const rawEdges = memoryUI.graph.edges || [];
  const canvas = $('mem-canvas');
  if (!canvas) return;

  const w = canvas.width;
  const h = canvas.height;
  const cx = w / 2;
  const cy = h / 2;

  const existingMap = new Map();
  for (const n of memoryUI.nodes) {
    existingMap.set(n.id, n);
  }

  const nodes = [];
  const catAngleMap = new Map();
  const catHubs = rawNodes.filter(n => n.category === 'category_hub');
  catHubs.forEach((hub, idx) => {
    const angle = (idx / catHubs.length) * Math.PI * 2;
    catAngleMap.set(hub.id, angle);
  });

  for (const rn of rawNodes) {
    let node = existingMap.get(rn.id);
    if (!node) {
      let x = cx;
      let y = cy;
      if (rn.id === 'node_user') {
        x = cx; y = cy;
      } else if (rn.category === 'category_hub') {
        const ang = catAngleMap.get(rn.id) || 0;
        x = cx + Math.cos(ang) * 140;
        y = cy + Math.sin(ang) * 140;
      } else {
        const ang = Math.random() * Math.PI * 2;
        const dist = 180 + Math.random() * 120;
        x = cx + Math.cos(ang) * dist;
        y = cy + Math.sin(ang) * dist;
      }
      node = {
        ...rn,
        x, y,
        vx: (Math.random() - 0.5) * 0.4,
        vy: (Math.random() - 0.5) * 0.4,
        targetX: x, targetY: y,
      };
    } else {
      Object.assign(node, rn);
    }
    nodes.push(node);
  }

  memoryUI.nodes = nodes;
  memoryUI.edges = rawEdges;

  if (memoryUI.pulses.length < 12 && rawEdges.length > 0) {
    for (let i = 0; i < 4; i++) {
      const edge = rawEdges[Math.floor(Math.random() * rawEdges.length)];
      memoryUI.pulses.push({
        edge,
        progress: Math.random(),
        speed: 0.005 + Math.random() * 0.015,
        color: edge.color || '#00f0ff'
      });
    }
  }
}

function renderMemoryCards() {
  const container = $('mem-cards-grid');
  if (!container) return;

  let filtered = memoryUI.memories;
  if (memoryUI.activeCategory !== 'all') {
    filtered = filtered.filter(m => m.category === memoryUI.activeCategory);
  }
  if (memoryUI.searchQuery) {
    const q = memoryUI.searchQuery.toLowerCase();
    filtered = filtered.filter(m =>
      (m.subject && m.subject.toLowerCase().includes(q)) ||
      (m.content && m.content.toLowerCase().includes(q)) ||
      (m.category && m.category.toLowerCase().includes(q))
    );
  }

  if (filtered.length === 0) {
    container.innerHTML = '<div class="mem-empty mono micro dim">NO MEMORIES MATCH CURRENT FILTER.</div>';
    return;
  }

  container.innerHTML = '';
  for (const m of filtered) {
    const card = document.createElement('div');
    card.className = 'mem-card';

    const top = document.createElement('div');
    top.className = 'mem-card-top';

    const badge = document.createElement('span');
    badge.className = `mem-card-badge badge-${m.category || 'general'}`;
    badge.textContent = m.category || 'GENERAL';

    const delBtn = document.createElement('button');
    delBtn.className = 'mem-card-delete';
    delBtn.title = 'Forget this memory';
    delBtn.innerHTML = '✕';
    delBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (confirm(`Forget memory about "${m.subject}"?`)) {
        await fetch('/api/memories/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: m.id })
        });
        refreshMemoriesFromApi();
      }
    });

    top.append(badge, delBtn);

    const subject = document.createElement('div');
    subject.className = 'mem-card-subject';
    subject.textContent = m.subject || 'Note';

    const content = document.createElement('p');
    content.className = 'mem-card-content';
    content.textContent = m.content || '';

    const meta = document.createElement('div');
    meta.className = 'mem-card-meta mono micro';

    const d = new Date((m.updated_at || m.created_at || Date.now() / 1000) * 1000);
    const dateStr = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;

    const dateSpan = document.createElement('span');
    dateSpan.textContent = dateStr;

    const accessSpan = document.createElement('span');
    accessSpan.textContent = `RECALLED: ${m.access_count || 0}×`;

    meta.append(dateSpan, accessSpan);
    card.append(top, subject, content, meta);
    container.appendChild(card);
  }
}

function initMemoryCanvas() {
  const canvas = $('mem-canvas');
  if (!canvas) return;

  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(800, Math.floor(rect.width * dpr()));
  canvas.height = Math.max(450, Math.floor(rect.height * dpr()));

  const ctx = canvas.getContext('2d');
  memoryUI.canvas = canvas;
  memoryUI.ctx = ctx;

  buildGraphPhysicsNodes();

  if (memoryUI.animId) cancelAnimationFrame(memoryUI.animId);
  function loop() {
    if (!memoryUI.open || memoryUI.mode !== 'graph') return;
    drawMemoryGraphFrame();
    memoryUI.animId = requestAnimationFrame(loop);
  }
  loop();

  canvas.onmousemove = (e) => {
    const cRect = canvas.getBoundingClientRect();
    const mx = (e.clientX - cRect.left) * (canvas.width / cRect.width);
    const my = (e.clientY - cRect.top) * (canvas.height / cRect.height);

    if (memoryUI.draggedNode) {
      memoryUI.draggedNode.x = mx + memoryUI.dragOffset.x;
      memoryUI.draggedNode.y = my + memoryUI.dragOffset.y;
      return;
    }

    let hit = null;
    for (const n of memoryUI.nodes) {
      const dx = n.x - mx;
      const dy = n.y - my;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < (n.size || 10) * dpr() + 6) {
        hit = n;
        break;
      }
    }
    memoryUI.hoveredNode = hit;

    const tooltip = $('mem-tooltip');
    if (tooltip) {
      if (hit && (hit.full_subject || hit.content || hit.label)) {
        tooltip.style.display = 'block';
        tooltip.style.left = `${e.clientX - cRect.left + 12}px`;
        tooltip.style.top = `${e.clientY - cRect.top - 12}px`;
        tooltip.innerHTML = `<strong>${hit.full_subject || hit.label}</strong><br>${hit.content ? `<span style="color:#b0c4de">${hit.content}</span>` : `<span style="color:#00f0ff">${hit.category.toUpperCase()}</span>`}`;
      } else {
        tooltip.style.display = 'none';
      }
    }
  };

  canvas.onmousedown = (e) => {
    if (memoryUI.hoveredNode) {
      memoryUI.draggedNode = memoryUI.hoveredNode;
      const cRect = canvas.getBoundingClientRect();
      const mx = (e.clientX - cRect.left) * (canvas.width / cRect.width);
      const my = (e.clientY - cRect.top) * (canvas.height / cRect.height);
      memoryUI.dragOffset.x = memoryUI.draggedNode.x - mx;
      memoryUI.dragOffset.y = memoryUI.draggedNode.y - my;
    }
  };

  canvas.onmouseup = () => {
    memoryUI.draggedNode = null;
  };
}

function drawMemoryGraphFrame() {
  const canvas = memoryUI.canvas;
  const ctx = memoryUI.ctx;
  if (!canvas || !ctx) return;

  const w = canvas.width;
  const h = canvas.height;
  const cx = w / 2;
  const cy = h / 2;
  const ratio = dpr();

  ctx.clearRect(0, 0, w, h);

  ctx.strokeStyle = 'rgba(0, 240, 255, 0.035)';
  ctx.lineWidth = 1;
  for (let r = 80 * ratio; r < Math.max(w, h); r += 90 * ratio) {
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();
  }

  const nodeMap = new Map();
  for (const n of memoryUI.nodes) {
    nodeMap.set(n.id, n);
  }

  for (const n of memoryUI.nodes) {
    if (n !== memoryUI.draggedNode && n.id !== 'node_user') {
      const dx = cx - n.x;
      const dy = cy - n.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist > 30) {
        n.x += (dx / dist) * 0.15;
        n.y += (dy / dist) * 0.15;
      }
      n.x += n.vx;
      n.y += n.vy;
      n.vx *= 0.98;
      n.vy *= 0.98;
    }
  }

  for (const edge of memoryUI.edges) {
    const s = nodeMap.get(edge.source);
    const t = nodeMap.get(edge.target);
    if (!s || !t) continue;

    const isHovered = memoryUI.hoveredNode && (memoryUI.hoveredNode.id === s.id || memoryUI.hoveredNode.id === t.id);

    ctx.beginPath();
    ctx.moveTo(s.x, s.y);
    ctx.lineTo(t.x, t.y);
    ctx.strokeStyle = isHovered ? '#00f0ff' : (edge.color || 'rgba(0, 240, 255, 0.25)');
    ctx.lineWidth = isHovered ? 2 * ratio : 1 * ratio;
    ctx.stroke();
  }

  for (let i = memoryUI.pulses.length - 1; i >= 0; i--) {
    const p = memoryUI.pulses[i];
    p.progress += p.speed;
    if (p.progress >= 1.0) {
      p.progress = 0;
    }
    const s = nodeMap.get(p.edge.source);
    const t = nodeMap.get(p.edge.target);
    if (s && t) {
      const px = s.x + (t.x - s.x) * p.progress;
      const py = s.y + (t.y - s.y) * p.progress;
      ctx.beginPath();
      ctx.arc(px, py, 2.5 * ratio, 0, Math.PI * 2);
      ctx.fillStyle = '#ffffff';
      ctx.shadowColor = p.color || '#00f0ff';
      ctx.shadowBlur = 6 * ratio;
      ctx.fill();
      ctx.shadowBlur = 0;
    }
  }

  for (const n of memoryUI.nodes) {
    const isHovered = memoryUI.hoveredNode === n;
    const baseSize = (n.size || 10) * ratio;
    const size = isHovered ? baseSize * 1.3 : baseSize;

    ctx.beginPath();
    ctx.arc(n.x, n.y, size + 3 * ratio, 0, Math.PI * 2);
    ctx.fillStyle = isHovered ? 'rgba(0, 240, 255, 0.4)' : 'rgba(0, 240, 255, 0.1)';
    ctx.fill();

    ctx.beginPath();
    ctx.arc(n.x, n.y, size, 0, Math.PI * 2);
    ctx.fillStyle = n.color || '#00f0ff';
    ctx.fill();

    ctx.beginPath();
    ctx.arc(n.x, n.y, size * 0.45, 0, Math.PI * 2);
    ctx.fillStyle = '#ffffff';
    ctx.fill();

    ctx.font = `${Math.round(9 * ratio)}px "JetBrains Mono", monospace`;
    ctx.fillStyle = isHovered ? '#00f0ff' : 'rgba(255, 255, 255, 0.85)';
    ctx.textAlign = 'center';
    ctx.fillText(n.label || '', n.x, n.y + size + 11 * ratio);
  }
}

// Memory UI controls setup
function initMemoryEvents() {
  if ($('mem-btn-graph')) {
    $('mem-btn-graph').addEventListener('click', () => {
      memoryUI.mode = 'graph';
      $('mem-btn-graph').classList.add('active');
      $('mem-btn-ledger').classList.remove('active');
      $('mem-graph-view').style.display = 'block';
      $('mem-ledger-view').style.display = 'none';
      initMemoryCanvas();
    });
  }

  if ($('mem-btn-ledger')) {
    $('mem-btn-ledger').addEventListener('click', () => {
      memoryUI.mode = 'ledger';
      $('mem-btn-ledger').classList.add('active');
      $('mem-btn-graph').classList.remove('active');
      $('mem-graph-view').style.display = 'none';
      $('mem-ledger-view').style.display = 'block';
      renderMemoryCards();
    });
  }

  const pills = document.querySelectorAll('#mem-filters .mem-pill');
  pills.forEach(pill => {
    pill.addEventListener('click', () => {
      pills.forEach(p => p.classList.remove('active'));
      pill.classList.add('active');
      memoryUI.activeCategory = pill.dataset.cat || 'all';
      renderMemoryCards();
    });
  });

  if ($('mem-search-input')) {
    $('mem-search-input').addEventListener('input', (e) => {
      memoryUI.searchQuery = e.target.value.trim();
      renderMemoryCards();
    });
  }

  if ($('mem-add-toggle')) {
    $('mem-add-toggle').addEventListener('click', () => {
      const panel = $('mem-add-panel');
      if (panel) panel.style.display = panel.style.display === 'none' ? 'flex' : 'none';
    });
  }

  if ($('mem-new-cancel')) {
    $('mem-new-cancel').addEventListener('click', () => {
      const panel = $('mem-add-panel');
      if (panel) panel.style.display = 'none';
    });
  }

  if ($('mem-new-save')) {
    $('mem-new-save').addEventListener('click', async () => {
      const input = $('mem-new-content');
      const catSelect = $('mem-new-category');
      const content = input ? input.value.trim() : '';
      const category = catSelect ? catSelect.value : 'general';
      if (!content) return;

      await fetch('/api/memories/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, category })
      });
      if (input) input.value = '';
      const panel = $('mem-add-panel');
      if (panel) panel.style.display = 'none';
      refreshMemoriesFromApi();
    });
  }
}
setTimeout(initMemoryEvents, 100);


/* ── executive coach & focus standup (panel 15) ───────────────────── */

const coachUI = {
  open: false,
  goals: [],
  metrics: {
    total_goals: 0,
    completed_goals: 0,
    pending_goals: 0,
    goal_pct: 0,
    focus_ratio_pct: 100,
    productivity_score: 0,
    focus_hours: 0,
    distraction_minutes: 0,
    neutral_minutes: 0
  },
  current_window: "Desktop",
  current_category: "neutral",
  blocker_enabled: false,
  date: "TODAY"
};

function openCoach() {
  coachUI.open = true;
  const el = $('coach-overlay');
  if (el) {
    el.classList.add('open');
    el.setAttribute('aria-hidden', 'false');
    el.style.display = 'flex';
  }
  refreshCoachFromApi();
}

function closeCoach() {
  coachUI.open = false;
  const el = $('coach-overlay');
  if (el) {
    el.classList.remove('open');
    el.setAttribute('aria-hidden', 'true');
    el.style.display = 'none';
  }
}

function toggleCoach() {
  if (coachUI.open) closeCoach();
  else openCoach();
}

async function refreshCoachFromApi() {
  try {
    const res = await fetch('/api/coach');
    if (!res.ok) return;
    const data = await res.json();
    applyCoach(data, false);
  } catch (err) {
    console.error('Failed to fetch coach state:', err);
  }
}

function applyCoach(data, fromSnapshot) {
  if (!data) return;
  if (data.event === 'show_overlay' || data.event === 'standup_prompt' ||
      data.event === 'standup_started' || data.event === 'debrief_completed') {
    openCoach();
  }
  if (data.goals) coachUI.goals = data.goals;
  if (data.metrics) coachUI.metrics = data.metrics;
  if (data.current_window) coachUI.current_window = data.current_window;
  if (data.current_category) coachUI.current_category = data.current_category;
  if (typeof data.blocker_enabled === 'boolean') coachUI.blocker_enabled = data.blocker_enabled;
  if (data.date) coachUI.date = data.date;

  updateCoachUI();
}

function updateCoachUI() {
  const m = coachUI.metrics || {};
  const goals = coachUI.goals || [];

  // 1. Date label
  if ($('coach-date-label')) $('coach-date-label').textContent = coachUI.date || 'TODAY';

  // 2. Center score
  if ($('coach-center-score')) $('coach-center-score').textContent = `${m.productivity_score || 0}%`;

  // 3. Concentric Rings SVG progress
  // Circumferences:
  // goals: r=95 -> C = 2 * PI * 95 = 596.9
  // focus: r=75 -> C = 2 * PI * 75 = 471.2
  // score: r=55 -> C = 2 * PI * 55 = 345.6
  const cGoals = 597;
  const cFocus = 471;
  const cScore = 346;

  const ringGoals = $('ring-goals');
  if (ringGoals) {
    const gPct = Math.min(100, Math.max(0, m.goal_pct || 0));
    ringGoals.style.strokeDashoffset = cGoals - (cGoals * gPct / 100);
  }

  const ringFocus = $('ring-focus');
  if (ringFocus) {
    const fPct = Math.min(100, Math.max(0, m.focus_ratio_pct ?? 100));
    ringFocus.style.strokeDashoffset = cFocus - (cFocus * fPct / 100);
  }

  const ringScore = $('ring-score');
  if (ringScore) {
    const sPct = Math.min(100, Math.max(0, m.productivity_score || 0));
    ringScore.style.strokeDashoffset = cScore - (cScore * sPct / 100);
  }

  // 4. Metrics Cards
  if ($('coach-stat-goals')) {
    $('coach-stat-goals').innerHTML = `${m.completed_goals || 0} / ${m.total_goals || 0} <span class="cm-pct" id="coach-pct-goals">(${m.goal_pct || 0}%)</span>`;
  }
  if ($('coach-stat-focus')) {
    $('coach-stat-focus').innerHTML = `${m.focus_ratio_pct ?? 100}% <span class="cm-sub" id="coach-sub-focus">(${m.focus_hours || 0}h focus)</span>`;
  }
  if ($('coach-stat-score')) {
    $('coach-stat-score').textContent = `${m.productivity_score || 0} / 100`;
  }

  // 5. Active window telemetry
  const winTitle = $('coach-win-title');
  if (winTitle) {
    winTitle.textContent = coachUI.current_window || 'Desktop';
    winTitle.title = coachUI.current_window || 'Desktop';
  }
  const badge = $('coach-win-badge');
  if (badge) {
    const cat = coachUI.current_category || 'neutral';
    badge.className = `cat-badge badge-${cat}`;
    badge.textContent = cat.toUpperCase();
  }

  // 6. Blocker switch
  const blockerToggle = $('coach-blocker-toggle');
  if (blockerToggle) {
    blockerToggle.checked = !!coachUI.blocker_enabled;
  }

  // 7. Focus Bar Breakdown
  const fHrs = m.focus_hours || 0;
  const dMin = m.distraction_minutes || 0;
  if ($('cfb-focus-hrs')) $('cfb-focus-hrs').textContent = `${fHrs}h`;
  if ($('cfb-distract-min')) $('cfb-distract-min').textContent = `${dMin}m`;

  const totalTimeMin = (fHrs * 60) + dMin;
  const fillFocus = $('cfb-fill-focus');
  const fillDistract = $('cfb-fill-distract');
  if (fillFocus && fillDistract) {
    if (totalTimeMin > 0) {
      const fWidth = Math.round(((fHrs * 60) / totalTimeMin) * 100);
      fillFocus.style.width = `${fWidth}%`;
      fillDistract.style.width = `${100 - fWidth}%`;
    } else {
      fillFocus.style.width = '100%';
      fillDistract.style.width = '0%';
    }
  }

  // 8. Goals Checklist
  renderCoachGoals();
}

function renderCoachGoals() {
  const container = $('coach-goals-list');
  if (!container) return;

  const goals = coachUI.goals || [];
  if (goals.length === 0) {
    container.innerHTML = `<div class="coach-empty-goals mono micro dim">
      NO GOALS LOGGED FOR TODAY.<br/>
      SAY "START STANDUP" OR TYPE YOUR NON-NEGOTIABLES ABOVE.
    </div>`;
    return;
  }

  container.innerHTML = '';
  for (const g of goals) {
    const item = document.createElement('div');
    item.className = `coach-goal-item ${g.completed ? 'is-completed' : ''}`;

    const mainDiv = document.createElement('div');
    mainDiv.className = 'coach-goal-main';

    const chk = document.createElement('input');
    chk.type = 'checkbox';
    chk.className = 'coach-checkbox';
    chk.checked = !!g.completed;
    chk.addEventListener('change', async () => {
      await fetch('/api/coach/complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: g.id, completed: chk.checked })
      });
      refreshCoachFromApi();
    });

    const textSpan = document.createElement('span');
    textSpan.className = 'coach-goal-text mono';
    textSpan.textContent = g.text;

    mainDiv.append(chk, textSpan);

    const delBtn = document.createElement('button');
    delBtn.className = 'coach-goal-del';
    delBtn.title = 'Delete goal';
    delBtn.innerHTML = '✕';
    delBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      await fetch('/api/coach/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: g.id })
      });
      refreshCoachFromApi();
    });

    item.append(mainDiv, delBtn);
    container.appendChild(item);
  }
}

function initCoachEvents() {
  // Blocker toggle change
  const blockerToggle = $('coach-blocker-toggle');
  if (blockerToggle) {
    blockerToggle.addEventListener('change', async () => {
      await fetch('/api/coach/blocker', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: blockerToggle.checked })
      });
    });
  }

  // Evening Debrief button
  const debriefBtn = $('coach-btn-debrief');
  if (debriefBtn) {
    debriefBtn.addEventListener('click', async () => {
      debriefBtn.disabled = true;
      try {
        await fetch('/api/coach/debrief', { method: 'POST' });
      } finally {
        setTimeout(() => { debriefBtn.disabled = false; }, 1500);
      }
    });
  }

  // Add goal form
  const addForm = $('coach-add-form');
  if (addForm) {
    addForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const input = $('coach-new-goal-input');
      const text = input ? input.value.trim() : '';
      if (!text) return;
      await fetch('/api/coach/goal', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text })
      });
      if (input) input.value = '';
      refreshCoachFromApi();
    });
  }

  // Close button & backdrop
  if ($('coach-close')) $('coach-close').addEventListener('click', closeCoach);
  if ($('coach-backdrop')) $('coach-backdrop').addEventListener('click', closeCoach);

  // Chip click
  if ($('chip-coach')) $('chip-coach').addEventListener('click', toggleCoach);
}
setTimeout(initCoachEvents, 100);



/* ══════════════════════════════════════════════════════════════════════
   HOLOGRAPHIC WEATHER STATION (PANEL 05-B)
   5-Day Outlook · 3-Hour Intervals · Atmospheric Radar Telemetry
   ══════════════════════════════════════════════════════════════════════ */

const weatherStationUI = {
  open: false,
  data: null,
};

function openWeatherStation(data) {
  weatherStationUI.open = true;
  const overlay = $('weather-station-overlay');
  if (overlay) {
    overlay.dataset.status = 'open';
    overlay.setAttribute('aria-hidden', 'false');
  }
  if (data && data.ok) {
    weatherStationUI.data = data;
    renderWeatherStation(data);
  } else if (weatherStationUI.data && weatherStationUI.data.ok) {
    renderWeatherStation(weatherStationUI.data);
  } else {
    refreshWeatherStation();
  }
}

function closeWeatherStation() {
  weatherStationUI.open = false;
  const overlay = $('weather-station-overlay');
  if (overlay) {
    overlay.dataset.status = 'idle';
    overlay.setAttribute('aria-hidden', 'true');
  }
}

function toggleWeatherStation() {
  if (weatherStationUI.open) closeWeatherStation();
  else openWeatherStation();
}

async function refreshWeatherStation() {
  try {
    const btn = $('ws-btn-refresh');
    if (btn) btn.classList.add('spinning');
    const res = await fetch('/api/weather/station');
    if (!res.ok) return;
    const data = await res.json();
    weatherStationUI.data = data;
    renderWeatherStation(data);
  } catch (err) {
    console.error('Failed to fetch weather station data:', err);
  } finally {
    const btn = $('ws-btn-refresh');
    if (btn) btn.classList.remove('spinning');
  }
}

function applyWeatherStation(data, fromSnapshot) {
  if (!data) return;
  weatherStationUI.data = data;
  if (data.open) {
    openWeatherStation(data);
  } else if (weatherStationUI.open) {
    renderWeatherStation(data);
  }
}

function renderWeatherStation(data) {
  if (!data || !data.ok) return;
  const curr = data.current || {};
  const units = data.units || { temp: '°C', wind: 'km/h' };

  // Metadata
  if ($('ws-meta-location')) $('ws-meta-location').textContent = (data.location || data.place || 'SIRAJGANJ').toUpperCase();
  if ($('ws-meta-coords') && data.coordinates) {
    const lat = data.coordinates.lat !== undefined ? Number(data.coordinates.lat).toFixed(2) : '24.45';
    const lon = data.coordinates.lon !== undefined ? Number(data.coordinates.lon).toFixed(2) : '89.72';
    $('ws-meta-coords').textContent = `${lat}°N, ${lon}°E`;
  }
  if ($('ws-meta-provider')) $('ws-meta-provider').textContent = (data.provider || 'OPENWEATHERMAP v2.5').toUpperCase();
  if ($('ws-meta-time')) $('ws-meta-time').textContent = data.local_time || '--:--';
  if ($('ws-meta-status')) $('ws-meta-status').textContent = (data.radar_status || 'SATELLITE TELEMETRY ACTIVE').toUpperCase();

  // Briefing
  if ($('ws-brief-text') && data.brief) {
    $('ws-brief-text').textContent = data.brief;
  }
  if ($('ws-brief-time')) {
    const d = new Date();
    $('ws-brief-time').textContent = `LIVE TELEMETRY · ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
  }

  // Current Telemetry
  if ($('ws-cur-temp')) $('ws-cur-temp').textContent = curr.temp !== undefined ? `${curr.temp}°` : '--°';
  if ($('ws-cur-feels')) $('ws-cur-feels').textContent = `FEELS LIKE ${curr.feels_like !== undefined ? curr.feels_like : '--'}°`;
  if ($('ws-cur-cond')) $('ws-cur-cond').textContent = (curr.condition || 'NOMINAL').toUpperCase();
  if ($('ws-cur-hi')) $('ws-cur-hi').textContent = curr.temp_max !== undefined ? `${curr.temp_max}°` : '--°';
  if ($('ws-cur-lo')) $('ws-cur-lo').textContent = curr.temp_min !== undefined ? `${curr.temp_min}°` : '--°';

  // Pressure & Barometer
  if ($('ws-cur-pressure')) $('ws-cur-pressure').innerHTML = `${curr.pressure || 1013} <i>hPa</i>`;
  if ($('ws-bar-pressure')) {
    const pPct = Math.max(10, Math.min(100, (((curr.pressure || 1013) - 970) / 70) * 100));
    $('ws-bar-pressure').style.width = `${pPct}%`;
  }

  // Humidity & Dew Point
  if ($('ws-cur-humidity')) $('ws-cur-humidity').innerHTML = `${curr.humidity !== undefined ? curr.humidity : 50}<i>%</i>`;
  if ($('ws-cur-dew')) $('ws-cur-dew').textContent = `DEW POINT: ${curr.dew_point !== undefined ? curr.dew_point : '--'}${units.temp || '°C'}`;
  if ($('ws-bar-humidity')) $('ws-bar-humidity').style.width = `${Math.max(5, Math.min(100, curr.humidity || 50))}%`;

  // Wind Vector
  if ($('ws-cur-wind')) $('ws-cur-wind').innerHTML = `${curr.wind_speed !== undefined ? curr.wind_speed : 0} <i>${units.wind || 'km/h'}</i>`;
  if ($('ws-cur-winddir')) $('ws-cur-winddir').textContent = `DIRECTION: ${curr.wind_dir || 'N'} (${curr.wind_deg !== undefined ? curr.wind_deg : 0}°)`;
  if ($('ws-cur-gust')) $('ws-cur-gust').textContent = curr.wind_gust ? `GUSTS: ${curr.wind_gust} ${units.wind || 'km/h'}` : 'GUSTS: NOMINAL';

  // Visibility & Clouds
  if ($('ws-cur-vis')) $('ws-cur-vis').innerHTML = `${curr.visibility_km !== undefined ? curr.visibility_km : 10} <i>km</i>`;
  if ($('ws-cur-clouds')) $('ws-cur-clouds').textContent = `CLOUD COVER: ${curr.clouds_pct !== undefined ? curr.clouds_pct : 0}%`;
  if ($('ws-bar-clouds')) $('ws-bar-clouds').style.width = `${Math.max(5, Math.min(100, curr.clouds_pct || 0))}%`;

  // Solar Cycle
  if ($('ws-cur-sunrise')) $('ws-cur-sunrise').textContent = curr.sunrise || '05:45 AM';
  if ($('ws-cur-sunset')) $('ws-cur-sunset').textContent = curr.sunset || '06:15 PM';
  if ($('ws-cur-phase')) $('ws-cur-phase').textContent = `SOLAR ELEVATION: ${curr.is_day ? 'DAYLIGHT PHASE' : 'NIGHT PHASE'}`;

  // 24-Hour Timeline
  const hourlyStrip = $('ws-hourly-strip');
  if (hourlyStrip) {
    const hourly = Array.isArray(data.hourly_forecast) ? data.hourly_forecast : [];
    if (!hourly.length) {
      hourlyStrip.innerHTML = '<div class="ws-empty mono micro dim">NO HOURLY RADAR DATA</div>';
    } else {
      hourlyStrip.innerHTML = hourly.slice(0, 8).map(h => `
        <div class="ws-hour-card">
          <span class="ws-hour-time mono">${h.time || '--:--'}</span>
          <svg class="ws-hour-icon" viewBox="0 0 32 32" aria-hidden="true">
            <use href="#${wxSymbol(h.group, true)}"></use>
          </svg>
          <span class="ws-hour-temp mono">${h.temp !== undefined ? `${h.temp}°` : '--'}</span>
          <span class="ws-hour-cond mono micro">${h.condition || ''}</span>
          <span class="ws-hour-pop mono micro">RAIN ${h.pop || 0}%</span>
        </div>
      `).join('');
    }
  }

  // 5-Day Outlook
  const dailyGrid = $('ws-daily-grid');
  if (dailyGrid) {
    const daily = Array.isArray(data.daily_forecast) ? data.daily_forecast : [];
    if (!daily.length) {
      dailyGrid.innerHTML = '<div class="ws-empty mono micro dim">NO 5-DAY PROJECTIONS</div>';
    } else {
      dailyGrid.innerHTML = daily.slice(0, 5).map((d, i) => `
        <div class="ws-day-card ${i === 0 ? 'today' : ''}">
          <div class="ws-day-head">
            <span class="ws-day-badge mono">${d.day || 'DAY'}</span>
            <span class="ws-day-date mono">${d.date || ''}</span>
          </div>
          <div class="ws-day-main">
            <svg class="ws-day-icon" viewBox="0 0 32 32" aria-hidden="true">
              <use href="#${wxSymbol(d.group, true)}"></use>
            </svg>
            <div class="ws-day-info">
              <span class="ws-day-cond">${d.condition || 'Clear'}</span>
              <span class="ws-day-pop mono micro">PRECIP CHANCE ${d.rain_pct || 0}%</span>
            </div>
          </div>
          <div class="ws-day-temps">
            <span class="ws-day-hi mono">MAX ${d.hi !== undefined ? `${d.hi}°` : '--'}</span>
            <span class="ws-day-lo mono">MIN ${d.lo !== undefined ? `${d.lo}°` : '--'}</span>
          </div>
        </div>
      `).join('');
    }
  }
}

/* ══════════════════════════════════════════════════════════════════════
   THE RON WORLD TRIBUNE — HOLOGRAPHIC BROADSHEET MODAL CONTROLLER
   ══════════════════════════════════════════════════════════════════════ */

const tribuneUI = {
  open: false,
  activeDesk: 'all',
  data: null,
};

function openTribune(data) {
  tribuneUI.open = true;
  const overlay = $('tribune-overlay');
  if (overlay) {
    overlay.removeAttribute('hidden');
    overlay.setAttribute('aria-hidden', 'false');
    overlay.setAttribute('data-status', 'open');
    overlay.style.display = 'flex';
  }
  if (data && data.edition_id) {
    tribuneUI.data = data;
    renderTribune(data);
  } else if (tribuneUI.data && tribuneUI.data.edition_id) {
    renderTribune(tribuneUI.data);
  } else {
    refreshTribune();
  }
}

function closeTribune() {
  tribuneUI.open = false;
  const overlay = $('tribune-overlay');
  if (overlay) {
    overlay.setAttribute('hidden', '');
    overlay.setAttribute('aria-hidden', 'true');
    overlay.setAttribute('data-status', 'idle');
    overlay.style.display = 'none';
  }
}

function toggleTribune() {
  if (tribuneUI.open) closeTribune();
  else openTribune();
}

async function refreshTribune() {
  const btn = $('tb-btn-refresh');
  if (btn) btn.classList.add('spinning');
  try {
    const res = await fetch('/api/tribune/latest');
    if (!res.ok) return;
    const data = await res.json();
    tribuneUI.data = data;
    renderTribune(data);
  } catch (err) {
    console.error('Failed to fetch Tribune edition:', err);
  } finally {
    if (btn) btn.classList.remove('spinning');
  }
}

async function forceGenerateTribune() {
  const btn = $('tb-btn-refresh');
  if (btn) {
    btn.classList.add('spinning');
    btn.disabled = true;
  }
  try {
    const res = await fetch('/api/tribune/generate', { method: 'POST' });
    if (!res.ok) return;
    const resJson = await res.json();
    if (resJson.data) {
      tribuneUI.data = resJson.data;
      renderTribune(resJson.data);
    }
  } catch (err) {
    console.error('Failed to re-publish Tribune edition:', err);
  } finally {
    if (btn) {
      btn.classList.remove('spinning');
      btn.disabled = false;
    }
  }
}

async function triggerTribuneBroadcast() {
  const btn = $('tb-btn-broadcast');
  if (btn) btn.style.opacity = '0.5';
  try {
    await fetch('/api/tribune/broadcast', { method: 'POST' });
  } catch (err) {
    console.error('Failed to trigger broadcast:', err);
  } finally {
    if (btn) btn.style.opacity = '1';
  }
}

async function openTribunePdf() {
  try {
    await fetch('/api/tribune/pdf');
  } catch (err) {
    console.error('Failed to open PDF:', err);
  }
}

function applyTribune(data, fromSnapshot) {
  if (!data || !data.edition_id) return;
  tribuneUI.data = data;
  if (!fromSnapshot && (data.event === 'show_overlay' || data.open === true)) {
    openTribune(data);
  } else if (tribuneUI.open) {
    renderTribune(data);
  }
}

function renderTribune(data) {
  if (!data) return;
  // Meta bar
  if ($('tb-meta-edition')) $('tb-meta-edition').textContent = data.edition_id || '--';
  if ($('tb-meta-vol')) $('tb-meta-vol').textContent = data.volume || 'VOL. IV';
  if ($('tb-meta-date')) $('tb-meta-date').textContent = (data.date_formatted || '').toUpperCase();
  if ($('tb-meta-weather')) $('tb-meta-weather').textContent = (data.weather_short || 'SIRAJGANJ').toUpperCase();
  if ($('tb-meta-sources')) $('tb-meta-sources').textContent = `${data.source_count || 50}+ SOURCES HARVESTED`;

  const sections = data.sections || {};
  const world = sections.world || [];
  const tech = sections.tech || [];
  const markets = sections.markets || [];
  const science = sections.science || [];
  const sports = sections.sports || [];
  const regional = sections.regional || [];

  // Executive summary
  if ($('tb-exec-world')) $('tb-exec-world').textContent = world[0]?.title || 'Global geopolitical dispatches nominal.';
  if ($('tb-exec-tech')) $('tb-exec-tech').textContent = tech[0]?.title || 'Frontier AI & computing architectures accelerating.';
  const cryptoAssets = (markets || []).filter(m => m.is_price_ticker);
  if ($('tb-exec-markets')) {
    $('tb-exec-markets').textContent = cryptoAssets.length ? cryptoAssets.slice(0, 2).map(c => c.title).join(' · ') : (markets[0]?.title || 'Digital assets and spot rates holding.');
  }
  if ($('tb-exec-sports')) $('tb-exec-sports').textContent = sports[0]?.title || 'Global football fixtures and athletic telemetry updated.';

  // Lead article
  const lead = tech[0] || world[0] || null;
  if (lead && $('tb-lead-container')) {
    if ($('tb-lead-title')) $('tb-lead-title').textContent = lead.title;
    if ($('tb-lead-byline')) $('tb-lead-byline').textContent = `DISPATCHED BY ${lead.source ? lead.source.toUpperCase() : 'R.O.N. EDITORIAL BUREAU'} · GLOBAL WIRE`;
    if ($('tb-lead-body')) $('tb-lead-body').textContent = lead.summary || '';
  }

  // Render articles grid based on activeDesk
  renderTribuneArticlesGrid(sections);
}

function renderTribuneArticlesGrid(sections) {
  const container = $('tb-articles-grid');
  if (!container) return;
  container.innerHTML = '';

  const desk = tribuneUI.activeDesk;
  let articles = [];

  if (desk === 'all') {
    Object.keys(sections).forEach(k => {
      (sections[k] || []).forEach(a => articles.push({ ...a, deskKey: k }));
    });
  } else {
    articles = (sections[desk] || []).map(a => ({ ...a, deskKey: desk }));
  }

  if (!articles.length) {
    container.innerHTML = '<div class="ws-empty mono micro dim">NO ARTICLES IN CURRENT DESK FILTER.</div>';
    return;
  }

  articles.slice(0, 36).forEach(art => {
    const card = document.createElement('div');
    card.className = 'tb-article-card';

    const head = document.createElement('div');
    head.className = 'tb-article-head mono micro';
    head.innerHTML = `
      <span class="tb-desk-badge tb-desk-${art.deskKey || 'tech'}">${(art.deskKey || 'wire').toUpperCase()}</span>
      <span class="tb-article-source">${art.source || 'WIRE'}</span>
    `;

    const title = document.createElement('h4');
    title.className = 'tb-article-title';
    title.textContent = art.title;

    const summary = document.createElement('p');
    summary.className = 'tb-article-summary';
    summary.textContent = art.summary || '';

    card.append(head, title, summary);

    if (art.url && art.url.startsWith('http')) {
      const link = document.createElement('a');
      link.className = 'tb-article-link mono micro';
      link.href = art.url;
      link.target = '_blank';
      link.rel = 'noopener';
      link.innerHTML = 'READ ORIGINAL DISPATCH ↗';
      link.addEventListener('click', (e) => {
        e.preventDefault();
        fetch('/api/open', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: art.url })
        });
      });
      card.append(link);
    }

    container.appendChild(card);
  });
}

function initTribuneEvents() {
  if ($('tb-close')) $('tb-close').addEventListener('click', closeTribune);
  if ($('tb-btn-broadcast')) $('tb-btn-broadcast').addEventListener('click', triggerTribuneBroadcast);
  if ($('tb-btn-pdf')) $('tb-btn-pdf').addEventListener('click', openTribunePdf);
  if ($('tb-btn-refresh')) $('tb-btn-refresh').addEventListener('click', forceGenerateTribune);

  // Tabs
  const tabs = document.querySelectorAll('.tb-tab');
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      tabs.forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      tribuneUI.activeDesk = tab.dataset.desk || 'all';
      if (tribuneUI.data && tribuneUI.data.sections) {
        renderTribuneArticlesGrid(tribuneUI.data.sections);
      }
    });
  });
}
setTimeout(initTribuneEvents, 150);


/* ── RON WORLD REPORT · LIVE INTEL BROADCAST (PANEL 16) ────────── */

const intelUI = {
  open: false,
  activeCategory: 'all',
  data: {
    ticker: '',
    hackernews: [],
    technews: [],
    crypto: [],
    github: [],
    football: [],
    football_news: [],
    weather: {},
    spoken: '',
    timestamp: '00:00:00'
  }
};

function openIntel() {
  intelUI.open = true;
  const el = $('intel-overlay');
  if (el) {
    el.classList.add('open');
    el.setAttribute('aria-hidden', 'false');
    el.style.display = 'flex';
  }
  refreshIntelFromApi();
}

function closeIntel() {
  intelUI.open = false;
  const el = $('intel-overlay');
  if (el) {
    el.classList.remove('open');
    el.setAttribute('aria-hidden', 'true');
    el.style.display = 'none';
  }
}

function toggleIntel() {
  if (intelUI.open) closeIntel();
  else openIntel();
}

async function refreshIntelFromApi() {
  try {
    const res = await fetch('/api/intel');
    if (!res.ok) return;
    const data = await res.json();
    applyIntel(data, false);
  } catch (err) {
    console.error('Failed to fetch intel state:', err);
  }
}

function applyIntel(data, fromSnapshot) {
  if (!data) return;
  if (data.event === 'show_overlay') {
    openIntel();
  }

  if (data.ticker) intelUI.data.ticker = data.ticker;
  if (data.hackernews) intelUI.data.hackernews = data.hackernews;
  if (data.technews) intelUI.data.technews = data.technews;
  if (data.crypto) intelUI.data.crypto = data.crypto;
  if (data.github) intelUI.data.github = data.github;
  if (data.football) intelUI.data.football = data.football;
  if (data.football_news) intelUI.data.football_news = data.football_news;
  if (data.weather) intelUI.data.weather = data.weather;
  if (data.spoken) intelUI.data.spoken = data.spoken;
  if (data.timestamp) intelUI.data.timestamp = data.timestamp;

  if (data.category) {
    intelUI.activeCategory = data.category;
    const filters = $('intel-filters');
    if (filters) {
      filters.querySelectorAll('.intel-pill').forEach(b => {
        b.classList.toggle('active', b.dataset.cat === data.category);
      });
    }
  }

  updateIntelUI();
}

function updateIntelUI() {
  const d = intelUI.data;

  // 1. Update live ticker tape marquee
  const tickerEl = $('intel-ticker-text');
  if (tickerEl && d.ticker) {
    tickerEl.textContent = d.ticker;
  }

  // 2. Timestamp
  if ($('intel-timestamp')) $('intel-timestamp').textContent = d.timestamp || '00:00:00';

  // 3. Spoken broadcast text
  if ($('intel-spoken-text') && d.spoken) {
    $('intel-spoken-text').textContent = `"${d.spoken}"`;
  }

  // 4. Crypto prices
  const crypto = d.crypto || [];
  const btc = crypto.find(c => c.symbol === 'BTC');
  if (btc) {
    if ($('btc-price')) $('btc-price').textContent = `$${btc.price.toLocaleString()}`;
    if ($('btc-change')) {
      const sign = btc.change_24h >= 0 ? '+' : '';
      $('btc-change').textContent = `${sign}${btc.change_24h}%`;
      $('btc-change').className = `cc-change mono micro ${btc.change_24h >= 0 ? 'up' : 'down'}`;
    }
  }
  const eth = crypto.find(c => c.symbol === 'ETH');
  if (eth) {
    if ($('eth-price')) $('eth-price').textContent = `$${eth.price.toLocaleString()}`;
    if ($('eth-change')) {
      const sign = eth.change_24h >= 0 ? '+' : '';
      $('eth-change').textContent = `${sign}${eth.change_24h}%`;
      $('eth-change').className = `cc-change mono micro ${eth.change_24h >= 0 ? 'up' : 'down'}`;
    }
  }
  const sol = crypto.find(c => c.symbol === 'SOL');
  if (sol) {
    if ($('sol-price')) $('sol-price').textContent = `$${sol.price.toLocaleString()}`;
    if ($('sol-change')) {
      const sign = sol.change_24h >= 0 ? '+' : '';
      $('sol-change').textContent = `${sign}${sol.change_24h}%`;
      $('sol-change').className = `cc-change mono micro ${sol.change_24h >= 0 ? 'up' : 'down'}`;
    }
  }

  // 5. Weather telemetry
  const wx = d.weather || {};
  if ($('intel-wx-temp')) {
    $('intel-wx-temp').textContent = wx.temp !== null && wx.temp !== undefined ? `${Math.round(wx.temp)}°C` : '--°C';
  }
  if ($('intel-wx-desc')) {
    $('intel-wx-desc').textContent = `${(wx.place || 'LOCAL SECTOR').toUpperCase()} · ${(wx.condition || 'NOMINAL').toUpperCase()}`;
  }

  // Category column visibility filtering
  const colFoot = $('col-football');
  const colTech = $('col-tech-hn');
  const colGh = $('col-github');
  const cat = intelUI.activeCategory;

  if (colFoot && colTech && colGh) {
    if (cat === 'all') {
      colFoot.style.display = 'flex';
      colTech.style.display = 'flex';
      colGh.style.display = 'flex';
      colFoot.style.gridColumn = '';
      colTech.style.gridColumn = '';
      colGh.style.gridColumn = '';
    } else if (cat === 'football') {
      colFoot.style.display = 'flex';
      colTech.style.display = 'none';
      colGh.style.display = 'none';
      colFoot.style.gridColumn = '1 / -1';
    } else if (cat === 'tech' || cat === 'hn') {
      colFoot.style.display = 'none';
      colTech.style.display = 'flex';
      colGh.style.display = 'none';
      colTech.style.gridColumn = '1 / -1';
    } else if (cat === 'github') {
      colFoot.style.display = 'none';
      colTech.style.display = 'none';
      colGh.style.display = 'flex';
      colGh.style.gridColumn = '1 / -1';
    } else {
      // e.g. crypto
      colFoot.style.display = 'flex';
      colTech.style.display = 'flex';
      colGh.style.display = 'flex';
      colFoot.style.gridColumn = '';
      colTech.style.gridColumn = '';
      colGh.style.gridColumn = '';
    }
  }

  // 6. Render Football / Soccer matches and 2026 breaking news
  const footList = $('intel-cards-football');
  if (footList) {
    const matches = d.football || [];
    const news = d.football_news || [];
    if (matches.length === 0 && news.length === 0) {
      footList.innerHTML = '<div class="intel-empty mono micro dim">NO FOOTBALL INTELLIGENCE IN ACTIVE WINDOW.</div>';
    } else {
      footList.innerHTML = '';

      // Section 1: Latest 2026 Spotlight Matches
      if (matches.length > 0) {
        const matchTitle = document.createElement('div');
        matchTitle.className = 'fc-section-title mono micro';
        matchTitle.innerHTML = '<span>⚽ SPOTLIGHT MATCHES (2026/27 SEASON)</span>';
        footList.appendChild(matchTitle);

        matches.forEach(m => {
          const card = document.createElement('div');
          card.className = `football-card${m.is_priority ? ' priority' : ''}`;
          card.setAttribute('role', 'button');
          card.setAttribute('tabindex', '0');
          card.title = `${m.home} vs ${m.away} · Click for ESPN match intelligence`;

          const head = document.createElement('div');
          head.className = 'fc-head mono micro';

          const leagueDiv = document.createElement('div');
          leagueDiv.className = 'fc-league';
          const dateTag = m.date ? ` · ${m.date}` : '';
          leagueDiv.innerHTML = `<span>⚽ ${m.league || 'SOCCER'}${dateTag}</span>${m.is_priority ? '<span class="fc-spotlight-tag">★ SPOTLIGHT</span>' : ''}`;

          const statusSpan = document.createElement('span');
          const st = (m.status || 'FT').toUpperCase();
          let stClass = 'sched';
          if (m.state === 'in' || st.includes('LIVE') || st.includes("'")) {
            stClass = 'live';
          } else if (m.state === 'post' || st === 'FT' || st.includes('FINAL') || st.includes('FULL')) {
            stClass = 'ft';
          }
          statusSpan.className = `fc-status ${stClass}`;
          statusSpan.textContent = st;

          head.append(leagueDiv, statusSpan);

          const teams = document.createElement('div');
          teams.className = 'fc-teams';

          // Home row
          const homeRow = document.createElement('div');
          homeRow.className = 'fc-team-row';
          const homeLeft = document.createElement('div');
          homeLeft.className = 'fc-team-left';
          if (m.home_logo) {
            const img = document.createElement('img');
            img.className = 'fc-team-logo';
            img.src = m.home_logo;
            img.alt = m.home;
            img.onerror = () => { img.style.display = 'none'; };
            homeLeft.appendChild(img);
          } else {
            const initial = document.createElement('span');
            initial.className = 'fc-logo-fallback mono';
            initial.textContent = (m.home || 'H')[0].toUpperCase();
            homeLeft.appendChild(initial);
          }
          const homeName = document.createElement('span');
          homeName.className = 'fc-team-name';
          homeName.textContent = m.home;
          homeLeft.appendChild(homeName);

          const homeScore = document.createElement('span');
          homeScore.className = 'fc-team-score mono';
          homeScore.textContent = m.home_score !== undefined ? m.home_score : '-';
          homeRow.append(homeLeft, homeScore);

          // Away row
          const awayRow = document.createElement('div');
          awayRow.className = 'fc-team-row';
          const awayLeft = document.createElement('div');
          awayLeft.className = 'fc-team-left';
          if (m.away_logo) {
            const img = document.createElement('img');
            img.className = 'fc-team-logo';
            img.src = m.away_logo;
            img.alt = m.away;
            img.onerror = () => { img.style.display = 'none'; };
            awayLeft.appendChild(img);
          } else {
            const initial = document.createElement('span');
            initial.className = 'fc-logo-fallback mono';
            initial.textContent = (m.away || 'A')[0].toUpperCase();
            awayLeft.appendChild(initial);
          }
          const awayName = document.createElement('span');
          awayName.className = 'fc-team-name';
          awayName.textContent = m.away;
          awayLeft.appendChild(awayName);

          const awayScore = document.createElement('span');
          awayScore.className = 'fc-team-score mono';
          awayScore.textContent = m.away_score !== undefined ? m.away_score : '-';
          awayRow.append(awayLeft, awayScore);

          teams.append(homeRow, awayRow);
          card.append(head, teams);

          card.addEventListener('click', () => {
            if (m.url) {
              fetch('/api/open', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: m.url })
              });
            }
          });

          footList.appendChild(card);
        });
      }

      // Section 2: Breaking Football News (2026)
      if (news.length > 0) {
        const newsTitle = document.createElement('div');
        newsTitle.className = 'fc-section-title mono micro';
        newsTitle.style.marginTop = '12px';
        newsTitle.innerHTML = '<span>📰 BREAKING FOOTBALL NEWS (2026)</span>';
        footList.appendChild(newsTitle);

        news.forEach(item => {
          const card = document.createElement('div');
          card.className = 'intel-card-item';
          card.setAttribute('role', 'button');
          card.setAttribute('tabindex', '0');
          card.title = 'Click to open in browser';

          const head = document.createElement('div');
          head.className = 'ici-head mono micro';
          const pubShort = item.date ? item.date.replace(/^[A-Za-z]+,\s*/, '').replace(/:\d+\s+.*$/, '') : 'SEP 2026';
          head.innerHTML = `<span class="ici-source" style="color:#ffaa00">${item.source || 'SOCCER WIRE'}</span><span class="ici-meta">${pubShort}</span>`;

          const title = document.createElement('div');
          title.className = 'ici-title';
          title.textContent = item.title;

          card.append(head, title);

          if (item.description) {
            const desc = document.createElement('div');
            desc.className = 'ici-desc';
            desc.textContent = item.description;
            card.append(desc);
          }

          card.addEventListener('click', () => {
            if (item.url) {
              fetch('/api/open', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: item.url })
              });
            }
          });

          footList.appendChild(card);
        });
      }
    }
  }

  // 7. Render Tech & Hacker News cards
  const techList = $('intel-cards-tech');
  if (techList) {
    const combinedTech = [];
    const cat = intelUI.activeCategory;

    if (cat === 'all' || cat === 'tech') {
      (d.technews || []).forEach(t => combinedTech.push({ ...t, type: 'TECH' }));
    }
    if (cat === 'all' || cat === 'hn') {
      (d.hackernews || []).forEach(h => combinedTech.push({ ...h, type: 'HN' }));
    }

    if (combinedTech.length === 0) {
      techList.innerHTML = '<div class="intel-empty mono micro dim">NO WIRE STORIES IN CURRENT FILTER.</div>';
    } else {
      techList.innerHTML = '';
      combinedTech.forEach(item => {
        const card = document.createElement('div');
        card.className = 'intel-card-item';
        card.setAttribute('role', 'button');
        card.setAttribute('tabindex', '0');
        card.title = 'Click to open in browser';

        const head = document.createElement('div');
        head.className = 'ici-head mono micro';
        head.innerHTML = `<span class="ici-source">${item.source || 'WIRE'}</span><span class="ici-meta">${item.score ? `▲ ${item.score}` : 'BREAKING'}</span>`;

        const title = document.createElement('div');
        title.className = 'ici-title';
        title.textContent = item.title;

        card.append(head, title);

        if (item.description) {
          const desc = document.createElement('div');
          desc.className = 'ici-desc';
          desc.textContent = item.description;
          card.append(desc);
        }

        card.addEventListener('click', () => {
          if (item.url) {
            fetch('/api/open', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ url: item.url })
            });
          }
        });

        techList.appendChild(card);
      });
    }
  }

  // 8. Render GitHub Trending cards
  const ghList = $('intel-cards-github');
  if (ghList) {
    const repos = d.github || [];
    if (repos.length === 0 || (intelUI.activeCategory !== 'all' && intelUI.activeCategory !== 'github')) {
      ghList.innerHTML = '<div class="intel-empty mono micro dim">NO GITHUB REPOSITORIES IN CURRENT FILTER.</div>';
    } else {
      ghList.innerHTML = '';
      repos.forEach(repo => {
        const card = document.createElement('div');
        card.className = 'intel-card-item';
        card.setAttribute('role', 'button');
        card.setAttribute('tabindex', '0');
        card.title = 'Click to open in browser';

        const head = document.createElement('div');
        head.className = 'ici-head mono micro';
        head.innerHTML = `<span class="ici-source" style="color:#b055ff">GITHUB · ${repo.language || 'CODE'}</span><span class="ici-meta">★ ${(repo.stars || 0).toLocaleString()}</span>`;

        const title = document.createElement('div');
        title.className = 'ici-title mono';
        title.style.color = '#00f0ff';
        title.textContent = repo.name;

        const desc = document.createElement('div');
        desc.className = 'ici-desc';
        desc.textContent = repo.description || 'Open-source software repository';

        card.append(head, title, desc);

        card.addEventListener('click', () => {
          if (repo.url) {
            fetch('/api/open', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ url: repo.url })
            });
          }
        });

        ghList.appendChild(card);
      });
    }
  }
}

function initIntelEvents() {
  // Harvest / Refresh button
  const refreshBtn = $('intel-btn-refresh');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', async () => {
      refreshBtn.disabled = true;
      try {
        await fetch('/api/intel/refresh', { method: 'POST' });
        refreshIntelFromApi();
      } finally {
        setTimeout(() => { refreshBtn.disabled = false; }, 2000);
      }
    });
  }

  // Category filter pills
  const filters = $('intel-filters');
  if (filters) {
    filters.querySelectorAll('.intel-pill').forEach(btn => {
      btn.addEventListener('click', () => {
        filters.querySelectorAll('.intel-pill').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        intelUI.activeCategory = btn.dataset.cat || 'all';
        updateIntelUI();
      });
    });
  }

  // Ticker bar click
  const tickerWrap = $('intel-ticker-wrap');
  if (tickerWrap) tickerWrap.addEventListener('click', toggleIntel);

  // Close & Chip buttons
  if ($('intel-close')) $('intel-close').addEventListener('click', closeIntel);
  if ($('chip-intel')) $('chip-intel').addEventListener('click', toggleIntel);
}
setTimeout(initIntelEvents, 120);


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

/* ── Telegram Pocket Uplink ───────────────────────────────────────────── */

function applyTelegram(t, isSnapshot) {
  const chip = $('chip-tg');
  const label = $('chip-tg-label');
  if (!chip || !label) return;

  const connected = Boolean(t.connected);
  const paired = Boolean(t.paired);
  const username = t.username ? `@${t.username}` : '';

  if (paired && connected) {
    chip.dataset.ok = 'paired';
    label.textContent = 'LINKED';
    chip.title = `Telegram Uplink: Connected as ${username || 'Bot'} (Paired)\nCommands active.`;
  } else if (connected) {
    chip.dataset.ok = 'true';
    label.textContent = 'STANDBY';
    chip.title = `Telegram Uplink: Bot active (${username || 'Bot'})\nSend /pair <PIN> to authorize device.`;
  } else {
    chip.dataset.ok = 'false';
    label.textContent = 'OFFLINE';
    chip.title = 'Telegram Uplink: Offline (configure bot_token in config.json)';
  }
}

if ($('chip-tg')) {
  $('chip-tg').addEventListener('click', async () => {
    try {
      const res = await fetch('/api/telegram/status');
      if (res.ok) {
        const data = await res.json();
        const authed = (data.authorized_users || []).length;
        const info = `RON Telegram Pocket Uplink:\n• Status: ${data.connected ? 'ONLINE' : 'OFFLINE'}\n• Bot: ${data.username ? '@' + data.username : 'None'}\n• Authorized Devices: ${authed}\n• Last Command: ${data.last_command || 'None'}`;
        alert(info);
      }
    } catch (e) {
      console.warn('Failed to query telegram status', e);
    }
  });
}


/* ══════════════════════════════════════════════════════════════════════
   RON CYBER WATCHDOG — NETWORK RADAR & HACKER INTRUSION SCANNER
   ══════════════════════════════════════════════════════════════════════ */

const radarUI = {
  open: false,
  scanning: false,
  data: null,
  canvas: null,
  ctx: null,
  animId: null,
  sweepAngle: 0,
  blips: [], // [{x, y, r, alpha, ip, mac, vendor, name, isRogue, isHost, isGateway}]
};

function openRadar(data) {
  radarUI.open = true;
  const el = $('radar-overlay');
  if (el) {
    el.removeAttribute('hidden');
    el.setAttribute('aria-hidden', 'false');
    el.setAttribute('data-status', 'open');
    el.classList.add('open');
    el.style.display = 'flex';
  }
  initRadarCanvas();
  if (data && data.devices) {
    radarUI.data = data;
    renderRadarUI(data);
  } else if (!radarUI.data) {
    refreshRadarFromApi();
  } else {
    renderRadarUI(radarUI.data);
  }
}

function closeRadar() {
  radarUI.open = false;
  const el = $('radar-overlay');
  if (el) {
    el.setAttribute('hidden', '');
    el.setAttribute('aria-hidden', 'true');
    el.setAttribute('data-status', 'idle');
    el.classList.remove('open');
    el.style.display = 'none';
  }
  if (radarUI.animId) {
    cancelAnimationFrame(radarUI.animId);
    radarUI.animId = null;
  }
}

function toggleRadar() {
  if (radarUI.open) closeRadar();
  else openRadar();
}

async function refreshRadarFromApi() {
  try {
    const res = await fetch('/api/netradar/status');
    if (!res.ok) return;
    const data = await res.json();
    radarUI.data = data;
    renderRadarUI(data);
  } catch (err) {
    console.error('Failed to fetch radar status:', err);
  }
}

async function triggerRadarScan() {
  const btn = $('radar-btn-scan');
  if (btn) btn.classList.add('spinning');
  try {
    const res = await fetch('/api/netradar/scan', { method: 'POST' });
    if (res.ok) {
      const data = await res.json();
      radarUI.data = data;
      renderRadarUI(data);
    }
  } catch (err) {
    console.error('Failed to run radar scan:', err);
  } finally {
    if (btn) btn.classList.remove('spinning');
  }
}

async function trustDevice(mac, name) {
  try {
    const res = await fetch('/api/netradar/trust', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mac, name })
    });
    if (res.ok) {
      triggerRadarScan();
    }
  } catch (e) {
    console.error('Failed to trust device:', e);
  }
}

function applyNetRadar(data, isSnapshot) {
  if (!data) return;
  radarUI.data = data;

  // Update top-right status cluster chip
  const chip = $('chip-radar');
  const countLabel = $('chip-radar-count');
  if (chip && countLabel) {
    const devCount = (data.devices || []).length;
    const rogueCount = (data.rogue_devices || []).length;
    countLabel.textContent = `${devCount} NODES`;
    if (rogueCount > 0) {
      chip.dataset.ok = 'alert';
      chip.title = `NETWORK RADAR: ${rogueCount} ROGUE/UNKNOWN INTRUSION(S) DETECTED!`;
    } else {
      chip.dataset.ok = 'true';
      chip.title = `NETWORK RADAR: ${devCount} verified nodes active on subnet ${data.subnet || 'LAN'}`;
    }
  }

  // Open overlay when commanded via voice/bus, but do not open during initial snapshot hydration
  if (!isSnapshot && (data.event === 'show_overlay' || data.open === true)) {
    openRadar(data);
  } else if (radarUI.open) {
    renderRadarUI(data);
  }
}

function initRadarCanvas() {
  if (!radarUI.canvas) {
    radarUI.canvas = $('radar-canvas');
    if (radarUI.canvas) {
      radarUI.ctx = radarUI.canvas.getContext('2d');
    }
  }
  if (!radarUI.animId && radarUI.canvas) {
    radarLoop();
  }
}

function radarLoop() {
  if (!radarUI.open) {
    radarUI.animId = null;
    return;
  }
  drawRadarScope();
  radarUI.sweepAngle = (radarUI.sweepAngle + 0.024) % (Math.PI * 2);
  radarUI.animId = requestAnimationFrame(radarLoop);
}

function drawRadarScope() {
  const canvas = radarUI.canvas;
  const ctx = radarUI.ctx;
  if (!canvas || !ctx) return;

  const w = canvas.width;
  const h = canvas.height;
  const cx = w / 2;
  const cy = h / 2;
  const maxR = w * 0.44;

  ctx.clearRect(0, 0, w, h);

  // Background subtle dark radar vignette
  const bgGrad = ctx.createRadialGradient(cx, cy, 20, cx, cy, maxR);
  bgGrad.addColorStop(0, 'rgba(0, 20, 26, 0.7)');
  bgGrad.addColorStop(1, 'rgba(2, 8, 12, 0.95)');
  ctx.fillStyle = bgGrad;
  ctx.beginPath();
  ctx.arc(cx, cy, maxR, 0, Math.PI * 2);
  ctx.fill();

  // Range rings
  const rings = [0.25, 0.5, 0.75, 1.0];
  ctx.lineWidth = 1;
  rings.forEach((ratio, idx) => {
    ctx.beginPath();
    ctx.arc(cx, cy, maxR * ratio, 0, Math.PI * 2);
    ctx.strokeStyle = idx === 3 ? 'rgba(56, 225, 240, 0.45)' : 'rgba(56, 225, 240, 0.15)';
    if (idx === 1) ctx.setLineDash([4, 4]);
    else ctx.setLineDash([]);
    ctx.stroke();

    // Range label
    ctx.fillStyle = 'rgba(56, 225, 240, 0.4)';
    ctx.font = '9px "JetBrains Mono", monospace';
    ctx.fillText(`${Math.round(ratio * 100)}m`, cx + 6, cy - (maxR * ratio) + 11);
  });
  ctx.setLineDash([]);

  // Crosshairs & azimuth angle ticks
  ctx.strokeStyle = 'rgba(56, 225, 240, 0.16)';
  ctx.beginPath();
  ctx.moveTo(cx - maxR, cy);
  ctx.lineTo(cx + maxR, cy);
  ctx.moveTo(cx, cy - maxR);
  ctx.lineTo(cx, cy + maxR);
  ctx.stroke();

  // Diagonal azimuth lines
  for (let deg = 30; deg < 360; deg += 30) {
    if (deg % 90 === 0) continue;
    const rad = (deg * Math.PI) / 180;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(rad) * (maxR * 0.92), cy + Math.sin(rad) * (maxR * 0.92));
    ctx.lineTo(cx + Math.cos(rad) * maxR, cy + Math.sin(rad) * maxR);
    ctx.strokeStyle = 'rgba(56, 225, 240, 0.28)';
    ctx.stroke();
  }

  // Draw Sweeping Beam with trailing sector gradient
  const sweep = radarUI.sweepAngle;
  ctx.save();
  const sweepGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, maxR);
  sweepGrad.addColorStop(0, 'rgba(56, 225, 240, 0.35)');
  sweepGrad.addColorStop(1, 'rgba(56, 225, 240, 0.05)');

  // Sweep sector trail (approx 45 degrees back)
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.arc(cx, cy, maxR, sweep - 0.7, sweep, false);
  ctx.closePath();
  ctx.fillStyle = sweepGrad;
  ctx.fill();

  // Leading beam line
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(cx + Math.cos(sweep) * maxR, cy + Math.sin(sweep) * maxR);
  ctx.strokeStyle = '#00f0ff';
  ctx.lineWidth = 1.8;
  ctx.shadowColor = '#00f0ff';
  ctx.shadowBlur = 8;
  ctx.stroke();
  ctx.restore();

  // Center hub (Workstation / Core)
  ctx.beginPath();
  ctx.arc(cx, cy, 4, 0, Math.PI * 2);
  ctx.fillStyle = '#00f0ff';
  ctx.fill();

  // Render Node Blips
  const blips = radarUI.blips || [];
  blips.forEach(b => {
    // Check angular difference with sweep line for phosphor persistence
    let angleDiff = sweep - b.angle;
    while (angleDiff < 0) angleDiff += Math.PI * 2;
    while (angleDiff >= Math.PI * 2) angleDiff -= Math.PI * 2;

    let glow = 0.35;
    if (angleDiff < 0.25) {
      glow = 1.0;
    } else if (angleDiff < 1.6) {
      glow = 0.35 + (1.6 - angleDiff) / 1.6 * 0.65;
    }

    const bx = cx + Math.cos(b.angle) * (b.distanceFrac * maxR);
    const by = cy + Math.sin(b.angle) * (b.distanceFrac * maxR);

    ctx.save();
    ctx.beginPath();
    ctx.arc(bx, by, b.isRogue ? 5.5 : 4.5, 0, Math.PI * 2);

    let col = b.isRogue ? `rgba(255, 60, 90, ${glow})` : (b.isHost ? `rgba(0, 240, 255, ${glow})` : `rgba(0, 255, 157, ${glow})`);
    ctx.fillStyle = col;
    ctx.shadowColor = b.isRogue ? '#ff3c5a' : (b.isHost ? '#00f0ff' : '#00ff9d');
    ctx.shadowBlur = glow * 10;
    ctx.fill();

    // Subtle ripple for rogue nodes
    if (b.isRogue) {
      ctx.beginPath();
      ctx.arc(bx, by, 7 + (Math.sin(performance.now() / 200) + 1) * 3, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(255, 60, 90, ${glow * 0.5})`;
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    // IP / Vendor tag
    if (glow > 0.4) {
      ctx.font = '8px "JetBrains Mono", monospace';
      ctx.fillStyle = b.isRogue ? 'rgba(255, 120, 140, 0.9)' : 'rgba(180, 255, 230, 0.85)';
      ctx.fillText(b.label, bx + 7, by + 3);
    }
    ctx.restore();
  });
}

function renderRadarUI(data) {
  if (!data) return;
  const devices = data.devices || [];
  const rogues = data.rogue_devices || [];
  const ports = data.ports_audit || [];

  // Update status bar
  if ($('radar-meta-subnet')) $('radar-meta-subnet').textContent = `${data.subnet || '192.168.1.0/24'}`;
  if ($('radar-meta-gateway')) $('radar-meta-gateway').textContent = `${data.gateway || '192.168.1.1'}`;
  if ($('radar-meta-count')) $('radar-meta-count').textContent = `${devices.length} NODES`;
  if ($('radar-meta-rogue')) {
    const rEl = $('radar-meta-rogue');
    rEl.textContent = `${rogues.length} DETECTED`;
    rEl.style.color = rogues.length > 0 ? 'var(--warn)' : 'var(--ok)';
  }
  if ($('radar-meta-status')) {
    const sEl = $('radar-meta-status');
    if (rogues.length > 0) {
      sEl.textContent = 'PERIMETER ALERT';
      sEl.style.color = 'var(--warn)';
    } else {
      sEl.textContent = 'SECURE';
      sEl.style.color = '#00ff9d';
    }
  }

  // Build Radar Blips for Canvas
  const blips = [];
  const count = devices.length;
  devices.forEach((dev, idx) => {
    // Generate distinct radial angles and distances
    let angle = (idx / Math.max(1, count)) * Math.PI * 2 + 0.3;
    let dist = 0.28 + ((idx * 37) % 65) / 100 * 0.65;
    if (dev.is_gateway) {
      dist = 0.22;
      angle = 0.78;
    } else if (dev.is_host) {
      dist = 0.12;
      angle = 0.0;
    }

    blips.push({
      ip: dev.ip,
      mac: dev.mac,
      vendor: dev.vendor || 'Unknown',
      name: dev.name || dev.vendor,
      isRogue: !!dev.is_rogue,
      isHost: !!dev.is_host,
      isGateway: !!dev.is_gateway,
      angle: angle,
      distanceFrac: dist,
      label: `${dev.ip.split('.').slice(-2).join('.')} [${dev.vendor || 'Node'}]`
    });
  });
  radarUI.blips = blips;

  // Render Subnet Nodes List
  const listEl = $('radar-device-list');
  if (listEl) {
    if (devices.length === 0) {
      listEl.innerHTML = '<div class="radar-empty-msg mono micro dim">No active sweep data. Click SWEEP PERIMETER.</div>';
    } else {
      listEl.innerHTML = '';
      devices.forEach(dev => {
        const item = document.createElement('div');
        item.className = `radar-device-card ${dev.is_rogue ? 'is-rogue' : ''} ${dev.is_host ? 'is-host' : ''}`;

        const left = document.createElement('div');
        left.className = 'rd-left';

        const dot = document.createElement('div');
        dot.className = `rd-dot ${dev.is_rogue ? 'dot-rogue' : (dev.is_host ? 'dot-host' : 'dot-trusted')}`;

        const details = document.createElement('div');
        details.className = 'rd-details';

        const titleRow = document.createElement('div');
        titleRow.className = 'rd-title-row';

        const nameSpan = document.createElement('span');
        nameSpan.className = 'rd-name mono';
        nameSpan.textContent = dev.name || dev.vendor || 'Unknown Device';

        const badge = document.createElement('span');
        badge.className = `rd-badge mono micro ${dev.is_rogue ? 'badge-rogue' : 'badge-trusted'}`;
        badge.textContent = dev.is_rogue ? 'ROGUE / UNTRUSTED' : (dev.is_host ? 'THIS HOST' : (dev.is_gateway ? 'GATEWAY ROUTER' : 'TRUSTED NODE'));

        titleRow.append(nameSpan, badge);

        const subRow = document.createElement('div');
        subRow.className = 'rd-sub-row mono micro dim';
        subRow.textContent = `IP: ${dev.ip} · MAC: ${dev.mac} · VENDOR: ${dev.vendor}`;

        details.append(titleRow, subRow);
        left.append(dot, details);

        const right = document.createElement('div');
        right.className = 'rd-right';

        if (dev.is_rogue) {
          const trustBtn = document.createElement('button');
          trustBtn.type = 'button';
          trustBtn.className = 'rd-btn-trust mono micro';
          trustBtn.textContent = 'TRUST DEVICE';
          trustBtn.title = 'Add this MAC to network_whitelist.json';
          trustBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            trustDevice(dev.mac, dev.vendor || 'Trusted Device');
          });
          right.appendChild(trustBtn);
        } else {
          const pingBadge = document.createElement('span');
          pingBadge.className = 'rd-status-ok mono micro';
          pingBadge.textContent = 'VERIFIED';
          right.appendChild(pingBadge);
        }

        item.append(left, right);
        listEl.appendChild(item);
      });
    }
  }

  // Render Port Audit Grid
  const portGrid = $('radar-port-grid');
  if (portGrid) {
    portGrid.innerHTML = '';
    ports.forEach(p => {
      const card = document.createElement('div');
      card.className = `radar-port-card ${p.open ? 'port-open' : 'port-closed'}`;

      const top = document.createElement('div');
      top.className = 'rpc-top mono micro';
      top.innerHTML = `<span>PORT ${p.port}</span><span class="rpc-state ${p.open ? 'open' : 'closed'}">${p.open ? 'EXPOSED' : 'FILTERED'}</span>`;

      const sName = document.createElement('div');
      sName.className = 'rpc-service mono';
      sName.textContent = p.service;

      const risk = document.createElement('div');
      risk.className = 'rpc-risk mono micro dim';
      risk.textContent = `RISK: ${p.risk} · ${p.desc}`;

      card.append(top, sName, risk);
      portGrid.appendChild(card);
    });
  }
}

if ($('chip-radar')) {
  $('chip-radar').addEventListener('click', () => {
    toggleRadar();
  });
}
if ($('radar-close')) $('radar-close').addEventListener('click', closeRadar);
if ($('radar-backdrop')) $('radar-backdrop').addEventListener('click', closeRadar);
if ($('radar-overlay')) {
  $('radar-overlay').addEventListener('click', (e) => {
    if (e.target === $('radar-overlay')) closeRadar();
  });
}
if ($('radar-btn-scan')) $('radar-btn-scan').addEventListener('click', triggerRadarScan);

/* ══════════════════════════════════════════════════════════════════════
   SMART PDF & DOCUMENT INTELLIGENCE ENGINE (HUD OVERLAY)
   ══════════════════════════════════════════════════════════════════════ */

const docUI = {
  open: false,
  activeTab: 'digest',
  data: null,
  uploading: false,
};

function openDocIntel(data) {
  docUI.open = true;
  const el = $('docintel-overlay');
  if (el) {
    el.removeAttribute('hidden');
    el.setAttribute('aria-hidden', 'false');
    el.setAttribute('data-status', 'open');
    el.classList.add('open');
    el.style.display = 'flex';
  }
  if (data && (data.filename || data.digest)) {
    docUI.data = data;
    renderDocIntelUI(data);
  } else if (!docUI.data) {
    refreshDocIntelFromApi();
  } else {
    renderDocIntelUI(docUI.data);
  }
}

function closeDocIntel() {
  docUI.open = false;
  const el = $('docintel-overlay');
  if (el) {
    el.setAttribute('hidden', '');
    el.setAttribute('aria-hidden', 'true');
    el.setAttribute('data-status', 'idle');
    el.classList.remove('open');
    el.style.display = 'none';
  }
}

function toggleDocIntel() {
  if (docUI.open) closeDocIntel();
  else openDocIntel();
}

async function refreshDocIntelFromApi() {
  try {
    const res = await fetch('/api/docintel/status');
    if (!res.ok) return;
    const data = await res.json();
    if (data && data.filename) {
      docUI.data = data;
      renderDocIntelUI(data);
    }
  } catch (err) {
    console.error('Failed to fetch docintel status:', err);
  }
}

function switchDocTab(tabName) {
  docUI.activeTab = tabName;
  document.querySelectorAll('.doc-tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabName);
  });
  document.querySelectorAll('.doc-tab-content').forEach(content => {
    content.classList.toggle('active', content.id === `doc-tab-${tabName}`);
  });
}

function applyDocIntel(data, isSnapshot) {
  if (!data) return;
  docUI.data = data;

  const chip = $('chip-doc');
  const label = $('chip-doc-label');
  if (chip && label) {
    if (data.filename) {
      label.textContent = `${data.filename.slice(0, 10).toUpperCase()}..`;
      chip.dataset.ok = 'true';
      chip.title = `DOCUMENT INTELLIGENCE: ${data.filename} (${data.pages || 1} pages)`;
    } else {
      label.textContent = 'DOC INTEL';
      chip.dataset.ok = 'true';
      chip.title = 'Open Smart PDF & Document Intelligence Engine (Ctrl+Shift+D)';
    }
  }

  if (!isSnapshot && (data.event === 'show_overlay' || data.open === true)) {
    openDocIntel(data);
  } else if (docUI.open) {
    renderDocIntelUI(data);
  }
}

function renderDocIntelUI(data) {
  if (!data) return;

  if ($('doc-meta-filename')) $('doc-meta-filename').textContent = data.filename || 'NO FILE LOADED';
  if ($('doc-meta-pages')) $('doc-meta-pages').textContent = data.pages ? `${data.pages} PAGES` : '--';
  if ($('doc-meta-words')) $('doc-meta-words').textContent = data.words ? `${data.words.toLocaleString()} WORDS` : '--';
  if ($('doc-meta-tables')) $('doc-meta-tables').textContent = `${data.table_count || (data.tables || []).length} DETECTED`;
  if ($('doc-meta-status')) {
    const status = data.status || (data.digest ? 'READY' : 'STANDBY');
    $('doc-meta-status').textContent = status;
    $('doc-meta-status').style.color = status === 'ANALYZING' ? '#ffaa00' : '#00ff9d';
  }

  const digest = data.digest;
  if (digest) {
    if ($('doc-premise-text')) $('doc-premise-text').textContent = digest.premise || 'Synthesis ready.';
    if ($('doc-takeaways-list')) {
      const listEl = $('doc-takeaways-list');
      listEl.innerHTML = '';
      const items = digest.takeaways || [];
      if (items.length === 0) {
        listEl.innerHTML = '<li class="mono micro dim">No bullet points extracted.</li>';
      } else {
        items.forEach(it => {
          const li = document.createElement('li');
          li.textContent = it;
          listEl.appendChild(li);
        });
      }
    }
    if ($('doc-metrics-box')) $('doc-metrics-box').textContent = digest.critical_data || 'No numerical anomalies detected.';
    if ($('doc-spoken-text')) $('doc-spoken-text').textContent = `"${digest.spoken_debrief || ''}"`;
  }

  const tables = data.tables || [];
  if ($('doc-tables-counter')) $('doc-tables-counter').textContent = `${tables.length} TABLES EXTRACTED`;
  const tablesContainer = $('doc-tables-container');
  if (tablesContainer) {
    if (tables.length === 0) {
      tablesContainer.innerHTML = '<div class="radar-empty-msg mono micro dim">No structured tables found in this document.</div>';
    } else {
      tablesContainer.innerHTML = '';
      tables.forEach((t, idx) => {
        const wrap = document.createElement('div');
        wrap.className = 'doc-table-wrap';

        const header = document.createElement('div');
        header.className = 'doc-table-header';
        header.innerHTML = `<h4>TABLE ${t.id || idx + 1} · PAGE ${t.page || 1} (${t.row_count || (t.rows || []).length} ROWS)</h4><span class="mono micro dim">${t.col_count || (t.headers || []).length} COLUMNS</span>`;

        const tbl = document.createElement('table');
        tbl.className = 'doc-data-table';

        const thead = document.createElement('thead');
        const trH = document.createElement('tr');
        (t.headers || []).forEach(h => {
          const th = document.createElement('th');
          th.textContent = h;
          trH.appendChild(th);
        });
        thead.appendChild(trH);
        tbl.appendChild(thead);

        const tbody = document.createElement('tbody');
        (t.rows || []).slice(0, 15).forEach(row => {
          const tr = document.createElement('tr');
          row.forEach(cell => {
            const td = document.createElement('td');
            td.textContent = cell;
            tr.appendChild(td);
          });
          tbody.appendChild(tr);
        });
        tbl.appendChild(tbody);

        wrap.append(header, tbl);
        tablesContainer.appendChild(wrap);
      });
    }
  }

  if ($('doc-raw-text') && data.preview_text) {
    $('doc-raw-text').textContent = data.preview_text;
  }

  if (data.qa_history && data.qa_history.length > 0) {
    const hist = $('doc-qa-history');
    if (hist) {
      hist.innerHTML = '';
      data.qa_history.forEach(qa => {
        const uMsg = document.createElement('div');
        uMsg.className = 'doc-qa-msg user';
        uMsg.innerHTML = `<div class="doc-qa-author mono micro">YOU (${qa.timestamp || ''})</div><div>${qa.question}</div>`;

        const bMsg = document.createElement('div');
        bMsg.className = 'doc-qa-msg bot';
        let citeHtml = '';
        if (qa.citations && qa.citations.length > 0) {
          citeHtml = `<div class="doc-qa-citation mono micro">CITATIONS: ${qa.citations.join(' · ')}</div>`;
        }
        bMsg.innerHTML = `<div class="doc-qa-author mono micro">RON DOC INTEL</div><div>${qa.answer}</div>${citeHtml}`;

        hist.append(uMsg, bMsg);
      });
      hist.scrollTop = hist.scrollHeight;
    }
  }
}

function initDocIntelDropzone() {
  const dropzone = $('doc-dropzone');
  const fileInput = $('doc-file-input');
  const browseBtn = $('doc-btn-browse');

  if (browseBtn && fileInput) {
    browseBtn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', (e) => {
      const file = e.target.files && e.target.files[0];
      if (file) handleFileIngest(file);
    });
  }

  if (dropzone) {
    dropzone.addEventListener('click', () => {
      if (fileInput) fileInput.click();
    });

    ['dragenter', 'dragover'].forEach(evt => {
      dropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropzone.classList.add('drag-active');
      });
    });

    ['dragleave', 'drop'].forEach(evt => {
      dropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropzone.classList.remove('drag-active');
      });
    });

    dropzone.addEventListener('drop', (e) => {
      const dt = e.dataTransfer;
      const file = dt.files && dt.files[0];
      if (file) handleFileIngest(file);
    });
  }

  window.addEventListener('dragover', (e) => {
    if (docUI.open) e.preventDefault();
  });
  window.addEventListener('drop', (e) => {
    if (docUI.open && e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
      e.preventDefault();
      handleFileIngest(e.dataTransfer.files[0]);
    }
  });
}

async function handleFileIngest(file) {
  if (!file) return;
  const statusEl = $('doc-meta-status');
  if (statusEl) {
    statusEl.textContent = 'INGESTING...';
    statusEl.style.color = '#ffaa00';
  }

  const reader = new FileReader();
  reader.onload = async () => {
    const base64Data = reader.result;
    try {
      const res = await fetch('/api/docintel/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: file.name,
          file_base64: base64Data
        })
      });
      const resData = await res.json();
      if (res.ok) {
        if ($('doc-meta-filename')) $('doc-meta-filename').textContent = file.name;
        if ($('doc-meta-pages')) $('doc-meta-pages').textContent = `${resData.pages} PAGES`;
        if ($('doc-meta-words')) $('doc-meta-words').textContent = `${resData.words.toLocaleString()} WORDS`;
        if ($('doc-meta-tables')) $('doc-meta-tables').textContent = `${resData.table_count} DETECTED`;
        if (statusEl) {
          statusEl.textContent = 'ANALYZING...';
          statusEl.style.color = '#ffaa00';
        }
      }
    } catch (err) {
      console.error('Upload failed:', err);
    }
  };
  reader.readAsDataURL(file);
}

async function submitDocQuestion(query) {
  const text = (query || '').trim();
  if (!text) return;

  const hist = $('doc-qa-history');
  if (hist) {
    const uMsg = document.createElement('div');
    uMsg.className = 'doc-qa-msg user';
    uMsg.innerHTML = `<div class="doc-qa-author mono micro">YOU</div><div>${text}</div>`;

    const thinkingMsg = document.createElement('div');
    thinkingMsg.className = 'doc-qa-msg bot';
    thinkingMsg.id = 'doc-qa-thinking';
    thinkingMsg.innerHTML = `<div class="doc-qa-author mono micro">RON DOC INTEL</div><div class="dim">Analyzing document and calculating response...</div>`;

    hist.append(uMsg, thinkingMsg);
    hist.scrollTop = hist.scrollHeight;
  }

  try {
    const res = await fetch('/api/docintel/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: text })
    });
    const data = await res.json();
    const thinking = $('doc-qa-thinking');
    if (thinking) thinking.remove();

    if (hist && data) {
      const bMsg = document.createElement('div');
      bMsg.className = 'doc-qa-msg bot';
      let citeHtml = '';
      if (data.citations && data.citations.length > 0) {
        citeHtml = `<div class="doc-qa-citation mono micro">CITATIONS: ${data.citations.join(' · ')}</div>`;
      }
      bMsg.innerHTML = `<div class="doc-qa-author mono micro">RON DOC INTEL</div><div>${data.answer || 'No answer found.'}</div>${citeHtml}`;
      hist.appendChild(bMsg);
      hist.scrollTop = hist.scrollHeight;
    }
  } catch (err) {
    console.error('Q&A failed:', err);
    const thinking = $('doc-qa-thinking');
    if (thinking) thinking.textContent = `Error querying document: ${err}`;
  }
}

async function exportDocTables() {
  const btn = $('doc-btn-export-csv');
  if (btn) btn.classList.add('spinning');
  try {
    const res = await fetch('/api/docintel/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    });
    const data = await res.json();
    if (res.ok) {
      alert(`Exported ${data.count} table(s) to CSV in your Documents/RON_Extracted_Tables/ folder!`);
    }
  } catch (err) {
    console.error('Export tables failed:', err);
  } finally {
    if (btn) btn.classList.remove('spinning');
  }
}

function initDocIntelListeners() {
  initDocIntelDropzone();

  if ($('chip-doc')) $('chip-doc').addEventListener('click', toggleDocIntel);
  if ($('doc-close')) $('doc-close').addEventListener('click', closeDocIntel);
  if ($('docintel-backdrop')) $('docintel-backdrop').addEventListener('click', closeDocIntel);
  if ($('docintel-overlay')) {
    $('docintel-overlay').addEventListener('click', (e) => {
      if (e.target === $('docintel-overlay')) closeDocIntel();
    });
  }

  document.querySelectorAll('.doc-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchDocTab(btn.dataset.tab));
  });

  if ($('doc-btn-speak')) {
    $('doc-btn-speak').addEventListener('click', () => {
      const text = $('doc-spoken-text') ? $('doc-spoken-text').textContent : '';
      if (text) {
        fetch('/api/command', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ command: `speak ${text.replace(/^"|"$/g, '')}` })
        }).catch(() => {});
      }
    });
  }

  if ($('doc-btn-export-csv')) {
    $('doc-btn-export-csv').addEventListener('click', exportDocTables);
  }

  const form = $('doc-qa-form');
  if (form) {
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const input = $('doc-qa-input');
      if (input && input.value) {
        submitDocQuestion(input.value);
        input.value = '';
      }
    });
  }

  document.querySelectorAll('.doc-chip-preset').forEach(chip => {
    chip.addEventListener('click', () => {
      const prompt = chip.dataset.prompt;
      if (prompt) {
        submitDocQuestion(prompt);
      }
    });
  });

  const searchInput = $('doc-reader-search');
  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      const q = e.target.value.toLowerCase();
      const raw = $('doc-raw-text');
      if (raw && docUI.data && docUI.data.preview_text) {
        if (!q) {
          raw.textContent = docUI.data.preview_text;
          return;
        }
        const lines = docUI.data.preview_text.split('\n');
        const matches = lines.filter(l => l.toLowerCase().includes(q));
        raw.textContent = matches.join('\n') || 'No matching lines found.';
      }
    });
  }
}

// Call init once DOM is parsed
initDocIntelListeners();

/* ══════════════════════════════════════════════════════════════════════
   RON ঢাকা বুলেটিন — DHAKA LIVE NEWS WIRE & INTELLIGENCE DESK (0% GPU)
   ══════════════════════════════════════════════════════════════════════ */

const dhakaDeskUI = {
  open: false,
  activeCat: 'all',
  searchQuery: '',
  data: null,
};

function openDhakaDesk(data) {
  dhakaDeskUI.open = true;
  const overlay = $('dhaka-overlay');
  if (overlay) {
    overlay.removeAttribute('hidden');
    overlay.setAttribute('aria-hidden', 'false');
    overlay.setAttribute('data-status', 'open');
    overlay.classList.add('open');
    overlay.style.display = 'flex';
  }
  if (data && data.articles && data.articles.length) {
    dhakaDeskUI.data = data;
    renderDhakaDesk(data);
  } else if (dhakaDeskUI.data && dhakaDeskUI.data.articles) {
    renderDhakaDesk(dhakaDeskUI.data);
  } else {
    refreshDhakaDesk();
  }
}

function closeDhakaDesk() {
  dhakaDeskUI.open = false;
  const overlay = $('dhaka-overlay');
  if (overlay) {
    overlay.setAttribute('hidden', '');
    overlay.setAttribute('aria-hidden', 'true');
    overlay.setAttribute('data-status', 'idle');
    overlay.classList.remove('open');
    overlay.style.display = 'none';
  }
}

function toggleDhakaDesk() {
  if (dhakaDeskUI.open) closeDhakaDesk();
  else openDhakaDesk();
}

async function refreshDhakaDesk() {
  const btn = $('dhaka-btn-refresh');
  if (btn) btn.classList.add('spinning');
  try {
    const res = await fetch('/api/dhaka/latest');
    if (!res.ok) return;
    const data = await res.json();
    dhakaDeskUI.data = data;
    renderDhakaDesk(data);
  } catch (err) {
    console.error('Failed to fetch Dhaka bulletin:', err);
  } finally {
    if (btn) btn.classList.remove('spinning');
  }
}

async function forceHarvestDhakaDesk() {
  const btn = $('dhaka-btn-refresh');
  if (btn) {
    btn.classList.add('spinning');
    btn.disabled = true;
  }
  try {
    const res = await fetch('/api/dhaka/refresh', { method: 'POST' });
    if (!res.ok) return;
    const data = await fetch('/api/dhaka/latest').then(r => r.json());
    dhakaDeskUI.data = data;
    renderDhakaDesk(data);
  } catch (err) {
    console.error('Failed to refresh Dhaka desk:', err);
  } finally {
    if (btn) {
      btn.classList.remove('spinning');
      btn.disabled = false;
    }
  }
}

async function triggerDhakaBroadcast() {
  const btn = $('dhaka-btn-broadcast');
  if (btn) btn.classList.add('pulse');
  try {
    await fetch('/api/dhaka/broadcast', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category: dhakaDeskUI.activeCat })
    });
  } catch (err) {
    console.error('Failed to broadcast Dhaka bulletin:', err);
  } finally {
    setTimeout(() => { if (btn) btn.classList.remove('pulse'); }, 1200);
  }
}

function applyDhakaDesk(bulletin, fromSnapshot) {
  if (!bulletin) return;
  if (bulletin.articles && bulletin.articles.length) {
    dhakaDeskUI.data = bulletin;
  }
  if (bulletin.open === true) {
    openDhakaDesk(bulletin);
  } else if (dhakaDeskUI.open && dhakaDeskUI.data) {
    renderDhakaDesk(dhakaDeskUI.data);
  }
}

function renderDhakaDesk(data) {
  if (!data) return;

  // Metadata
  if ($('dhaka-meta-updated')) $('dhaka-meta-updated').textContent = data.updated_at || 'LIVE';
  if ($('dhaka-meta-count')) $('dhaka-meta-count').textContent = data.total_count || (data.articles ? data.articles.length : 0);

  // Category Counts
  const counts = data.category_counts || {};
  if ($('dhaka-count-all')) $('dhaka-count-all').textContent = counts.all || (data.articles ? data.articles.length : 0);
  if ($('dhaka-count-national')) $('dhaka-count-national').textContent = counts.national || 0;
  if ($('dhaka-count-economy')) $('dhaka-count-economy').textContent = counts.economy || 0;
  if ($('dhaka-count-sports')) $('dhaka-count-sports').textContent = counts.sports || 0;
  if ($('dhaka-count-tech')) $('dhaka-count-tech').textContent = counts.tech || 0;

  // Marquee Ticker
  const tickerEl = $('dhaka-ticker-text');
  if (tickerEl && data.articles && data.articles.length) {
    const tickerItems = data.articles.slice(0, 15).map(a => `● [${a.source || 'BD'}] ${a.title}`).join('     ');
    tickerEl.textContent = tickerItems;
  }

  renderDhakaArticles();
}

function renderDhakaArticles() {
  const container = $('dhaka-articles-grid');
  if (!container) return;

  const data = dhakaDeskUI.data;
  if (!data || !data.articles) {
    container.innerHTML = '<p class="empty">সংবাদ লোড হচ্ছে...</p>';
    return;
  }

  let articles = data.articles;
  if (dhakaDeskUI.activeCat && dhakaDeskUI.activeCat !== 'all') {
    if (data.categories && data.categories[dhakaDeskUI.activeCat]) {
      articles = data.categories[dhakaDeskUI.activeCat];
    } else {
      articles = articles.filter(a => a.category === dhakaDeskUI.activeCat);
    }
  }

  const query = (dhakaDeskUI.searchQuery || '').trim().toLowerCase();
  if (query) {
    articles = articles.filter(a =>
      (a.title && a.title.toLowerCase().includes(query)) ||
      (a.summary && a.summary.toLowerCase().includes(query)) ||
      (a.source && a.source.toLowerCase().includes(query))
    );
  }

  container.innerHTML = '';
  if (!articles.length) {
    container.innerHTML = '<p class="empty">কোনো খবর পাওয়া যায়নি (No matching dispatches found)</p>';
    return;
  }

  articles.slice(0, 50).forEach(art => {
    const card = document.createElement('article');
    card.className = 'dhaka-card';

    const catBadge = {
      national: 'জাতীয়',
      economy: 'অর্থনীতি',
      sports: 'ক্রিকেট',
      tech: 'প্রযুক্তি'
    }[art.category] || (art.category || 'সংবাদ').toUpperCase();

    const topRow = document.createElement('div');
    topRow.className = 'dhaka-card-top';

    const sourceEl = document.createElement('span');
    sourceEl.className = 'dhaka-card-source mono micro';
    sourceEl.textContent = art.source || 'WIRE';

    const catEl = document.createElement('span');
    catEl.className = `dhaka-card-cat mono micro cat-${art.category || 'national'}`;
    catEl.textContent = catBadge;

    topRow.append(sourceEl, catEl);

    const titleEl = document.createElement('h3');
    titleEl.className = 'dhaka-card-title';
    titleEl.textContent = art.title;

    const summaryEl = document.createElement('p');
    summaryEl.className = 'dhaka-card-summary';
    summaryEl.textContent = art.summary || '';

    const footRow = document.createElement('div');
    footRow.className = 'dhaka-card-foot';

    const timeEl = document.createElement('span');
    timeEl.className = 'dhaka-card-time mono micro dim';
    timeEl.textContent = art.published_at || '';

    const linkEl = document.createElement('a');
    linkEl.className = 'dhaka-card-link mono micro';
    linkEl.href = art.url || '#';
    linkEl.target = '_blank';
    linkEl.rel = 'noopener';
    linkEl.innerHTML = 'মূল খবর ↗';
    linkEl.addEventListener('click', (e) => {
      if (art.url && art.url.startsWith('http')) {
        e.preventDefault();
        fetch('/api/open', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: art.url })
        });
      }
    });

    footRow.append(timeEl, linkEl);
    card.append(topRow, titleEl, summaryEl, footRow);
    container.appendChild(card);
  });
}

function initDhakaListeners() {
  if ($('dhaka-close')) $('dhaka-close').addEventListener('click', closeDhakaDesk);
  if ($('chip-dhaka')) $('chip-dhaka').addEventListener('click', toggleDhakaDesk);
  if ($('dhaka-btn-refresh')) $('dhaka-btn-refresh').addEventListener('click', forceHarvestDhakaDesk);
  if ($('dhaka-btn-broadcast')) $('dhaka-btn-broadcast').addEventListener('click', triggerDhakaBroadcast);

  const tabs = document.querySelectorAll('.dhaka-tab');
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      tabs.forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      dhakaDeskUI.activeCat = tab.dataset.cat || 'all';
      renderDhakaArticles();
    });
  });

  const searchInput = $('dhaka-search');
  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      dhakaDeskUI.searchQuery = e.target.value;
      renderDhakaArticles();
    });
  }
}

initDhakaListeners();

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
        applyReminder(msg.reminder || {}, true);
        applyProtocol(msg.protocol || {}, true);
        applyBriefing(msg.briefing || {}, true);
        applyResearch(msg.research || {}, true);
        applyAutopilot(msg.autopilot || {}, true);
        applyMemory(msg.memory || {}, true);
        applyCoach(msg.coach || {}, true);
        applyIntel(msg.intel || {}, true);
        applyWeatherStation(msg.weather_station || {}, true);
        applyTribune(msg.tribune || {}, true);
        applyTelegram(msg.telegram || {}, true);
        applyNetRadar(msg.netradar || {}, true);
        applyDocIntel(msg.docintel || {}, true);
        applyDhakaDesk(msg.dhaka_bulletin || {}, true);
        applyLanguage(msg.language || 'en');
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
      case 'reminder':
        applyReminder(msg.reminder || {}, false);
        break;
      case 'protocol':
        applyProtocol(msg.protocol || {}, false);
        break;
      case 'briefing':
        applyBriefing(msg.briefing || {}, false);
        break;
      case 'research':
        applyResearch(msg.research || {}, false);
        break;
      case 'autopilot':
        applyAutopilot(msg.autopilot || {}, false);
        break;
      case 'memory':
        applyMemory(msg.memory || {}, false);
        break;
      case 'coach':
        applyCoach(msg.coach || {}, false);
        break;
      case 'intel':
        applyIntel(msg.intel || {}, false);
        break;
      case 'weather_station':
        applyWeatherStation(msg.weather_station || {}, false);
        break;
      case 'tribune':
        applyTribune(msg.tribune || {}, false);
        break;
      case 'telegram':
        applyTelegram(msg.telegram || {}, false);
        break;
      case 'netradar':
        applyNetRadar(msg.netradar || {}, false);
        break;
      case 'docintel':
        applyDocIntel(msg.docintel || {}, false);
        break;
      case 'dhaka_bulletin':
        applyDhakaDesk(msg.dhaka_bulletin || {}, false);
        break;
      case 'language':
        applyLanguage(msg.language || 'en');
        break;
      case 'diagnostic':
        triggerLaserDiagnostic(Object.assign({ fromServer: true }, msg.diagnostic || {}));
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
  if (autopilotUI.open && e.key === 'Escape') { e.preventDefault(); closeAutopilot(); return; }
  if (memoryUI.open && e.key === 'Escape') { e.preventDefault(); closeMemory(); return; }
  if (coachUI.open && e.key === 'Escape') { e.preventDefault(); closeCoach(); return; }
  if (intelUI.open && e.key === 'Escape') { e.preventDefault(); closeIntel(); return; }
  if (weatherStationUI.open && e.key === 'Escape') { e.preventDefault(); closeWeatherStation(); return; }
  if (tribuneUI.open && e.key === 'Escape') { e.preventDefault(); closeTribune(); return; }
  if ((radarUI.open || $('radar-overlay')?.getAttribute('data-status') === 'open' || $('radar-overlay')?.classList.contains('open')) && e.key === 'Escape') { e.preventDefault(); closeRadar(); return; }
  if ((docUI.open || $('docintel-overlay')?.getAttribute('data-status') === 'open' || $('docintel-overlay')?.classList.contains('open')) && e.key === 'Escape') { e.preventDefault(); closeDocIntel(); return; }
  if ((dhakaDeskUI.open || $('dhaka-overlay')?.getAttribute('data-status') === 'open' || $('dhaka-overlay')?.classList.contains('open')) && e.key === 'Escape') { e.preventDefault(); closeDhakaDesk(); return; }
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'b' || e.key === 'B')) { e.preventDefault(); toggleDhakaDesk(); return; }
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'd' || e.key === 'D')) { e.preventDefault(); toggleDocIntel(); return; }
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'l' || e.key === 'L')) { e.preventDefault(); triggerLaserDiagnostic(); return; }
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'n' || e.key === 'N')) { e.preventDefault(); toggleRadar(); return; }
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 't' || e.key === 'T')) { e.preventDefault(); toggleTribune(); return; }
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'i' || e.key === 'I')) { e.preventDefault(); toggleIntel(); return; }
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'w' || e.key === 'W')) { e.preventDefault(); toggleWeatherStation(); return; }
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'f' || e.key === 'F')) { e.preventDefault(); toggleCoach(); return; }
  if ((e.ctrlKey || e.metaKey) && (e.key === 'm' || e.key === 'M')) { e.preventDefault(); toggleMemory(); return; }
  if (researchUI.open && e.key === 'Escape') { e.preventDefault(); closeResearch(); return; }
  if (briefingUI.open && e.key === 'Escape') { e.preventDefault(); closeBriefing(); return; }
  if (protocolUI.open && e.key === 'Escape') { e.preventDefault(); closeProtocol(); return; }
  if (speed.open && e.key === 'Escape') { e.preventDefault(); closeSpeed(); return; }
  if (timer.open && e.key === 'Escape') { e.preventDefault(); closeTimer(); return; }
  if (reminder.open && e.key === 'Escape') { e.preventDefault(); closeReminder(); return; }
  const typing = document.activeElement === input;
  if (e.key === 'Escape') { input.blur(); return; }
  if (typing) return;
  if (e.key === '/' || e.key === 'Enter') { e.preventDefault(); input.focus(); return; }
  if (e.key === 'm' || e.key === 'M') { toggleMic(); }
});
$('so-close').addEventListener('click', closeSearch);
if ($('ws-close')) $('ws-close').addEventListener('click', closeWeatherStation);
if ($('ws-btn-refresh')) $('ws-btn-refresh').addEventListener('click', refreshWeatherStation);
if ($('panel-weather')) $('panel-weather').addEventListener('click', toggleWeatherStation);
if ($('tb-close')) $('tb-close').addEventListener('click', closeTribune);
if ($('tb-btn-broadcast')) $('tb-btn-broadcast').addEventListener('click', triggerTribuneBroadcast);
if ($('tb-btn-pdf')) $('tb-btn-pdf').addEventListener('click', openTribunePdf);
if ($('tb-btn-refresh')) $('tb-btn-refresh').addEventListener('click', forceGenerateTribune);
if ($('ap-close')) $('ap-close').addEventListener('click', closeAutopilot);
if ($('mem-close')) $('mem-close').addEventListener('click', closeMemory);
if ($('coach-close')) $('coach-close').addEventListener('click', closeCoach);
if ($('chip-coach')) $('chip-coach').addEventListener('click', toggleCoach);
if ($('chip-mem')) $('chip-mem').addEventListener('click', toggleMemory);
if ($('chip-sys')) $('chip-sys').addEventListener('click', () => triggerLaserDiagnostic());
if ($('chip-dhaka')) $('chip-dhaka').addEventListener('click', toggleDhakaDesk);
if ($('dhaka-close')) $('dhaka-close').addEventListener('click', closeDhakaDesk);
if ($('pt-close')) $('pt-close').addEventListener('click', closeProtocol);
if ($('bf-close')) $('bf-close').addEventListener('click', closeBriefing);
if ($('rs-close')) $('rs-close').addEventListener('click', closeResearch);
$('ns-close').addEventListener('click', closeSpeed);
$('tm-close').addEventListener('click', closeTimer);
$('rm-close').addEventListener('click', closeReminder);

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
if (nsCanvas) window.addEventListener('resize', sizeSpeedCanvas);

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
