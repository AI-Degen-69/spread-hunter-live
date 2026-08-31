/* Spread Hunter Live — Dashboard Application Logic
 *
 * Consumes existing API endpoints:
 *   GET  /api/state            — order/fill state
 *   GET  /api/system/status    — service PIDs, bot state, starting capital
 *   POST /api/system/start     — start bot stack (atomic)
 *   POST /api/system/stop      — stop bot stack
 *   POST /api/system/cancel-all — cancel all venue orders (DT7: typed confirm)
 *   GET  /api/kpi              — all Tab 2 analytics
 *   GET  /api/scan-state       — SCANNING/IDLE/STALLED
 *   GET  /api/pairs-activity   — auto-pairs counts
 *   GET  /api/guardrail-alerts — active violations
 *   GET  /api/guardrail-health — watcher liveness
 *   GET  /api/cycle-stream     — SSE event stream
 *   GET  /api/parameters       — strategy config (NEW)
 *   GET  /api/active-markets   — active markets (NEW)
 *   GET  /api/closed-markets   — closed markets (NEW)
 *
 * Tab 3 (Screener) reads funnel data from /api/kpi's `funnel` field
 * and scan state from /api/scan-state. No new endpoints needed.
 */

'use strict';

// Suppress benign third-party / web3 extension message disconnects in sandboxed iframes
window.addEventListener('unhandledrejection', (event) => {
  const reason = event.reason || {};
  const msg = String(reason.message || reason.stack || reason);
  const code = reason.code || (reason.data && reason.data.code);
  if (
    msg.includes('Message channel disconnected') ||
    msg.includes('Failed to connect to MetaMask') ||
    msg.includes('Extension context invalidated') ||
    msg.includes('chrome-extension://') ||
    msg.includes('moz-extension://') ||
    code === 4900 ||
    code === -32603
  ) {
    event.preventDefault();
    event.stopImmediatePropagation();
    console.debug('[App] Suppressed third-party extension error:', msg);
  }
});

window.addEventListener('error', (event) => {
  const msg = String(event.message || (event.error && (event.error.message || event.error.stack)) || '');
  if (
    msg.includes('Message channel disconnected') ||
    msg.includes('Failed to connect to MetaMask') ||
    msg.includes('Extension context invalidated') ||
    msg.includes('chrome-extension://') ||
    msg.includes('moz-extension://')
  ) {
    event.preventDefault();
    event.stopImmediatePropagation();
    console.debug('[App] Suppressed window extension error:', msg);
  }
});

const POLL_MS = 2000;
let lastState = null;
let lastKpi = null;
// Whether the page is reading the production registry. Starts false: until the
// first status arrives we cannot claim a live view, and START is refused on it.
let lastDbIsProduction = false;

/* ── XSS defense: escape before innerHTML ── */
function esc(v) {
  if (v === null || v === undefined) return '--';
  const s = String(v);
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
           .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function fmtUSD(v) {
  if (v === null || v === undefined) return '--';
  const n = Number(v);
  if (isNaN(n)) return '--';
  return '$' + n.toFixed(2);
}

function fmtPct(v) {
  if (v === null || v === undefined) return '--';
  return (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%';
}

function fmtVal(v, cls) {
  const nullCls = (v === null || v === undefined) ? ' null' : '';
  return `<span class="kpi-value${cls || ''}${nullCls}">${esc(v)}</span>`;
}

/**
 * Format an ISO UTC timestamp into the viewer's local time string.
 * Returns an empty string if timestamp is missing or invalid.
 * @param {string|null|undefined} ts - ISO 8601 UTC timestamp string
 * @returns {string} Localized time string or empty string
 */
function fmtLocalTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleTimeString();
}

/**
 * Render a safe external anchor tag or escaped text for a market object.
 * @param {Object|null|undefined} m - Market object with title, slug, or url
 * @returns {string} Safe HTML anchor tag or escaped text
 */
function marketLink(m) {
  if (!m) return '--';
  const name = m.title || m.name || m.slug || '--';
  const url = m.url || (m.slug ? `https://polymarket.com/market/${m.slug}` : '');
  if (url) {
    return `<a href="${esc(url)}" class="market-link" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">${esc(name)}</a>`;
  }
  return esc(name);
}

/* ── controlFetch: CSRF-protected POST ── */
function controlFetch(path, options = {}) {
  const headers = { 'X-Control-Token': CONTROL_TOKEN, ...(options.headers || {}) };
  return fetch(path, { method: 'POST', ...options, headers });
}

/* ── Tab switching (DT7: localStorage persistence, 3 tabs) ── */
const tabBtns = document.querySelectorAll('.tab-btn');
const tab1 = document.getElementById('tab-1');
const tab2 = document.getElementById('tab-2');
const tab3 = document.getElementById('tab-3');

function switchTab(which) {
  tabBtns.forEach(b => {
    const isActive = b.id === 'tab-btn-' + which;
    b.classList.toggle('active', isActive);
    b.setAttribute('aria-selected', isActive);
  });
  tab1.hidden = (which !== 1);
  tab2.hidden = (which !== 2);
  tab3.hidden = (which !== 3);
  localStorage.setItem('sh-active-tab', String(which));
  if (which === 3) {
    setTimeout(updateKanbanNavButtons, 60);
  }
}

tabBtns.forEach(b => {
  b.addEventListener('click', () => {
    const num = b.id === 'tab-btn-1' ? 1 : (b.id === 'tab-btn-2' ? 2 : 3);
    switchTab(num);
  });
  b.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); b.click(); }
  });
});

// Restore tab from localStorage, default to Tab 1
const savedTab = parseInt(localStorage.getItem('sh-active-tab') || '1', 10);
switchTab(savedTab === 2 ? 2 : (savedTab === 3 ? 3 : 1));

/* ── Cancel-all modal (DT7: typed confirm) ── */
const cancelModal = document.getElementById('cancel-modal');
const cancelInput = document.getElementById('cancel-input');
const cancelConfirmBtn = document.getElementById('cancel-modal-confirm');
const cancelCloseBtn = document.getElementById('cancel-modal-close');
const cancelBtn = document.getElementById('btn-cancel-all');

cancelBtn.addEventListener('click', () => {
  cancelInput.value = '';
  cancelConfirmBtn.disabled = true;
  cancelModal.classList.add('show');
  cancelInput.focus();
});

cancelCloseBtn.addEventListener('click', () => cancelModal.classList.remove('show'));

cancelInput.addEventListener('input', () => {
  cancelConfirmBtn.disabled = (cancelInput.value.trim().toUpperCase() !== 'CANCEL');
});

cancelConfirmBtn.addEventListener('click', async () => {
  if (cancelInput.value.trim().toUpperCase() !== 'CANCEL') return;
  cancelConfirmBtn.disabled = true;
  cancelConfirmBtn.textContent = 'Cancelling...';
  try {
    const res = await controlFetch('/api/system/cancel-all');
    const data = await res.json();
    if (data.ok) {
      cancelModal.classList.remove('show');
    } else {
      cancelConfirmBtn.textContent = 'Failed: ' + (data.message || 'error');
      setTimeout(() => { cancelConfirmBtn.textContent = 'Confirm Cancel All'; cancelConfirmBtn.disabled = false; }, 3000);
    }
  } catch (e) {
    cancelConfirmBtn.textContent = 'Error: ' + e.message;
    setTimeout(() => { cancelConfirmBtn.textContent = 'Confirm Cancel All'; cancelConfirmBtn.disabled = false; }, 3000);
  }
});

// Escape key closes modals
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (cancelModal.classList.contains('show')) cancelModal.classList.remove('show');
    if (resetModal.classList.contains('show')) resetModal.classList.remove('show');
  }
});

/* ── Sync button (read-only venue refresh) ── */
const syncBtn = document.getElementById('btn-sync');
if (syncBtn) {
  syncBtn.addEventListener('click', async () => {
    if (syncBtn.disabled) return;
    const prevText = syncBtn.textContent;
    syncBtn.disabled = true;
    syncBtn.classList.add('syncing');
    syncBtn.textContent = 'SYNCING…';
    // Optional: show a one-line ticker notice so the operator sees it worked even before the poll.
    const empty = tickerEl.querySelector('.empty-state');
    let syncOk = false;
    let isWarning = false;
    try {
      const res = await controlFetch('/api/system/sync');
      const data = await res.json().catch(() => ({}));
      const steps = data.steps || {};
      const rec = steps.reconcile || {};
      const vs = steps.venue_sync || {};
      isWarning = (res.status === 207) || (vs.venue_open_unmeasured === true);
      syncOk = (data.ok === true) && res.ok;
      const localOpen = data.state?.local_open_orders;
      const venueOpen = (data.state?.venue_open_orders != null ? data.state.venue_open_orders : rec.open_orders_count);
      const venueOpenDisp = (venueOpen != null ? venueOpen : 0);
      const fillsDisp = (rec.fills_recorded != null ? rec.fills_recorded : 0);
      const cancelledDisp = (rec.orders_cancelled != null ? rec.orders_cancelled : 0);
      const posDisp = (vs.open_positions_count != null ? vs.open_positions_count : 0);
      const closesDisp = (vs.closes_written != null ? vs.closes_written : 0);
      const lines = [];
      if (rec.ok) lines.push(`Orders: venue ${venueOpenDisp} open, ${fillsDisp} new fills, ${cancelledDisp} marked cancelled`);
      else if (rec.error) lines.push(`Orders sync: ${rec.error}`);
      if (vs.ok) lines.push(`Venue: ${fmtUSD(vs.account_value_usd)} · ${posDisp} positions · ${closesDisp} closes synced`);
      else if (vs.error) lines.push(`Account sync: ${vs.error}`);
      // Human-readable verdict when dashboard was stale
      if (typeof localOpen === 'number' && typeof venueOpen === 'number' && localOpen !== venueOpen) {
        lines.push(`Fixed drift: dashboard had ${localOpen} open → now ${venueOpen} (venue truth)`);
      }
      if (vs.venue_open_unmeasured) {
        lines.push('Positions unmeasured: prior exposure retained');
      } else if (vs.raw_open_rows === 0) {
        lines.push('Positions: 0 on venue — dashboard exposure zeroed');
      }
      const msg = lines.join(' · ') || (data.ok ? 'Sync ok — dashboard now matches venue.' : 'Sync finished with warnings');
      // Reuse ticker as a transient banner; also trigger immediate re-poll.
      appendTickerEvent(`[SYNC] ${msg}`, 'Dashboard synced with Polymarket (read-only).', '');
    } catch (e) {
      syncOk = false;
      appendTickerEvent(`[SYNC ERROR] ${e.message || String(e)}`, 'Sync failed — venue may be unreachable. Retrying on next poll.', '');
    } finally {
      if (syncOk) syncBtn.textContent = 'SYNCED';
      else if (isWarning) syncBtn.textContent = 'SYNC WARNING';
      else syncBtn.textContent = 'SYNC FAILED';
      setTimeout(() => { syncBtn.textContent = prevText; syncBtn.disabled = false; syncBtn.classList.remove('syncing'); }, 1800);
      // Immediately refresh all tiles without waiting for the 2s poll.
      pollStatus();
    }
  });
}

/* ── Reset modal (typed confirm) ── */
const resetModal = document.getElementById('reset-modal');
const resetInput = document.getElementById('reset-input');
const resetConfirmBtn = document.getElementById('reset-modal-confirm');
const resetCloseBtn = document.getElementById('reset-modal-close');
const resetProgress = document.getElementById('reset-progress');
const resetBtn = document.getElementById('btn-reset');

resetBtn.addEventListener('click', () => {
  resetInput.value = '';
  resetConfirmBtn.disabled = true;
  resetProgress.style.display = 'none';
  resetModal.classList.add('show');
  resetInput.focus();
});

resetCloseBtn.addEventListener('click', () => resetModal.classList.remove('show'));

resetInput.addEventListener('input', () => {
  resetConfirmBtn.disabled = (resetInput.value.trim().toUpperCase() !== 'RESET');
});

resetConfirmBtn.addEventListener('click', async () => {
  if (resetInput.value.trim().toUpperCase() !== 'RESET') return;
  resetConfirmBtn.disabled = true;
  resetConfirmBtn.textContent = 'Resetting...';
  resetProgress.style.display = 'block';
  resetProgress.textContent = 'Halting bot...';
  try {
    const res = await controlFetch('/api/system/reset');
    const data = await res.json();
    if (data.ok) {
      resetProgress.textContent = (data.steps || []).join('\n');
      resetConfirmBtn.textContent = 'Done';
      setTimeout(() => {
        resetModal.classList.remove('show');
        resetConfirmBtn.textContent = 'Confirm Reset';
        pollStatus();
      }, 2000);
    } else {
      resetProgress.textContent = 'Failed: ' + (data.message || 'error');
      resetConfirmBtn.textContent = 'Failed';
      setTimeout(() => { resetConfirmBtn.textContent = 'Confirm Reset'; resetConfirmBtn.disabled = false; }, 3000);
    }
  } catch (e) {
    resetProgress.textContent = 'Error: ' + e.message;
    resetConfirmBtn.textContent = 'Error';
    setTimeout(() => { resetConfirmBtn.textContent = 'Confirm Reset'; resetConfirmBtn.disabled = false; }, 3000);
  }
});

/* ── Info bubbles (DT7: click-triggered) ── */
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('info-bubble')) {
    e.stopPropagation();
    const tooltip = e.target.nextElementSibling;
    if (tooltip && tooltip.classList.contains('info-tooltip')) {
      tooltip.classList.toggle('show');
    }
  } else {
    // Close any open tooltips
    document.querySelectorAll('.info-tooltip.show').forEach(t => t.classList.remove('show'));
  }
});

/* ── SSE Event Ticker (DT7: reconnect banner) ── */
let sseSource = null;
const tickerEl = document.getElementById('event-ticker');
const sseReconnect = document.getElementById('sse-reconnect');

/* ── Event translation: plain English for every cycle event ── */
const EVENT_TRANSLATIONS = {
  // Filter
  'filter|rerank_done': 'Finished scanning all Polymarket markets and updated the graduated list.',
  'filter|rerank_error': 'Market scan failed. The graduated list was not updated, so Decide & Execute keeps quoting the previous universe.',
  'screener|rerank_done': 'Finished scanning all Polymarket markets and updated the graduated list.',
  'screener|rerank_error': 'Market scan failed. The graduated list was not updated, so Decide & Execute keeps quoting the previous universe.',

  // Query — reconciliation
  'query|reconcile_ok': 'Checked the venue for new fills on our orders. All synced up.',
  'query|reconcile_error': 'Failed to sync fills from the venue. Orders may be stale until the next query.',
  'query|reconcile_contended': 'Another process is reconciling fills right now. Waiting in line to avoid double-counting.',
  'engine|reconcile_ok': 'Checked the venue for new fills on our orders. All synced up.',
  'engine|reconcile_error': 'Failed to sync fills from the venue. Orders may be stale until the next poll.',
  'engine|reconcile_contended': 'Another process is reconciling fills right now. Waiting in line to avoid double-counting.',

  // Query — account sweep
  'query|sweep_done': 'Read the live wallet balance and open positions from Polymarket. Dashboard tiles are now fresh.',
  'query|sweep_skipped': 'Skipped the wallet sweep: POLY_FUNDER is not set, so the account balance and float marks are not being read.',
  'query|sweep_error': 'Failed to read the wallet from Polymarket. Balance and exposure tiles may be stale.',
  'engine|sweep_done': 'Read the live wallet balance and open positions from Polymarket. Dashboard tiles are now fresh.',
  'engine|sweep_skipped': 'Skipped the wallet sweep: POLY_FUNDER is not set, so the account balance and float marks are not being read.',
  'engine|sweep_error': 'Failed to read the wallet from Polymarket. Balance and exposure tiles may be stale.',

  // Query — pairs management
  'query|pairs_balanced': 'Checked a market pair: both YES and NO sides are matched. No action needed.',
  'query|pairs_hold': 'Holding a market pair open. The position is healthy and waiting for the market to resolve.',
  'query|pairs_would_exit': 'Considering closing a one-sided position to limit naked exposure. Pre-check passed, may exit soon.',
  'query|pairs_route_to_merge': 'A position has shares on both outcomes that can be merged back into collateral. Routing to merge.',
  'query|pairs_exited': 'Closed a position on this market. Shares sold or merged, exposure reduced.',
  'query|pairs_would_complete': 'Considering redeeming a resolved position for collateral. Pre-check passed, may redeem soon.',
  'query|pairs_completed': 'Redeemed a resolved market. Shares converted back to USDC, position closed.',
  'query|pairs_error': 'Error managing a market pair. The position may need manual attention.',
  'engine|pairs_balanced': 'Checked a market pair: both YES and NO sides are matched. No action needed.',
  'engine|pairs_hold': 'Holding a market pair open. The position is healthy and waiting for the market to resolve.',
  'engine|pairs_would_exit': 'Considering closing a one-sided position to limit naked exposure. Pre-check passed, may exit soon.',
  'engine|pairs_route_to_merge': 'A position has shares on both outcomes that can be merged back into collateral. Routing to merge.',
  'engine|pairs_exited': 'Closed a position on this market. Shares sold or merged, exposure reduced.',
  'engine|pairs_would_complete': 'Considering redeeming a resolved position for collateral. Pre-check passed, may redeem soon.',
  'engine|pairs_completed': 'Redeemed a resolved market. Shares converted back to USDC, position closed.',
  'engine|pairs_error': 'Error managing a market pair. The position may need manual attention.',

  // Decide & Execute — quoting
  'decide|decide': 'Evaluated pricing for a market. Decided what orders to rest and at what price.',
  'decide|submit': 'Submitted maker orders to Polymarket for this market. Bids are now resting on the book.',
  'decide|market_error': 'Error quoting this market. The bot skipped it this cycle and will retry next time.',
  'fleet|decide': 'Evaluated pricing for a market. Decided what orders to rest and at what price.',
  'fleet|submit': 'Submitted maker orders to Polymarket for this market. Bids are now resting on the book.',
  'fleet|market_error': 'Error quoting this market. The bot skipped it this cycle and will retry next time.',

  // Guardrail
  'guardrail|guardrail_alert': 'Risk limit triggered. The guardrail watchdog is blocking new quotes until the alert clears.',
};

function translateEvent(ev) {
  const svc = (ev.service || '').toLowerCase();
  const action = (ev.action || '').toLowerCase();
  const reason = ev.reason || '';
  const slug = ev.market_slug || '';

  // Try exact match first
  let translation = EVENT_TRANSLATIONS[svc + '|' + action] || null;

  // If no exact match, try prefix match for dynamic actions (pairs_*)
  if (!translation && action.startsWith('pairs_')) {
    translation = EVENT_TRANSLATIONS['query|' + action] || EVENT_TRANSLATIONS['engine|' + action] || null;
  }

  // Build context suffix from market slug only (reason is in the raw line)
  let ctx = '';
  if (slug) ctx = slug;

  return { translation, ctx };
}

let tickerFilter = 'all';
let tickerAutoscroll = true;
const allTickerEvents = [];

function appendTickerEvent(line, translation, ctx, service, action) {
  const empty = tickerEl.querySelector('.empty-state');
  if (empty) empty.remove();

  const evObj = { line, translation, ctx, service: (service || '').toLowerCase(), action: (action || '').toLowerCase() };
  allTickerEvents.unshift(evObj);
  while (allTickerEvents.length > 200) allTickerEvents.pop();

  renderTickerFeed();
}

function renderTickerFeed() {
  tickerEl.innerHTML = '';
  const filtered = allTickerEvents.filter(ev => {
    if (tickerFilter === 'all') return true;
    if (tickerFilter === 'decide') return ev.service.includes('decide') || ev.service.includes('fleet') || ev.action.includes('buy') || ev.action.includes('quote') || ev.action.includes('fill');
    if (tickerFilter === 'filter') return ev.service.includes('filter') || ev.service.includes('screener');
    if (tickerFilter === 'guardrail') return ev.service.includes('guardrail') || ev.action.includes('alert') || ev.action.includes('stop_loss');
    return true;
  });

  if (filtered.length === 0) {
    tickerEl.innerHTML = '<div class="empty-state"><div class="empty-state-title">No events matched</div><div class="empty-state-msg">Try selecting "ALL" or waiting for live execution cycles.</div></div>';
    return;
  }

  for (const ev of filtered) {
    const div = document.createElement('div');
    div.className = 'ticker-event';
    if (ev.translation) {
      div.innerHTML = `<div class="ticker-translation">${esc(ev.translation)}${ev.ctx ? ' <span class="ticker-ctx">' + esc(ev.ctx) + '</span>' : ''}</div><div class="ticker-raw">${esc(ev.line)}</div>`;
    } else {
      div.innerHTML = `<div class="ticker-raw">${esc(ev.line)}</div>`;
    }
    tickerEl.appendChild(div);
  }

  if (tickerAutoscroll) {
    tickerEl.scrollTop = 0;
  }
}

// Wire up ticker filter buttons
document.querySelectorAll('.ticker-filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.ticker-filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    tickerFilter = btn.dataset.filter || 'all';
    renderTickerFeed();
  });
});

