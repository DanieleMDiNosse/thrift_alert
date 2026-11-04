'use strict';
(() => {
  const $ = (id) => document.getElementById(id);
  const sites = ['subito', 'vinted', 'wallapop'];
  const names = {subito: 'Subito', vinted: 'Vinted', wallapop: 'Wallapop'};
  const schedules = [['*/15 * * * *', 'Ogni 15 minuti'], ['*/30 * * * *', 'Ogni 30 minuti'], ['0 * * * *', 'Ogni ora']];
  let state = null;
  let editing = null;
  let returnFocus = null;
  let toastTimer;
  let loading = false;
  let notifying = false;
  let showAllListings = false;
  let searchTimer;
  let listingSite = '', listingOffset = 0, listingWatch = '', listingOrder = 'balanced_recency', listingQuery = '', listingView = 'all';
  const expandedDescriptions = new Set();
  const loadingDescriptions = new Set();
  const loadedDescriptions = new Map();
  const pending = new Set();
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined && text !== null) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const button = (text, handler, className = 'text-button') => {
    const element = node('button', text, className);
    element.type = 'button';
    element.addEventListener('click', handler);
    return element;
  };
  const safeURL = (raw) => {
    try {
      const url = new URL(raw);
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch (_) { return null; }
  };
  const date = (value) => {
    if (value === null || value === undefined || value === '') return 'Mai eseguita';
    const parsed = new Date(typeof value === 'number' ? value * 1000 : value);
    return Number.isNaN(parsed.getTime()) ? 'Data non disponibile' : new Intl.DateTimeFormat('it-IT', {day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: state?.timezone || 'Europe/Rome'}).format(parsed);
  };
  const price = (value, currency = 'EUR') => {
    if (value === null || value === undefined || value === '') return 'Prezzo non indicato';
    try { return new Intl.NumberFormat('it-IT', {style: 'currency', currency}).format(value); }
    catch (_) { return `${value} ${currency}`; }
  };
  function toast(message) {
    clearTimeout(toastTimer);
    $('toast').textContent = message;
    $('toast').hidden = false;
    toastTimer = setTimeout(() => { $('toast').hidden = true; }, 6500);
  }
  async function request(path, payload) {
    const options = {method: payload === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json'}};
    if (payload !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.headers['X-CSRF-Token'] = document.querySelector('meta[name="csrf-token"]').content;
      options.body = JSON.stringify(payload);
    }
    let response;
    try { response = await fetch(path, options); }
    catch (_) { throw new Error('Connessione al servizio locale non disponibile. Verifica che sia in esecuzione.'); }
    let data;
    try { data = await response.json(); }
    catch (_) { throw new Error('Risposta del servizio non valida. Riprova.'); }
    if (!response.ok) throw new Error(typeof data.error === 'string' ? data.error : 'Operazione non riuscita. Riprova.');
    return data;
  }
  async function refresh() {
    if (loading) return false;
    loading = true;
    for (const id of ['listing-view', 'listing-watch', 'listing-site', 'listing-order', 'listing-prev', 'listing-next']) if ($(id)) $(id).disabled = true;
    try {
      const params = new URLSearchParams();
      if (listingSite) params.set('listing_site', listingSite);
      if (listingOffset) params.set('listing_offset', String(listingOffset));
      if (listingWatch) params.set('watch_id', listingWatch);
      if (listingOrder !== 'balanced_recency') params.set('listing_order', listingOrder);
      if (listingQuery) params.set('listing_q', listingQuery);
      if (listingView && listingView !== 'all') params.set('listing_view', listingView);
      let next = await request('/api/state' + (params.size ? `?${params}` : ''));
      if (!Array.isArray(next.watches) || !Array.isArray(next.listings)) throw new Error('Stato del servizio non valido.');
      if (listingWatch && !next.watches.some(w => String(w.id) === listingWatch)) {
        listingWatch = ''; listingOffset = 0;
        params.delete('watch_id'); params.delete('listing_offset');
        next = await request('/api/state' + (params.size ? `?${params}` : ''));
        toast('La ricerca selezionata non è più disponibile. Visualizzo tutte le ricerche.');
      }
      state = next;
      listingOffset = next.listing_page?.offset ?? listingOffset;
      render();
      $('load-error').hidden = true;
      return true;
    } catch (error) {
      $('load-error').textContent = error.message;
      $('load-error').hidden = false;
      if (!state) $('watches').replaceChildren(node('p', 'Impossibile caricare le ricerche. Il collegamento verrà ritentato automaticamente.', 'muted'));
      $('monitor-label').textContent = 'Servizio non raggiungibile';
      $('monitor-status').classList.remove('running');
      $('test-notify').disabled = true;
      return false;
    } finally { loading = false; $('watches').setAttribute('aria-busy', 'false'); renderListingControls(); }
  }
  async function mutate(key, path, payload, message, trigger) {
    if (pending.has(key)) return;
    pending.add(key);
    if (trigger) trigger.disabled = true;
    try {
      await request(path, payload);
      const verified = await refresh();
      toast(verified ? message : 'Operazione inviata. Aggiornamento dello stato non riuscito: verifica prima di riprovare.');
    } catch (error) { toast(error.message); }
    finally {
      pending.delete(key);
      if (state && $('load-error').hidden) render();
      else if (trigger?.isConnected) trigger.disabled = false;
    }
  }
  function render() {
    $('monitor-status').classList.toggle('running', Boolean(state.monitor_running));
    $('monitor-label').textContent = state.monitor_running ? 'Monitor attivo' : 'Monitor non attivo';
    $('monitor-help').hidden = Boolean(state.monitor_running);
    $('notification-label').textContent = state.notification_configured ? 'Notifiche configurate' : 'Notifiche non configurate';
    $('test-notify').disabled = !state.notification_configured || notifying;
    const cards = state.watches.map(renderWatch);
    if (!cards.length) {
      const empty = node('div', undefined, 'empty-state');
      empty.append(node('h3', 'La prossima scoperta parte da qui'), node('p', 'Aggiungi una ricerca e scegli i mercatini da tenere d’occhio.'), button('Nuova ricerca', () => openEditor(), 'primary'));
      cards.push(empty);
    }
    $('watches').replaceChildren(...cards);
    renderListings();
  }
  function renderWatch(watch) {
    const card = node('article', undefined, 'watch-card');
    const top = node('div', undefined, 'card-top');
    top.append(node('h3', watch.name), node('span', watch.enabled ? 'Attiva' : 'In pausa', `badge${watch.enabled ? '' : ' paused'}`));
    const range = [];
    if (watch.min_price_eur !== null && watch.min_price_eur !== undefined) range.push(`Da ${price(watch.min_price_eur)}`);
    if (watch.max_price_eur !== null && watch.max_price_eur !== undefined) range.push(`fino a ${price(watch.max_price_eur)}`);
    const summary = node('p', range.length ? range.join(' · ') : 'Qualsiasi prezzo', 'watch-summary');
    const sources = node('div', undefined, 'source-list');
    for (const [site, source] of Object.entries(watch.sources || {})) {
      const query = (watch.queries || []).find((item) => item.site === site);
      const attempt = query?.last_attempt;
      const failed = attempt ? ['error', 'failed'].includes(attempt.status) : Boolean(query?.error);
      const retryAt = Math.max(query?.recovery?.retry_at || 0, query?.site_retry_at || 0);
      const cooldown = (query?.site_retry_at || 0) > Date.now() / 1000;
      const code = query?.recovery?.error_code || attempt?.error_code;
      const deferred = code === 'description_pending';
      const tone = (!source.enabled || !watch.enabled) ? 'paused' : (failed && !deferred) ? 'error' : (cooldown || deferred || query?.status === 'running' || !query?.seeded) ? 'pending' : 'ok';
      const sourceBlock = node('div', undefined, `source-item is-${tone}`);
      const row = node('div', undefined, 'source-row');
      const scanErrors = {timeout: 'Tempo limite superato durante la scansione.', network: 'Connessione o DNS temporaneamente non disponibili.', browser_closed: 'Browser interrotto: nuovo tentativo programmato.', temporary_server: 'Mercatino temporaneamente non disponibile.', rate_limited: 'Limite richieste del mercatino: pausa temporanea.', description_pending: 'Verifica descrizioni in corso, a piccoli gruppi.', access_denied: 'Accesso negato dal mercatino.', invalid_response: 'Risposta del mercatino cambiata o non valida: verifica necessaria.', fetch_error: 'Errore imprevisto durante la scansione.'};
      const scanError = failed ? (scanErrors[code] || query?.error || 'Ultima scansione non riuscita.') : null;
      let status;
      if (!source.enabled) status = 'Fonte in pausa';
      else if (!watch.enabled) status = 'Ricerca in pausa';
      else if (query?.status === 'running') status = 'Scansione in corso';
      else if (cooldown) status = 'In pausa per limite richieste';
      else if (deferred) status = 'Verifica descrizioni in attesa';
      else if (failed) status = `Ultima scansione non riuscita · ${date(attempt?.finished_at ?? attempt?.started_at ?? query.last_run_at)}`;
      else if (!query?.seeded) status = 'Prima scansione';
      else status = `Ultima scansione · ${date(query.last_run_at)}`;
      row.append(node('span', names[site] || site, 'source-name'), node('span', status, 'source-state'));
      sourceBlock.append(row);
      if (query?.coverage) {
        const c = query.coverage;
        const label = c.complete ? 'Paginazione completata' : String(c.reason || '').split(',').some(reason => reason.trim() === 'page_limit') ? 'Limite pagine raggiunto' : 'Scansione parziale';
        const observed = query.coverage_observed_at != null ? ` · ${date(query.coverage_observed_at)}` : '';
        sourceBlock.append(node('p', `${failed ? 'Copertura precedente · ' : ''}${label} · ${c.pages} pagine · ${c.phrases} ${c.phrases === 1 ? 'ricerca' : 'ricerche'}${observed}`, 'source-detail'));
      } else if (query?.seeded) {
        sourceBlock.append(node('p', 'Copertura non ancora misurata', 'source-detail'));
      }
      if (failed) sourceBlock.append(node('p', scanError, deferred ? 'source-detail' : 'source-error'));
      if (retryAt > Date.now() / 1000 && source.enabled && watch.enabled && query?.status !== 'running') {
        sourceBlock.append(node('p', `Ripresa automatica · ${date(retryAt)}`, 'source-detail'));
      }
      if (query?.description_pending > 0) sourceBlock.append(node('p', `${query.description_pending} descrizioni ancora da verificare`, 'source-detail'));
      if (query?.description_skipped > 0) sourceBlock.append(node('p', `${query.description_skipped} ${query.description_skipped === 1 ? 'annuncio escluso' : 'annunci esclusi'}: descrizione non leggibile, nuovo tentativo più tardi`, 'source-detail'));
      if (query?.error || query?.status === 'error') {
        const retry = button('Riprova scansione', (event) => {
          if (window.confirm(`Vuoi riprovare la scansione di ${names[site] || site}? L’operazione contatterà il mercatino e potrà inviare notifiche.`)) {
            mutate(`query-${query.id}`, `/api/queries/${encodeURIComponent(query.id)}/retry`, {}, 'Richiesta di scansione inviata.', event.currentTarget);
          }
        }, 'text-button retry-button');
        retry.disabled = cooldown || deferred || pending.has(`query-${query.id}`);
        sourceBlock.append(retry);
      }
      sources.append(sourceBlock);
    }
    if (!sources.childElementCount) sources.append(node('span', 'Nessuna fonte configurata', 'muted'));
    const actions = node('div', undefined, 'card-actions');
    const edit = button('Modifica', () => openEditor(watch));
    const toggle = button(watch.enabled ? 'Metti in pausa' : 'Attiva', (event) => mutate(watch.id, `/api/watches/${encodeURIComponent(watch.id)}/toggle`, {enabled: !watch.enabled}, watch.enabled ? 'Ricerca messa in pausa.' : 'Ricerca attivata.', event.currentTarget));
    const remove = button('Elimina', (event) => {
      if (window.confirm(`Eliminare la ricerca “${watch.name}”? L’operazione non può essere annullata.`)) mutate(watch.id, `/api/watches/${encodeURIComponent(watch.id)}/remove`, {}, 'Ricerca eliminata.', event.currentTarget);
    }, 'text-button danger');
    for (const action of [edit, toggle, remove]) action.disabled = pending.has(watch.id);
    actions.append(edit, toggle, remove);
    card.append(top, summary, sources, actions);
    return card;
  }
  async function loadDescription(listing) {
    const key = `${listing.site}:${listing.site_listing_id}`;
    if (loadingDescriptions.has(key)) return;
    loadingDescriptions.add(key);
    renderListings();
    try {
      const result = await request('/api/listings/description', {site: listing.site, id: listing.site_listing_id});
      loadedDescriptions.set(key, result.description);
    } catch (error) { toast(error.message); }
    finally { loadingDescriptions.delete(key); renderListings(); }
  }
  async function toggleListingFlag(listing, patch, trigger) {
    const key = `flag:${listing.site}:${listing.site_listing_id}`;
    if (pending.has(key)) return;
    pending.add(key);
    if (trigger) trigger.disabled = true;
    try {
      await request('/api/listings/flag', {site: listing.site, id: listing.site_listing_id, ...patch});
      await refresh();
      if ('hidden' in patch) toast(patch.hidden ? 'Annuncio nascosto. Puoi rivederlo dal filtro “Nascosti”.' : 'Annuncio ripristinato.');
      else if ('starred' in patch) toast(patch.starred ? 'Annuncio salvato tra i preferiti ★.' : 'Annuncio rimosso dai salvati.');
    } catch (error) { toast(error.message); }
    finally {
      pending.delete(key);
      if (trigger?.isConnected) trigger.disabled = false;
    }
  }
  function renderListingControls() {
    if (!state || !$('listing-site')) return;
    const meta = state.listing_page || {}, counts = meta.site_counts || {};
    const total = meta.total ?? state.listings.length, offset = meta.offset ?? 0;
    const visible = showAllListings ? state.listings.length : Math.min(8, state.listings.length);
    $('listing-summary').textContent = sites.map(site => `${names[site]}: ${counts[site] ?? state.listings.filter(l => l.site === site).length}`).join(' · ');
    $('listing-page-label').textContent = `${total ? offset + 1 : 0}–${offset + state.listings.length} di ${total} · ${visible} visibili`;
    $('listing-site').value = listingSite; $('listing-site').disabled = loading;
    if ($('listing-view')) {
      const viewSelect = $('listing-view');
      const starredCount = meta.starred_count ?? 0, hiddenCount = meta.hidden_count ?? 0;
      if (viewSelect.options[1]) viewSelect.options[1].textContent = starredCount ? `Salvati ★ (${starredCount})` : 'Salvati ★';
      if (viewSelect.options[2]) viewSelect.options[2].textContent = hiddenCount ? `Nascosti (${hiddenCount})` : 'Nascosti';
      viewSelect.value = listingView; viewSelect.disabled = loading;
    }
    if ($('listing-search') && document.activeElement !== $('listing-search')) {
      $('listing-search').value = listingQuery;
    }
    if ($('listing-order')) { $('listing-order').value = listingOrder; $('listing-order').disabled = loading; }
    if ($('listing-order-note')) $('listing-order-note').textContent = listingOrder === 'balanced_recency'
      ? 'Panoramica bilanciata tra mercatini; ultimi incontri all’interno di ciascun sito.'
      : 'Prezzi richiesti, ordinati per valuta. Spedizione e commissioni escluse; prezzi non indicati in fondo.';
    if ($('listing-watch')) {
      const select = $('listing-watch');
      const signature = JSON.stringify(state.watches.map(w => [w.id, w.name, w.enabled]));
      if (select.dataset.signature !== signature) {
        select.replaceChildren(new Option('Tutte le ricerche', ''));
        state.watches.forEach(w => select.append(new Option(w.name + (w.enabled ? '' : ' (in pausa)'), String(w.id))));
        select.dataset.signature = signature;
      }
      select.value = listingWatch; select.disabled = loading;
    }
    $('listing-prev').disabled = loading || offset === 0;
    $('listing-next').disabled = loading || !meta.has_more;
    renderPriceDistribution(meta.price_distribution);
  }
  function renderPriceDistribution(dist) {
    const box = $('price-distribution');
    if (!box) return;
    if (!listingWatch || !dist || !dist.count) {
      box.hidden = true;
      box.replaceChildren();
      return;
    }
    const watchObj = (state?.watches || []).find(w => String(w.id) === String(listingWatch));
    const head = node('div', undefined, 'price-dist-head');
    const titleWrap = node('div');
    titleWrap.append(
      node('span', 'DISTRIBUZIONE PREZZI', 'price-dist-eyebrow'),
      node('h3', watchObj ? watchObj.name : 'Ricerca selezionata', 'price-dist-title')
    );
    head.append(
      titleWrap,
      node('span', `${dist.count} ${dist.count === 1 ? 'annuncio con prezzo' : 'annunci con prezzo'}`, 'price-dist-count')
    );
    const stats = node('div', undefined, 'price-dist-stats');
    const addStat = (label, value, sub = null, highlight = false) => {
      const card = node('div', undefined, `price-stat${highlight ? ' is-median' : ''}`);
      card.append(node('span', label, 'price-stat-label'), node('strong', value, 'price-stat-value'));
      if (sub) card.append(node('span', sub, 'price-stat-sub'));
      stats.append(card);
    };
    const ccy = dist.currency || 'EUR';
    addStat('Minimo', price(dist.min, ccy), 'Affare / base');
    addStat('Mediana', price(dist.median, ccy), `Media ${price(dist.mean, ccy)}`, true);
    addStat('Fascia tipica (25°–75°)', `${price(dist.p25, ccy)} – ${price(dist.p75, ccy)}`, 'Metà centrale del mercato');
    addStat('Massimo', price(dist.max, ccy), 'Tetto osservato');

    const bins = Array.isArray(dist.bins) ? dist.bins : [];
    const maxCount = Math.max(1, ...bins.map(b => b.count || 0));
    const hist = node('div', undefined, 'price-histogram');
    bins.forEach((b, idx) => {
      const col = node('div', undefined, 'price-bin');
      const isMedianBin = dist.median >= b.min && (idx === bins.length - 1 ? dist.median <= b.max : dist.median < b.max);
      if (isMedianBin) col.classList.add('is-median-bin');
      const countLabel = node('span', String(b.count), 'price-bin-count');
      const track = node('div', undefined, 'price-bin-track');
      const fill = node('div', undefined, 'price-bin-fill');
      const pct = b.count > 0 ? Math.max(10, Math.round((b.count / maxCount) * 100)) : 0;
      fill.style.height = `${pct}%`;
      track.append(fill);
      const rangeText = Math.round(b.min) === Math.round(b.max)
        ? `€${Math.round(b.min)}`
        : `€${Math.round(b.min)}–${Math.round(b.max)}`;
      const rangeLabel = node('span', rangeText, 'price-bin-range');
      col.title = `${price(b.min, ccy)} – ${price(b.max, ccy)}: ${b.count} ${b.count === 1 ? 'annuncio' : 'annunci'}`;
      col.append(countLabel, track, rangeLabel);
      hist.append(col);
    });
    box.replaceChildren(head, stats, hist);
    box.hidden = false;
  }
  async function changeListingPage(site, offset, watch = listingWatch, order = listingOrder, query = listingQuery, view = listingView) {
    if (loading) return;
    const priorSite = listingSite, priorOffset = listingOffset, priorWatch = listingWatch, priorOrder = listingOrder, priorQuery = listingQuery, priorView = listingView;
    listingSite = site; listingOffset = offset; listingWatch = watch; listingOrder = order; listingQuery = query; listingView = view; showAllListings = false;
    if (!await refresh()) { listingSite = priorSite; listingOffset = priorOffset; listingWatch = priorWatch; listingOrder = priorOrder; listingQuery = priorQuery; listingView = priorView; renderListingControls(); }
  }
  if ($('listing-site')) {
    if ($('listing-search')) {
      const applySearch = () => {
        clearTimeout(searchTimer);
        const nextQuery = $('listing-search').value.trim();
        if (nextQuery !== listingQuery) changeListingPage(listingSite, 0, listingWatch, listingOrder, nextQuery, listingView);
      };
      $('listing-search').addEventListener('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(applySearch, 260);
      });
      $('listing-search').addEventListener('search', applySearch);
    }
    if ($('listing-view')) $('listing-view').addEventListener('change', () => changeListingPage(listingSite, 0, listingWatch, listingOrder, listingQuery, $('listing-view').value));
    if ($('listing-order')) $('listing-order').addEventListener('change', () => changeListingPage(listingSite, 0, listingWatch, $('listing-order').value));
    if ($('listing-watch')) $('listing-watch').addEventListener('change', () => changeListingPage(listingSite, 0, $('listing-watch').value));
    $('listing-site').addEventListener('change', () => changeListingPage($('listing-site').value, 0));
    $('listing-prev').addEventListener('click', () => changeListingPage(listingSite, Math.max(0, listingOffset - (state?.listing_page?.limit || 40))));
    $('listing-next').addEventListener('click', () => changeListingPage(listingSite, listingOffset + (state?.listing_page?.limit || 40)));
  }
  function renderListings() {
    const visible = showAllListings ? state.listings : state.listings.slice(0, 8);
    const items = visible.map((listing) => {
      const row = node('div', undefined, `listing${listing.starred ? ' is-starred' : ''}`);
      const description = node('div');
      const url = safeURL(listing.url);
      const title = node(url ? 'a' : 'span', listing.title || 'Annuncio senza titolo', 'listing-title');
      if (url) { title.href = url; title.target = '_blank'; title.rel = 'noopener noreferrer'; }
      description.append(title, node('div', `${names[listing.site] || listing.site || 'Mercatino'} · ${listing.watch_name || 'Ricerca'}`, 'listing-meta'));
      const key = `${listing.site}:${listing.site_listing_id}`;
      const text = loadedDescriptions.has(key) ? loadedDescriptions.get(key) : listing.description;
      if (typeof text === 'string' && text) {
        const expanded = expandedDescriptions.has(key);
        const paragraph = node('p', text, `listing-description${expanded ? ' expanded' : ''}`);
        const toggle = button(expanded ? 'Mostra meno' : 'Mostra tutto', () => {
          if (expandedDescriptions.has(key)) expandedDescriptions.delete(key);
          else expandedDescriptions.add(key);
          renderListings();
        }, 'text-button description-toggle');
        toggle.setAttribute('aria-expanded', String(expanded));
        description.append(paragraph, toggle);
        requestAnimationFrame(() => {
          if (!expanded && paragraph.isConnected) toggle.hidden = paragraph.scrollHeight <= paragraph.clientHeight + 1;
        });
      } else if (listing.can_load_description && text == null) {
        const loading = loadingDescriptions.has(key);
        const load = button(loading ? 'Caricamento…' : 'Carica descrizione', () => loadDescription(listing), 'text-button description-toggle');
        load.disabled = loading;
        description.append(load);
      } else {
        description.append(node('p', 'Descrizione non disponibile.', 'listing-description-unavailable'));
      }
      if (listing.site && listing.site_listing_id) {
        const curation = node('div', undefined, 'listing-curation');
        const starBtn = button(
          listing.starred ? '★ Salvato' : '☆ Salva',
          (event) => toggleListingFlag(listing, {starred: !listing.starred}, event.currentTarget),
          `curation-chip${listing.starred ? ' is-active' : ''}`
        );
        starBtn.setAttribute('aria-pressed', String(Boolean(listing.starred)));
        const hideBtn = button(
          listing.hidden ? 'Ripristina' : 'Nascondi',
          (event) => toggleListingFlag(listing, {hidden: !listing.hidden}, event.currentTarget),
          'curation-chip curation-hide'
        );
        curation.append(starBtn, hideBtn);
        description.append(curation);
      }
      const media = node('div', undefined, 'listing-media');
      const fallback = node('span', 'Foto non disponibile', 'image-placeholder');
      const imageURL = safeURL(listing.image_url);
      if (imageURL && imageURL.startsWith('https://')) {
        const link = node('a', undefined, 'listing-image-link');
        link.href = imageURL; link.target = '_blank'; link.rel = 'noopener noreferrer';
        link.setAttribute('aria-label', `Apri foto: ${listing.title || 'annuncio'}`);
        const image = node('img', undefined, 'listing-image');
        image.alt = listing.title || 'Foto articolo'; image.loading = 'lazy';
        image.decoding = 'async'; image.referrerPolicy = 'no-referrer';
        image.width = 104; image.height = 104;
        image.addEventListener('error', () => media.replaceChildren(fallback), {once: true});
        image.src = imageURL; link.append(image); media.append(link);
      } else media.append(fallback);
      row.append(media, description, node('span', price(listing.price, listing.currency || 'EUR'), 'listing-price'), node('span', date(listing.last_seen_at ?? listing.first_seen_at), 'listing-date'));
      return row;
    });
    $('listings').replaceChildren(...(items.length ? items : [node('p', 'Nessun annuncio osservato. Compariranno qui dopo la prima scansione delle tue ricerche.', 'empty-copy')]));
    // The inline assistant tracks connected views; attach only after insertion.
    if (window.WorthAssistant) visible.forEach((listing, index) => window.WorthAssistant.decorate(items[index], listing));
    if (state.listings.length > 8) {
      $('listings').append(button(showAllListings ? 'Mostra meno' : `Espandi pagina (${state.listings.length})`, () => {
        showAllListings = !showAllListings;
        renderListings();
      }, 'text-button listing-more'));
    }
    renderListingControls();
  }
  function setSearchMode() {
    const manual = $('custom-urls').checked;
    $('query-field').hidden = manual;
    $('search-text').required = !manual;
    $('manual-name').hidden = !manual;
    $('watch-name').required = manual;
    document.querySelectorAll('.custom-url-field').forEach((element) => { element.hidden = !manual; });
  }
  $('custom-urls').addEventListener('change', setSearchMode);
  function openEditor(watch = null) {
    returnFocus = document.activeElement;
    editing = watch ? JSON.parse(JSON.stringify(watch)) : null;
    $('watch-form').reset();
    $('form-error').hidden = true;
    $('dialog-title').textContent = watch ? 'Modifica ricerca' : 'Nuova ricerca';
    $('watch-name').value = watch?.name || '';
    const firstPhrase = (watch?.search_text || '').split('\n').map(s => s.trim()).find(Boolean) || '';
    if ($('quick-watch-name')) {
      $('quick-watch-name').value = (watch?.name && firstPhrase && watch.name !== firstPhrase) ? watch.name : '';
    }
    $('search-text').value = watch?.search_text || watch?.name || '';
    $('custom-urls').checked = Boolean(watch && !watch.search_text);
    $('watch-enabled').checked = watch ? Boolean(watch.enabled) : true;
    $('min-price').value = watch?.min_price_eur ?? '';
    $('max-price').value = watch?.max_price_eur ?? '';
    const automatic = new Set((watch?.search_text || '').split(/\s+/).filter(Boolean));
    $('include-words').value = (watch?.title_include || []).filter((word) => !automatic.has(word)).join('\n');
    $('exclude-words').value = (watch?.title_exclude || []).join('\n');
    $('description-words').value = (watch?.description_include || []).join('\n');
    $('price-change').checked = Boolean(watch?.notify_on_price_change);
    $('advanced-options').open = $('custom-urls').checked;
    const fields = sites.map((site) => {
      const source = watch?.sources?.[site];
      const box = node('div', undefined, 'source-editor');
      const heading = node('div', undefined, 'source-editor-heading');
      const label = node('label', undefined, 'check-label');
      const enabled = node('input');
      enabled.type = 'checkbox'; enabled.id = `source-${site}`; enabled.checked = source ? Boolean(source.enabled) : !watch;
      label.append(enabled, document.createTextNode(names[site]));
      const select = node('select'); select.id = `cron-${site}`; select.setAttribute('aria-label', `Frequenza scansione ${names[site]}`);
      for (const [value, text] of schedules) { const option = node('option', text); option.value = value; select.append(option); }
      const cron = source?.cron || schedules[1][0];
      if (!schedules.some(([value]) => value === cron)) { const option = node('option', `Personalizzata (${cron})`); option.value = cron; select.append(option); }
      select.value = cron;
      heading.append(label, select);
      const urlLabel = node('label', `URL ricerca ${names[site]}`); urlLabel.htmlFor = `url-${site}`;
      const input = node('input'); input.type = 'url'; input.id = `url-${site}`; input.value = source?.search_url || ''; input.placeholder = `https://… · ricerca ${names[site]}`; input.autocomplete = 'off';
      const urlField = node('div', undefined, 'custom-url-field');
      urlField.append(urlLabel, input);
      box.append(heading, urlField);
      return box;
    });
    $('source-fields').replaceChildren(...fields);
    const preserved = Object.keys(watch?.sources || {}).filter((site) => !sites.includes(site));
    $('preserved-sources').hidden = !preserved.length;
    $('preserved-sources').textContent = preserved.length ? `Fonti non modificabili da questa pagina, mantenute invariate: ${preserved.join(', ')}.` : '';
    setSearchMode();
    $('watch-dialog').showModal();
    ($('custom-urls').checked ? $('watch-name') : $('search-text')).focus();
  }
  function closeEditor() { if (!$('save-watch').disabled) $('watch-dialog').close(); }
  $('new-watch').addEventListener('click', () => openEditor());
  $('close-dialog').addEventListener('click', closeEditor);
  $('cancel-dialog').addEventListener('click', closeEditor);
  $('watch-dialog').addEventListener('close', () => { if (returnFocus?.isConnected) returnFocus.focus(); else $('new-watch').focus(); });
  $('watch-dialog').addEventListener('cancel', (event) => { if ($('save-watch').disabled) event.preventDefault(); });
  $('watch-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if ($('save-watch').disabled) return;
    $('form-error').hidden = true;
    const terms = (id) => $(id).value.split('\n').map((word) => word.trim()).filter(Boolean);
    const min = $('min-price').value === '' ? null : Number($('min-price').value);
    const max = $('max-price').value === '' ? null : Number($('max-price').value);
    try {
      const manual = $('custom-urls').checked;
      const query = $('search-text').value.trim();
      const name = manual ? $('watch-name').value.trim() : query;
      if (!name) throw new Error(manual ? 'Dai un nome alla ricerca.' : 'Scrivi cosa vuoi cercare.');
      if (min !== null && max !== null && min > max) throw new Error('Il prezzo minimo non può superare il massimo.');
      const sources = manual ? {...(editing?.sources || {})} : {};
      for (const site of sites) {
        const existing = editing?.sources?.[site];
        if (!manual) {
          // An absent source in an existing quick watch stays absent unless selected.
          sources[site] = {enabled: $(`source-${site}`).checked, cron: $(`cron-${site}`).value, max_pages: existing?.max_pages ?? 1};
          continue;
        }
        const url = $(`url-${site}`).value.trim();
        if (!url) { delete sources[site]; continue; }
        if (!safeURL(url)) throw new Error(`Inserisci un URL http o https valido per ${names[site]}.`);
        sources[site] = {...existing, search_urls: [], search_url: url, enabled: $(`source-${site}`).checked, cron: $(`cron-${site}`).value, max_pages: existing?.max_pages ?? 1};
      }
      if (manual && !Object.values(sources).some((source) => safeURL(source.search_url))) {
        throw new Error('Incolla almeno un URL di ricerca da un mercatino.');
      }
      if (!manual && !Object.values(sources).some((source) => source.enabled)) throw new Error('Seleziona almeno un mercatino nelle opzioni avanzate.');
      const payload = {enabled: $('watch-enabled').checked, min_price_eur: min, max_price_eur: max, title_include: terms('include-words'), title_exclude: terms('exclude-words'), description_include: terms('description-words'), notify_on_price_change: $('price-change').checked, sources};
      if (manual) { payload.name = name; payload.search_text = null; }
      else {
        payload.query = query;
        const quickName = $('quick-watch-name')?.value.trim();
        if (quickName) payload.name = quickName;
      }
      if (editing) payload.id = editing.id;
      $('save-watch').disabled = true;
      $('save-watch').textContent = 'Salvataggio…';
      await request(manual ? '/api/watches/save' : '/api/watches/quick-save', payload);
      const verified = await refresh();
      $('watch-dialog').close();
      toast(verified ? 'Ricerca salvata.' : 'Ricerca inviata. Stato non aggiornato: verifica prima di riprovare.');
    } catch (error) {
      $('form-error').textContent = error.message;
      $('form-error').hidden = false;
      $('form-error').scrollIntoView({block: 'nearest'});
    } finally {
      $('save-watch').disabled = false;
      $('save-watch').textContent = 'Salva ricerca';
    }
  });
  $('test-notify').addEventListener('click', async () => {
    if (notifying || !state?.notification_configured) return;
    notifying = true;
    $('test-notify').disabled = true;
    $('test-notify').textContent = 'Invio…';
    try {
      await request('/api/notify/test', {});
      toast('Notifica di prova pubblicata. La ricezione sul telefono dipende dall’app e dalle sue impostazioni.');
    } catch (error) { toast(error.message); }
    finally { notifying = false; $('test-notify').disabled = !state?.notification_configured; $('test-notify').textContent = 'Prova notifica'; }
  });
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 30000);
})();
