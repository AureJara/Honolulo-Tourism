/* Pantalla principal de Honolulo: clima actual, calendario, panel del día, evaluación e historial.
   Sin dependencias. La sesión viaja en cookies HttpOnly; aquí solo se llama a /api/v1/*. */
(() => {
  'use strict';

  const TZ = 'America/Lima';
  const MONTHS = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'];
  const WEEKDAYS = ['Domingo', 'Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado'];
  const TONE = {
    clear: 'text-amber-600', partly_cloudy: 'text-teal-600', cloudy: 'text-outline', fog: 'text-teal-600',
    drizzle: 'text-blue-600', light_rain: 'text-blue-600', rain: 'text-blue-600', heavy_rain: 'text-blue-700',
    thunderstorm: 'text-blue-700',
  };
  const MSG_RN08 = 'No se pudo actualizar la información meteorológica. Inténtelo nuevamente.';
  const MSG_DOWN = 'El servicio meteorológico no está disponible. Inténtelo nuevamente.';
  const MSG_PENDING = 'Pronóstico pendiente de evaluación.';

  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const pad = (n) => String(n).padStart(2, '0');
  const icon = (name, cls = '') => `<span class="material-symbols-outlined ${cls}" aria-hidden="true">${esc(name)}</span>`;
  const num = (v, unit = '', digits = 0) => (v === null || v === undefined ? '—' : `${Number(v).toFixed(digits).replace(/\.0+$/, '')}${unit}`);
  const temp = (v) => (v === null || v === undefined ? '—' : `${Math.round(v)}°C`);

  // ---------------------------------------------------------------- fechas
  const todayISO = () => new Intl.DateTimeFormat('en-CA', { timeZone: TZ }).format(new Date());
  const parseISO = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(Date.UTC(y, m - 1, d)); };
  const longDate = (iso) => { const d = parseISO(iso); return `${WEEKDAYS[d.getUTCDay()]}, ${d.getUTCDate()} de ${MONTHS[d.getUTCMonth()]}`; };
  const absTime = (iso) => new Intl.DateTimeFormat('es-PE', { timeZone: TZ, day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(iso));
  function relativeAgo(iso) {
    const secs = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
    if (secs < 45) return 'hace unos segundos';
    const mins = Math.round(secs / 60);
    if (mins < 60) return `hace ${mins} ${mins === 1 ? 'minuto' : 'minutos'}`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return `hace ${hours} ${hours === 1 ? 'hora' : 'horas'}`;
    const days = Math.round(hours / 24);
    return `hace ${days} ${days === 1 ? 'día' : 'días'}`;
  }

  // ------------------------------------------------------------------- API
  class ApiError extends Error {
    constructor(status, body) { super((body && body.detail) || 'Error'); this.status = status; this.body = body; }
  }
  async function api(path, { method = 'GET', body } = {}) {
    const opts = { method, credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (method !== 'GET') opts.headers['X-Requested-With'] = 'XMLHttpRequest';
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    let res;
    try { res = await fetch(path, opts); } catch { throw new ApiError(0, { detail: 'Sin conexión con el servidor.' }); }
    let data = null;
    try { data = await res.json(); } catch { /* cuerpo vacío */ }
    if (res.status === 401) {                       // RF01: sesión no activa
      window.location.href = `/ingresar?next=${encodeURIComponent(window.location.pathname)}`;
      throw new ApiError(401, data);
    }
    if (!res.ok) throw new ApiError(res.status, data);
    return data;
  }

  const state = {
    month: todayISO().slice(0, 7), selected: todayISO(), days: {}, current: null, dayToken: 0, calToken: 0,
    hist: { page: 1, total: 0, pageSize: 10, loaded: false },
  };

  // ------------------------------------------------------------------ aviso
  let toastTimer;
  function toast(message, kind = 'info') {
    const box = $('#toast');
    const palette = kind === 'error' ? 'bg-error-container text-on-error-container' : kind === 'warn' ? 'bg-warn-container text-on-warn-container' : 'bg-inverse-surface text-inverse-on-surface';
    box.innerHTML = `<div class="px-4 py-2.5 rounded-xl shadow-lg text-sm ${palette}">${esc(message)}</div>`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { box.innerHTML = ''; }, 5000);
  }

  // ------------------------------------------------------------ clima actual
  const stat = (label, value) => `<div><dt class="text-[11px] uppercase tracking-wider text-tertiary font-headline font-semibold">${label}</dt><dd class="font-headline text-lg font-bold text-on-surface">${value}</dd></div>`;

  function currentHTML(data) {
    const w = data.weather;
    const tone = TONE[w.condition_code] || 'text-tertiary';
    const warning = data.stale
      ? `<div role="alert" class="mb-4 p-3 rounded-lg bg-warn-container text-on-warn-container text-sm flex items-start gap-2">${icon('warning', 'text-[18px] mt-0.5')}<span>${esc(data.message || MSG_RN08)} Se muestran los últimos datos guardados.</span></div>`
      : '';
    return `${warning}
      <div class="flex items-center gap-4">
        ${icon(w.icon, `text-6xl ${tone} filled`)}
        <div>
          <p class="font-headline text-5xl font-bold text-primary leading-none">${temp(w.temperature)}</p>
          <p class="text-on-surface-variant mt-1">${esc(w.condition)}</p>
        </div>
      </div>
      <dl class="grid grid-cols-3 gap-3 mt-5 pt-4 border-t border-outline-variant/20">
        ${stat('Humedad', num(w.humidity, '%'))}${stat('Lluvia', num(w.rain_probability, '%'))}${stat('Viento', num(w.wind_speed, ' km/h', 0))}
      </dl>
      <details class="mt-3 text-sm">
        <summary class="cursor-pointer text-primary font-semibold font-headline text-xs">Más detalles</summary>
        <dl class="grid grid-cols-3 gap-3 mt-3">
          ${stat('Sensación', temp(w.feels_like))}${stat('Precipitación', num(w.precipitation, ' mm', 1))}${stat('Nubosidad', num(w.cloud_cover, '%'))}
        </dl>
      </details>`;
  }

  function renderCurrent() {
    const card = $('#current-card'), body = $('#current-body');
    card.setAttribute('aria-busy', 'false');
    const cur = state.current;
    if (!cur || cur.error) {
      body.innerHTML = `<div role="alert" class="p-4 rounded-lg bg-error-container text-on-error-container text-sm flex items-start gap-2">${icon('cloud_off', 'text-[20px] mt-0.5')}<span>${esc((cur && cur.error) || MSG_DOWN)}</span></div>`;
      $('#freshness').textContent = '';
      return;
    }
    body.innerHTML = currentHTML(cur.data);
    tickFreshness();
  }

  function tickFreshness() {
    const cur = state.current, el = $('#freshness');
    if (!cur || !cur.data) return;
    const d = cur.data;
    const when = `<time datetime="${esc(d.updated_at)}" title="${esc(absTime(d.updated_at))}">${esc(relativeAgo(d.updated_at))}</time>`;
    // RN08 / Escenario 5: un dato vencido nunca se presenta como «actual».
    el.innerHTML = d.stale
      ? `${icon('schedule', 'text-[16px]')}<span>Última actualización conocida: ${esc(absTime(d.updated_at))} (${when}).</span>`
      : `${icon('schedule', 'text-[16px]')}<span>Actualizado ${when}.</span>`;
  }

  async function loadCurrent() {
    try {
      state.current = { data: await api('/api/v1/weather/current') };
    } catch (e) {
      state.current = { error: e.status === 503 || e.status === 502 ? MSG_DOWN : ((e.body && e.body.detail) || MSG_DOWN) };
    }
    renderCurrent();
  }

  async function onRefresh() {
    const btn = $('#refresh-btn'), ic = $('#refresh-icon');
    btn.disabled = true; ic.classList.add('animate-spin');
    try {
      const res = await api('/api/v1/weather/refresh', { method: 'POST' });
      if (res.current) { state.current = { data: res.current }; renderCurrent(); }
      if (res.message) toast(res.message, 'warn');
      else toast(res.refreshed ? 'Datos meteorológicos actualizados.' : 'Los datos ya están al día.');
      if (res.forecast && res.forecast.refreshed) await Promise.all([loadCalendar(), loadDay(state.selected)]);
    } catch (e) {
      const last = e.body && e.body.last_known;
      if (last) { state.current = { data: last }; renderCurrent(); }
      else if (!state.current || !state.current.data) { state.current = { error: e.status === 503 ? MSG_DOWN : MSG_RN08 }; renderCurrent(); }
      toast((e.body && e.body.detail) || MSG_RN08, 'error');
    } finally {
      btn.disabled = false; ic.classList.remove('animate-spin');
    }
  }

  // -------------------------------------------------------------- calendario
  function calCell(iso, info, flags) {
    const day = Number(iso.slice(8));
    const selected = iso === state.selected, isToday = iso === todayISO();
    const label = `${longDate(iso)}: ${info && info.available ? `${info.condition}, ${Math.round(info.temperature)} grados` : 'sin pronóstico'}`;
    const base = 'min-h-[84px] p-2 rounded-lg text-left flex flex-col justify-between transition-all group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary';
    const tone = selected
      ? 'border-2 border-primary shadow-md ring-2 ring-primary/20 bg-surface-container-lowest scale-[1.02]'
      : `border border-outline-variant/20 bg-surface-container-lowest hover:border-primary/40 hover:bg-surface-container-low ${info && info.available ? '' : 'opacity-70'}`;
    const number = isToday
      ? `<span class="w-6 h-6 rounded-full bg-primary text-on-primary flex items-center justify-center font-headline text-xs font-bold">${day}</span>`
      : `<span class="font-headline text-sm font-semibold group-hover:text-primary">${day}</span>`;
    const right = info && info.available
      ? icon(info.icon, `text-[18px] ${TONE[info.condition_code] || ''} ${info.condition_code === 'clear' ? 'filled' : ''}`)
      : '';
    const foot = info && info.available
      ? `<p class="font-headline text-xs font-semibold ${selected ? 'text-primary font-bold' : 'text-tertiary'}">${Math.round(info.temperature)}°C</p>`
      : `<p class="font-headline text-xs text-tertiary" aria-hidden="true">—</p>`;
    return `<button type="button" data-date="${iso}" aria-label="${esc(label)}" aria-pressed="${selected}" ${isToday ? 'aria-current="date"' : ''} class="${base} ${tone}">
      <div class="flex items-start justify-between">${number}${right}</div><div class="mt-1">${foot}</div></button>`;
  }

  const mutedCell = (text) => `<div class="min-h-[84px] p-2 rounded-lg border border-transparent text-tertiary font-headline text-xs" aria-hidden="true">${text}</div>`;

  function renderCalendar() {
    const [y, m] = state.month.split('-').map(Number);
    $('#cal-title').textContent = `${MONTHS[m - 1]} ${y}`;
    const offset = (new Date(Date.UTC(y, m - 1, 1)).getUTCDay() + 6) % 7;        // lunes primero
    const dim = new Date(Date.UTC(y, m, 0)).getUTCDate();
    const prevDim = new Date(Date.UTC(y, m - 1, 0)).getUTCDate();
    const nextName = MONTHS[m % 12].slice(0, 3);
    const cells = [];
    for (let i = 0; i < offset; i++) cells.push(mutedCell(prevDim - offset + 1 + i));
    for (let d = 1; d <= dim; d++) { const iso = `${y}-${pad(m)}-${pad(d)}`; cells.push(calCell(iso, state.days[iso])); }
    for (let i = 1; i <= (7 - ((offset + dim) % 7)) % 7; i++) cells.push(mutedCell(`${i} ${nextName}`));
    $('#cal-grid').innerHTML = cells.join('');
    $('#cal-grid').setAttribute('aria-busy', 'false');
  }

  async function loadCalendar() {
    const token = ++state.calToken, month = state.month;
    $('#cal-grid').setAttribute('aria-busy', 'true');
    try {
      const data = await api(`/api/v1/weather/calendar?month=${month}`);
      if (token !== state.calToken) return;
      state.days = Object.fromEntries(data.days.map((d) => [d.date, d]));
    } catch (e) {
      if (token !== state.calToken) return;
      state.days = {};
      toast((e.body && e.body.detail) || MSG_DOWN, 'error');
    }
    renderCalendar();
  }

  function shiftMonth(delta) {
    const [y, m] = state.month.split('-').map(Number);
    const d = new Date(Date.UTC(y, m - 1 + delta, 1));
    state.month = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}`;
    state.days = {};
    renderCalendar();
    loadCalendar();
  }

  function selectDate(iso) {
    state.selected = iso;
    renderCalendar();
    loadDay(iso);
  }

  // --------------------------------------------------------------- panel del día
  function evalCardHTML(ev) {
    if (!ev || ev.status === 'pending' || ev.accuracy_score === null || ev.accuracy_score === undefined) {
      return `<div class="rounded-xl border border-outline-variant/30 bg-surface-container-low p-4 text-center">
        <p class="font-headline text-xs font-bold uppercase tracking-wider text-tertiary">Precisión del pronóstico</p>
        <p class="mt-2 text-sm text-on-surface-variant flex items-center justify-center gap-1.5">${icon('hourglass_top', 'text-[18px]')}${esc(MSG_PENDING)}</p></div>`;
    }
    const partial = ev.status === 'partial' ? ` <span class="text-tertiary">(parcial)</span>` : '';
    return `<div class="rounded-xl border border-outline-variant/30 bg-surface-container-low p-4 text-center">
      <p class="font-headline text-xs font-bold uppercase tracking-wider text-tertiary">Precisión del pronóstico</p>
      <p class="mt-1 font-headline text-4xl font-bold text-primary">${Math.round(ev.accuracy_score)}%</p>
      <p class="text-sm font-semibold text-on-surface">${esc(ev.accuracy_label || '')}${partial}</p>
      <p class="mt-1 text-xs text-tertiary">Promedio de ${ev.hours_evaluated} de ${ev.hours_total} horas evaluadas</p></div>`;
  }

  function bandsHTML(bands) {
    if (!bands || !bands.length) return '';
    return `<h3 class="mt-6 pt-6 border-t border-outline-variant/20 font-headline text-sm font-bold text-on-surface mb-3 flex items-center gap-2">${icon('routine', 'text-primary text-[18px]')}Dinámica Climática por Franjas</h3>
      <div class="grid grid-cols-3 gap-3 text-center">${bands.map((b) => `
        <div class="bg-surface-container-low p-3 rounded-lg border border-outline-variant/20">
          <span class="font-headline text-[11px] font-semibold text-tertiary block">${b.from} - ${b.to}</span>
          ${icon(b.icon, `text-[22px] my-1 ${TONE[b.condition_code] || ''}`)}
          <span class="font-headline text-sm font-semibold block text-on-surface">${Math.round(b.temperature)}°C</span>
          <span class="text-[11px] text-on-surface-variant">${esc(b.label)}</span>
        </div>`).join('')}</div>`;
  }

  function metricsHTML(m) {
    const cell = (label, value, extra = '') => `<div><span class="block text-[11px] text-tertiary">${label}</span><strong class="text-on-surface font-semibold">${value}</strong>${extra}</div>`;
    const rain = m.rain_probability, hum = m.humidity;
    return `<div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4 pt-4 border-t border-outline-variant/10 text-sm text-on-surface-variant">
      ${cell('Prob. de Lluvia', `${num(rain.value, '%')}${rain.label ? ` (${esc(rain.label)})` : ''}`)}
      ${cell('Humedad', `${num(hum.value, '%')}${hum.label ? ` ${esc(hum.label)}` : ''}`)}
      ${cell('Viento', num(m.wind_speed.value, ' km/h', 0))}
      ${cell('Precipitación', num(m.precipitation.value, ' mm', 1))}</div>`;
  }

  function hourlyHTML(rows) {
    const badge = (ev) => ev && ev.status === 'evaluated'
      ? `<span class="inline-block px-2 py-0.5 rounded-full bg-secondary-container text-on-secondary-container text-xs font-semibold">${Math.round(ev.accuracy_score)}%</span>`
      : `<span class="text-xs text-tertiary">Pendiente</span>`;
    return `<details class="mt-5 group/h">
      <summary class="cursor-pointer text-primary font-headline font-semibold text-sm flex items-center gap-1">${icon('schedule', 'text-[18px]')}Pronóstico por hora <span class="text-tertiary font-normal text-xs">(toca una hora para compararla con lo observado)</span></summary>
      <div class="mt-3 overflow-x-auto custom-scroll">
        <table class="w-full text-sm min-w-[480px]">
          <thead><tr class="text-left text-[11px] uppercase tracking-wider text-tertiary font-headline">
            <th class="py-1.5 pr-2">Hora</th><th class="pr-2">Temp.</th><th class="pr-2">Humedad</th><th class="pr-2">Lluvia</th><th class="pr-2">Precip.</th><th class="pr-2">Condición</th><th>Precisión</th></tr></thead>
          <tbody id="hourly-body">${rows.map((r) => `
            <tr data-id="${r.forecast_id}" tabindex="0" role="button" aria-expanded="false" class="cursor-pointer border-t border-outline-variant/15 hover:bg-surface-container-low focus-visible:bg-surface-container-low">
              <td class="py-2 pr-2 font-headline font-semibold">${esc(r.time)}</td><td class="pr-2">${temp(r.temperature)}</td>
              <td class="pr-2">${num(r.humidity, '%')}</td><td class="pr-2">${num(r.rain_probability, '%')}</td>
              <td class="pr-2">${num(r.precipitation, ' mm', 1)}</td>
              <td class="pr-2"><span class="inline-flex items-center gap-1">${icon(r.icon, `text-[16px] ${TONE[r.condition_code] || ''}`)}${esc(r.condition)}</span></td>
              <td>${badge(r.evaluation)}</td></tr>`).join('')}
          </tbody></table></div></details>`;
  }

  function dayHTML(d) {
    if (!d.available) {
      return `<h2 class="font-headline text-2xl font-bold text-on-surface">${esc(longDate(d.date))}</h2>
        <div class="mt-4 p-4 rounded-lg bg-surface-container-low border border-outline-variant/30 text-sm text-on-surface-variant flex items-start gap-2">${icon('info', 'text-primary text-[20px]')}<span>${esc(d.message || 'No hay pronóstico disponible para esta fecha.')}</span></div>`;
    }
    const tone = TONE[d.condition_code] || '';
    return `<div class="flex items-start justify-between gap-4">
        <div><h2 class="font-headline text-2xl font-bold text-on-surface">${esc(longDate(d.date))}</h2>
          <p class="text-sm text-tertiary mt-0.5">Condición proyectada: ${esc(d.summary)}</p></div>
        <div class="text-right shrink-0 flex items-center gap-2">${icon(d.icon, `text-3xl ${tone} filled`)}
          <span class="font-headline text-3xl font-bold text-primary">${temp(d.temperature)}</span></div>
      </div>
      ${bandsHTML(d.bands)}${metricsHTML(d.metrics)}
      <div id="eval-card" class="mt-5">${evalCardHTML(d.evaluation)}</div>
      ${hourlyHTML(d.forecast)}
      <p class="mt-4 text-[11px] text-tertiary">Pronóstico emitido ${esc(absTime(d.issued_at))}. Fuente: proveedor meteorológico externo.</p>`;
  }

  function errorBox(message) {
    return `<div role="alert" class="p-4 rounded-lg bg-error-container text-on-error-container text-sm flex items-start gap-2">${icon('cloud_off', 'text-[20px] mt-0.5')}<span>${esc(message)}</span></div>`;
  }

  async function loadDay(iso) {
    const token = ++state.dayToken, body = $('#day-body');
    body.innerHTML = '<div class="skeleton h-64 w-full"></div>';
    let data;
    try {
      data = await api(`/api/v1/weather/forecast?date=${iso}`);
    } catch (e) {
      if (token === state.dayToken) body.innerHTML = errorBox(e.status === 503 || e.status === 502 ? MSG_DOWN : ((e.body && e.body.detail) || MSG_DOWN));
      return;
    }
    if (token !== state.dayToken) return;
    body.innerHTML = dayHTML(data);
    if (data.available) {
      // La evaluación agregada puede pedir observaciones al proveedor: se completa sin bloquear el panel.
      api(`/api/v1/weather/evaluation?date=${iso}`).then((ev) => {
        if (token === state.dayToken && $('#eval-card')) $('#eval-card').innerHTML = evalCardHTML(ev);
      }).catch(() => { /* se conserva el estado inicial */ });
    }
  }

  // ---- comparación pronóstico vs. observación (RF10)
  const VARS = { temperature: ['Temperatura', '°C'], humidity: ['Humedad', '%'], wind: ['Viento', ' km/h'], precipitation: ['Precipitación', ' mm'] };
  function comparisonHTML(ev) {
    if (ev.status !== 'evaluated') {
      return `<p class="py-2 text-sm text-on-surface-variant flex items-center gap-1.5">${icon('hourglass_top', 'text-[18px]')}${esc(ev.message || MSG_PENDING)}</p>`;
    }
    const rows = Object.entries(ev.variables).map(([key, v]) => {
      if (VARS[key]) { const [label, unit] = VARS[key]; return `<tr><td class="py-1 pr-3">${label}</td><td class="pr-3">${v.predicted}${unit}</td><td class="pr-3">${v.observed}${unit}</td><td>${v.error}${unit}</td></tr>`; }
      if (key === 'rain') return `<tr><td class="py-1 pr-3">Lluvia</td><td class="pr-3">${v.predicted ? 'Sí' : 'No'} (${v.probability}%)</td><td class="pr-3">${v.observed ? 'Sí' : 'No'}</td><td>${v.correct ? 'Acertó' : 'Falló'}</td></tr>`;
      return `<tr><td class="py-1 pr-3">Condición</td><td class="pr-3">${esc(v.predicted)}</td><td class="pr-3">${esc(v.observed)}</td><td>${v.predicted_code === v.observed_code ? 'Coincide' : 'Distinta'}</td></tr>`;
    }).join('');
    return `<table class="w-full text-xs my-2"><thead><tr class="text-left text-tertiary uppercase tracking-wider font-headline"><th class="pb-1">Variable</th><th>Pronóstico</th><th>Observado</th><th>Error</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="text-xs font-semibold text-primary">Precisión: ${ev.accuracy_score}% — ${esc(ev.accuracy_label)}</p>`;
  }

  async function toggleHour(tr) {
    const open = tr.getAttribute('aria-expanded') === 'true';
    const next = tr.nextElementSibling;
    if (next && next.dataset.detail) next.remove();
    tr.setAttribute('aria-expanded', String(!open));
    if (open) return;
    const detail = document.createElement('tr');
    detail.dataset.detail = tr.dataset.id;
    detail.innerHTML = `<td colspan="7" class="bg-surface-container-low px-3 py-2"><div class="skeleton h-12 w-full"></div></td>`;
    tr.after(detail);
    try {
      const ev = await api(`/api/v1/weather/evaluation/${tr.dataset.id}`);
      detail.firstElementChild.innerHTML = comparisonHTML(ev);
    } catch (e) {
      detail.firstElementChild.innerHTML = `<p class="text-sm text-error py-2">${esc((e.body && e.body.detail) || MSG_DOWN)}</p>`;
    }
  }

  // -------------------------------------------------------------- historial
  async function loadHistory() {
    const box = $('#history-body'), h = state.hist;
    try {
      const d = await api(`/api/v1/weather/history?source=current&page=${h.page}&page_size=${h.pageSize}`);
      h.total = d.total; h.loaded = true;
      box.innerHTML = d.items.length ? `<div class="overflow-x-auto custom-scroll"><table class="w-full text-sm min-w-[640px]">
        <thead><tr class="text-left text-[11px] uppercase tracking-wider text-tertiary font-headline"><th class="py-1.5 pr-3">Fecha</th><th class="pr-3">Hora</th><th class="pr-3">Temp.</th><th class="pr-3">Humedad</th><th class="pr-3">Lluvia</th><th class="pr-3">Precip.</th><th class="pr-3">Viento</th><th class="pr-3">Nubosidad</th><th>Condición</th></tr></thead>
        <tbody>${d.items.map((r) => `<tr class="border-t border-outline-variant/15"><td class="py-1.5 pr-3">${esc(r.date)}</td><td class="pr-3">${esc(r.time)}</td><td class="pr-3">${temp(r.temperature)}</td><td class="pr-3">${num(r.humidity, '%')}</td><td class="pr-3">${num(r.rain_probability, '%')}</td><td class="pr-3">${num(r.precipitation, ' mm', 1)}</td><td class="pr-3">${num(r.wind_speed, ' km/h', 0)}</td><td class="pr-3">${num(r.cloud_cover, '%')}</td><td>${esc(r.condition)}</td></tr>`).join('')}</tbody></table></div>`
        : 'Aún no hay lecturas guardadas.';
      const pages = Math.max(1, Math.ceil(h.total / h.pageSize));
      $('#hist-page').textContent = `Página ${h.page} de ${pages} · ${h.total} lecturas`;
      $('#hist-prev').disabled = h.page <= 1;
      $('#hist-next').disabled = h.page >= pages;
    } catch (e) {
      box.textContent = (e.body && e.body.detail) || MSG_DOWN;
    }
  }

  // ---------------------------------------------------------------- lugares
  const starsHTML = (value, size = 'text-[18px]') => Array.from({ length: 5 }, (_, i) =>
    icon('star', `${size} ${i < Math.round(value || 0) ? 'filled text-amber-500' : 'text-outline-variant'}`)).join('');
  const plural = (n) => `${n} ${n === 1 ? 'opinión' : 'opiniones'}`;
  function ratingLineHTML(r) {
    if (!r || !r.count) return '<span class="text-sm text-on-surface-variant">Aún sin opiniones: sé la primera persona en opinar.</span>';
    return `<span class="inline-flex" aria-hidden="true">${starsHTML(r.average)}</span>
      <span class="text-sm font-semibold text-on-surface" aria-hidden="true">${Number(r.average).toFixed(1)}</span>
      <span class="text-sm text-on-surface-variant" aria-hidden="true">· ${plural(r.count)}</span>
      <span class="sr-only">Puntuación ${Number(r.average).toFixed(1)} de 5 según ${plural(r.count)}</span>`;
  }

  function placeCardHTML(p) {
    const cover = p.cover
      ? `<img src="${esc(p.cover.thumb_url)}" srcset="${esc(p.cover.thumb_url)} ${Number(p.cover.thumb_width) || 640}w, ${esc(p.cover.url)} ${Number(p.cover.width) || 1600}w" sizes="(min-width: 768px) 50vw, 100vw" alt="${esc(p.cover.alt)}" loading="lazy" decoding="async" class="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105">`
      : `<div class="w-full h-full flex flex-col items-center justify-center gap-1 bg-gradient-to-br from-primary-fixed to-surface-container text-primary">${icon('landscape', 'text-5xl')}<span class="text-xs font-headline font-semibold">Foto próximamente</span></div>`;
    const extras = (p.photos || []).filter((ph) => !ph.is_cover).slice(0, 3)
      .map((ph) => `<img src="${esc(ph.thumb_url)}" alt="${esc(ph.alt)}" loading="lazy" decoding="async" width="96" height="64" class="w-24 h-16 object-cover rounded-lg border border-outline-variant/30">`).join('');
    const spec = (label, value) => value ? `<div><dt class="text-[11px] text-tertiary font-medium">${label}</dt><dd class="text-sm font-semibold text-on-surface mt-0.5">${esc(value)}</dd></div>` : '';
    return `<article class="group bg-surface-container-lowest rounded-2xl overflow-hidden border border-primary/15 shadow-sm hover:shadow-lg hover:-translate-y-1 transition-all duration-300 flex flex-col">
      <div class="h-64 w-full overflow-hidden bg-surface-container">${cover}</div>
      <div class="p-6 flex flex-col gap-4 flex-1">
        <h3 class="font-headline text-2xl font-bold text-primary">${esc(p.name)}</h3>
        <div class="flex flex-wrap items-center gap-x-2 gap-y-1 -mt-2" data-rating-for="${esc(p.slug)}">${ratingLineHTML(p.rating)}</div>
        <p class="text-on-surface-variant whitespace-pre-line">${esc(p.description)}</p>
        ${extras ? `<div class="flex gap-2 flex-wrap">${extras}</div>` : ''}
        <dl class="grid grid-cols-3 gap-3 py-3 border-y border-outline-variant/30 mt-auto">
          ${spec('Dificultad', p.difficulty && p.difficulty.label)}${spec('Caminata', p.hike && p.hike.label)}${spec(p.depth ? p.depth.label : 'Profundidad', p.depth && p.depth.display)}
        </dl>
        <button type="button" data-open-reviews="${esc(p.slug)}" data-place-name="${esc(p.name)}" class="self-start inline-flex items-center gap-2 px-4 py-2 rounded-full border border-primary/40 text-primary hover:bg-primary hover:text-on-primary font-headline font-semibold text-sm transition-colors">
          ${icon('rate_review', 'text-[18px]')}Opiniones y puntuación
        </button>
      </div></article>`;
  }

  async function loadPlaces() {
    const grid = $('#places-grid');
    try {
      const data = await api('/api/v1/places');
      grid.innerHTML = data.items.length ? data.items.map(placeCardHTML).join('')
        : '<p class="text-on-surface-variant">Pronto publicaremos nuestros lugares.</p>';
    } catch (e) {
      grid.innerHTML = errorBox((e.body && e.body.detail) || 'No se pudieron cargar los lugares.');
    }
    grid.setAttribute('aria-busy', 'false');
  }

  // ------------------------------------------------------------------ inicio
  function init() {
    renderCalendar();
    $('#refresh-btn').addEventListener('click', onRefresh);
    $('#cal-prev').addEventListener('click', () => shiftMonth(-1));
    $('#cal-next').addEventListener('click', () => shiftMonth(1));
    $('#cal-today').addEventListener('click', () => {
      state.month = todayISO().slice(0, 7); state.selected = todayISO(); state.days = {};
      renderCalendar(); loadCalendar(); loadDay(state.selected);
    });
    $('#cal-grid').addEventListener('click', (ev) => { const b = ev.target.closest('button[data-date]'); if (b) selectDate(b.dataset.date); });
    const hourly = (ev) => { const tr = ev.target.closest('#hourly-body tr[data-id]'); if (tr) toggleHour(tr); };
    $('#day-body').addEventListener('click', hourly);
    $('#day-body').addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); hourly(ev); } });
    $('#history-details').addEventListener('toggle', (ev) => { if (ev.target.open && !state.hist.loaded) loadHistory(); });
    $('#hist-prev').addEventListener('click', () => { state.hist.page = Math.max(1, state.hist.page - 1); loadHistory(); });
    $('#hist-next').addEventListener('click', () => { state.hist.page += 1; loadHistory(); });

    loadCurrent();
    loadCalendar();
    loadDay(state.selected);
    loadPlaces();
    setInterval(tickFreshness, 30000);
    setInterval(loadCurrent, 5 * 60 * 1000);       // el servidor refresca al proveedor si el dato venció
  }

  // Utilidades compartidas con reviews.js (opiniones y puntuación).
  window.HN = { api, esc, icon, toast, starsHTML, ratingLineHTML, plural };

  init();
})();