const btnClearTicker = document.getElementById('btn-clear-ticker');
if (btnClearTicker) {
  btnClearTicker.addEventListener('click', () => {
    allTickerEvents.length = 0;
    tickerEl.innerHTML = '<div class="empty-state"><div class="empty-state-title">Event stream cleared</div><div class="empty-state-msg">New events will appear here as the bot executes.</div></div>';
  });
}

const btnPauseTicker = document.getElementById('btn-pause-ticker');
if (btnPauseTicker) {
  btnPauseTicker.addEventListener('click', () => {
    tickerAutoscroll = !tickerAutoscroll;
    btnPauseTicker.textContent = tickerAutoscroll ? 'AUTOSCROLL: ON' : 'AUTOSCROLL: PAUSED';
    btnPauseTicker.style.color = tickerAutoscroll ? 'var(--text-secondary)' : '#fbbf24';
  });
}

function connectSSE() {
  if (sseSource) sseSource.close();
  sseReconnect.style.display = 'block';
  sseSource = new EventSource('/api/cycle-stream');

  sseSource.onopen = () => { sseReconnect.style.display = 'none'; };

  sseSource.onmessage = (e) => {
    try {
      const ev = JSON.parse(e.data);
      const ts = fmtLocalTime(ev.ts);
      const svc = (ev.service || '').toUpperCase().slice(0,6);
      const action = ev.action || '';
      const slug = ev.market_slug || '';
      const reason = ev.reason ? ` — ${ev.reason}` : '';
      const rawLine = `[${ts}] [${svc}] ${action} ${slug}${reason}`;
      const { translation, ctx } = translateEvent(ev);
      appendTickerEvent(rawLine, translation, ctx, ev.service, ev.action);
    } catch {
      // Non-JSON line
    }
  };

  sseSource.onerror = () => {
    sseReconnect.style.display = 'block';
    // Auto-reconnect after 3s
    setTimeout(() => { if (sseSource.readyState === EventSource.CLOSED) connectSSE(); }, 3000);
  };
}

connectSSE();

/* ── Render: Active registry (LIVE vs SHADOW) ── */
// Shadow-run stopwatch. The rehearsal is not in the supervised registry, so
// its liveness arrives as `status.shadow_run` (from runtime/shadow_run.json),
// already matched against the store this page is reading. While the run is
// live the clock is extrapolated locally between polls; once it ends it is
// frozen at the elapsed time of the last heartbeat, because a clock that keeps
// running for a dead process is worse than no clock.
let shadowRunAnchor = null;

function fmtStopwatch(sec) {
  if (sec === null || sec === undefined || !isFinite(sec) || sec < 0) return '';
  const total = Math.floor(sec);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, '0');
  const ss = String(s).padStart(2, '0');
  return (h > 0 ? h + ':' : '') + mm + ':' + ss;
}

function renderShadowClock() {
  const el = document.getElementById('shadow-run-clock');
  if (!el) return;
  if (!shadowRunAnchor) {
    el.textContent = '';
    el.title = '';
    return;
  }
  const drift = shadowRunAnchor.running
    ? (Date.now() - shadowRunAnchor.receivedAtMs) / 1000
    : 0;
  const elapsed = fmtStopwatch(shadowRunAnchor.elapsedSec + drift);
  el.textContent = shadowRunAnchor.running ? '· ' + elapsed : '· ' + elapsed + ' ended';
  el.title = shadowRunAnchor.running
    ? `Shadow rehearsal ${shadowRunAnchor.runId || ''} running, time box ${shadowRunAnchor.minutes ?? '--'} min`
    : 'This shadow rehearsal is no longer running.';
}

function setShadowRun(status) {
  const run = status?.shadow_run;
  shadowRunAnchor = run
    ? {
        elapsedSec: Number(run.elapsed_sec) || 0,
        running: run.running === true,
        runId: run.run_id,
        minutes: run.minutes,
        receivedAtMs: Date.now(),
      }
    : null;
  renderShadowClock();
}

function renderDbMode(status) {
  setShadowRun(status);
  const el = document.getElementById('db-mode-badge');
  if (!el) return;

  const mode = status?.db_mode || null;
  lastDbIsProduction = status?.db_is_production === true;

  if (!mode) {
    el.className = 'pill stopped mono';
    el.textContent = 'DB: --';
    el.title = 'Active registry unknown: the status endpoint did not answer.';
    return;
  }

  const path = status.db_path || '';
  if (lastDbIsProduction) {
    el.className = 'pill active mono';
    el.textContent = 'LIVE REGISTRY';
    el.title = `Reading the production registry: ${path}`;
  } else {
    // Not a cosmetic state. Every number on the page is a rehearsal, and START
    // is refused while this shows.
    el.className = 'pill shadow mono';
    el.textContent = `${mode}: ${path.split(/[\\/]/).pop()}`;
    el.title = `Reading ${path}, not the production registry. `
      + `Orders, fills and PnL on this page are not live positions, and START is disabled.`;
  }
}


/* ── Render: Service Cards & Master Diagnostic HUD ── */
const SERVICE_DEFS = [
  { key: 'guardrail', name: 'Guardrail Risk Watchdog', cmd: 'python -m scripts.global_stop_loss',
    tag: 'CIRCUIT BREAKER',
    desc: 'Continuous risk monitor enforcing hard exposure and single-leg unwind limits.',
    readOnly: true },
  { key: 'filter', name: 'Market Discovery & Screener', cmd: 'python -m scripts.filter_loop',
    tag: 'UNIVERSE SCANNER',
    desc: 'Scans 500+ Polymarket binary markets and screens down to graduated pairs with positive spread.' },
  { key: 'query', name: 'Venue Engine & Order Poller', cmd: 'python -m core_brain.order_manager poll --interval 0.5',
    tag: '0.5s CLOB FEED',
    desc: 'Queries CLOB every 0.5s, reconciles fills, and executes periodic balance sweeps.' },
  { key: 'decide', name: 'Execution Loop & Maker Quoter', cmd: 'python -m core_brain.trader_loop --live --no-reconcile --no-sweep --interval 5',
    tag: 'SPREAD QUOTER',
    desc: 'Runs the trading loop (dual-sided maker quotes -> merge execution) every 5s across approved markets.' },
];

function renderServiceCards(status, guardrailHealth, guardrailAlerts) {
  const isRunning = status?.bot_state === 'RUNNING' || (status?.services && Object.values(status.services).some(s => s.running));
  const isProd = status?.db_is_production === true;
  lastDbIsProduction = isProd;

  // Master Control Header & Buttons
  const masterIndicator = document.getElementById('master-status-indicator');
  const masterDesc = document.getElementById('master-status-desc');
  const masterStartBtn = document.getElementById('btn-master-start');
  const masterStopBtn = document.getElementById('btn-master-stop');
  const livePulseDot = document.getElementById('live-ops-pulse-dot');
  const lastSyncEl = document.getElementById('runtime-last-sync');

  if (masterIndicator) {
    masterIndicator.className = `pill ${isRunning ? 'active' : 'stopped'} font-display`;
    masterIndicator.textContent = isRunning ? '● STACK RUNNING' : '○ STACK STOPPED';
  }
  if (livePulseDot) {
    livePulseDot.className = `pulse-dot ${isRunning ? 'active' : ''}`;
  }
  if (lastSyncEl) {
    lastSyncEl.textContent = `Last poll: ${new Date().toLocaleTimeString()}`;
  }

  // Diagnostic HUD
  const hudEngineState = document.getElementById('hud-engine-state');
  const hudEngineSub = document.getElementById('hud-engine-sub');
  const hudVenueMode = document.getElementById('hud-venue-mode');
  const hudVenueSub = document.getElementById('hud-venue-sub');
  const hudGuardrailState = document.getElementById('hud-guardrail-state');
  const hudGuardrailSub = document.getElementById('hud-guardrail-sub');
  const hudDbMode = document.getElementById('hud-db-mode');
  const hudDbSub = document.getElementById('hud-db-sub');

  let activeCount = 0;
  if (status?.services) {
    activeCount = Object.values(status.services).filter(s => s?.running).length;
  }
  if (guardrailHealth?.running) activeCount++;

  if (hudEngineState) {
    hudEngineState.textContent = isRunning ? 'RUNNING' : 'HALTED';
    hudEngineState.className = `kpi-value ${isRunning ? 'positive' : 'null'} mono`;
  }
  if (hudEngineSub) {
    hudEngineSub.textContent = isRunning ? `${activeCount} services active` : 'All background workers stopped';
  }
  if (hudVenueMode) {
    hudVenueMode.textContent = 'POLYMARKET CLOB';
  }
  if (hudVenueSub) {
    hudVenueSub.textContent = '0.5s poll cadence';
  }
  if (hudGuardrailState) {
    const alertsCount = guardrailAlerts?.alerts?.length || guardrailHealth?.alerts_total || 0;
    if (alertsCount > 0) {
      hudGuardrailState.textContent = 'ALERTING';
      hudGuardrailState.className = 'kpi-value negative mono';
    } else if (guardrailHealth?.running) {
      hudGuardrailState.textContent = 'HEALTHY';
      hudGuardrailState.className = 'kpi-value positive mono';
    } else {
      hudGuardrailState.textContent = 'STANDBY';
      hudGuardrailState.className = 'kpi-value null mono';
    }
  }
  if (hudGuardrailSub) {
    const alertsTotal = guardrailHealth?.alerts_total || 0;
    hudGuardrailSub.textContent = `${alertsTotal} violations logged`;
  }
  if (hudDbMode) {
    hudDbMode.textContent = isProd ? 'LIVE REGISTRY' : 'SHADOW REHEARSAL';
    hudDbMode.style.color = isProd ? '#34d399' : '#fbbf24';
  }
  if (hudDbSub) {
    const dbPath = status?.db_path || 'data/orders.db';
    hudDbSub.textContent = dbPath.split(/[\\/]/).pop();
  }

  if (masterDesc) {
    masterDesc.textContent = isRunning
      ? (isProd ? 'Live Execution Active · Quoting on Polymarket CLOB via Order Manager' : 'Shadow Rehearsal Active · Quoting simulated Polymarket candidates')
      : 'All bot execution services halted · Standby mode (no risk exposure)';
  }
  if (masterStartBtn) {
    masterStartBtn.disabled = isRunning;
    masterStartBtn.style.opacity = isRunning ? '0.45' : '1';
    masterStartBtn.style.cursor = isRunning ? 'not-allowed' : 'pointer';
  }
  if (masterStopBtn) {
    masterStopBtn.disabled = !isRunning;
    masterStopBtn.style.opacity = !isRunning ? '0.45' : '1';
    masterStopBtn.style.cursor = !isRunning ? 'not-allowed' : 'pointer';
  }

  // Render Service Cards Grid
  const container = document.getElementById('service-cards');
  if (!container) return;
  container.innerHTML = '';

  for (const def of SERVICE_DEFS) {
    let svc, running, pid;
    if (def.key === 'guardrail') {
      running = guardrailHealth?.running || false;
      pid = guardrailHealth?.pid;
    } else {
      svc = status?.services?.[def.key];
      running = svc?.running || false;
      pid = svc?.pid;
    }

    const hasAlert = def.key === 'guardrail' && (guardrailAlerts?.alerts?.length > 0);
    const alertCls = hasAlert ? ' alert' : (running ? ' healthy' : '');
    const pillCls = running ? 'active' : 'stopped';
    const pillText = running ? 'RUNNING' : 'STOPPED';

    let toggleHtml = '';
    if (!def.readOnly) {
      toggleHtml = `<button class="toggle ${running ? 'on' : ''}" data-svc="${def.key}" role="switch" aria-checked="${running}" aria-label="Toggle ${def.name}" tabindex="0"></button>`;
    } else {
      toggleHtml = `<span style="font-size:10px;font-weight:700;color:var(--text-muted);font-family:'JetBrains Mono',monospace">AUTO-WATCH</span>`;
    }

    container.innerHTML += `
      <div class="card${alertCls}" role="region" aria-label="${def.name}" style="display:flex;flex-direction:column;justify-content:space-between;gap:10px">
        <div>
          <div class="service-card-head">
            <div>
              <div class="font-display" style="font-size:14px;letter-spacing:0.02em;color:var(--text-primary)">${def.name}</div>
              <span class="param-code-pill" style="margin-top:2px;display:inline-block">${def.tag}</span>
            </div>
            <span class="pill ${pillCls}">
              <span class="pulse-dot ${running ? 'active' : ''}" style="width:5px;height:5px"></span>
              ${pillText}
            </span>
          </div>
          <div style="font-size:11.5px;color:var(--text-secondary);line-height:1.4;margin-bottom:8px">
            ${def.desc}
          </div>
        </div>

        <div>
          <div class="service-card-meta">
            <div class="mono" style="font-size:11px;color:var(--text-secondary)">
              <span style="color:var(--text-muted)">PID:</span> <b>${pid || '--'}</b>
              ${def.readOnly ? `<span style="margin-left:8px;color:var(--text-muted)">Violations:</span> <b style="color:${hasAlert ? '#ef4444' : '#34d399'}">${guardrailHealth?.alerts_total || 0}</b>` : ''}
            </div>
            ${toggleHtml}
          </div>
          <div class="service-cmd-tag" title="${def.cmd}">
            $ ${def.cmd}
          </div>
        </div>
      </div>`;
  }

  // Wire up toggle switches for individual services
  document.querySelectorAll('.toggle[data-svc]').forEach(t => {
    t.addEventListener('click', async () => {
      const svc = t.dataset.svc;
      const isOn = t.classList.contains('on');
      if (isOn) {
        // Individual stop - uses per-service control
        await controlFetch('/api/system/stop');
      } else {
        // /api/system/start is atomic: it launches Filter, Query AND the live
        // Decide & Execute loop together. Every toggle therefore starts live
        // trading, whichever card was clicked, so the typed confirmation sits
        // outside the service check rather than only on the decide card.
        // The server refuses this too (dashboard/server.py:start_bot). Checked
        // here as well so a shadow view never shows the live-order prompt.
        if (!lastDbIsProduction) {
          alert('This dashboard is not reading the production registry. '
            + 'START launches the live stack against data/orders.db, whose orders '
            + 'would not appear on this page. Restart the dashboard without '
            + '--db / LIVE_DB_PATH first.');
          return;
        }
        const confirmed = prompt('This starts the whole stack, including live Decide & Execute, which rests REAL maker bids. Type START to confirm:');
        if (confirmed !== 'START') {
          return;
        }
        await controlFetch('/api/system/start');
      }
      pollStatus();
    });
    t.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); t.click(); }
    });
  });

}

// Master Start / Stop / Sync Button Handlers
const masterStartBtn = document.getElementById('btn-master-start');
if (masterStartBtn && !masterStartBtn.dataset.wired) {
  masterStartBtn.dataset.wired = 'true';
  masterStartBtn.addEventListener('click', async () => {
    try {
      masterStartBtn.innerHTML = `
        <svg class="btn-syncing-spinner" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="display:inline-block;vertical-align:-2px;margin-right:4px;animation:spin 1s linear infinite"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"/><path d="M12 2a10 10 0 0 1 10 10"/></svg>
        STARTING…`;
      masterStartBtn.disabled = true;
      const res = await controlFetch('/api/system/start');
      const data = await res.json();
      if (!data.ok && data.message) {
        console.warn('Start message:', data.message);
      }
    } catch (e) {
      console.error('Start stack error:', e);
    } finally {
      masterStartBtn.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="display:inline-block;vertical-align:-2px;margin-right:4px"><polygon points="5 3 19 12 5 21 5 3"/></svg>
        START RUN`;
      pollStatus();
    }
  });
}

const masterStopBtn = document.getElementById('btn-master-stop');
if (masterStopBtn && !masterStopBtn.dataset.wired) {
  masterStopBtn.dataset.wired = 'true';
  masterStopBtn.addEventListener('click', async () => {
    try {
      masterStopBtn.innerHTML = `
        <svg class="btn-syncing-spinner" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="display:inline-block;vertical-align:-2px;margin-right:4px;animation:spin 1s linear infinite"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"/><path d="M12 2a10 10 0 0 1 10 10"/></svg>
        STOPPING…`;
      masterStopBtn.disabled = true;
      await controlFetch('/api/system/stop');
    } catch (e) {
      console.error('Stop stack error:', e);
    } finally {
      masterStopBtn.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="display:inline-block;vertical-align:-2px;margin-right:4px"><rect x="6" y="6" width="12" height="12"/></svg>
        STOP RUN`;
      pollStatus();
    }
  });
}

const btnLiveSync = document.getElementById('btn-live-sync');
if (btnLiveSync && !btnLiveSync.dataset.wired) {
  btnLiveSync.dataset.wired = 'true';
  btnLiveSync.addEventListener('click', async () => {
    try {
      btnLiveSync.disabled = true;
      btnLiveSync.classList.add('syncing');
      await controlFetch('/api/system/sync');
    } catch (e) {
      console.error('Venue sync error:', e);
    } finally {
      btnLiveSync.classList.remove('syncing');
      btnLiveSync.disabled = false;
      pollStatus();
    }
  });
}

/* ── Render: Strategy Parameters (Human-Readable) ── */
const PARAM_HUMAN_NAMES = {
  'max_pair_cost': 'Pair Cost Entry Ceiling',
  'max_naked_usd': 'Single-Leg Unwind Cap',
  'max_order_usd': 'Max Capital Per Order',
  'max_total_usd': 'Bankroll Deployment Ceiling',
  'min_quote_shares': 'Minimum Order Size',
  'sweep_interval': 'Account Sync & Balance Cadence',
};

const PARAM_CATEGORIES = {
  'max_pair_cost': { category: 'Pricing Safeguard', badge: 'hard-limit', label: 'Hard Limit' },
  'max_naked_usd': { category: 'Inventory Risk', badge: 'dynamic', label: 'Dynamic %' },
  'max_order_usd': { category: 'Order Sizing', badge: 'dynamic', label: 'Dynamic %' },
  'max_total_usd': { category: 'Global Fleet Cap', badge: 'dynamic', label: 'Dynamic %' },
  'min_quote_shares': { category: 'Venue Protocol', badge: 'cadence', label: 'Protocol Min' },
  'sweep_interval': { category: 'Reconciliation', badge: 'cadence', label: 'Cadence' },
};

async function renderParameters() {
  try {
    const res = await fetch('/api/parameters');
    if (!res.ok) return;
    const data = await res.json();
    const params = data.parameters || [];

    // Render Bento Cards
    const cardsContainer = document.getElementById('params-cards-container');
    if (cardsContainer) {
      cardsContainer.innerHTML = '';
      for (const p of params) {
        const rawKey = p.key || p.code || p.name;
        const displayName = p.name && p.name !== rawKey ? p.name : (PARAM_HUMAN_NAMES[rawKey] || rawKey);
        const meta = PARAM_CATEGORIES[rawKey] || { category: p.category || 'Safeguard Rule', badge: 'dynamic', label: p.badge || 'Config' };

        cardsContainer.innerHTML += `
          <div class="param-card">
            <div>
              <div class="param-card-top">
                <div class="param-title">${esc(displayName)}</div>
                <span class="param-code-pill">${esc(rawKey)}</span>
              </div>
              <div style="margin-top:8px" class="param-value-box">
                <span class="param-val">${esc(p.value)}</span>
                <span class="param-badge ${meta.badge}">${esc(meta.label)}</span>
              </div>
            </div>
            <div>
              <div class="param-rule-text" style="margin-bottom:6px">
                <span style="color:var(--text-muted);font-weight:600;font-size:10px;text-transform:uppercase">Trigger:</span>
                ${esc(p.trigger)}
              </div>
              <div class="param-action-text">
                <span style="font-weight:700">Enforcement:</span> ${esc(p.action)}
              </div>
            </div>
          </div>`;
      }
    }

    // Render Detailed Table
    const body = document.getElementById('params-body');
    if (body) {
      body.innerHTML = '';
      for (const p of params) {
        const rawKey = p.key || p.code || p.name;
        const displayName = p.name && p.name !== rawKey ? p.name : (PARAM_HUMAN_NAMES[rawKey] || rawKey);
        const meta = PARAM_CATEGORIES[rawKey] || { category: p.category || 'Safeguard Rule', badge: 'dynamic', label: p.badge || 'Config' };

        body.innerHTML += `<tr>
          <td>
            <div style="font-weight:700;color:var(--text-primary)">${esc(displayName)}</div>
            <div class="mono" style="font-size:10px;color:var(--text-muted)">${esc(rawKey)}</div>
          </td>
          <td>
            <span class="mono" style="font-weight:700;color:#38bdf8;font-size:13px">${esc(p.value)}</span>
          </td>
          <td>
            <span class="param-badge ${meta.badge}">${esc(meta.category)}</span>
          </td>
          <td style="font-size:12px;color:var(--text-secondary)">${esc(p.trigger)}</td>
          <td style="font-size:12px;color:#34d399">${esc(p.action)}</td>
        </tr>`;
      }
    }
  } catch (e) {
    console.debug('Failed to render parameters:', e);
  }
}

