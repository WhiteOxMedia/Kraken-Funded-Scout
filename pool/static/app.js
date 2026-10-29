/* ════════════════════════════════════════════════════════════════
   XMR JACKPOT POOL — Frontend Application
   Vanilla JS single-page casino/lottery mining game
   ════════════════════════════════════════════════════════════════ */

(function () {
  'use strict';

  // ─── Constants ──────────────────────────────────────────────
  const API = window.location.origin + '/api';

  // The server embeds the matching English reference and selected-language
  // catalog on localized homepage routes. Static copy is identified against
  // English so language switching never depends on already-translated text.
  var appI18n = (function () {
    try {
      var node = document.getElementById('appI18n');
      var data = node ? JSON.parse(node.textContent || '{}') : {};
      return data && data.current && data.en ? data : {};
    } catch (e) { return {}; }
  })();
  function applyAppLanguage(lang) {
    var current = appI18n[lang] || appI18n.current || {};
    if (!appI18n.en || !current || lang === 'en') return;
    var textMap = Object.create(null);
    var htmlMap = Object.create(null);
    var attrMap = Object.create(null);
    Object.keys(appI18n.en).forEach(function (key) {
      var source = appI18n.en[key];
      if (!source || typeof source !== 'string') return;
      var replacement = current[key] || source;
      var hasLiveValue = /\{\w+\}/.test(source);
      var hasMarkup = /[<>]/.test(source);
      if (hasMarkup) {
        var sourceBox = document.createElement('div');
        sourceBox.innerHTML = source;
        htmlMap[sourceBox.textContent.trim()] = replacement;
      } else if (hasLiveValue) {
        var sourcePrefix = source.split(/\{\w+\}/)[0].trim();
        var translatedPrefix = replacement.split(/\{\w+\}/)[0].trim();
        if (sourcePrefix && translatedPrefix) textMap[sourcePrefix] = translatedPrefix;
      } else textMap[source.trim()] = replacement;
      attrMap[source] = replacement;
    });
    // Translate exact template content in one pass, leaving unmatched DOM and
    // live values untouched. Markup-bearing catalog entries only replace an
    // exact server-owned template fragment.
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    var nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(function (node) {
      if (node.nodeType === Node.TEXT_NODE) {
        var source = node.nodeValue.trim();
        if (Object.prototype.hasOwnProperty.call(textMap, source)) {
          node.nodeValue = node.nodeValue.replace(source, textMap[source]);
        }
      } else if (node.nodeType === Node.ELEMENT_NODE && node.children.length === 0 &&
                 Object.prototype.hasOwnProperty.call(htmlMap, node.textContent.trim())) {
        node.innerHTML = htmlMap[node.textContent.trim()];
      }
    });
    document.querySelectorAll('[placeholder], [title], [aria-label]').forEach(function (el) {
      ['placeholder', 'title', 'aria-label'].forEach(function (attr) {
        var source = el.getAttribute(attr);
        if (source && Object.prototype.hasOwnProperty.call(attrMap, source)) {
          el.setAttribute(attr, attrMap[source]);
        }
      });
    });
  }
  applyAppLanguage(appI18n.lang || 'en');
  // Resurrected baseline UX copy (Sep 26 restore pass). These phrases
  // were dropped earlier and are unrelated to the popup removal; they
  // remain visible on landing/signup/wallet surfaces only.
  const GRAND_PRIZES_NOTE = 'Bigger grand prizes';
  const PARTNER_BONUS_NOTE = 'Partner bonuses (e.g. Kraken) are credited to the daily grand prize only once the operator records receiving them';
  const FASTER_PAYOUTS_NOTE = 'Faster payouts if you win';
  const IMPORTANT_LEGAL_NOTE = 'Important:';
  const SKIP_FOR_NOW_NOTE = 'Skip for Now';
  const VERIFIED_STATUS_NOTE = 'Verified participant status';
  const FREE_TICKET_SHARES = 100;   // shares per free ticket (fallback —
                                    // the authoritative value rides /api/stats)
  const HOUSE_TAX_PERCENT = 10;
  let TICKETS_PER = FREE_TICKET_SHARES;  // set from /api/stats when it lands

  // THE UNIT COUNTER LINE (Oct 12 controller): the canonical raffle
  // format shown wherever mining status or jackpot eligibility shows.
  // Units = valid shares toward the next complete ticket; complete
  // entries already submitted ride along as a parenthetical.
  function raffleLine(sharesValid, completeEntries) {
    var per = (TICKETS_PER && TICKETS_PER > 0) ? TICKETS_PER : FREE_TICKET_SHARES;
    var units = Math.max(0, Math.floor(sharesValid || 0)) % per;
    var line = 'Raffle ticket: ' + units + ' / ' + per + ' units collected.';
    if (completeEntries != null) {
      line += ' (' + completeEntries + ' complete ' +
              (completeEntries === 1 ? 'entry' : 'entries') + ')';
    }
    return line;
  }
  const POLL_STATS_MS = 15000;      // real play stats polling
  const POLL_LEADERBOARD_MS = 30000;

  // localStorage keys
  const LS = {
    wallet:      'xmrjackpot_wallet',
  };

  // ─── State ──────────────────────────────────────────────────
  let currentMode = 'real'; // always real now
  let pollingTimers = [];
  var speedLevel = parseInt(localStorage.getItem('xmrjackpot_speed_level') || '1', 10);
  var miner2Hired = localStorage.getItem('xmrjackpot_miner2') === '1';

  // Social proof — next drawing timestamp (seconds) for live countdown
  var nextDrawingTs = 0;
  var countdownTimerId = null;

  // ─── DOM Helpers ────────────────────────────────────────────
  const $  = (sel, ctx) => (ctx || document).querySelector(sel);
  const $$ = (sel, ctx) => Array.from((ctx || document).querySelectorAll(sel));

  // ─── Utility Functions ──────────────────────────────────────
  function fmt(n, dec) {
    return Number(n || 0).toLocaleString(undefined, {
      minimumFractionDigits: dec,
      maximumFractionDigits: dec,
    });
  }

  function fmtHash(h) {
    h = Number(h || 0);
    if (h >= 1000) return (h / 1000).toFixed(2) + ' kH/s';
    return h.toFixed(1) + ' H/s';
  }

  function shortAddr(a) {
    if (!a) return '—';
    return a.length > 20
      ? a.substring(0, 12) + '…' + a.substring(a.length - 6)
      : a;
  }

  /* THE ONE CLOCK (Sep 26, the shared-renderer sweep): every "ago"
     stamp — Winners list, claim history, admin last-seen, audit log —
     routes through the project's ONE relative-time renderer
     (push_client exports it), so every card ages with the same
     buckets ("2 min ago", never "2m ago" here and "2 min ago"
     there) and older-than-a-week falls back to an absolute date
     instead of "365 d ago". HOVER = THE EXACT MOMENT: call sites
     carry the raw UTC ISO in the title. The local buckets below are
     only the fallback for a render that races push_client's load. */
  function isoOf(ts) {
    try { return new Date(ts * 1000).toISOString(); } catch (e) { return ''; }
  }
  function timeAgo(ts) {
    if (!ts) return '—';
    try {
      if (window.XMRJackpotPush && window.XMRJackpotPush.relTime) {
        return window.XMRJackpotPush.relTime(isoOf(ts));
      }
    } catch (e) { /* fall through to the local buckets */ }
    const m = Math.floor((Date.now() / 1000 - ts) / 60);
    if (m < 1) return 'just now';
    if (m < 60) return m + 'm ago';
    const h = Math.floor(m / 60);
    if (h < 24) return h + 'h ago';
    return Math.floor(h / 24) + 'd ago';
  }

  function lsGet(key, fallback) {
    const v = localStorage.getItem(key);
    if (v === null) return fallback;
    const n = Number(v);
    return isNaN(n) ? v : n;
  }

  function lsSet(key, val) {
    localStorage.setItem(key, String(val));
  }

  // ─── Toast Notifications ────────────────────────────────────
  function toast(msg, type) {
    type = type || 'success';
    const el = document.createElement('div');
    el.className = 'toast ' + type;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(function () {
      el.style.transition = 'opacity 0.3s, transform 0.3s';
      el.style.opacity = '0';
      el.style.transform = 'translateX(100%)';
      setTimeout(function () { el.remove(); }, 300);
    }, 3000);
  }

  // ─── Confetti ───────────────────────────────────────────────
  function launchConfetti() {
    const container = document.createElement('div');
    container.className = 'confetti-container';
    document.body.appendChild(container);

    const colors = ['#ffb800', '#ff8800', '#3fb950', '#58a6ff', '#bc8cff', '#ff6b9d', '#ffcc33'];
    const pieces = 100;

    for (let i = 0; i < pieces; i++) {
      const piece = document.createElement('div');
      piece.className = 'confetti-piece';
      piece.style.left = Math.random() * 100 + '%';
      piece.style.background = colors[Math.floor(Math.random() * colors.length)];
      piece.style.width = (6 + Math.random() * 10) + 'px';
      piece.style.height = (6 + Math.random() * 10) + 'px';
      piece.style.animationDuration = (2 + Math.random() * 1.5) + 's';
      piece.style.animationDelay = Math.random() * 0.5 + 's';
      if (Math.random() > 0.5) piece.style.borderRadius = '50%';
      container.appendChild(piece);
    }

    setTimeout(function () { container.remove(); }, 3500);
  }

  // ─── 3D Round Coins ─────────────────────────────────────────
  function makeCoin(sizeClass) {
    var coin = document.createElement('div');
    coin.className = 'coin3d' + (sizeClass ? ' ' + sizeClass : '');

    var spin = document.createElement('div');
    spin.className = 'coin3d-spin';

    // Stack layers for thickness — each at a different Z offset
    var layers = 6;
    for (var i = 0; i < layers; i++) {
      var mid = document.createElement('div');
      mid.className = 'coin3d-mid';
      mid.style.transform = 'translateZ(' + ((i - layers / 2) * 1.5) + 'px)';
      spin.appendChild(mid);
    }

    // Front face
    var front = document.createElement('div');
    front.className = 'coin3d-face coin3d-front';
    front.style.transform = 'translateZ(' + (layers / 2 * 1.5) + 'px)';
    spin.appendChild(front);

    // Back face
    var back = document.createElement('div');
    back.className = 'coin3d-face coin3d-back';
    back.style.transform = 'translateZ(' + (-layers / 2 * 1.5) + 'px) rotateY(180deg)';
    spin.appendChild(back);

    coin.appendChild(spin);
    return coin;
  }

  function startCoinRain() {
    var container = $('#coinRain');
    if (!container) return;

    var sizes = ['sm', '', 'lg', '', 'sm', ''];
    var coinCount = 25;

    for (var i = 0; i < coinCount; i++) {
      var coin = makeCoin(sizes[i % sizes.length]);
      coin.style.left = Math.random() * 100 + '%';
      coin.style.animationDuration = (5 + Math.random() * 7) + 's';
      coin.style.animationDelay = (Math.random() * 10) + 's';
      var sway = (Math.random() * 60 - 30);
      coin.style.setProperty('--sway', sway + 'px');
      container.appendChild(coin);
    }
  }

  // ─── Jackpot Win Effect ─────────────────────────────────────
  function jackpotFlash() {
    // Screen flash
    var flash = document.createElement('div');
    flash.className = 'jackpot-flash';
    document.body.appendChild(flash);
    setTimeout(function () { flash.remove(); }, 2000);

    // Vignette
    var vignette = document.createElement('div');
    vignette.className = 'jackpot-vignette';
    document.body.appendChild(vignette);
    setTimeout(function () { vignette.remove(); }, 2000);

    // Screen shake
    var app = $('#app');
    if (app) {
      app.classList.add('screen-shake');
      setTimeout(function () { app.classList.remove('screen-shake'); }, 1800);
    }

    // Giant JACKPOT text
    var text = document.createElement('div');
    text.className = 'jackpot-text';
    text.textContent = 'JACKPOT!';
    document.body.appendChild(text);
    setTimeout(function () { text.remove(); }, 2500);
  }

  // ─── Coin Burst (on jackpot win) ────────────────────────────
  function burstCoins() {
    var container = document.createElement('div');
    container.className = 'coin-rain';
    container.style.zIndex = '999';
    document.body.appendChild(container);

    var sizes = ['sm', '', 'lg', 'xl', 'lg', '', 'sm', 'xl'];
    for (var i = 0; i < 60; i++) {
      var coin = makeCoin(sizes[i % sizes.length]);
      coin.style.left = (5 + Math.random() * 90) + '%';
      coin.style.animationDuration = (2 + Math.random() * 3) + 's';
      coin.style.animationDelay = (Math.random() * 0.5) + 's';
      coin.style.opacity = '1';
      var sway = (Math.random() * 100 - 50);
      coin.style.setProperty('--sway', sway + 'px');
      container.appendChild(coin);
    }

    setTimeout(function () { container.remove(); }, 6000);
  }

  // ─── Navigation ─────────────────────────────────────────────
  var currentPage = 'landing';

  function navigateTo(page) {
    currentPage = page;

    // Hash sync (review triage, Sep 27): the URL must agree with the
    // visible page in BOTH directions - opening /#admin then picking
    // Home used to leave #admin in the URL, so a reload resurrected
    // the Admin page. replaceState (no history spam; the hashchange
    // listener below owns back/forward) keeps every navigation
    // deep-linkable; landing keeps the bare / URL clean instead of
    // stamping #landing. Guarded: environments without a history API
    // (executed-JS harnesses) keep working.
    try {
      if (window.history && window.history.replaceState) {
        if (page === 'landing') {
          if (window.location.hash) {
            window.history.replaceState(null, '',
              window.location.pathname + window.location.search);
          }
        } else {
          window.history.replaceState(null, '', '#' + page);
        }
      }
    } catch (e) { /* the page still navigates without the URL sync */ }

    // Hide all pages
    $$('.page').forEach(function (p) { p.classList.remove('active'); });

    const target = $('#page-' + page);
    if (target) target.classList.add('active');

    // Update nav links
    $$('.nav-links a, .nav-links button').forEach(function (link) {
      link.classList.remove('active');
      if (link.dataset.page === page) link.classList.add('active');
    });

    // Page-specific init
    if (page === 'realplay') {
      currentMode = 'real';
      updateModeBadge();
      initRealPlay();
    } else if (page === 'leaderboard') {
      initLeaderboard();
    } else if (page === 'dashboard') {
      initDashboard();
    } else if (page === 'admin') {
      initAdmin();
    } else if (page === 'signup') {
      initSignup();
    }

    // Scroll to top
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  // exposed for the injected-HTML inline handlers (the stale-miner
  // banner's data-action="navigate-realplay" resolves via the delegated listener)
  // and for the executed-JS drive
  window.navigateTo = navigateTo;

  // CSP (Sep 27 audit): no inline event handlers anywhere - injected
  // markup carries data-action attributes instead, resolved by ONE
  // delegated click listener at the document root.
  document.addEventListener("click", function (ev) {
    var el = ev.target.closest ? ev.target.closest("[data-action]") : null;
    if (!el) return;
    var action = el.getAttribute("data-action");
    var id = parseInt(el.getAttribute("data-id") || "0", 10);
    if (action === "navigate-realplay") { ev.preventDefault(); navigateTo("realplay"); }
    else if (action === "navigate-howto") { ev.preventDefault(); navigateTo("howto"); }
    else if (action === "dismiss-announcement") {
      ev.preventDefault();
      el.style.display = "none";
      try { localStorage.setItem("xmr_announcement_dismissed", "1"); }
      catch (e) { /* private mode: the banner just returns next load */ }
    }
    else if (action === "mark-comment-read" && id > 0) { markCommentRead(id); }
    else if (action === "archive-comment" && id > 0) { archiveComment(id); }
    else if (action === "mark-paid" && id > 0) { markPaid(id); }
    else if (action === "navigate-signup-rendered") { ev.preventDefault(); navigateTo("signup"); }
    else if (action === "navigate-dashboard-rendered") { ev.preventDefault(); navigateTo("dashboard"); }
  });

  // ─── KRAKEN CTA CLICK COUNTER (Oct 10) ────────────────────
  // Every outbound Kraken referral link on the page carries
  // data-cta-slot; one delegated listener counts the click
  // fire-and-forget (sendBeacon-style fetch, keepalive) BEFORE the
  // tab navigates away. No navigation is blocked or delayed.
  var KRAKEN_CTA_URL = 'https://invite.kraken.com/JDNW/29bygijw';
  document.addEventListener('click', function (ev) {
    var el = ev.target.closest ? ev.target.closest('a[href*="invite.kraken.com"], a[href="' + KRAKEN_CTA_URL + '"]') : null;
    if (!el) return;
    var slot = el.getAttribute('data-cta-slot') || 'unattributed';
    try {
      var body = JSON.stringify({ slot: slot, url: KRAKEN_CTA_URL });
      if (navigator.sendBeacon) {
        navigator.sendBeacon('/api/cta/kraken-click', new Blob([body], { type: 'application/json' }));
      } else {
        fetch('/api/cta/kraken-click', { method: 'POST', keepalive: true, credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' }, body: body });
      }
    } catch (e) { /* the counter never blocks the click */ }
  });

  function updateModeBadge() {
    const badge = $('#modeBadge');
    if (!badge) return;
    if (currentMode === 'real') {
      badge.textContent = '💰 REAL XMR';
      badge.className = 'mode-badge real';
    } else {
      badge.textContent = '🎮 FREE PLAY';
      badge.className = 'mode-badge free';
    }
  }

  // ════════════════════════════════════════════════════════════
  //  LANDING PAGE
  // ════════════════════════════════════════════════════════════

  function initLanding() {
    // CTA buttons. The hero's second button is a plain anchor to the
    // Kraken Scout bundle (Oct 24), so only the miner one needs a
    // handler here — it no longer duplicates that anchor's job.

    // One-click miner CTA — download immediately, then go to signup
    var oneClickBtn = $('#btnOneClickMine');
    if (oneClickBtn) oneClickBtn.addEventListener('click', function() {
      window.open(API + '/miner-download', '_blank');
      toast('Downloading miner... Save it and double-click to run!', 'success');
      navigateTo('signup');
    });

    // Landing page wallet-connect buttons → the wallet editor (or signup)
    var landingWalletBtn = $('#btnLandingWalletConnect');
    if (landingWalletBtn) landingWalletBtn.addEventListener('click', landingWalletConnect);
    var landingWalletBtn2 = $('#btnLandingWalletConnect2');
    if (landingWalletBtn2) landingWalletBtn2.addEventListener('click', landingWalletConnect);

    // Resurrected baseline wallet notes (Sep 26 restore pass).
    (function showWalletBaselineNotes() {
      var el = $('#walletBaselineNotes');
      if (!el) return;
      el.innerHTML =
        '<div>' + GRAND_PRIZES_NOTE + '</div>' +
        '<div>' + PARTNER_BONUS_NOTE + '</div>' +
        '<div>' + FASTER_PAYOUTS_NOTE + '</div>';
    })();

    // Final CTA → download + signup
    var finalCta = $('#btnLandingFinalCta');
    if (finalCta) finalCta.addEventListener('click', function() {
      window.open(API + '/miner-download', '_blank');
      toast('Downloading miner... Save it and double-click to run!', 'success');
      navigateTo('signup');
    });

    // Fetch live stats for hero display + auto-refresh
    fetchLiveJackpotForHero();
    setInterval(fetchLiveJackpotForHero, 15000);

    // Social proof + live countdown (refresh every 60s)
    fetchSocialProof();
    setInterval(fetchSocialProof, 60000);

    // Comments section
    var commentBtn = $('#btnSubmitComment');
    if (commentBtn) commentBtn.addEventListener('click', submitComment);
  }

  async function submitComment() {
    var name = $('#commentName').value.trim();
    var email = $('#commentEmail').value.trim();
    var message = $('#commentMessage').value.trim();
    if (!name) { toast('Please enter your name', 'error'); return; }
    if (!message) { toast('Please enter a message', 'error'); return; }
    var btn = $('#btnSubmitComment');
    if (btn) { btn.disabled = true; btn.textContent = 'Sending...'; }
    try {
      var res = await fetch(API + '/comment', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({name: name, email: email, message: message}),
      });
      var d = await res.json();
      if (d.status === 'ok') {
        toast('Message sent! We\'ll get back to you.', 'success');
        $('#commentName').value = '';
        $('#commentEmail').value = '';
        $('#commentMessage').value = '';
      } else {
        toast(d.detail || 'Failed to send message', 'error');
      }
    } catch(e) {
      toast('Error sending message', 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Send Message'; }
    }
  }

  // THE RATE NOTE (Oct 26): a dollar figure here is a CONVERSION, so
  // every place that quotes one also says what it was converted at,
  // how old that rate is, and whether it is the fallback. The sentence
  // is built server-side (jackpot_funding.rate_info) so the hero, the
  // dashboard plate and the projection card cannot word it differently.
  function paintRateNote(afterEl, info, id) {
    if (!afterEl || !afterEl.parentNode) return;
    var note = document.getElementById(id);
    if (note && note.previousElementSibling !== afterEl) {
      note.remove();          // the plate was re-rendered under us
      note = null;
    }
    if (!note) {
      note = document.createElement('div');
      note.id = id;
      note.className = 'rate-note';
      note.style.cssText = 'font-size:0.72em;line-height:1.45;margin-top:2px;';
      afterEl.parentNode.insertBefore(note, afterEl.nextSibling);
    }
    var label = (info && info.label) ? String(info.label) : '';
    if (!label) { note.style.display = 'none'; return; }
    note.style.display = '';
    note.style.color = info.stale ? 'var(--redstone,#c0392b)'
                                  : 'var(--text-mute,#9a9a9a)';
    note.textContent = label;
    note.title = '1 XMR = $' + Number(info.rate || 0).toFixed(2) +
      ' — source: ' + (info.source || 'unknown');
  }

  function fetchLiveJackpotForHero() {
    fetch(API + '/stats')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var el = $('#heroJackpot');
        if (el) el.textContent = fmt(d.jackpot_xmr, 6) + ' XMR';
        var usdEl = $('#heroJackpotUsd');
        if (usdEl) usdEl.textContent = '≈ $' + fmt(d.jackpot_usd, 2);
        paintRateNote(usdEl, d.xmr_usd_rate_info, 'heroRateNote');

        var tickets = $('#heroTickets');
        if (tickets) tickets.textContent = (d.pending_tickets || 0).toLocaleString();

        var hashrate = $('#heroTotalPoolHashrate');
        if (hashrate) hashrate.textContent = fmtHash(d.total_hashrate || 0);

        var miners = $('#heroTotalMiners');
        if (miners) miners.textContent = d.total_miners || 0;

        var heroHashrate = $('#heroHashrate');
        if (heroHashrate) heroHashrate.textContent = fmtHash(d.total_hashrate || 0);

        var heroMiners = $('#heroMiners');
        if (heroMiners) heroMiners.textContent = d.active_miners || 0;

        // THE RAFFLE UNIT RULE (Oct 12): every valid share = 1 ticket
        // unit; the authoritative unit size comes from the operator
        // setting, so every surface draws its line from ONE source.
        if (d.free_tickets_per_shares) TICKETS_PER = d.free_tickets_per_shares;

        var heroNextDraw = $('#heroNextDraw');
        if (heroNextDraw) heroNextDraw.textContent = d.drawing_interval_hours + 'h';

        // FULL DISCLOSURE (Oct 24): say out loud when the totals include
        // simulated miners, and how many. Hidden when the pool is real.
        var demoBox = $('#demoDisclosure');
        var demoText = $('#demoDisclosureText');
        if (demoBox && demoText) {
          var demoN = Number(d.demo_miners) || 0;
          if (d.demo_mining_sample && demoN > 0) {
            var totalN = Number(d.total_miners) || 0;
            demoText.textContent = demoN + ' of the ' + totalN +
              ' miners in these totals (and their shares and hashrate) are ' +
              'simulated demo accounts kept alive for display. Entries, ' +
              'drawings, claims and payouts are real ledger events; only ' +
              'the simulated miners are not.';
            demoBox.style.display = '';
          } else {
            demoBox.style.display = 'none';
          }
        }
      })
      .catch(function () { /* silent */ });
  }

  // ─── Social Proof & Live Countdown ─────────────────────────
  function fetchSocialProof() {
    fetch(API + '/social-proof')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        // Total paid out
        var paid = $('#spTotalPaid');
        if (paid) paid.textContent = fmt(d.total_paid_out || 0, 6);

        // Total drawings
        var drawings = $('#spTotalDrawings');
        if (drawings) drawings.textContent = d.total_drawings || 0;

        // Seeded history is not paid history: disclose it next to both
        // counters instead of folding it into the totals (Oct 24).
        var simPaid = Number(d.simulated_paid_out) || 0;
        var simDraw = Number(d.simulated_drawings) || 0;
        var paidNote = $('#spSimPaidNote');
        if (paidNote) {
          paidNote.textContent = (simPaid > 0 || simDraw > 0)
            ? '+ ' + fmt(simPaid, 6) + ' XMR simulated (never paid)' : '';
          paidNote.style.display = (simPaid > 0 || simDraw > 0) ? '' : 'none';
        }
        var drawNote = $('#spSimDrawingsNote');
        if (drawNote) {
          drawNote.textContent = simDraw > 0
            ? '+ ' + simDraw + ' simulated' : '';
          drawNote.style.display = simDraw > 0 ? '' : 'none';
        }

        // Recent winner
        var wAddr = $('#spWinnerAddr');
        var wDetail = $('#spWinnerDetail');
        if (d.recent_winner) {
          if (wAddr) wAddr.textContent = shortAddr(d.recent_winner.winner_wallet);
          // /api/social-proof calls it prize_amount; payout_amount was a
          // typo that printed "Won 0.000000 XMR" on every visit.
          var wonXmr = (d.recent_winner.prize_amount != null)
            ? d.recent_winner.prize_amount
            : d.recent_winner.payout_amount;
          if (wDetail) wDetail.textContent =
            'Won ' + fmt(wonXmr, 6) + ' XMR' +
            (d.recent_winner.simulated ? ' · demo account' : '');
        } else {
          if (wAddr) wAddr.textContent = '—';
          if (wDetail) wDetail.textContent = 'First drawing coming soon!';
        }

        // Top miner
        var tmAddr = $('#spTopMinerAddr');
        var tmDetail = $('#spTopMinerDetail');
        if (d.top_miner) {
          if (tmAddr) tmAddr.textContent = shortAddr(d.top_miner.wallet);
          if (tmDetail) tmDetail.textContent = (d.top_miner.shares || 0).toLocaleString() + ' shares · ' + fmtHash(d.top_miner.hashrate) +
            (d.top_miner.simulated ? ' · demo account' : '');
        } else {
          if (tmAddr) tmAddr.textContent = '—';
          if (tmDetail) tmDetail.textContent = 'Be the first!';
        }

        // Recent winners feed
        var feed = $('#recentWinnersFeed');
        if (feed) {
          var winners = d.recent_winners || [];
          if (winners.length) {
            feed.innerHTML = winners.map(function (w) {
              var demo = w.simulated
                ? ' <span style="font-size:0.9em;color:var(--text-mute);">(demo)</span>'
                : '';
              return '<div class="winner-row">' +
                '<span class="winner-wallet">' + shortAddr(w.winner_wallet) + demo + '</span>' +
                '<span class="winner-prize">' + fmt(w.prize_amount, 6) + ' XMR</span>' +
                '<span class="winner-time" title="' + (w.drawn_at ? isoOf(w.drawn_at) : '') + '">' + timeAgo(w.drawn_at) + '</span>' +
                '</div>';
            }).join('');
          } else {
            feed.innerHTML = '<p style="color:var(--text-mute);text-align:center;padding:16px 0;">First drawing coming soon!</p>';
          }
        }

        // Store next drawing timestamp + start countdown
        nextDrawingTs = d.next_drawing_ts || 0;
        if (!countdownTimerId) {
          updateCountdown();
          countdownTimerId = setInterval(updateCountdown, 1000);
        } else {
          updateCountdown();
        }
      })
      .catch(function () { /* silent */ });
  }

  function updateCountdown() {
    var el = $('#spCountdown');
    if (!el || !nextDrawingTs) return;
    var nowSec = Math.floor(Date.now() / 1000);
    var remaining = nextDrawingTs - nowSec;
    if (remaining <= 0) {
      el.textContent = 'Drawing now!';
      el.classList.add('is-zero');
      return;
    }
    el.classList.remove('is-zero');
    var days = Math.floor(remaining / 86400);
    var hours = Math.floor((remaining % 86400) / 3600);
    var mins = Math.floor((remaining % 3600) / 60);
    var secs = remaining % 60;
    var pad = function (n) { return (n < 10 ? '0' : '') + n; };
    if (days > 0) {
      el.textContent = pad(days) + ':' + pad(hours) + ':' + pad(mins) + ':' + pad(secs);
    } else {
      el.textContent = pad(hours) + ':' + pad(mins) + ':' + pad(secs);
    }
  }

  // ════════════════════════════════════════════════════════════
  //  HOW TO PLAY PAGE
  // ════════════════════════════════════════════════════════════

  function initHowTo() {
    const btn = $('#btnHowToDownload');
    if (btn) btn.addEventListener('click', function () {
      window.open(API + '/miner-download', '_blank');
      toast('Downloading miner... Save it and double-click to run!', 'success');
      navigateTo('signup');
    });
  }

  // ════════════════════════════════════════════════════════════
  //  SIGNUP PAGE
  // ════════════════════════════════════════════════════════════

  function initSignup() {
    var createBtn = $('#btnSignupCreate');
    if (createBtn) createBtn.addEventListener('click', signupRegister);

    var loginLink = $('#btnSignupLogin');
    if (loginLink) loginLink.addEventListener('click', function(e) {
      e.preventDefault();
      navigateTo('dashboard');
      // Show the login form
      var loginForm = $('#dashLoginForm');
      var regForm = $('#dashRegForm');
      if (loginForm) loginForm.style.display = '';
      if (regForm) regForm.style.display = 'none';
    });
  }

  async function signupRegister() {
    var user = $('#signupUser').value.trim();
    var pass = $('#signupPass').value;
    var email = $('#signupEmail').value.trim();
    var referrerEmail = $('#signupReferrerEmail') ? $('#signupReferrerEmail').value.trim() : '';
    var wallet = $('#signupWallet').value.trim();
    var secQ = $('#signupSecurityQ') ? $('#signupSecurityQ').value : '';
    var secA = $('#signupSecurityA') ? $('#signupSecurityA').value.trim() : '';
    if (!user || !pass) { toast('Username and password required', 'error'); return; }
    if (!secQ || !secA) { toast('Please choose a security question and answer', 'error'); return; }
    var refCode = localStorage.getItem('xmrjackpot_ref') || '';
    try {
      var res = await fetch(API + '/auth/register', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({username: user, password: pass, email: email, wallet_address: wallet, referral_code: refCode, referrer_email: referrerEmail, security_question: secQ, security_answer: secA}),
      });
      var d = await res.json();
      if (d.status === 'ok') {
        localStorage.removeItem('xmrjackpot_ref');
        toast(d.is_admin ? 'Registered as ADMIN!' : 'Account created!', 'success');
        // Redirect to realplay (download) page after signup
        if (d.is_admin) {
          navigateTo('admin');
        } else {
          navigateTo('realplay');
        }
        dashCheckAuth();
      } else {
        toast(d.detail || 'Registration failed', 'error');
      }
    } catch(e) { toast('Registration error', 'error'); }
  }

  // ════════════════════════════════════════════════════════════
  //  WALLET CONNECT (inline — the modal was deleted in 609919f)
  // ════════════════════════════════════════════════════════════

  // The connect prompt no longer exists; the landing CTAs go straight to
  // the inline wallet field — the dashboard editor when signed in, the
  // signup form when not. Behaviour is what the old modal's connect
  // button did, minus the dialog that can never render.
  function landingWalletConnect() {
    fetch(API + '/auth/me').then(function(r) { return r.json(); }).then(function(d) {
      if (d.authenticated) {
        navigateTo('dashboard');
        setTimeout(function() {
          var w = $('#editWallet');
          if (w) { w.focus(); w.scrollIntoView({behavior:'smooth', block:'center'}); }
        }, 400);
      } else {
        navigateTo('signup');
        setTimeout(function() {
          var w = $('#signupWallet');
          if (w) { w.focus(); w.scrollIntoView({behavior:'smooth', block:'center'}); }
        }, 400);
      }
    }).catch(function() {
      navigateTo('signup');
    });
  }

  // ════════════════════════════════════════════════════════════
  //  WINNER CLAIM FLOW
  // ════════════════════════════════════════════════════════════

  var _winnerClaimData = null;

  async function checkWinnerStatus() {
    try {
      var res = await fetch(API + '/winner/status');
      if (!res.ok) return;
      var d = await res.json();
      var banner = $('#winnerClaimBanner');
      if (!banner) return;
      if (!d.has_pending_claim) {
        banner.style.display = 'none';
        banner.innerHTML = '';
        _winnerClaimData = null;
        return;
      }
      _winnerClaimData = d;
        renderWinnerBanner(d);
    } catch(e) { /* endpoint may be unavailable; fail silently */ }
  }

  function renderWinnerBanner(d) {
    var banner = $('#winnerClaimBanner');
    if (!banner) return;
    /* SHOW, DON'T HIDE (Oct 29, playtest stumble): this function only
       runs when /api/winner/status has a pending claim, so the container
       must become visible — the pre-fix line left the banner hidden on
       EVERY render (checkWinnerStatus hides it itself on has_pending_claim
       false), so winners never saw their claim and the claim-success
       innerHTML swap also rendered into the hidden container. */
    banner.style.display = '';

    /* THE COIN-AWARE BANNER (Oct 29): the winner's miner coin is the
       coin the prize pays in (a VRSC draw even names Verus), so the
       input hint and currency name read that coin — an XMR banner for
       a VRSC winner told them to add 'a Monero wallet' for a Verus
       payout, and its XMR-only placeholder refused their R-address. */
    var isVrsc = (d.winner_coin === 'vrsc');
    var coinName = isVrsc ? 'VRSC' : 'XMR';
    var daysLeft = Math.ceil((d.time_remaining_seconds || 0) / 86400);
    var payoutStr = fmt(d.payout_amount, 6);

    var walletInputHtml = '';
    if (!d.has_wallet) {
      walletInputHtml = '<div class="winner-claim-row">' +
        '<input class="spin-input" id="winnerWalletInput" placeholder="' + (isVrsc ? 'Your Verus wallet address (starts with R)' : 'Your XMR wallet address (starts with 4 or 8)') + '">' +
        '<button class="btn btn-gold" id="btnWinnerClaim">🏆 Claim Prize</button>' +
      '</div>';
    } else {
      walletInputHtml = '<div class="winner-claim-row">' +
        '<button class="btn btn-gold btn-lg" id="btnWinnerClaim">🏆 Claim Prize</button>' +
      '</div>';
    }

    banner.innerHTML =
      '<div class="winner-banner">' +
        '<div class="winner-banner-title">🎉 You\'re the winner! You won <b>' + payoutStr + ' ' + coinName + '</b>! ' +
          (d.has_wallet ? '' : 'Add a ' + (isVrsc ? 'Verus' : 'Monero') + ' wallet to receive your payout.') + '</div>' +
        '<div class="winner-banner-deadline">⏰ Claim deadline: ' + daysLeft + ' day' + (daysLeft === 1 ? '' : 's') + ' remaining</div>' +
        walletInputHtml +
      '</div>';

    var claimBtn = $('#btnWinnerClaim');
    if (claimBtn) claimBtn.addEventListener('click', function () {
        claimPrize();
      });
  }

  async function claimPrize() {
    if (!_winnerClaimData) { toast('No pending claim', 'error'); return; }
    var wallet = '';
    var input = $('#winnerWalletInput');
    if (input) {
      wallet = input.value.trim();
    } else if (_winnerClaimData.has_wallet) {
      // Use the wallet on file — fetch from profile
      wallet = _winnerClaimData.winner_wallet || '';
    }

    if (!wallet) {
      toast(_winnerClaimData.winner_coin === 'vrsc'
        ? 'Enter your Verus wallet address'
        : 'Enter your Monero wallet address', 'error');
      return;
    }

    // THE COIN-AWARE PRE-GATE (Oct 29): the old 4/8 + 90-120 client
    // check hard-coded XMR — a Verus miner's R-address would have been
    // refused by their own claim banner. The payout validator the
    // server's gates use now answers, coin derived from shape exactly
    // like the Connect Wallet gate. The server stays the authority:
    // its claim route re-validates with the drawing's miner coin.
    var claimCoin = (_winnerClaimData.winner_coin === 'vrsc') ? 'vrsc'
                  : ((wallet.length === 34 && wallet[0] === 'R') ? 'vrsc' : 'xmr');
    if (claimCoin === 'xmr') {
      if (!(wallet.startsWith('4') || wallet.startsWith('8'))) {
        toast('Invalid Monero address — must start with 4 or 8', 'error');
        return;
      }
      if (wallet.length < 90 || wallet.length > 120) {
        toast('Invalid Monero address length (must be 90-120 characters)', 'error');
        return;
      }
    }

    var claimBtn = $('#btnWinnerClaim');
    if (claimBtn) { claimBtn.disabled = true; claimBtn.textContent = '⏳ Sending payout...'; }

    try {
      var res = await fetch(API + '/winner/claim', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({wallet_address: wallet}),
      });
      var d = await res.json();
      if (res.ok && d.status === 'ok') {
        var payoutStr = fmt(d.payout_amount, 6);
        /* THE COIN THE SERVER ANSWERED (Oct 29, playtest stumble): the
           server's claim reply carries the drawing's claim coin (xmr or
           vrsc) — the success text used to hard-code XMR, telling a
           Verus winner their payout was sent in the wrong coin. */
        var claimCoin = (d.coin === 'vrsc') ? 'VRSC' : 'XMR';
        /* QUEUED, NOT TRANSACTED (Oct 29): the route queues a pending
           payout (manual-from-Kraken for XMR, treasury for VRSC) — the
           pre-fix wording said "has been sent" while the server marks
           the ref pending_*, which overclaims. */
        var queued = (d.tx_hash && String(d.tx_hash).indexOf('pending_') === 0);
        var banner = $('#winnerClaimBanner');
        if (banner) {
          banner.innerHTML =
            '<div class="winner-confirmed">' +
              '<div class="winner-confirmed-title">✅ Your payout of ' + payoutStr + ' ' + claimCoin +
                (queued ? ' has been queued for payout!' : ' has been sent!') + '</div>' +
              '<div class="winner-confirmed-tx">Payout ref: ' + (d.tx_hash || '—') + '</div>' +
            '</div>';
        }
        launchConfetti();
        toast(queued ? 'Payout queued! ' + payoutStr + ' ' + claimCoin 
                     : 'Payout sent! ' + payoutStr + ' ' + claimCoin, 'success');
        _winnerClaimData = null;
      } else {
        toast(d.detail || 'Claim failed', 'error');
        if (claimBtn) { claimBtn.disabled = false; claimBtn.textContent = '🏆 Claim Prize'; }
      }
    } catch(e) {
      toast('Claim error — please try again', 'error');
      if (claimBtn) { claimBtn.disabled = false; claimBtn.textContent = '🏆 Claim Prize'; }
    }
  }

  // ════════════════════════════════════════════════════════════
  //  REAL MONEY MODE
  // ════════════════════════════════════════════════════════════

  function setText(sel, val) {
    const el = $(sel);
    if (el) el.textContent = val;
  }

  function getWallet() {
    return lsGet(LS.wallet, '') || '';
  }

  function setWallet(addr) {
    lsSet(LS.wallet, addr);
  }

  // ─── Miner Upgrades ─────────────────────────────────────────
  function boostMinerSpeed() {
    if (speedLevel >= 5) {
      toast('Max speed reached! (5x)', 'error');
      return;
    }
    // Cost scales with level
    var cost = 0.5 * speedLevel;
    speedLevel++;
    localStorage.setItem('xmrjackpot_speed_level', String(speedLevel));
    updateMineSceneUpgrades();
    toast('⚡ Speed boosted to ' + speedLevel + 'x! (Cost: ' + cost + ' XMR)', 'success');
  }

  function hireMiner2() {
    if (miner2Hired) {
      toast('Miner 2 already hired!', 'error');
      return;
    }
    miner2Hired = true;
    localStorage.setItem('xmrjackpot_miner2', '1');
    updateMineSceneUpgrades();
    toast('Miner 2 hired! Double the mining power!', 'success');
  }

  function updateMineSceneUpgrades() {
    var scene = $('#mineScene');
    if (!scene) return;

    // Remove old speed classes
    scene.classList.remove('speed-2', 'speed-3', 'speed-4', 'speed-5');
    if (speedLevel >= 2) scene.classList.add('speed-' + speedLevel);

    // Show/hide miner 2
    var miner2 = $('#realMinerSprite2');
    if (miner2) miner2.style.display = miner2Hired ? '' : 'none';

    // Update labels
    var sl = $('#speedLevel');
    var mc = $('#minerCount');
    if (sl) sl.textContent = speedLevel + 'x';
    if (mc) mc.textContent = miner2Hired ? '2' : '1';

    // Update boost button
    var boostBtn = $('#btnBoostSpeed');
    if (boostBtn) {
      if (speedLevel >= 5) { boostBtn.textContent = 'MAX SPEED'; boostBtn.disabled = true; }
      else { boostBtn.textContent = 'Boost Speed (' + (0.5*speedLevel) + ' XMR)'; boostBtn.disabled = false; }
    }
    var hireBtn = $('#btnHireMiner2');
    if (hireBtn && miner2Hired) { hireBtn.textContent = 'Miner 2 Active'; hireBtn.disabled = true; }
  }

  function initRealPlay() {
    // Stop any existing polls
    stopPolling();

    // Wallet connect. TWO buttons carry the same job (Oct 28 playtest):
    // the landing hero's #btnConnectWallet AND the real-play page's own
    // #btnConnectWalletRealPlay — the latter shipped with no listener,
    // so a user on "Play for XMR" could paste a wallet and click to
    // nothing at all. One handler, both ids.
    const walletInput = $('#realWalletInput');
    const connectBtns = [$('#btnConnectWallet'), $('#btnConnectWalletRealPlay')].filter(Boolean);

    if (walletInput) {
      const saved = getWallet();
      if (saved) walletInput.value = saved;
    }

    if (connectBtns.length) {
      // THE SERVER-SIDE GATE (Oct 29, playtest stumble): a length check
      // alone accepted any 20+-character string ('this-is-not-a-'
      // 'address-but-long-enough' got 'Wallet connected!'). Money was
      // never misdirected — signup/profile/claim re-validate offline —
      // but the stored and DISPLAYED wallet could carry junk. The same
      // mainnet validator the other gates use now answers this button:
      // one /api/wallet/validate call before connecting. The SHAPE
      // derives the GATE choice only: an R+33-Base58 string rides the
      // Verus gate, anything else the Monero mainnet gate. The shape is
      // deliberately never stamped into miners.coin — that column is
      // what the drawing payout branch reads to decide the coin a win
      // pays in, and its one writer is the Verus share pipeline
      // (vrsc_relay.record_accepted_share): a pasted address is not
      // share truth and must never decide a payout coin. Network-down:
      // SERVER_ERR, not a fallback — degrade to 'cannot verify now',
      // never to 'looks fine'.
      const connectHandler = async function () {
        const addr = (walletInput.value || '').trim();
        if (!addr || addr.length < 20) {
          toast('Enter a valid wallet address', 'error');
          return;
        }
        var coin = (addr.length === 34 && addr[0] === 'R') ? 'vrsc' : 'xmr';
        try {
          var vres = await fetch(API + '/wallet/validate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ address: addr, coin: coin })
          });
          if (vres.status === 429) {
            toast('Too many attempts — wait a moment and try again', 'error');
            return;
          }
          var vinfo = await vres.json();
        } catch (e) {
          toast('Cannot verify the wallet right now — try again', 'error');
          return;
        }
        if (!vres.ok || !vinfo.valid) {
          var why = (vinfo && vinfo.reason) ? vinfo.reason : 'address rejected';
          toast(coin === 'vrsc'
            ? ('Invalid Verus address — ' + why)
            : ('Invalid XMR address — ' + why), 'error');
          return;
        }
        setWallet(addr);
        toast('Wallet connected!', 'success');
        showRealPlayMain();
        startRealPlayPolling();
      };
      connectBtns.forEach(function (btn) { btn.addEventListener('click', connectHandler); });
    }

    // Copy referral code button (PROGRAM B — the internal programme)
    // The label says COPY MY CODE, so the clipboard gets the code (?ref=
    // takes either); the full link stays visible in the input beside it.
    var copyBtn = $('#btnCopyReferral');
    if (copyBtn) copyBtn.addEventListener('click', function() {
      var codeEl = $('#referralCodeText');
      var code = codeEl ? (codeEl.textContent || '').trim() : '';
      if (!code || code === '…') {
        toast('Sign in to get your referral code', 'error');
        return;
      }
      copyText(code, 'Referral code copied!');
    });

    // PROGRAM A — the operator's one Kraken invite link. It is never
    // rendered as text: the value lives in a hidden input and the copy
    // button (plus the share buttons below) is the only way it leaves
    // the page.
    var krakenCopy = $('#btnCopyKrakenLink');
    if (krakenCopy) krakenCopy.addEventListener('click', function() {
      var hidden = $('#krakenInviteLink');
      var url = hidden ? (hidden.value || '').trim() : '';
      if (!url) {
        toast('Invite link still loading — try again in a moment', 'error');
        return;
      }
      copyText(url, 'Kraken invite link copied!');
    });

    // Share intents: built at CLICK TIME from the hidden link, so no raw
    // URL is ever in the page text and nothing is shared until asked.
    document.addEventListener('click', function (ev) {
      var btn = ev.target && ev.target.closest
        ? ev.target.closest('[data-share]') : null;
      if (!btn) return;
      var hidden = $('#krakenInviteLink');
      var url = hidden ? (hidden.value || '').trim() : '';
      if (!url) {
        toast('Invite link still loading — try again in a moment', 'error');
        return;
      }
      var where = btn.getAttribute('data-share');
      var text = 'Mining XMR in the community pool — join Kraken through our link ' +
        '(mystery reward for both sides, on Kraken terms):';
      var target = '';
      if (where === 'x') {
        target = 'https://twitter.com/intent/tweet?text=' + encodeURIComponent(text) +
          '&url=' + encodeURIComponent(url);
      } else if (where === 'telegram') {
        target = 'https://t.me/share/url?url=' + encodeURIComponent(url) +
          '&text=' + encodeURIComponent(text);
      } else if (where === 'whatsapp') {
        target = 'https://wa.me/?text=' + encodeURIComponent(text + ' ' + url);
      } else if (where === 'facebook') {
        target = 'https://www.facebook.com/sharer/sharer.php?u=' + encodeURIComponent(url);
      } else if (where === 'email') {
        target = 'mailto:?subject=' + encodeURIComponent('Mine XMR with me') +
          '&body=' + encodeURIComponent(text + '\n\n' + url);
      }
      if (target) window.open(target, '_blank', 'noopener');
    });

    // Get subaddress button
    var subaddrBtn = $('#btnGetSubaddress');
    if (subaddrBtn) subaddrBtn.addEventListener('click', async function() {
      try {
        var res = await fetch(API + '/payment/subaddress');
        var d = await res.json();
        if (d.subaddress) {
          var el = $('#realSubaddress');
          if (el) el.textContent = d.subaddress;
          subaddrBtn.style.display = 'none';
          toast('Subaddress assigned! ' + d.confirmations_required + ' confirmations required', 'success');
        } else {
          toast(d.detail || 'Could not generate subaddress', 'error');
        }
      } catch(e) { toast('Error: ' + e.message, 'error'); }
    });

    // Miner upgrades
    const boostBtn = $('#btnBoostSpeed');
    const hireBtn = $('#btnHireMiner2');
    if (boostBtn) boostBtn.addEventListener('click', boostMinerSpeed);
    if (hireBtn) hireBtn.addEventListener('click', hireMiner2);

    // Restore saved upgrade state
    updateMineSceneUpgrades();

    // Fetch subaddress
    fetchSubaddress();

    // Miner installer button — shows confirmation dialog before download
    var dlInstaller = $('#btnDownloadInstaller');
    if (dlInstaller) dlInstaller.addEventListener('click', function() {
      var wallet = getWallet();
      if (!wallet) { toast('Connect your wallet first', 'error'); return; }

      window.open(API + '/miner-installer/' + encodeURIComponent(wallet), '_blank');
      return;

      // Show confirmation modal explaining what the installer does
      var modal = document.createElement('div');
      modal.style.cssText = 'position:fixed;inset:0;z-index:9998;background:rgba(0,0,0,0.85);display:flex;align-items:center;justify-content:center;';
      modal.innerHTML = `
        <div style="background:var(--bg-deep);border:3px solid var(--gold);border-radius:12px;padding:24px;max-width:520px;margin:16px;">
          <h3 style="color:var(--gold);margin-bottom:12px;">📋 Before You Download</h3>
          <div style="font-size:0.85em;color:var(--text-dim);line-height:1.6;margin-bottom:16px;">
            <p style="margin-bottom:8px;"><b>What this installer does:</b></p>
            <p style="margin-bottom:8px;">• Downloads <b>xmrig</b> (open-source Monero miner) from the official GitHub<br>
            • Verifies SHA256 hash to ensure integrity<br>
            • Creates a folder on your Desktop with config + start script<br>
            • Starts mining to the XMR Jackpot pool</p>
            <p style="margin-bottom:8px;color:var(--gold);">⚠️ <b>Your antivirus may flag this as "riskware."</b></p>
            <p style="margin-bottom:8px;">This is a <b>false positive</b>. xmrig is legitimate open-source software (MIT license, <a href="https://github.com/xmrig/xmrig" target="_blank" style="color:#7c7cff;">github.com/xmrig/xmrig</a>). Antivirus programs flag any mining software because it can be abused by malware — but this installer downloads from the official source and verifies the hash.</p>
            <p style="margin-bottom:8px;">If Windows Defender blocks it: Settings → Security → Exclusions → Add the "XMR Jackpot Miner" folder.</p>
            <p style="color:var(--text-mute);font-size:0.9em;">The installer uses 75% CPU at low priority. Close the window to stop mining. No background services, no auto-start.</p>
          </div>
          <div style="display:flex;gap:12px;justify-content:center;">
            <button class="btn btn-gold" id="confirmDownload">✅ I understand, download it</button>
            <button class="btn btn-outline" id="cancelDownload">Cancel</button>
          </div>
        </div>
      `;
      document.body.appendChild(modal);

      document.getElementById('confirmDownload').addEventListener('click', function() {
        modal.remove();
        window.open(API + '/miner-installer/' + encodeURIComponent(wallet), '_blank');
        toast('Downloading... Save it and double-click to run!', 'success');
      });
      document.getElementById('cancelDownload').addEventListener('click', function() {
        modal.remove();
      });
    });

    // If wallet already saved, go straight to main view
    if (getWallet()) {
      showRealPlayMain();
      startRealPlayPolling();
    } else {
      showWalletConnect();
    }
  }

  async function fetchSubaddress() {
    try {
      var res = await fetch(API + '/payment/subaddress');
      if (res.status === 401) return;  // not logged in
      var d = await res.json();
      if (d.subaddress) {
        var el = $('#realSubaddress');
        if (el) el.textContent = d.subaddress;
        var btn = $('#btnGetSubaddress');
        if (btn) btn.style.display = 'none';
      }
    } catch(e) {}
  }

  // One clipboard path for both programmes: the async API where it
  // exists (a click handler is a user gesture, so it is allowed), with a
  // hidden-textarea fallback for older/insecure contexts.
  function copyText(value, okMessage) {
    function fallback() {
      try {
        var ta = document.createElement('textarea');
        ta.value = value;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        toast(okMessage, 'success');
      } catch (e) {
        toast('Copy failed — select the value and copy manually', 'error');
      }
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).then(function () {
        toast(okMessage, 'success');
      }).catch(fallback);
    } else {
      fallback();
    }
  }

  async function fetchReferralStats() {
    try {
      var res = await fetch(API + '/referral/stats');
      if (!res.ok) return;
      var d = await res.json();
      var linkEl = $('#referralLink');
      if (linkEl && d.referral_link) linkEl.value = d.referral_link;
      var rc = $('#referralCount');
      if (rc) rc.textContent = d.referral_count || 0;
      var ar = $('#activeReferrals');
      if (ar) ar.textContent = d.active_referrals || 0;
      var rt = $('#referralTickets');
      if (rt) rt.textContent = d.tickets_earned || 0;
      var cl = $('#refCycleLine');
      if (cl && typeof d.cycle_cap === 'number') {
        cl.textContent = (d.awards_this_cycle || 0) + ' of ' + d.cycle_cap +
          ' referral rewards used this cycle' +
          (d.entries_held ? ' · ' + d.entries_held +
            ' held until a wallet is connected' : '');
      }
      // PROGRAM B: the code itself, the XMRJ rate and the accrual.
      var codeEl = $('#referralCodeText');
      if (codeEl && d.referral_code) codeEl.textContent = d.referral_code;
      var rateEl = $('#xmrjPerReferral');
      if (rateEl) {
        rateEl.textContent = d.xmrj_per_referral
          ? (d.xmrj_per_referral + ' XMRJ') : 'not set';
      }
      var accruedEl = $('#xmrjAwarded');
      if (accruedEl) accruedEl.textContent = d.xmrj_awarded || 0;
      var walletEl = $('#solanaWalletLine');
      if (walletEl) {
        if (d.solana_wallet) {
          var w = String(d.solana_wallet);
          walletEl.textContent = 'Paid to your connected Solana wallet ' +
            w.slice(0, 4) + '…' + w.slice(-4) +
            ' — payable when the XMRJ treasury is deployed.';
        } else {
          walletEl.textContent = 'No Solana wallet connected yet — XMRJ awards ' +
            'accrue on your account until you connect one.';
        }
      }
      // PROGRAM A: the operator's invite link + its derived code.
      var hiddenLink = $('#krakenInviteLink');
      if (hiddenLink && d.kraken_invite_link) hiddenLink.value = d.kraken_invite_link;
      var codeBadge = $('#krakenInviteCode');
      if (codeBadge && d.kraken_invite_code) codeBadge.textContent = d.kraken_invite_code;
    } catch(e) {}
  }

  function showWalletConnect() {
    const connect = $('#realWalletSection');
    const main = $('#realMainSection');
    if (connect) connect.style.display = '';
    if (main) main.style.display = 'none';
  }

  function showRealPlayMain() {
    const connect = $('#realWalletSection');
    const main = $('#realMainSection');
    if (connect) connect.style.display = 'none';
    if (main) main.style.display = '';

    // Display wallet
    var wallet = getWallet();
    setText('#realWalletDisplay', shortAddr(wallet));

    // Set wizard command with real wallet
    setText('#wizardCommand', 'xmrig -o mine.xmrjackpot.com:3333 -u ' + wallet + '.worker -p x');
  }

  function startRealPlayPolling() {
    stopPolling();
    fetchRealStats();
    fetchRealMiner();
    fetchRealDrawings();
    fetchRealEntryStats();

    pollingTimers.push(setInterval(fetchRealStats, POLL_STATS_MS));
    pollingTimers.push(setInterval(fetchRealMiner, POLL_STATS_MS));
    pollingTimers.push(setInterval(fetchRealDrawings, POLL_LEADERBOARD_MS));
    pollingTimers.push(setInterval(fetchRealEntryStats, POLL_STATS_MS));
  }

  function stopPolling() {
    pollingTimers.forEach(function (t) { clearInterval(t); });
    pollingTimers = [];
  }

  async function fetchRealStats() {
    try {
      const res = await fetch(API + '/stats');
      const d = await res.json();

      setText('#realJackpotXmr', fmt(d.jackpot_xmr, 6) + ' XMR');
      setText('#realJackpotUsd', '≈ $' + fmt(d.jackpot_usd, 2));
      paintRateNote(document.querySelector('#realJackpotUsd'),
                    d.xmr_usd_rate_info, 'realRateNote');
      setText('#realActiveMiners', d.active_miners);
      setText('#realPoolHashrate', fmtHash(d.total_hashrate));
      setText('#realPendingTickets', d.pending_tickets);
      setText('#realTotalShares', d.total_shares.toLocaleString());
    } catch (e) {
      console.error('Stats fetch error:', e);
    }
  }

  async function fetchRealEntryStats() {
    try {
      const res = await fetch(API + '/profile/stats');
      if (!res.ok) return;
      const d = await res.json();
      setText('#realYourEntries', (d.your_entries || 0).toLocaleString());
      setText('#realTotalEntries', (d.total_entries || 0).toLocaleString());
      setText('#realWinChance', fmt(d.win_chance || 0, 2) + '%');
    } catch (e) {
      /* silent — not logged in */
    }
  }

  async function fetchRealMiner() {
    const wallet = getWallet();
    if (!wallet) return;

    try {
      const res = await fetch(API + '/miner/' + encodeURIComponent(wallet));
      const d = await res.json();

      const miner = d.miner || {};
      const tickets = d.tickets || 0;

      setText('#minerHashrate', fmtHash(miner.hashrate));
      setText('#minerShares', (miner.shares_valid || 0).toLocaleString());
      setText('#minerTickets', tickets);
      // THE UNIT COUNTER, ALWAYS SHOWN where mining status shows
      // (Oct 12 controller): partial progress lives in shares_valid
      // (the DB row), so a disconnect keeps the current ticket's units;
      // the server mints ONLY complete tickets (floor division at
      // ingest) and the remainder resets to 0 the moment the 100th
      // unit lands — never a partial entry, never a lost ticket.
      setText('#raffleProgress', raffleLine(miner.shares_valid, tickets));

      // Show miner section
      const section = $('#realMinerSection');
      if (section) section.style.display = '';

      const noMiner = $('#realNoMiner');
      if (noMiner) {
        noMiner.style.display = (miner.shares_valid > 0 || miner.hashrate > 0) ? 'none' : '';
      }

      // Update miner display
      var isMining = miner.hashrate > 0 || (miner.last_seen && (Date.now() / 1000 - miner.last_seen) < 600);
      var scene = $('#mineScene');
      var statusText = $('#mineStatusText');
      if (scene) {
        if (isMining) {
          scene.classList.add('working');
          if (statusText) { statusText.textContent = '⛏️ MINER WORKING'; statusText.classList.add('active'); }
        } else {
          scene.classList.remove('working');
          if (statusText) { statusText.textContent = '⚠️ MINER NOT DEPLOYED'; statusText.classList.remove('active'); }
        }
      }
    } catch (e) {
      console.error('Miner fetch error:', e);
    }
  }

  async function fetchRealDrawings() {
    try {
      const res = await fetch(API + '/drawings');
      const d = await res.json();
      renderRealDrawings(d);
    } catch (e) {
      console.error('Drawings fetch error:', e);
    }
  }

  function renderRealDrawings(drawings) {
    const tb = $('#realDrawingsTable');
    if (!tb) return;

    if (!drawings || !drawings.length) {
      tb.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-mute)">🚀 Beta — first drawing coming soon!</td></tr>';
      return;
    }

    tb.innerHTML = drawings.map(function (r) {
      var winner = r.winner_wallet ? shortAddr(r.winner_wallet) : '—';
      var winnerClass = r.winner_id ? 'win-badge won' : 'win-badge pending';
      var winnerText = r.winner_id ? 'WON' : (r.status === 'cancelled' ? 'CANCELLED' : 'PENDING');
      var verifyLink = r.status === 'drawn'
        ? ' <a href=\"/api/verify/' + r.id + '\" target=\"_blank\" style=\"color:var(--diamond-bright);text-decoration:none;\" title=\"Provably Fair: Verify\">🔍</a>'
        : '';
      return '<tr>' +
        '<td>#' + r.id + verifyLink + '</td>' +
        '<td>' + fmt(r.jackpot_amount, 6) + ' XMR</td>' +
        '<td><span class=\"' + winnerClass + '\">' + winnerText + '</span></td>' +
        '<td>' + (r.winner_wallet ? winner : '—') + '</td>' +
        '<td>' + fmt(r.house_tax, 6) + ' XMR</td>' +
        '<td>' + (r.total_tickets || 0) + '</td>' +
        '</tr>';
    }).join('');
  }

  // ════════════════════════════════════════════════════════════
  //  LEADERBOARD
  // ════════════════════════════════════════════════════════════

  // ════════════════════════════════════════════════════════════
  //  PLAYER DASHBOARD
  // ════════════════════════════════════════════════════════════

  function initDashboard() {
    stopPolling();

    // Login/Register toggle
    var showReg = $('#btnDashShowReg');
    var showLogin = $('#btnDashShowLogin');
    if (showReg) showReg.addEventListener('click', function(e) { e.preventDefault(); $('#dashLoginForm').style.display='none'; $('#dashRegForm').style.display=''; $('#dashResetForm').style.display='none'; });
    if (showLogin) showLogin.addEventListener('click', function(e) { e.preventDefault(); $('#dashRegForm').style.display='none'; $('#dashLoginForm').style.display=''; $('#dashResetForm').style.display='none'; });

    // Forgot password → show reset form
    var forgotBtn = $('#btnDashForgotPass');
    if (forgotBtn) forgotBtn.addEventListener('click', function(e) {
      e.preventDefault();
      $('#dashLoginForm').style.display = 'none';
      $('#dashRegForm').style.display = 'none';
      $('#dashResetForm').style.display = '';
      showResetStep(1);
    });

    // Back to login from reset
    var backLogin = $('#btnResetBackToLogin');
    if (backLogin) backLogin.addEventListener('click', function(e) {
      e.preventDefault();
      $('#dashResetForm').style.display = 'none';
      $('#dashLoginForm').style.display = '';
      // Clear reset fields
      var rn = $('#resetUsername'); if (rn) rn.value = '';
      var ra = $('#resetAnswer'); if (ra) ra.value = '';
      var rp = $('#resetNewPass'); if (rp) rp.value = '';
    });

    // Reset step 1 → get security question
    var getQBtn = $('#btnResetGetQuestion');
    if (getQBtn) getQBtn.addEventListener('click', resetGetQuestion);

    // Reset step 2 → verify answer
    var verifyBtn = $('#btnResetVerify');
    if (verifyBtn) verifyBtn.addEventListener('click', resetVerifyAnswer);

    // Reset step 3 → set new password
    var resetPassBtn = $('#btnResetPassword');
    if (resetPassBtn) resetPassBtn.addEventListener('click', resetPassword);

    // Login button
    var loginBtn = $('#btnDashLogin');
    if (loginBtn) loginBtn.addEventListener('click', dashLogin);

    // Register button
    var regBtn = $('#btnDashReg');
    if (regBtn) regBtn.addEventListener('click', dashRegister);

    // Logout
    var logoutBtn = $('#btnDashLogout');
    if (logoutBtn) logoutBtn.addEventListener('click', dashLogout);

    // Save profile
    var saveBtn = $('#btnSaveProfile');
    if (saveBtn) saveBtn.addEventListener('click', dashSaveProfile);

    // Check if already logged in
    dashCheckAuth();
  }

  function showResetStep(step) {
    var s1 = $('#resetStep1'), s2 = $('#resetStep2'), s3 = $('#resetStep3');
    if (s1) s1.style.display = step === 1 ? '' : 'none';
    if (s2) s2.style.display = step === 2 ? '' : 'none';
    if (s3) s3.style.display = step === 3 ? '' : 'none';
  }

  async function resetGetQuestion() {
    var username = $('#resetUsername').value.trim();
    if (!username) { toast('Enter your username', 'error'); return; }
    var btn = $('#btnResetGetQuestion');
    if (btn) { btn.disabled = true; btn.textContent = 'Checking...'; }
    try {
      var res = await fetch(API + '/auth/security-question', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({username: username}),
      });
      var d = await res.json();
      if (res.ok && d.security_question) {
        var qEl = $('#resetQuestionText');
        if (qEl) qEl.textContent = '🔒 ' + d.security_question;
        showResetStep(2);
      } else {
        toast(d.detail || 'User not found', 'error');
      }
    } catch(e) {
      toast('Error retrieving security question', 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Get Security Question'; }
    }
  }

  async function resetVerifyAnswer() {
    var username = $('#resetUsername').value.trim();
    var answer = $('#resetAnswer').value.trim();
    if (!answer) { toast('Enter your answer', 'error'); return; }
    var btn = $('#btnResetVerify');
    if (btn) { btn.disabled = true; btn.textContent = 'Verifying...'; }
    // We don't call the API yet — just move to step 3.
    // The actual verification happens on resetPassword call.
    if (btn) { btn.disabled = false; btn.textContent = 'Verify'; }
    showResetStep(3);
  }

  async function resetPassword() {
    var username = $('#resetUsername').value.trim();
    var answer = $('#resetAnswer').value.trim();
    var newPass = $('#resetNewPass').value;
    if (!newPass) { toast('Enter a new password', 'error'); return; }
    if (newPass.length < 4) { toast('Password too short (min 4 chars)', 'error'); return; }
    var btn = $('#btnResetPassword');
    if (btn) { btn.disabled = true; btn.textContent = 'Resetting...'; }
    try {
      var res = await fetch(API + '/auth/reset-password', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({username: username, security_answer: answer, new_password: newPass}),
      });
      var d = await res.json();
      if (res.ok && d.status === 'ok') {
        toast('Password reset! You can now log in.', 'success');
        $('#dashResetForm').style.display = 'none';
        $('#dashLoginForm').style.display = '';
        // Clear fields
        var rn = $('#resetUsername'); if (rn) rn.value = '';
        var ra = $('#resetAnswer'); if (ra) ra.value = '';
        var rp = $('#resetNewPass'); if (rp) rp.value = '';
      } else {
        toast(d.detail || 'Password reset failed', 'error');
      }
    } catch(e) {
      toast('Error resetting password', 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Reset Password'; }
    }
  }

  async function dashCheckAuth() {
    try {
      var res = await fetch(API + '/auth/me');
      var d = await res.json();
      if (d.authenticated) {
        showDashMain(d);
        await fetchProfileStats();
        // Check for pending jackpot win → show claim banner
        checkWinnerStatus();
        // If they came from the one-click miner button, redirect to real play
        if (window._oneClickFlow) {
          window._oneClickFlow = false;
          navigateTo('realplay');
        }
      }
    } catch(e) {}
  }

  async function dashLogin() {
    var user = $('#dashLoginUser').value.trim();
    var pass = $('#dashLoginPass').value;
    if (!user || !pass) { toast('Enter username and password', 'error'); return; }
    try {
      var res = await fetch(API + '/auth/login', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({username: user, password: pass}),
      });
      var d = await res.json();
      if (d.status === 'ok') {
        toast('Welcome back, ' + d.username + '!', 'success');
        if (d.is_admin) {
          navigateTo('admin');
        } else {
          navigateTo('dashboard');
        }
        dashCheckAuth();
      } else {
        toast(d.detail || 'Login failed', 'error');
      }
    } catch(e) { toast('Login error', 'error'); }
  }

  async function dashRegister() {
    var user = $('#dashRegUser').value.trim();
    var pass = $('#dashRegPass').value;
    var email = $('#dashRegEmail').value.trim();
    var referrerEl = $('#dashRegReferrerEmail');
    var referrerEmail = referrerEl ? referrerEl.value.trim() : '';
    var wallet = $('#dashRegWallet').value.trim();
    var phoneEl = $('#dashRegPhone');
    var nameEl = $('#dashRegName');
    var phone = phoneEl ? phoneEl.value.trim() : '';
    var fullName = nameEl ? nameEl.value.trim() : '';
    if (!user || !pass) { toast('Username and password required', 'error'); return; }
    var refCode = localStorage.getItem('xmrjackpot_ref') || '';
    try {
      var res = await fetch(API + '/auth/register', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({username: user, password: pass, email: email, wallet_address: wallet, referral_code: refCode, referrer_email: referrerEmail, phone: phone, full_name: fullName}),
      });
      var d = await res.json();
      if (d.status === 'ok') {
        localStorage.removeItem('xmrjackpot_ref');
        toast(d.is_admin ? 'Registered as ADMIN!' : 'Account created! +3 free entries signup bonus!', 'success');
        if (d.is_admin) {
          navigateTo('admin');
        } else {
          navigateTo('dashboard');
        }
        dashCheckAuth();
      } else {
        toast(d.detail || 'Registration failed', 'error');
      }
    } catch(e) { toast('Registration error', 'error'); }
  }

  async function dashLogout() {
    try {
      await fetch(API + '/auth/logout', {method:'POST'});
      $('#dashMain').style.display = 'none';
      $('#dashLogin').style.display = '';
      $('#navAdmin').style.display = 'none';
      toast('Logged out', 'success');
      // auth-state changed (logout): per-user surfaces must drop the
      // session's state the moment it ends — prefs ETag, undo history,
      // and the Scout panel's stamp all hang off this event
      document.dispatchEvent(new CustomEvent('xmrjackpot:auth'));
    } catch(e) {}
  }

  function showDashMain(user) {
    $('#dashLogin').style.display = 'none';
    $('#dashMain').style.display = '';
    if (user.is_admin) {
      var navAdmin = $('#navAdmin');
      if (navAdmin) navAdmin.style.display = '';
    }
    // Pre-fill profile editor
    if (user.display_name) $('#editDisplayName').value = user.display_name;
    if (user.avatar) $('#editAvatar').value = user.avatar;
    if (user.wallet_address) $('#editWallet').value = user.wallet_address;

    // auth-state changed (login/register): push clients re-read their
    // per-user state (prefs ETag belongs to the session's user)
    document.dispatchEvent(new CustomEvent('xmrjackpot:auth'));

    // Check if user's miner is active — show notification if not
    checkMinerStatus();
  }

  async function checkMinerStatus() {
    try {
      var res = await fetch(API + '/profile/stats');
      var d = await res.json();
      var miner = d.miner || {};
      var hashrate = miner.hashrate || 0;
      var shares = miner.shares_valid || 0;
      var lastSeen = miner.last_seen || 0;
      var now = Date.now() / 1000;
      var isStale = lastSeen && (now - lastSeen > 900); // 15 min

      var existing = document.getElementById('minerStatusBanner');
      if (existing) existing.remove();

      if (!hashrate && !shares) {
        // No miner at all
        var banner = document.createElement('div');
        banner.id = 'minerStatusBanner';
        banner.className = 'winner-banner';
        banner.style.cssText = 'background:rgba(255,150,0,0.1);border-color:rgba(255,150,0,0.3);margin-bottom:16px;';
        banner.innerHTML = '<span style="font-size:1.5em;">⚠️</span><div><b>Your miner is not running!</b> You need to run the XMR Jackpot miner to earn entries and win the daily grand prize. <a href="#" data-action="navigate-realplay" style="color:var(--gold);">Download &amp; start mining →</a></div>';
        var dashMain = document.getElementById('dashMain');
        if (dashMain) dashMain.insertBefore(banner, dashMain.firstChild);
      } else if (isStale) {
        // Miner was running but went stale
        var banner2 = document.createElement('div');
        banner2.id = 'minerStatusBanner';
        banner2.className = 'winner-banner';
        banner2.style.cssText = 'background:rgba(255,150,0,0.1);border-color:rgba(255,150,0,0.3);margin-bottom:16px;';
        banner2.innerHTML = '<span style="font-size:1.5em;">💤</span><div><b>Your miner appears to be offline.</b> Restart your XMR Jackpot miner to keep earning entries for the daily grand prize!</div>';
        var dashMain2 = document.getElementById('dashMain');
        if (dashMain2) dashMain2.insertBefore(banner2, dashMain2.firstChild);
      }
    } catch(e) {}
  }

  async function fetchProfileStats() {
    try {
      var res = await fetch(API + '/profile/stats');
      var d = await res.json();
      var u = d.user || {};

      $('#dashAvatar').textContent = u.avatar || '⛏️';
      $('#dashDisplayName').textContent = u.display_name || u.username;
      $('#dashUsername').textContent = '@' + u.username;
      $('#dashTotalEarned').textContent = fmt(u.total_earned || 0, 6);
      $('#dashTotalShares').textContent = (u.total_shares || (d.miner && d.miner.shares_valid) || 0).toLocaleString();
      $('#dashTotalJackpots').textContent = u.total_jackpots || 0;
      $('#dashTickets').textContent = d.tickets || 0;

      // Entry stats tiles
      var dashYourEntries = $('#dashYourEntries');
      if (dashYourEntries) dashYourEntries.textContent = (d.your_entries || 0).toLocaleString();
      var dashTotalEntries = $('#dashTotalEntries');
      if (dashTotalEntries) dashTotalEntries.textContent = (d.total_entries || 0).toLocaleString();
      var dashWinChance = $('#dashWinChance');
      if (dashWinChance) dashWinChance.textContent = fmt(d.win_chance || 0, 2) + '%';

      // THE CONTRIBUTION THANKS (Sep 26): once Kraken has PAID the
      // member's Referral bonus, their dashboard greets them. The
      // server decides from the member's own latest LIVE Paid
      // snapshot (never the simulation); the banner lands at the top
      // of the dashboard like the winner banner and disappears on a
      // later no-snapshot read (idempotent: remove before insert).
      var existingThanks = document.getElementById('krakenThanksBanner');
      if (existingThanks) existingThanks.remove();
      var thanks = d.kraken_thanks;
      if (thanks && typeof thanks.amount === 'number' && thanks.amount > 0) {
        var tBanner = document.createElement('div');
        tBanner.id = 'krakenThanksBanner';
        tBanner.className = 'winner-banner';
        tBanner.style.cssText = 'background:rgba(16,185,129,0.12);border-color:rgba(16,185,129,0.35);margin-bottom:16px;';
        tBanner.innerHTML = '<span style="font-size:1.5em;">🎉</span><div><b>Thanks, you\'ve contributed to the jackpot $' +
          thanks.amount.toFixed(2) + '</b></div>';
        var dashMainThanks = document.getElementById('dashMain');
        if (dashMainThanks) dashMainThanks.insertBefore(tBanner, dashMainThanks.firstChild);
      }

      // Show house tax stats only for admin
      if (u.is_admin) {
        var taxCard = $('#dashHouseTaxCard');
        var sentCard = $('#dashHouseTaxSentCard');
        if (taxCard) taxCard.style.display = '';
        if (sentCard) sentCard.style.display = '';

        // Fetch house tax data
        try {
          var taxRes = await fetch(API + '/admin/stats');
          if (taxRes.ok) {
            var taxData = await taxRes.json();
            var dashTax = $('#dashHouseTax');
            var dashSent = $('#dashHouseTaxSent');
            if (dashTax) dashTax.textContent = fmt(taxData.total_house_tax || 0, 6) + ' XMR';
            if (dashSent) dashSent.textContent = fmt(taxData.total_paid_out || 0, 6) + ' XMR';
          }
        } catch(e) {}
      }
      $('#dashWallet').textContent = u.wallet_address ? shortAddr(u.wallet_address) : '—';
      $('#dashJoined').textContent = u.created_at ? new Date(u.created_at * 1000).toLocaleDateString() : '—';
      // the last-login stamp joins the shared clock: relative text,
      // exact UTC moment on hover (computed values only — no user input)
      $('#dashLastLogin').innerHTML = u.last_login
        ? '<span title="' + isoOf(u.last_login) + '">' + timeAgo(u.last_login) + '</span>'
        : '—';

      var miner = d.miner || {};
      $('#dashMinerHashrate').textContent = fmtHash(miner.hashrate);
      $('#dashMinerShares').textContent = (miner.shares_valid || 0).toLocaleString();
      $('#dashMinerTickets').textContent = d.tickets || 0;
      // the unit counter rides the dashboard's mining stats too
      setText('#dashRaffleProgress', raffleLine(miner.shares_valid, d.tickets));

      // Recent wins
      var wins = d.recent_wins || [];
      var tb = $('#dashWinsTable');
      if (wins.length) {
        tb.innerHTML = wins.map(function(w) {
          return '<tr><td>#' + w.id + '</td><td>' + fmt(w.jackpot_amount,6) + ' XMR</td><td>' + fmt(w.jackpot_amount - w.house_tax,6) + ' XMR</td><td title="' + (w.drawn_at ? isoOf(w.drawn_at) : '') + '">' + (w.drawn_at ? new Date(w.drawn_at*1000).toLocaleDateString() : '—') + '</td></tr>';
        }).join('');
      }

      // Fetch referral stats
      fetchReferralStats();

      // Fetch the member's alert history (NFTY-off record)
      fetchProfileAlerts();

      // Fetch gamification stats (level, XP, badges)
      fetchGamification();
    } catch(e) { console.error('Profile stats error:', e); }
  }

  /* THE ALERT LOG (Oct 21): the profile reads back what the alert
     client wrote when NFTY was off. Values are computed text only —
     the payload is summarized, never injected as markup. An empty
     history keeps its one-line placeholder instead of an empty table. */
  async function fetchProfileAlerts() {
    var tb = $('#dashAlertLog');
    if (!tb) return;
    try {
      var res = await fetch(API + '/profile/alert-log');
      if (!res.ok) return;               // signed out: leave the placeholder
      var d = await res.json();
      var alerts = (d && d.alerts) || [];
      if (!alerts.length) return;
      tb.innerHTML = alerts.map(function (a) {
        var payload = a.payload == null ? '' : JSON.stringify(a.payload);
        var when = a.ts || '';
        var parsed = when ? new Date(when) : null;
        var label = (parsed && !isNaN(parsed))
          ? parsed.toLocaleString()
          : when;
        return '<tr>' +
          '<td><b>' + escapeHtml(a.type) + '</b></td>' +
          '<td style="font-size:0.8em;color:var(--text-dim);word-break:break-all;">' +
            escapeHtml(payload) + '</td>' +
          '<td title="' + escapeHtml(when) + '">' + escapeHtml(label) + '</td>' +
          '</tr>';
      }).join('');
    } catch (e) { /* the profile still renders without the history */ }
  }

  // ─── Gamification: Level, XP, Badges ───────────────────────
  function levelEmoji(level) {
    level = level || 1;
    if (level >= 10) return '💎';
    if (level >= 7) return '⚡';
    if (level >= 4) return '🔨';
    return '⛏️';
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // THE PROGRESSION CARD (Oct 24): streak, the 3x milestone conversion,
  // contributing days toward the 100-day Pro unlock and which path
  // (progression / donation) granted it. Values come from
  // /api/gamification's `progression` block, never from client guesses.
  function renderProgression(prog) {
    if (!prog) return;
    var st = prog.streak || {};
    function txt(id, value) {
      var el = $(id);
      if (el) el.textContent = value;
    }

    var streak = st.streak_days || 0;
    txt('#progStreak', streak + (streak === 1 ? ' day' : ' days'));
    var mult = st.multiplier || 1;
    txt('#progBonus', mult + '×');
    txt('#progBonusNote', mult > 1
      ? 'milestone day — shares count ' + mult + '×'
      : '3× on milestone days');
    txt('#progNext', 'Day ' + (st.next_milestone_day || 7));

    var days = prog.days_mined || 0;
    var req = prog.days_required || 100;
    var pct = Math.min(100, Math.max(0, Number(prog.progress_percent) || 0));
    txt('#progDays', days.toLocaleString() + ' / ' + req + ' contributing days');
    txt('#progPct', pct + '%');
    var bar = $('#progBar');
    if (bar) bar.style.width = pct + '%';
    txt('#progRemaining', prog.days_remaining > 0
      ? prog.days_remaining + ' more contributing day' +
        (prog.days_remaining === 1 ? '' : 's') + ' to the automatic unlock.'
      : 'The 100-day threshold is reached.');

    var unlocked = !!prog.pro_unlocked;
    var source = prog.pro_unlock_source || '';
    txt('#progPath100', unlocked && source === 'progression'
      ? '✅ Unlocked on this path'
      : (unlocked ? '—' : days.toLocaleString() + ' of ' + req + ' days'));
    // The price is the server's (prog.pro_price), never a literal here:
    // the dashboard must quote the same XMR the checkout will ask for.
    var pp = prog.pro_price || {};
    var priceLabel = '';
    // Lead with the DOLLAR price (the product is sold at $95); the XMR
    // figure is the checkout's quote at today's rate. Same pair the
    // server fills the homepage with.
    if (pp.usd) {
      priceLabel = '$' + Number(pp.usd).toFixed(2) +
        (pp.xmr ? ' (≈' + pp.xmr + ' XMR)' : '');
    } else if (pp.xmr) {
      priceLabel = pp.xmr + ' XMR';
    }
    txt('#progPath95', unlocked && source === 'donation'
      ? '✅ Unlocked by donation'
      : (unlocked ? '—'
        : (priceLabel ? 'Instant unlock — ' + priceLabel + ' — +1 entry'
                      : 'Instant unlock — +1 entry')));

    var status = $('#progStatus');
    if (status) {
      if (unlocked) {
        status.textContent = '💎 Kraken Scout Pro is unlocked on your account' +
          (source === 'donation' ? ' by donation' :
           source === 'progression' ? ' through the 100-day mining path' : '') +
          (prog.entry_held
            ? ' — your +1 jackpot entry is held until you add a payout wallet.'
            : (prog.unlock_entry_awarded
              ? ' — the +1 jackpot entry has been credited.' : '.'));
      } else {
        status.textContent = 'Not unlocked yet — ' +
          (prog.days_remaining || 0) +
          ' contributing days to go, or unlock now for ' +
          (priceLabel || 'the amount the checkout quotes') + '.';
      }
    }
  }

  async function fetchGamification() {
    var loading = $('#gameLoading');
    var main = $('#gameMain');
    var noMiner = $('#gameNoMiner');
    if (!loading && !main && !noMiner) return; // elements not on dashboard

    try {
      var res = await fetch(API + '/gamification');
      if (!res.ok) {
        if (loading) loading.style.display = 'none';
        if (main) main.style.display = 'none';
        if (noMiner) noMiner.style.display = '';
        return;
      }
      var d = await res.json();

      if (!d.has_miner) {
        if (loading) loading.style.display = 'none';
        if (main) main.style.display = 'none';
        if (noMiner) noMiner.style.display = '';
        renderProgression(d.progression);
        return;
      }

      if (loading) loading.style.display = 'none';
      if (noMiner) noMiner.style.display = 'none';
      if (main) main.style.display = '';

      renderProgression(d.progression);

      // Level + emoji
      var emojiEl = $('#gameLevelEmoji');
      var numEl = $('#gameLevelNum');
      if (emojiEl) emojiEl.textContent = levelEmoji(d.level);
      if (numEl) numEl.textContent = 'Level ' + (d.level || 1);

      // XP bar
      var xpInto = d.xp_into_level || 0;
      var xpNeeded = d.xp_needed_for_next || 1;
      var pct = Math.min(100, Math.max(0, (xpInto / xpNeeded) * 100));
      var fill = $('#gameXpFill');
      if (fill) fill.style.width = pct.toFixed(1) + '%';
      var xpText = $('#gameXpText');
      if (xpText) {
        xpText.innerHTML = '<b>' + xpInto.toLocaleString() + '</b> / ' + xpNeeded.toLocaleString() +
          ' XP to Level ' + (d.next_level || (d.level || 1) + 1);
      }

      // Stat tiles
      var streak = $('#gameStreak');
      if (streak) streak.textContent = (d.mining_streak_days || 0) + ' days';
      var shares = $('#gameLifetimeShares');
      if (shares) shares.textContent = (d.total_shares_lifetime || 0).toLocaleString();
      var superT = $('#gameSuperTickets');
      if (superT) superT.textContent = d.super_tickets || 0;
      var badgesCount = $('#gameBadgesCount');
      var totalBadges = (d.badges && d.badges.length) || 12;
      if (badgesCount) badgesCount.textContent = (d.badges_earned_count || 0) + ' / ' + totalBadges;

      // Badge grid
      var grid = $('#gameBadgeGrid');
      if (grid && d.badges && d.badges.length) {
        grid.innerHTML = d.badges.map(function (b) {
          var cls = b.earned ? 'badge-tile earned' : 'badge-tile unearned';
          var status = b.earned ? '✅' : '🔒';
          return '<div class="' + cls + '" title="' + escapeHtml(b.description) + '">' +
            '<div class="badge-emoji">' + escapeHtml(b.emoji) + '</div>' +
            '<div class="badge-name">' + escapeHtml(b.name) + '</div>' +
            '<div class="badge-status">' + status + '</div>' +
            '</div>';
        }).join('');
      }
    } catch (e) {
      console.error('Gamification error:', e);
      if (loading) loading.style.display = 'none';
      if (main) main.style.display = 'none';
      if (noMiner) noMiner.style.display = '';
    }
  }

  async function dashSaveProfile() {
    var data = {
      display_name: $('#editDisplayName').value.trim(),
      avatar: $('#editAvatar').value.trim() || '⛏️',
      bio: $('#editBio').value.trim(),
      wallet_address: $('#editWallet').value.trim(),
    };
    try {
      var res = await fetch(API + '/profile/update', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify(data),
      });
      var d = await res.json();
      if (d.status === 'ok') {
        toast('Profile updated!', 'success');
        dashCheckAuth();
      } else { toast(d.detail || 'Update failed', 'error'); }
    } catch(e) { toast('Update error', 'error'); }
  }

  // ════════════════════════════════════════════════════════════
  //  ADMIN DASHBOARD
  // ════════════════════════════════════════════════════════════

  async function initAdmin() {
    stopPolling();

    // Fetch admin panel HTML via authenticated endpoint
    // (keeps admin UI out of public page source)
    var container = document.getElementById('adminContent');
    if (container && !container.dataset.loaded) {
      try {
        var res = await fetch(API + '/admin/panel');
        if (res.status === 401 || res.status === 403) {
          if (container) container.innerHTML = '<p style="color:var(--redstone);">⛔ Admin access required.</p>';
          return;
        }
        var html = await res.text();
        container.innerHTML = html;
        container.dataset.loaded = '1';
      } catch(e) {
        if (container) container.innerHTML = '<p style="color:var(--redstone);">Error loading admin panel: ' + e.message + '</p>';
        return;
      }
    }

    fetchAdminStats();
    fetchAdminReferralMeter();
    fetchAdminUsers();
    fetchAdminMiners();
    fetchAdminPayouts();
    fetchAdminAudit();
    fetchAdminSettings();
    fetchAdminVrsc();
    fetchAdminWalletStatus();
    fetchScoutStatus();
    fetchAdminOrphans();
    loadAdminComments();

    pollingTimers.push(setInterval(fetchAdminStats, 15000));
    pollingTimers.push(setInterval(fetchAdminOrphans, 60000));
    pollingTimers.push(setInterval(fetchAdminVrsc, 60000));

    var saveSetting = $('#btnSaveSetting');
    if (saveSetting) saveSetting.addEventListener('click', saveAdminSetting);

    var vrscPinBtn = $('#btnVrscPinSet');
    if (vrscPinBtn) vrscPinBtn.addEventListener('click', vrscPinSet);
    var vrscUnpinBtn = $('#btnVrscPinClear');
    if (vrscUnpinBtn) vrscUnpinBtn.addEventListener('click', vrscPinClear);
    var vrscTerrBtn = $('#btnVrscTerrSave');
    if (vrscTerrBtn) vrscTerrBtn.addEventListener('click', vrscTerrSave);

    var drawBtn = $('#btnAdminDraw');
    if (drawBtn) drawBtn.addEventListener('click', adminTriggerDraw);

    var walletRefresh = $('#btnWalletRefresh');
    if (walletRefresh) walletRefresh.addEventListener('click', fetchAdminWalletStatus);
    var walletBalance = $('#btnWalletBalance');
    if (walletBalance) walletBalance.addEventListener('click', fetchAdminWalletStatus);

    // Comment filter buttons
    $$('.admin-comment-filter').forEach(function(btn) {
      btn.addEventListener('click', function() {
        adminCommentFilter = btn.dataset.filter;
        $$('.admin-comment-filter').forEach(function(b) { b.style.opacity = '0.6'; });
        btn.style.opacity = '1';
        renderAdminComments();
      });
    });
  }

  // ─── Admin Comments ────────────────────────────────────────
  var adminComments = [];
  var adminCommentFilter = 'all';

  async function loadAdminComments() {
    try {
      var res = await fetch(API + '/admin/comments');
      if (!res.ok) return;
      adminComments = await res.json();
      renderAdminComments();
    } catch(e) {}
  }

  function renderAdminComments() {
    var list = $('#adminCommentsList');
    if (!list) return;
    var filtered = adminCommentFilter === 'all'
      ? adminComments
      : adminComments.filter(function(c) { return c.status === adminCommentFilter; });
    var newCount = adminComments.filter(function(c) { return c.status === 'new'; }).length;
    var countEl = $('#adminCommentNewCount');
    if (countEl) {
      countEl.textContent = newCount;
      countEl.style.display = newCount > 0 ? '' : 'none';
    }
    if (!filtered.length) {
      list.innerHTML = '<p style="color:var(--text-mute);text-align:center;padding:16px 0;">No comments.</p>';
      return;
    }
    list.innerHTML = filtered.map(function(c) {
      var badgeColor = c.status === 'new' ? 'var(--redstone)' : c.status === 'read' ? 'var(--emerald)' : 'var(--netherite)';
      var badgeText = c.status === 'new' ? 'NEW' : c.status === 'read' ? 'READ' : 'ARCHIVED';
      var actions = '';
      if (c.status === 'new') {
        actions = '<button class="btn btn-sm btn-emerald" data-action="mark-comment-read" data-id="' + c.id + '">Mark as Read</button>';
      } else if (c.status === 'read') {
        actions = '<button class="btn btn-sm" style="background:var(--netherite);color:var(--text-dim);" data-action="archive-comment" data-id="' + c.id + '">Archive</button>';
      }
      var emailLine = c.email ? '<div style="font-size:0.8em;color:var(--text-mute);">✉️ ' + escapeHtml(c.email) + '</div>' : '';
      return '<div style="padding:12px;margin-bottom:8px;background:var(--bg-void);border:2px solid #000;border-radius:0;">' +
        '<div style="display:flex;justify-content:space-between;align-items:start;gap:8px;flex-wrap:wrap;">' +
          '<div style="flex:1;min-width:200px;">' +
            '<div style="font-weight:700;color:var(--gold-bright);">' + escapeHtml(c.name) + '</div>' +
            emailLine +
            '<div style="margin-top:6px;color:var(--text);font-size:0.9em;">' + escapeHtml(c.message) + '</div>' +
            '<div style="font-size:0.75em;color:var(--text-mute);margin-top:6px;"><span title="' + (c.created_at ? isoOf(c.created_at) : '') + '">' + timeAgo(c.created_at) + '</span>' + (c.ip_address ? ' · IP: ' + escapeHtml(c.ip_address) : '') + '</div>' +
          '</div>' +
          '<div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end;">' +
            '<span style="background:' + badgeColor + ';color:#fff;padding:2px 8px;font-size:0.7em;font-weight:700;">' + badgeText + '</span>' +
            actions +
          '</div>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  function escapeHtml(str) {
    if (!str) return '';
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  async function markCommentRead(id) {
    try {
      var res = await fetch(API + '/admin/comments/' + id + '/status?status=read', {method: 'POST'});
      if (res.ok) {
        var c = adminComments.find(function(x) { return x.id === id; });
        if (c) c.status = 'read';
        renderAdminComments();
      }
    } catch(e) {}
  }

  async function archiveComment(id) {
    try {
      var res = await fetch(API + '/admin/comments/' + id + '/status?status=archived', {method: 'POST'});
      if (res.ok) {
        var c = adminComments.find(function(x) { return x.id === id; });
        if (c) c.status = 'archived';
        renderAdminComments();
      }
    } catch(e) {}
  }

  async function fetchAdminWalletStatus() {
    var el = $('#adminWalletStatus');
    if (!el) return;
    el.innerHTML = '<span style="color:var(--text-mute);"><span class="loading"></span> Checking...</span>';
    try {
      var res = await fetch(API + '/admin/wallet-status');
      var d = await res.json();
      if (d.rpc_available) {
        el.innerHTML = '<span style="color:var(--green);">✅ Wallet RPC ONLINE</span> ' +
          'Balance: ' + (d.balance ? d.balance.toFixed(6) : '?') + ' XMR | ' +
          'Unlocked: ' + (d.unlocked_balance ? d.unlocked_balance.toFixed(6) : '?') + ' XMR | ' +
          'Height: ' + (d.height || '?');
      } else {
        el.innerHTML = '<span style="color:var(--redstone);">❌ Wallet RPC OFFLINE</span> ' +
          '<span style="color:var(--text-mute);">Start monero-wallet-rpc on port 18082</span>';
      }
    } catch(e) {
      el.innerHTML = '<span style="color:var(--redstone);">❌ Wallet status unavailable</span>';
    }
  }

  async function fetchScoutStatus() {
    var el = document.getElementById('scoutStatus');
    if (!el) return;
    try {
      var res = await fetch(API + '/scout/health');
      if (!res.ok) throw new Error('not admin');
      var d = await res.json();
      if (d.up) {
        el.textContent = '● LIVE';
        el.style.color = 'var(--emerald)';
      } else {
        el.textContent = '● CONSOLE OFFLINE';
        el.style.color = 'var(--redstone)';
      }
    } catch(e) {
      el.textContent = '● CONSOLE OFFLINE';
      el.style.color = 'var(--redstone)';
    }
  }

  async function fetchAdminOrphans() {
    var el = $('#adminOrphansBody');
    var stamp = $('#adminOrphansStamp');
    if (!el || !stamp) return;
    try {
      var res = await fetch(API + '/admin/scheduler/status');
      if (res.status === 401 || res.status === 403) return;
      var d = await res.json();
      var o = (d && d.orphans) || {};
      var last = o.last || {};
      var total = (typeof last.total === 'number') ? last.total : null;
      if (o.enabled === false) {
        el.innerHTML = '<p style="margin:4px 0;color:var(--text-mute);">Disabled by operator (SCHEDULER_ORPHAN_ENABLED=0).</p>';
        stamp.textContent = '\u25cf disabled';
        stamp.style.color = 'var(--text-mute)';
        return;
      }
      stamp.textContent = (total === null) ? '\u25cf no scan yet'
        : ('\u25cf last scan: ' + new Date(last.ts * 1000).toLocaleString());
      stamp.style.color = 'var(--text-mute)';
      if (total === null) {
        el.innerHTML = '<p style="margin:4px 0;">No scan yet - the weekly leg '
          + 'stamps its first report at the next due tick (or use the '
          + 'scheduler tick button).</p>';
        return;
      }
      if (total > 0) {
        el.innerHTML = '<div style="padding:8px 10px;margin-bottom:8px;'
          + 'border:1px solid var(--redstone);border-radius:6px;'
          + 'background:rgba(255,0,0,0.08);color:var(--redstone);'
          + 'font-weight:bold;">\u26a0\ufe0f ' + total + ' orphaned row(s) '
          + 'detected - do not sweep blind: run '
          + '<code>python sweep_db.py --report</code> to plan it '
          + '(rc 1 = sweepable, rc 2 = UNPLANNED edge).</div>'
          + '<p style="margin:4px 0;">The weekly scheduler leg reported '
          + 'this (audit action scheduler.orphans_report). Review the '
          + 'receipt, extend the statement set for any new edge, then '
          + 'dry-run before --apply.</p>';
      } else {
        el.innerHTML = '<p style="margin:4px 0;color:var(--emerald);">\u2705 '
          + 'All clear - 0 violating rows across the foreign keys at the '
          + 'last weekly scan.</p>';
      }
    } catch(e) {}
  }

  // WHO FUNDED THE POT (Oct 26): the Current Jackpot card's second line.
  // Two sources only — the daily funding accrual and XMR actually
  // received — and they add up to the number above them, because the
  // server scales both through the same earmark (get_jackpot_split).
  // Read-only copy: nothing here writes the pot.
  function paintAdminJackpotSplit(split) {
    var el = $('#adminJackpotSplit');
    if (!el) return;
    if (!split || !split.attributed) {
      el.textContent = 'no funds in play to attribute yet';
      el.title = 'The pot is empty: nothing has been accrued or received.';
      return;
    }
    var pot = Number(split.jackpot_xmr || 0);
    var accrual = Number(split.accrual_xmr || 0);
    var received = Number(split.received_xmr || 0);
    var pct = pot > 0 ? Math.round(accrual * 100 / pot) : 0;
    var held = Number(split.unclaimed_xmr || 0);
    el.innerHTML =
      '<div>daily accrual <b style="color:var(--gold);">' + fmt(accrual, 6) +
      '</b> <span style="opacity:.7;">(' + pct + '%)</span></div>' +
      '<div>received <b style="color:var(--diamond);">' + fmt(received, 6) +
      '</b> <span style="opacity:.7;">(' + (pot > 0 ? 100 - pct : 0) +
      '%)</span></div>';
    el.title = 'XMR, and the two lines add up to the pot above. "Daily ' +
      'accrual" is what the funding formula added and the pool still ' +
      'holds; "received" is XMR that actually arrived (Scout Pro ' +
      'donation shares, wallet seeds, house-tax top-ups). Claims are ' +
      'paid from both in proportion, so this is what is left, not what ' +
      'was ever contributed.' +
      (held > 0 ? ' A drawn prize of ' + fmt(held, 6) + ' XMR is being ' +
        'held back for its winner and is not in the pot.' : '') +
      (split.inferred ? ' The split was inferred from the funding ledger ' +
        'on first read; every credit since is attributed as it lands.' : '');
  }

  async function fetchAdminStats() {
    try {
      var res = await fetch(API + '/admin/stats');
      if (res.status === 401 || res.status === 403) { navigateTo('dashboard'); toast('Admin access required', 'error'); return; }
      var d = await res.json();
      // The admin sees the honest split: real vs simulated (Oct 24).
      var demoN = d.miners_demo || 0;
      var demoActive = d.miners_demo_active || 0;
      $('#adminActiveMiners').textContent = (d.miners_active || 0) +
        (demoActive ? ' (' + demoActive + ' demo)' : '');
      $('#adminTotalMiners').textContent = (d.miners_total || 0) +
        (demoN ? ' (' + demoN + ' demo)' : '');
      $('#adminJackpot').textContent = fmt(d.jackpot_current || 0, 6);
      paintAdminJackpotSplit(d.jackpot_split);
      $('#adminHouseTax').textContent = fmt(d.total_house_tax || 0, 6);
      $('#adminUsers').textContent = d.users_total || 0;
      $('#adminDrawings').textContent = d.drawings_total || 0;
      $('#adminTickets').textContent = d.tickets_total || 0;
      $('#adminPaidOut').textContent = fmt(d.total_paid_out || 0, 6);
    } catch(e) {}
  }

  // THE REFERRAL METER (Oct 25): the cap is per referrer per jackpot cycle,
  // so the panel draws one bar per referrer rather than one global bar — a
  // single aggregate would hide an account sitting at the cap.
  async function fetchAdminReferralMeter() {
    function ea(x) {
      return String(x == null ? '' : x).replace(/[&<>"']/g, function(c) {
        return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
      });
    }
    try {
      var res = await fetch(API + '/admin/referral-meter');
      if (!res.ok) return;
      var d = await res.json();
      var sum = $('#refMeterSummary');
      if (!sum) return;
      var stamp = $('#refMeterStamp');
      if (stamp) stamp.textContent = d.cycle_anchor
        ? 'cycle since ' + new Date(d.cycle_anchor * 1000).toLocaleString()
        : 'no drawing yet this cycle';
      var held = d.entries_held || 0;
      sum.innerHTML = '<b>' + (d.awards_this_cycle || 0) + '</b> award' +
        ((d.awards_this_cycle === 1) ? '' : 's') + ' this cycle · <b>' +
        (d.referrers_this_cycle || 0) + '</b> referrer' +
        ((d.referrers_this_cycle === 1) ? '' : 's') + ' · cap <b>' +
        (d.cap || 0) + '</b> each · <b>' + (d.referrers_at_cap || 0) +
        '</b> at the cap · <b>' + held + '</b> entr' +
        (held === 1 ? 'y' : 'ies') + ' held (' + (d.accounts_holding || 0) +
        ' account' + ((d.accounts_holding === 1) ? '' : 's') +
        ' without a wallet)';
      var rows = $('#refMeterReferrers');
      if (rows) {
        var list = d.top_referrers || [];
        rows.innerHTML = list.length ? list.map(function(r) {
          var pct = d.cap ? Math.min(100, Math.round(r.awards * 100 / d.cap)) : 0;
          var colour = r.at_cap ? 'var(--redstone)' : 'var(--gold)';
          return '<div><div style="display:flex;justify-content:space-between;font-size:0.8em;">' +
            '<span class="ref-meter-name">' + ea(r.username) + '</span>' +
            '<span style="color:' + colour + ';">' + r.awards + ' / ' + d.cap +
            (r.at_cap ? ' · cap reached' : '') + '</span></div>' +
            '<div style="height:8px;background:var(--bg-cobble);border-radius:4px;overflow:hidden;margin-top:3px;">' +
            '<div class="ref-meter-bar" data-awards="' + r.awards + '" style="height:100%;width:' + pct +
            '%;background:' + colour + ';"></div></div></div>';
        }).join('')
          : '<div style="font-size:0.8em;color:var(--text-mute);">No referral awards in this cycle.</div>';
      }
      var recent = $('#refMeterRecent');
      if (recent) {
        var rs = d.recent || [];
        recent.innerHTML = rs.length ? 'Latest: ' + rs.slice(0, 5).map(function(a) {
          var when = a.created_at
            ? new Date(a.created_at * 1000).toLocaleTimeString() : '—';
          return ea(a.referrer || '?') + ' → ' + ea(a.referred || '?') +
            ' (' + ea(a.method) + ', ' + a.tickets + ' each)' +
            (a.referrer_granted ? '' : ' · referrer held') +
            (a.referred_granted ? '' : ' · friend held') + ' · ' + when;
        }).join('<br>') : '';
      }
    } catch(e) {}
  }

  async function fetchAdminUsers() {
    try {
      var res = await fetch(API + '/admin/users');
      if (!res.ok) return;
      var d = await res.json();
      var tb = $('#adminUsersTable');
      if (!tb || !d.length) return;
      tb.innerHTML = d.map(function(u) {
        return '<tr><td>' + u.id + '</td><td>' + u.username + '</td><td>' + shortAddr(u.wallet_address||'') + '</td><td>' + (u.is_admin?'✅':'—') + '</td><td>' + fmt(u.total_earned||0,6) + '</td><td>' + (u.total_shares||0).toLocaleString() + '</td><td title="' + (u.created_at ? isoOf(u.created_at) : '') + '">' + (u.created_at?new Date(u.created_at*1000).toLocaleDateString():'—') + '</td></tr>';
      }).join('');
    } catch(e) {}
  }

  async function fetchAdminMiners() {
    try {
      var res = await fetch(API + '/admin/miners');
      if (!res.ok) return;
      var d = await res.json();
      var tb = $('#adminMinersTable');
      if (!tb || !d.length) return;
      tb.innerHTML = d.map(function(m) {
        var demo = m.is_demo
          ? ' <span style="font-size:0.75em;color:var(--text-mute);border:1px solid var(--hairline);border-radius:4px;padding:1px 4px;">demo</span>'
          : '';
        return '<tr><td>' + m.id + '</td><td>' + shortAddr(m.wallet_address) + demo + '</td><td>' + (m.shares_valid||0).toLocaleString() + '</td><td>' + fmtHash(m.hashrate) + '</td><td><span title="' + (m.last_seen ? isoOf(m.last_seen) : '') + '">' + timeAgo(m.last_seen) + '</span></td></tr>';
      }).join('');
    } catch(e) {}
  }

  async function fetchAdminPayouts() {
    try {
      var res = await fetch(API + '/admin/payouts');
      if (!res.ok) return;
      var d = await res.json();
      var tb = $('#adminPayoutsTable');
      if (!tb || !d.length) return;
      tb.innerHTML = d.map(function(p) {
        var action = p.status === 'pending' ? '<button class="btn btn-emerald btn-sm" data-action="mark-paid" data-id="'+p.id+'">Mark Sent</button>' : p.status;
        // THE COIN FIELD (Oct 28): a VRSC payout names its coin and
        // quantity; the XMR equivalent stays secondary. XMR rows keep
        // the exact markup that was always here.
        var coin = (p.coin || 'xmr').toLowerCase();
        var amountCell = coin === 'xmr'
          ? fmt(p.amount_xmr,6) + ' XMR'
          : (p.amount_coin != null
              ? fmt(p.amount_coin,6) + ' ' + coin.toUpperCase()
                + ' <span style="font-size:0.78em;color:var(--text-mute);">(equiv ' + fmt(p.amount_xmr,6) + ' XMR)</span>'
              : fmt(p.amount_xmr,6) + ' XMR-equiv \u00b7 ' + coin.toUpperCase());
        var statusCell = (coin === 'vrsc' && p.status === 'pending'
          && p.tx_hash === 'pending_manual_treasury')
          ? 'pending <span style="font-size:0.78em;color:var(--gold);">\u00b7 treasury send</span>'
          : p.status;
        return '<tr><td>' + p.id + '</td><td>' + shortAddr(p.wallet_address||'') + '</td><td>' + amountCell + '</td><td>' + p.payout_type + '</td><td>' + statusCell + '</td><td>' + action + '</td></tr>';
      }).join('');
    } catch(e) {}
  }

  async function fetchAdminAudit() {
    try {
      var res = await fetch(API + '/admin/audit');
      if (!res.ok) return;
      var d = await res.json();
      var el = $('#adminAuditLog');
      if (!el) return;
      if (!d.length) { el.innerHTML = '<p style="color:var(--text-mute);">No activity logged</p>'; return; }
      el.innerHTML = d.map(function(a) {
        return '<div style="padding:6px 0;border-bottom:1px solid var(--border);"><span style="color:var(--gold);">' + (a.username||'system') + '</span> — <span style="color:var(--text-dim);">' + a.action + '</span> <span style="color:var(--text-mute);font-size:0.85em;" title="' + (a.created_at ? isoOf(a.created_at) : '') + '">' + (a.created_at ? timeAgo(a.created_at) : '') + '</span></div>';
      }).join('');
    } catch(e) {}
  }


  // ─── Admin Verus card (Oct 28) ─────────────────────────────
  // Pin, territories and the reader's episode log, painted with the
  // SAME discipline the public rate notes follow: a number always
  // names its tier — a bare figure would be a fallback pretending to
  // be live. CSP forbids inline handlers, so the card's buttons are
  // bound once in initAdmin like every other admin control.
  function _vesc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;')
      .replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  var VRSC_SRC_LABELS = {
    override: 'operator pin',
    coingecko: 'CoinGecko (live fetch)',
    default: 'fallback constant'
  };

  function _paintVrscState(d) {
    var info = d.rate || {};
    var rateEl = $('#vrscRateValue');
    var srcEl = $('#vrscRateSource');
    if (rateEl) rateEl.textContent = Number(info.rate || 0).toFixed(4) + ' USD/VRSC';
    if (srcEl) {
      var label = VRSC_SRC_LABELS[info.source] || (info.source || 'unknown');
      var asOf = info.as_of ? new Date(info.as_of * 1000).toLocaleString() : '—';
      srcEl.innerHTML = _vesc(label) +
        ' · <span style="color:' +
        (info.stale ? 'var(--redstone,#c0392b)' : 'var(--emerald,#2ea86f)') + '">' +
        (info.stale ? 'STALE' : 'fresh') + '</span> · as of ' + _vesc(asOf);
      srcEl.title = info.stale
        ? 'The number is older than the TTL (or is the fallback) — the reader says so rather than pretending.'
        : '';
    }
    var liveEl = $('#vrscLiveNote');
    if (liveEl) {
      liveEl.textContent = d.live_enabled
        ? 'live fetching: ON'
        : 'live fetching: OFF (kill switch)';
      liveEl.style.color = d.live_enabled ? '' : 'var(--redstone,#c0392b)';
    }
    var pinEl = $('#vrscPinNote');
    if (pinEl) {
      pinEl.textContent = d.pin
        ? 'pinned at ' + d.pin + ' — the pin outranks any stamped rate'
        : 'no pin — the stamped live tier answers';
    }
    var pinInput = $('#vrscPinInput');
    if (pinInput) pinInput.value = d.pin || '';
    var terr = d.territories || {};
    var terrInput = $('#vrscTerrInput');
    if (terrInput) terrInput.value = (terr.codes || []).join(',');
    var terrSrc = $('#vrscTerrSource');
    if (terrSrc) {
      var tsrc = { db: 'set in the db (this editor wins)',
                   env: 'from XMRJ_VRSC_TERRITORIES (env)',
                   'default': 'built-in default (US,CA)' }[terr.source] || terr.source;
      terrSrc.textContent = (terr.codes || []).length + ' in force · ' + tsrc;
    }
    var st = d.state || {};
    var stEl = $('#vrscStateNote');
    if (stEl) {
      stEl.textContent =
        'served=' + (st.served || '—') +
        ' · fails=' + (st.fails || '—') +
        ' · candidate=' + (st.candidate || '—');
    }
    var epi = $('#vrscEpiList');
    if (epi) {
      var rows = d.episodes || [];
      epi.innerHTML = rows.length ? rows.map(function (a) {
        var when = a.created_at
          ? new Date(a.created_at * 1000).toLocaleString() : '—';
        return '<div style="padding:4px 0;border-bottom:1px solid var(--border);">' +
          '<span style="color:var(--gold);">' + _vesc(a.action || '') + '</span>' +
          ' <span style="color:var(--text-mute);font-size:0.85em;" title="' +
          _vesc(when) + '">' + (a.created_at ? timeAgo(a.created_at) : '') + '</span>' +
          '<div style="color:var(--text-dim);margin-top:2px;word-break:break-all;">' +
          _vesc(a.details || '') + '</div></div>';
      }).join('')
        : '<p style=\'color:var(--text-mute);margin:4px 0;\'>No vrsc_rate episodes logged — the tier has been holding steady.</p>';
    }
    var stamp = $('#vrscStamp');
    if (stamp) stamp.textContent = 'refreshed ' + new Date().toLocaleTimeString();
  }

  async function fetchAdminVrsc() {
    try {
      var res = await fetch(API + '/admin/vrsc');
      if (!res.ok) return;
      _paintVrscState(await res.json());
    } catch (e) {}
  }

  async function _postVrsc(path, body, okMsg) {
    try {
      var res = await fetch(API + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      var d = await res.json();
      if (res.ok && d.status === 'ok') {
        toast(okMsg, 'success');
        fetchAdminVrsc();
        fetchAdminAudit();
      } else {
        toast(d.detail || 'Failed', 'error');
      }
    } catch (e) { toast('Error: ' + okMsg, 'error'); }
  }

  function vrscPinSet() {
    var v = ($('#vrscPinInput').value || '').trim();
    _postVrsc('/admin/vrsc/rate-pin', { value: v }, 'VRSC rate pin saved');
  }
  function vrscPinClear() {
    _postVrsc('/admin/vrsc/rate-pin', { value: '' }, 'VRSC rate pin cleared');
  }
  function vrscTerrSave() {
    var v = ($('#vrscTerrInput').value || '').trim();
    _postVrsc('/admin/vrsc/territories', { territories: v }, 'VRSC territories saved');
  }

  async function fetchAdminSettings() {
    try {
      var res = await fetch(API + '/admin/settings');
      if (!res.ok) return;
      var d = await res.json();
      var el = $('#adminSettingsList');
      if (!el) return;
      var keys = Object.keys(d);
      if (!keys.length) { el.innerHTML = '<p style="color:var(--text-mute);">No custom settings</p>'; return; }
      el.innerHTML = keys.map(function(k) {
        return '<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--border);"><span style="color:var(--text-dim);">' + k + '</span><span style="color:var(--gold);">' + d[k] + '</span></div>';
      }).join('');
    } catch(e) {}
  }

  async function saveAdminSetting() {
    var key = $('#newSettingKey').value.trim();
    var val = $('#newSettingVal').value.trim();
    if (!key) { toast('Enter setting key', 'error'); return; }
    try {
      var res = await fetch(API + '/admin/settings', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({key: key, value: val}),
      });
      var d = await res.json();
      if (d.status === 'ok') { toast('Setting saved!', 'success'); fetchAdminSettings(); }
      else { toast(d.detail || 'Failed', 'error'); }
    } catch(e) { toast('Error saving setting', 'error'); }
  }

  async function adminTriggerDraw() {
    try {
      // Get nonce first
      var nr = await fetch(API + '/draw-nonce');
      var nd = await nr.json();
      var nonce = nd.nonce;
      // Use nonce in draw request
      var res = await fetch(API + '/admin/draw', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({nonce: nonce}),
      });
      var d = await res.json();
      if (d.status === 'drawn') {
        toast('Drawing complete! Winner: ' + (d.winner||'unknown'), 'success');
        fetchAdminStats();
      } else if (d.status === 'skipped') {
        toast('Drawing skipped: ' + d.reason, 'error');
      } else if (d.detail) {
        toast('Error: ' + d.detail, 'error');
      } else {
        toast('Drawing: ' + JSON.stringify(d), 'success');
      }
    } catch(e) { toast('Draw error', 'error'); }
  }

  // Expose for inline onclick
  window.markPaid = async function(id) {
    try {
      await fetch(API + '/admin/payout/' + id + '/status?status=sent', {method:'POST'});
      toast('Payout marked as sent', 'success');
      fetchAdminPayouts();
    } catch(e) { toast('Error updating payout', 'error'); }
  };

  window.markCommentRead = markCommentRead;
  window.archiveComment = archiveComment;

  function initLeaderboard() {
    stopPolling();
    fetchLeaderboardDrawings();
    fetchLeaderboardMiners();

    pollingTimers.push(setInterval(fetchLeaderboardDrawings, POLL_LEADERBOARD_MS));
    pollingTimers.push(setInterval(fetchLeaderboardMiners, POLL_LEADERBOARD_MS));
  }

  async function fetchLeaderboardDrawings() {
    try {
      const res = await fetch(API + '/drawings');
      const d = await res.json();
      const tb = $('#lbDrawingsTable');
      if (!tb) return;

      if (!d || !d.length) {
        tb.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-mute)">🚀 Beta — first drawing coming soon!</td></tr>';
        return;
      }

      tb.innerHTML = d.map(function (r) {
        var winner = r.winner_wallet ? shortAddr(r.winner_wallet) : '—';
        var winnerClass = r.winner_id ? 'win-badge won' : 'win-badge pending';
        var winnerText = r.winner_id ? 'WON' : (r.status === 'cancelled' ? 'CANCELLED' : 'PENDING');
        var verifyLink = r.status === 'drawn'
          ? ' <a href=\"/api/verify/' + r.id + '\" target=\"_blank\" style=\"color:var(--diamond-bright);text-decoration:none;\" title=\"Provably Fair: Verify\">🔍</a>'
          : '';
        // Seeded history is display data: say so on the row.
        if (r.winner_is_demo) winner += ' <span style="font-size:0.8em;color:var(--text-mute);">(demo)</span>';
        return '<tr>' +
          '<td>#' + r.id + verifyLink + '</td>' +
          '<td>' + fmt(r.jackpot_amount, 6) + ' XMR</td>' +
          '<td><span class=\"' + winnerClass + '\">' + winnerText + '</span></td>' +
          '<td>' + winner + '</td>' +
          '<td>' + fmt(r.house_tax, 6) + ' XMR</td>' +
          '<td>' + (r.total_tickets || 0) + '</td>' +
          '</tr>';
      }).join('');
    } catch (e) {
      console.error('Leaderboard drawings error:', e);
    }
  }

  async function fetchLeaderboardMiners() {
    try {
      const res = await fetch(API + '/miners');
      const d = await res.json();
      const tb = $('#lbMinersTable');
      if (!tb) return;

      if (!d || !d.length) {
        tb.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-mute)">⛏️ Be the first to mine!</td></tr>';
        return;
      }

      tb.innerHTML = d.slice(0, 10).map(function (m, i) {
        // A simulated row says so on the row itself (Oct 24).
        var demoTag = m.is_demo
          ? ' <span style="font-size:0.72em;color:var(--text-mute);border:1px solid var(--hairline);border-radius:4px;padding:1px 4px;">demo</span>'
          : '';
        return '<tr>' +
          '<td>' + (i + 1) + '</td>' +
          '<td>' + shortAddr(m.wallet_address) + demoTag + '</td>' +
          '<td>' + (m.shares_valid || 0).toLocaleString() + '</td>' +
          '<td>' + fmtHash(m.hashrate) + '</td>' +
          '<td><span title="' + (m.last_seen ? isoOf(m.last_seen) : '') + '">' + timeAgo(m.last_seen) + '</span></td>' +
          '</tr>';
      }).join('');

      var demoRows = d.filter(function (m) { return m.is_demo; }).length;
      var note = $('#lbDemoNote');
      if (note) {
        if (demoRows > 0) {
          note.textContent = '⚠️ ' + demoRows + ' of these ' + d.length +
            ' rows are simulated demo accounts, not real miners. Their ' +
            'shares and hashrate are generated for display.';
          note.style.display = '';
        } else {
          note.style.display = 'none';
        }
      }
    } catch (e) {
      console.error('Leaderboard miners error:', e);
    }
  }

  // ════════════════════════════════════════════════════════════
  //  INITIALIZATION
  // ════════════════════════════════════════════════════════════

  /* THE ANNOUNCEMENT BANNER (fixed Oct 21): the old branch HID the banner
     exactly when an announcement was active and undismissed - the inverse
     of its purpose. Now an active announcement fills the text and shows
     the banner, a dismissed one stays hidden, and an inactive one stays
     hidden too. The message is textContent, never HTML. */
  function fetchAnnouncement() {
    fetch(API + '/announcement')
      .then(function(r) { return r.json(); })
      .then(function(d) {
        var banner = document.getElementById('announcementBanner');
        if (!banner) return;
        var dismissed = localStorage.getItem('xmr_announcement_dismissed') === '1';
        if (d && d.active && d.message && !dismissed) {
          var txt = document.getElementById('announcementText');
          if (txt) txt.textContent = d.message;
          banner.style.display = '';
        } else {
          banner.style.display = 'none';
        }
      })
      .catch(function() {});
  }

  function init() {
    // Bind locale selection before ordinary page navigation handlers.
    $$('.nav-language a').forEach(function (link) {
      link.addEventListener('click', function (event) {
        event.preventDefault();
        var target = new URL(link.href, window.location.href);
        if (window.location.hash) {
          var nextPath = target.searchParams.get('next') || '/';
          target.searchParams.set('next', nextPath + window.location.hash);
        }
        window.location.assign(target.href);
      });
    });

    // Fetch site-wide announcement
    fetchAnnouncement();

    // Check for referral code in URL
    var urlParams = new URLSearchParams(window.location.search);
    var refCode = urlParams.get('ref');
    if (refCode) {
      localStorage.setItem('xmrjackpot_ref', refCode);
      console.log('Referral code detected:', refCode);
    }

    // Resurrected baseline signup/legal notes (Sep 26 restore pass).
    (function showSignupBaselineNotes() {
      var legal = $('#legalNote');
      if (legal) legal.textContent = IMPORTANT_LEGAL_NOTE;
      var skip = $('#skipForNow');
      if (skip) skip.textContent = SKIP_FOR_NOW_NOTE;
      var verified = $('#verifiedParticipantStatus');
      if (verified) verified.textContent = VERIFIED_STATUS_NOTE;
    })();

    // Nav link bindings
    $$('.nav-links a, .nav-links button').forEach(function (link) {
      const page = link.dataset.page;
      if (page) {
        link.addEventListener('click', function (e) {
          e.preventDefault();
          navigateTo(page);
        });
      }
    });

    // Initialize all page sections
    initLanding();
    initHowTo();
    initSignup();

    // Hash-based routing: /#admin, /#dashboard, /#signup, etc.
    var hashPage = (window.location.hash || '').replace('#', '');
    var validPages = ['landing', 'signup', 'howto', 'realplay', 'leaderboard', 'dashboard', 'admin', 'challenge'];
    if (hashPage && validPages.indexOf(hashPage) !== -1) {
      navigateTo(hashPage);
    } else {
      // Show landing by default
      navigateTo('landing');
    }
    // f5 landing cleanup (Sep 26): the initial load handled the deep
    // link, but in-session hash changes (sharing /#dashboard links,
    // browser back/forward) never re-routed — listen for them. The
    // currentPage guard stops double-fires; admin stays gated in-page
    // (initAdmin renders "Admin access required" for non-admins).
    window.addEventListener('hashchange', function () {
      var h = (window.location.hash || '').replace('#', '');
      if (h && validPages.indexOf(h) !== -1 && h !== currentPage) {
        navigateTo(h);
      }
    });

    // Update mode badge
    updateModeBadge();
  }

  // DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();

