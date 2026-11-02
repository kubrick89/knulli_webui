/* Knulli WebUI - screenshots */
'use strict';

const NO_GAME = '-';  // value of the game filter for captures not linked to a ROM
const captureUrl = (c, download) => `/api/captures/file?id=${encodeURIComponent(c.id)}${download ? '&download=1' : ''}`;

const Captures = {
  items: [],
  list: [],
  loaded: false,
  loading: false,
  selected: new Set(),
  index: -1,

  show() {
    Roms.ensureLoaded();
    if (!this.loaded && !this.loading) this.load();
  },

  async load() {
    this.loading = true;
    try {
      this.items = (await api('/api/captures')).captures;
      this.loaded = true;
      const ids = new Set(this.items.map((c) => c.id));
      for (const id of [...this.selected]) if (!ids.has(id)) this.selected.delete(id);
      this.fillGames();
      this.render();
    } catch (e) {
      $('#sh-empty').hidden = false;
      $('#sh-empty').innerHTML = `<strong>Loading failed</strong>${esc(e.message)}`;
    } finally {
      this.loading = false;
    }
  },

  fillGames() {
    const sel = $('#sh-game');
    const cur = sel.value;
    const games = new Map();
    for (const c of this.items) {
      const key = c.romId || NO_GAME;
      const g = games.get(key) || { name: c.romId ? c.game : 'Not linked to a game', n: 0 };
      g.n++;
      games.set(key, g);
    }
    sel.innerHTML = '<option value="*">All games</option>' + [...games.entries()]
      .sort((a, b) => (a[0] === NO_GAME) - (b[0] === NO_GAME) || collator.compare(a[1].name, b[1].name))
      .map(([id, g]) => `<option value="${esc(id)}">${esc(g.name)} (${g.n})</option>`).join('');
    sel.value = games.has(cur) ? cur : '*';
  },

  render() {
    if (!this.loaded) return;
    const q = norm($('#sh-search').value).trim();
    const game = $('#sh-game').value;
    this.list = this.items.filter((c) => (game === '*' || (c.romId || NO_GAME) === game)
      && (!q || norm(c.name + ' ' + (c.game || '')).includes(q)));
    $('#sh-info').textContent = `${this.list.length} file${this.list.length === 1 ? '' : 's'} · ${fmtBytes(this.list.reduce((a, c) => a + c.size, 0))}`;
    $('#sh-empty').hidden = this.list.length > 0;
    if (!this.list.length) {
      $('#sh-empty').innerHTML = this.items.length ? '<strong>Nothing matches the filters</strong>'
        : '<strong>No screenshots yet</strong>Take one with the screenshot hotkey while playing (usually Hotkey + R1).';
    }
    $('#sh-grid').innerHTML = this.list.map((c, i) => `<div class="shot${this.selected.has(c.id) ? ' selected' : ''}" data-i="${i}" data-id="${esc(c.id)}">
        <label class="sel"><input type="checkbox" data-select ${this.selected.has(c.id) ? 'checked' : ''} aria-label="Select"></label>
        <div class="shot-img"><img loading="lazy" src="${esc(captureUrl(c))}" alt=""></div>
        <div class="shot-cap"><div class="t" title="${esc(c.name)}">${c.romId ? sysIcon(c.system, 'sys-ico sm') + esc(c.game) : esc(c.name)}</div>
        <div class="s">${esc(fmtDate(new Date(c.mtime * 1000).toISOString(), true))}</div></div>
      </div>`).join('');
    this.updateSelection();
  },

  updateSelection() {
    const n = this.selected.size;
    $('#sh-bulk').hidden = n === 0;
    $('#sh-grid').classList.toggle('has-selection', n > 0);
    const size = this.items.filter((c) => this.selected.has(c.id)).reduce((a, c) => a + c.size, 0);
    $('#sh-bulk-info').innerHTML = `<b>${n}</b> selected · ${fmtBytes(size)}`;
    $('#sh-bulk-delete').hidden = !App.config.allowDelete;
    const all = this.list.length > 0 && this.list.every((c) => this.selected.has(c.id));
    $('#sh-select-all').textContent = all ? 'Deselect all' : 'Select all';
  },

  toggle(id) {
    if (this.selected.has(id)) this.selected.delete(id); else this.selected.add(id);
    const el = $(`.shot[data-id="${CSS.escape(id)}"]`);
    if (el) { el.classList.toggle('selected', this.selected.has(id)); $('[data-select]', el).checked = this.selected.has(id); }
    this.updateSelection();
  },

  /* ---- viewer ---- */
  view(i) {
    if (i < 0 || i >= this.list.length) return;
    this.index = i;
    const c = this.list[i];
    $('#shot-media').innerHTML = `<img src="${esc(captureUrl(c))}" alt="${esc(c.name)}">`;
    $('#shot-info').innerHTML = `<div class="t">${c.romId ? sysIcon(c.system, 'sys-ico sm') + esc(c.game) : esc(c.name)}</div>
      <div class="s muted">${esc(c.name)} · ${esc(fmtDate(new Date(c.mtime * 1000).toISOString(), true))} · ${fmtBytes(c.size)} · ${i + 1} / ${this.list.length}</div>`;
    $('#shot-download').href = captureUrl(c, true);
    $('#shot-game').hidden = !c.romId;
    $('#shot-delete').hidden = !App.config.allowDelete;
    $('#shot-prev').disabled = i === 0;
    $('#shot-next').disabled = i === this.list.length - 1;
    const dlg = $('#dlg-shot');
    if (!dlg.open) dlg.showModal();
  },

  async remove(ids) {
    const ok = await promptDialog({ title: `Delete ${ids.length} file${ids.length === 1 ? '' : 's'}?`, ok: 'Delete', danger: true,
      text: 'The selected screenshots will be deleted. This cannot be undone.' });
    if (!ok) return false;
    try {
      const res = await api('/api/captures/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }) });
      toast(`${res.deleted} file${res.deleted === 1 ? '' : 's'} deleted`, res.deleted === ids.length ? 'ok' : 'err');
      ids.forEach((id) => this.selected.delete(id));
    } catch (e) {
      toast('Delete failed: ' + e.message, 'err');
    }
    await this.load();
    return true;
  },

  download(ids) {
    if (ids.length === 1) {
      const c = this.items.find((x) => x.id === ids[0]);
      const a = document.createElement('a');
      a.href = captureUrl(c, true); a.download = '';
      document.body.appendChild(a); a.click(); a.remove();
      return;
    }
    const form = $('#dl-form');
    form.action = '/api/captures/download';
    form.ids.value = JSON.stringify(ids);
    form.submit();
    form.action = '/api/download';
    toast(`Preparing a zip archive with ${ids.length} files…`);
  },
};

TAB_HOOKS.shots = { show: () => Captures.show() };
Roms.afterLoad.push(() => { if (Captures.loaded) Captures.load(); });

['#sh-search', '#sh-game'].forEach((id) => $(id).addEventListener(id === '#sh-search' ? 'input' : 'change', () => Captures.render()));
$('#sh-refresh').addEventListener('click', () => Captures.load());
$('#sh-grid').addEventListener('click', (e) => {
  const card = e.target.closest('.shot');
  if (!card) return;
  if (e.target.closest('.sel')) {
    if (!e.target.matches('[data-select]')) e.preventDefault();
    Captures.toggle(card.dataset.id);
    return;
  }
  if (Captures.selected.size && (e.ctrlKey || e.metaKey || e.shiftKey)) { Captures.toggle(card.dataset.id); return; }
  Captures.view(+card.dataset.i);
});
$('#sh-select-all').addEventListener('click', () => {
  const all = Captures.list.every((c) => Captures.selected.has(c.id));
  for (const c of Captures.list) if (all) Captures.selected.delete(c.id); else Captures.selected.add(c.id);
  Captures.render();
});
$('#sh-bulk-clear').addEventListener('click', () => { Captures.selected.clear(); Captures.render(); });
$('#sh-bulk-download').addEventListener('click', () => Captures.download([...Captures.selected]));
$('#sh-bulk-delete').addEventListener('click', () => Captures.remove([...Captures.selected]));
$('#shot-prev').addEventListener('click', () => Captures.view(Captures.index - 1));
$('#shot-next').addEventListener('click', () => Captures.view(Captures.index + 1));
$('#shot-game').addEventListener('click', () => Roms.openDetail(Captures.list[Captures.index].romId));
$('#shot-delete').addEventListener('click', async () => {
  const i = Captures.index;
  if (await Captures.remove([Captures.list[i].id])) {
    if (Captures.list.length) Captures.view(Math.min(i, Captures.list.length - 1)); else $('#dlg-shot').close();
  }
});
$('#dlg-shot').addEventListener('keydown', (e) => {
  if (e.key === 'ArrowLeft') Captures.view(Captures.index - 1);
  if (e.key === 'ArrowRight') Captures.view(Captures.index + 1);
});
$('#dlg-shot').addEventListener('close', () => { $('#shot-media').innerHTML = ''; });