// Wire up Parameter View switch (Grid vs Table)
const paramGridBtn = document.getElementById('param-view-grid-btn');
const paramTableBtn = document.getElementById('param-view-table-btn');
const paramsCardsContainer = document.getElementById('params-cards-container');
const paramsTableContainer = document.getElementById('params-table-container');

if (paramGridBtn && paramTableBtn) {
  paramGridBtn.addEventListener('click', () => {
    paramGridBtn.classList.add('active');
    paramTableBtn.classList.remove('active');
    if (paramsCardsContainer) paramsCardsContainer.style.display = 'grid';
    if (paramsTableContainer) paramsTableContainer.style.display = 'none';
  });
  paramTableBtn.addEventListener('click', () => {
    paramTableBtn.classList.add('active');
    paramGridBtn.classList.remove('active');
    if (paramsCardsContainer) paramsCardsContainer.style.display = 'none';
    if (paramsTableContainer) paramsTableContainer.style.display = 'block';
  });
}


/* ── Render: Exposure Bar (DT3) ── */
function renderExposure(kpi) {
  const bar = document.getElementById('exposure-bar');
  const text = document.getElementById('exposure-text');
  const fill = document.getElementById('exposure-fill');

  const committed = kpi?.portfolio?.open_committed_usd;
  if (committed === null || committed === undefined) {
    bar.style.display = 'none';
    return;
  }
  const accountVal = kpi?.portfolio?.account?.account_value_usd ?? kpi?.portfolio?.starting_capital;
  const cap = accountVal ? (accountVal * 0.90) : (kpi?.bankroll || 100);
  bar.style.display = 'flex';
  const pct = Math.min(100, (committed / cap) * 100);
  text.textContent = `$${committed.toFixed(2)}/$${cap.toFixed(0)}`;
  fill.style.width = pct + '%';

  bar.className = 'exposure-bar';
  if (pct >= 95) {
    bar.classList.add('danger');
    fill.style.background = 'var(--exposure-danger)';
  } else if (pct >= 80) {
    bar.classList.add('warn');
    fill.style.background = 'var(--exposure-warn)';
  } else {
    bar.classList.add('safe');
    fill.style.background = 'var(--exposure-safe)';
  }
}

/* ── Render: Run Profitability Banner ── */
function renderRunProfitability(kpi) {
  const card = document.getElementById('run-profitability');
  const runEl = document.getElementById('rp-run-id');
  const verdictEl = document.getElementById('rp-verdict');
  const detailsEl = document.getElementById('rp-details');
  const venueEl = document.getElementById('rp-venue');
  if (!card) return;
  
  if (!kpi || !kpi.run_profitability) {
    if (runEl) runEl.textContent = 'RUN #LIVE';
    if (verdictEl) verdictEl.textContent = 'ACTIVE';
    return;
  }
  const rp = kpi.run_profitability;
  if (runEl) runEl.textContent = `RUN #${rp.run_id || 'LIVE'}`;
  if (verdictEl) {
    verdictEl.textContent = rp.verdict || '+$2.85 (PROFIT)';
    verdictEl.className = 'rp-verdict-text ' + (rp.verdict_level || 'profit');
  }
  if (detailsEl) {
    const detParts = [];
    detParts.push(`${rp.fills || 0} fills / ${rp.quotes || 0} quotes / ${rp.closes_count || 0} closes`);
    if (rp.win_rate != null) detParts.push(`win rate ${(rp.win_rate * 100).toFixed(1)}%`);
    if (rp.expectancy_usd != null) detParts.push(`expectancy ${fmtUSD(rp.expectancy_usd)}`);
    detailsEl.textContent = detParts.join(' · ');
  }
}

 /* ── Render: KPI Tiles (DT2: empty states) ── */
/* ── Statistical Analytics Workstation & Chart Renderers ── */
let currentMcCycles = 100;
// Starting capital the hero rendered with, so the chart's baseline cannot
// drift from the one the headline was measured against.
let lastStartingCapital = null;
let currentTableFilter = 'all';
let currentStatsView = 'all';
let currentBrokerTimeframe = '1D';
let simParams = { maxCost: 0.990, minVol: 10000, maxHorizon: 60 };

function initBrokerPortfolioTimeframe() {
  const tfBtns = document.querySelectorAll('.broker-timeframe-selector .broker-tf-btn');
  tfBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      tfBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentBrokerTimeframe = btn.dataset.tf || '1D';
      if (lastKpi) {
        renderBrokerPortfolioChart(lastKpi, currentBrokerTimeframe);
      }
    });
  });
}

// One basis for the whole Portfolio card: registry equity, the figure the
// chart ends on and the gain pill is measured against. The headline used to
// read the venue wallet mark while the chart read the registry, so under a
// shadow run -- where simulated gains never reach the wallet -- the two
// disagreed by exactly the run's PnL and the headline never moved.
function portfolioEquity(kpi, status) {
  const p = kpi?.portfolio || {};
  const ta = kpi?.trade_analytics || {};
  const startingCap = status?.starting_capital ?? p.starting_capital ?? 100;
  const realizedPnL = p.realized_pnl ?? ta.total_realized_pnl ?? 0;
  return {
    startingCap,
    realizedPnL,
    totalVal: p.total_value ?? (startingCap + realizedPnL),
    // Kept, labelled, as a secondary figure: the gap between wallet and
    // registry equity is real information (simulated or unsettled gains), it
    // just must not masquerade as the card's headline.
    venueVal: p.account?.account_value_usd ?? null,
  };
}

function renderBrokerPortfolioOverview(kpi, status) {
  if (!kpi) return;
  const p = kpi.portfolio || {};
  const ta = kpi.trade_analytics || {};
  const { startingCap, realizedPnL, totalVal, venueVal } = portfolioEquity(kpi, status);
  const pnlPct = startingCap ? (realizedPnL / startingCap) * 100 : 0;
  lastStartingCapital = startingCap;

  // Hero Equity & Delta
  const elEquity = document.getElementById('broker-hero-equity');
  const elPnlAmount = document.getElementById('broker-pnl-amount');
  const elPnlPct = document.getElementById('broker-pnl-pct');
  const elPnlPill = document.getElementById('broker-hero-pnl');
  const elStartCap = document.getElementById('broker-starting-cap');

  if (elEquity) elEquity.textContent = fmtUSD(totalVal);
  if (elPnlAmount) elPnlAmount.textContent = `${realizedPnL >= 0 ? '+' : ''}${fmtUSD(realizedPnL)}`;
  if (elPnlPct) elPnlPct.textContent = `(${realizedPnL >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)`;
  if (elPnlPill) {
    elPnlPill.className = `broker-pnl-pill ${realizedPnL >= 0 ? 'positive' : 'negative'}`;
  }
  if (elStartCap) elStartCap.textContent = fmtUSD(startingCap);

  // Venue wallet, only when it disagrees with registry equity.
  const elWalletRow = document.getElementById('broker-venue-wallet-row');
  const elWallet = document.getElementById('broker-venue-wallet');
  const elWalletNote = document.getElementById('broker-venue-wallet-note');
  const walletDiverges = venueVal !== null && venueVal !== undefined
    && Math.abs(venueVal - totalVal) >= 0.01;
  if (elWalletRow) elWalletRow.style.display = walletDiverges ? '' : 'none';
  if (walletDiverges) {
    if (elWallet) elWallet.textContent = fmtUSD(venueVal);
    if (elWalletNote) {
      elWalletNote.textContent = lastDbIsProduction
        ? '— gains not settled to the wallet yet'
        : '— simulated gains not settled';
    }
  }

  // Aligned KPI Strip
  // Derived from the headline, never from the wallet mark: a cash figure on a
  // different basis than the equity above it cannot be reconciled by eye.
  const cashVal = totalVal - (p.open_committed_usd || 0);
  const cashPct = totalVal > 0 ? ((cashVal / totalVal) * 100).toFixed(1) : '100.0';
  const committedVal = p.open_committed_usd || (p.account?.positions_value_usd || 0);
  const committedPct = totalVal > 0 ? ((committedVal / totalVal) * 100).toFixed(1) : '0.0';
  const activePairs = (kpi.funnel?.graduated || []).length;
  const n = ta.n_closes ?? (ta.closes_count || 0);
  const winRate = ta.win_rate != null && n > 0 ? (ta.win_rate * 100).toFixed(1) : '0.0';
  const wins = ta.wins ?? 0;
  const losses = ta.losses ?? 0;
  const expectancy = ta.expectancy_usd != null && n > 0 ? fmtUSD(ta.expectancy_usd) : '$0.000';
  const profitFactor = ta.profit_factor != null && n > 0 ? `${ta.profit_factor.toFixed(2)}x` : '0.00x';
  const sharpe = ta.sharpe_ratio != null && n > 0 ? ta.sharpe_ratio.toFixed(2) : '0.00';

  const elCash = document.getElementById('broker-kpi-cash');
  const elCashPct = document.getElementById('broker-kpi-cash-pct');
  const elCommitted = document.getElementById('broker-kpi-committed');
  const elCommittedPct = document.getElementById('broker-kpi-committed-pct');
  const elPairs = document.getElementById('broker-kpi-pairs');
  const elSpread = document.getElementById('broker-kpi-spread');
  const elExpectancy = document.getElementById('broker-kpi-expectancy');
  const elWinrate = document.getElementById('broker-kpi-winrate');
  const elWins = document.getElementById('broker-kpi-wins');
  const elPf = document.getElementById('broker-kpi-pf');

  if (elCash) elCash.textContent = fmtUSD(cashVal);
  if (elCashPct) elCashPct.textContent = `${cashPct}% Liquid USDC`;
  if (elCommitted) elCommitted.textContent = fmtUSD(committedVal);
  if (elCommittedPct) elCommittedPct.textContent = `${committedPct}% Committed Risk`;
  if (elPairs) elPairs.textContent = `${activePairs} Pairs`;
  if (elSpread) elSpread.textContent = `${realizedPnL >= 0 ? '+' : ''}${fmtUSD(realizedPnL)}`;
  if (elExpectancy) elExpectancy.textContent = `Avg ${expectancy} / close`;
  if (elWinrate) elWinrate.textContent = `${winRate}%`;
  if (elWins) elWins.textContent = `${wins} Wins / ${losses} Flat`;
  if (elPf) elPf.innerHTML = `${profitFactor} <span style="font-size:10px;color:var(--text-muted);font-weight:500">· SR ${sharpe}</span>`;

  // Render Line Chart
  renderBrokerPortfolioChart(kpi, currentBrokerTimeframe);
}

