/* Standalone per-listing advice. The database, not browser storage, owns reports. */
(() => {
  'use strict';
  const states = new Map();
  const storageKey = 'marketwatch.review.open.v1';
  let selected = new Set();
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey) || '[]');
    if (Array.isArray(saved)) selected = new Set(saved.filter(v => typeof v === 'string').slice(-100));
  } catch (_) { /* Private browsing/storage restrictions do not block advice. */ }
  let sequence = 0;
  const pending = s => ['queued', 'running'].includes(s.data?.status);
  function remember(s) {
    if (s.open) selected.add(s.key); else selected.delete(s.key);
    selected = new Set([...selected].slice(-100));
    try { sessionStorage.setItem(storageKey, JSON.stringify([...selected])); } catch (_) { /* Optional UI preference only. */ }
  }
  function node(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }
  function button(text, action) {
    const el = node('button', 'worth-button', text);
    el.type = 'button';
    el.addEventListener('click', action);
    return el;
  }
  function money(amount, currency) {
    if (typeof amount !== 'number' || !Number.isFinite(amount)) return 'non disponibile';
    try { return new Intl.NumberFormat('it-IT', {style: 'currency', currency: currency || 'EUR'}).format(amount); }
    catch (_) { return `${amount.toLocaleString('it-IT')} ${currency || ''}`; }
  }
  function safeURL(value) {
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch (_) { return null; }
  }
  function live(s) {
    for (const v of s.views) if (!v.host.isConnected) s.views.delete(v);
    return s.open && s.views.size > 0;
  }
  function stop(s) { clearTimeout(s.timer); s.timer = null; }
  function schedule(s) {
    stop(s);
    if (!live(s) || !pending(s) || s.busy || s.polls >= 60 || s.error) return;
    s.timer = setTimeout(() => {
      s.timer = null;
      if (!live(s)) return;
      s.polls += 1;
      request(s, 'GET');
    }, 2000);
  }
  function render(s) {
    live(s);
    for (const v of s.views) {
      v.trigger.disabled = s.open && (s.busy || pending(s));
      v.trigger.setAttribute('aria-expanded', String(s.open));
      v.panel.hidden = !s.open;
      v.panel.setAttribute('aria-busy', String(s.busy || pending(s)));
      v.panel.replaceChildren();
      if (!s.open) continue;
      const header = node('div', 'worth-heading');
      header.append(node('h4', '', 'Recensione del prodotto'), button('Chiudi recensione', () => {
        s.open = false; remember(s); stop(s); render(s);
      }));
      v.panel.append(header);
      if (s.busy) v.panel.append(node('p', 'worth-status', 'Caricamento recensione…'));
      else if (pending(s)) v.panel.append(node('p', 'worth-status', s.data.status === 'queued' ? 'In coda…' : 'Analisi in corso…'));
      if (pending(s) && s.polls >= 60) {
        v.panel.append(node('p', 'worth-status', 'Analisi ancora in corso. Controllo automatico sospeso.'));
        v.panel.append(button('Controlla stato', () => { s.polls = 0; request(s, 'GET'); }));
      }
      const report = s.report;
      if (report?.result) {
        const r = report.result;
        const hasSources = Array.isArray(report.sources) && report.sources.length > 0;
        v.panel.append(node(
          'p',
          'worth-note',
          hasSources
            ? 'GPT-6.1-sol · low · Scheda tecnica, trasmissione e stima nuovo verificate con ricerca web sul singolo annuncio.'
            : 'GPT-6.1-sol · low · Scheda tecnica, trasmissione e stima nuovo sul singolo annuncio.'
        ));
        if (s.data?.stale || report.stale) v.panel.append(node('p', 'worth-stale', 'Recensione non aggiornata: i dati del prodotto potrebbero essere cambiati.'));
        if (r.product_name || Number.isInteger(r.release_year) || (typeof r.reference_price_eur === 'number' && r.reference_price_eur > 0) || r.drivetrain || r.crankset || r.lowest_gear_ratio) {
          const specs = node('div', 'worth-specs');
          const addSpec = (label, value) => {
            const chip = node('div', 'worth-spec');
            chip.append(node('span', 'worth-spec-label', label), node('strong', 'worth-spec-value', value));
            specs.append(chip);
          };
          if (r.product_name) addSpec('Modello', String(r.product_name));
          if (Number.isInteger(r.release_year)) addSpec('Anno uscita', String(r.release_year));
          if (typeof r.reference_price_eur === 'number' && Number.isFinite(r.reference_price_eur) && r.reference_price_eur > 0) {
            const kindLabel = r.reference_price_kind === 'current_new' ? 'Nuovo (stima)' : 'Listino lancio (stima)';
            let valueText = `~${money(r.reference_price_eur, 'EUR')}`;
            const ask = s.listing?.price;
            if (typeof ask === 'number' && ask > 0 && (s.listing?.currency || 'EUR') === 'EUR') {
              const delta = Math.round(((ask - r.reference_price_eur) / r.reference_price_eur) * 100);
              if (Number.isFinite(delta) && Math.abs(delta) <= 99) {
                valueText += ` (${delta > 0 ? `+${delta}%` : `${delta}%`} l’annuncio)`;
              }
            }
            addSpec(kindLabel, valueText);
          }
          if (r.drivetrain) addSpec('Cambio / Gruppo', String(r.drivetrain));
          if (r.crankset) addSpec('Guarnitura', String(r.crankset));
          if (r.lowest_gear_ratio) addSpec('Rapporto agile (min)', String(r.lowest_gear_ratio));
          if (r.product_name) {
            const links = node('div', 'worth-spec worth-compare');
            links.append(node('span', 'worth-spec-label', 'Confronta nuovo'));
            const row = node('span', 'worth-compare-links');
            const q = encodeURIComponent(String(r.product_name).trim());
            for (const [name, href] of [
              ['Trovaprezzi ↗', `https://www.trovaprezzi.it/prezzo_${q}.aspx`],
              ['Idealo ↗', `https://www.idealo.it/risultati.html?q=${q}`],
            ]) {
              const a = node('a', 'worth-compare-link', name);
              a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.referrerPolicy = 'no-referrer';
              row.append(a);
            }
            links.append(row);
            specs.append(links);
          }
          v.panel.append(specs);
        }
        const message = node('div', 'worth-message');
        // Plain-text paragraphs deliberately do not interpret model HTML or links.
        for (const paragraph of String(report.result.message || '').split(/\n\s*\n/)) message.append(node('p', '', paragraph));
        v.panel.append(message);
        const ids = new Set(Array.isArray(report.result.source_ids) ? report.result.source_ids : []);
        const citations = node('ul', 'worth-sources');
        const seen = new Set();
        for (const source of report.sources || []) {
          if (!ids.has(source.id) || seen.has(source.id)) continue;
          seen.add(source.id);
          const href = safeURL(source.url);
          if (!href) continue;
          const item = node('li');
          const link = node('a', '', source.title || source.id);
          link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer';
          item.append(link);
          if (source.retrieved_at) item.append(node('span', 'worth-retrieved', ` · consultata: ${source.retrieved_at}`));
          citations.append(item);
        }
        if (citations.childElementCount) { v.panel.append(node('h5', '', 'Fonti consultate'), citations); }
      }
      const error = s.error || (s.data?.status === 'failed' ? s.data.error || 'Analisi non riuscita.' : null);
      if (error) v.panel.append(node('p', 'worth-error', error));
      if (s.error) v.panel.append(button('Riprova caricamento', () => { s.polls = 0; request(s, 'GET'); }));
      const refresh = button(report?.result ? 'Aggiorna recensione' : 'Riprova recensione', () => { s.polls = 0; request(s, 'POST', true); });
      refresh.disabled = s.busy || pending(s);
      if (report?.result || s.data?.status === 'failed' || s.data?.status === 'not_analyzed') v.panel.append(refresh);
    }
  }
  async function request(s, method, refresh = false, createIfMissing = false) {
    if (s.busy) return;
    stop(s); s.busy = true; s.error = null; render(s);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    let create = false;
    try {
      const query = new URLSearchParams({site: s.listing.site, id: s.listing.site_listing_id});
      const options = {method, credentials: 'same-origin', headers: {Accept: 'application/json'}, signal: controller.signal};
      if (method === 'POST') {
        options.headers['Content-Type'] = 'application/json';
        options.headers['X-CSRF-Token'] = document.querySelector('meta[name="csrf-token"]')?.content || '';
        options.body = JSON.stringify({site: s.listing.site, id: s.listing.site_listing_id, refresh});
      }
      const res = await fetch('/api/ai/review' + (method === 'GET' ? `?${query}` : ''), options);
      if (!res.ok) {
        let msg = '';
        try {
          const errBody = await res.json();
          msg = errBody?.error || '';
        } catch (_) {}
        throw new Error(msg || `HTTP ${res.status}`);
      }
      const data = await res.json();
      if (!['not_analyzed', 'queued', 'running', 'completed', 'failed'].includes(data.status)) throw new Error('Invalid response');
      s.data = data;
      if (data.result) s.report = data;
      create = createIfMissing && data.status === 'not_analyzed' && live(s);
    } catch (err) {
      const msg = err && err.message;
      s.error = (msg && !msg.startsWith('HTTP ') && msg !== 'Invalid response')
        ? msg
        : 'Connessione non disponibile o risposta non valida. Riprova il caricamento; la recensione precedente resta visibile.';
    } finally {
      clearTimeout(timeout); s.busy = false; render(s);
    }
    if (create) return request(s, 'POST', false);
    schedule(s);
  }
  function decorate(row, listing) {
    if (!row || !listing || listing.site == null || listing.site_listing_id == null || row.querySelector('.worth-assistant')) return;
    const key = JSON.stringify([listing.site, listing.site_listing_id]);
    let s = states.get(key);
    if (!s) {
      s = {key, listing, views: new Set(), open: selected.has(key), busy: false, polls: 0, data: null, report: null, error: null, timer: null};
      states.set(key, s);
    }
    const host = node('div', 'worth-assistant');
    const panel = node('section', 'worth-panel');
    panel.id = `worth-panel-${++sequence}`;
    panel.setAttribute('aria-label', 'Recensione del prodotto');
    const trigger = button('Recensisci', () => {
      if (s.open && (s.busy || pending(s))) return;
      s.open = !s.open; remember(s); stop(s); s.polls = 0; render(s);
      if (s.open) request(s, 'GET', false, !s.data && !s.report);
    });
    trigger.setAttribute('aria-controls', panel.id);
    host.append(trigger, panel); row.append(host);
    s.views.add({host, panel, trigger}); render(s);
    // Only explicitly selected panels restore; never enqueue work during render.
    if (s.open && !s.busy) request(s, 'GET');
  }
  window.WorthAssistant = Object.freeze({decorate});
})();
