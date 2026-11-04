/* Integrated local AI workspace. Server/DB remain the source of truth. */
(() => {
  'use strict';
  const root = document.getElementById('ai-assistant');
  if (!root || root.dataset.workflow === 'on_demand') return;
  const array = value => Array.isArray(value) ? value : [];
  const text = value => value == null ? 'Non disponibile' : String(value);
  const number = value => typeof value === 'number' && Number.isFinite(value) ? value : null;
  const score = value => number(value) == null ? 'Non disponibile' : String(value);
  const money = (value, currency = 'EUR') => {
    if (number(value) == null) return 'Non disponibile';
    try { return new Intl.NumberFormat('it-IT', {style: 'currency', currency}).format(value); }
    catch (_) { return `${value} ${currency}`; }
  };
  function node(tag, content, className) {
    const element = document.createElement(tag);
    if (content != null) element.textContent = String(content);
    if (className) element.className = className;
    return element;
  }
  function button(label, action) {
    const element = node('button', label, 'secondary');
    element.type = 'button'; element.addEventListener('click', action); return element;
  }
  function field(label, element) {
    const wrapper = node('label', null, 'ai-field');
    wrapper.append(node('span', label), element); return wrapper;
  }
  function select(options) {
    const element = node('select');
    options.forEach(([value, label]) => element.append(new Option(label, value))); return element;
  }
  function safeLink(title, url) {
    try {
      const parsed = new URL(url);
      if (!['https:', 'http:'].includes(parsed.protocol) || parsed.username || parsed.password) return node('span', title);
      const link = node('a', title); link.href = parsed.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; return link;
    } catch (_) { return node('span', title); }
  }
  // Deliberately small markdown subset: text, paragraphs, bold and inline code.
  // Inferred markdown links stay literal; only structured server sources link out.
  function markdown(value) {
    const block = node('div', null, 'ai-markdown');
    String(value || '').split(/\n/).forEach(line => {
      const paragraph = node('p');
      line.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).forEach(part => {
        paragraph.append(part.startsWith('**') ? node('strong', part.slice(2, -2)) : part.startsWith('`') ? node('code', part.slice(1, -1)) : document.createTextNode(part));
      });
      block.append(paragraph);
    }); return block;
  }
  let state = {}, selected = new Set(), detailId = null, conversationId = '', loading = false, epoch = 0;
  try { conversationId = localStorage.getItem('subitoo.ai.conversation') || ''; } catch (_) { /* private browsing */ }
  const heading = node('div', null, 'ai-toolbar');
  heading.append(node('h2', 'Assistente acquisti AI'));
  const sync = button('Analizza annunci', () => mutate('/api/ai/sync', {watch_id: watch.value ? Number(watch.value) : null, limit: 5}, sync));
  const pause = button('Metti in pausa AI', () => mutate('/api/ai/settings', {enabled: !Boolean(state.settings?.enabled)}, pause));
  heading.append(pause);
  const recalc = button('Ricalcola classifiche', () => refresh());
  heading.append(sync, recalc);
  const notice = node('div', '', 'ai-error'); notice.setAttribute('role', 'alert');
  const status = node('p', 'Caricamento…', 'ai-status'); status.setAttribute('role', 'status');
  const jobDetails = node('details', null, 'ai-jobs');
  jobDetails.append(node('summary', 'Ultime attività AI'));
  const jobList = node('div', null, 'ai-job-list'); jobDetails.append(jobList);
  const filters = node('div', null, 'ai-toolbar');
  const category = select([['', 'Tutte le categorie']]);
  const watch = select([['', 'Tutte le ricerche']]);
  const ranking = select([['quality', 'Qualità'], ['value', 'Qualità / prezzo'], ['deal', 'Convenienza annuncio']]);
  const marketplace = select([['', 'Tutti i marketplace'], ['subito', 'Subito'], ['vinted', 'Vinted'], ['wallapop', 'Wallapop']]);
  const platformSummary = node('p', '', 'ai-platform-summary ai-muted');
  marketplace.addEventListener('change', renderRanks);
  filters.append(field('Categoria', category), field('Ricerca monitorata', watch), field('Classifica', ranking), field('Marketplace AI', marketplace));
  category.addEventListener('change', () => { selected.clear(); refresh(); });
  watch.addEventListener('change', () => { selected.clear(); refresh(); });
  ranking.addEventListener('change', renderRanks);
  const ranks = node('div', null, 'ai-ranks');
  const compareButton = button('Confronta selezionati', async () => {
    const response = await mutate('/api/ai/compare', {product_ids: [...selected]}, compareButton);
    if (response) renderComparison(response);
  }); compareButton.disabled = true;
  const comparison = node('section', null, 'ai-panel'); comparison.setAttribute('aria-label', 'Confronto prodotti');
  const details = node('section', null, 'ai-panel'); details.setAttribute('aria-label', 'Dettagli prodotto');
  const settingsBox = node('details', null, 'ai-settings'); settingsBox.append(node('summary', 'Impostazioni AI e stato'));
  const enabled = node('input'); enabled.type = 'checkbox';
  enabled.addEventListener('change', () => mutate('/api/ai/settings', {enabled: enabled.checked}, enabled));
  const settingsInfo = node('p');
  settingsBox.append(field('AI abilitata', enabled), settingsInfo, node('p', 'Endpoint, modello e credenziali sono configurati lato server; non modificabili da questa pagina.', 'ai-muted'));
  const chat = node('section', null, 'ai-panel'); chat.append(node('h3', 'Chat contestuale'));
  const chatTools = node('div', null, 'ai-toolbar');
  const conversations = select([['', 'Scegli una conversazione']]);
  const newChat = button('Nuova conversazione', async () => {
    const payload = {product_ids: [...selected]}; if (watch.value) payload.watch_id = Number(watch.value);
    const response = await mutate('/api/ai/conversations/new', payload, newChat);
    if (response && response.id != null) { chooseConversation(response.id); await refresh(); }
  });
  const clearChat = button('Svuota conversazione', () => mutate(`/api/ai/conversations/${encodeURIComponent(conversationId)}/clear`, {}, clearChat));
  conversations.addEventListener('change', () => { chooseConversation(conversations.value); renderChat(); });
  chatTools.append(field('Conversazione', conversations), newChat, clearChat);
  const messages = node('div', null, 'ai-messages'); messages.setAttribute('role', 'log'); messages.setAttribute('aria-live', 'polite');
  const hypothetical = node('details', null, 'ai-hypothetical'); hypothetical.append(node('summary', 'Prezzi ipotetici (non modificano gli annunci)'));
  const priceFields = node('div', null, 'ai-price-fields'); hypothetical.append(priceFields);
  const draft = node('textarea'); draft.rows = 3; draft.maxLength = 8000;
  const research = node('input'); research.type = 'checkbox';
  const send = button('Invia messaggio', async () => {
    const message = draft.value.trim(); if (!message || !conversationId) return;
    const prices = {};
    for (const input of priceFields.querySelectorAll('input')) {
      if (input.value !== '') {
        const price = Number(input.value);
        if (!Number.isFinite(price) || price <= 0) { notice.textContent = 'Inserire prezzi validi e positivi.'; return; }
        prices[input.dataset.listingId] = price;
      }
    }
    const response = await mutate(`/api/ai/conversations/${encodeURIComponent(conversationId)}/messages`, {message, research: research.checked, hypothetical: prices}, send);
    if (response) { draft.value = ''; status.textContent = 'Messaggio accodato. La risposta viene letta dal database.'; }
  });
  draft.addEventListener('input', () => { send.disabled = !conversationId || !draft.value.trim(); });
  chat.append(chatTools, messages, hypothetical, field('Messaggio', draft), field('Ricerca nuove fonti', research), send);
  root.append(heading, node('p', 'Valutazioni assistite: dati mancanti, campioni ridotti e identificazioni incerte non sono garanzie di acquisto.', 'ai-muted'), notice, status, jobDetails, platformSummary, filters, ranks, compareButton, comparison, details, chat, settingsBox);

  async function api(path, payload) {
    const options = {credentials: 'same-origin', headers: {'Accept': 'application/json'}};
    if (payload !== undefined) {
      options.method = 'POST'; options.headers['Content-Type'] = 'application/json';
      options.headers['X-CSRF-Token'] = document.querySelector('meta[name="csrf-token"]')?.content || '';
      options.body = JSON.stringify(payload);
    }
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || data.detail || `Errore HTTP ${response.status}`);
    return data;
  }
  async function mutate(path, payload, control) {
    control.disabled = true; notice.textContent = '';
    try { const result = await api(path, payload); await refresh(); return result; }
    catch (error) { notice.textContent = error.message; return null; }
    finally { control.disabled = false; renderChatControls(); compareButton.disabled = selected.size < 2; }
  }
  function chooseConversation(id) {
    conversationId = String(id || '');
    try { if (conversationId) localStorage.setItem('subitoo.ai.conversation', conversationId); else localStorage.removeItem('subitoo.ai.conversation'); } catch (_) { /* storage is optional */ }
  }
  async function refresh(silent = false) {
    const version = ++epoch;
    if (!silent) status.textContent = 'Aggiornamento…';
    try {
      const params = new URLSearchParams(); if (category.value) params.set('category', category.value); if (watch.value) params.set('watch_id', watch.value);
      const data = await api(`/api/ai/state?${params}`);
      if (version !== epoch) return;
      state = data || {};
      const categories = new Set([...array(state.products).map(p => p.category).filter(Boolean), ...Array.from(category.options).map(o => o.value).filter(Boolean)]);
      const prior = category.value; category.replaceChildren(new Option('Tutte le categorie', '')); [...categories].sort().forEach(c => category.append(new Option(c, c))); category.value = prior;
      const ids = new Set(array(state.products).map(p => String(p.id))); selected = new Set([...selected].filter(id => ids.has(id)));
      renderRanks(); renderDetails(); renderChat(); renderPrices();
      const settings = state.settings || {}; enabled.checked = Boolean(settings.enabled);
      settingsInfo.textContent = `Provider: ${text(settings.provider || 'local')} · Modello: ${text(settings.model)} · Reasoning: ${text(settings.reasoning_effort)} · Ricerca: ${text(settings.search_backend)} · Cache: ${text(settings.research_ttl_days)} giorni`;
      pause.textContent = settings.enabled ? 'Metti in pausa AI' : 'Riprendi AI';
      renderJobs();
    } catch (error) { notice.textContent = error.message; status.textContent = 'AI non disponibile. Riprova con Ricalcola classifiche.'; }
  }
  function renderJobs() {
    const jobs = array(state.jobs);
    const labels = {queued: 'In coda', running: 'In corso', done: 'Completati', failed: 'Falliti', cancelled: 'Annullati'};
    const counts = state.job_summary || jobs.reduce((result, job) => {
      if (Object.hasOwn(labels, job.status)) result[job.status] = (result[job.status] || 0) + 1;
      return result;
    }, {});
    status.textContent = `${state.settings?.enabled ? 'AI attiva' : 'AI in pausa'} · ${Object.entries(labels).map(([key, label]) => `${label}: ${number(counts[key]) ?? 0}`).join(' · ')}`;
    if (state.settings?.configuration_error) status.textContent += ` · ${state.settings.configuration_error}`;
    const signature = JSON.stringify(jobs);
    if (jobList.dataset.signature === signature) return;
    jobList.dataset.signature = signature; jobList.replaceChildren();
    const kinds = {identify: 'Identificazione', research: 'Ricerca', chat: 'Chat'};
    const timestamp = job => {
      const value = job.updated_at ?? job.created_at;
      return number(value) ?? (Date.parse(value) || 0);
    };
    [...jobs].sort((a, b) => timestamp(b) - timestamp(a) || Number(b.id) - Number(a.id)).slice(0, 10).forEach(job => {
      const row = node('article', null, 'ai-job');
      row.append(node('p', `${kinds[job.kind] || 'Attività'} · ${labels[job.status] || 'Stato non disponibile'} · ${job.title || `Attività ${job.id}`}`));
      if (job.error) row.append(node('p', job.error, 'ai-warning'));
      if (['failed', 'cancelled'].includes(job.status) && job.id != null) {
        const retry = button(`Riprova attività ${job.id}`, () => mutate(`/api/ai/jobs/${encodeURIComponent(job.id)}/retry`, {}, retry));
        row.append(retry);
      }
      jobList.append(row);
    });
    if (!jobs.length) jobList.append(node('p', 'Nessuna attività recente.', 'ai-muted'));
  }
  function renderRanks() {
    ranks.replaceChildren();
    const products = array(state.products), allListings = array(state.listings);
    const platforms = {subito: 'Subito', vinted: 'Vinted', wallapop: 'Wallapop'};
    platformSummary.textContent = 'Annunci raccolti · ' + Object.entries(platforms).map(([key, label]) => `${label}: ${allListings.filter(l => l.site === key).length}`).join(' · ');
    const listings = allListings.filter(l => !marketplace.value || l.site === marketplace.value);
    const visible = new Set(listings.map(l => String(l.product_id)));
    const groups = Object.entries(state.rankings?.by_category || {});
    const sections = groups.length ? groups : [['', state.rankings || {}]];
    if (!products.length) { ranks.append(node('p', 'Nessun prodotto analizzato.')); compareButton.disabled = true; return; }
    const seen = new Set();
    for (const [categoryName, group] of sections) {
      const members = products.filter(p => (!categoryName || p.category === categoryName) && visible.has(String(p.id)));
      if (!members.length) continue;
      const heading = node('h3', categoryName || members[0].category || 'Prodotti', 'ai-group-heading'); ranks.append(heading);
      const inScope = row => row.listing_id != null ? listings.some(l => String(l.id) === String(row.listing_id)) : visible.has(String(row.product_id));
      let rows = array(group[ranking.value]).filter(inScope);
      if (ranking.value === 'value' && !rows.length) {
        rows = array(group.asking_value).filter(inScope);
        if (rows.length) ranks.append(node('p', 'Indicatore sul prezzo richiesto: spedizione e commissioni escluse. Non è una classifica sul costo totale.', 'ai-group-heading ai-warning'));
      }
      const ranked = new Set(rows.map(row => String(row.product_id)));
      const entries = [...rows, ...members.filter(p => !ranked.has(String(p.id))).map(p => ({product_id: p.id}))];
      if (!rows.length) ranks.append(node('p', 'Nessuna classifica affidabile con questi dati. Gli annunci restano consultabili qui sotto.', 'ai-group-heading ai-muted'));
      const evaluations = array(group.listings);
      for (const entry of entries) {
        const product = members.find(p => String(p.id) === String(entry.product_id));
        if (!product) continue;
        const offers = listings.filter(l => String(l.product_id) === String(product.id)).sort((a,b) => (number(a.price) ?? Infinity) - (number(b.price) ?? Infinity) || Number(a.id) - Number(b.id));
        const listing = offers.find(l => String(l.id) === String(entry.listing_id)) || offers[0];
        const evaluation = evaluations.find(l => String(l.listing_id ?? l.id) === String(listing?.id)) || {};
        const row = {...evaluation, ...entry};
        const card = node('article', null, 'ai-product');
        const id = String(product.id), label = product.name || product.model || listing?.title || 'Prodotto da identificare';
        if (!seen.has(id)) {
          const checkbox = node('input'); checkbox.type = 'checkbox'; checkbox.checked = selected.has(id);
          checkbox.addEventListener('change', () => { checkbox.checked ? selected.add(id) : selected.delete(id); compareButton.disabled = selected.size < 2; });
          card.append(field(`Seleziona ${label}`, checkbox)); seen.add(id);
        }
        card.append(node('h3', label));
        card.append(node('p', `${text(product.category)} · Confidenza: ${score(row.confidence ?? product.confidence)} · Ricerca: ${text(product.research_status)}`, 'ai-muted'));
        card.append(node('p', number(row.quality_score) != null ? `Qualità: ${score(row.quality_score)}` : 'Qualità non valutabile: criteri documentati insufficienti o categoria non supportata.'));
        if (number(row.value_score) != null) card.append(node('p', `Qualità/prezzo sul totale: ${score(row.value_score)}`));
        else if (number(row.asking_value_score) != null) card.append(node('p', `Indicatore sul prezzo richiesto: ${score(row.asking_value_score)} · spedizione e commissioni escluse`, 'ai-muted'));
        if (number(row.deal_score) != null) card.append(node('p', `Affare: ${score(row.deal_score)}%`));
        if (listing) {
          card.append(node('p', `${platforms[listing.site] || text(listing.site)} · ${offers.length} annunci disponibili`));
          card.append(safeLink(listing.title || label, listing.url));
          const total = number(row.effective_price) != null ? money(row.effective_price, listing.currency) : 'Da completare';
          card.append(node('p', `Prezzo: ${money(listing.price, listing.currency)} · Totale: ${total}`));
          const condition = listing.condition_label || ({new:'Nuovo',like_new:'Come nuovo',good:'Buono',fair:'Discreto',poor:'Danneggiato',unknown:'Da verificare'}[listing.condition]) || text(listing.condition);
          card.append(node('p', `Condizione: ${condition}${listing.facts_sources?.condition === 'marketplace' ? ' (dichiarata dal venditore)' : ''}`));
          card.append(node('p', `Spedizione: ${number(listing.shipping_cost) == null ? 'Da verificare' : money(listing.shipping_cost, listing.currency)} · Commissioni: ${number(listing.fees) == null ? 'Da verificare' : money(listing.fees, listing.currency)}`, 'ai-muted'));
          if (offers.length > 1) {
            const more = node('details'); more.append(node('summary', 'Tutti gli annunci di questo prodotto'));
            offers.forEach(offer => { const line = node('p'); line.append(safeLink(`${platforms[offer.site] || text(offer.site)} · ${money(offer.price, offer.currency)} · ${offer.title}`, offer.url)); more.append(line); });
            card.append(more);
          }
        }
        if (row.market && number(row.market.median) != null) card.append(node('p', `Mediana prezzi richiesti: ${money(row.market.median)} · Campione: ${score(row.market.n)}`));
        else card.append(node('p', 'Affare di mercato non stimabile: servono almeno 5 altri annunci dello stesso modello, variante e condizione.', 'ai-muted'));
        const warnings = {'Shipping cost unknown':'Spedizione da verificare','Marketplace fees unknown':'Commissioni da verificare','Condition unknown; conservative penalty':'Condizione sconosciuta: penalità prudenziale','Insufficient comparable asking prices (minimum 5 other listings)':'Campione di mercato insufficiente'};
        array(row.warnings).filter(w => !Object.hasOwn(warnings, w)).forEach(w => card.append(node('p', w, 'ai-warning')));
        card.append(button(`Dettagli ${label}`, () => { detailId = id; renderDetails(); details.scrollIntoView({block: 'nearest'}); }));
        ranks.append(card);
      }
    }
    if (!seen.size) ranks.append(node('p', 'Nessun annuncio per il marketplace selezionato.'));
    compareButton.disabled = selected.size < 2;
  }
  function renderDetails() {
    const product = array(state.products).find(p => String(p.id) === detailId);
    if (!product) { details.replaceChildren(); details.dataset.signature = ''; return; }
    const signature = JSON.stringify([product, array(state.listings).filter(l => String(l.product_id) === detailId)]);
    if (details.dataset.signature === signature) return;
    if (details.dataset.productId === detailId && details.contains(document.activeElement)) return;
    details.dataset.signature = signature; details.dataset.productId = detailId; details.replaceChildren();
    const research = product.research || {};
    details.append(node('h3', product.name || product.model || product.id));
    details.append(node('p', ['manufacturer', 'model', 'generation', 'variant'].map(key => `${key}: ${text(product[key])}`).join(' · '), 'ai-muted'));
    details.append(markdown(research.summary || 'Ricerca non ancora disponibile.'), node('p', `Confidenza ricerca: ${score(research.confidence)}`));
    if (research.professional_summary) details.append(node('h4', 'Recensioni professionali'), markdown(research.professional_summary));
    if (research.user_summary) details.append(node('h4', 'Esperienze utenti'), markdown(research.user_summary));
    const refreshButton = button('Aggiorna ricerca', () => mutate(`/api/ai/products/${encodeURIComponent(product.id)}/refresh`, {}, refreshButton)); details.append(refreshButton);
    [['strengths', 'Punti di forza'], ['weaknesses', 'Limiti'], ['known_issues', 'Problemi noti']].forEach(([key, label]) => {
      details.append(node('h4', label)); const list = node('ul'); array(research[key]).forEach(item => list.append(node('li', item))); if (!list.childElementCount) list.append(node('li', 'Non disponibile')); details.append(list);
    });
    details.append(node('h4', 'Criteri e fonti'));
    Object.entries(research.criteria || {}).forEach(([key, value]) => {
      const criterion = value && typeof value === 'object' ? value : {score: value};
      const line = node('p', `${key}: ${score(criterion.score)} `);
      array(criterion.source_ids).forEach(id => { const source = array(product.sources).find(s => String(s.id) === String(id)); if (source) line.append(safeLink(`[${id}]`, source.url)); }); details.append(line);
    });
    details.append(node('h4', 'Specifiche')); Object.entries(research.specifications || {}).forEach(([key, value]) => details.append(node('p', `${key}: ${typeof value === 'object' ? JSON.stringify(value) : text(value)}`)));
    details.append(node('h4', 'Fonti verificabili')); array(product.sources).forEach(source => {
      const line = node('p'); line.append(safeLink(source.title || source.url || source.id, source.url), document.createTextNode(` · ${text(source.source_type || source.kind)} · ${text(source.reliability)} · ${text(source.retrieved_at)}`)); details.append(line);
    });
    if (!array(product.sources).length) details.append(node('p', 'Nessuna fonte disponibile: valutazione incerta.', 'ai-warning'));
    details.append(node('h4', 'Annunci disponibili e costi verificati'));
    array(state.listings).filter(l => String(l.product_id) === detailId).forEach(listing => {
      details.append(safeLink(listing.title, listing.url), node('p', `${text(listing.site)} · ${money(listing.price, listing.currency)}`));
      const editor = node('details'); editor.append(node('summary', `Dati verificati annuncio ${listing.id}`));
      editor.append(node('p', 'Inserisci solo dati verificati con il venditore. Vuoto = sconosciuto; zero = costo confermato nullo. Non modifica i dati originali.', 'ai-muted'));
      const condition = select(['unknown','new','like_new','good','fair','poor'].map(x => [x,x])); condition.value = listing.condition || 'unknown';
      const shipping = node('input'), fees = node('input');
      [shipping,fees].forEach(input => { input.type = 'number'; input.min = '0'; input.step = '0.01'; });
      shipping.value = listing.shipping_cost ?? ''; fees.value = listing.fees ?? '';
      editor.append(field(`Condizione ${listing.id}`, condition), field(`Spedizione ${listing.id}`, shipping), field(`Commissioni ${listing.id}`, fees));
      const save = button(`Salva dati ${listing.id}`, () => mutate(`/api/ai/listings/${listing.id}/facts`, {
        condition: condition.value, shipping_cost: shipping.value === '' ? null : Number(shipping.value),
        fees: fees.value === '' ? null : Number(fees.value), age_years: listing.age_years ?? null,
        accessories: array(listing.accessories), warranty: listing.warranty ?? null
      }, save)); editor.append(save); details.append(editor);
    });
  }
  function renderComparison(response) {
    comparison.replaceChildren(node('h3', 'Confronto'), markdown(response.summary || 'Differenze tecniche documentate; dati mancanti non equivalgono a prestazioni inferiori.'));
    const products = array(response.products), table = node('table'), head = node('tr');
    head.append(node('th','Criterio')); products.forEach(p => head.append(node('th',p.name || p.model || p.id))); table.append(head);
    const criteria = new Set(products.flatMap(p => Object.keys(p.research?.criteria || {})));
    for (const key of criteria) {
      const row = node('tr'); row.append(node('td', key));
      products.forEach(p => row.append(node('td', score(p.research?.criteria?.[key]?.score)))); table.append(row);
    }
    for (const key of ['variant','generation','confidence']) {
      const row = node('tr'); row.append(node('td',key)); products.forEach(p => row.append(node('td',text(p[key])))); table.append(row);
    }
    comparison.append(table);
    for (const [key,label] of [['quality','Qualità assoluta'],['value','Qualità / prezzo'],['deal','Affare rispetto ai prezzi richiesti']]) {
      const best = array(response.rankings?.[key])[0];
      comparison.append(node('p', `${label}: ${best ? (products.find(p => p.id === best.product_id)?.name || best.name || best.product_id) : 'Non determinabile con i dati disponibili'}`));
    }
    comparison.append(node('p','Qualità: rubrica euristica 0–100; valore: punti qualità corretti per condizione ogni €100 di costo totale noto; affare: sconto % sulla mediana di almeno 5 altri annunci comparabili. Non sono prezzi di vendita conclusa.', 'ai-muted'));
    array(response.warnings).forEach(w => comparison.append(node('p',w,'ai-warning')));
  }
  function renderChatControls() { clearChat.disabled = !conversationId; send.disabled = !conversationId || !draft.value.trim(); }
  function renderChat() {
    const items = array(state.conversations);
    if (conversationId && !items.some(c => String(c.id) === conversationId)) chooseConversation('');
    conversations.replaceChildren(new Option('Scegli una conversazione', ''));
    items.forEach(c => conversations.append(new Option(c.title || `Conversazione ${c.id}`, String(c.id)))); conversations.value = conversationId;
    const current = items.find(c => String(c.id) === conversationId);
    const signature = JSON.stringify(current?.messages || []);
    if (messages.dataset.signature !== signature) {
      messages.dataset.signature = signature; messages.replaceChildren();
      array(current?.messages).forEach(message => {
        const article = node('article', null, 'ai-message'); article.append(node('strong', message.role === 'user' ? 'Tu' : 'Assistente'), markdown(message.content));
        const sources = array(message.sources).length ? message.sources : array(state.products).flatMap(p => array(p.sources));
        array(message.source_ids).forEach(id => { const source = sources.find(s => s.id === id); if (source) article.append(safeLink(source.title || id, source.url), document.createTextNode(' ')); });
        messages.append(article);
      });
    }
    renderChatControls();
  }
  function renderPrices() {
    const listings = array(state.listings);
    const signature = JSON.stringify(listings.map(l => [l.id, l.title]));
    if (priceFields.dataset.signature === signature) return;
    const previous = new Map([...priceFields.querySelectorAll('input')].map(input => [input.dataset.listingId, input.value]));
    priceFields.dataset.signature = signature; priceFields.replaceChildren();
    listings.forEach(listing => {
      const input = node('input'); input.type = 'number'; input.min = '0'; input.step = '0.01'; input.dataset.listingId = String(listing.id); input.value = previous.get(String(listing.id)) || '';
      priceFields.append(field(`Prezzo ipotetico ${listing.title || listing.id}`, input));
    });
  }
  api('/api/state').then(data => array(data.watches).forEach(w => watch.append(new Option(w.name || w.search_text || `Ricerca ${w.id}`, String(w.id))))).catch(() => {});
  refresh();
  setInterval(() => { if (!document.hidden && !loading) { loading = true; refresh(true).finally(() => { loading = false; }); } }, 2500);
})();