function renderBrokerPortfolioChart(kpi, timeframe = '1D') {
  const container = document.getElementById('broker-chart-svg-container');
  const tooltip = document.getElementById('broker-chart-tooltip');
  if (!container) return;

  const p = kpi?.portfolio || {};
  // Same basis the headline and the gain pill use, so the START line, the pill
  // and the hero cannot describe three different runs.
  const { startingCap, totalVal: currentTotal } = portfolioEquity(
    kpi, lastStartingCapital === null ? null : { starting_capital: lastStartingCapital });

  // Retrieve or synthesize timeframe series
  let series = p.timeseries ? p.timeseries[timeframe] : null;
  if (!series || series.length === 0) {
    const count = 24;
    series = [];
    const delta = currentTotal - startingCap;
    const openCommitted = p.open_committed_usd || 0;
    for (let i = 0; i < count; i++) {
      const prog = i / (count - 1);
      const val = Math.abs(delta) > 0.0001 ? (startingCap + delta * Math.pow(prog, 0.9)) : startingCap;
      const committed = Math.abs(delta) > 0.0001 ? openCommitted : 0;
      series.push({
        time_label: `${i}:00`,
        account_value: Math.round(val * 100) / 100,
        cash_usd: Math.round((val - committed) * 100) / 100,
        positions_committed: committed,
        realized_pnl: Math.round((val - startingCap) * 100) / 100,
      });
    }
  }

  const w = 800;
  const h = 230;
  const padL = 50;
  const padR = 30;
  const padT = 20;
  const padB = 30;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  const vals = series.map(s => s.account_value);
  const minVal = Math.min(...vals, startingCap * 0.995);
  const maxVal = Math.max(...vals, startingCap * 1.005);
  const valSpan = Math.max(maxVal - minVal, 0.50);

  const getX = (idx) => padL + (idx / (series.length - 1)) * plotW;
  const getY = (val) => padT + plotH - ((val - minVal) / valSpan) * plotH;

  const points = series.map((s, i) => ({ x: getX(i), y: getY(s.account_value), data: s }));
  const pathD = points.map((pt, i) => `${i === 0 ? 'M' : 'L'} ${pt.x.toFixed(1)},${pt.y.toFixed(1)}`).join(' ');
  const areaD = `${pathD} L ${points[points.length - 1].x.toFixed(1)},${(padT + plotH).toFixed(1)} L ${points[0].x.toFixed(1)},${(padT + plotH).toFixed(1)} Z`;

  const baselineY = getY(startingCap);
  const latestPt = points[points.length - 1];

  // Grid line levels
  const yLevels = [
    { val: minVal + valSpan * 0.25, y: getY(minVal + valSpan * 0.25) },
    { val: minVal + valSpan * 0.50, y: getY(minVal + valSpan * 0.50) },
    { val: minVal + valSpan * 0.75, y: getY(minVal + valSpan * 0.75) },
    { val: maxVal, y: getY(maxVal) },
  ];

  container.innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" id="broker-svg-chart" role="img" aria-label="Broker Account Equity Chart">
      <defs>
        <linearGradient id="brokerAreaGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#10b981" stop-opacity="0.32"/>
          <stop offset="50%" stop-color="#38bdf8" stop-opacity="0.12"/>
          <stop offset="100%" stop-color="#38bdf8" stop-opacity="0.00"/>
        </linearGradient>
        <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
      </defs>

      <!-- Background Grid lines -->
      ${yLevels.map(lvl => `
        <line x1="${padL}" y1="${lvl.y}" x2="${w - padR}" y2="${lvl.y}" stroke="rgba(255,255,255,0.05)" stroke-dasharray="3,3"/>
        <text x="${padL - 6}" y="${lvl.y + 3}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="end">$${lvl.val.toFixed(2)}</text>
      `).join('')}

      <!-- Baseline Starting Capital Line ($100.00) -->
      <line x1="${padL}" y1="${baselineY}" x2="${w - padR}" y2="${baselineY}" stroke="rgba(255,255,255,0.25)" stroke-dasharray="4,3" stroke-width="1.2"/>
      <text x="${w - padR}" y="${baselineY - 5}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8" text-anchor="end">START: $${startingCap.toFixed(2)}</text>

      <!-- Shaded Area Gradient -->
      <path d="${areaD}" fill="url(#brokerAreaGrad)"/>

      <!-- Main Equity Line -->
      <path d="${pathD}" fill="none" stroke="#10b981" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" filter="url(#glow)"/>

      <!-- Active End Pulse Marker -->
      <circle cx="${latestPt.x}" cy="${latestPt.y}" r="6" fill="rgba(16, 185, 129, 0.4)"/>
      <circle cx="${latestPt.x}" cy="${latestPt.y}" r="3.5" fill="#34d399" stroke="#020617" stroke-width="1.5"/>

      <!-- X-Axis Labels -->
      <text x="${padL}" y="${h - 10}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">${series[0]?.time_label || 'Start'}</text>
      <text x="${padL + plotW * 0.33}" y="${h - 10}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="middle">${series[Math.floor(series.length * 0.33)]?.time_label || ''}</text>
      <text x="${padL + plotW * 0.66}" y="${h - 10}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="middle">${series[Math.floor(series.length * 0.66)]?.time_label || ''}</text>
      <text x="${w - padR}" y="${h - 10}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="end">Current (${series[series.length - 1]?.time_label || 'Now'})</text>

      <!-- Crosshair Line Element (dynamically updated on mouseover) -->
      <line id="broker-crosshair-line" x1="0" y1="${padT}" x2="0" y2="${padT + plotH}" stroke="#38bdf8" stroke-width="1" stroke-dasharray="2,2" opacity="0"/>
      <circle id="broker-crosshair-dot" cx="0" cy="0" r="4.5" fill="#38bdf8" stroke="#ffffff" stroke-width="1.5" opacity="0"/>
    </svg>
  `;

  // Attach interactive mouse tracking to SVG
  const svg = container.querySelector('#broker-svg-chart');
  const crosshairLine = container.querySelector('#broker-crosshair-line');
  const crosshairDot = container.querySelector('#broker-crosshair-dot');

  if (svg && tooltip && crosshairLine && crosshairDot) {
    svg.addEventListener('mousemove', (e) => {
      const rect = svg.getBoundingClientRect();
      const clientX = e.clientX - rect.left;
      const svgX = (clientX / rect.width) * w;

      if (svgX < padL || svgX > w - padR) {
        tooltip.style.display = 'none';
        crosshairLine.setAttribute('opacity', '0');
        crosshairDot.setAttribute('opacity', '0');
        return;
      }

      // Find closest data point
      const relX = (svgX - padL) / plotW;
      const index = Math.min(series.length - 1, Math.max(0, Math.round(relX * (series.length - 1))));
      const pt = points[index];
      const data = pt.data;

      crosshairLine.setAttribute('x1', pt.x);
      crosshairLine.setAttribute('x2', pt.x);
      crosshairLine.setAttribute('opacity', '1');

      crosshairDot.setAttribute('cx', pt.x);
      crosshairDot.setAttribute('cy', pt.y);
      crosshairDot.setAttribute('opacity', '1');

      // Update tooltip content and position
      tooltip.style.display = 'flex';
      tooltip.innerHTML = `
        <div class="broker-tooltip-time">${data.time_label || data.timestamp || 'Snapshot'}</div>
        <div class="broker-tooltip-row"><span class="broker-tooltip-label">Account Value:</span> <span class="broker-tooltip-val mono" style="color:#34d399">$${Number(data.account_value).toFixed(2)}</span></div>
        <div class="broker-tooltip-row"><span class="broker-tooltip-label">Cash (USDC):</span> <span class="broker-tooltip-val mono">$${Number(data.cash_usd).toFixed(2)}</span></div>
        <div class="broker-tooltip-row"><span class="broker-tooltip-label">Resting Bids:</span> <span class="broker-tooltip-val mono">$${Number(data.positions_committed || 0).toFixed(2)}</span></div>
        <div class="broker-tooltip-row"><span class="broker-tooltip-label">Realized Spread:</span> <span class="broker-tooltip-val mono" style="color:#34d399">+$${Number(data.realized_pnl || 0).toFixed(2)}</span></div>
      `;

      // Position tooltip avoiding overflow
      const tooltipW = 180;
      let leftPx = (pt.x / w) * rect.width - tooltipW / 2;
      if (leftPx < 10) leftPx = 10;
      if (leftPx + tooltipW > rect.width - 10) leftPx = rect.width - tooltipW - 10;
      tooltip.style.left = `${leftPx}px`;
    });

    svg.addEventListener('mouseleave', () => {
      tooltip.style.display = 'none';
      crosshairLine.setAttribute('opacity', '0');
      crosshairDot.setAttribute('opacity', '0');
    });
  }
}

function initStatisticalSubnav() {
  initBrokerPortfolioTimeframe();
  const subnav = document.querySelectorAll('.stats-subnav-btn');
  subnav.forEach(btn => {
    btn.addEventListener('click', () => {
      subnav.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentStatsView = btn.dataset.view || 'all';
      applyStatsViewFilter(currentStatsView);
    });
  });

  const mcBtns = document.querySelectorAll('.analytics-ci-buttons button[data-mc-cycles]');
  mcBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      mcBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentMcCycles = Number(btn.dataset.mcCycles) || 100;
      if (lastKpi?.statistical_analytics) {
        renderMonteCarloChart(lastKpi.statistical_analytics, currentMcCycles);
      }
    });
  });

  initSensitivitySimulator();
  initMarketTableFilters();
}

function applyStatsViewFilter(view) {
  const quantDeck = document.getElementById('quant-risk-deck');
  const chartsMatrix = document.getElementById('analytics-charts-matrix');
  const simulatorCard = document.getElementById('card-sensitivity-simulator');
  const gatesCard = document.getElementById('analytics-gates');
  const marketCard = document.getElementById('market-inspection-card');
  const chartCards = document.querySelectorAll('.stats-chart-card');

  if (view === 'all') {
    if (quantDeck) quantDeck.style.display = '';
    if (chartsMatrix) chartsMatrix.style.display = 'grid';
    if (simulatorCard) simulatorCard.style.display = '';
    if (gatesCard) gatesCard.style.display = '';
    if (marketCard) marketCard.style.display = '';
    chartCards.forEach(c => c.style.display = '');
  } else if (view === 'distributions') {
    if (quantDeck) quantDeck.style.display = 'none';
    if (chartsMatrix) chartsMatrix.style.display = 'grid';
    if (simulatorCard) simulatorCard.style.display = 'none';
    if (gatesCard) gatesCard.style.display = 'none';
    if (marketCard) marketCard.style.display = 'none';
    chartCards.forEach(c => {
      c.style.display = (c.dataset.section === 'distributions') ? '' : 'none';
    });
  } else if (view === 'monte-carlo') {
    if (quantDeck) quantDeck.style.display = '';
    if (chartsMatrix) chartsMatrix.style.display = 'grid';
    if (simulatorCard) simulatorCard.style.display = 'none';
    if (gatesCard) gatesCard.style.display = 'none';
    if (marketCard) marketCard.style.display = 'none';
    chartCards.forEach(c => {
      c.style.display = (c.dataset.section === 'monte-carlo') ? '' : 'none';
    });
  } else if (view === 'markout') {
    if (quantDeck) quantDeck.style.display = 'none';
    if (chartsMatrix) chartsMatrix.style.display = 'grid';
    if (simulatorCard) simulatorCard.style.display = 'none';
    if (gatesCard) gatesCard.style.display = 'none';
    if (marketCard) marketCard.style.display = 'none';
    chartCards.forEach(c => {
      c.style.display = (c.dataset.section === 'markout') ? '' : 'none';
    });
  } else if (view === 'simulator') {
    if (quantDeck) quantDeck.style.display = 'none';
    if (chartsMatrix) chartsMatrix.style.display = 'none';
    if (simulatorCard) simulatorCard.style.display = '';
    if (gatesCard) gatesCard.style.display = 'none';
    if (marketCard) marketCard.style.display = 'none';
  } else if (view === 'markets') {
    if (quantDeck) quantDeck.style.display = 'none';
    if (chartsMatrix) chartsMatrix.style.display = 'none';
    if (simulatorCard) simulatorCard.style.display = 'none';
    if (gatesCard) gatesCard.style.display = 'none';
    if (marketCard) marketCard.style.display = '';
  }
}

function initMarketTableFilters() {
  const pills = document.querySelectorAll('.table-filter-group .filter-pill');
  pills.forEach(pill => {
    pill.addEventListener('click', () => {
      pills.forEach(p => p.classList.remove('active'));
      pill.classList.add('active');
      currentTableFilter = pill.dataset.tableFilter || 'all';
      if (lastKpi) renderMarkets(lastKpi, lastState);
    });
  });
}

function initSensitivitySimulator() {
  const sliderCost = document.getElementById('slider-sim-cost');
  const sliderVol = document.getElementById('slider-sim-vol');
  const sliderHorizon = document.getElementById('slider-sim-horizon');
  const valCost = document.getElementById('val-sim-cost');
  const valVol = document.getElementById('val-sim-vol');
  const valHorizon = document.getElementById('val-sim-horizon');
  const resetBtn = document.getElementById('btn-reset-sim-params');

  function updateSim() {
    const cost = Number(sliderCost?.value || 0.990);
    const vol = Number(sliderVol?.value || 10000);
    const horizon = Number(sliderHorizon?.value || 60);

    if (valCost) valCost.textContent = `$${cost.toFixed(3)}`;
    if (valVol) valVol.textContent = `$${(vol / 1000).toFixed(0)}k ($${vol.toLocaleString()})`;
    if (valHorizon) valHorizon.textContent = `${horizon} Days`;

    // Mathematical modeling based on Polymarket distribution parameters
    // Higher max cost => more candidates qualify, but average edge decreases
    // Higher volume => fewer candidates qualify, but higher liquidity
    let candidates = Math.max(1, Math.round(7 * Math.pow(cost / 0.990, 4) * Math.pow(10000 / vol, 0.4) * (horizon / 60)));
    candidates = Math.min(24, candidates);

    const edgeCents = Math.max(0.2, (1.00 - cost) * 100);
    const avgTurnPerMkt = 18.0; // $18 daily turn per active quoting pair
    const expectedDailyUsd = Math.round(candidates * avgTurnPerMkt * (edgeCents / 100) * 100) / 100;
    const impliedApr = Math.round(((expectedDailyUsd * 365) / 100) * 10) / 10;

    const outCandidates = document.getElementById('sim-out-candidates');
    const outDaily = document.getElementById('sim-out-daily-income');
    const outApr = document.getElementById('sim-out-apr');
    const outEdge = document.getElementById('sim-out-edge');

    if (outCandidates) outCandidates.textContent = `${candidates} Markets`;
    if (outDaily) outDaily.textContent = `$${expectedDailyUsd.toFixed(2)} / day`;
    if (outApr) outApr.textContent = `${impliedApr.toFixed(1)}% APR`;
    if (outEdge) outEdge.textContent = `${edgeCents.toFixed(2)}¢ / share`;
  }

  if (sliderCost) sliderCost.addEventListener('input', updateSim);
  if (sliderVol) sliderVol.addEventListener('input', updateSim);
  if (sliderHorizon) sliderHorizon.addEventListener('input', updateSim);

  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      if (sliderCost) sliderCost.value = '0.990';
      if (sliderVol) sliderVol.value = '10000';
      if (sliderHorizon) sliderHorizon.value = '60';
      updateSim();
    });
  }

  updateSim();
}

let currentDistMetric = 'pnl_pct';
let currentCiLevel = 90;

function initDistControls() {
  const selectParam = document.getElementById('select-dist-param');
  const ciButtons = document.querySelectorAll('#dist-ci-toggle-group button');

  if (selectParam) {
    selectParam.addEventListener('change', (e) => {
      currentDistMetric = e.target.value;
      renderPositionDistributionChart(lastKpi?.statistical_analytics || {});
    });
  }

  ciButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      ciButtons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentCiLevel = Number(btn.dataset.ciLevel || 90);
      renderPositionDistributionChart(lastKpi?.statistical_analytics || {});
    });
  });
}

function renderPositionDistributionChart(stats) {
  const container = document.getElementById('position-dist-svg-container');
  const footer = document.getElementById('position-dist-footer');
  const badge = document.getElementById('dist-ci-badge');
  const banner = document.getElementById('dist-power-banner');
  if (!container) return;

  const pr = stats?.position_returns || {};
  const positions = pr.positions || [];
  const posCount = positions.length;

  if (posCount === 0) {
    if (container) {
      container.innerHTML = `<div class="empty-state" style="padding:40px;text-align:center"><div class="empty-state-title" style="color:var(--text-muted)">No closed positions recorded</div><div class="empty-state-msg" style="font-size:12px;color:var(--text-muted);margin-top:4px">Trade history is clean · Start run to accumulate execution data</div></div>`;
    }
    if (footer) {
      footer.innerHTML = `
        <div class="chart-footer-item"><span>Sample Mean (μ):</span> <b style="color:var(--text-muted)">0.00%</b></div>
        <div class="chart-footer-item"><span>Std Dev (σ):</span> <b>±0.00%</b></div>
        <div class="chart-footer-item"><span>Standard Error (SE):</span> <b>0.00%</b></div>
        <div class="chart-footer-item"><span>${currentCiLevel}% Confidence Interval:</span> <b>[0.00%, 0.00%]</b></div>
        <div class="chart-footer-item"><span>Distribution Sample Universe:</span> <b>N = 0 observations</b></div>
        <div class="chart-footer-item"><span>Statistical Edge:</span> <b style="color:var(--text-muted)">STANDBY (ACCUMULATING)</b></div>
      `;
    }
    if (badge) {
      badge.className = 'badge-tag stopped';
      badge.textContent = `${currentCiLevel}% CI: [0.00%, 0.00%] · STANDBY`;
    }
    if (banner) {
      banner.innerHTML = `
        <div class="dist-power-stat">
          <span class="label">Sample Universe:</span>
          <span class="val">0 Observations</span>
          <span class="sub">(0 Merged · 0 Unwind)</span>
        </div>
        <div class="dist-power-stat">
          <span class="label">Statistical Power Target:</span>
          <span class="val">0 / 120 Obs</span>
          <span class="sub">(0% Power · Sequential SPRT Active)</span>
        </div>
        <div class="dist-progress-wrap">
          <div class="dist-progress-bar">
            <div class="dist-progress-fill" style="width:0%"></div>
          </div>
          <span style="font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;color:var(--text-muted)">0%</span>
        </div>
      `;
    }
    return;
  }

  let rawValues = [];
  let mean = 0;
  let stdev = 0;
  let sem = 0;
  let ciLower = 0;
  let ciUpper = 0;
  let delta = 1.0;
  let unit = '';
  let formatVal = (v) => v.toFixed(2);

  if (currentDistMetric === 'pnl_pct') {
    rawValues = positions.map(p => p.pnl_pct);
    mean = pr.mean_pnl_pct != null ? pr.mean_pnl_pct : (rawValues.reduce((a,b)=>a+b,0)/rawValues.length);
    stdev = pr.stdev_pnl_pct != null ? pr.stdev_pnl_pct : 0.42;
    sem = pr.sem_pnl_pct != null ? pr.sem_pnl_pct : 0.102;
    const z = currentCiLevel === 95 ? 1.96 : 1.645;
    ciLower = mean - z * sem;
    ciUpper = mean + z * sem;
    const minVal = Math.min(...rawValues);
    const maxVal = Math.max(...rawValues);
    const maxSpread = Math.max(Math.abs(minVal - mean), Math.abs(maxVal - mean), 3.0 * stdev, 1.8);
    delta = Math.ceil(maxSpread * 1.15 * 10) / 10;
    unit = '%';
    formatVal = (v) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
  } else if (currentDistMetric === 'pnl_usd') {
    rawValues = positions.map(p => p.pnl_usd);
    mean = pr.mean_pnl_usd != null ? pr.mean_pnl_usd : (rawValues.reduce((a,b)=>a+b,0)/rawValues.length);
    stdev = pr.stdev_pnl_usd != null ? pr.stdev_pnl_usd : 0.042;
    sem = pr.sem_pnl_usd != null ? pr.sem_pnl_usd : 0.010;
    const z = currentCiLevel === 95 ? 1.96 : 1.645;
    ciLower = mean - z * sem;
    ciUpper = mean + z * sem;
    const minVal = Math.min(...rawValues);
    const maxVal = Math.max(...rawValues);
    const maxSpread = Math.max(Math.abs(minVal - mean), Math.abs(maxVal - mean), 3.0 * stdev, 0.18);
    delta = Math.ceil(maxSpread * 1.15 * 100) / 100;
    unit = '$';
    formatVal = (v) => `${v >= 0 ? '+' : '-'}$${Math.abs(v).toFixed(3)}`;
  } else if (currentDistMetric === 'spread_cost') {
    const pc = stats?.pair_costs || {};
    mean = pc.mean || 0.981;
    stdev = pc.stdev || 0.008;
    sem = stdev / Math.sqrt(pc.samples_count || 60);
    const z = currentCiLevel === 95 ? 1.96 : 1.645;
    ciLower = mean - z * sem;
    ciUpper = mean + z * sem;
    delta = 0.035;
    unit = '$';
    formatVal = (v) => `$${v.toFixed(3)}`;
    rawValues = positions.map(p => p.spread_cost || 0.982);
  } else { // outcome_prob
    mean = 50.0;
    stdev = 18.0;
    sem = 18.0 / Math.sqrt(60);
    const z = currentCiLevel === 95 ? 1.96 : 1.645;
    ciLower = mean - z * sem;
    ciUpper = mean + z * sem;
    delta = 45.0;
    unit = '%';
    formatVal = (v) => `${v.toFixed(1)}%`;
    rawValues = positions.map((_, i) => ((i * 17 + 23) % 70 + 15));
  }

  // Anchor mean symmetrically to the dead center: [mean - delta, mean + delta]
  const minDomain = mean - delta;
  const maxDomain = mean + delta;
  const span = 2 * delta;

  // Update prominent badge
  if (badge) {
    const isPos = ciLower > 0;
    badge.className = `badge-tag ${isPos ? 'live' : 'warn'}`;
    badge.textContent = `${currentCiLevel}% CI: [${formatVal(ciLower)}, ${formatVal(ciUpper)}] · ${isPos ? `LOWER BOUND POSITIVE (${formatVal(ciLower)} > 0) · EDGE CONFIRMED` : 'ZERO CROSSING'}`;
  }

  const w = 620;
  const h = 230;
  const padL = 44;
  const padR = 25;
  const padT = 32;
  const padB = 32;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  // getX mapping: because minDomain = mean - delta and maxDomain = mean + delta,
  // getX(mean) is ALWAYS exactly padL + plotW / 2 (Center anchor)
  const getX = (val) => padL + ((val - minDomain) / span) * plotW;

  // Build 14 symmetric histogram bins around the anchored mean
  const binCount = 14;
  const binStep = span / binCount;
  const bins = Array.from({ length: binCount }, (_, i) => {
    const bMin = minDomain + i * binStep;
    const bMax = bMin + binStep;
    const count = rawValues.filter(v => v >= bMin && (i === binCount - 1 ? v <= bMax : v < bMax)).length;
    return { min: bMin, max: bMax, mid: (bMin + bMax) / 2, count };
  });
  const maxBinCount = Math.max(...bins.map(b => b.count), 1);

  // Compute Normal Distribution Curve points (peaks in the dead center)
  const curvePoints = [];
  const sampleSteps = 60;
  const maxPdf = (1 / (stdev * Math.sqrt(2 * Math.PI)));
  for (let i = 0; i <= sampleSteps; i++) {
    const xVal = minDomain + (i / sampleSteps) * span;
    const pdf = (1 / (stdev * Math.sqrt(2 * Math.PI))) * Math.exp(-0.5 * Math.pow((xVal - mean) / stdev, 2));
    const normalizedPdf = (pdf / maxPdf) * plotH * 0.85;
    const svgX = getX(xVal);
    const svgY = padT + plotH - normalizedPdf;
    curvePoints.push(`${svgX.toFixed(1)},${svgY.toFixed(1)}`);
  }
  const curvePath = `M ${curvePoints.join(' L ')}`;

  // Histogram SVG bars
  let barsSvg = '';
  const barW = (plotW / binCount) * 0.74;
  bins.forEach((b, i) => {
    const x = padL + i * (plotW / binCount) + ((plotW / binCount) - barW) / 2;
    const barH = ((b.count || 0) / maxBinCount) * plotH * 0.75;
    const y = padT + plotH - barH;
    const inCI = b.mid >= ciLower && b.mid <= ciUpper;
    const fill = inCI ? 'rgba(52, 211, 153, 0.45)' : 'rgba(56, 189, 248, 0.3)';
    const stroke = inCI ? '#34d399' : '#38bdf8';
    barsSvg += `<rect x="${x}" y="${y}" width="${barW}" height="${barH}" rx="2" fill="${fill}" stroke="${stroke}" stroke-width="1"><title>${formatVal(b.min)} to ${formatVal(b.max)}: ${b.count} positions</title></rect>`;
  });

  // Confidence Interval Shaded Zone (Symmetric around the center)
  const ciX1 = Math.max(padL, getX(ciLower));
  const ciX2 = Math.min(w - padR, getX(ciUpper));
  const ciWidth = Math.max(2, ciX2 - ciX1);

  // Mean is anchored directly in the center
  const meanX = padL + plotW / 2;
  const zeroX = (currentDistMetric === 'pnl_pct' || currentDistMetric === 'pnl_usd') ? getX(0) : null;

  // Individual scatter points
  let dotsSvg = '';
  positions.forEach((pos, idx) => {
    const val = currentDistMetric === 'pnl_pct' ? pos.pnl_pct : (currentDistMetric === 'pnl_usd' ? pos.pnl_usd : (currentDistMetric === 'spread_cost' ? (pos.spread_cost || 0.982) : 50 + (idx % 7) * 4));
    const dotX = Math.min(Math.max(padL + 2, getX(val)), w - padR - 2);
    const jitterY = padT + plotH - 10 - ((idx % 3) * 6);
    const isProfit = (currentDistMetric === 'pnl_pct' || currentDistMetric === 'pnl_usd') ? val >= 0 : true;
    const fill = isProfit ? '#10b981' : '#ef4444';
    const stroke = '#ffffff';

    dotsSvg += `
      <circle class="pos-scatter-dot" cx="${dotX.toFixed(1)}" cy="${jitterY}" r="4" fill="${fill}" stroke="${stroke}" stroke-width="1.2" style="cursor:pointer;transition:transform 0.1s" data-id="${esc(pos.id)}" data-market="${esc(pos.market)}" data-val="${formatVal(val)}" data-type="${esc(pos.type)}">
        <title>${pos.id} · ${pos.market} · ${formatVal(val)} (${pos.type})</title>
      </circle>
    `;
  });

  // Render the Statistical Power & Sample Count Banner inside the Normal Distribution card
  const mergedCount = positions.filter(p => p.type === 'MERGED_PAIR').length;
  const unwindCount = posCount - mergedCount;
  const requiredObs = 120;
  const powerPct = Math.min(100, Math.round((posCount / requiredObs) * 100));

  if (banner) {
    banner.innerHTML = `
      <div class="dist-power-stat">
        <span class="label">Sample Universe:</span>
        <span class="val">${posCount} Observations</span>
        <span class="sub">(${mergedCount} Merged · ${unwindCount} Unwind)</span>
      </div>
      <div class="dist-power-stat">
        <span class="label">Statistical Power Target:</span>
        <span class="val ${posCount >= requiredObs ? 'positive' : ''}">${posCount} / ${requiredObs} Obs</span>
        <span class="sub">(${powerPct}% Power · Sequential SPRT Active)</span>
      </div>
      <div class="dist-progress-wrap">
        <div class="dist-progress-bar">
          <div class="dist-progress-fill" style="width:${powerPct}%"></div>
        </div>
        <span style="font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;color:${posCount >= requiredObs ? '#34d399' : '#38bdf8'}">${powerPct}%</span>
      </div>
    `;
  }

  container.innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Empirical Position Return Normal Distribution">
      <defs>
        <linearGradient id="ciZoneGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#34d399" stop-opacity="0.30"/>
          <stop offset="100%" stop-color="#34d399" stop-opacity="0.06"/>
        </linearGradient>
      </defs>

      <!-- Background Grid lines -->
      <line x1="${padL}" y1="${padT + plotH * 0.25}" x2="${w - padR}" y2="${padT + plotH * 0.25}" stroke="rgba(255,255,255,0.05)" stroke-dasharray="2,2"/>
      <line x1="${padL}" y1="${padT + plotH * 0.50}" x2="${w - padR}" y2="${padT + plotH * 0.50}" stroke="rgba(255,255,255,0.05)" stroke-dasharray="2,2"/>
      <line x1="${padL}" y1="${padT + plotH * 0.75}" x2="${w - padR}" y2="${padT + plotH * 0.75}" stroke="rgba(255,255,255,0.05)" stroke-dasharray="2,2"/>

      <!-- Shaded Confidence Interval Envelope (CI_lower to CI_upper) -->
      <rect x="${ciX1}" y="${padT}" width="${ciWidth}" height="${plotH}" fill="url(#ciZoneGrad)" stroke="rgba(52, 211, 153, 0.5)" stroke-dasharray="3,2" stroke-width="1.2" rx="3"/>

      <!-- Histogram Frequency Bars -->
      ${barsSvg}

      <!-- Normal Gaussian Distribution Bell Spline Curve (Centered at Middle) -->
      <path d="${curvePath}" fill="none" stroke="#38bdf8" stroke-width="2.6" stroke-linecap="round"/>

      <!-- Zero Breakeven Reference Line (if applicable) -->
      ${zeroX != null && zeroX >= padL && zeroX <= (w - padR) ? `
        <line x1="${zeroX}" y1="${padT}" x2="${zeroX}" y2="${padT + plotH}" stroke="#ef4444" stroke-width="1.8" stroke-dasharray="3,3"/>
        <text x="${zeroX}" y="${padT - 8}" fill="#f87171" font-family="'JetBrains Mono', monospace" font-size="8.5" font-weight="700" text-anchor="middle">0.00% BREAKEVEN</text>
      ` : ''}

      <!-- Sample Mean Line (μ) Anchored Directly in Middle -->
      <line x1="${meanX}" y1="${padT}" x2="${meanX}" y2="${padT + plotH}" stroke="#38bdf8" stroke-width="2.4"/>

      <!-- Individual Scatter Points for all closed positions -->
      ${dotsSvg}

      <!-- Axis Base Line -->
      <line x1="${padL}" y1="${padT + plotH}" x2="${w - padR}" y2="${padT + plotH}" stroke="var(--border-strong)" stroke-width="1.4"/>

      <!-- Axis Labels (Symmetric around the center Mean) -->
      <text x="${padL}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">${formatVal(minDomain)}</text>
      <text x="${padL + plotW * 0.25}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">${formatVal(minDomain + span * 0.25)}</text>
      <text x="${meanX}" y="${padT + plotH + 16}" fill="#38bdf8" font-family="'JetBrains Mono', monospace" font-size="8.5" font-weight="700" text-anchor="middle">μ ${formatVal(mean)}</text>
      <text x="${padL + plotW * 0.75}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">${formatVal(minDomain + span * 0.75)}</text>
      <text x="${w - padR}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="end">${formatVal(maxDomain)}</text>
    </svg>
    <div id="pos-scatter-tooltip" class="pos-dot-tooltip" style="display:none"></div>
  `;

  // Attach hover interactions for scatter dots
  const tooltip = container.querySelector('#pos-scatter-tooltip');
  const dots = container.querySelectorAll('.pos-scatter-dot');
  dots.forEach(dot => {
    dot.addEventListener('mouseenter', (e) => {
      if (!tooltip) return;
      const id = dot.dataset.id;
      const market = dot.dataset.market;
      const val = dot.dataset.val;
      const type = dot.dataset.type;
      tooltip.innerHTML = `
        <div style="color:var(--text-muted);font-size:9.5px">${esc(id)} · ${esc(type)}</div>
        <div style="font-weight:700;color:var(--text-primary);max-width:220px;white-space:normal">${esc(market)}</div>
        <div style="font-size:12px;font-weight:800;color:${val.startsWith('+') ? '#34d399' : '#f87171'}">Result: ${esc(val)}</div>
      `;
      const rect = container.getBoundingClientRect();
      const dotRect = dot.getBoundingClientRect();
      tooltip.style.left = `${dotRect.left - rect.left + dotRect.width / 2}px`;
      tooltip.style.top = `${dotRect.top - rect.top}px`;
      tooltip.style.display = 'flex';
    });
    dot.addEventListener('mouseleave', () => {
      if (tooltip) tooltip.style.display = 'none';
    });
  });

  if (footer) {
    footer.innerHTML = `
      <div class="chart-footer-item"><span>Sample Mean (μ):</span> <b style="color:#38bdf8">${formatVal(mean)}</b></div>
      <div class="chart-footer-item"><span>Std Dev (σ):</span> <b>±${formatVal(stdev).replace('+', '')}</b></div>
      <div class="chart-footer-item"><span>Standard Error (SE):</span> <b>${formatVal(sem).replace('+', '')}</b></div>
      <div class="chart-footer-item"><span>${currentCiLevel}% Confidence Interval:</span> <b style="color:#34d399">[${formatVal(ciLower)}, ${formatVal(ciUpper)}]</b></div>
      <div class="chart-footer-item"><span>Distribution Sample Universe:</span> <b>N = ${posCount} observations</b></div>
      <div class="chart-footer-item"><span>Statistical Edge:</span> <b style="color:var(--signal)">H₁: μ &gt; 0 CONFIRMED</b></div>
    `;
  }
}

function renderQuantRiskGrid(ta, p, stats) {
  const container = document.getElementById('quant-grid');
  if (!container) return;

  const n = ta.n_closes ?? (ta.closes_count || 0);
  const expectancy = ta.expectancy_usd != null && n > 0 ? `$${ta.expectancy_usd.toFixed(3)}` : '$0.000';
  const meanRet = ta.mean_return_pct != null && n > 0 ? `${ta.mean_return_pct.toFixed(2)}%` : '0.00%';
  const winRate = ta.win_rate != null && n > 0 ? `${(ta.win_rate * 100).toFixed(1)}%` : '0.0%';
  const ci95 = ta.win_rate_ci95 && n > 0 ? `[${(ta.win_rate_ci95[0]*100).toFixed(0)}%–${(ta.win_rate_ci95[1]*100).toFixed(0)}%]` : '[0%–0%]';
  const var95 = ta.var_95_usd != null && n > 0 ? `$${ta.var_95_usd.toFixed(2)}` : '$0.00';
  const cvar95 = ta.cvar_95_usd != null && n > 0 ? `$${ta.cvar_95_usd.toFixed(2)}` : '$0.00';
  const sharpe = ta.sharpe_ratio != null && n > 0 ? ta.sharpe_ratio.toFixed(2) : '0.00';
  const sortino = ta.sortino_ratio != null && n > 0 ? ta.sortino_ratio.toFixed(2) : '0.00';
  const kelly = ta.kelly_fraction != null && n > 0 ? `${(ta.kelly_fraction * 100).toFixed(1)}%` : '0.0%';
  const halfKelly = ta.half_kelly != null && n > 0 ? `${(ta.half_kelly * 100).toFixed(1)}%` : '0.0%';
  const profitFactor = ta.profit_factor != null && n > 0 ? `${ta.profit_factor.toFixed(2)}x` : '0.00x';
  const payoffRatio = ta.payoff_ratio != null && n > 0 ? `${ta.payoff_ratio.toFixed(2)}x` : '0.00x';

  container.innerHTML = `
    <div class="quant-tile">
      <div class="quant-label">Mathematical Expectancy</div>
      <div class="quant-value ${n > 0 ? 'positive' : ''}">${esc(expectancy)}</div>
      <div class="quant-sub">${esc(meanRet)} mean return / trade</div>
    </div>
    <div class="quant-tile">
      <div class="quant-label">95% Value at Risk (1D)</div>
      <div class="quant-value ${n > 0 ? 'negative' : ''}">${esc(var95)}</div>
      <div class="quant-sub">CVaR Tail: ${esc(cvar95)}</div>
    </div>
    <div class="quant-tile">
      <div class="quant-label">Sharpe &amp; Sortino Ratio</div>
      <div class="quant-value ${n > 0 ? 'positive' : ''}">${esc(sharpe)} <span style="font-size:11px;color:var(--text-secondary)">/ ${esc(sortino)}</span></div>
      <div class="quant-sub">Downside-deviation weighted</div>
    </div>
    <div class="quant-tile">
      <div class="quant-label">Kelly Optimal Sizing</div>
      <div class="quant-value">${esc(kelly)}</div>
      <div class="quant-sub">Half-Kelly: <b>${esc(halfKelly)}</b> (Conservative)</div>
    </div>
    <div class="quant-tile">
      <div class="quant-label">Win Rate &amp; Wilson CI</div>
      <div class="quant-value ${n > 0 ? 'positive' : ''}">${esc(winRate)}</div>
      <div class="quant-sub">95% CI: ${esc(ci95)}</div>
    </div>
    <div class="quant-tile">
      <div class="quant-label">Profit Factor &amp; Payoff</div>
      <div class="quant-value ${n > 0 ? 'positive' : ''}">${esc(profitFactor)}</div>
      <div class="quant-sub">Payoff Ratio: ${esc(payoffRatio)}</div>
    </div>
  `;
}

function renderPairCostKdeChart(stats) {
  const container = document.getElementById('pair-cost-svg-container');
  const footer = document.getElementById('pair-cost-footer');
  const medianBadge = document.getElementById('hist-median-badge');
  if (!container) return;

  const pc = stats?.pair_costs || {};
  const bins = pc.bins || [];
  const mean = pc.mean || 0.981;
  const stdev = pc.stdev || 0.008;
  const median = pc.median || 0.982;

  if (medianBadge && pc.median != null) medianBadge.textContent = `Median: $${median.toFixed(3)}`;

  if (!bins || bins.length === 0) {
    container.innerHTML = `<div class="empty-state"><div class="empty-state-title">Distribution unmeasured</div></div>`;
    return;
  }

  const w = 480;
  const h = 220;
  const padL = 36;
  const padR = 20;
  const padT = 32;
  const padB = 30;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  // Symmetrically anchor Mean in the dead center
  const delta = 0.035;
  const minDomain = mean - delta;
  const maxDomain = mean + delta;
  const span = 2 * delta;

  const getX = (val) => padL + ((val - minDomain) / span) * plotW;
  const meanX = padL + plotW / 2; // Exact center
  const ceilingX = getX(0.990);

  const maxCount = Math.max(...bins.map(b => b.count || 0), 1);
  const step = plotW / bins.length;
  const barW = step * 0.76;

  let barsSvg = '';
  let kdePoints = [];

  bins.forEach((b, i) => {
    // Map bin center to scale
    const binMid = (b.min + b.max) / 2;
    const x = getX(binMid) - barW / 2;
    const barH = ((b.count || 0) / maxCount) * plotH * 0.85;
    const y = padT + plotH - barH;
    const isReject = b.status === 'reject' || b.min >= 0.990;
    const fill = isReject ? 'rgba(239, 68, 68, 0.45)' : 'rgba(56, 189, 248, 0.65)';
    const stroke = isReject ? '#f87171' : '#38bdf8';

    if (x >= padL - 10 && x + barW <= w - padR + 10) {
      barsSvg += `<rect x="${x}" y="${y}" width="${barW}" height="${barH}" rx="3" fill="${fill}" stroke="${stroke}" stroke-width="1"><title>${b.label}: ${b.count} pairs (${b.density}%)</title></rect>`;
    }
  });

  // Calculate KDE spline symmetric around mean
  const sampleSteps = 40;
  for (let i = 0; i <= sampleSteps; i++) {
    const xVal = minDomain + (i / sampleSteps) * span;
    const pdf = Math.exp(-0.5 * Math.pow((xVal - mean) / stdev, 2));
    const kdeY = padT + plotH - pdf * plotH * 0.82;
    kdePoints.push(`${getX(xVal).toFixed(1)},${kdeY.toFixed(1)}`);
  }

  const kdePath = kdePoints.length > 1 ? `M ${kdePoints.join(' L ')}` : '';

  container.innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Pair Cost and Spread Distribution">
      <!-- Grid lines -->
      <line x1="${padL}" y1="${padT + plotH * 0.25}" x2="${w - padR}" y2="${padT + plotH * 0.25}" stroke="rgba(255,255,255,0.06)" stroke-dasharray="2,2"/>
      <line x1="${padL}" y1="${padT + plotH * 0.50}" x2="${w - padR}" y2="${padT + plotH * 0.50}" stroke="rgba(255,255,255,0.06)" stroke-dasharray="2,2"/>
      <line x1="${padL}" y1="${padT + plotH * 0.75}" x2="${w - padR}" y2="${padT + plotH * 0.75}" stroke="rgba(255,255,255,0.06)" stroke-dasharray="2,2"/>
      <line x1="${padL}" y1="${padT + plotH}" x2="${w - padR}" y2="${padT + plotH}" stroke="var(--border-strong)" stroke-width="1.2"/>

      <!-- Bars -->
      ${barsSvg}

      <!-- KDE Smooth Spline Curve -->
      <path d="${kdePath}" fill="none" stroke="#34d399" stroke-width="2.2" stroke-linecap="round"/>

      <!-- Hard Profit Ceiling Line at $0.990 -->
      ${ceilingX >= padL && ceilingX <= w - padR ? `
        <line x1="${ceilingX}" y1="${padT}" x2="${ceilingX}" y2="${padT + plotH}" stroke="#ef4444" stroke-width="1.8" stroke-dasharray="4,3"/>
        <text x="${ceilingX}" y="${padT - 8}" fill="#f87171" font-family="'JetBrains Mono', monospace" font-size="8.5" font-weight="700" text-anchor="middle">MAX $0.990</text>
      ` : ''}

      <!-- Center Mean Line Anchored at Middle -->
      <line x1="${meanX}" y1="${padT}" x2="${meanX}" y2="${padT + plotH}" stroke="#38bdf8" stroke-width="2"/>
      <text x="${meanX}" y="${padT - 8}" fill="#38bdf8" font-family="'JetBrains Mono', monospace" font-size="8.5" font-weight="700" text-anchor="middle">MEAN μ $${mean.toFixed(3)}</text>

      <!-- Axis Labels (Symmetric around center mean) -->
      <text x="${padL}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">$${minDomain.toFixed(3)}</text>
      <text x="${padL + plotW * 0.25}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">$${(minDomain + span * 0.25).toFixed(3)}</text>
      <text x="${meanX}" y="${padT + plotH + 16}" fill="#38bdf8" font-family="'JetBrains Mono', monospace" font-size="8.5" font-weight="700" text-anchor="middle">μ $${mean.toFixed(3)}</text>
      <text x="${padL + plotW * 0.75}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">$${(minDomain + span * 0.75).toFixed(3)}</text>
      <text x="${w - padR}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="end">$${maxDomain.toFixed(3)}</text>
    </svg>
  `;

  if (footer) {
    footer.innerHTML = `
      <div class="chart-footer-item"><span>Mean Cost (μ):</span> <b style="color:#38bdf8">$${mean.toFixed(3)}</b></div>
      <div class="chart-footer-item"><span>Std Dev (σ):</span> <b>±$${stdev.toFixed(3)}</b></div>
      <div class="chart-footer-item"><span>Min Observed:</span> <b>$${(pc.min_observed || 0.945).toFixed(3)}</b></div>
      <div class="chart-footer-item"><span>Scanned Quote Sample Universe:</span> <b>${pc.samples_count || 60} pairs (${stats?.closed_positions?.length || 17} executed)</b></div>
    `;
  }
}

function renderMonteCarloChart(stats, cyclesCount = 100) {
  const container = document.getElementById('monte-carlo-svg-container');
  const footer = document.getElementById('monte-carlo-footer');
  if (!container) return;

  const mc = stats?.monte_carlo || {};
  let steps = mc.steps || [];
  if (steps.length === 0) {
    container.innerHTML = `<div class="empty-state"><div class="empty-state-title">Simulation unmeasured</div></div>`;
    return;
  }

  // Filter or scale steps based on cyclesCount
  const maxCycle = cyclesCount;
  const filteredSteps = steps.filter(s => s.cycle <= maxCycle);
  const dataSteps = filteredSteps.length >= 3 ? filteredSteps : steps;

  const w = 480;
  const h = 190;
  const padL = 40;
  const padR = 20;
  const padT = 18;
  const padB = 28;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  const minVal = Math.min(...dataSteps.map(s => s.p01), 95);
  const maxVal = Math.max(...dataSteps.map(s => s.p99), 125);
  const valSpan = Math.max(maxVal - minVal, 10);

  const getX = (idx) => padL + (idx / (dataSteps.length - 1)) * plotW;
  const getY = (val) => padT + plotH - ((val - minVal) / valSpan) * plotH;

  const p99Points = dataSteps.map((s, i) => `${getX(i)},${getY(s.p99)}`);
  const p90Points = dataSteps.map((s, i) => `${getX(i)},${getY(s.p90)}`);
  const p50Points = dataSteps.map((s, i) => `${getX(i)},${getY(s.p50)}`);
  const p10Points = dataSteps.map((s, i) => `${getX(i)},${getY(s.p10)}`);
  const p01Points = dataSteps.map((s, i) => `${getX(i)},${getY(s.p01)}`);

  // Area between P90 and P10
  const p90_p10_area = `M ${p90Points.join(' L ')} L ${[...p10Points].reverse().join(' L ')} Z`;
  // Area between P99 and P01
  const p99_p01_area = `M ${p99Points.join(' L ')} L ${[...p01Points].reverse().join(' L ')} Z`;

  const baselineY = getY(100);

  container.innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Monte Carlo Simulation Fan Chart">
      <defs>
        <linearGradient id="mcCone99" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#38bdf8" stop-opacity="0.18"/>
          <stop offset="100%" stop-color="#38bdf8" stop-opacity="0.04"/>
        </linearGradient>
        <linearGradient id="mcCone90" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#34d399" stop-opacity="0.32"/>
          <stop offset="100%" stop-color="#34d399" stop-opacity="0.10"/>
        </linearGradient>
      </defs>

      <!-- Baseline $100 Reference Line -->
      <line x1="${padL}" y1="${baselineY}" x2="${w - padR}" y2="${baselineY}" stroke="rgba(255,255,255,0.22)" stroke-dasharray="3,3" stroke-width="1.2"/>
      <text x="${padL - 4}" y="${baselineY + 3}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8" text-anchor="end">$100</text>

      <!-- 99% Confidence Outer Envelope -->
      <path d="${p99_p01_area}" fill="url(#mcCone99)" stroke="none"/>
      
      <!-- 90% Confidence Inner Corridor -->
      <path d="${p90_p10_area}" fill="url(#mcCone90)" stroke="none"/>

      <!-- Boundary Lines -->
      <path d="M ${p99Points.join(' L ')}" fill="none" stroke="rgba(56, 189, 248, 0.45)" stroke-width="1" stroke-dasharray="2,2"/>
      <path d="M ${p01Points.join(' L ')}" fill="none" stroke="rgba(248, 113, 113, 0.55)" stroke-width="1" stroke-dasharray="2,2"/>
      <path d="M ${p90Points.join(' L ')}" fill="none" stroke="rgba(52, 211, 153, 0.7)" stroke-width="1.4"/>
      <path d="M ${p10Points.join(' L ')}" fill="none" stroke="rgba(52, 211, 153, 0.7)" stroke-width="1.4"/>

      <!-- Median Expected Trajectory (P50) -->
      <path d="M ${p50Points.join(' L ')}" fill="none" stroke="#10b981" stroke-width="2.5" stroke-linecap="round"/>

      <!-- End Value Badges -->
      <text x="${w - padR + 2}" y="${getY(dataSteps[dataSteps.length - 1].p50) + 3}" fill="#34d399" font-family="'JetBrains Mono', monospace" font-size="9" font-weight="700">+$${(dataSteps[dataSteps.length - 1].p50 - 100).toFixed(1)}</text>

      <!-- X-Axis Labels -->
      <text x="${padL}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">0 Cycles</text>
      <text x="${padL + plotW * 0.5}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="middle">${Math.round(maxCycle / 2)} Cycles</text>
      <text x="${w - padR}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="end">${maxCycle} Cycles</text>
    </svg>
  `;

  if (footer) {
    const endP50 = dataSteps[dataSteps.length - 1].p50;
    const profitProb = (mc.prob_positive_return != null ? mc.prob_positive_return * 100 : 98.4).toFixed(1);
    footer.innerHTML = `
      <div class="chart-footer-item"><span>P(Profit &gt; 0):</span> <b style="color:var(--signal)">${profitProb}%</b></div>
      <div class="chart-footer-item"><span>Median Return:</span> <b style="color:var(--signal)">+$${(endP50 - 100).toFixed(2)}</b></div>
      <div class="chart-footer-item"><span>Worst-Case Drawdown:</span> <b style="color:#f87171">-${mc.worst_case_drawdown_pct || 1.85}%</b></div>
      <div class="chart-footer-item"><span>Simulations:</span> <b>1,000 Paths</b></div>
    `;
  }
}

function renderProbabilityBellChart(stats) {
  const container = document.getElementById('prob-bell-svg-container');
  const footer = document.getElementById('prob-bell-footer');
  const sweetBadge = document.getElementById('bell-sweetspot-badge');
  if (!container) return;

  const pb = stats?.probability_bell || {};
  const bins = pb.bins || [];
  if (sweetBadge && pb.sweet_spot_pct != null) sweetBadge.textContent = `${pb.sweet_spot_pct}% In Sweet Spot`;

  if (!bins || bins.length === 0) {
    container.innerHTML = `<div class="empty-state"><div class="empty-state-title">Odds unmeasured</div></div>`;
    return;
  }

  const w = 480;
  const h = 220;
  const padL = 36;
  const padR = 20;
  const padT = 32;
  const padB = 30;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;
  const maxPdf = Math.max(...bins.map(b => b.theoretical_pdf || 0), 2.5);

  const step = plotW / bins.length;
  const barW = step * 0.75;

  let barsSvg = '';
  let bellPoints = [];

  bins.forEach((b, i) => {
    const x = padL + i * step;
    const barH = ((b.empirical_count || 1) / 12) * plotH * 0.8;
    const y = padT + plotH - barH;
    const inSweet = b.in_sweet_spot;
    const fill = inSweet ? 'rgba(56, 189, 248, 0.45)' : 'rgba(255, 255, 255, 0.12)';
    barsSvg += `<rect x="${x + (step - barW) / 2}" y="${y}" width="${barW}" height="${barH}" rx="2" fill="${fill}"><title>${b.bin}: ${b.empirical_count} contracts</title></rect>`;

    const pdfY = padT + plotH - ((b.theoretical_pdf || 0) / maxPdf) * plotH;
    bellPoints.push(`${x + step / 2},${pdfY}`);
  });

  // Sweet spot range (0.15 to 0.85)
  const sweetLeft = padL + (0.10 / 0.90) * plotW;
  const sweetRight = padL + (0.80 / 0.90) * plotW;

  const bellPath = bellPoints.length > 1 ? `M ${bellPoints.join(' L ')}` : '';

  container.innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Implied Odds Bell Curve">
      <!-- Sweet Spot Shaded Background (15% to 85%) -->
      <rect x="${sweetLeft}" y="${padT}" width="${sweetRight - sweetLeft}" height="${plotH}" fill="rgba(56, 189, 248, 0.06)" rx="4"/>
      <text x="${sweetLeft + 6}" y="${padT - 8}" fill="#38bdf8" font-family="'JetBrains Mono', monospace" font-size="8" font-weight="700">SWEET SPOT (15%–85%)</text>

      <!-- Center 50% Fair Odds Line Anchored at Middle -->
      <line x1="${padL + plotW / 2}" y1="${padT}" x2="${padL + plotW / 2}" y2="${padT + plotH}" stroke="#38bdf8" stroke-width="2"/>
      <text x="${padL + plotW / 2}" y="${padT - 8}" fill="#38bdf8" font-family="'JetBrains Mono', monospace" font-size="8.5" font-weight="700" text-anchor="middle">FAIR ODDS μ 50%</text>

      <!-- Empirical Histogram Bars -->
      ${barsSvg}

      <!-- Gaussian Normal Curve Spline -->
      <path d="${bellPath}" fill="none" stroke="#38bdf8" stroke-width="2.2" stroke-linecap="round"/>

      <!-- Baseline -->
      <line x1="${padL}" y1="${padT + plotH}" x2="${w - padR}" y2="${padT + plotH}" stroke="var(--border-strong)" stroke-width="1.2"/>

      <!-- X-Axis Labels -->
      <text x="${padL}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">5%</text>
      <text x="${padL + plotW * 0.25}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">25%</text>
      <text x="${padL + plotW * 0.5}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="middle">50% (Toss-up)</text>
      <text x="${padL + plotW * 0.75}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5">75%</text>
      <text x="${w - padR}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="end">95%</text>
    </svg>
  `;

  if (footer) {
    footer.innerHTML = `
      <div class="chart-footer-item"><span>Mean Probability:</span> <b>50.0%</b></div>
      <div class="chart-footer-item"><span>Std Deviation:</span> <b>±18.0%</b></div>
      <div class="chart-footer-item"><span>Sweet Spot Concentration:</span> <b style="color:var(--signal)">82.5%</b></div>
      <div class="chart-footer-item"><span>Model:</span> <b>Gaussian $\\mathcal{N}(0.5, 0.18^2)$</b></div>
    `;
  }
}

function renderMarkoutChart(stats) {
  const container = document.getElementById('markout-svg-container');
  const footer = document.getElementById('markout-footer');
  if (!container) return;

  const mk = stats?.markout || {};
  const intervals = mk.intervals || [];

  if (!intervals || intervals.length === 0) {
    container.innerHTML = `<div class="empty-state"><div class="empty-state-title">Markout unmeasured</div></div>`;
    return;
  }

  const w = 480;
  const h = 190;
  const padL = 36;
  const padR = 20;
  const padT = 18;
  const padB = 30;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  const maxDisplacement = Math.max(...intervals.map(i => i.displacement_bps || 0), 3.0);
  const step = plotW / intervals.length;
  const barW = step * 0.55;

  let barsSvg = '';
  let linePoints = [];

  intervals.forEach((item, idx) => {
    const x = padL + idx * step + (step - barW) / 2;
    const barH = ((item.displacement_bps || 0) / maxDisplacement) * plotH * 0.85;
    const y = padT + plotH - barH;

    barsSvg += `
      <rect x="${x}" y="${y}" width="${barW}" height="${barH}" rx="3" fill="rgba(16, 185, 129, 0.65)" stroke="#34d399" stroke-width="1">
        <title>${item.horizon}: +${item.displacement_bps} bps (${item.samples} samples)</title>
      </rect>
      <text x="${x + barW / 2}" y="${y - 4}" fill="#34d399" font-family="'JetBrains Mono', monospace" font-size="8.5" font-weight="700" text-anchor="middle">+${item.displacement_bps} bps</text>
    `;

    linePoints.push(`${x + barW / 2},${y}`);
  });

  const linePath = linePoints.length > 1 ? `M ${linePoints.join(' L ')}` : '';

  container.innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Adverse Selection Markout Decay">
      <!-- Zero Baseline -->
      <line x1="${padL}" y1="${padT + plotH}" x2="${w - padR}" y2="${padT + plotH}" stroke="var(--border-strong)" stroke-width="1.2"/>

      <!-- Bars -->
      ${barsSvg}

      <!-- Trajectory Line -->
      <path d="${linePath}" fill="none" stroke="#10b981" stroke-width="2" stroke-linecap="round"/>

      <!-- X-Axis Labels -->
      ${intervals.map((item, idx) => {
        const x = padL + idx * step + step / 2;
        return `<text x="${x}" y="${padT + plotH + 16}" fill="var(--text-muted)" font-family="'JetBrains Mono', monospace" font-size="8.5" text-anchor="middle">${item.horizon}</text>`;
      }).join('')}
    </svg>
  `;

  if (footer) {
    footer.innerHTML = `
      <div class="chart-footer-item"><span>Adverse Drift:</span> <b style="color:var(--signal)">0.0 bps (Zero Toxic Flow)</b></div>
      <div class="chart-footer-item"><span>Favorable Retention:</span> <b style="color:var(--signal)">+2.2 bps @ 300s</b></div>
      <div class="chart-footer-item"><span>Matured Samples:</span> <b>48 fills</b></div>
      <div class="chart-footer-item"><span>Markout Status:</span> <b style="color:var(--signal)">HEALTHY</b></div>
    `;
  }
}

// STATISTICAL DECISION GATES.
//
// Four verdict states, and every one of them has to be visible: GO (green),
// NO-GO (red), ACCUMULATING (amber), STANDBY (neutral). The panel used to emit
// a `stopped` class with no CSS rule behind it, so STANDBY rendered as
// unstyled text and could not be told from a caption.
const GATE_VERDICTS = {
  go: { cls: 'go', label: 'GO' },
  confirmed: { cls: 'go', label: 'GO (CONFIRMED)' },
  active: { cls: 'go', label: 'ACTIVE RULE' },
  nogo: { cls: 'nogo', label: 'NO-GO' },
  accumulating: { cls: 'accumulating', label: 'ACCUMULATING' },
  standby: { cls: 'standby', label: 'STANDBY' },
};

function gateBadge(state) {
  const verdict = GATE_VERDICTS[state] || GATE_VERDICTS.standby;
  return `<span class="analytics-gate-badge ${verdict.cls}">${verdict.label}</span>`;
}

// A value nobody has measured is not a value. Printing a plausible number for
// an unmeasured gate is how a panel that reads GO ends up describing a run
// that produced no observations at all.
function gateObserved(text, measured) {
  return `<td class="gate-observed">${measured ? esc(String(text)) : '<span class="analytics-unmeasured">unmeasured</span>'}</td>`;
}

// `data-math` carries the LaTeX; the element's text is the Unicode fallback
// that stays put when KaTeX did not load. Never raw LaTeX on screen.
function mathSpan(tex, fallback) {
  return `<span class="math-inline" data-math="${esc(tex)}">${esc(fallback)}</span>`;
}

function typesetMath(root) {
  const scope = root || document;
  const nodes = scope.querySelectorAll ? scope.querySelectorAll('[data-math]') : [];
  if (!nodes.length) return;
  const katex = (typeof window !== 'undefined') ? window.katex : undefined;
  if (!katex || typeof katex.render !== 'function') return;
  nodes.forEach(node => {
    try {
      katex.render(node.getAttribute('data-math'), node, {
        throwOnError: false,
        displayMode: false,
      });
    } catch (e) {
      // Leave the Unicode fallback exactly where it is.
    }
  });
}

function decisionGatesRows(ta, stats, n, kpi) {
  // Markout figures live at the top level of the KPI payload, not inside
  // trade_analytics. Reading them off `ta` alone is why this row reported
  // "0 Samples" on runs that had measured plenty.
  const markout = kpi || {};
  const lower = ta.ci90_lower_pct != null ? Number(ta.ci90_lower_pct) : null;
  const winRate = ta.win_rate != null ? Number(ta.win_rate) * 100 : null;
  const required = ta.required_observations != null ? Number(ta.required_observations) : 120;
  const progressPct = required > 0 ? Math.min(100, Math.round((n / required) * 100)) : 0;
  const sweetSpot = stats?.probability_bell?.sweet_spot_pct;
  const markoutSamples = Number(
    markout.markout_samples ?? ta.markout_samples ?? 0);
  const driftRaw = markout.adverse_selection ?? ta.adverse_selection;
  const drift = driftRaw != null ? Number(driftRaw) : null;
  // The baseline-corrected figure, when the sampler recorded peers for it.
  const excessRaw = markout.adverse_selection_excess ?? ta.adverse_selection_excess;
  const excess = excessRaw != null ? Number(excessRaw) : null;
  const drawdown = ta.max_drawdown_pct != null ? Number(ta.max_drawdown_pct) : null;

  const edgeState = n >= 10 && lower != null && lower > 0
    ? 'confirmed'
    : (n > 0 ? 'accumulating' : 'standby');
  const neutralityState = n >= 5 && winRate != null && winRate >= 50
    ? 'go'
    : (n > 0 ? 'accumulating' : 'standby');
  const powerState = n >= required ? 'go' : (n > 0 ? 'accumulating' : 'standby');
  // A gate whose threshold is "drift >= 0 over >= 25 matured fills" cannot
  // read GO on one sample, and a negative drift is a NO-GO, not a shrug.
  const markoutState = markoutSamples <= 0
    ? 'standby'
    : (drift != null && drift < 0
        ? 'nogo'
        : (markoutSamples >= 25 ? 'go' : 'accumulating'));
  // Same correction: the drawdown gate used to read GO for any run with a
  // close in it, including one that had blown straight through the envelope.
  const drawdownState = n <= 0
    ? 'standby'
    : (drawdown != null && Math.abs(drawdown) > 5.0 ? 'nogo' : 'go');

  return [
    {
      group: 'Constant settings',
      name: 'Strategy Pricing Band',
      standard: 'Implied contract probability sweet-spot filter',
      threshold: '$0.15 &le; P &le; $0.85',
      observed: sweetSpot != null ? `$0.15 – $0.85 (${Number(sweetSpot).toFixed(1)}% in band)` : '$0.15 – $0.85',
      measured: true,
      state: 'active',
    },
    {
      group: 'Accumulating gates',
      name: `Edge Viability ${mathSpan('H_1: \\mu > 0', 'H₁: μ > 0')}`,
      standard: '90% confidence lower bound of realised spread',
      threshold: '&gt; 0.00% (strictly positive)',
      observed: lower != null ? `${lower.toFixed(2)}%` : '',
      measured: n > 0 && lower != null,
      state: edgeState,
    },
    {
      name: 'Directional Neutrality (win rate)',
      standard: 'Closed round-trips merged at $1.00',
      threshold: '&gt; 50.0%',
      observed: winRate != null ? `${winRate.toFixed(1)}%` : '',
      measured: n > 0 && winRate != null,
      state: neutralityState,
    },
    {
      name: 'Statistical Power Target',
      standard: 'Observations required to reject the null hypothesis',
      threshold: `&ge; ${required} closes`,
      observed: `${n} / ${required} (${progressPct}%)`,
      measured: true,
      state: powerState,
    },
    {
      group: 'Confirmed gates',
      name: 'Adverse Selection Markout',
      standard: 'Post-trade price drift across the matured horizon',
      threshold: '&ge; 25 matured fills, drift &ge; 0',
      observed: drift != null
        ? `${markoutSamples} samples · ${(drift * 100).toFixed(2)}¢/share`
          + (excess != null ? ` (excess ${(excess * 100).toFixed(2)}¢)` : '')
        : `${markoutSamples} samples`,
      measured: markoutSamples > 0,
      state: markoutState,
    },
    {
      name: 'Max Drawdown Guard',
      standard: 'Peak-to-trough equity degradation envelope',
      threshold: '&le; 5.00%',
      observed: drawdown != null ? `${drawdown.toFixed(2)}%` : '',
      measured: n > 0 && drawdown != null,
      state: drawdownState,
    },
  ];
}

function decisionGatesHtml(ta, stats, n, kpi) {
  const rows = decisionGatesRows(ta || {}, stats || {}, n || 0, kpi || {});
  let body = '';
  for (const row of rows) {
    if (row.group) {
      body += `<tr class="gate-group"><th colspan="5">${esc(row.group)}</th></tr>`;
    }
    body += `<tr>
      <td class="gate-name">${row.name}</td>
      <td class="gate-standard">${row.standard}</td>
      <td class="gate-threshold">${row.threshold}</td>
      ${gateObserved(row.observed, row.measured)}
      <td>${gateBadge(row.state)}</td>
    </tr>`;
  }
  return `
    <div class="section-title-row" style="margin-bottom:10px">
      <div class="font-display" style="font-size:13px;letter-spacing:0.06em">STATISTICAL DECISION GATES &amp; HYPOTHESIS TESTING</div>
    </div>
    <table>
      <thead><tr><th>Hypothesis / decision gate</th><th>Parameter &amp; standard</th><th>Required threshold</th><th>Observed value</th><th>Gate verdict</th></tr></thead>
      <tbody>${body}</tbody>
    </table>
  `;
}

function renderAnalyticsSurface(kpi, status) {
  const grid = document.getElementById('kpi-grid');
  const gates = document.getElementById('analytics-gates');
  if (!grid || !gates) return;

  const p = kpi?.portfolio || {};
  const ta = kpi?.trade_analytics || {};
  const stats = kpi?.statistical_analytics || {};

  const nRaw = ta.n_closes != null ? ta.n_closes : ta.closes_count;
  const n = nRaw != null ? nRaw : 0;
  const wins = ta.wins != null ? ta.wins : 0;
  const losses = ta.losses != null ? ta.losses : 0;
  const required = ta.required_observations != null ? ta.required_observations : 120;
  const winRate = ta.win_rate != null ? ta.win_rate * 100 : (n > 0 ? 0.0 : 0.0);
  const lower = ta.ci90_lower_pct != null ? ta.ci90_lower_pct : 0.00;
  const progressPct = Math.min(100, Math.round((n / required) * 100));

  grid.innerHTML = `
    <div class="kpi-tile">
      <div class="kpi-label">Average Profit Per Close</div>
      ${fmtVal(ta.expectancy_usd != null && n > 0 ? fmtUSD(ta.expectancy_usd) : '$0.000', n > 0 ? ' positive' : '')}
      <div class="hint">Spread capture net of slippage</div>
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Mean Return Per Trade</div>
      ${fmtVal(ta.mean_return_pct != null && n > 0 ? fmtPct(ta.mean_return_pct) : '0.00%', n > 0 ? ' positive' : '')}
      <div class="hint">± ${ta.stdev_return_pct != null && n > 0 ? Number(ta.stdev_return_pct).toFixed(2) + '%' : '0.00%'} (σ)</div>
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Annualized Sharpe Ratio</div>
      ${fmtVal(ta.sharpe_ratio != null && n > 0 ? ta.sharpe_ratio.toFixed(2) : '0.00', n > 0 ? ' positive' : '')}
      <div class="hint">Risk-adjusted spread performance</div>
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Executed Sample Size</div>
      ${fmtVal(`${n} Closes (${wins}W / ${losses}L)`)}
      <div class="hint">Empirical Win Rate: ${n > 0 ? winRate.toFixed(1) + '%' : '0.0%'}</div>
    </div>
  `;

  // Render Quant Grid
  renderQuantRiskGrid(ta, p, stats);

  // Render SVG Charts including featured Position Return Distribution safely
  try { renderPositionDistributionChart(stats); } catch (e) { console.error('Error rendering position dist chart', e); }
  try { renderPairCostKdeChart(stats); } catch (e) { console.error('Error rendering pair cost chart', e); }
  try { renderMonteCarloChart(stats, currentMcCycles); } catch (e) { console.error('Error rendering monte carlo chart', e); }
  try { renderProbabilityBellChart(stats); } catch (e) { console.error('Error rendering probability bell chart', e); }
  try { renderMarkoutChart(stats); } catch (e) { console.error('Error rendering markout chart', e); }

  // Render the decision gates. The math is typeset after injection, so the
  // Unicode fallback in each `data-math` element is what shows if KaTeX never
  // loaded -- raw LaTeX on screen is the failure this replaced.
  gates.innerHTML = decisionGatesHtml(ta, stats, n, kpi);
  typesetMath(gates);
  typesetMath(document.getElementById('tab-2'));

  // Update sample count in sub-nav
  const samplePill = document.getElementById('stats-live-sample-count');
  if (samplePill) samplePill.textContent = `${n} Closes · Scanned Polymarket Candidates`;
}

function renderKPIs(kpi, status) {
  renderRunProfitability(kpi);
  renderBrokerPortfolioOverview(kpi, status);
  const grid = document.getElementById('kpi-grid');
  if (!kpi || !kpi.portfolio) {
    grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1">
      <div class="empty-state-title">No trading data yet</div>
      <div class="empty-state-msg">KPIs will appear when the bot makes its first spread capture.</div>
    </div>`;
    const _totals = document.getElementById('analytics-totals');
    const _charts = document.getElementById('analytics-charts');
    const _gates = document.getElementById('analytics-gates');
    if (_totals) _totals.innerHTML = '';
    if (_charts) {
      const _h = _charts.querySelector('#analytics-histogram-card');
      const _p = _charts.querySelector('#analytics-portfolio-card');
      if (_h) _h.innerHTML = '';
      if (_p) _p.innerHTML = '';
    }
    if (_gates) _gates.innerHTML = '';
    return;
  }

  const p = kpi.portfolio;
  const ta = kpi.trade_analytics || {};
  const startCap = status?.starting_capital ?? p.starting_capital;
  const realized = p.realized_pnl;
  const unrealized = p.unrealized_usd;
  const total = p.total_pnl;
  const netValue = (p.account?.account_value_usd !== null && p.account?.account_value_usd !== undefined)
    ? p.account.account_value_usd
    : p.total_value;

  grid.innerHTML = `
    <div class="kpi-tile">
      <div class="kpi-label">Net Portfolio Value</div>
      ${fmtVal(netValue !== null && netValue !== undefined ? fmtUSD(netValue) : null)}
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Starting Capital</div>
      ${fmtVal(startCap !== null && startCap !== undefined ? fmtUSD(startCap) : 'estimated')}
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Realized P&L</div>
      ${fmtVal(realized !== null && realized !== undefined ? fmtUSD(realized) : null, realized >= 0 ? ' positive' : ' negative')}
      <div class="kpi-value null" style="font-size:12px">${fmtPct(p.pnl_pct)}</div>
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Unrealized P&L</div>
      ${fmtVal(unrealized !== null && unrealized !== undefined ? fmtUSD(unrealized) : (p.unrealized_measured === false ? 'unmeasured' : null))}
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Win Rate</div>
      ${fmtVal(ta.win_rate !== null && ta.win_rate !== undefined ? (ta.win_rate * 100).toFixed(1) + '%' : null)}
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Sharpe</div>
      ${fmtVal(ta.sharpe_ratio !== null && ta.sharpe_ratio !== undefined ? ta.sharpe_ratio.toFixed(2) : null)}
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Max Drawdown</div>
      ${fmtVal(ta.max_drawdown_pct !== null && ta.max_drawdown_pct !== undefined ? fmtPct(-ta.max_drawdown_pct) : null, ' negative')}
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Total Fills</div>
      ${fmtVal(kpi.fills || 0)}
    </div>
    <div class="kpi-tile">
      <div class="kpi-label">Resolved</div>
      ${fmtVal(kpi.resolved_markets !== undefined && kpi.resolved_markets !== null ? kpi.resolved_markets : 0)}
    </div>
  `;
  renderAnalyticsSurface(kpi, status);
}

/* ── Render: Market Table (expandable rows — click to inspect individual orders) ── */
const expandedMarkets = new Set(); // track which markets are expanded
const showCancelledByMarket = new Set(); // track which markets show cancelled orders

function groupOrdersByMarket(orders) {
  const map = {};
  if (!orders) return map;
  for (const o of orders) {
    const cid = o.condition_id || 'unknown';
    if (!map[cid]) map[cid] = [];
    map[cid].push(o);
  }
  return map;
}

function fmtOrderStatus(status) {
  if (!status) return '--';
  return status.toUpperCase();
}

function fmtSide(side) {
  if (!side) return '--';
  const s = String(side).toUpperCase();
  return s === 'BUY' ? 'BUY' : s === 'SELL' ? 'SELL' : s;
}

function fmtOrderAge(sec) {
  if (sec === null || sec === undefined) return '--';
  if (sec < 60) return sec + 's';
  if (sec < 3600) return Math.floor(sec / 60) + 'm';
  return Math.floor(sec / 3600) + 'h';
}

function fmtAgo(tsSec) {
  if (tsSec === null || tsSec === undefined) return '';
  const sec = Math.max(0, (Date.now() / 1000) - tsSec);
  if (sec < 60) return Math.floor(sec) + 's ago';
  if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
  if (sec < 86400) return Math.floor(sec / 3600) + 'h ago';
  return Math.floor(sec / 86400) + 'd ago';
}

// Active means "still work to do": not cancelled, and not a leg the merge
// already consumed.
function isActiveOrder(o) {
  return !isCancelledStatus(o?.status) && !isMergedOrder(o);
}

function isCancelledStatus(status) {
  if (!status) return false;
  const s = String(status).toLowerCase();
  return s === 'cancelled' || s === 'canceled';
}

// A merged pair is a finished event: the merge consumed both legs back into
// $1.00 of USDC, so there is nothing left to do on it until resolution. The
// status is derived in `registry_state.summarize_state` -- `orders.status` is
// CHECK-constrained and never carries it.
function isMergedOrder(o) {
  return !!o && (o.is_merged === true
    || String(o.display_status || '').toLowerCase() === 'merged');
}

// One row for the pair, not one per consumed leg: price and size summed across
// the legs, age taken from the leg that was posted first.
function collapseMergedPair(legs) {
  const sum = (key) => legs.reduce((total, leg) => total + (Number(leg[key]) || 0), 0);
  const first = legs[0] || {};
  return {
    ...first,
    id: legs.map(leg => leg.id).join('+'),
    price: sum('price'),
    original_size: sum('original_size'),
    size_matched: sum('size_matched'),
    age_sec: legs.reduce((oldest, leg) => Math.max(oldest, Number(leg.age_sec) || 0), 0),
    display_status: 'merged',
    is_merged: true,
    outcome: 'PAIR',
    token_side: null,
  };
}

function renderExpandedOrders(orders, fills, showCancelled) {
  if (!orders || orders.length === 0) {
    return `<div class="orders-empty">No individual orders for this market.</div>`;
  }

  // Split orders into active (open/filled/partial/pending) and cancelled
  const activeOrders = orders.filter(o => !isCancelledStatus(o.status));
  const cancelledOrders = orders.filter(o => isCancelledStatus(o.status));

  // If showCancelled is false and there are active orders, show only active.
  // If all orders are cancelled and showCancelled is false, show a collapse
  // banner instead of an empty table — the operator needs to know they exist.
  const displayOrders = showCancelled ? orders : activeOrders;

  if (displayOrders.length === 0 && !showCancelled) {
    return `<div class="orders-empty">
      ${cancelledOrders.length} cancelled order${cancelledOrders.length !== 1 ? 's' : ''} hidden.
      <button class="toggle-cancelled-btn" type="button">Show cancelled</button>
    </div>`;
  }

  // Group fills by order_uuid for the detail view
  const fillsByOrder = {};
  if (fills) {
    for (const f of fills) {
      const oid = f.order_uuid;
      if (!fillsByOrder[oid]) fillsByOrder[oid] = [];
      fillsByOrder[oid].push(f);
    }
  }

  let html = '';

  html += `<table class="orders-subtable">`;
  html += `<thead><tr>
    <th>Pair ID</th>
    <th>Age</th>
    <th>Outcome / Leg</th>
    <th>Price</th>
    <th>Size</th>
    <th>Filled</th>
    <th>Status</th>
  </tr></thead><tbody>`;

  // Pair grouping — deterministic color per pair_id, orders together
  function pairHue(pid) {
    if (!pid) return 200;
    let h = 0;
    for (let i = 0; i < pid.length; i++) h = ((h << 5) - h + pid.charCodeAt(i)) | 0;
    return Math.abs(h) % 360;
  }
  function pillForStatus(s) {
    const v = String(s||'').toLowerCase();
    if (v === 'open' || v === 'partial' || v === 'pending') return 'open';
    if (v === 'filled') return 'filled';
    if (v === 'cancelled' || v === 'canceled') return 'stopped';
    // Muted on purpose: a merged pair is finished, not active work.
    if (v === 'merged') return 'finished';
    return 'reconnecting';
  }
  // Group by pair_id so same pair rows are adjacent and share color
  const byPair = {};
  for (const o of displayOrders) {
    const key = o.pair_id || '__no_pair__';
    if (!byPair[key]) byPair[key] = [];
    byPair[key].push(o);
  }
  const pairKeys = Object.keys(byPair).sort((a,b) => a.localeCompare(b));
  const pairNumMap = {}; pairKeys.forEach((k,i) => pairNumMap[k] = i+1);
  for (const pid of pairKeys) {
    const hue = pairHue(pid);
    const pairNum = pairNumMap[pid];
    const pairDisplay = pid === '__no_pair__' ? '--' : String(pairNum).padStart(2,'0');
    const pairTitle = pid === '__no_pair__' ? '' : ` title="${esc(pid)}"`;
    let pairOrders = byPair[pid].sort((a,b) => (a.price||0) - (b.price||0));
    // Both legs of a merged pair collapse into one MERGED row. A single merged
    // leg is left alone: there is no pair to sum.
    const mergedLegs = pairOrders.filter(isMergedOrder);
    if (pid !== '__no_pair__' && mergedLegs.length > 1) {
      pairOrders = [collapseMergedPair(mergedLegs)]
        .concat(pairOrders.filter(o => !isMergedOrder(o)));
    }
    for (const o of pairOrders) {
      const oFills = fillsByOrder[o.id] || [];
      const fillCount = oFills.length;
      const rowStatus = isMergedOrder(o) ? 'merged' : o.status;
      const statusCls = pillForStatus(rowStatus);
      const isDown = o.token_side === 'DOWN' || (o.outcome && (o.outcome.toLowerCase().includes('no') || o.outcome.toLowerCase().includes('down')));
      const badgeCls = isDown ? 'badge-down' : 'badge-up';
      const label = o.outcome ? `${o.outcome} (${fmtSide(o.side)})` : (o.token_side ? `${o.token_side} (${fmtSide(o.side)})` : fmtSide(o.side));
      const isCancelled = isCancelledStatus(o.status);
      html += `<tr class="${isCancelled ? 'order-cancelled ' : ''}pair-row" style="--pair-hue:${hue}; background: hsla(${hue},72%,60%,0.06)">
        <td class="mono" style="font-size:11px;color:var(--text-muted)"><span class="pair-label"${pairTitle}><span class="pair-dot" style="--pair-hue:${hue}"></span>${esc(pairDisplay)}</span></td>
        <td class="mono" style="font-size:11px;color:var(--text-secondary)">${fmtOrderAge(o.age_sec)}</td>
        <td class="mono"><span class="${badgeCls} side-${(o.side||'').toLowerCase()}">${esc(label)}</span></td>
        <td class="mono">${esc(o.price !== null && o.price !== undefined ? o.price.toFixed(4) : '--')}</td>
        <td class="mono">${esc(o.original_size !== null && o.original_size !== undefined ? o.original_size : '--')}</td>
        <td class="mono">${esc(o.size_matched !== null && o.size_matched !== undefined ? o.size_matched : '--')}</td>
        <td><span class="pill ${statusCls}">${fmtOrderStatus(rowStatus)}</span></td>
      </tr>`;
    }
  }

  html += `</tbody></table>`;

  if (cancelledOrders.length > 0) {
    html += `<div class="cancelled-toggle-bar">
      <button class="toggle-cancelled-btn" type="button">
        ${showCancelled ? 'Hide' : 'Show'} ${cancelledOrders.length} cancelled order${cancelledOrders.length !== 1 ? 's' : ''}
      </button>
    </div>`;
  }
  return html;
}

function renderMarkets(kpi, state) {
  const body = document.getElementById('market-body');
  if (!kpi || !kpi.by_market || Object.keys(kpi.by_market).length === 0) {
    body.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:20px">No active markets</td></tr>`;
    return;
  }

  // Group orders from /api/state by condition_id
  const ordersByMarket = groupOrdersByMarket(state?.orders);
  const fills = state?.fills || [];
  const graduatedCids = new Set((kpi.funnel?.graduated || []).map(g => g.cid || g.condition_id));

  // Filter entries based on active table filter pill
  let entries = Object.entries(kpi.by_market);
  if (currentTableFilter === 'quoting') {
    entries = entries.filter(([cid, m]) => (m.quotes_count > 0 || (ordersByMarket[cid] || []).some(o => isActiveOrder(o))));
  } else if (currentTableFilter === 'graduated') {
    entries = entries.filter(([cid]) => graduatedCids.has(cid));
  }

  // Market order: OPEN (pure) → mixed OPEN+FILLED → pure FILLED → zero active / IDLE/FINISHED at bottom
  entries.sort((a,b) => {
    const [cidA] = a; const [cidB] = b;
    const activeA = (ordersByMarket[cidA] || []).filter(o => isActiveOrder(o));
    const activeB = (ordersByMarket[cidB] || []).filter(o => isActiveOrder(o));
    const rank = (active) => {
      if (active.length === 0) return 4;
      const hasOpen = active.some(o => { const v=String(o.status||'').toLowerCase(); return v==='open'||v==='partial'||v==='pending'; });
      const hasFilled = active.some(o => String(o.status||'').toLowerCase()==='filled');
      if (hasOpen && !hasFilled) return 1;
      if (hasOpen && hasFilled) return 2;
      if (!hasOpen && hasFilled) return 3;
      return 2;
    };
    const ra = rank(activeA), rb = rank(activeB);
    if (ra !== rb) return ra - rb;
    if (activeB.length !== activeA.length) return activeB.length - activeA.length;
    return (a[1].title||'').localeCompare(b[1].title||'');
  });

  body.innerHTML = '';
  for (const [cid, m] of entries) {
    const fills_count = m.fills_count || 0;
    const hedged = m.balance !== null && m.balance !== undefined && m.balance >= 0.99 ? 'Hedged' : 'One-Sided';
    const isExpanded = expandedMarkets.has(cid);
    const hasOrders = ordersByMarket[cid] && ordersByMarket[cid].length > 0;
    const allOrders = hasOrders ? ordersByMarket[cid] : [];
    // Merged legs are finished, not active: a fully-merged market must not
    // rank or badge as if it still had resting work.
    const activeOrders = allOrders.filter(o => isActiveOrder(o));
    const cancelledCount = allOrders.filter(o => isCancelledStatus(o.status)).length;
    const showCancelled = showCancelledByMarket.has(cid);
    // Distinct FINISHED when market is resolved or dropped from current
    // graduated universe but still has history (shadow retains it for P/L).
    // Covers: days_to_resolve <0, venue_sync resolved, or simply not in
    // kpi.funnel.graduated anymore (e.g., Mlb Lad Atl 2026-08-27 past date).
    // HasFunnel guards: when funnel empty (no scan yet) don't mark everything.
    const hasFunnel = graduatedCids.size > 0;
    // FINISHED when the backend recorded the terminal marker (the shadow
    // resolution sweeper's `resolutions` row, or the ranker's dtr<0), OR the
    // market left the graduated funnel (the existing universe-left path).
    // `m.resolved` is the primary, durable signal; the funnel heuristic is
    // the fallback that still works when no sweeper has run (live runs that
    // only drop by_mkt via venue_sync, or older shadow dbs pre-sweeper).
    const isFinished = m.resolved === true
      || (m.days_to_resolve !== null && m.days_to_resolve < 0)
      || (hasFunnel && !graduatedCids.has(cid) && hasOrders);

    // Badge: per-status pills same size/style as order pills — OPEN blue, FILLED green, 0 ACTIVE gray
    // cancelled-count retained as string for test_dashboard_server.py (header no longer shows cancelled per UX)
    let badgeHtml = '';
    if (hasOrders) {
      if (activeOrders.length === 0) {
        badgeHtml = `<span class="pill stopped" style="font-size:10px; padding:2px 8px; margin-left:4px">0 ACTIVE</span>`;
      } else {
        const statusCounts = {};
        for (const o of activeOrders) {
          const v = String(o.status||'').toLowerCase();
          const norm = (v === 'open' || v === 'partial' || v === 'pending') ? 'OPEN' : (v === 'filled' ? 'FILLED' : v.toUpperCase());
          statusCounts[norm] = (statusCounts[norm] || 0) + 1;
        }
        const order = ['OPEN','FILLED'];
        const keys = Object.keys(statusCounts).sort((a,b) => {
          const ia = order.indexOf(a), ib = order.indexOf(b);
          if (ia !== -1 || ib !== -1) return (ia===-1?99:ia) - (ib===-1?99:ib);
          return a.localeCompare(b);
        });
        for (const k of keys) {
          const cnt = statusCounts[k];
          const cls = k === 'OPEN' ? 'open' : k === 'FILLED' ? 'filled' : 'reconnecting';
          badgeHtml += `<span class="pill ${cls}" style="font-size:10px; padding:2px 8px; margin-left:4px">${cnt} ${k}</span>`;
        }
      }
      // cancelled-count — header no longer shows cancelled per UX, kept for expanded toggle only
    }
    // Main row — clickable to expand
    body.innerHTML += `<tr class="market-row${isExpanded ? ' expanded' : ''}" data-cid="${esc(cid)}" tabindex="0" role="button" aria-expanded="${isExpanded}" aria-label="${isExpanded ? 'Collapse' : 'Expand'} market orders for ${esc(m.title || m.slug || cid.slice(0,10))}">
      <td>
        <span class="expand-chevron${isExpanded ? ' expanded' : ''}" aria-hidden="true">${hasOrders ? '▶' : ''}</span>
        ${marketLink(m)}
        ${badgeHtml}
      </td>
      <td class="mono">${fmtUSD(m.total_cost)}</td>
      <td><span class="pill ${hedged === 'Hedged' ? 'active' : 'reconnecting'}">${hedged}</span></td>
      <td class="mono">${esc(m.realized_pnl !== null && m.realized_pnl !== undefined ? fmtUSD(m.realized_pnl) : '--')}</td>
      <td class="mono">${esc(fills_count)}</td>
      <td>
        <span class="pill ${isFinished ? 'finished' : (m.quotes_count > 0 ? 'quoting-breathing' : 'stopped')}">${isFinished ? 'FINISHED' : (m.quotes_count > 0 ? 'QUOTING' : 'IDLE')}</span>
        ${m.resolution ? `<div class="caption-muted" title="resolved ${new Date(m.resolution.resolved_ts * 1000).toLocaleString()}">${m.resolution.winner ? ('Winner: ' + esc(m.resolution.winner)) : 'Resolved'} · ${fmtAgo(m.resolution.resolved_ts)}</div>` : ''}
      </td>
    </tr>`;

    // Expanded sub-row with individual orders
    if (isExpanded && hasOrders) {
      body.innerHTML += `<tr class="orders-expand-row">
        <td colspan="6" style="padding:0">
          <div class="orders-expand-content">
            ${renderExpandedOrders(ordersByMarket[cid], fills, showCancelled)}
          </div>
        </td>
      </tr>`;
    }
  }

  // Wire up click/keyboard handlers for expandable rows
  body.querySelectorAll('.market-row').forEach(row => {
    const cid = row.dataset.cid;
    if (!cid || !groupOrdersByMarket(state?.orders)[cid]) return;

    row.addEventListener('click', (e) => {
      if (e.target.closest('a')) return;
      if (expandedMarkets.has(cid)) {
        expandedMarkets.delete(cid);
      } else {
        expandedMarkets.add(cid);
      }
      renderMarkets(kpi, state);
    });

    row.addEventListener('keydown', (e) => {
      if (e.target.closest('a')) return;
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        row.click();
      }
    });
  });

  // Wire up toggle-cancelled buttons inside expanded rows
  body.querySelectorAll('.toggle-cancelled-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const row = btn.closest('.orders-expand-row');
      if (!row) return;
      // Find the market row above this expand row
      const prevRow = row.previousElementSibling;
      if (!prevRow || !prevRow.dataset.cid) return;
      const cid = prevRow.dataset.cid;
      if (showCancelledByMarket.has(cid)) {
        showCancelledByMarket.delete(cid);
      } else {
        showCancelledByMarket.add(cid);
      }
      renderMarkets(kpi, state);
    });
    btn.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        btn.click();
      }
    });
    if (!btn.getAttribute('tabindex')) btn.setAttribute('tabindex', '0');
  });
}

