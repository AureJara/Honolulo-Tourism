/* Opiniones y puntuación de los lugares: ventana con el resumen, el formulario del visitante y, para el
   administrador, los botones de fijar y eliminar. Todo texto escrito por usuarios se escapa antes de mostrarse. */
(() => {
  'use strict';

  const HN = window.HN;
  const dialog = document.getElementById('reviews-dialog');
  if (!HN || !dialog || typeof dialog.showModal !== 'function') return;
  const { api, esc, icon, toast, starsHTML, ratingLineHTML, plural } = HN;

  const main = document.getElementById('contenido');
  const isAdmin = !!main && main.dataset.role === 'admin';
  const body = document.getElementById('reviews-body');
  const title = document.getElementById('reviews-title');
  const PAGE = 10;
  const MAX = 1000;
  const dateFmt = new Intl.DateTimeFormat('es-PE', { timeZone: 'America/Lima', day: 'numeric', month: 'long', year: 'numeric' });
  const st = { slug: '', name: '', mine: null, items: [], total: 0, summary: null, draft: null, token: 0 };

  const base = () => `/api/v1/places/${encodeURIComponent(st.slug)}/reviews`;
  const btnPrimary = 'inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-primary hover:bg-primary-container text-on-primary font-headline font-semibold text-sm transition-colors disabled:opacity-60 disabled:cursor-wait';
  const btnGhost = 'inline-flex items-center gap-1.5 px-3 py-2 rounded-xl border border-outline-variant/60 text-sm font-semibold hover:bg-surface-container-high transition-colors';

  // ---------------------------------------------------------------- carga
  async function load(reset = true) {
    const token = ++st.token;
    try {
      const [list, mine] = await Promise.all([
        api(`${base()}?limit=${PAGE}&offset=${reset ? 0 : st.items.length}`),
        reset ? api(`${base()}/mine`) : Promise.resolve({ review: st.mine }),
      ]);
      if (token !== st.token) return;                       // llegó tarde: se abrió otro lugar
      st.items = reset ? list.items : st.items.concat(list.items);
      st.total = list.total; st.summary = list.summary; st.mine = mine.review;
      syncCard();
      render();
    } catch (e) {
      if (token !== st.token) return;
      body.innerHTML = `<div role="alert" class="p-4 rounded-lg bg-error-container text-on-error-container text-sm">${esc((e.body && e.body.detail) || 'No se pudieron cargar las opiniones. Inténtalo nuevamente.')}</div>`;
    }
  }

  // La tarjeta del lugar (fuera de la ventana) refleja el promedio actualizado.
  function syncCard() {
    document.querySelectorAll(`[data-rating-for="${CSS.escape(st.slug)}"]`).forEach((el) => { el.innerHTML = ratingLineHTML(st.summary); });
  }

  // ------------------------------------------------------------ render
  function summaryHTML() {
    const s = st.summary;
    if (!s || !s.count) return '<p class="text-on-surface-variant">Todavía no hay opiniones de este lugar. ¡Cuéntanos tu experiencia!</p>';
    const rows = [5, 4, 3, 2, 1].map((n) => {
      const c = s.distribution[String(n)] || 0;
      return `<div class="flex items-center gap-2 text-xs"><span class="w-10 text-on-surface-variant">${n} ${icon('star', 'text-[13px] align-middle filled text-amber-500')}</span>
        <div class="flex-1 h-2 rounded-full bg-surface-container-high overflow-hidden" aria-hidden="true"><div class="h-full bg-amber-500" style="width:${Math.round((c / s.count) * 100)}%"></div></div>
        <span class="w-8 text-right text-tertiary">${c}</span></div>`;
    }).join('');
    return `<div class="grid grid-cols-1 sm:grid-cols-[auto_1fr] gap-6 items-center">
      <div class="text-center sm:px-4"><p class="font-headline text-5xl font-bold text-primary leading-none">${Number(s.average).toFixed(1)}</p>
        <div class="mt-2" aria-hidden="true">${starsHTML(s.average, 'text-[20px]')}</div>
        <p class="mt-1 text-sm text-on-surface-variant">${plural(s.count)}</p></div>
      <div class="flex flex-col gap-1.5" role="group" aria-label="Cuántas personas dieron cada puntuación">${rows}</div></div>`;
  }

  function formHTML() {
    const seed = st.draft || (st.mine ? { rating: st.mine.rating, comment: st.mine.comment || '' } : { rating: 0, comment: '' });
    const stars = [1, 2, 3, 4, 5].map((n) => `<input type="radio" id="rv-star-${n}" name="rating" value="${n}" class="sr-only peer" ${seed.rating === n ? 'checked' : ''}>
      <label for="rv-star-${n}" class="cursor-pointer p-0.5 rounded peer-focus-visible:ring-2 peer-focus-visible:ring-primary">${icon('star', 'text-[34px] text-outline-variant')}<span class="sr-only">${n} ${n === 1 ? 'estrella' : 'estrellas'}</span></label>`).join('');
    return `<form id="review-form" class="mt-6 p-4 rounded-xl border border-outline-variant/40 bg-surface-container-low" novalidate>
      <h3 class="font-headline text-lg font-bold">${st.mine ? 'Tu opinión' : 'Deja tu opinión'}</h3>
      <fieldset class="mt-3">
        <legend class="text-xs font-headline font-semibold uppercase tracking-wider text-primary">Tu puntuación</legend>
        <div class="mt-1 flex items-center gap-0.5" id="star-group">${stars}</div>
        <p id="rating-error" class="text-xs text-error mt-1" role="alert"></p>
      </fieldset>
      <div class="mt-3 flex flex-col gap-1.5">
        <label for="review-comment" class="text-xs font-headline font-semibold uppercase tracking-wider text-primary">Comentario (opcional)</label>
        <textarea id="review-comment" rows="4" maxlength="${MAX}" placeholder="¿Cómo fue tu visita? El agua, el sendero, la seguridad…" class="w-full px-4 py-2.5 rounded-xl border border-outline-variant/60 bg-surface text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-secondary-container">${esc(seed.comment)}</textarea>
        <div class="flex justify-between gap-3 text-xs text-tertiary"><span>Se publicará con tu nombre y la inicial de tu apellido. No se permiten enlaces.</span><span id="comment-count" aria-hidden="true">0/${MAX}</span></div>
        <p id="comment-error" class="text-xs text-error" role="alert"></p>
      </div>
      <p id="form-error" class="mt-2 text-sm text-error" role="alert"></p>
      <div class="mt-3 flex flex-wrap items-center gap-3">
        <button type="submit" class="${btnPrimary}">${icon('send', 'text-[18px]')}${st.mine ? 'Actualizar mi opinión' : 'Publicar opinión'}</button>
        ${st.mine ? `<button type="button" data-action="delete-mine" class="${btnGhost} text-error border-error/40">${icon('delete', 'text-[18px]')}Eliminar mi opinión</button>` : ''}
      </div></form>`;
  }

  function itemHTML(r) {
    const own = st.mine && st.mine.id === r.id;
    const badge = (cls, ic, text) => `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold ${cls}">${icon(ic, 'text-[14px]')}${text}</span>`;
    const tools = isAdmin ? `<div class="mt-3 flex flex-wrap gap-2 text-xs">
        <button type="button" data-action="pin" data-id="${esc(r.id)}" data-pinned="${r.pinned}" class="${btnGhost} !px-2.5 !py-1">${icon('push_pin', 'text-[16px]')}${r.pinned ? 'Quitar de fijadas' : 'Fijar'}<span class="sr-only"> la opinión de ${esc(r.author)}</span></button>
        <button type="button" data-action="moderate-delete" data-id="${esc(r.id)}" class="${btnGhost} !px-2.5 !py-1 text-error border-error/40">${icon('delete', 'text-[16px]')}Eliminar<span class="sr-only"> la opinión de ${esc(r.author)}</span></button>
      </div>` : '';
    return `<li class="p-4 rounded-xl border ${r.pinned ? 'border-primary/40 bg-secondary-container/25' : 'border-outline-variant/30 bg-surface-container-lowest'}">
      <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span class="font-headline font-semibold">${esc(r.author)}</span>
        <span class="inline-flex" aria-hidden="true">${starsHTML(r.rating, 'text-[16px]')}</span><span class="sr-only">${r.rating} de 5 estrellas</span>
        <span class="text-xs text-tertiary">${esc(dateFmt.format(new Date(r.created_at)))}</span>
        ${r.pinned ? badge('bg-secondary-container text-on-secondary-container', 'push_pin', 'Fijada') : ''}${own ? badge('bg-surface-container-high text-on-surface', 'person', 'Tu opinión') : ''}
      </div>
      ${r.comment ? `<p class="mt-2 text-sm text-on-surface-variant whitespace-pre-line break-words">${esc(r.comment)}</p>` : ''}
      ${tools}</li>`;
  }

  function render() {
    title.textContent = st.name;
    body.innerHTML = `${summaryHTML()}${formHTML()}
      <h3 class="mt-8 font-headline text-lg font-bold">Lo que dicen los visitantes</h3>
      ${st.items.length ? `<ul class="mt-3 flex flex-col gap-3">${st.items.map(itemHTML).join('')}</ul>` : '<p class="mt-3 text-sm text-on-surface-variant">Aún no hay opiniones publicadas.</p>'}
      ${st.items.length < st.total ? `<button type="button" data-action="more" class="${btnGhost} mt-4">Ver más opiniones</button>` : ''}`;
    paintStars();
    updateCount();
  }

  function paintStars() {
    const checked = body.querySelector('input[name="rating"]:checked');
    const value = checked ? Number(checked.value) : 0;
    body.querySelectorAll('#star-group label .material-symbols-outlined').forEach((el, i) => {
      el.classList.toggle('filled', i < value);
      el.classList.toggle('text-amber-500', i < value);
      el.classList.toggle('text-outline-variant', i >= value);
    });
  }

  function updateCount() {
    const area = body.querySelector('#review-comment');
    if (area) body.querySelector('#comment-count').textContent = `${area.value.length}/${MAX}`;
  }

  function rememberDraft() {
    const checked = body.querySelector('input[name="rating"]:checked');
    st.draft = { rating: checked ? Number(checked.value) : 0, comment: (body.querySelector('#review-comment') || {}).value || '' };
  }

  // ------------------------------------------------------------- acciones
  function showFormErrors(e) {
    const list = (e.body && e.body.errors) || [];
    const put = (id, text) => { const el = body.querySelector(id); if (el) el.textContent = text || ''; };
    const byField = (f) => (list.find((x) => x.field === f) || {}).message;
    put('#rating-error', byField('rating'));
    put('#comment-error', byField('comment'));
    if (!list.length) put('#form-error', (e.body && e.body.detail) || 'No se pudo guardar tu opinión. Inténtalo nuevamente.');
  }

  async function submitReview(form) {
    ['#rating-error', '#comment-error', '#form-error'].forEach((id) => { body.querySelector(id).textContent = ''; });
    const checked = form.querySelector('input[name="rating"]:checked');
    if (!checked) {
      body.querySelector('#rating-error').textContent = 'Elige de 1 a 5 estrellas.';
      form.querySelector('input[name="rating"]').focus();
      return;
    }
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const res = await api(`${base()}/mine`, { method: 'PUT', body: { rating: Number(checked.value), comment: form.querySelector('#review-comment').value } });
      st.draft = null;
      toast(res.created ? '¡Gracias! Tu opinión ya es pública.' : 'Tu opinión se actualizó.');
      await load(true);
    } catch (e) {
      showFormErrors(e);
      button.disabled = false;
    }
  }

  async function run(action, okMessage) {
    try {
      await action();
      if (okMessage) toast(okMessage);
      await load(true);
    } catch (e) {
      toast((e.body && e.body.detail) || 'No se pudo completar la acción.', 'error');
    }
  }

  function openReviews(slug, name) {
    Object.assign(st, { slug, name, mine: null, items: [], total: 0, summary: null, draft: null });
    title.textContent = name;
    body.innerHTML = '<div class="skeleton h-40 w-full"></div>';
    dialog.showModal();
    load(true);
  }

  // ------------------------------------------------------------ eventos
  document.addEventListener('click', (ev) => {
    const opener = ev.target.closest('[data-open-reviews]');
    if (opener) openReviews(opener.dataset.openReviews, opener.dataset.placeName || '');
  });

  dialog.addEventListener('close', () => { st.token += 1; });                 // ignora respuestas que lleguen tarde

  // Cada botón de la ventana declara su acción en ``data-action``. Para añadir una (p. ej. «reportar») basta una entrada
  // nueva en esta tabla y un botón con ese nombre: el manejador de clics no cambia (principio abierto/cerrado).
  const moderate = (el, request, okMessage) => run(() => api(`/api/v1/reviews/${encodeURIComponent(el.dataset.id)}${request.path}`, request.options), okMessage);
  const ACTIONS = {
    close: () => dialog.close(),
    more: (el) => { el.disabled = true; load(false); },
    'delete-mine': () => {
      if (!confirm('¿Eliminar tu opinión? Podrás escribir otra cuando quieras.')) return;
      run(() => api(`${base()}/mine`, { method: 'DELETE' }).then(() => { st.draft = null; }), 'Tu opinión se eliminó.');
    },
    pin: (el) => {
      const pinned = el.dataset.pinned !== 'true';
      moderate(el, { path: '/pin', options: { method: 'PUT', body: { pinned } } },
        pinned ? 'Opinión fijada: aparece primero.' : 'Se quitó de las fijadas.');
    },
    'moderate-delete': (el) => {
      if (confirm('¿Eliminar esta opinión? No se puede deshacer.')) moderate(el, { path: '', options: { method: 'DELETE' } }, 'Opinión eliminada.');
    },
  };

  dialog.addEventListener('click', (ev) => {
    if (ev.target === dialog) { ACTIONS.close(); return; }          // clic en el fondo oscuro
    const trigger = ev.target.closest('[data-action]');
    const action = trigger && ACTIONS[trigger.dataset.action];
    if (action) action(trigger);
  });

  dialog.addEventListener('change', (ev) => { if (ev.target.name === 'rating') { paintStars(); rememberDraft(); } });
  dialog.addEventListener('input', (ev) => { if (ev.target.id === 'review-comment') { updateCount(); rememberDraft(); } });
  dialog.addEventListener('submit', (ev) => {
    if (ev.target.id === 'review-form') { ev.preventDefault(); submitReview(ev.target); }
  });
})();
