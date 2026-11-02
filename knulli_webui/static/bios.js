/* Knulli WebUI - BIOS check (files and MD5 listed by Knulli's batocera-systems) */
'use strict';

const BIOS_STATUS = {
  ok: { icon: '✓', label: 'OK', cls: 'ok' },
  mismatch: { icon: '!', label: 'Wrong MD5', cls: 'warn' },
  missing: { icon: '✕', label: 'Missing', cls: 'crit' },
};

const Bios = {
  data: null,
  loading: false,
  open: new Set(),

  show() {
    Roms.ensureLoaded();
    if (!this.data && !this.loading) this.load();
  },

  async load() {
    this.loading = true;
    $('#bi-empty').hidden = false;
    $('#bi-empty').innerHTML = '<strong>Checking BIOS files…</strong>The first check computes the checksums and may take a few seconds.';
    $('#bi-list').innerHTML = '';
    try {
      this.data = await api('/api/bios');
      // systems with ROMs and problems start expanded
      if (!this.open.size) for (const s of this.data.systems) if (s.roms && s.ok < s.total) this.open.add(s.name);
      this.render();
    } catch (e) {
      $('#bi-empty').innerHTML = `<strong>Check failed</strong>${esc(e.message)}`;
    } finally {
      this.loading = false;
    }
  },

  systemStatus(s) {
    if (s.ok === s.total) return { icon: '✓', label: 'Complete', cls: 'ok' };
    if (s.ok > 0) return { icon: '!', label: `${s.ok} of ${s.total} found`, cls: 'warn' };
    if (s.files.some((f) => f.status === 'mismatch')) return { icon: '!', label: 'Wrong files', cls: 'warn' };
    return { icon: '✕', label: 'Missing', cls: 'crit' };
  },

  render() {
    if (!this.data) return;
    const { systems, error, database } = this.data;
    $('#bi-source').textContent = database ? `BIOS list: ${database}.` : '';
    $('#bi-error').hidden = !error;
    $('#bi-error').textContent = error || '';
    const withRoms = systems.filter((s) => s.roms > 0);
    const count = (cls) => withRoms.filter((s) => this.systemStatus(s).cls === cls).length;
    $('#bi-summary').innerHTML = [
      ['Systems with ROMs', withRoms.length, ''],
      ['Complete', count('ok'), 'ok'],
      ['Incomplete or wrong', count('warn'), 'warn'],
      ['Missing', count('crit'), 'crit'],
    ].map(([label, n, cls]) => `<div class="kpi ${cls}"><div class="label">${label}</div><div class="value">${n}</div></div>`).join('');

    const q = norm($('#bi-search').value).trim();
    const scope = $('#bi-scope').value;
    const status = $('#bi-status').value;
    const list = systems.filter((s) => {
      if (scope === 'roms' && !s.roms) return false;
      if (status === 'problems' && s.ok === s.total) return false;
      if (status === 'complete' && s.ok < s.total) return false;
      return !q || norm(s.fullname + ' ' + s.name + ' ' + s.files.map((f) => f.file + ' ' + (f.zipped || '')).join(' ')).includes(q);
    });
    $('#bi-empty').hidden = list.length > 0;
    if (!list.length) $('#bi-empty').innerHTML = systems.length ? '<strong>No system matches the filters</strong>' : '<strong>No BIOS information available</strong>';

    $('#bi-list').innerHTML = list.map((s) => {
      const st = this.systemStatus(s);
      const open = this.open.has(s.name);
      const rows = s.files.map((f) => {
        const fs = BIOS_STATUS[f.status];
        const target = (f.file.split('/').pop());
        return `<tr>
          <td><span class="status ${fs.cls}"><b aria-hidden="true">${fs.icon}</b> ${fs.label}</span></td>
          <td class="mono" title="${esc(f.file)}">${esc(f.file)}${f.zipped ? `<span class="muted"> › ${esc(f.zipped)}</span>` : ''}
            ${f.for ? `<span class="tag">only for ${esc(f.for)}</span>` : ''}</td>
          <td class="mono muted k-md5" title="Expected MD5">${esc(f.md5 || 'any')}</td>
          <td class="r">${f.status === 'ok' ? '' : `<button class="btn sm" data-bios-upload="${esc(target)}" title="Upload ${esc(target)}">⇪ Upload</button>`}</td></tr>`;
      }).join('');
      return `<div class="bios-sys${open ? ' open' : ''}" data-sys="${esc(s.name)}">
        <button class="bios-head" aria-expanded="${open}">
          ${ICONS[s.name] ? `<img class="bios-ico" src="${esc(ICONS[s.name])}" alt="">` : `<div class="sys-avatar" style="background:${avatarBg(s.name)}">${esc(s.name.slice(0, 4))}</div>`}
          <div class="bios-title"><div class="t">${esc(s.fullname)}</div>
            <div class="s">${s.roms ? `${s.roms} ROMs` : 'no ROMs'} · ${s.total} file${s.total === 1 ? '' : 's'} listed</div></div>
          <span class="spacer"></span>
          <span class="status ${st.cls}"><b aria-hidden="true">${st.icon}</b> ${st.label}</span>
          <span class="chev" aria-hidden="true">▾</span>
        </button>
        ${open ? `<div class="table-wrap bios-files"><table class="roms"><thead><tr><th>Status</th><th>File</th><th class="k-md5">Expected MD5</th><th></th></tr></thead>
          <tbody>${rows}</tbody></table></div>` : ''}
      </div>`;
    }).join('');
  },

  /* Upload: `rename` forces the file name (row button), so any file picked lands on that entry */
  async upload(files, rename) {
    let done = 0;
    for (const file of files) {
      const name = rename || file.name;
      try {
        const res = await api('/api/bios/upload?name=' + encodeURIComponent(name), {
          method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' },
        });
        done++;
        if (!res.known) toast(`${name}: not in Knulli's BIOS list, saved as ${res.placed[0]}`, 'info', 7000);
        else if (!res.matched) toast(`${name}: saved as ${res.placed.join(', ')}, but its MD5 is not the expected one`, 'err', 8000);
        else toast(`${name} → ${res.placed.join(', ')}`, 'ok');
      } catch (e) {
        toast(`${name}: ${e.message}`, 'err', 7000);
      }
    }
    if (done) this.load();
  },
};