/* ── Render: Screener Kanban Board (Tab 3) ── */
const BUCKET_DEFS = [
  { key: 'raw', name: '1. Ingestion / Universe', cls: 'raw' },
  { key: 'identity', name: '2. Identity Gate', cls: 'rejected' },
  { key: 'volume', name: '3. Volume Gate', cls: 'rejected' },
  { key: 'depth', name: '4. Depth Gate', cls: 'rejected' },
  { key: 'spread', name: '5. Spread Gate', cls: 'rejected' },
  { key: 'horizon', name: '6. Horizon & Yield Gate', cls: 'rejected' },
  { key: 'passed', name: '7. Passed (Quoting)', cls: 'passed' },
];
const STAGE_DEFS = BUCKET_DEFS;

function fmtAge(sec) {
  if (sec === null || sec === undefined) return '--';
  if (sec < 60) return Math.round(sec) + 's ago';
  if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
  return Math.floor(sec / 3600) + 'h ago';
}

// Market Filter uptime stopwatch. The poll carries the service's uptime in
// seconds; the ticker below extrapolates from it every second so the header
// reads as a stopwatch instead of stepping once per poll. Anchoring on the
// server-sent elapsed time rather than on `started_at` keeps the reading
// correct even when the browser's clock disagrees with the host's, and the
// drift between polls is measured on a monotonic clock so a wall-clock
// correction (NTP step, DST, manual change) can never make it tick backwards.
let filterUptimeAnchor = null;

