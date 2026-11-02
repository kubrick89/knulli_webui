/* Knulli WebUI - live screen and remote control */
'use strict';

const PAD_KEYS = {
  ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
  KeyX: 'a', KeyZ: 'b', KeyS: 'x', KeyA: 'y', KeyQ: 'pageup', KeyW: 'pagedown',
  Digit1: 'l2', Digit2: 'r2', Enter: 'start', Backspace: 'select', KeyH: 'hotkey',
};
const MIN_HOLD = 80;     // ms: a shorter press could fall between two frames of the emulator
const KEEP_ALIVE = 2000; // ms: the server releases a button the page stops refreshing

/* Raw framebuffer pixels (layout in the X-Frame-Format header) -> ImageBitmap */
function rawBitmap(buf, f) {
  const src = new Uint8Array(buf), img = new ImageData(f.w, f.h), out = img.data, bpp = f.bpp / 8;
  const bytes = bpp === 4 && f.r[1] === 8 && f.g[1] === 8 && f.b[1] === 8;
  const chan = ([off, len]) => ({ off, mask: (1 << len) - 1, k: len ? 255 / ((1 << len) - 1) : 0 });
  const r = chan(f.r), g = chan(f.g), b = chan(f.b);
  for (let y = 0; y < f.h; y++) {
    let s = y * f.stride, d = y * f.w * 4;
    if (bytes) {
      const ro = f.r[0] >> 3, go = f.g[0] >> 3, bo = f.b[0] >> 3;
      for (let x = 0; x < f.w; x++, s += 4, d += 4) {
        out[d] = src[s + ro]; out[d + 1] = src[s + go]; out[d + 2] = src[s + bo]; out[d + 3] = 255;
      }
    } else {
      for (let x = 0; x < f.w; x++, s += bpp, d += 4) {
        const v = (src[s] | src[s + 1] << 8 | (bpp > 2 ? src[s + 2] << 16 : 0) | (bpp > 3 ? src[s + 3] << 24 : 0)) >>> 0;
        out[d] = ((v >>> r.off) & r.mask) * r.k;
        out[d + 1] = ((v >>> g.off) & g.mask) * g.k;
        out[d + 2] = ((v >>> b.off) & b.mask) * b.k;
        out[d + 3] = 255;
      }
    }
  }
  return createImageBitmap(img);
}

