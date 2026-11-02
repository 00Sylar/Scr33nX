// Scr33nX listing badges — overlays a small status badge on model thumbnails
// so you can see who is recording / tracked without opening the tab.
// Data comes from the background worker (single cached /models call).
// Also: the highlight filter (dims thumbnails that don't match) and the hover
// mini player, both configured from the popup via extension storage.
(() => {
  const host = location.hostname.replace('www.', '');
  let site = null;
  if (host.includes('chaturbate.com')) site = 'chaturbate';
  else if (host.includes('stripchat.com')) site = 'stripchat';
  else if (host.includes('camsoda.com'))    site = 'camsoda';
  else if (host.includes('myfreecams.com')) site = 'myfreecams';
  if (!site) return;

  const SKIP = ['tags', 'search', 'following', 'discover', 'login', 'register',
                'promo', 'affiliates', 'p', 'trending', 'new-cams', 'female', 'male'];

  // name → model entry for this site; null until the first response arrives
  let known = null;

  // Popup options — keep defaults in sync with popup.js OPT_DEFAULTS.
  const OPT_DEFAULTS = { highlight: [], mpEnabled: true, mpWidth: 360,
                         mpDelay: 1000, mpMuted: true, mpQuality: 480,
                         mpAny: false };
  const opts = Object.assign({}, OPT_DEFAULTS);

  const STYLE = `
    .scr33nx-badge {
      position: absolute; top: 6px; left: 6px; z-index: 2147483000;
      padding: 2px 7px; border-radius: 4px; pointer-events: none;
      font: bold 12px/16px Arial, sans-serif; color: #fff; letter-spacing: .5px;
      box-shadow: 0 1px 3px rgba(0,0,0,.6);
    }
    .scr33nx-rec, .scr33nx-lnkrec {
      padding: 4px 10px; font-size: 15px; line-height: 18px;
      border: 2px solid #fff;
    }
    .scr33nx-rec    { background: #e53935; }
    .scr33nx-lnkrec { background: #ff8f00; }
    .scr33nx-on     { background: #2e7d32; }
    .scr33nx-idle   { background: #546e7a; }
    .scr33nx-saved  { background: rgba(0,0,0,.72); color: #ffc107;
                      font-size: 14px; letter-spacing: 1px; }
    .scr33nx-saved .scr33nx-off { color: rgba(255,255,255,.35); }
    /* Glow pulse instead of fading the badge, so REC never goes faint. */
    @keyframes scr33nx-pulse {
      50% { box-shadow: 0 0 0 4px rgba(229,57,53,.45), 0 1px 3px rgba(0,0,0,.6); }
    }
    .scr33nx-rec, .scr33nx-lnkrec { animation: scr33nx-pulse 1.2s ease-in-out infinite; }
    /* Highlight filter: non-matching thumbnails stay visible but go dark. */
    a.scr33nx-dim { filter: brightness(.3) saturate(.4); transition: filter .2s; }
    a.scr33nx-dim:hover { filter: none; }
    .scr33nx-mini {
      position: fixed; z-index: 2147483647; border: 2px solid #ff2b3d;
      border-radius: 6px; background: #000; box-shadow: 0 6px 24px rgba(0,0,0,.7);
    }
  `;
  const styleEl = document.createElement('style');
  styleEl.textContent = STYLE;
  document.documentElement.appendChild(styleEl);

  // Model name from a card link href (same first-segment rule as the popup;
  // MFC links may carry the name in the hash instead).
  function nameFromHref(href) {
    let u;
    try { u = new URL(href, location.href); } catch { return null; }
    if (!u.hostname.replace('www.', '').includes(host)) return null;
    let name;
    if (site === 'myfreecams' && u.hash) {
      let h = u.hash.replace(/^#\/?/, '');
      if (h.startsWith('model/')) h = h.slice('model/'.length);
      name = h.split(/[/?]/).filter(Boolean)[0] || '';
    } else {
      const segs = u.pathname.split('/').filter(Boolean);
      if (segs.length !== 1) return null;   // deep paths are never model roots
      name = segs[0];
    }
    name = (name || '').toLowerCase();
    if (!name || SKIP.includes(name)) return null;
    if (!/^[a-z0-9_-]+$/.test(name)) return null;
    return name;
  }

  // Saved-model badge shows the 0-5 star rank: filled stars gold, the rest dim.
  function starsHtml(rank) {
    const r = Math.max(0, Math.min(5, rank | 0));
    return '★'.repeat(r) + `<span class="scr33nx-off">${'★'.repeat(5 - r)}</span>`;
  }

  function badgeFor(m) {
    if (m.status === 'recording') return ['scr33nx-rec',   '● REC',
      `Scr33nX: recording ${m.name}`];
    if (m.linked_recording)       return ['scr33nx-lnkrec', '● REC',
      `Scr33nX: already recording as ${m.linked_recording.name} on ${m.linked_recording.site}`];
    if (m.status === 'online')    return ['scr33nx-on',    'ON',
      `Scr33nX: ${m.name} online (in Recorder)`];
    if (m.in_recorder)            return ['scr33nx-idle',  'OFF',
      `Scr33nX: ${m.name} in Recorder (${m.status || 'idle'})`];
    const rank = m.rank | 0;
    return ['scr33nx-saved', starsHtml(rank),
      `Scr33nX: ${m.name} in Saved Models — ${rank ? `rank ${rank}/5` : 'unranked'}`];
  }

  // Highlight keys: 'recording', 'online', 'r5'..'r1' (exact rank), 'r0' (no
  // rank). A card stays bright when its model matches ANY selected key;
  // untracked models never match.
  function matches(m, key) {
    if (!m) return false;
    if (key === 'recording') return m.status === 'recording' || !!m.linked_recording;
    if (key === 'online')    return m.status === 'online' || m.status === 'recording';
    if (key[0] === 'r')      return (m.rank | 0) === Number(key.slice(1));
    return false;
  }

  const isCard = (a) => !!a.querySelector('img, video, picture');

  function sweep() {
    if (!known) {
      document.querySelectorAll('a.scr33nx-dim')
        .forEach((a) => a.classList.remove('scr33nx-dim'));
      return;
    }
    const hl = Array.isArray(opts.highlight) ? opts.highlight : [];
    for (const a of document.querySelectorAll('a[href]')) {
      const name = nameFromHref(a.getAttribute('href'));
      const m = name ? known.get(name) : null;
      const dim = hl.length > 0 && !!name && !hl.some((k) => matches(m, k)) && isCard(a);
      a.classList.toggle('scr33nx-dim', dim);
      let badge = a.querySelector(':scope > .scr33nx-badge');
      if (!m) {
        if (badge) badge.remove();
        continue;
      }
      // Only badge links that look like cards (contain an image/preview);
      // plain text links (menus, chat mentions) stay clean.
      if (!badge && !isCard(a)) continue;
      if (!badge) {
        if (getComputedStyle(a).position === 'static') a.style.position = 'relative';
        badge = document.createElement('span');
        badge.className = 'scr33nx-badge';
        a.appendChild(badge);
      }
      const [cls, text, title] = badgeFor(m);
      const want = `scr33nx-badge ${cls}`;
      if (badge.className !== want) badge.className = want;
      // Badge markup is built only from our own constants (no page/model text).
      if (badge.innerHTML !== text) badge.innerHTML = text;
      badge.title = title;   // pointer-events:none — shown via the card hover
      a.title = title;
    }
  }

  async function refresh() {
    let resp = null;
    try { resp = await browser.runtime.sendMessage({ type: 'models' }); } catch {}
    if (resp && resp.models) {
      known = new Map(resp.models.filter((m) => m.site === site)
                                 .map((m) => [m.name, m]));
    } else {
      known = null;                        // app down → drop stale badges
      document.querySelectorAll('.scr33nx-badge').forEach((b) => b.remove());
    }
    sweep();
  }

  // ── Hover mini player ───────────────────────────────────────────────────────
  // Resting on the card of an online/recording Recorder model for mpDelay ms
  // (or, with "Preview any model", of a model the Recorder doesn't poll)
  // opens player.html (an extension page, so neither the site's CSP nor its
  // origin gets in the way of the localhost relay) beside the card. It closes
  // once the pointer is on neither the card nor the player.
  let hoverA = null, hoverTimer = null, closeTimer = null;
  let frame = null, frameA = null;

  function closePlayer() {
    clearTimeout(closeTimer);
    if (frame) frame.remove();
    frame = null;
    frameA = null;
  }

  function scheduleClose() {
    if (!frame) return;
    clearTimeout(closeTimer);
    closeTimer = setTimeout(closePlayer, 250);   // grace to cross the gap
  }

  function openPlayer(a, m, any) {
    closePlayer();
    if (!a.isConnected) return;
    const r = a.getBoundingClientRect();
    const vw = innerWidth, vh = innerHeight, gap = 8;
    const w = Math.min(Number(opts.mpWidth) || 360, vw - 16);
    const h = Math.round(w * 9 / 16) + 4;
    let left = r.right + gap;                          // right of the card…
    if (left + w > vw - 4) left = r.left - gap - w;    // …else left of it…
    if (left < 4) left = Math.max(4, Math.min(vw - w - 4, r.left + (r.width - w) / 2));
    const top = Math.max(4, Math.min(vh - h - 4, r.top + (r.height - h) / 2));
    frame = document.createElement('iframe');
    frame.className = 'scr33nx-mini';
    frame.allow = 'autoplay';
    frame.src = browser.runtime.getURL('player.html') + '?' + new URLSearchParams({
      name: m.name, site: m.site, q: String(opts.mpQuality | 0),
      muted: opts.mpMuted ? '1' : '0', any: any ? '1' : '0',
    });
    Object.assign(frame.style, { left: `${left}px`, top: `${top}px`,
                                 width: `${w}px`, height: `${h}px` });
    document.documentElement.appendChild(frame);
    frameA = a;
  }

  document.addEventListener('pointerover', (e) => {
    if (frame && e.target === frame) { clearTimeout(closeTimer); return; }
    const a0 = e.target.closest ? e.target.closest('a[href]') : null;
    const a = a0 && isCard(a0) ? a0 : null;
    if (a === hoverA) return;                 // still inside the same card
    hoverA = a;
    clearTimeout(hoverTimer);
    if (frame) {
      if (a === frameA) { clearTimeout(closeTimer); return; }
      scheduleClose();
    }
    if (!a || !opts.mpEnabled || !known) return;
    const name = nameFromHref(a.getAttribute('href'));
    if (!name) return;
    const m = known.get(name);
    const live = m && (m.status === 'online' || m.status === 'recording');
    // Models the Recorder polls and knows are offline are skipped even then.
    const any = !live && opts.mpAny && !(m && (m.in_recorder || m.status));
    if (!live && !any) return;
    hoverTimer = setTimeout(() => openPlayer(a, m || { name, site }, any),
                            Number(opts.mpDelay) || 1000);
  }, true);

  const reset = () => { clearTimeout(hoverTimer); hoverA = null; closePlayer(); };
  document.documentElement.addEventListener('mouseleave', () => {
    clearTimeout(hoverTimer);
    hoverA = null;
    scheduleClose();
  });
  addEventListener('scroll', reset, { passive: true, capture: true });
  document.addEventListener('visibilitychange', reset);

  // ── Options ──────────────────────────────────────────────────────────────────
  function applyOpts(st) {
    for (const k of Object.keys(OPT_DEFAULTS)) {
      if (st && st[k] !== undefined) opts[k] = st[k];
    }
  }
  browser.storage.local.get(Object.keys(OPT_DEFAULTS))
    .then((st) => { applyOpts(st); sweep(); }).catch(() => {});
  browser.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    const st = {};
    for (const k of Object.keys(OPT_DEFAULTS)) {
      if (changes[k]) st[k] = changes[k].newValue ?? OPT_DEFAULTS[k];
    }
    if (!Object.keys(st).length) return;
    applyOpts(st);
    if (!opts.mpEnabled) reset();
    sweep();
  });

  // Re-sweep when infinite scroll / SPA navigation adds cards (debounced).
  let debounce = null;
  new MutationObserver((muts) => {
    if (muts.every((mu) => [...mu.addedNodes].every(
        (n) => n.nodeType !== 1 || n.classList?.contains('scr33nx-badge')
            || n.classList?.contains('scr33nx-mini')))) return;
    clearTimeout(debounce);
    debounce = setTimeout(sweep, 500);
  }).observe(document.documentElement, { childList: true, subtree: true });

  refresh();
  setInterval(() => { if (document.visibilityState === 'visible') refresh(); }, 10000);
})();