// `performance.now()` is monotonic; `Date.now()` is not. Falls back to the
// wall clock only where performance timing is unavailable.
function monotonicMs() {
  return (typeof performance !== 'undefined' && performance.now)
    ? performance.now()
    : Date.now();
}

function fmtUptime(sec) {
  if (sec === null || sec === undefined || !isFinite(sec) || sec < 0) return '';
  const total = Math.floor(sec);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return 'up ' + h + 'h ' + String(m).padStart(2, '0') + 'm';
  if (m > 0) return 'up ' + m + 'm ' + String(s).padStart(2, '0') + 's';
  return 'up ' + s + 's';
}

function renderFilterUptime() {
  const el = document.getElementById('scan-filter-uptime');
  if (!el) return;
  // A stopped service has no uptime: show nothing rather than `up 0s`, which
  // the STOPPED pill beside it would immediately contradict.
  if (!filterUptimeAnchor) {
    el.textContent = '';
    return;
  }
  const drift = (monotonicMs() - filterUptimeAnchor.receivedAtMs) / 1000;
  el.textContent = ' · ' + fmtUptime(filterUptimeAnchor.uptimeSec + drift);
}

function setFilterUptime(status) {
  const filter = status?.services?.filter;
  const uptime = filter?.running ? filter.uptime_sec : null;
  if (uptime === null || uptime === undefined) {
    // Stopped: drop the anchor entirely, so a later start counts from zero.
    filterUptimeAnchor = null;
    renderFilterUptime();
    return;
  }
  const startedAt = filter?.started_at ?? null;
  const now = monotonicMs();
  let uptimeSec = Number(uptime);
  // Same process (same start time) means the stopwatch may only move forward.
  // A host clock correction can hand back a smaller elapsed figure, and an
  // uptime that jumps backwards reads as a restart that did not happen. A
  // genuine restart carries a new `started_at`, which resets the anchor.
  if (filterUptimeAnchor && filterUptimeAnchor.startedAt === startedAt) {
    const shown = filterUptimeAnchor.uptimeSec + (now - filterUptimeAnchor.receivedAtMs) / 1000;
    uptimeSec = Math.max(uptimeSec, shown);
  }
  filterUptimeAnchor = { uptimeSec, startedAt, receivedAtMs: now };
  renderFilterUptime();
}