TAB_HOOKS.bios = { show: () => Bios.show() };
Roms.afterLoad.push(() => Bios.render());

['#bi-search', '#bi-scope', '#bi-status'].forEach((id) => $(id).addEventListener(id === '#bi-search' ? 'input' : 'change', () => Bios.render()));
$('#bi-refresh').addEventListener('click', () => Bios.load());
$('#bi-upload').addEventListener('click', () => { $('#bi-files').dataset.rename = ''; $('#bi-files').click(); });
$('#bi-files').addEventListener('change', (e) => {
  const files = [...e.target.files];
  const rename = e.target.dataset.rename;
  e.target.value = '';
  if (files.length) Bios.upload(rename ? files.slice(0, 1) : files, rename || null);
});
$('#bi-list').addEventListener('click', (e) => {
  const up = e.target.closest('[data-bios-upload]');
  if (up) {
    $('#bi-files').dataset.rename = up.dataset.biosUpload;
    $('#bi-files').click();
    return;
  }
  const head = e.target.closest('.bios-head');
  if (head) {
    const name = head.parentElement.dataset.sys;
    if (Bios.open.has(name)) Bios.open.delete(name); else Bios.open.add(name);
    Bios.render();
  }
});

// drop BIOS files anywhere on the tab
let biosDrag = 0;
const biosTab = $('#tab-bios');
const draggingFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
biosTab.addEventListener('dragenter', (e) => { if (draggingFiles(e)) { biosDrag++; $('#bi-drop').hidden = false; } });
biosTab.addEventListener('dragleave', () => { if (--biosDrag <= 0) { biosDrag = 0; $('#bi-drop').hidden = true; } });
biosTab.addEventListener('dragover', (e) => { if (draggingFiles(e)) e.preventDefault(); });
biosTab.addEventListener('drop', (e) => {
  if (!draggingFiles(e)) return;
  e.preventDefault(); biosDrag = 0; $('#bi-drop').hidden = true;
  Bios.upload([...e.dataTransfer.files]);
});
