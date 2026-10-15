/* Afluencia de visitantes: histórico por día, mes y año, y predicción. Sin dependencias (gráfico en SVG propio).
   Usa las utilidades de app.js (window.HN). Solo se carga con sesión. */
(() => {
  'use strict';
  const HN = window.HN;
  const root = document.getElementById('afluencia');
  if (!HN || !HN.authed || !root) return;

  const { api, esc, toast } = HN;
  const $ = (sel) => root.querySelector(sel);
  const MON = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
  const MONTHS = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
  const WD = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado'];
  const COLOR = { registro: '#236847', demo: '#90d5ad', mixto: '#3e815f' };
  const fmt = (n) => (n === null || n === undefined ? '—' : Number(n).toLocaleString('es-PE'));
  const st = { gran: 'month', place: '', token: 0, data: null, regionalLoaded: false };

  // ------------------------------------------------------------ etiquetas
  const parts = (p) => p.split('-').map(Number);
  function shortLabel(p, gran) {
    if (gran === 'year') return p;
    const [y, m, d] = parts(p);
    return gran === 'month' ? `${MON[m - 1]} ${String(y).slice(2)}` : `${String(d).padStart(2, '0')}/${String(m).padStart(2, '0')}`;
  }
  function longLabel(p, gran) {
    if (gran === 'year') return `Año ${p}`;
    const [y, m, d] = parts(p);
    if (gran === 'month') return `${MONTHS[m - 1]} de ${y}`;
    return `${WD[new Date(Date.UTC(y, m - 1, d)).getUTCDay()]} ${d} de ${MONTHS[m - 1]}`;
  }
  const niceMax = (v) => {
    if (v <= 0) return 10;
    const mag = 10 ** Math.floor(Math.log10(v));
    return [1, 2, 2.5, 5, 10].map((k) => k * mag).find((k) => k >= v) || 10 * mag;
  };

  // ------------------------------------------------------------ gráfico
  function chartHTML(data) {
    const gran = data.granularity;
    const W = 720, H = 300, L = 54, R = 12, T = 22, B = 34;
    const iw = W - L - R, ih = H - T - B;
    const slots = [];
    const index = new Map();
    const slot = (p) => { if (!index.has(p)) { index.set(p, slots.length); slots.push({ period: p }); } return slots[index.get(p)]; };
    data.series.forEach((s) => { slot(s.period).hist = s; });
    data.forecast.forEach((f) => { slot(f.period).fc = f; });
    const top = Math.max(1, ...slots.map((s) => Math.max((s.hist && s.hist.visitors) || 0, (s.fc && s.fc.high) || 0)));
    const max = niceMax(top * 1.05);
    const y = (v) => T + ih - (v / max) * ih;
    const step = iw / slots.length;
    const bw = Math.max(2, Math.min(34, step * 0.72));
    const x = (i) => L + step * i + (step - bw) / 2;
    const firstFc = slots.findIndex((s) => s.fc);
    const every = gran === 'day' ? 7 : gran === 'month' ? 3 : 1;

    let g = '';
    for (let k = 0; k <= 4; k += 1) {
      const v = (max / 4) * k;
      g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="#bfc9c0" stroke-opacity="${k === 0 ? 0.9 : 0.45}" stroke-dasharray="${k === 0 ? '' : '3 4'}"/>`
        + `<text x="${L - 8}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="#404942">${fmt(Math.round(v))}</text>`;
    }
    slots.forEach((s, i) => {
      const cx = x(i) + bw / 2;
      if (s.fc) {
        const f = s.fc;
        const tip = `${longLabel(s.period, gran)}: pronóstico ${fmt(f.expected)} (entre ${fmt(f.low)} y ${fmt(f.high)})${f.holiday ? ' · feriado' : ''}${f.rain != null ? ` · lluvia ${f.rain} %` : ''}`;
        g += `<g><title>${esc(tip)}</title><rect x="${x(i)}" y="${y(f.expected)}" width="${bw}" height="${Math.max(0, y(0) - y(f.expected))}" fill="url(#vfc)" stroke="#c27a0e" stroke-width="0.8" rx="1.5"/>`
          + `<line x1="${cx}" x2="${cx}" y1="${y(f.high)}" y2="${y(f.low)}" stroke="#5c4400" stroke-width="1.2"/>`
          + (bw > 5 ? `<line x1="${cx - 3}" x2="${cx + 3}" y1="${y(f.high)}" y2="${y(f.high)}" stroke="#5c4400"/><line x1="${cx - 3}" x2="${cx + 3}" y1="${y(f.low)}" y2="${y(f.low)}" stroke="#5c4400"/>` : '')
          + '</g>';
      }
      if (s.hist && s.hist.visitors !== null && s.hist.visitors !== undefined) {
        const h = s.hist;
        const w = s.fc ? bw * 0.55 : bw;
        const ox = x(i) + (bw - w) / 2;
        const fill = COLOR[h.source] || COLOR.registro;
        const tip = `${longLabel(s.period, gran)}: ${fmt(h.visitors)} visitantes${h.source === 'demo' ? ' (demostración)' : h.source === 'mixto' ? ' (registro + demostración)' : ''}${h.partial ? ' · periodo en curso' : ''}${h.holiday ? ' · feriado' : ''}`;
        g += `<g><title>${esc(tip)}</title><rect x="${ox}" y="${y(h.visitors)}" width="${w}" height="${Math.max(0.5, y(0) - y(h.visitors))}" fill="${fill}" ${h.partial ? 'fill-opacity="0.55" stroke="#236847" stroke-dasharray="3 2"' : ''} rx="1.5"/></g>`;
      }
      if (i % every === (gran === 'day' ? (slots.length - 1) % every : 0) || gran === 'year') {
        g += `<text x="${cx}" y="${H - 14}" text-anchor="middle" font-size="10.5" fill="#404942">${esc(shortLabel(s.period, gran))}</text>`;
      }
    });
    if (firstFc > 0) {
      const lx = L + step * firstFc;
      g += `<line x1="${lx}" x2="${lx}" y1="${T - 6}" y2="${y(0)}" stroke="#707972" stroke-dasharray="4 3"/>`
        + `<text x="${lx + 5}" y="${T - 9}" font-size="11" font-weight="700" fill="#595c64">Pronóstico →</text>`;
    }
    const first = data.series.find((s) => s.visitors != null);
    const label = `Gráfico de barras de visitantes por ${gran === 'day' ? 'día' : gran === 'month' ? 'mes' : 'año'}${first ? '' : ' (sin datos)'}. Los mismos datos están en la tabla de abajo.`;
    return `<svg viewBox="0 0 ${W} ${H}" class="w-full h-auto" role="img" aria-label="${esc(label)}">
      <defs><pattern id="vfc" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="#fff0c2"/><rect width="2.4" height="6" fill="#c27a0e" fill-opacity="0.85"/></pattern></defs>${g}</svg>`;
  }

  // ------------------------------------------------------------ resto de la sección
  function kpisHTML(k) {
    const card = (label, value, hint, tone = '') => `<div class="rounded-lg border border-outline-variant/30 p-3 ${tone || 'bg-surface-container-low'}">
      <dt class="text-[11px] font-medium text-tertiary">${label}</dt><dd class="font-headline text-2xl font-bold text-on-surface mt-0.5">${value}</dd><p class="text-[11px] text-on-surface-variant">${hint}</p></div>`;
    const fc = 'bg-warn-container/60';
    return card('Últimos 7 días', fmt(k.last_7), 'visitantes (sin contar hoy)')
      + card('Últimos 30 días', fmt(k.last_30), 'visitantes (sin contar hoy)')
      + card('Lo que va del año', fmt(k.year_to_date), 'visitantes acumulados')
      + card('Próximos 7 días', fmt(k.next_7), 'pronóstico', fc)
      + card('Próximos 30 días', fmt(k.next_30), 'pronóstico', fc);
  }

  function modelHTML(d) {
    const m = d.model;
    if (!m.method) return `<p class="flex items-start gap-2">${HN.icon('info', 'text-[18px] text-tertiary')}<span>${esc(m.message)}</span></p>`;
    const gran = d.granularity;
    const rainDays = gran === 'day' ? d.forecast.filter((f) => f.rain != null).length : 0;
    const mape = m.backtest_mape == null ? 'sin datos suficientes' : `${String(m.backtest_mape).replace('.', ',')} %`;
    const checked = gran === 'day' ? 'las últimas 2 semanas' : 'los últimos 6 meses';
    return `<p class="font-semibold text-on-surface flex items-center gap-2">${HN.icon('insights', 'text-[18px] text-primary')}Cómo se calcula la predicción</p>
      <p class="mt-1"><strong>Método:</strong> ${esc(m.label)}. <strong>Intervalo:</strong> ${esc(m.interval)}.
      <strong>Error medio de validación:</strong> ${mape} <span class="text-xs">(se ocultaron ${checked}, se predijeron y se compararon con lo ocurrido)</span>.</p>
      ${rainDays ? `<p class="mt-1">${HN.icon('rainy', 'text-[16px] text-blue-600 align-text-bottom')} Se ajustó por la probabilidad de lluvia del pronóstico del tiempo en ${rainDays} de los próximos ${d.forecast.length} días.</p>` : ''}
      ${m.notes.length ? `<ul class="mt-1 list-disc pl-5 space-y-0.5">${m.notes.map((n) => `<li>${esc(n)}</li>`).join('')}</ul>` : ''}`;
  }

  function tableHTML(d) {
    const gran = d.granularity;
    const rows = [...d.series].filter((s) => s.visitors != null).reverse().map((s) =>
      `<tr class="border-t border-outline-variant/20"><td class="py-1.5 pr-4">${esc(longLabel(s.period, gran))}${s.partial ? ' <span class="text-xs text-tertiary">(en curso)</span>' : ''}</td><td class="pr-4 font-semibold">${fmt(s.visitors)}</td><td class="pr-4">${s.source === 'demo' ? 'Demostración' : s.source === 'mixto' ? 'Mixto' : 'Registro'}</td><td></td></tr>`);
    const fc = d.forecast.map((f) =>
      `<tr class="border-t border-outline-variant/20 bg-warn-container/30"><td class="py-1.5 pr-4">${esc(longLabel(f.period, gran))}${f.holiday ? ' <span class="text-xs text-tertiary">(feriado)</span>' : ''}</td><td class="pr-4 font-semibold">${fmt(f.expected)}</td><td class="pr-4">Pronóstico</td><td class="text-xs">${fmt(f.low)} – ${fmt(f.high)}</td></tr>`);
    return `<table class="w-full text-left"><thead class="text-xs text-tertiary"><tr><th class="py-1 pr-4">${gran === 'day' ? 'Día' : gran === 'month' ? 'Mes' : 'Año'}</th><th class="pr-4">Visitantes</th><th class="pr-4">Origen</th><th>Intervalo 95 %</th></tr></thead><tbody>${fc.join('')}${rows.join('')}</tbody></table>`;
  }

  async function load() {
    const token = ++st.token;
    $('#visits-chart').setAttribute('aria-busy', 'true');
    let d;
    try {
      const qs = new URLSearchParams({ granularity: st.gran });
      if (st.place) qs.set('place', st.place);
      d = await api(`/api/v1/visits/stats?${qs}`);
    } catch (e) {
      if (token !== st.token) return;
      $('#visits-chart').innerHTML = `<p class="rounded-lg bg-error-container text-on-error-container p-4 text-sm">${esc((e.body && e.body.detail) || 'No se pudo cargar la afluencia.')}</p>`;
      $('#visits-chart').setAttribute('aria-busy', 'false');
      return;
    }
    if (token !== st.token) return;                       // llegó una respuesta de una selección anterior
    st.data = d;
    $('#visits-kpis').innerHTML = kpisHTML(d.kpis);
    $('#visits-chart').innerHTML = chartHTML(d);
    $('#visits-chart').setAttribute('aria-busy', 'false');
    $('#visits-model').innerHTML = modelHTML(d);
    $('#visits-table').innerHTML = tableHTML(d);
    const q = d.quality;
    $('#visits-demo-badge').hidden = !q.has_demo;
    $('#visits-demo-badge').lastChild.textContent = q.has_real ? 'Datos mixtos: registro + demostración' : 'Datos de demostración';
  }

  function selectGran(gran) {
    st.gran = gran;
    root.querySelectorAll('[data-visits-gran]').forEach((b) => {
      const on = b.dataset.visitsGran === gran;
      b.setAttribute('aria-selected', String(on));
      b.className = `px-4 py-2 transition-colors ${b.dataset.visitsGran !== 'day' ? 'border-l border-outline-variant/40' : ''} ${on ? 'bg-primary text-on-primary' : 'bg-surface-container-lowest text-on-surface-variant hover:bg-surface-container-low'}`;
    });
    load();
  }

  // ------------------------------------------------------------ referencia regional (BCRP)
  async function loadRegional() {
    const box = $('#visits-regional');
    try {
      const r = await api('/api/v1/visits/regional');
      const maxIdx = Math.max(...r.seasonal_index.map((s) => s.index));
      const bars = r.seasonal_index.map((s) => `<div class="flex flex-col items-center gap-1 flex-1"><span class="text-[10px] text-on-surface-variant">${String(s.index).replace('.', ',').slice(0, 4)}</span>
        <div class="w-full rounded-t bg-primary-container" style="height:${Math.round((s.index / maxIdx) * 70)}px" title="${esc(s.month)}: ${s.index}"></div><span class="text-[10px] text-tertiary">${esc(s.month)}</span></div>`).join('');
      const years = r.yearly.map((y) => `<tr class="border-t border-outline-variant/20"><td class="py-1 pr-4">${y.year}</td><td class="font-semibold">${fmt(y.arrivals)}</td></tr>`).join('');
      box.innerHTML = `<p>${esc(r.series_name)}.</p>
        <div class="mt-3 grid gap-5 md:grid-cols-2">
          <div><p class="text-xs font-semibold text-tertiary mb-1">Estacionalidad mensual (promedio 2015-2019; 1,00 = mes promedio)</p><div class="flex items-end gap-1 h-24">${bars}</div></div>
          <div><p class="text-xs font-semibold text-tertiary mb-1">Arribos anuales a hospedajes de Huánuco</p><table class="text-sm"><tbody>${years}</tbody></table></div>
        </div>
        <p class="mt-3 text-xs">${esc(r.note)} Fuente: ${esc(r.source)}, serie <a class="text-primary underline" href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.series_code)}</a> (consultada el ${esc(r.retrieved)}). Esta serie es de toda la región y de turismo en hospedajes: se usa para dar forma a la estacionalidad, no como conteo de visitas a Honolulo.</p>`;
      st.regionalLoaded = true;
    } catch {
      box.textContent = 'No se pudo cargar la referencia regional.';
    }
  }

  // ------------------------------------------------------------ administración
  async function loadPlaces() {
    try {
      const data = await api('/api/v1/places');
      const opts = data.items.map((p) => `<option value="${esc(p.slug)}">${esc(p.name)}</option>`).join('');
      $('#visits-place').insertAdjacentHTML('beforeend', opts);
      const formPlace = $('#visits-form-place');
      if (formPlace) formPlace.innerHTML = opts;
    } catch { /* el selector queda con «Todos los lugares» */ }
  }

  async function loadRecent() {
    const list = $('#visits-recent');
    if (!list) return;
    try {
      const r = await api('/api/v1/admin/visits/recent');
      list.innerHTML = r.items.length
        ? r.items.map((i) => `<li class="flex items-center gap-2">${esc(i.date)} · ${esc(i.place_name)}: <strong>${fmt(i.visitors)}</strong>
            <button type="button" data-remove-visit data-place="${esc(i.place)}" data-date="${esc(i.date)}" class="ml-auto text-xs text-error hover:underline" aria-label="Quitar el registro de ${esc(i.place_name)} del ${esc(i.date)}">Quitar</button></li>`).join('')
        : '<li>Aún no hay visitas registradas.</li>';
    } catch { /* sin lista */ }
  }

  function bindAdmin() {
    const form = $('#visits-form');
    if (!form) return;
    const iso = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Lima' }).format(new Date());
    $('#visits-form-date').value = iso;
    $('#visits-form-date').max = iso;
    const msg = $('#visits-form-msg');
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const count = $('#visits-form-count').value;
      if (!/^\d+$/.test(count)) { msg.className = 'basis-full text-sm text-error'; msg.textContent = 'Escribe un número entero de visitantes (0 o más).'; return; }
      try {
        const r = await api('/api/v1/admin/visits', { method: 'POST', body: { place: $('#visits-form-place').value, date: $('#visits-form-date').value, visitors: Number(count) } });
        msg.className = 'basis-full text-sm text-primary';
        msg.textContent = `Guardado: ${fmt(r.visitors)} visitantes en ${r.place_name} el ${r.date}.${r.replaced_demo ? ' Reemplaza el dato de demostración de ese día.' : ''}`;
        $('#visits-form-count').value = '';
        loadRecent();
        load();
      } catch (e) {
        const first = e.body && e.body.errors && e.body.errors[0];
        msg.className = 'basis-full text-sm text-error';
        msg.textContent = (first && first.message) || (e.body && e.body.detail) || 'No se pudo guardar.';
      }
    });
    const clear = $('#visits-clear-demo');
    let armed = null;
    clear.addEventListener('click', async () => {
      if (!armed) {
        clear.textContent = 'Pulsa de nuevo para confirmar';
        armed = setTimeout(() => { armed = null; clear.textContent = 'Quitar los datos de demostración'; }, 5000);
        return;
      }
      clearTimeout(armed); armed = null; clear.textContent = 'Quitar los datos de demostración';
      try {
        const r = await api('/api/v1/admin/visits/demo', { method: 'DELETE' });
        toast(`Se quitaron ${fmt(r.deleted)} registros de demostración.`);
        load();
      } catch (e) { toast((e.body && e.body.detail) || 'No se pudo quitar.', 'error'); }
    });
    $('#visits-recent').addEventListener('click', async (ev) => {
      const b = ev.target.closest('[data-remove-visit]');
      if (!b) return;
      try {
        await api(`/api/v1/admin/visits?place=${encodeURIComponent(b.dataset.place)}&date=${encodeURIComponent(b.dataset.date)}`, { method: 'DELETE' });
        toast('Registro quitado.');
        loadRecent();
        load();
      } catch (e) { toast((e.body && e.body.detail) || 'No se pudo quitar el registro.', 'error'); }
    });
    $('#visits-admin').addEventListener('toggle', (ev) => { if (ev.target.open) loadRecent(); });
  }

  root.querySelectorAll('[data-visits-gran]').forEach((b) => b.addEventListener('click', () => selectGran(b.dataset.visitsGran)));
  $('#visits-place').addEventListener('change', (ev) => { st.place = ev.target.value; load(); });
  $('#visits-regional-box').addEventListener('toggle', (ev) => { if (ev.target.open && !st.regionalLoaded) loadRegional(); });
  bindAdmin();
  loadPlaces();
  selectGran('month');
})();
