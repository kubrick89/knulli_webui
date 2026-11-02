/* Knulli WebUI - battery saves and save states (/userdata/saves) */
'use strict';

const saveUrl = (path, inline) => `/api/saves/file?p=${encodeURIComponent(path)}${inline ? '&inline=1' : ''}`;
const KIND_LABEL = { save: 'Battery save', auto: 'Auto save', state: 'Save state', other: 'Other file' };

const Saves = {
  groups: [],
  loaded: false,
  loading: false,

  show() {
    Roms.ensureLoaded();
    if (!this.loaded && !this.loading) this.load();
  },

  async load() {
    this.loading = true;
    $('#sv-empty').hidden = false;
    $('#sv-empty').innerHTML = '<strong>Loading saves…</strong>';
    try {
      const data = await api('/api/saves');
      this.groups = data.groups;
      this.loaded = true;
      this.fillSystems();
      this.render();
      const dlg = $('#dlg-saves');
      if (dlg.open) this.open(dlg.dataset.id);
    } catch (e) {
      $('#sv-empty').innerHTML = `<strong>Loading failed</strong>${esc(e.message)}`;
    } finally {
      this.loading = false;
    }
  },

  fillSystems() {
    const sel = $('#sv-system');
    const cur = sel.value;
    const counts = new Map();
    for (const g of this.groups) if (g.system) counts.set(g.system, (counts.get(g.system) || 0) + 1);
    const name = (s) => Roms.sysMap[s]?.fullname || s;
    sel.innerHTML = `<option value="">All systems</option>` + [...counts.entries()]
      .sort((a, b) => collator.compare(name(a[0]), name(b[0])))
      .map(([s, n]) => `<option value="${esc(s)}">${esc(name(s))} (${n})</option>`).join('');
    sel.value = counts.has(cur) ? cur : '';
  },

  filtered() {
    const q = norm($('#sv-search').value).trim();
    const system = $('#sv-system').value;
    const kind = $('#sv-kind').value;
    return this.groups.filter((g) => {
      if (system && g.system !== system) return false;
      if (kind === 'orphan' && g.romId) return false;
      if (kind === 'state' && !g.files.some((f) => f.kind === 'state' || f.kind === 'auto')) return false;
      if (kind === 'save' && !g.files.some((f) => f.kind === 'save')) return false;
      return !q || norm(g.name + ' ' + g.files.map((f) => f.name).join(' ')).includes(q);
    });
  },

  thumb(g) {
    const shot = g.files.filter((f) => f.image).sort((a, b) => b.mtime - a.mtime)[0];
    if (shot) return `<img loading="lazy" src="${esc(saveUrl(shot.image, true))}" alt="" data-ph="${esc(g.system || '?')}">`;
    const rom = Roms.byId.get(g.romId);
    if (rom && (rom.thumbnail || rom.image)) return `<img loading="lazy" src="${esc(rom.thumbnail || rom.image)}" alt="" data-ph="${esc(g.system || '?')}">`;
    return placeholder(g.system || '?');
  },

  summary(g) {
    const states = g.files.filter((f) => f.kind === 'state').length;
    return [states ? `${states} state${states === 1 ? '' : 's'}` : '',
      g.files.some((f) => f.kind === 'auto') ? 'auto save' : '',
      g.files.some((f) => f.kind === 'save') ? 'battery save' : '',
      g.files.some((f) => f.kind === 'other') ? `${g.files.filter((f) => f.kind === 'other').length} other` : '']
      .filter(Boolean).join(' · ');
  },

  render() {
    if (!this.loaded) return;
    const list = this.filtered();
    const size = list.reduce((a, g) => a + g.size, 0);
    const nFiles = list.reduce((a, g) => a + g.files.length, 0);
    $('#sv-info').textContent = `${list.length} game${list.length === 1 ? '' : 's'} · ${nFiles} file${nFiles === 1 ? '' : 's'} · ${fmtBytes(size)}`;
    $('#sv-empty').hidden = list.length > 0;
    if (!list.length) {
      $('#sv-empty').innerHTML = this.groups.length ? '<strong>No saves match the filters</strong>'
        : '<strong>No saves yet</strong>Battery saves and save states appear here after you play.';
    }
    $('#sv-list').innerHTML = list.map((g) => `<button class="save-row" data-group="${esc(g.id)}">
        <div class="save-thumb">${this.thumb(g)}</div>
        <div class="save-main"><div class="t">${esc(g.name)}${g.romId ? '' : ' <span class="tag warn">no matching ROM</span>'}</div>
        <div class="s">${sysIcon(g.system, 'sys-ico sm')}${esc(Roms.sysMap[g.system]?.fullname || g.system || 'Other folder')}</div>
        <div class="s">${esc(this.summary(g))}</div></div>
        <div class="save-meta muted"><div>${fmtAgo(new Date(g.mtime * 1000).toISOString())}</div><div>${fmtBytes(g.size)}</div></div>
      </button>`).join('');
  },

  open(id) {
    const g = this.groups.find((x) => x.id === id);
    const dlg = $('#dlg-saves');
    if (!g) { if (dlg.open) dlg.close(); return; }
    dlg.dataset.id = id;
    const card = (f) => {
      const label = f.kind === 'state' ? `Slot ${f.slot}` : f.kind === 'other' ? f.name : KIND_LABEL[f.kind];
      const art = f.image ? `<img loading="lazy" src="${esc(saveUrl(f.image, true))}" alt="">`
        : `<div class="ph" style="background:${avatarBg(g.system || f.kind)}">${esc(KIND_LABEL[f.kind])}</div>`;
      return `<div class="state-card">
        <div class="state-art">${art}</div>
        <div class="state-info"><div class="t" title="${esc(f.name)}">${esc(label)}</div>
          <div class="s">${esc(fmtDate(new Date(f.mtime * 1000).toISOString(), true))}</div>
          <div class="s">${fmtBytes(f.size)}</div>
          <div class="s muted" title="${esc(f.path)}">${esc(f.path)}</div></div>
        <div class="state-actions">
          <a class="btn sm" href="${esc(saveUrl(f.path))}" download title="Download${f.image ? ' (the screenshot is not included)' : ''}">⇩</a>
          ${App.config.allowDelete ? `<button class="btn sm danger" data-del="${esc(f.path)}" title="Delete">Delete</button>` : ''}
        </div></div>`;
    };
    const zipUrl = '/api/saves/download?' + g.files.map((f) => 'p=' + encodeURIComponent(f.path)).join('&');
    $('#saves-body').innerHTML = `
      <h2>${esc(g.name)}</h2>
      <div class="sys">${sysIcon(g.system, 'sys-ico sm')}${esc(Roms.sysMap[g.system]?.fullname || g.system || '')}
        ${g.romId ? '' : '<span class="tag warn">no matching ROM</span>'}</div>
      <div class="state-grid">${g.files.map(card).join('')}</div>
      <div class="dlg-actions">
        ${g.romId ? '<button class="btn" data-game>Game details</button>' : ''}
        <span class="spacer"></span>
        <button class="btn" data-upload>⇪ Upload</button>
        <a class="btn" href="${esc(zipUrl)}" download>⇩ Download all</a>
        ${App.config.allowDelete ? '<button class="btn danger" data-del-all>Delete all</button>' : ''}
      </div>`;
    if (!dlg.open) dlg.showModal();
  },

  async remove(paths, what) {
    const ok = await promptDialog({
      title: 'Delete saves?', ok: 'Delete', danger: true,
      text: `${esc(what)} will be deleted, together with the screenshots of the save states. This cannot be undone.`,
    });
    if (!ok) return;
    try {
      const res = await api('/api/saves/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ paths }) });
      const failed = res.results.filter((r) => !r.ok);
      toast(`${res.deleted} file${res.deleted === 1 ? '' : 's'} deleted` + (failed.length ? `, ${failed.length} errors` : ''), failed.length ? 'err' : 'ok');
    } catch (e) {
      toast('Delete failed: ' + e.message, 'err');
    }
    this.load();
  },

  /* ---- upload ---- */
  openUpload(dir) {
    const sel = $('#su-dir');
    const dirs = new Set(Roms.systems.map((s) => s.name));
    for (const g of this.groups) for (const f of g.files) dirs.add(f.path.slice(0, f.path.lastIndexOf('/')));
    const label = (d) => (Roms.sysMap[d] ? `${Roms.sysMap[d].fullname} (${d})` : d);
    sel.innerHTML = [...dirs].sort((a, b) => collator.compare(label(a), label(b)))
      .map((d) => `<option value="${esc(d)}">${esc(label(d))}</option>`).join('');
    if (dir) sel.value = dir;
    this.queue = [];
    this.renderQueue();
    $('#dlg-save-upload').showModal();
  },

  queue: [],
  renderQueue() {
    $('#su-queue').innerHTML = this.queue.map((q) => `<li><div class="q-top"><span class="q-name">${esc(q.file.name)}</span>
      <span class="muted">${fmtBytes(q.file.size)}</span><span class="q-state ${q.state}">${esc(q.msg || { wait: 'waiting', run: 'uploading…', ok: 'done' }[q.state])}</span></div></li>`).join('');
    $('#su-start').disabled = !this.queue.some((q) => q.state === 'wait' || q.state === 'err');
  },

  async upload() {
    const dir = $('#su-dir').value;
    let ok = 0;
    for (const q of this.queue.filter((x) => x.state === 'wait' || x.state === 'err')) {
      q.state = 'run'; q.msg = ''; this.renderQueue();
      const params = new URLSearchParams({ dir, name: q.file.name, overwrite: $('#su-overwrite').checked ? '1' : '0' });
      try {
        await api('/api/saves/upload?' + params, { method: 'POST', body: q.file, headers: { 'Content-Type': 'application/octet-stream' } });
        q.state = 'ok'; ok++;
      } catch (e) {
        q.state = 'err'; q.msg = e.message;
      }
      this.renderQueue();
    }
    if (ok) { toast(`${ok} file${ok === 1 ? '' : 's'} uploaded`, 'ok'); this.load(); }
  },
};

TAB_HOOKS.saves = { show: () => Saves.show() };
Roms.afterLoad.push(() => { if (Saves.loaded) { Saves.fillSystems(); Saves.render(); } });

['#sv-search', '#sv-system', '#sv-kind'].forEach((id) => $(id).addEventListener(id === '#sv-search' ? 'input' : 'change', () => Saves.render()));
$('#sv-list').addEventListener('click', (e) => {
  const row = e.target.closest('[data-group]');
  if (row) Saves.open(row.dataset.group);
});
$('#saves-body').addEventListener('click', (e) => {
  const g = Saves.groups.find((x) => x.id === $('#dlg-saves').dataset.id);
  if (!g) return;
  const del = e.target.closest('[data-del]');
  if (del) Saves.remove([del.dataset.del], del.dataset.del);
  if (e.target.closest('[data-del-all]')) Saves.remove(g.files.map((f) => f.path), `All ${g.files.length} save files of “${g.name}”`);
  if (e.target.closest('[data-upload]')) Saves.openUpload(g.files[0].path.slice(0, g.files[0].path.lastIndexOf('/')));
  if (e.target.closest('[data-game]')) Roms.openDetail(g.romId);
});
$('#sv-upload').addEventListener('click', () => Saves.openUpload($('#sv-system').value));
$('#su-files').addEventListener('change', (e) => {
  for (const f of e.target.files) Saves.queue.push({ file: f, state: 'wait' });
  e.target.value = '';
  Saves.renderQueue();
});
$('#su-drop').addEventListener('dragover', (e) => { e.preventDefault(); e.currentTarget.classList.add('over'); });
$('#su-drop').addEventListener('dragleave', (e) => e.currentTarget.classList.remove('over'));
$('#su-drop').addEventListener('drop', (e) => {
  e.preventDefault(); e.currentTarget.classList.remove('over');
  for (const f of e.dataTransfer.files) Saves.queue.push({ file: f, state: 'wait' });
  Saves.renderQueue();
});
$('#su-start').addEventListener('click', () => Saves.upload());
$('#su-cancel').addEventListener('click', () => $('#dlg-save-upload').close());