// ── THE DEPLOY BADGE (footer) ─────────────────────────────────────────
// Names the commit the RUNNING PROCESS started from, read from /version
// (pool/server.py). Fetched with cache:'no-store' AND a timestamp query so
// neither the browser nor Cloudflare can answer with a stamp from an older
// deploy — a cached version badge is worse than none, because it would
// claim the server is something it is not. Any failure (route absent on an
// older process, offline, non-JSON) hides the line: the badge must never
// be the reason the footer breaks.
(function deployBadge() {
  var host = document.getElementById('deploy-version');
  if (!host) return;
  var commitEl = document.getElementById('deploy-commit');
  var startedEl = document.getElementById('deploy-started');

  function render(doc) {
    if (!doc || !doc.commit) {
      host.style.display = 'none';
      return;
    }
    host.style.display = '';
    if (commitEl) commitEl.textContent = doc.commit_short || String(doc.commit).slice(0, 12);
    if (startedEl) startedEl.textContent = doc.started_at_iso || 'unknown';
    host.title = 'branch ' + (doc.branch || '?') +
      (doc.dirty ? ' · worktree dirty' : '') +
      ' · pid ' + (doc.pid || '?') +
      ' · up ' + Math.round(doc.uptime_seconds || 0) + 's';
  }

  function load() {
    fetch('/version?ts=' + Date.now(), {
      cache: 'no-store',
      headers: { 'Accept': 'application/json' }
    })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(render)
      .catch(function () { host.style.display = 'none'; });
  }

  load();
  // re-read on a slow cadence: after a restart, an open tab corrects
  // itself within a minute instead of waiting for a reload
  setInterval(load, 60000);
})();
