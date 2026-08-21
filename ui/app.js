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
}

/* ── feeds ────────────────────────────────────────────────────────────── */

function nearBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 48;
}

function addTurn(entry) {
  const box = $('conversation');
  const empty = box.querySelector('.empty');
  if (empty) empty.remove();
  const stick = nearBottom(box);

  const div = document.createElement('div');
  div.className = `turn ${entry.role === 'ron' ? 'ron' : 'user'}`;
  const who = document.createElement('div');
  who.className = 'who';
  who.textContent = entry.role === 'ron' ? 'RON' : 'YOU';
  const p = document.createElement('p');
  p.textContent = entry.text;            // textContent, never innerHTML
  div.append(who, p);
  box.append(div);

  while (box.children.length > 14) box.firstElementChild.remove();
  if (stick) box.scrollTop = box.scrollHeight;
}

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
        $('conversation').innerHTML = '';
        $('activity').innerHTML = '';
        (msg.transcript || []).forEach(addTurn);
        (msg.activity || []).forEach(addActivity);
        if (!(msg.transcript || []).length) {
          $('conversation').innerHTML = '<p class="empty">AWAITING FIRST EXCHANGE</p>';
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
  const typing = document.activeElement === input;
  if (e.key === 'Escape') { input.blur(); return; }
  if (typing) return;
  if (e.key === '/' || e.key === 'Enter') { e.preventDefault(); input.focus(); return; }
  if (e.key === 'm' || e.key === 'M') { toggleMic(); }
});

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
