/* Boletería: compra de boletos (S/ 5 por persona, pago simulado), «Mis boletos» y validación en la puerta (administrador).
   Usa las utilidades de app.js (window.HN). El precio y el total los calcula el servidor; aquí solo se muestran. */
(() => {
  'use strict';
  const HN = window.HN;
  const root = document.getElementById('boleteria');
  if (!HN || !root) return;

  const { api, esc, toast } = HN;
  const $ = (sel) => root.querySelector(sel);
  const st = { price: 500, max: 20, advance: 90 };
  const money = (cents) => `S/ ${(cents / 100).toFixed(2)}`;
  const METHOD = { yape: 'Yape', plin: 'Plin', tarjeta: 'Tarjeta', efectivo: 'Efectivo en puerta' };
  const STATUS = {
    pagado: ['Válido', 'bg-secondary-container text-on-secondary-container'],
    usado: ['Usado', 'bg-surface-container-high text-on-surface-variant'],
    anulado: ['Anulado', 'bg-error-container text-on-error-container'],
  };
  const MONTHS = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
  const dateLabel = (iso) => { const [y, m, d] = iso.split('-').map(Number); return `${d} de ${MONTHS[m - 1]} de ${y}`; };
  const peruToday = () => new Date(Date.now() - 5 * 3600 * 1000).toISOString().slice(0, 10);   // Perú = UTC-5
  const addDays = (iso, n) => { const t = new Date(`${iso}T00:00:00Z`); t.setUTCDate(t.getUTCDate() + n); return t.toISOString().slice(0, 10); };
  const errorText = (e, fallback) => {
    const first = e && e.body && e.body.errors && e.body.errors[0];
    return (first && first.message) || (e && e.body && e.body.detail) || fallback;
  };
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

  // ------------------------------------------------------------ precio (público)
  async function loadConfig() {
    try {
      const cfg = await api('/api/v1/tickets/config');
      st.price = cfg.price_cents; st.max = cfg.max_per_order; st.advance = cfg.advance_days;
      $('#tk-price').textContent = money(cfg.price_cents);
    } catch { /* se queda el precio por defecto del texto */ }
  }

  if (!HN.authed) { loadConfig(); return; }            // el visitante solo ve el precio y la invitación a ingresar

  // ------------------------------------------------------------ formulario de compra
  const qty = $('#tk-qty'), date = $('#tk-date'), errBox = $('#tk-error');
  const quantity = () => Math.min(Math.max(parseInt(qty.value, 10) || 1, 1), st.max);

  function updateTotal() {
    const n = quantity();
    $('#tk-sum').textContent = `${plural(n, 'persona', 'personas')} × ${money(st.price)}`;
    $('#tk-total').textContent = money(n * st.price);
  }
  qty.addEventListener('input', updateTotal);
  qty.addEventListener('change', () => { qty.value = quantity(); updateTotal(); });
  $('#tk-minus').addEventListener('click', () => { qty.value = Math.max(quantity() - 1, 1); updateTotal(); });
  $('#tk-plus').addEventListener('click', () => { qty.value = Math.min(quantity() + 1, st.max); updateTotal(); });

  function showError(msg) { errBox.textContent = msg; errBox.hidden = !msg; }

  $('#tk-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    showError('');
    if (!date.value) { showError('Elige la fecha de tu visita.'); date.focus(); return; }
    const btn = $('#tk-buy');
    btn.disabled = true;
    try {
      const method = root.querySelector('input[name="tk-method"]:checked').value;
      const order = await api('/api/v1/tickets', { method: 'POST', body: {
        place: $('#tk-place').value, date: date.value, quantity: quantity(), payment_method: method } });
      showResult(order);
      toast('Compra registrada. Guarda tu código de boleto.');
      loadMine();
      if ($('#tk-summary')) loadSummary();
    } catch (e) {
      showError(errorText(e, 'No se pudo completar la compra. Inténtalo nuevamente.'));
    } finally { btn.disabled = false; }
  });

  function showResult(o) {
    const box = $('#tk-result');
    box.hidden = false;
    box.innerHTML = `
      <p class="font-headline font-bold text-primary flex items-center gap-2"><span class="material-symbols-outlined" aria-hidden="true">check_circle</span>¡Compra registrada!</p>
      <p class="mt-3 text-xs uppercase tracking-wide text-on-surface-variant">Código de tu boleto</p>
      <p class="font-mono text-3xl font-bold text-on-surface tracking-wider" id="tk-result-code">${esc(o.code)}</p>
      <p class="mt-2 text-sm text-on-surface">${esc(o.place_name)} · ${esc(dateLabel(o.visit_date))}</p>
      <p class="text-sm text-on-surface-variant">${plural(o.quantity, 'persona', 'personas')} × ${money(o.unit_price_cents)} = <strong class="text-on-surface">${money(o.total_cents)}</strong> · ${esc(METHOD[o.payment_method] || o.payment_method)} (pago simulado)</p>
      <div class="mt-3 flex flex-wrap gap-2">
        <button type="button" data-copy="${esc(o.code)}" class="px-4 py-1.5 rounded-full border border-outline-variant/60 bg-surface-container-lowest text-sm font-headline font-semibold hover:bg-surface-container-high">Copiar código</button>
        <button type="button" data-print class="px-4 py-1.5 rounded-full border border-outline-variant/60 bg-surface-container-lowest text-sm font-headline font-semibold hover:bg-surface-container-high">Imprimir</button>
      </div>`;
    box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  root.addEventListener('click', async (ev) => {
    const copy = ev.target.closest('[data-copy]');
    if (copy) {
      try { await navigator.clipboard.writeText(copy.dataset.copy); toast('Código copiado.'); }
      catch { toast('No se pudo copiar. Anótalo manualmente.', 'warn'); }
      return;
    }
    if (ev.target.closest('[data-print]')) { window.print(); return; }
    const cancel = ev.target.closest('[data-cancel]');
    if (cancel) {
      if (!cancel.dataset.armed) {
        cancel.dataset.armed = '1'; cancel.textContent = 'Confirmar anulación';
        setTimeout(() => { delete cancel.dataset.armed; cancel.textContent = 'Anular'; }, 5000);
        return;
      }
      cancel.disabled = true;
      try {
        await api('/api/v1/tickets/cancel', { method: 'POST', body: { code: cancel.dataset.cancel } });
        toast('Boleto anulado.');
        loadMine();
        if ($('#tk-summary')) loadSummary();
      } catch (e) { toast(errorText(e, 'No se pudo anular el boleto.'), 'error'); cancel.disabled = false; }
    }
  });

  // ------------------------------------------------------------ mis boletos
  function ticketCard(o) {
    const [label, cls] = STATUS[o.status] || [o.status, ''];
    const canCancel = o.status === 'pagado' && o.visit_date > peruToday();
    return `<li class="rounded-lg border border-outline-variant/40 bg-surface-container-low p-4 flex flex-wrap items-center justify-between gap-3">
      <div class="min-w-0">
        <p class="font-mono font-bold text-on-surface tracking-wide">${esc(o.code)}</p>
        <p class="text-sm text-on-surface-variant truncate">${esc(o.place_name)} · ${esc(dateLabel(o.visit_date))}</p>
        <p class="text-sm text-on-surface-variant">${plural(o.quantity, 'persona', 'personas')} · ${money(o.total_cents)}</p>
      </div>
      <div class="flex items-center gap-2">
        <span class="px-2.5 py-1 rounded-full text-xs font-headline font-bold ${cls}">${esc(label)}</span>
        ${canCancel ? `<button type="button" data-cancel="${esc(o.code)}" class="px-3 py-1 rounded-full border border-outline-variant/60 text-xs font-headline font-semibold hover:bg-surface-container-high">Anular</button>` : ''}
      </div></li>`;
  }

  async function loadMine() {
    const list = $('#tk-mine');
    try {
      const { items } = await api('/api/v1/tickets/mine');
      list.innerHTML = items.length ? items.map(ticketCard).join('')
        : '<li class="text-on-surface-variant text-sm">Aún no has comprado boletos.</li>';
    } catch { list.innerHTML = '<li class="text-error text-sm">No se pudieron cargar tus boletos.</li>'; }
  }

  // ------------------------------------------------------------ puerta y ventas (administrador)
  async function loadSummary() {
    const box = $('#tk-summary');
    try {
      const s = await api('/api/v1/admin/tickets/summary');
      const kpi = (t, v, sub) => `<div class="rounded-lg bg-surface-container p-3"><dt class="text-xs text-on-surface-variant">${t}</dt><dd class="font-headline text-xl font-bold text-on-surface">${v}</dd><p class="text-xs text-on-surface-variant">${sub}</p></div>`;
      box.innerHTML = `<dl class="grid grid-cols-2 gap-3">
          ${kpi('Vendidos hoy', s.sold_today.tickets, money(s.sold_today.revenue_cents))}
          ${kpi('Entran hoy', s.entering_today.tickets, `${s.used_today} ya validados`)}
          ${kpi('Últimos 30 días', s.sold_30_days.tickets, money(s.sold_30_days.revenue_cents))}
          ${kpi('Total vendido', s.sold_total.tickets, money(s.sold_total.revenue_cents))}
        </dl>
        <p class="mt-3 text-xs font-semibold text-on-surface-variant">Boletos para los próximos 7 días</p>
        <p class="text-sm text-on-surface">${s.upcoming.length ? s.upcoming.map((u) => `${esc(dateLabel(u.date))}: <strong>${u.tickets}</strong>`).join(' · ') : 'Sin ventas para esos días.'}</p>
        <p class="mt-3 text-xs font-semibold text-on-surface-variant">Por lugar</p>
        <p class="text-sm text-on-surface">${s.by_place.length ? s.by_place.map((p) => `${esc(p.place_name)}: <strong>${p.tickets}</strong> (${money(p.revenue_cents)})`).join(' · ') : 'Sin ventas todavía.'}</p>`;
    } catch { box.innerHTML = '<p class="text-error text-sm">No se pudo cargar el resumen de ventas.</p>'; }
  }

  function bindValidate() {
    const form = $('#tk-validate');
    if (!form) return;
    const msg = $('#tk-validate-msg');
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const code = $('#tk-code').value.trim().toUpperCase();
      msg.hidden = false;
      if (!/^HN-[A-Z0-9]{8}$/.test(code)) { msg.className = 'text-sm text-error'; msg.textContent = 'El código tiene el formato HN-XXXXXXXX.'; return; }
      try {
        const o = await api('/api/v1/admin/tickets/validate', { method: 'POST', body: { code } });
        msg.className = 'text-sm text-primary font-semibold';
        msg.textContent = `Entrada válida: ${plural(o.quantity, 'persona', 'personas')} a ${o.place_name} (${o.buyer_name}).`;
        $('#tk-code').value = '';
        loadSummary();
      } catch (e) { msg.className = 'text-sm text-error'; msg.textContent = errorText(e, 'No se pudo validar el boleto.'); }
    });
  }

  // ------------------------------------------------------------ arranque
  async function loadPlaces() {
    try {
      const data = await api('/api/v1/places');
      $('#tk-place').innerHTML = data.items.map((p) => `<option value="${esc(p.slug)}">${esc(p.name)}</option>`).join('');
    } catch { $('#tk-place').innerHTML = '<option value="">No se pudieron cargar los lugares</option>'; }
  }

  const today = peruToday();
  date.min = today; date.max = addDays(today, 90); date.value = today;
  updateTotal();
  loadConfig().then(() => { date.max = addDays(today, st.advance); updateTotal(); });
  loadPlaces();
  loadMine();
  bindValidate();
  if ($('#tk-summary')) loadSummary();
})();