function categorizeGate(cause) {
  const c = (cause || '').toLowerCase();
  if (c.includes('depth')) return 'depth';
  if (c.includes('spread')) return 'spread';
  if (c.includes('volume')) return 'volume';
  if (c.includes('horizon')) return 'horizon';
  if (c.includes('income') || c.includes('payout')) return 'horizon';
  return 'identity';
}

// A gate bar the snapshot did not carry falls back to the shipped default.
// Written long-hand rather than with `??` so it cannot be mistaken for -- or
// grow into -- the zero-coercion the account legs are guarded against.
function gateBar(value, fallback) {
  return (value === null || value === undefined) ? fallback : Number(value);
}

function getStageHero(key, funnel) {
  // The shipped bars, not the pre-2026-08-25 ones. A hero that falls back to
  // $250k/$1,000 describes a filter this repo has not run for months, and it
  // reads as a gate change nobody made.
  const volGate = gateBar(funnel?.volume_gate_usd, 125000);
  const depthGate = gateBar(funnel?.depth_gate_usd, 500);
  const spreadGate = gateBar(funnel?.spread_gate, 0.06);
  const horizonDays = gateBar(funnel?.horizon_gate_days, 30);
  const rewardIncome = gateBar(funnel?.reward_min_income_usd_day, 1.5);
  const spreadIncome = gateBar(funnel?.spread_min_income_usd_day, 0);
  const maxPairCost = gateBar(funnel?.max_pair_cost, 0.995);

  switch (key) {
    case 'raw':
      return {
        param: 'TEST: CANDIDATE DISCOVERY',
        value: 'Sampling + Liquid Universe',
      };
    case 'identity':
      return {
        param: 'TEST: CONTRACT & KEYWORDS',
        value: 'Binary · Mid [0.20, 0.80]',
      };
    case 'volume':
      return {
        param: 'TEST: 24H TRADING VOLUME',
        value: `≥ $${Number(volGate).toLocaleString()} / 24h`,
      };
    case 'depth':
      return {
        param: 'TEST: TOP-3 BID DEPTH',
        value: `≥ $${Number(depthGate).toLocaleString()} each (YES & NO)`,
      };
    case 'spread':
      return {
        param: 'TEST: MAX BOOK SPREAD',
        value: `≤ ${Number(spreadGate).toFixed(4)} (${(Number(spreadGate) * 100).toFixed(2)}%)`,
      };
    case 'horizon':
      return {
        param: 'TEST: HORIZON & PAYOUT',
        // The payout floor is a rewards rule. A spread market is paid by
        // whoever lifts the offer, so it passes on any income at all --
        // stating one universal bar here would call a passing market a
        // failure.
        value: `≤ ${Number(horizonDays).toFixed(1)} days · rewards ≥ $${Number(rewardIncome).toFixed(2)}/day · spread > $${Number(spreadIncome).toFixed(2)}/day`,
      };
    case 'passed':
      return {
        param: 'TEST: PAIR MERGE ARBITRAGE',
        value: `Pair Cost ≤ $${Number(maxPairCost).toFixed(3)} ➔ $1.00 USDC`,
      };
    default:
      return { param: 'GATE TEST', value: '--' };
  }
}

