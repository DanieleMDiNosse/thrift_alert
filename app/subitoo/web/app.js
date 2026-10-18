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
    try {
      const next = await request('/api/state');
      if (!Array.isArray(next.watches) || !Array.isArray(next.listings)) throw new Error('Stato del servizio non valido.');
      state = next;
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
    } finally { loading = false; $('watches').setAttribute('aria-busy', 'false'); }
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
      const sourceBlock = node('div');
      const row = node('div', undefined, 'source-row');
      let status;
      if (!source.enabled) status = 'Fonte in pausa';
      else if (!watch.enabled) status = 'Ricerca in pausa';
      else if (query?.error) status = 'Da verificare';
      else if (!query?.seeded) status = 'Prima scansione';
      else status = `Ultima scansione · ${date(query.last_run_at)}`;
      row.append(node('span', names[site] || site, 'source-name'), node('span', status, 'source-state'));
      sourceBlock.append(row);
      if (query?.error) {
        sourceBlock.append(node('p', query.error, 'source-error'));
        const retry = button('Riprova scansione', (event) => {
          if (window.confirm(`Vuoi riprovare la scansione di ${names[site] || site}? L’operazione contatterà il mercatino e potrà inviare notifiche.`)) {
            mutate(`query-${query.id}`, `/api/queries/${encodeURIComponent(query.id)}/retry`, {}, 'Richiesta di scansione inviata.', event.currentTarget);
          }
        });
        retry.disabled = pending.has(`query-${query.id}`);
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
  function renderListings() {
    const visible = showAllListings ? state.listings : state.listings.slice(0, 8);
    const items = visible.map((listing) => {
      const row = node('div', undefined, 'listing');
      const description = node('div');
      const url = safeURL(listing.url);
      const title = node(url ? 'a' : 'span', listing.title || 'Annuncio senza titolo', 'listing-title');
      if (url) { title.href = url; title.target = '_blank'; title.rel = 'noopener noreferrer'; }
      description.append(title, node('div', `${names[listing.site] || listing.site || 'Mercatino'} · ${listing.watch_name || 'Ricerca'}`, 'listing-meta'));
      row.append(description, node('span', price(listing.price, listing.currency || 'EUR'), 'listing-price'), node('span', date(listing.last_seen_at ?? listing.first_seen_at), 'listing-date'));
      return row;
    });
    $('listings').replaceChildren(...(items.length ? items : [node('p', 'Nessun annuncio osservato. Compariranno qui dopo la prima scansione delle tue ricerche.', 'empty-copy')]));
    if (state.listings.length > 8) {
      $('listings').append(button(showAllListings ? 'Mostra meno' : `Mostra tutti (${state.listings.length})`, () => {
        showAllListings = !showAllListings;
        renderListings();
      }, 'text-button listing-more'));
    }
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
    $('search-text').value = watch?.search_text || watch?.name || '';
    $('custom-urls').checked = Boolean(watch && !watch.search_text);
    $('watch-enabled').checked = watch ? Boolean(watch.enabled) : true;
    $('min-price').value = watch?.min_price_eur ?? '';
    $('max-price').value = watch?.max_price_eur ?? '';
    const automatic = new Set((watch?.search_text || '').split(/\s+/).filter(Boolean));
    $('include-words').value = (watch?.title_include || []).filter((word) => !automatic.has(word)).join('\n');
    $('exclude-words').value = (watch?.title_exclude || []).join('\n');
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
        sources[site] = {...existing, search_url: url, enabled: $(`source-${site}`).checked, cron: $(`cron-${site}`).value, max_pages: existing?.max_pages ?? 1};
      }
      if (manual && !Object.values(sources).some((source) => safeURL(source.search_url))) {
        throw new Error('Incolla almeno un URL di ricerca da un mercatino.');
      }
      if (!manual && !Object.values(sources).some((source) => source.enabled)) throw new Error('Seleziona almeno un mercatino nelle opzioni avanzate.');
      const payload = {enabled: $('watch-enabled').checked, min_price_eur: min, max_price_eur: max, title_include: terms('include-words'), title_exclude: terms('exclude-words'), notify_on_price_change: $('price-change').checked, sources};
      if (manual) { payload.name = name; payload.search_text = null; }
      else payload.query = query;
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
