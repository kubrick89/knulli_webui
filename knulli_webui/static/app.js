/* Knulli WebUI - client */
'use strict';

/* ======================================================================
 * Utility
 * ==================================================================== */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const norm = (s) => String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
const LOCALE = 'en-US';
const collator = new Intl.Collator('en', { sensitivity: 'base', numeric: true });

function fmtBytes(n) {
  if (n == null || isNaN(n)) return '–';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0; n = +n;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i ? n.toFixed(n < 10 ? 2 : n < 100 ? 1 : 0) : n) + ' ' + u[i];
}
const fmtRate = (n) => fmtBytes(n) + '/s';
function fmtHz(hz) {
  if (!hz) return '–';
  return hz >= 1e9 ? (hz / 1e9).toFixed(2) + ' GHz' : Math.round(hz / 1e6) + ' MHz';
}
function fmtDuration(s) {
  s = Math.max(0, Math.floor(s || 0));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h ${m}m`;
  if (h) return `${h}h ${m}m`;
  return `${m}m ${s % 60}s`;
}
function fmtDate(iso, withTime) {
  if (!iso) return '–';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return withTime ? d.toLocaleString(LOCALE, { dateStyle: 'medium', timeStyle: 'short' }) : d.toLocaleDateString(LOCALE, { dateStyle: 'medium' });
}
const pct = (v) => (v == null ? '–' : Math.round(v) + '%');
const level = (v, warn, crit) => (v == null ? '' : v >= crit ? 'crit' : v >= warn ? 'warn' : 'ok');
function hue(str) { let h = 0; for (const c of String(str)) h = (h * 31 + c.charCodeAt(0)) % 360; return h; }
const avatarBg = (name) => `linear-gradient(135deg, hsl(${hue(name)} 55% 45%), hsl(${(hue(name) + 40) % 360} 60% 32%))`;
/* System icons (from /api/systems and /api/roms), by system folder name */
const ICONS = {};
const rememberIcons = (systems) => { for (const s of systems || []) if (s.icon) ICONS[s.name] = s.icon; };
const sysIcon = (system, cls = 'sys-ico') => (ICONS[system] ? `<img class="${cls}" src="${esc(ICONS[system])}" alt="">` : '');
/* Cover placeholder: the system icon on a coloured background, or the system name when there is no icon */
const placeholder = (system, cls = '') => `<div class="ph ${cls}${ICONS[system] ? ' has-icon' : ''}" style="background:${avatarBg(system)}">`
  + (ICONS[system] ? `<img src="${esc(ICONS[system])}" alt=""><span>${esc(system)}</span>` : esc(system)) + '</div>';
const stars = (r) => (r == null ? '–' : '<span class="stars">' + '★'.repeat(Math.round(r)) + '☆'.repeat(5 - Math.round(r)) + '</span> ' + r.toFixed(1));

const store = {
  get(k, d) { try { const v = localStorage.getItem('kw.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('kw.' + k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};

async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: { 'X-Requested-With': 'knulli-webui', ...(opts.headers || {}) } });
  let data = null;
  try { data = await res.json(); } catch { /* empty response */ }
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
  return data;
}

function toast(msg, type = 'info', ms = 4500) {
  const t = document.createElement('div');
  t.className = 'toast ' + type;
  t.textContent = msg;
  $('#toasts').appendChild(t);
  setTimeout(() => t.remove(), ms);
}

/* Modal prompt: resolves to the entered text (or true for a confirmation), or null when cancelled */
function promptDialog({ title, text = '', value = null, hint = '', ok = 'OK', danger = false }) {
  return new Promise((resolve) => {
    const dlg = $('#dlg-prompt');
    const input = $('#prompt-input');
    let result = null;
    $('#prompt-title').textContent = title;
    $('#prompt-text').innerHTML = text;
    $('#prompt-text').hidden = !text;
    $('#prompt-hint').textContent = hint;
    input.hidden = value === null;
    input.value = value ?? '';
    $('#prompt-ok').textContent = ok;
    $('#prompt-ok').className = 'btn ' + (danger ? 'danger' : 'primary');
    const accept = () => { result = value === null ? true : input.value.trim(); dlg.close(); };
    $('#prompt-ok').onclick = accept;
    $('#prompt-cancel').onclick = () => dlg.close();
    input.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); accept(); } };
    dlg.onclose = () => resolve(result);
    dlg.showModal();
    if (value !== null) { input.focus(); input.select(); }
  });
}

/* ======================================================================
 * Theme and authentication setup (both saved in config.json)
 * ==================================================================== */
const ICON_SUN = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
const ICON_MOON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
const systemDark = window.matchMedia('(prefers-color-scheme: dark)');

const Theme = {
  /* The theme shown now: the one chosen, or the system's when it is "auto" */
  current() {
    const t = document.documentElement.dataset.theme;
    return t === 'light' || t === 'dark' ? t : systemDark.matches ? 'dark' : 'light';
  },

  apply(theme) {
    if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;
    else delete document.documentElement.dataset.theme;
    const dark = this.current() === 'dark';
    $('#theme-toggle').innerHTML = dark ? ICON_SUN : ICON_MOON;
    $('#theme-toggle').title = dark ? 'Switch to the light theme' : 'Switch to the dark theme';
  },

  async toggle() {
    const before = document.documentElement.dataset.theme;
    const theme = this.current() === 'dark' ? 'light' : 'dark';
    this.apply(theme);
    try {
      await api('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ theme }) });
    } catch (e) {
      toast('Could not save the theme: ' + e.message, 'err');
      this.apply(before);
    }
  },
};

const AuthSetup = {
  show() {
    $('#auth-error').hidden = true;
    $('#dlg-auth').showModal();
    $('#auth-user').focus();
  },

  error(msg) { $('#auth-error').textContent = msg; $('#auth-error').hidden = false; },

  async submit() {
    const username = $('#auth-user').value.trim(), password = $('#auth-pass').value;
    if (username.includes(':')) return this.error('The username cannot contain ":".');
    if (password !== $('#auth-pass2').value) return this.error('The two passwords do not match.');
    try {
      await api('/api/auth-setup', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }) });
    } catch (e) {
      return this.error(e.message);
    }
    $('#dlg-auth').close();
    toast('Authentication enabled: log in with the new credentials', 'ok');
    // the browser asks for the credentials as soon as the server answers 401
    setTimeout(() => location.reload(), 1500);
  },

  async never() {
    try {
      await api('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ authPrompt: false }) });
      $('#dlg-auth').close();
    } catch (e) {
      this.error(e.message);
    }
  },
};

function setConn(ok, text) {
  $('#conn').className = 'conn ' + (ok ? 'ok' : 'err');
  $('#conn-text').textContent = text;
}

/* ======================================================================
 * Tabs
 * ==================================================================== */
const App = { config: { allowUpload: true, allowDelete: true } };

/* Called when a tab is shown/hidden; the other scripts (stats.js, saves.js…) add their own */
const TAB_HOOKS = {
  dashboard: { show: () => Dash.start(), hide: () => Dash.stop() },
  roms: { show: () => Roms.ensureLoaded() },
};
let currentTab = null;

function showTab(name) {
  if (!TAB_HOOKS[name]) name = 'dashboard';
  $$('.tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  $$('.tab-panel').forEach((p) => (p.hidden = p.id !== 'tab-' + name));
  if (location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
  if (currentTab && currentTab !== name) TAB_HOOKS[currentTab].hide?.();
  currentTab = name;
  TAB_HOOKS[name].show?.();
}

/* ======================================================================
 * Dashboard
 * ==================================================================== */
const Dash = {
  timer: null,
  stats: null,
  statsAt: 0,

  start() {
    if (this.timer) return;
    const tick = async () => {
      try {
        const d = await api('/api/telemetry');
        this.render(d);
        setConn(true, 'Connected');
      } catch (e) {
        setConn(false, 'Disconnected');
      }
      if (Date.now() - this.statsAt > 60000) this.loadStats();
    };
    tick();
    this.timer = setInterval(() => { if (!document.hidden) tick(); }, 2000);
  },
  stop() { clearInterval(this.timer); this.timer = null; },

  async loadStats() {
    this.statsAt = Date.now();
    try {
      const { systems } = await api('/api/systems');
      this.stats = systems;
      rememberIcons(systems);
      this.renderLibrary();
    } catch { this.statsAt = 0; }
  },

  spark(values, max, color) {
    const v = values.map((x) => (x == null ? null : x));
    if (v.filter((x) => x != null).length < 2) return '<svg class="spark"></svg>';
    const mx = max || Math.max(1, ...v.filter((x) => x != null)) * 1.15;
    const n = v.length - 1;
    const pts = v.map((x, i) => [(i / n) * 100, 30 - (Math.min(x ?? 0, mx) / mx) * 28]);
    const line = pts.map((p) => p[0].toFixed(2) + ',' + p[1].toFixed(2)).join(' ');
    return `<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none">
      <polygon points="0,30 ${line} 100,30" fill="${color}" opacity=".16"/>
      <polyline points="${line}" fill="none" stroke="${color}" stroke-width="1.6" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>
    </svg>`;
  },

  meter(label, value, percent, cls) {
    const p = Math.max(0, Math.min(100, percent || 0));
    return `<div class="meter"><div class="top"><span>${label}</span><span>${value}</span></div>
      <div class="bar"><span class="${cls ?? level(p, 75, 90)}" style="width:${p}%"></span></div></div>`;
  },

  kv(rows) {
    return '<dl class="kv">' + rows.filter((r) => r && r[1] !== '' && r[1] != null).map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('') + '</dl>';
  },

  render(d) {
    const h = d.history || [];
    const s = d.system;
    $('#hostname').textContent = s.hostname ? '· ' + s.hostname : '';
    const bat = (d.power.batteries || [])[0];
    const userdata = (d.storage || [])[0];
    const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

    const kpis = [
      { label: 'CPU', value: pct(d.cpu.usage), cls: level(d.cpu.usage, 70, 90),
        sub: d.cpu.cores.length ? `${d.cpu.cores.length} core · ${fmtHz(Math.max(...d.cpu.cores.map((c) => c.freq || 0)))}` : '',
        spark: this.spark(h.map((p) => p.cpu), 100, cssVar('--accent')) },
      { label: 'Memory', value: pct(d.memory.percent), cls: level(d.memory.percent, 80, 92),
        sub: `${fmtBytes(d.memory.used)} / ${fmtBytes(d.memory.total)}`,
        spark: this.spark(h.map((p) => p.mem), 100, cssVar('--info')) },
      { label: 'Temperature', value: d.temp_max != null ? d.temp_max.toFixed(1) + ' °C' : '–', cls: level(d.temp_max, 70, 85),
        sub: d.temps.length ? `${d.temps.length} sensors` : 'no sensors',
        spark: this.spark(h.map((p) => p.temp), Math.max(90, ...h.map((p) => p.temp || 0)), cssVar('--warn')) },
      { label: 'Battery', value: bat && bat.capacity != null ? bat.capacity + '%' : 'N/A',
        cls: bat && bat.capacity != null && bat.status !== 'Charging' ? (bat.capacity <= 10 ? 'crit' : bat.capacity <= 20 ? 'warn' : '') : '',
        sub: bat ? translateStatus(bat.status) + (bat.power ? ` · ${bat.power} W` : '') : (d.power.external ? 'External power' : ''),
        spark: '' },
      { label: 'Network', value: fmtRate((h.at(-1)?.rx || 0) + (h.at(-1)?.tx || 0)), cls: '',
        sub: d.network.primary_ip || 'not connected',
        spark: this.spark(h.map((p) => (p.rx || 0) + (p.tx || 0)), 0, cssVar('--ok')) },
      { label: 'Free space', value: userdata ? fmtBytes(userdata.free) : '–', cls: userdata ? level(userdata.percent, 85, 95) : '',
        sub: userdata ? `${userdata.percent}% used of ${fmtBytes(userdata.total)}` : '',
        spark: userdata ? `<div class="bar" style="margin-top:20px"><span class="${level(userdata.percent, 85, 95)}" style="width:${userdata.percent}%"></span></div>` : '' },
    ];
    $('#kpis').innerHTML = kpis.map((k) => `<div class="kpi ${k.cls}"><div class="label">${k.label}</div>
      <div class="value">${k.value}</div><div class="sub">${esc(k.sub)}</div>${k.spark}</div>`).join('');

    $('#card-system .body').innerHTML = this.kv([
      ['Device', esc(s.model || s.board || '–')],
      ['OS', esc(s.os)],
      ['Version', esc(s.version)],
      ['Hostname', esc(s.hostname)],
      ['Kernel', esc(s.kernel) + ` <span class="muted">(${esc(s.arch)})</span>`],
      ['CPU', esc(s.cpu_model || '–') + ` <span class="muted">· ${s.cpu_count} core</span>`],
      ['Uptime', fmtDuration(s.uptime)],
      ['Load average', s.load.length ? s.load.join(' · ') : '–'],
      ['Processes', s.processes],
      ['Device time', new Date(s.time * 1000).toLocaleString(LOCALE)],
      ['Knulli WebUI', esc(s.webui_version || '–')],
    ]);

    const a = d.activity;
    let act;
    if (a.game || a.emulator) {
      const g = a.game || {};
      const em = a.emulator || {};
      act = `<div class="now-playing">${ICON_PAD}<div>
        <div class="muted">Now playing</div>
        <div class="title">${esc(g.name || em.rom_name || 'Game running')}</div>
        <div class="muted">${esc([g.system, em.name, em.core].filter(Boolean).join(' · '))}</div></div></div>`;
    } else {
      act = `<div class="now-playing">${ICON_PAD.replace('icon', 'icon idle')}<div>
        <div class="title">${a.es_running ? 'No game running' : 'EmulationStation is not running'}</div>
        <div class="muted">${a.es_running ? 'EmulationStation is in the main menu' : ''}</div></div></div>`;
    }
    $('#card-activity .body').innerHTML = act + this.kv([
      ['EmulationStation', a.es_running ? '<span class="pill ok">running</span>' : '<span class="pill">stopped</span>'],
      ['Detected via', a.es_source ? esc(a.es_source) : null],
    ]).replace('<dl', '<dl style="margin-top:12px"');

    const cores = d.cpu.cores.map((c) => `<div class="core"><div class="top"><span>${esc(c.id)}</span><span>${c.online ? pct(c.usage) : 'off'}</span></div>
      <div class="bar"><span class="${level(c.usage, 70, 90)}" style="width:${c.usage || 0}%"></span></div>
      <div class="muted">${fmtHz(c.freq)}${c.max ? ' / ' + fmtHz(c.max) : ''}</div></div>`).join('');
    const govs = [...new Set(d.cpu.cores.map((c) => c.governor).filter(Boolean))];
    $('#card-cpu .body').innerHTML = this.meter('Total usage', pct(d.cpu.usage), d.cpu.usage)
      + `<div class="cores">${cores}</div>`
      + this.kv([['Governor', esc(govs.join(', ') || '–')]]).replace('<dl', '<dl style="margin-top:10px"')
      + (d.gpus.length ? '<div class="sub-h">GPU / devfreq</div>' + this.kv(d.gpus.map((g) => [esc(g.name.replace(/^[0-9a-f]+\./, '')),
        `${fmtHz(g.freq)}${g.max ? ' / ' + fmtHz(g.max) : ''} <span class="muted">${esc(g.governor)}</span>`])) : '');

    const m = d.memory;
    $('#card-memory .body').innerHTML = this.meter('RAM', `${fmtBytes(m.used)} / ${fmtBytes(m.total)}`, m.percent)
      + (m.swap_total ? this.meter('Swap', `${fmtBytes(m.swap_used)} / ${fmtBytes(m.swap_total)}`, (100 * m.swap_used) / m.swap_total) : '')
      + this.kv([['Available', fmtBytes(m.available)], ['Cache', fmtBytes(m.cached)], ['Buffers', fmtBytes(m.buffers)]]);

    let pw = '';
    for (const b of d.power.batteries) {
      pw += this.meter(`Battery <span class="muted">${esc(translateStatus(b.status))}</span>`, pct(b.capacity), b.capacity,
        b.capacity <= 10 ? 'crit' : b.capacity <= 20 ? 'warn' : 'ok');
      pw += this.kv([
        ['Voltage', b.voltage != null ? b.voltage.toFixed(2) + ' V' : null],
        ['Current', b.current != null ? Math.abs(b.current * 1000).toFixed(0) + ' mA' : null],
        ['Power', b.power != null ? b.power + ' W' : null],
        ['Temperature', b.temp != null ? b.temp + ' °C' : null],
        ['Health', b.health ? esc(b.health) : null],
      ]);
    }
    if (!d.power.batteries.length) pw += '<p class="muted">No battery detected.</p>';
    if (d.power.external != null) pw += this.kv([['Charger', d.power.external ? '<span class="pill ok">connected</span>' : '<span class="pill">disconnected</span>']]).replace('<dl', '<dl style="margin-top:6px"');
    if (d.temps.length) {
      pw += '<div class="sub-h">Temperature sensors</div>'
        + d.temps.map((t) => this.meter(esc(t.name), t.temp.toFixed(1) + ' °C', t.temp, level(t.temp, 70, 85))).join('');
    }
    $('#card-power .body').innerHTML = pw;

    const ifs = d.network.interfaces;
    $('#card-network .body').innerHTML = ifs.length ? ifs.map((i) => `<div class="iface">
        <div><span class="name">${esc(i.name)}</span> <span class="pill ${i.state === 'up' ? 'ok' : ''}">${esc(i.state)}</span>
        ${i.wireless ? `<span class="muted">· ${esc(i.wireless.ssid || 'Wi-Fi')} · ${i.wireless.level} dBm</span>` : ''}</div>
        ${this.kv([
          ['Addresses', esc(i.addresses.join(', ') || '–')],
          ['MAC', esc(i.mac)],
          ['Traffic', `↓ ${fmtRate(i.rx_rate)} · ↑ ${fmtRate(i.tx_rate)}`],
          ['Total', `↓ ${fmtBytes(i.rx_total)} · ↑ ${fmtBytes(i.tx_total)}`],
        ])}</div>`).join('') : '<p class="muted">No active network interface.</p>';

    $('#card-storage .body').innerHTML = (d.storage || []).map((st) =>
      this.meter(`${esc(st.label)} <span class="muted">${esc(st.mount)} ${esc(st.fstype)}</span>`,
        `${fmtBytes(st.used)} / ${fmtBytes(st.total)}`, st.percent, level(st.percent, 85, 95))
      + `<div class="hint">${fmtBytes(st.free)} free</div>`).join('') || '<p class="muted">No volumes found.</p>';
  },

  renderLibrary() {
    const systems = (this.stats || []).filter((s) => s.count > 0);
    const total = systems.reduce((a, s) => a + s.count, 0);
    const size = systems.reduce((a, s) => a + s.size, 0);
    const favs = systems.reduce((a, s) => a + s.favorites, 0);
    const top = [...systems].sort((a, b) => b.size - a.size).slice(0, 8);
    const maxSize = top[0]?.size || 1;
    $('#card-library .body').innerHTML = this.kv([
      ['Total ROMs', total.toLocaleString(LOCALE)],
      ['Systems with ROMs', systems.length],
      ['Favorites', favs],
      ['Space used', fmtBytes(size)],
    ]) + (top.length ? '<div class="sub-h">Systems by space used</div>' + top.map((s) =>
      this.meter(`${sysIcon(s.name, 'sys-ico sm')}${esc(s.fullname)} <span class="muted">${s.count}</span>`, fmtBytes(s.size), (100 * s.size) / maxSize, '')).join('') : '');
  },
};

function translateStatus(s) {
  return { Charging: 'Charging', Discharging: 'Discharging', Full: 'Full', 'Not charging': 'Not charging', Unknown: 'Unknown' }[s] || s || '';
}

/* ======================================================================
 * ROM
 * ==================================================================== */
const DEFAULT_FILTERS = {
  search: '', system: '', collection: '', genre: '', developer: '', publisher: '', region: '',
  yearFrom: '', yearTo: '', players: '', rating: '', played: '', meta: '', hidden: '',
};
const FILTER_LABELS = {
  system: 'System', collection: 'Collection', genre: 'Genre', developer: 'Developer', publisher: 'Publisher', region: 'Region',
  yearFrom: 'From', yearTo: 'To', players: 'Players', rating: 'Rating', played: 'Played', meta: 'Metadata',
  hidden: 'Visibility',
};
const FILTER_INPUTS = {
  search: 'f-search', system: 'f-system', collection: 'f-collection', genre: 'f-genre', developer: 'f-developer', publisher: 'f-publisher',
  region: 'f-region', yearFrom: 'f-year-from', yearTo: 'f-year-to', players: 'f-players', rating: 'f-rating',
  played: 'f-played', meta: 'f-meta', hidden: 'f-hidden',
};
const PAGE = 120;
const COLLECTION_HINT = 'Letters, digits, spaces and - [ ] ( ) only, as in EmulationStation.';
const ICON_PAD = '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="2" y="6" width="20" height="12" rx="6"/><path d="M7 10v4M5 12h4"/><circle cx="16" cy="11" r="1"/><circle cx="18" cy="13.5" r="1"/></svg>';
const splitGenres = (g) => String(g || '').split(/\s*[\/,;|]\s*/).map((x) => x.trim()).filter(Boolean);

const Roms = {
  roms: [],
  byId: new Map(),
  systems: [],
  sysMap: {},
  loaded: false,
  loading: null,
  view: store.get('view', 'systems'),
  system: null,
  collections: [],
  colMap: {},
  collection: null,
  restartNeeded: false,
  layout: store.get('layout', 'grid'),
  sort: store.get('sort', 'name'),
  // only the known filters: older versions saved some that no longer exist
  filters: Object.fromEntries(Object.entries({ ...DEFAULT_FILTERS, ...store.get('filters', {}), search: '' })
    .filter(([k]) => k in DEFAULT_FILTERS)),
  afterLoad: [],
  selected: new Set(),
  list: [],
  rendered: 0,

  ensureLoaded() { if (!this.loaded && !this.loading) this.load(); },

  async load(refresh = false) {
    $('#empty').hidden = false;
    $('#empty').innerHTML = '<strong>Loading library…</strong>The first scan may take a few seconds.';
    $('#rom-list').innerHTML = '';
    $('#system-grid').innerHTML = '';
    this.loading = api('/api/roms' + (refresh ? '?refresh=1' : ''));
    const cols = api('/api/collections').catch((e) => { toast('Could not load collections: ' + e.message, 'err'); return null; });
    try {
      const data = await this.loading;
      const colState = await cols;
      this.systems = data.systems;
      rememberIcons(data.systems);
      this.sysMap = Object.fromEntries(data.systems.map((s) => [s.name, s]));
      this.roms = data.roms.map((r) => ({
        ...r,
        _genres: splitGenres(r.genre),
        _search: norm(r.name + ' ' + r.file + ' ' + (this.sysMap[r.system]?.fullname || '')),
        _sys: this.sysMap[r.system]?.fullname || r.system,
      }));
      this.byId = new Map(this.roms.map((r) => [r.id, r]));
      for (const id of [...this.selected]) if (!this.byId.has(id)) this.selected.delete(id);
      this.loaded = true;
      if (this.system && !this.sysMap[this.system]) this.system = null;
      if (colState) this.setCollections(colState);
      this.fillUploadSystems();
      this.refresh(true);
      Dash.stats = data.systems; Dash.renderLibrary();
      this.afterLoad.forEach((fn) => fn());
    } catch (e) {
      $('#empty').innerHTML = `<strong>Loading failed</strong>${esc(e.message)}`;
      toast('Could not load ROMs: ' + e.message, 'err');
    } finally {
      this.loading = null;
    }
  },

  /* Base set the filters are applied to, depending on the view */
  scope() {
    if (this.view === 'favorites') return this.roms.filter((r) => r.favorite);
    if (this.view === 'systems') return this.system ? this.roms.filter((r) => r.system === this.system) : [];
    if (this.view === 'collections') {
      const c = this.colMap[this.collection];
      return c ? c.ids.map((id) => this.byId.get(id)).filter(Boolean) : [];
    }
    return this.roms;
  },

  activeFilters() {
    const f = this.filters;
    return Object.keys(FILTER_LABELS).filter((k) => {
      if (k === 'system' && this.view === 'systems') return false;
      if (k === 'collection' && this.view === 'collections') return false;
      return f[k] !== '' && f[k] !== false;
    });
  },

  apply(list) {
    const f = this.filters;
    const q = norm(f.search).trim();
    const terms = q ? q.split(/\s+/) : [];
    const yf = parseInt(f.yearFrom, 10), yt = parseInt(f.yearTo, 10);
    const pl = parseInt(f.players, 10), rt = parseFloat(f.rating);
    const sysFilter = this.view !== 'systems' && f.system;
    const colFilter = this.view !== 'collections' && f.collection ? this.colMap[f.collection]?.idSet || new Set() : null;
    return list.filter((r) => {
      if (colFilter && !colFilter.has(r.id)) return false;
      if (terms.length && !terms.every((t) => r._search.includes(t))) return false;
      if (sysFilter && r.system !== f.system) return false;
      if (f.genre && !r._genres.includes(f.genre)) return false;
      if (f.developer && r.developer !== f.developer) return false;
      if (f.publisher && r.publisher !== f.publisher) return false;
      if (f.region && r.region !== f.region) return false;
      if (!isNaN(yf) && !(r.year >= yf)) return false;
      if (!isNaN(yt) && !(r.year && r.year <= yt)) return false;
      if (!isNaN(pl) && !(r.playersMax >= pl)) return false;
      if (!isNaN(rt) && !(r.rating >= rt)) return false;
      if (f.played === 'never' && r.playcount > 0) return false;
      if (f.played === 'played' && !(r.playcount > 0)) return false;
      if (f.meta === 'with' && !r.inGamelist) return false;
      if (f.meta === 'without' && r.inGamelist) return false;
      if (f.meta === 'noimage' && (r.image || r.thumbnail)) return false;
      if (f.hidden === 'visible' && r.hidden) return false;
      if (f.hidden === 'hidden' && !r.hidden) return false;
      return true;
    });
  },

  sorted(list) {
    const desc = this.sort.startsWith('-');
    const key = this.sort.replace('-', '');
    const byName = (a, b) => collator.compare(a.name, b.name);
    const cmp = {
      name: byName,
      system: (a, b) => collator.compare(a._sys, b._sys) || byName(a, b),
      year: (a, b) => (a.year || 0) - (b.year || 0),
      rating: (a, b) => (a.rating ?? -1) - (b.rating ?? -1),
      size: (a, b) => a.size - b.size,
      playcount: (a, b) => a.playcount - b.playcount,
      lastplayed: (a, b) => String(a.lastplayed || '').localeCompare(String(b.lastplayed || '')),
      mtime: (a, b) => a.mtime - b.mtime,
    }[key] || byName;
    return [...list].sort((a, b) => (desc ? -cmp(a, b) : cmp(a, b)) || byName(a, b));
  },

  /* Fill the filter selects with the values present in the current set */
  facets(base) {
    const count = (getter) => {
      const m = new Map();
      for (const r of base) for (const v of [].concat(getter(r))) if (v) m.set(v, (m.get(v) || 0) + 1);
      return [...m.entries()].sort((a, b) => collator.compare(String(a[0]), String(b[0])));
    };
    const fill = (id, entries, labelFn = (v) => v) => {
      const sel = $('#' + id);
      const cur = sel.value;
      sel.innerHTML = `<option value="">All (${entries.length})</option>`
        + entries.map(([v, n]) => `<option value="${esc(v)}">${esc(labelFn(v))} (${n})</option>`).join('');
      sel.value = entries.some(([v]) => String(v) === cur) ? cur : '';
      sel.closest('label').hidden = !entries.length;
    };
    if (this.view !== 'systems') {
      // the per-system count ignores the system filter itself
      const allBase = this.view === 'favorites' ? this.roms.filter((r) => r.favorite) : this.roms;
      const sysName = (v) => this.sysMap[v]?.fullname || v;
      const m = new Map();
      for (const r of allBase) m.set(r.system, (m.get(r.system) || 0) + 1);
      fill('f-system', [...m.entries()].sort((a, b) => collator.compare(sysName(a[0]), sysName(b[0]))), sysName);
    }
    fill('f-collection', this.collections.filter((c) => c.type === 'custom')
      .map((c) => [c.name, base.reduce((n, r) => n + (c.idSet.has(r.id) ? 1 : 0), 0)])
      .filter(([, n]) => n > 0), (v) => v);
    if (this.view === 'collections') $('#fld-collection').hidden = true;
    fill('f-genre', count((r) => r._genres));
    fill('f-developer', count((r) => r.developer));
    fill('f-publisher', count((r) => r.publisher));
    fill('f-region', count((r) => r.region));
    $('#fld-system').hidden = this.view === 'systems';
  },

  syncInputs() {
    for (const [k, id] of Object.entries(FILTER_INPUTS)) {
      const el = $('#' + id);
      if (el.type === 'checkbox') el.checked = !!this.filters[k]; else el.value = this.filters[k];
    }
    $('#f-sort').value = this.sort;
    $$('#rom-views button').forEach((b) => b.classList.toggle('active', b.dataset.view === this.view));
    $$('#layout button').forEach((b) => b.classList.toggle('active', b.dataset.layout === this.layout));
  },

  persist() {
    store.set('view', this.view); store.set('layout', this.layout); store.set('sort', this.sort);
    const { search, ...rest } = this.filters;
    store.set('filters', rest);
  },

  refresh(rebuildFacets = false) {
    if (!this.loaded) return;
    this.persist();
    const base = this.scope();
    if (rebuildFacets) this.facets(base);
    this.syncInputs();

    const af = this.activeFilters();
    const badge = $('#filter-count');
    badge.hidden = !af.length;
    badge.textContent = af.length;
    $('#chips').innerHTML = af.map((k) => {
      let v = this.filters[k];
      if (k === 'system') v = this.sysMap[v]?.fullname || v;
      if (typeof v === 'boolean') v = 'yes';
      const sel = $('#' + FILTER_INPUTS[k]);
      if (sel.tagName === 'SELECT' && sel.selectedOptions[0]) v = sel.selectedOptions[0].textContent.replace(/\s\(\d+\)$/, '');
      return `<span class="chip">${FILTER_LABELS[k]}: <b>${esc(v)}</b><button data-clear="${k}" aria-label="Remove">×</button></span>`;
    }).join('');

    const showSystems = this.view === 'systems' && !this.system;
    const showCollections = this.view === 'collections' && !this.collection;
    const col = this.view === 'collections' ? this.colMap[this.collection] : null;
    $('#breadcrumb').innerHTML = this.view === 'systems'
      ? (this.system ? `<button class="link" id="bc-back">← Systems</button><span>${esc(this.sysMap[this.system]?.fullname || this.system)}</span>` : '<span>Systems</span>')
      : this.view === 'collections'
        ? (col ? `<button class="link" id="bc-back">← Collections</button><span>${esc(col.name)}</span>`
          + (col.type === 'dynamic' ? '<span class="tag">dynamic</span>' : '') + (col.enabled ? '' : '<span class="tag">hidden in ES</span>') : '<span>Collections</span>')
        : `<span>${this.view === 'favorites' ? '★ Favorites' : 'All ROMs'}</span>`;
    $('#es-notice').hidden = !(this.view === 'collections' && this.restartNeeded);
    $('#col-actions').hidden = !col;
    if (col) this.renderCollectionActions(col);

    if (showSystems || showCollections) {
      if (showSystems) this.renderSystems(); else this.renderCollections();
      $('#rom-list').innerHTML = '';
      this.list = [];
      $('#btn-select-all').hidden = true;
      $('.rom-layout').classList.add('no-filters');
      return;
    }
    $('.rom-layout').classList.remove('no-filters');
    $('#system-grid').innerHTML = '';
    $('#btn-select-all').hidden = false;
    this.list = this.sorted(this.apply(base));
    const size = this.list.reduce((a, r) => a + r.size, 0);
    $('#result-info').textContent = `${this.list.length.toLocaleString(LOCALE)} ROMs · ${fmtBytes(size)}`
      + (this.list.length !== base.length ? ` (of ${base.length.toLocaleString(LOCALE)})` : '');
    $('#empty').hidden = this.list.length > 0;
    if (!this.list.length) {
      $('#empty').innerHTML = base.length
        ? '<strong>No ROMs match the filters</strong><button class="link" data-reset>Reset filters</button>'
        : this.view === 'favorites'
          ? '<strong>No favorite ROMs</strong>Click the ☆ on a game (or mark it in EmulationStation) to see it here.'
          : col?.type === 'dynamic'
            ? '<strong>Dynamic collection</strong>EmulationStation fills it with its own filters: edit it from the EmulationStation menu.'
            : col
              ? '<strong>This collection is empty</strong>Select ROMs in any view and use “+ Collection” to add them.'
              : '<strong>No ROMs</strong>Use “Upload ROMs” to add some.';
    }
    this.rendered = 0;
    const listEl = $('#rom-list');
    listEl.innerHTML = this.layout === 'grid'
      ? '<div class="rom-grid"></div>'
      : `<div class="table-wrap"><table class="roms"><thead><tr><th class="c"><input type="checkbox" id="sel-page" aria-label="Select all"></th>
          <th class="k-name">Name</th>${this.view === 'systems' ? '' : '<th class="k-sys">System</th>'}<th class="k-genre">Genre</th><th class="k-year">Year</th>
          <th class="k-dev">Developer</th><th class="k-players">Players</th><th class="k-rating">Rating</th><th class="r k-plays">Plays</th>
          <th class="r k-size">Size</th><th class="k-file">File</th></tr></thead><tbody></tbody></table></div>`;
    this.renderMore();
    this.updateSelection();
  },

  renderSystems() {
    const q = norm(this.filters.search).trim();
    let systems = this.systems.filter((s) => s.count > 0);
    if (q) systems = systems.filter((s) => norm(s.fullname + ' ' + s.name).includes(q));
    systems.sort((a, b) => collator.compare(a.fullname, b.fullname));
    const total = systems.reduce((a, s) => a + s.count, 0);
    $('#result-info').textContent = `${systems.length} systems · ${total.toLocaleString(LOCALE)} ROMs`;
    $('#empty').hidden = systems.length > 0;
    if (!systems.length) $('#empty').innerHTML = '<strong>No systems with ROMs</strong>Upload your ROMs to get started.';
    $('#system-grid').innerHTML = systems.map((s) => `<button class="sys-card" data-system="${esc(s.name)}">
        ${s.icon ? `<div class="sys-avatar has-icon"><img src="${esc(s.icon)}" alt=""></div>`
          : `<div class="sys-avatar" style="background:${avatarBg(s.name)}">${esc(s.name.slice(0, 4))}</div>`}
        <div><div class="t">${esc(s.fullname)}</div>
        <div class="s">${esc([s.manufacturer, s.release].filter(Boolean).join(' · ') || s.name)}</div>
        <div class="s">${s.count} ROMs · ${fmtBytes(s.size)}${s.favorites ? ` · ★ ${s.favorites}` : ''}</div></div></button>`).join('');
  },

  renderCollections() {
    const q = norm(this.filters.search).trim();
    const cols = this.collections.filter((c) => !q || norm(c.name).includes(q));
    const total = this.collections.reduce((a, c) => a + c.ids.length, 0);
    $('#result-info').textContent = `${this.collections.length} collections · ${total.toLocaleString(LOCALE)} entries`;
    $('#empty').hidden = true;
    const card = (c) => {
      const roms = c.ids.map((id) => this.byId.get(id)).filter(Boolean);
      const systems = [...new Set(roms.map((r) => r.system))].filter((s) => ICONS[s]).slice(0, 3);
      const size = roms.reduce((a, r) => a + r.size, 0);
      const icons = systems.length
        ? `<div class="col-icons n${systems.length}">${systems.map((s) => `<img src="${esc(ICONS[s])}" alt="">`).join('')}</div>`
        : `<div class="sys-avatar" style="background:${avatarBg(c.name)}">${esc(c.name.slice(0, 4))}</div>`;
      const tags = [c.type === 'dynamic' ? '<span class="tag">dynamic</span>' : '',
        c.enabled ? '' : '<span class="tag">hidden in ES</span>',
        c.missing ? `<span class="tag warn">${c.missing} missing</span>` : ''].join('');
      return `<button class="sys-card" data-collection="${esc(c.name)}">${icons}
        <div><div class="t">${esc(c.name)}</div>
        <div class="s">${c.type === 'dynamic' ? 'Filled by EmulationStation' : `${roms.length} ROMs · ${fmtBytes(size)}`}</div>
        <div class="s">${tags || '<span class="tag ok">shown in ES</span>'}</div></div></button>`;
    };
    $('#system-grid').innerHTML = `<button class="sys-card new-col" data-new-col><div class="sys-avatar plus">+</div>
        <div><div class="t">New collection</div><div class="s">Saved as an EmulationStation custom collection</div></div></button>`
      + cols.map(card).join('');
  },

  renderCollectionActions(c) {
    $('#col-actions').innerHTML = [
      `<button class="btn sm" data-col-action="rename">Rename</button>`,
      `<button class="btn sm" data-col-action="enable" title="Show or hide this collection in EmulationStation">${c.enabled ? 'Hide in ES' : 'Show in ES'}</button>`,
      c.missing ? `<button class="btn sm" data-col-action="prune" title="Remove the entries whose ROM no longer exists">Remove ${c.missing} missing</button>` : '',
      `<button class="btn sm danger" data-col-action="delete">Delete</button>`,
    ].join('');
  },

  cover(r, cls = '') {
    const src = r.thumbnail || r.image;
    return src ? `<img loading="lazy" src="${esc(src)}" alt="" data-ph="${esc(r.system)}">` : placeholder(r.system, cls);
  },

  renderMore() {
    if (this.rendered >= this.list.length) return;
    const chunk = this.list.slice(this.rendered, this.rendered + PAGE);
    this.rendered += chunk.length;
    if (this.layout === 'grid') {
      $('#rom-list .rom-grid').insertAdjacentHTML('beforeend', chunk.map((r) => `
        <div class="rom-card${this.selected.has(r.id) ? ' selected' : ''}" data-id="${esc(r.id)}">
          <label class="sel"><input type="checkbox" data-select ${this.selected.has(r.id) ? 'checked' : ''} aria-label="Select"></label>
          ${this.favButton(r, 'fav')}
          <div class="cover">${this.cover(r)}</div>
          <div class="info"><div class="title" title="${esc(r.name)}">${esc(r.name)}</div>
          <div class="meta">${esc([this.view === 'systems' ? null : r._sys, r.year, fmtBytes(r.size)].filter(Boolean).join(' · '))}</div></div>
        </div>`).join(''));
    } else {
      $('#rom-list tbody').insertAdjacentHTML('beforeend', chunk.map((r) => `
        <tr data-id="${esc(r.id)}" class="${this.selected.has(r.id) ? 'selected' : ''}">
          <td class="c"><input type="checkbox" data-select ${this.selected.has(r.id) ? 'checked' : ''} aria-label="Select"></td>
          <td class="n k-name" title="${esc(r.name)}">${this.favButton(r, 'fav-t')}${esc(r.name)}${r.hidden ? '<span class="tag">hidden</span>' : ''}</td>
          ${this.view === 'systems' ? '' : `<td class="k-sys" title="${esc(r._sys)}">${sysIcon(r.system, 'sys-ico sm')}${esc(r._sys)}</td>`}
          <td class="k-genre" title="${esc(r.genre)}">${esc(r.genre || '–')}</td><td class="k-year">${r.year || '–'}</td>
          <td class="k-dev" title="${esc(r.developer)}">${esc(r.developer || '–')}</td><td class="k-players">${esc(r.players || '–')}</td>
          <td class="k-rating">${r.rating != null ? '<span class="stars">★</span> ' + r.rating.toFixed(1) : '–'}</td>
          <td class="r k-plays">${r.playcount || 0}</td><td class="r k-size">${fmtBytes(r.size)}</td>
          <td class="muted k-file" title="${esc(r.path)}">${esc(r.path)}</td>
        </tr>`).join(''));
    }
  },

  favButton(r, cls) {
    const label = r.favorite ? 'Remove from favorites' : 'Add to favorites';
    return `<button class="${cls}${r.favorite ? ' on' : ''}" data-fav title="${label}" aria-label="${label}" aria-pressed="${r.favorite}">${r.favorite ? '★' : '☆'}</button>`;
  },

  /* ---- favorites ---- */
  favBusy: new Set(),

  async setFavorite(ids, on) {
    const requested = ids.length;
    ids = ids.filter((id) => this.byId.has(id) && this.byId.get(id).favorite !== on && !this.favBusy.has(id));
    if (!ids.length) {
      if (requested > 1) toast(on ? 'The selected ROMs are already favorites' : 'None of the selected ROMs are favorites');
      return;
    }
    ids.forEach((id) => this.favBusy.add(id));
    try {
      const res = await api('/api/favorite', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids, favorite: on }),
      });
      const failed = res.results.filter((r) => !r.ok);
      for (const { id, ok } of res.results) {
        const r = this.byId.get(id);
        if (!ok || !r || r.favorite === on) continue;
        r.favorite = on;
        if (on) r.inGamelist = true; // the server creates the gamelist.xml entry if it was missing
        const s = this.sysMap[r.system];
        if (s) s.favorites += on ? 1 : -1;
      }
      if (ids.length > 1 || failed.length) {
        toast(`${res.updated} ROM${res.updated === 1 ? '' : 's'} ${on ? 'added to' : 'removed from'} favorites`
          + (failed.length ? `, ${failed.length} errors` : ''), failed.length ? 'err' : 'ok');
      }
      failed.slice(0, 3).forEach((f) => toast(`${f.id}: ${f.error}`, 'err', 7000));
      this.favoritesChanged(ids);
    } catch (e) {
      toast('Could not update favorites: ' + e.message, 'err');
    } finally {
      ids.forEach((id) => this.favBusy.delete(id));
    }
  },

  favoritesChanged(ids) {
    Dash.renderLibrary();
    if (this.view === 'favorites') {
      // the visible set changes: rebuild the list
      for (const id of ids) if (!this.byId.get(id)?.favorite) this.selected.delete(id);
      this.refresh(true);
    } else {
      for (const id of ids) {
        const el = $(`[data-id="${CSS.escape(id)}"] [data-fav]`, $('#rom-list'));
        if (el) el.outerHTML = this.favButton(this.byId.get(id), el.classList.contains('fav') ? 'fav' : 'fav-t');
      }
    }
    const dlg = $('#dlg-detail');
    if (dlg.open && ids.includes(dlg.dataset.id)) this.openDetail(dlg.dataset.id);
  },

  /* ---- selection ---- */
  toggle(id, on) {
    if (on ?? !this.selected.has(id)) this.selected.add(id); else this.selected.delete(id);
    const el = $(`[data-id="${CSS.escape(id)}"]`, $('#rom-list'));
    if (el) {
      el.classList.toggle('selected', this.selected.has(id));
      const cb = $('[data-select]', el);
      if (cb) cb.checked = this.selected.has(id);
    }
    this.updateSelection();
  },

  updateSelection() {
    const n = this.selected.size;
    $('#bulkbar').hidden = n === 0;
    $('#rom-list').classList.toggle('has-selection', n > 0);
    const size = [...this.selected].reduce((a, id) => a + (this.byId.get(id)?.size || 0), 0);
    $('#bulk-info').innerHTML = `<b>${n}</b> selected · ${fmtBytes(size)}`;
    $('#bulk-delete').hidden = !App.config.allowDelete;
    $('#bulk-uncollect').hidden = !(this.view === 'collections' && this.colMap[this.collection]?.type === 'custom');
    const allSel = this.list.length > 0 && this.list.every((r) => this.selected.has(r.id));
    $('#btn-select-all').textContent = allSel ? 'Deselect all' : 'Select all';
    const sp = $('#sel-page');
    if (sp) sp.checked = allSel;
  },

  selectAll() {
    const allSel = this.list.every((r) => this.selected.has(r.id));
    for (const r of this.list) if (allSel) this.selected.delete(r.id); else this.selected.add(r.id);
    $$('#rom-list [data-id]').forEach((el) => {
      const on = this.selected.has(el.dataset.id);
      el.classList.toggle('selected', on);
      const cb = $('[data-select]', el); if (cb) cb.checked = on;
    });
    this.updateSelection();
  },

  /* ---- detail ---- */
  async openDetail(id) {
    const r = this.byId.get(id);
    if (!r) return;
    const dlg = $('#dlg-detail');
    dlg.dataset.id = id;
    const sys = this.sysMap[r.system];
    const render = (desc) => {
      $('#detail-body').innerHTML = `<div class="detail">
        <div class="art">${r.thumbnail || r.image ? `<img src="${esc(r.thumbnail || r.image)}" alt="" data-ph="${esc(r.system)}">` : placeholder(r.system)}
          ${r.image && r.thumbnail ? `<img src="${esc(r.image)}" alt="" loading="lazy">` : ''}
          ${r.video ? `<video src="${esc(r.video)}" controls muted preload="none" playsinline></video>` : ''}
        </div>
        <div>
          <h2>${r.favorite ? '<span class="stars">★</span> ' : ''}${esc(r.name)}</h2>
          <div class="sys">${sysIcon(r.system, 'sys-ico sm')}${esc(sys?.fullname || r.system)}${r.hidden ? ' <span class="tag">hidden</span>' : ''}${r.kidgame ? ' <span class="tag">kids</span>' : ''}${!r.inGamelist ? ' <span class="tag">no metadata</span>' : ''}</div>
          ${Dash.kv([
            ['Genre', esc(r.genre) || null],
            ['Release date', r.releasedate ? fmtDate(r.releasedate) : null],
            ['Developer', esc(r.developer) || null],
            ['Publisher', esc(r.publisher) || null],
            ['Players', esc(r.players) || null],
            ['Rating', r.rating != null ? stars(r.rating) : null],
            ['Region', esc([r.region, r.lang].filter(Boolean).join(' · ')) || null],
            ['Plays', r.playcount ? r.playcount : null],
            ['Last played', r.lastplayed ? fmtDate(r.lastplayed, true) : null],
            ['Play time', r.gametime ? fmtDuration(r.gametime) : null],
            ['File', `<code>${esc(r.path)}</code>${r.isDir ? ' <span class="tag">folder</span>' : ''}`],
            ['Size', fmtBytes(r.size)],
            ['Modified', fmtDate(new Date(r.mtime * 1000).toISOString(), true)],
            ['Collections', this.collections.filter((c) => c.idSet.has(r.id)).map((c) =>
              `<span class="chip">${esc(c.name)}<button data-uncollect="${esc(c.name)}" aria-label="Remove from ${esc(c.name)}" title="Remove from ${esc(c.name)}">×</button></span> `).join('')
              + '<button class="link" data-collect>+ Add to collection</button>'],
          ])}
          ${desc ? `<div class="desc">${esc(desc)}</div>` : ''}
          <div class="actions">
            <a class="btn primary" href="/api/download?id=${encodeURIComponent(r.id)}" download>⇩ Download${r.isDir ? ' (zip)' : ''}</a>
            <button class="btn" data-fav-toggle>${r.favorite ? '☆ Remove from favorites' : '★ Add to favorites'}</button>
            ${r.manual ? `<a class="btn" href="${esc(r.manual)}" target="_blank" rel="noopener">Manual</a>` : ''}
            ${App.config.allowDelete ? `<button class="btn danger" data-delete="${esc(r.id)}">Delete</button>` : ''}
          </div>
        </div></div>`;
    };
    render(r.desc);
    if (!dlg.open) dlg.showModal();
    if (r.desc !== undefined) return;
    try {
      const full = await api('/api/rom?id=' + encodeURIComponent(id));
      r.desc = full.desc || '';
      if (dlg.open && dlg.dataset.id === id && r.desc) render(r.desc);
    } catch { /* the basic details are already shown */ }
  },

  /* ---- download ---- */
  download(ids) {
    if (ids.length === 1) {
      const a = document.createElement('a');
      a.href = '/api/download?id=' + encodeURIComponent(ids[0]);
      a.download = '';
      document.body.appendChild(a); a.click(); a.remove();
      return;
    }
    const form = $('#dl-form');
    form.ids.value = JSON.stringify(ids);
    form.submit();
    toast(`Preparing a zip archive with ${ids.length} ROMs…`);
  },

  /* ---- deletion ---- */
  askDelete(ids) {
    const roms = ids.map((id) => this.byId.get(id)).filter(Boolean);
    if (!roms.length) return;
    $('#del-text').innerHTML = `You are about to delete <b>${roms.length}</b> ROM${roms.length === 1 ? '' : 's'} (${fmtBytes(roms.reduce((a, r) => a + r.size, 0))}).`;
    $('#del-list').innerHTML = roms.slice(0, 15).map((r) => `<li>${esc(r.name)} <span class="muted">· ${esc(r._sys)}</span></li>`).join('')
      + (roms.length > 15 ? `<li class="muted">…and ${roms.length - 15} more</li>` : '');
    const dlg = $('#dlg-delete');
    dlg.dataset.ids = JSON.stringify(roms.map((r) => r.id));
    dlg.showModal();
  },

  async confirmDelete() {
    const dlg = $('#dlg-delete');
    const ids = JSON.parse(dlg.dataset.ids || '[]');
    const btn = $('#del-confirm');
    btn.disabled = true; btn.textContent = 'Deleting…';
    try {
      const res = await api('/api/delete', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids, deleteMedia: $('#del-media').checked }),
      });
      const failed = res.results.filter((r) => !r.ok);
      toast(`${res.deleted} ROMs deleted` + (failed.length ? `, ${failed.length} errors` : ''), failed.length ? 'err' : 'ok');
      failed.slice(0, 3).forEach((f) => toast(`${f.id}: ${f.error}`, 'err', 7000));
      for (const id of ids) this.selected.delete(id);
      dlg.close();
      $('#dlg-detail').close();
      await this.load();
    } catch (e) {
      toast('Delete failed: ' + e.message, 'err');
    } finally {
      btn.disabled = false; btn.textContent = 'Delete';
    }
  },

  /* ---- collections ---- */
  setCollections(state) {
    this.collections = state.collections.map((c) => ({ ...c, idSet: new Set(c.ids) }));
    this.colMap = Object.fromEntries(this.collections.map((c) => [c.name, c]));
    this.restartNeeded = state.restartNeeded;
    if (this.collection && !this.colMap[this.collection]) this.collection = null;
    if (this.filters.collection && !this.colMap[this.filters.collection]) this.filters.collection = '';
  },

  /* POST an action to /api/collections and refresh the views; returns the response or null */
  async colAction(body, message) {
    try {
      const res = await api('/api/collections', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      this.setCollections(res);
      this.refresh(true);
      const dlg = $('#dlg-detail');
      if (dlg.open) this.openDetail(dlg.dataset.id);
      if (message) toast(typeof message === 'function' ? message(res) : message, 'ok');
      return res;
    } catch (e) {
      toast(e.message, 'err', 6000);
      return null;
    }
  },

  async newCollection(ids = []) {
    const name = await promptDialog({ title: 'New collection', value: '', hint: COLLECTION_HINT, ok: 'Create' });
    if (!name) return null;
    const res = await this.colAction({ action: 'create', name, ids },
      ids.length ? `Collection “${name}” created with ${ids.length} ROM${ids.length === 1 ? '' : 's'}` : `Collection “${name}” created`);
    if (res) store.set('lastCollection', res.name);
    return res;
  },

  openCollect(ids) {
    ids = ids.filter((id) => this.byId.has(id));
    if (!ids.length) return;
    const dlg = $('#dlg-collect');
    dlg.dataset.ids = JSON.stringify(ids);
    $('#collect-text').textContent = `${ids.length} ROM${ids.length === 1 ? '' : 's'} selected.`;
    const custom = this.collections.filter((c) => c.type === 'custom');
    const last = store.get('lastCollection', '');
    $('#collect-list').innerHTML = custom.length ? custom.map((c) => {
      const inside = ids.filter((id) => c.idSet.has(id)).length;
      return `<label class="collect-item"><input type="radio" name="collect" value="${esc(c.name)}" ${c.name === last ? 'checked' : ''}>
        <span class="t">${esc(c.name)}</span><span class="muted">${c.ids.length} ROMs${inside ? ` · ${inside === ids.length ? 'already contains them' : `contains ${inside}`}` : ''}</span></label>`;
    }).join('') : '<p class="muted">No custom collections yet: type a name below to create one.</p>';
    $('#collect-new').value = '';
    dlg.showModal();
  },

  async confirmCollect() {
    const dlg = $('#dlg-collect');
    const ids = JSON.parse(dlg.dataset.ids || '[]');
    const name = $('#collect-new').value.trim();
    const picked = $('#collect-list input:checked')?.value;
    let res;
    if (name) {
      res = await this.colAction({ action: 'create', name, ids }, (r) => `Collection “${r.name}” created with ${ids.length} ROM${ids.length === 1 ? '' : 's'}`);
    } else if (picked) {
      res = await this.colAction({ action: 'add', name: picked, ids }, (r) => r.added
        ? `${r.added} ROM${r.added === 1 ? '' : 's'} added to “${picked}”` : `The ROMs were already in “${picked}”`);
    } else {
      toast('Choose a collection or type the name of a new one', 'err');
      return;
    }
    if (res) {
      store.set('lastCollection', res.name);
      dlg.close();
    }
  },

  async collectionAction(kind) {
    const c = this.colMap[this.collection];
    if (!c) return;
    if (kind === 'rename') {
      const newName = await promptDialog({ title: 'Rename collection', value: c.name, hint: COLLECTION_HINT, ok: 'Rename' });
      if (!newName || newName === c.name) return;
      const res = await this.colAction({ action: 'rename', name: c.name, newName }, `Collection renamed to “${newName}”`);
      if (res) { this.collection = res.name; this.refresh(true); }
    } else if (kind === 'enable') {
      this.colAction({ action: 'enable', name: c.name, enabled: !c.enabled },
        c.enabled ? `“${c.name}” hidden in EmulationStation` : `“${c.name}” will be shown in EmulationStation`);
    } else if (kind === 'prune') {
      this.colAction({ action: 'prune', name: c.name }, (r) => `${r.removed} missing entr${r.removed === 1 ? 'y' : 'ies'} removed`);
    } else if (kind === 'delete') {
      const ok = await promptDialog({
        title: 'Delete collection?', ok: 'Delete', danger: true,
        text: `The collection <b>${esc(c.name)}</b> will be deleted. The ROMs it contains are <b>not</b> deleted.`,
      });
      if (!ok) return;
      const res = await this.colAction({ action: 'delete', name: c.name }, `Collection “${c.name}” deleted`);
      if (res) { this.collection = null; this.refresh(true); }
    }
  },

  async uncollect(name, ids) {
    const res = await this.colAction({ action: 'remove', name, ids },
      (r) => `${r.removed} ROM${r.removed === 1 ? '' : 's'} removed from “${name}”`);
    if (res && this.view === 'collections' && this.collection === name) {
      for (const id of ids) this.selected.delete(id);
      this.updateSelection();
    }
  },

  async restartES() {
    const ok = await promptDialog({
      title: 'Restart EmulationStation?', ok: 'Restart',
      text: 'EmulationStation will close and start again (the device is not rebooted), so that it shows the current collections.',
    });
    if (!ok) return;
    try {
      await api('/api/es-restart', { method: 'POST' });
      toast('EmulationStation is restarting…', 'ok');
      setTimeout(async () => {
        try { this.setCollections(await api('/api/collections')); this.refresh(); } catch { /* retry on next load */ }
      }, 10000);
    } catch (e) {
      toast(e.message, 'err', 6000);
    }
  },

  fillUploadSystems() {
    const sel = $('#up-system');
    const cur = sel.value || store.get('uploadSystem', '');
    const systems = [...this.systems].sort((a, b) => collator.compare(a.fullname, b.fullname));
    sel.innerHTML = systems.map((s) => `<option value="${esc(s.name)}">${esc(s.fullname)} (${esc(s.name)})</option>`).join('');
    if (systems.some((s) => s.name === cur)) sel.value = cur;
  },
};

/* ======================================================================
 * Upload
 * ==================================================================== */
const Upload = {
  queue: [],
  running: false,
  xhr: null,

  open(files) {
    if (!App.config.allowUpload) return toast('Uploads are disabled in the configuration', 'err');
    if (Roms.view === 'systems' && Roms.system) $('#up-system').value = Roms.system;
    else if (Roms.filters.system) $('#up-system').value = Roms.filters.system;
    this.updateExts();
    if (files) this.add(files);
    const dlg = $('#dlg-upload');
    if (!dlg.open) dlg.showModal();
  },

  updateExts() {
    const s = Roms.sysMap[$('#up-system').value];
    $('#up-exts').textContent = s && s.extensions.length ? 'Supported extensions: ' + s.extensions.join(' ') : '';
    this.renderQueue();
  },

  add(files) {
    for (const f of files) {
      if (!this.queue.some((q) => q.file.name === f.name && q.file.size === f.size && q.state !== 'ok')) {
        this.queue.push({ file: f, state: 'wait', progress: 0, msg: '' });
      }
    }
    this.renderQueue();
  },

  renderQueue() {
    const s = Roms.sysMap[$('#up-system').value];
    const exts = s?.extensions || [];
    $('#up-queue').innerHTML = this.queue.map((q, i) => {
      const ext = (q.file.name.match(/\.[^.]+$/) || [''])[0].toLowerCase();
      const warn = q.state === 'wait' && exts.length && !exts.includes(ext);
      const st = { wait: 'waiting', run: Math.round(q.progress) + '%', ok: 'done', err: q.msg || 'error' }[q.state];
      return `<li><div class="q-top"><span class="q-name" title="${esc(q.file.name)}">${esc(q.file.name)}</span>
        <span class="muted">${fmtBytes(q.file.size)}</span><span class="q-state ${q.state}">${esc(st)}</span>
        ${q.state === 'run' ? '' : `<button class="q-rm" data-rm="${i}" title="Remove">✕</button>`}</div>
        ${q.state === 'run' || q.state === 'ok' ? `<div class="bar"><span class="ok" style="width:${q.progress}%"></span></div>` : ''}
        ${warn ? `<div class="q-warn">⚠ Extension ${esc(ext)} is not recognized by this system</div>` : ''}</li>`;
    }).join('');
    const pending = this.queue.filter((q) => q.state === 'wait' || q.state === 'err');
    $('#up-summary').textContent = this.queue.length
      ? `${this.queue.length} file${this.queue.length === 1 ? '' : 's'} · ${fmtBytes(this.queue.reduce((a, q) => a + q.file.size, 0))}` : 'No files selected';
    $('#up-start').disabled = this.running || !pending.length;
    $('#up-start').textContent = this.running ? 'Uploading…' : 'Start upload';
    $('#up-clear').textContent = this.running ? 'Cancel' : 'Clear';
  },

  async start() {
    const system = $('#up-system').value;
    if (!system) return toast('Select a target system', 'err');
    store.set('uploadSystem', system);
    this.running = true;
    let ok = 0;
    for (const q of this.queue) {
      if (q.state !== 'wait' && q.state !== 'err') continue;
      if (!this.running) break;
      q.state = 'run'; q.progress = 0; q.msg = '';
      this.renderQueue();
      try {
        await this.send(q, system);
        q.state = 'ok'; q.progress = 100; ok++;
      } catch (e) {
        q.state = 'err'; q.msg = e.message;
      }
      this.renderQueue();
    }
    this.running = false;
    this.renderQueue();
    if (ok) {
      toast(`${ok} file${ok === 1 ? '' : 's'} uploaded to ${Roms.sysMap[system]?.fullname || system}`, 'ok');
      Roms.load();
    }
  },

  send(q, system) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      this.xhr = xhr;
      const params = new URLSearchParams({ system, name: q.file.name, overwrite: $('#up-overwrite').checked ? '1' : '0' });
      xhr.open('POST', '/api/upload?' + params);
      xhr.setRequestHeader('X-Requested-With', 'knulli-webui');
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      let last = 0;
      xhr.upload.onprogress = (e) => {
        if (!e.lengthComputable) return;
        q.progress = (100 * e.loaded) / e.total;
        if (Date.now() - last > 250) { last = Date.now(); this.renderQueue(); }
      };
      xhr.onload = () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch { /* empty */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
        else reject(new Error(data.error || 'HTTP ' + xhr.status));
      };
      xhr.onerror = () => reject(new Error('network error'));
      xhr.onabort = () => reject(new Error('cancelled'));
      xhr.send(q.file);
    });
  },

  clear() {
    if (this.running) {
      this.running = false;
      if (this.xhr) this.xhr.abort();
      return;
    }
    this.queue = [];
    this.renderQueue();
  },
};

/* ======================================================================
 * Events
 * ==================================================================== */
function bind() {
  $('#theme-toggle').addEventListener('click', () => Theme.toggle());
  systemDark.addEventListener('change', () => Theme.apply(document.documentElement.dataset.theme));
  $('#auth-form').addEventListener('submit', (e) => { e.preventDefault(); AuthSetup.submit(); });
  $('#auth-later').addEventListener('click', () => $('#dlg-auth').close());
  $('#auth-never').addEventListener('click', () => AuthSetup.never());

  $$('.tab').forEach((b) => b.addEventListener('click', () => showTab(b.dataset.tab)));
  window.addEventListener('hashchange', () => showTab(location.hash.slice(1)));

  // view and layout
  $('#rom-views').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-view]');
    if (!b) return;
    // every view starts clean: no filters and no search
    if (b.dataset.view !== Roms.view) Roms.filters = { ...DEFAULT_FILTERS };
    Roms.view = b.dataset.view;
    if (Roms.view !== 'systems') Roms.system = null;
    if (Roms.view !== 'collections') Roms.collection = null;
    Roms.refresh(true);
  });
  $('#layout').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-layout]');
    if (b) { Roms.layout = b.dataset.layout; Roms.refresh(); }
  });
  $('#f-sort').addEventListener('change', (e) => { Roms.sort = e.target.value; Roms.refresh(); });

  // filters
  let searchTimer;
  for (const [k, id] of Object.entries(FILTER_INPUTS)) {
    const el = $('#' + id);
    const handler = () => {
      Roms.filters[k] = el.type === 'checkbox' ? el.checked : el.value;
      Roms.refresh();
    };
    if (k === 'search') el.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(handler, 180); });
    else el.addEventListener(el.type === 'number' ? 'input' : 'change', handler);
  }
  const resetFilters = () => { Roms.filters = { ...DEFAULT_FILTERS, search: Roms.filters.search }; Roms.refresh(); };
  $('#btn-reset').addEventListener('click', resetFilters);
  $('#chips').addEventListener('click', (e) => {
    const k = e.target.dataset.clear;
    if (k) { Roms.filters[k] = DEFAULT_FILTERS[k]; Roms.refresh(); }
  });
  $('#empty').addEventListener('click', (e) => { if (e.target.closest('[data-reset]')) resetFilters(); });
  $('#btn-filters').addEventListener('click', () => {
    const on = $('.rom-layout').classList.toggle('filters-toggled');
    store.set('filtersToggled', on);
  });
  if (store.get('filtersToggled', false) && window.innerWidth > 900) $('.rom-layout').classList.add('filters-toggled');

  // systems / breadcrumb
  $('#system-grid').addEventListener('click', (e) => {
    const c = e.target.closest('[data-system]');
    if (c) { Roms.system = c.dataset.system; Roms.filters.search = ''; Roms.refresh(true); window.scrollTo({ top: 0 }); }
    const col = e.target.closest('[data-collection]');
    if (col) { Roms.collection = col.dataset.collection; Roms.filters.search = ''; Roms.refresh(true); window.scrollTo({ top: 0 }); }
    if (e.target.closest('[data-new-col]')) Roms.newCollection();
  });
  $('#breadcrumb').addEventListener('click', (e) => {
    if (e.target.id === 'bc-back') { Roms.system = null; Roms.collection = null; Roms.refresh(true); }
  });

  // ROM list
  $('#rom-list').addEventListener('click', (e) => {
    if (e.target.id === 'sel-page') { Roms.selectAll(); return; }
    const item = e.target.closest('[data-id]');
    if (!item) return;
    if (e.target.closest('[data-fav]')) {
      Roms.setFavorite([item.dataset.id], !Roms.byId.get(item.dataset.id)?.favorite);
      return;
    }
    if (e.target.matches('[data-select]') || e.target.closest('.sel') || e.target.closest('td.c')) {
      if (!e.target.matches('[data-select]')) e.preventDefault();
      Roms.toggle(item.dataset.id, e.target.matches('[data-select]') ? e.target.checked : undefined);
      return;
    }
    if (Roms.selected.size && (e.ctrlKey || e.metaKey || e.shiftKey)) { Roms.toggle(item.dataset.id); return; }
    Roms.openDetail(item.dataset.id);
  });
  // missing images -> placeholder
  document.addEventListener('error', (e) => {
    const img = e.target;
    if (img.tagName === 'IMG' && img.dataset.ph !== undefined) {
      img.insertAdjacentHTML('afterend', placeholder(img.dataset.ph));
      img.remove();
    }
  }, true);
  new IntersectionObserver((entries) => {
    if (entries.some((en) => en.isIntersecting)) Roms.renderMore();
  }, { rootMargin: '800px' }).observe($('#sentinel'));

  $('#btn-select-all').addEventListener('click', () => Roms.selectAll());
  $('#bulk-clear').addEventListener('click', () => {
    Roms.selected.clear();
    $$('#rom-list [data-id].selected').forEach((el) => { el.classList.remove('selected'); const cb = $('[data-select]', el); if (cb) cb.checked = false; });
    Roms.updateSelection();
  });
  $('#bulk-fav').addEventListener('click', () => Roms.setFavorite([...Roms.selected], true));
  $('#bulk-unfav').addEventListener('click', () => Roms.setFavorite([...Roms.selected], false));
  $('#bulk-collect').addEventListener('click', () => Roms.openCollect([...Roms.selected]));
  $('#bulk-uncollect').addEventListener('click', () => Roms.uncollect(Roms.collection, [...Roms.selected]));
  $('#col-actions').addEventListener('click', (e) => {
    const b = e.target.closest('[data-col-action]');
    if (b) Roms.collectionAction(b.dataset.colAction);
  });
  $('#btn-es-restart').addEventListener('click', () => Roms.restartES());
  $('#collect-ok').addEventListener('click', () => Roms.confirmCollect());
  $('#collect-cancel').addEventListener('click', () => $('#dlg-collect').close());
  $('#collect-new').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); Roms.confirmCollect(); } });
  $('#collect-new').addEventListener('input', (e) => {
    if (e.target.value.trim()) $$('#collect-list input:checked').forEach((r) => { r.checked = false; });
  });
  $('#collect-list').addEventListener('change', () => { $('#collect-new').value = ''; });
  $('#bulk-download').addEventListener('click', () => Roms.download([...Roms.selected]));
  $('#bulk-delete').addEventListener('click', () => Roms.askDelete([...Roms.selected]));
  $('#btn-refresh').addEventListener('click', () => Roms.load(true));

  // detail / deletion
  $('#detail-body').addEventListener('click', (e) => {
    const b = e.target.closest('[data-delete]');
    if (b) Roms.askDelete([b.dataset.delete]);
    const un = e.target.closest('[data-uncollect]');
    if (un) Roms.uncollect(un.dataset.uncollect, [$('#dlg-detail').dataset.id]);
    if (e.target.closest('[data-collect]')) Roms.openCollect([$('#dlg-detail').dataset.id]);
    if (e.target.closest('[data-fav-toggle]')) {
      const id = $('#dlg-detail').dataset.id;
      Roms.setFavorite([id], !Roms.byId.get(id)?.favorite);
    }
  });
  $('#del-cancel').addEventListener('click', () => $('#dlg-delete').close());
  $('#del-confirm').addEventListener('click', () => Roms.confirmDelete());
  $$('dialog').forEach((d) => d.addEventListener('click', (e) => { if (e.target === d && !(d.id === 'dlg-upload' && Upload.running)) d.close(); }));
  $('#dlg-detail').addEventListener('close', () => { const v = $('#dlg-detail video'); if (v) v.pause(); });

  // upload
  $('#btn-upload').addEventListener('click', () => Upload.open());
  $('#up-system').addEventListener('change', () => Upload.updateExts());
  $('#up-files').addEventListener('change', (e) => { Upload.add(e.target.files); e.target.value = ''; });
  $('#up-start').addEventListener('click', () => Upload.start());
  $('#up-clear').addEventListener('click', () => Upload.clear());
  $('#up-queue').addEventListener('click', (e) => {
    const i = e.target.dataset.rm;
    if (i !== undefined) { Upload.queue.splice(+i, 1); Upload.renderQueue(); }
  });
  $('#dlg-upload').addEventListener('cancel', (e) => { if (Upload.running) e.preventDefault(); });
  const dz = $('#dropzone');
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('over'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('over'));
  dz.addEventListener('drop', (e) => { e.preventDefault(); e.stopPropagation(); dz.classList.remove('over'); Upload.add(e.dataTransfer.files); });

  // dragging files onto the whole ROM tab
  let dragDepth = 0;
  const romsTab = $('#tab-roms');
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
  romsTab.addEventListener('dragenter', (e) => { if (hasFiles(e) && !$('#dlg-upload').open) { dragDepth++; $('#drop-overlay').hidden = false; } });
  romsTab.addEventListener('dragleave', () => { if (--dragDepth <= 0) { dragDepth = 0; $('#drop-overlay').hidden = true; } });
  romsTab.addEventListener('dragover', (e) => { if (hasFiles(e)) e.preventDefault(); });
  romsTab.addEventListener('drop', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault(); dragDepth = 0; $('#drop-overlay').hidden = true;
    Upload.open(e.dataTransfer.files);
  });

  window.addEventListener('beforeunload', (e) => { if (Upload.running) { e.preventDefault(); e.returnValue = ''; } });
}

async function init() {
  bind();
  Theme.apply(document.documentElement.dataset.theme);
  try {
    App.config = await api('/api/config');
    Theme.apply(App.config.theme);
    $('#hostname').textContent = '· ' + App.config.hostname;
    $('#btn-upload').hidden = !App.config.allowUpload;
    setConn(true, 'Connected');
  } catch {
    setConn(false, 'Disconnected');
  }
  showTab(location.hash.slice(1) || 'dashboard');
  if (App.config.authPrompt && App.config.canSaveConfig) AuthSetup.show();
}

// The feature scripts (stats.js, saves.js, bios.js, captures.js, remote.js) load after this one
document.addEventListener('DOMContentLoaded', init);