const Remote = {
  info: null,
  running: false,
  paused: false,
  timer: null,
  etag: null,
  bitmap: null,
  fps: store.get('remoteFps', 10),
  rotation: store.get('remoteRotation', 0),
  frames: [],
  held: new Map(),        // button -> keep-alive timer
  queue: Promise.resolve(),
  errorAt: 0,

  async show() {
    $('#rm-fps').value = this.fps;
    try {
      this.info = await api('/api/screen');
    } catch (e) {
      this.info = null;
      this.notice(`Could not reach the device: ${esc(e.message)}`);
    }
    this.renderPad();
    if (this.info && !this.info.capture) {
      this.notice('<span>No way to capture the screen was found: no Wayland compositor with <code>grim</code> and no framebuffer.'
        + ' Set <code>screen_command</code> in config.json to a command that prints a PNG or JPEG image.</span>');
      $('#rm-placeholder').textContent = 'Screen capture not available';
    }
    if (!this.paused) this.start();
    this.renderToggle();
  },

  hide() { this.stop(); this.releaseAll(); },

  notice(html) { $('#rm-error').hidden = !html; $('#rm-error').innerHTML = html || ''; },

  /* ---- screen ---- */
  start() {
    if (this.running || !this.info?.capture) return;
    this.running = true;
    this.etag = null;
    this.loop();
    this.renderToggle();
  },

  stop() {
    this.running = false;
    clearTimeout(this.timer);
    this.renderToggle();
  },

  renderToggle() { $('#rm-toggle').textContent = this.running ? '❚❚ Pause' : '▶ Resume'; },

  async loop() {
    if (!this.running) return;
    const t0 = performance.now();
    let delay = 0;
    try {
      const res = await fetch('/api/screen/frame', { cache: 'no-store', headers: this.etag ? { 'If-None-Match': this.etag } : {} });
      if (res.status === 200) {
        const fmt = res.headers.get('X-Frame-Format');
        const bmp = fmt ? await rawBitmap(await res.arrayBuffer(), JSON.parse(fmt)) : await createImageBitmap(await res.blob());
        this.etag = res.headers.get('ETag');
        this.bitmap?.close();
        this.bitmap = bmp;
        this.draw();
        this.frames.push(performance.now());
      } else if (res.status !== 304) {
        let msg = `HTTP ${res.status}`;
        try { msg = (await res.json()).error || msg; } catch { /* not JSON */ }
        this.status(msg, true);
        delay = 2000;
      }
      if (res.ok || res.status === 304) this.status();
    } catch (e) {
      this.status('Connection lost', true);
      delay = 2000;
    }
    if (!this.running) return;
    this.timer = setTimeout(() => this.loop(), Math.max(delay, 1000 / this.fps - (performance.now() - t0)));
  },

  draw() {
    const bmp = this.bitmap;
    if (!bmp) return;
    const c = $('#rm-canvas'), swap = this.rotation % 180 !== 0;
    const w = swap ? bmp.height : bmp.width, h = swap ? bmp.width : bmp.height;
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    const ctx = c.getContext('2d');
    ctx.setTransform(1, 0, 0, 1, w / 2, h / 2);
    ctx.rotate(this.rotation * Math.PI / 180);
    ctx.drawImage(bmp, -bmp.width / 2, -bmp.height / 2);
    $('#rm-placeholder').hidden = true;
  },

  status(error, isError) {
    const now = performance.now();
    this.frames = this.frames.filter((t) => now - t < 3000);
    const parts = [];
    if (this.info?.capture) parts.push(this.info.capture);
    if (this.bitmap) parts.push(`${this.bitmap.width}×${this.bitmap.height}`);
    if (this.running) parts.push(`${(this.frames.length / 3).toFixed(1)} fps`);
    $('#rm-status').textContent = parts.join(' · ');
    if (isError) {
      $('#rm-placeholder').hidden = false;
      $('#rm-placeholder').textContent = error;
    }
  },

  saveImage() {
    if (!this.bitmap) return;
    $('#rm-canvas').toBlob((blob) => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `screen-${new Date().toISOString().slice(0, 19).replace(/[T:]/g, '-')}.png`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    }, 'image/png');
  },

  /* ---- controller ---- */
  renderPad() {
    const pad = this.info?.pad;
    const mapped = new Set(pad?.buttons || []);
    $$('#rm-pad [data-btn]').forEach((b) => { b.disabled = !mapped.has(b.dataset.btn); });
    const err = this.info?.padError;
    $('#rm-pad-error').hidden = !err && !pad;
    $('#rm-pad-error').textContent = err ? 'Remote control not available: ' + err : pad ? `Controller: ${pad.device}` : '';
  },

  press(button, down) {
    if (!this.info?.pad?.buttons.includes(button)) return;
    const btn = $(`#rm-pad [data-btn="${button}"]`);
    if (down) {
      if (this.held.has(button)) return;
      this.held.set(button, { at: performance.now(), timer: setInterval(() => this.send({ button, down: true }), KEEP_ALIVE) });
      btn?.classList.add('down');
      this.send({ button, down: true });
    } else {
      const h = this.held.get(button);
      if (!h) return;
      this.held.delete(button);
      clearInterval(h.timer);
      btn?.classList.remove('down');
      this.send({ button, down: false }, MIN_HOLD - (performance.now() - h.at));
    }
  },

  releaseAll() {
    if (!this.held.size) return;
    for (const [button, h] of this.held) {
      clearInterval(h.timer);
      $(`#rm-pad [data-btn="${button}"]`)?.classList.remove('down');
    }
    this.held.clear();
    this.send({ releaseAll: true });
  },

  /* Requests go out one at a time, so a release never overtakes its press */
  send(body, delay = 0) {
    this.queue = this.queue.then(async () => {
      if (delay > 0) await new Promise((r) => setTimeout(r, delay));
      try {
        await api('/api/pad', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      } catch (e) {
        if (performance.now() - this.errorAt > 3000) toast('Remote control: ' + e.message, 'err');
        this.errorAt = performance.now();
      }
    });
  },
};

TAB_HOOKS.remote = { show: () => Remote.show(), hide: () => Remote.hide() };

$('#rm-toggle').addEventListener('click', () => {
  Remote.paused = Remote.running;
  if (Remote.running) Remote.stop(); else Remote.start();
});
$('#rm-fps').addEventListener('change', (e) => { Remote.fps = +e.target.value; store.set('remoteFps', Remote.fps); });
$('#rm-rotate').addEventListener('click', () => {
  Remote.rotation = (Remote.rotation + 90) % 360;
  store.set('remoteRotation', Remote.rotation);
  Remote.draw();
});
$('#rm-save').addEventListener('click', () => Remote.saveImage());
$('#rm-full').addEventListener('click', () => $('#rm-frame').requestFullscreen?.());

const padButton = (e) => e.target.closest('#rm-pad [data-btn]');
$('#rm-pad').addEventListener('pointerdown', (e) => {
  const b = padButton(e);
  if (!b || b.disabled) return;
  e.preventDefault();
  b.releasePointerCapture?.(e.pointerId); // so that sliding off the button releases it
  Remote.press(b.dataset.btn, true);
});
for (const type of ['pointerup', 'pointercancel', 'pointerout']) {
  $('#rm-pad').addEventListener(type, (e) => { const b = padButton(e); if (b) Remote.press(b.dataset.btn, false); });
}
$('#rm-pad').addEventListener('contextmenu', (e) => e.preventDefault());

const padKey = (e) => currentTab === 'remote' && !e.ctrlKey && !e.metaKey && !e.altKey
  && !e.target.closest('input, select, textarea, dialog') && PAD_KEYS[e.code];
document.addEventListener('keydown', (e) => {
  const button = padKey(e);
  if (!button) return;
  e.preventDefault();
  if (!e.repeat) Remote.press(button, true);
});
document.addEventListener('keyup', (e) => {
  const button = padKey(e);
  if (button) { e.preventDefault(); Remote.press(button, false); }
});
window.addEventListener('blur', () => Remote.releaseAll());
document.addEventListener('visibilitychange', () => {
  if (currentTab !== 'remote') return;
  if (document.hidden) Remote.hide();
  else if (!Remote.paused) Remote.start();
});