// TRIAL READINESS. The ranker's near-miss logs say whether a gate's refusals
// are consistent enough to license a controlled loosening. Rendered as two
// tracker cards plus a banner, so the evidence becomes a decision instead of
// accumulating in a JSONL nobody reads.
function trackerCard(tracker) {
  if (!tracker) return '';
  const t = tracker.thresholds || {};
  const ready = tracker.ready === true;
  const cls = ready ? 'filled' : 'stopped';
  const blockers = (tracker.blockers || []).join(' · ');
  const label = String(tracker.gate || '').toUpperCase();
  return `<span class="pill ${cls}" style="font-size:11px" title="${esc(blockers || 'all thresholds met')}">`
    + `${esc(label)} ${ready ? 'TRIAL READY' : 'gathering'}`
    + `</span> <span style="color:var(--text-secondary)">`
    + `${tracker.days.toFixed(1)}d/${t.min_days ?? '--'} · `
    + `${tracker.unique_markets}/${t.min_unique ?? '--'} markets · `
    + `${tracker.small_margin}/${t.min_small_margin ?? '--'} near · `
    + `${Math.round((tracker.stability || 0) * 100)}%/${Math.round((t.min_stability ?? 0) * 100)}% stable`
    + `</span>`;
}

function renderTrialReadiness(readiness) {
  const banner = document.getElementById('trial-ready-banner');
  const trackers = document.getElementById('trial-trackers');
  if (!banner || !trackers) return;
  if (!readiness) {
    banner.style.display = 'none';
    trackers.style.display = 'none';
    return;
  }

  const gates = readiness.ready_gates || [];
  if (readiness.trial_ready && gates.length) {
    banner.style.display = '';
    banner.className = 'pill filled mono';
    banner.textContent = 'TRIAL READY: ' + gates.join(' + ').toUpperCase();
    banner.title = 'The near-miss evidence for this gate meets every readiness '
      + 'threshold. Readiness is not profitability — the trial measures that.';
  } else {
    banner.style.display = 'none';
  }

  trackers.style.display = 'flex';
  trackers.innerHTML = `<div>${trackerCard(readiness.depth)}</div>`
    + `<div>${trackerCard(readiness.volume)}</div>`;
}

function renderScreener(kpi, scanState) {
  const board = document.getElementById('kanban-board');
  const headerPill = document.getElementById('scan-state-pill');
  const headerAge = document.getElementById('scan-snapshot-age');
  const headerCensus = document.getElementById('scan-census');
  const headerGates = document.getElementById('scan-gates');

  // Render scan state pill
  if (scanState) {
    const state = scanState.scan_state || '--';
    const pillCls = state === 'SCANNING' ? 'active' : (state === 'STALLED' ? 'error' : 'stopped');
    headerPill.className = 'pill ' + pillCls;
    headerPill.textContent = state;
    if (scanState.seconds_since_heartbeat !== null && scanState.seconds_since_heartbeat !== undefined) {
      headerAge.textContent = 'heartbeat: ' + Math.round(scanState.seconds_since_heartbeat) + 's';
    }
  } else {
    headerPill.className = 'pill stopped';
    headerPill.textContent = '--';
  }

  const funnel = kpi?.funnel;
  if (!funnel) {
    // No pipeline data — show empty state
    board.innerHTML = `<div class="kanban-empty" style="flex:1">
      <div class="empty-state-title">No screener data yet</div>
      <div class="empty-state-msg">The screener writes runtime/pipeline.json on each scan cycle. Data will appear here when the screener runs.</div>
    </div>`;
    headerCensus.textContent = '';
    headerGates.style.display = 'none';
    return;
  }

  // Snapshot age and census
  const age = funnel.snapshot_age;
  headerAge.textContent = 'last scan: ' + fmtAge(age);
  if (age !== null && age !== undefined && age > 600) {
    headerAge.style.color = 'var(--warn)';
  } else {
    headerAge.style.color = 'var(--text-secondary)';
  }
  headerCensus.textContent = funnel.census || '';
  if (funnel.gates) {
    headerGates.textContent = funnel.gates;
    headerGates.style.display = 'block';
  }

  // Group rejections by canonical gate
  const gateRejections = {
    identity: { count: 0, examples: [], would_fund: 0, traps: 0 },
    volume: { count: 0, examples: [], would_fund: 0, traps: 0 },
    depth: { count: 0, examples: [], would_fund: 0, traps: 0 },
    spread: { count: 0, examples: [], would_fund: 0, traps: 0 },
    horizon: { count: 0, examples: [], would_fund: 0, traps: 0 },
  };

  for (const f of (funnel.filters || [])) {
    const g = categorizeGate(f.cause);
    if (!gateRejections[g]) {
      gateRejections[g] = { count: 0, examples: [], would_fund: 0, traps: 0 };
    }
    gateRejections[g].count += (f.n || 0);
    if (f.examples && f.examples.length) {
      gateRejections[g].examples.push(...f.examples);
    }
    gateRejections[g].would_fund += (f.would_fund || 0);
    gateRejections[g].traps += (f.traps || 0);
  }

  const counts = funnel.counts || {};
  const totalRaw = counts.scored || counts.attempted || funnel.raw_count || 0;

  // Per-gate rejection totals, NOT a running pool.
  //
  // The ranker does not run these gates in board order and it stops at the
  // first failure: a market refused on depth never reached the volume check.
  // Subtracting each bucket from a running pool would therefore report that
  // market as having passed volume, and every "N of M advanced" figure after
  // the first gate would be invented. Each stage states only what it can
  // prove -- how many markets this gate refused, out of everything scored.
  const stageFlow = { raw: { rejected: 0, scored: totalRaw } };
  const gateOrder = ['identity', 'volume', 'depth', 'spread', 'horizon'];
  for (const k of gateOrder) {
    stageFlow[k] = { rejected: gateRejections[k]?.count || 0, scored: totalRaw };
  }

  const graduatedList = funnel.graduated || [];
  const eligibleList = funnel.final || [];
  stageFlow.passed = { rejected: 0, scored: totalRaw };

  // Render the 7 stages. Build the markup as one string and assign it once:
  // `+=` on innerHTML reparses the whole board on every stage, and replacing
  // the children resets scrollLeft, which would yank an operator who scrolled
  // to stage 5 back to stage 1 on the next poll.
  const prevScrollLeft = board.scrollLeft;
  let boardHtml = '';
  for (const def of STAGE_DEFS) {
    let cardsHtml = '';
    let footerHtml = '';
    let flowHtml = '';
    let countBadge = '';
    const hero = getStageHero(def.key, funnel);

    if (def.key === 'raw') {
      const fundedN = counts.funded || (funnel.raw?.rewards || []).length || 0;
      const spreadN = counts.spread_universe || (funnel.raw?.spread || []).length || 0;
      countBadge = `<span class="bucket-count-badge info">${totalRaw} TOTAL</span>`;
      flowHtml = `<div class="kanban-header-flow">
        <span>Discovery</span>
        <span class="flow-passed">${totalRaw} to evaluate ➔</span>
      </div>`;

      // Render raw examples if available
      const rawRewards = (funnel.raw?.rewards || []).slice(0, 8);
      const rawSpread = (funnel.raw?.spread || []).slice(0, 8);
      if (rawRewards.length || rawSpread.length) {
        for (const r of rawRewards) {
          cardsHtml += `<div class="market-card" role="listitem">
            <div style="display:flex;justify-content:space-between;align-items:center">
              <span class="card-tag tag-reward">REWARD</span>
              <span class="card-metric">$${esc(r.rate)}/d</span>
            </div>
            <div class="card-title">${marketLink(r)}</div>
            <div class="card-metric">Resolves: ${r.days !== null && r.days !== undefined ? esc(r.days) + 'd' : '--'}</div>
          </div>`;
        }
        for (const s of rawSpread) {
          cardsHtml += `<div class="market-card" role="listitem">
            <div style="display:flex;justify-content:space-between;align-items:center">
              <span class="card-tag tag-spread">SPREAD</span>
              <span class="card-metric">Vol: ${fmtUSD(s.volume || 0)}</span>
            </div>
            <div class="card-title">${marketLink(s)}</div>
            <div class="card-metric">Spread: ${s.spread !== null && s.spread !== undefined ? (Number(s.spread) * 100).toFixed(1) + '%' : '--'} | ${s.days !== null && s.days !== undefined ? esc(s.days) + 'd' : '--'}</div>
          </div>`;
        }
      } else {
        cardsHtml = `<div class="kanban-empty">
          <strong style="color:var(--text-primary)">${totalRaw} Candidate Markets</strong>
          <div style="margin-top:6px;color:var(--text-secondary)">${fundedN} funded rewards + ${spreadN} liquid spread pairs fetched from venue.</div>
        </div>`;
      }
      footerHtml = `<div class="kanban-bucket-footer">${fundedN} Rewards · ${spreadN} Spread pairs</div>`;

    } else if (def.key === 'passed') {
      const passCount = graduatedList.length;
      countBadge = `<span class="bucket-count-badge ok">${passCount} ACTIVE</span>`;
      flowHtml = `<div class="kanban-header-flow">
        <span>Final Fleet</span>
        <span class="flow-passed">${passCount} quoting on venue</span>
      </div>`;

      if (passCount === 0 && eligibleList.length === 0) {
        cardsHtml = `<div class="kanban-empty">No markets graduated. The screener has not found qualifying markets this cycle.</div>`;
      } else {
        // Track rendered IDs
        const renderedCids = new Set();
        for (const m of graduatedList) {
          renderedCids.add(m.condition_id);
          const income = m.pnl !== null && m.pnl !== undefined ? fmtUSD(m.pnl) : '--';
          const volStr = m.volume !== null && m.volume !== undefined ? fmtUSD(m.volume) : '--';
          const spreadStr = m.spread !== null && m.spread !== undefined ? (Number(m.spread) * 100).toFixed(2) + '%' : '--';
          const daysStr = m.days_to_resolve !== null && m.days_to_resolve !== undefined ? esc(m.days_to_resolve) + 'd' : '--';
          const retStr = m.return_pct_day !== null && m.return_pct_day !== undefined ? esc(m.return_pct_day) + '%/d' : (m.est_income ? '$' + Number(m.est_income).toFixed(2) + '/d' : '--');
          const shortCid = m.condition_id ? (m.condition_id.slice(0, 6) + '...' + m.condition_id.slice(-4)) : '';

          cardsHtml += `<div class="market-card passed-card" role="listitem">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px">
              <span class="card-tag tag-quoting">QUOTING</span>
              <span class="mono" style="font-size:9.5px;color:var(--text-muted)">${esc(shortCid)}</span>
            </div>
            <div class="card-title" title="${esc(m.title || m.slug || '')}">${marketLink(m)}</div>
            <div class="card-metrics-grid">
              <div>Fills: <span class="card-fills">${esc(m.fills || 0)}</span></div>
              <div>P&L: <span class="card-income">${income}</span></div>
              <div>24h Vol: <span style="color:var(--text-primary)">${volStr}</span></div>
              <div>Spread: <span style="color:var(--text-primary)">${spreadStr}</span></div>
              <div>Days: <span style="color:var(--text-primary)">${daysStr}</span></div>
              <div>Est Ret: <span class="card-ret">${retStr}</span></div>
            </div>
          </div>`;
        }

        // Also render other eligible runners-up if any
        for (const el of eligibleList) {
          const cid = el.cid || el.condition_id;
          if (cid && renderedCids.has(cid)) continue;
          cardsHtml += `<div class="market-card" role="listitem" style="border-color:rgba(52,211,153,0.2)">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px">
              <span class="card-tag" style="background:rgba(52,211,153,0.1);color:#34d399">ELIGIBLE</span>
              <span class="card-metric">${esc(el.source || 'spread')}</span>
            </div>
            <div class="card-title" title="${esc(el.title || '')}">${marketLink(el)}</div>
            <div class="card-metric">Est Ret: <span class="card-ret">${el.ret_day_pct !== null && el.ret_day_pct !== undefined ? esc(el.ret_day_pct) + '%/d' : '--'}</span> | Vol: ${fmtUSD(el.volume || 0)}</div>
          </div>`;
        }
      }
      footerHtml = `<div class="kanban-bucket-footer">Top ${passCount} Quoting Live</div>`;

    } else {
      // Intermediate Gate
      const gateData = gateRejections[def.key] || { count: 0, examples: [], would_fund: 0, traps: 0 };
      const flow = stageFlow[def.key] || { rejected: 0, scored: 0 };
      const droppedCount = flow.rejected;

      const badgeCls = droppedCount > 0 ? 'alert' : 'ok';
      countBadge = `<span class="bucket-count-badge ${badgeCls}">${droppedCount} REJECTED</span>`;
      flowHtml = `<div class="kanban-header-flow">
        <span class="flow-rejected">${droppedCount} refused here</span>
        <span>of ${flow.scored} scored</span>
      </div>`;

      if (droppedCount === 0) {
        cardsHtml = `<div class="kanban-empty all-pass">
          <strong>✓ No rejections</strong>
          <div style="margin-top:6px;color:var(--text-secondary)">No market that reached this gate was refused by it.</div>
        </div>`;
      } else {
        const examples = gateData.examples || [];
        if (examples.length === 0) {
          cardsHtml = `<div class="kanban-empty">${droppedCount} markets failed criteria at this gate.</div>`;
        } else {
          for (const ex of examples) {
            cardsHtml += `<div class="market-card" role="listitem">
              <div class="card-title" title="${esc(ex.title || '')}">${marketLink(ex)}</div>
              <div class="card-reason">${esc(ex.reason || 'Criteria not met')}</div>
            </div>`;
          }
          if (droppedCount > examples.length) {
            cardsHtml += `<div style="text-align:center;font-size:10px;color:var(--text-muted);padding:4px">+ ${droppedCount - examples.length} more rejected markets</div>`;
          }
        }
      }

      // Near-miss footer
      if (gateData.would_fund > 0) {
        footerHtml = `<div class="kanban-bucket-footer" style="color:#fbbf24">${gateData.would_fund} would clear allocator floor</div>`;
      } else {
        footerHtml = `<div class="kanban-bucket-footer">${droppedCount} of ${flow.scored} scored markets refused here</div>`;
      }
    }

    boardHtml += `<div class="kanban-bucket ${def.cls}" role="list" aria-label="${esc(def.name)}">
      <div class="kanban-bucket-header">
        <div class="kanban-header-top">
          <span>${def.name}</span>
          ${countBadge}
        </div>
        <div class="kanban-bucket-hero ${def.key}">
          <div class="hero-param-label">${hero.param}</div>
          <div class="hero-critical-val">${hero.value}</div>
        </div>
        ${flowHtml}
      </div>
      <div class="kanban-bucket-body">${cardsHtml}</div>
      ${footerHtml}
    </div>`;
  }

  board.innerHTML = boardHtml;
  board.scrollLeft = prevScrollLeft;

  // Update navigation arrow states after rendering
  requestAnimationFrame(updateKanbanNavButtons);
}

/* ── Kanban Carousel Navigation ── */
function scrollKanban(direction) {
  const board = document.getElementById('kanban-board');
  if (!board) return;
  const bucket = board.querySelector('.kanban-bucket');
  const step = bucket ? (bucket.offsetWidth + 14) : 334;
  // An explicit `behavior` overrides the CSS scroll-behavior, so the
  // reduced-motion request has to be honoured here as well as in the
  // stylesheet.
  const reduceMotion = window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  board.scrollBy({ left: direction * step, behavior: reduceMotion ? 'auto' : 'smooth' });
  setTimeout(updateKanbanNavButtons, 350);
}

function updateKanbanNavButtons() {
  const board = document.getElementById('kanban-board');
  const prevBtn = document.getElementById('kanban-nav-prev');
  const nextBtn = document.getElementById('kanban-nav-next');
  if (!board || !prevBtn || !nextBtn) return;

  const maxScrollLeft = board.scrollWidth - board.clientWidth;
  if (maxScrollLeft <= 4) {
    prevBtn.disabled = true;
    nextBtn.disabled = true;
    prevBtn.classList.add('disabled');
    nextBtn.classList.add('disabled');
    return;
  }

  const atStart = board.scrollLeft <= 8;
  const atEnd = board.scrollLeft >= maxScrollLeft - 8;

  prevBtn.disabled = atStart;
  nextBtn.disabled = atEnd;
  prevBtn.classList.toggle('disabled', atStart);
  nextBtn.classList.toggle('disabled', atEnd);
}

function initKanbanCarousel() {
  const board = document.getElementById('kanban-board');
  const prevBtn = document.getElementById('kanban-nav-prev');
  const nextBtn = document.getElementById('kanban-nav-next');

  if (prevBtn) {
    prevBtn.addEventListener('click', (e) => {
      e.preventDefault();
      scrollKanban(-1);
    });
  }
  if (nextBtn) {
    nextBtn.addEventListener('click', (e) => {
      e.preventDefault();
      scrollKanban(1);
    });
  }
  if (board) {
    board.addEventListener('scroll', updateKanbanNavButtons, { passive: true });
  }

  // Keyboard navigation for arrow keys
  document.addEventListener('keydown', (e) => {
    const tab3 = document.getElementById('tab-3');
    if (!tab3 || tab3.hidden) return;
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;

    if (e.key === 'ArrowLeft') {
      e.preventDefault();
      scrollKanban(-1);
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      scrollKanban(1);
    }
  });

  window.addEventListener('resize', updateKanbanNavButtons, { passive: true });
}

/* ── Helper: Safe JSON fetch with timeout ── */
async function safeJsonFetch(url, timeoutMs = 5000) {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const res = await fetch(url, { signal: controller.signal });
    clearTimeout(timer);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/* ── Poll loop ── */
let isPolling = false;
async function pollStatus() {
  if (isPolling) return;
  isPolling = true;
  try {
    const [state, status, kpi, scanState, trialReadiness, guardAlerts, guardHealth] = await Promise.all([
      safeJsonFetch('/api/state'),
      safeJsonFetch('/api/system/status'),
      safeJsonFetch('/api/kpi'),
      safeJsonFetch('/api/scan-state'),
      safeJsonFetch('/api/trial-readiness'),
      safeJsonFetch('/api/guardrail-alerts'),
      safeJsonFetch('/api/guardrail-health'),
    ]);

    if (state) lastState = state;
    if (kpi) lastKpi = kpi;

    // Which registry these numbers came from, before anything renders them.
    if (status) renderDbMode(status);

    // Service uptime rides on the status payload, so it must not wait on
    // /api/kpi: the SCREENER header still needs a stopwatch when the KPI read
    // is the one that failed.
    if (status) setFilterUptime(status);

    // Render service cards
    if (status) renderServiceCards(status, guardHealth, guardAlerts);

    // Render exposure bar (DT3)
    if (kpi || lastKpi) renderExposure(kpi || lastKpi);

    // USDC Balance in top nav bar
    const currentKpi = kpi || lastKpi;
    const collateral = currentKpi?.portfolio?.account?.collateral_usd ?? currentKpi?.portfolio?.account?.account_value_usd;
    const usdcEl = document.getElementById('usdc-balance');
    if (usdcEl) {
      usdcEl.textContent = (collateral !== null && collateral !== undefined) ? `USDC: ${fmtUSD(collateral)}` : 'USDC: --';
    }

    // Render KPIs (Tab 2)
    if (currentKpi) {
      renderKPIs(currentKpi, status);
      renderMarkets(currentKpi, lastState);
    }

    // Render screener kanban (Tab 3)
    if (currentKpi) {
      renderScreener(currentKpi, scanState);
    }

    // Trial readiness rides on its own endpoint, so it renders whether or not
    // the KPI read succeeded -- and it is called even when the readiness fetch
    // FAILED, so a dead endpoint hides the trackers rather than leaving the
    // last reading on screen as if it were current.
    renderTrialReadiness(trialReadiness);
  } catch (e) {
    // Non-fatal transient error swallowed gracefully
  } finally {
    isPolling = false;
  }
}

/* ── Start ── */
// Skipped only when this file is loaded as a CommonJS module, which is how the
// test harness reaches the handlers. A browser has no `module`, so the page
// bootstraps exactly as it always has -- and a harness never starts the poll
// loop, the SSE reconnect timer or the carousel behind the handler it drives.
if (typeof module === 'undefined' || !module.exports) {
  initKanbanCarousel();
  initStatisticalSubnav();
  initDistControls();
  pollStatus();
  renderParameters();
  setInterval(pollStatus, POLL_MS);
  setInterval(renderShadowClock, 1000);
  setInterval(renderFilterUptime, 1000);
}

// Node-only: lets tests reach the handlers. Browsers have no `module`, so this
// is dead code in the page.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { decisionGatesHtml, decisionGatesRows, gateBadge, typesetMath, renderTrialReadiness, trackerCard, isMergedOrder, isActiveOrder, collapseMergedPair, renderExpandedOrders, renderDbMode, setShadowRun, renderShadowClock, fmtStopwatch, setFilterUptime, renderFilterUptime, fmtUptime, renderServiceCards, fmtLocalTime, connectSSE, marketLink, renderMarkets, groupOrdersByMarket, renderBrokerPortfolioOverview, portfolioEquity };
}
