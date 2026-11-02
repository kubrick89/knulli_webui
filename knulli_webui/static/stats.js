/* Knulli WebUI - play statistics, computed from the gamelist.xml data of /api/roms */
'use strict';

function fmtAgo(iso) {
  if (!iso) return '–';
  const days = Math.floor((Date.now() - new Date(iso)) / 86400000);
  if (isNaN(days)) return '–';
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return `${days} days ago`;
  if (days < 365) { const m = Math.max(1, Math.round(days / 30)); return `${m} month${m === 1 ? '' : 's'} ago`; }
  return fmtDate(iso);
}

/* Horizontal bar list: one series, values written next to the bars.
 * rows: [{ label (html), value (number), text (formatted value), title, id? }] */
function barList(rows, { empty = 'No data yet' } = {}) {
  if (!rows.length) return `<p class="muted">${empty}</p>`;
  const max = Math.max(...rows.map((r) => r.value), 1);
  return '<div class="hbars">' + rows.map((r) => `<div class="hbar${r.id ? ' clickable' : ''}"${r.id ? ` data-rom="${esc(r.id)}"` : ''} title="${esc(r.title || '')}">
      <div class="hb-label">${r.label}</div>
      <div class="hb-track"><span style="width:${Math.max(1.5, (100 * r.value) / max)}%"></span></div>
      <div class="hb-val">${r.text}</div></div>`).join('') + '</div>';
}

const Stats = {
  show() {
    Roms.ensureLoaded();
    if (Roms.loaded) this.render();
  },

  render() {
    if (currentTab !== 'stats') return;
    const roms = Roms.roms;
    const played = roms.filter((r) => r.playcount > 0 || r.gametime > 0);
    const totalTime = played.reduce((a, r) => a + (r.gametime || 0), 0);
    const sessions = played.reduce((a, r) => a + (r.playcount || 0), 0);
    // Play time is the measure when EmulationStation recorded it, otherwise the number of launches
    const byTime = totalTime > 0;
    const measure = (r) => (byTime ? r.gametime || 0 : r.playcount || 0);
    const fmtMeasure = (v) => (byTime ? fmtDuration(v) : `${v} play${v === 1 ? '' : 's'}`);
    const recent = played.filter((r) => r.lastplayed).sort((a, b) => String(b.lastplayed).localeCompare(String(a.lastplayed)));
    const last30 = recent.filter((r) => Date.now() - new Date(r.lastplayed) < 30 * 86400000).length;

    $('#stats-empty').hidden = played.length > 0;
    $('#stats-cards').hidden = !played.length;
    if (!played.length) {
      $('#stats-kpis').innerHTML = '';
      $('#stats-empty').innerHTML = '<strong>No play data yet</strong>EmulationStation records play counts and play time as you play: come back after a few games.';
      return;
    }
    const pctPlayed = roms.length ? Math.round((100 * played.length) / roms.length) : 0;
    const kpis = [
      { label: 'Play time', value: byTime ? fmtDuration(totalTime) : '–', sub: byTime ? `${fmtDuration(totalTime / Math.max(1, played.length))} per game played` : 'not recorded' },
      { label: 'Games played', value: played.length.toLocaleString(LOCALE), sub: `${pctPlayed}% of ${roms.length.toLocaleString(LOCALE)} ROMs` },
      { label: 'Launches', value: sessions.toLocaleString(LOCALE), sub: 'total play count' },
      { label: 'Last 30 days', value: last30.toLocaleString(LOCALE), sub: 'different games played' },
      { label: 'Last played', value: recent[0] ? esc(recent[0].name) : '–', sub: recent[0] ? `${esc(recent[0]._sys)} · ${fmtAgo(recent[0].lastplayed)}` : '', cls: 'text' },
    ];
    $('#stats-kpis').innerHTML = kpis.map((k) => `<div class="kpi ${k.cls || ''}"><div class="label">${k.label}</div>
      <div class="value">${k.value}</div><div class="sub">${k.sub}</div></div>`).join('');

    const gameLabel = (r) => `${sysIcon(r.system, 'sys-ico sm')}<span class="hb-name">${esc(r.name)}</span>`;
    const top = [...played].sort((a, b) => measure(b) - measure(a) || b.playcount - a.playcount).slice(0, 10);
    $('#st-top .body').innerHTML = barList(top.map((r) => ({
      id: r.id, label: gameLabel(r), value: measure(r), text: fmtMeasure(measure(r)),
      title: `${r.name} (${r._sys}): ${fmtDuration(r.gametime || 0)} played, ${r.playcount} launches`,
    })));

    $('#st-recent .body').innerHTML = recent.length ? '<div class="recent">' + recent.slice(0, 10).map((r) => `
      <div class="recent-row clickable" data-rom="${esc(r.id)}" title="${esc(fmtDate(r.lastplayed, true))}">
        ${sysIcon(r.system, 'sys-ico')}<span class="hb-name">${esc(r.name)}</span>
        <span class="muted">${esc(r._sys)}</span><span class="spacer"></span><span class="muted num">${fmtAgo(r.lastplayed)}</span></div>`).join('') + '</div>'
      : '<p class="muted">No last played dates yet.</p>';

    const group = (keyFn) => {
      const m = new Map();
      for (const r of played) for (const k of [].concat(keyFn(r))) {
        const g = m.get(k) || { value: 0, games: 0 };
        g.value += measure(r); g.games += 1;
        m.set(k, g);
      }
      return [...m.entries()].sort((a, b) => b[1].value - a[1].value);
    };
    const sysRows = group((r) => r.system).slice(0, 10).map(([s, g]) => ({
      label: `${sysIcon(s, 'sys-ico sm')}<span class="hb-name">${esc(Roms.sysMap[s]?.fullname || s)}</span>`,
      value: g.value, text: fmtMeasure(g.value), title: `${Roms.sysMap[s]?.fullname || s}: ${g.games} games played`,
    }));
    $('#st-systems .body').innerHTML = barList(sysRows);
    const genreRows = group((r) => r._genres[0] || 'Unknown').slice(0, 8).map(([genre, g]) => ({
      label: `<span class="hb-name">${esc(genre)}</span>`, value: g.value, text: fmtMeasure(g.value), title: `${genre}: ${g.games} games played`,
    }));
    $('#st-genres .body').innerHTML = barList(genreRows);

    const progress = Roms.systems.filter((s) => s.count > 0).map((s) => {
      const n = played.filter((r) => r.system === s.name).length;
      return { s, n, pct: (100 * n) / s.count };
    }).filter((x) => x.n > 0).sort((a, b) => b.pct - a.pct || b.n - a.n).slice(0, 10);
    $('#st-progress .body').innerHTML = barList(progress.map(({ s, n, pct }) => ({
      label: `${sysIcon(s.name, 'sys-ico sm')}<span class="hb-name">${esc(s.fullname)}</span>`,
      value: pct, text: `${n} / ${s.count}`, title: `${s.fullname}: ${n} of ${s.count} games played (${Math.round(pct)}%)`,
    })));
  },
};

TAB_HOOKS.stats = { show: () => Stats.show() };
Roms.afterLoad.push(() => Stats.render());
$('#tab-stats').addEventListener('click', (e) => {
  const row = e.target.closest('[data-rom]');
  if (row) Roms.openDetail(row.dataset.rom);
});
