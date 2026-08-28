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
function controlFetch(path) {
  return fetch(path, { method: 'POST', headers: { 'X-Control-Token': CONTROL_TOKEN } });
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

function appendTickerEvent(line, translation, ctx) {
  const empty = tickerEl.querySelector('.empty-state');
  if (empty) empty.remove();
  const div = document.createElement('div');
  div.className = 'ticker-event';
  if (translation) {
    div.innerHTML = `<div class="ticker-translation">${esc(translation)}${ctx ? ' <span class="ticker-ctx">' + esc(ctx) + '</span>' : ''}</div><div class="ticker-raw">${esc(line)}</div>`;
  } else {
    div.innerHTML = `<div class="ticker-raw">${esc(line)}</div>`;
  }
  tickerEl.insertBefore(div, tickerEl.firstChild);
  while (tickerEl.children.length > 100) tickerEl.removeChild(tickerEl.lastChild);
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
      appendTickerEvent(rawLine, translation, ctx);
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

/* ── Render: Service Cards (DT1: guardrail first, alert borders) ── */
const SERVICE_DEFS = [
  { key: 'filter', name: 'Market Filter', cmd: 'python -m scripts.filter_loop',
    desc: 'Scans 500+ Polymarket binary markets and screens down to 8 graduated pairs.' },
  { key: 'query', name: 'Query Polymarket', cmd: 'python -m core_brain.order_manager poll --interval 0.5',
    desc: 'Queries CLOB every 0.5s, reconciles fills, executes account sweeps.' },
  { key: 'decide', name: 'Decide & Execute', cmd: 'python -m core_brain.trader_loop --live --no-reconcile --no-sweep --interval 5',
    desc: 'Runs the trading loop (decide quotes -> submit maker orders) every 5s across approved markets.' },
  { key: 'guardrail', name: 'Guardrail Watchdog', cmd: 'python -m scripts.global_stop_loss',
    desc: 'Continuous risk monitor enforcing hard exposure and inventory limits.',
    readOnly: true },
];

function renderServiceCards(status, guardrailHealth, guardrailAlerts) {
  const container = document.getElementById('service-cards');
  container.innerHTML = '';

  // DT1: Guardrail card first (visual priority)
  const ordered = [...SERVICE_DEFS].sort((a, b) => {
    if (a.key === 'guardrail') return -1;
    if (b.key === 'guardrail') return 1;
    return 0;
  });

  for (const def of ordered) {
    let svc, running, pid;
    if (def.key === 'guardrail') {
      running = guardrailHealth?.running || false;
      pid = guardrailHealth?.pid;
    } else {
      svc = status?.services?.[def.key];
      running = svc?.running || false;
      pid = svc?.pid;
    }

    const hasAlert = def.key === 'guardrail' && guardrailAlerts?.alerts?.length > 0;
    const alertCls = hasAlert ? ' alert' : (running ? ' healthy' : '');
    const cardCls = def.readOnly ? 'card-guardrail' : '';
    const pillCls = running ? 'active' : 'stopped';
    const pillText = running ? 'ACTIVE' : 'STOPPED';

    let toggleHtml = '';
    if (!def.readOnly) {
      toggleHtml = `<button class="toggle ${running ? 'on' : ''}" data-svc="${def.key}" role="switch" aria-checked="${running}" aria-label="Toggle ${def.name}" tabindex="0"></button>`;
    }

    container.innerHTML += `
      <div class="card${alertCls} ${cardCls}" role="region" aria-label="${def.name}">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <div class="font-display" style="font-size:14px">${def.name}</div>
          <span class="pill ${pillCls}">${pillText}</span>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div class="mono" style="font-size:11px;color:var(--text-secondary)">
            PID: ${pid || '--'}<br>
            ${def.readOnly ? `Alerts: ${guardrailHealth?.alerts_total || 0}` : ''}
          </div>
          ${toggleHtml}
        </div>
        <button class="info-bubble" type="button" aria-label="${def.name} info">?</button>
        <div class="info-tooltip">
          <div style="font-weight:600;margin-bottom:4px">${def.name}</div>
          <div style="margin-bottom:6px">${def.desc}</div>
          <div class="mono" style="font-size:11px;background:var(--bg-base);padding:4px 6px;border-radius:4px">${def.cmd}</div>
        </div>
      </div>`;
  }

  // Wire up toggle switches
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

/* ── Render: Active registry (LIVE vs SHADOW) ── */
function renderDbMode(status) {
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

/* ── Render: Strategy Parameters ── */
async function renderParameters() {
  try {
    const res = await fetch('/api/parameters');
    if (!res.ok) return;
    const data = await res.json();
    const body = document.getElementById('params-body');
    body.innerHTML = '';
    for (const p of data.parameters || []) {
      body.innerHTML += `<tr>
        <td class="mono">${esc(p.name)}</td>
        <td class="mono">${esc(p.value)}</td>
        <td>${esc(p.trigger)}</td>
        <td>${esc(p.action)}</td>
      </tr>`;
    }
  } catch {}
}

/* ── Render: Exposure Bar (DT3) ── */
function renderExposure(kpi) {
  const bar = document.getElementById('exposure-bar');
  const text = document.getElementById('exposure-text');
  const fill = document.getElementById('exposure-fill');

  const committed = kpi?.portfolio?.open_committed_usd || 0;
  const accountVal = kpi?.portfolio?.account?.account_value_usd || kpi?.portfolio?.starting_capital;
  const cap = accountVal ? (accountVal * 0.90) : (kpi?.bankroll || 100);
  if (committed === null || committed === undefined) {
    bar.style.display = 'none';
    return;
  }
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
  if (!card || !kpi || !kpi.run_profitability) {
    if (card) card.style.display = 'none';
    return;
  }
  const rp = kpi.run_profitability;
  card.style.display = 'block';
  card.className = 'card run-profitability ' + (rp.verdict_level || 'neutral');
  runEl.textContent = rp.run_id || '--';
  verdictEl.className = 'rp-verdict ' + (rp.verdict_level || 'neutral');
  verdictEl.textContent = rp.verdict || '--';
  // Details line: fills/quotes/closes + expectancy
  const detParts = [];
  detParts.push(`${rp.fills} fills / ${rp.quotes} quotes / ${rp.closes_count} closes`);
  if (rp.win_rate !== null && rp.win_rate !== undefined) detParts.push(`win rate ${(rp.win_rate*100).toFixed(1)}%`);
  if (rp.expectancy_usd !== null && rp.expectancy_usd !== undefined) detParts.push(`expectancy ${fmtUSD(rp.expectancy_usd)}`);
  if (rp.merge_closes !== undefined) detParts.push(`${rp.merge_closes} merges`);
  detailsEl.textContent = detParts.join(' · ');
  // Venue line: scoped to same run window, plus open-order flat check
  const venueParts = [];
  if (rp.venue_measured) {
    venueParts.push(`Venue account ${fmtUSD(rp.venue_start_value)} → ${fmtUSD(rp.venue_end_value)} (${rp.venue_delta_usd >= 0 ? '+' : ''}${fmtUSD(rp.venue_delta_usd)} during run)`);
  } else {
    venueParts.push('Venue delta: unmeasured (no sweep in run window)');
  }
  venueParts.push(`${rp.open_orders} open order(s) — ${rp.open_orders === 0 ? 'flat' : 'still resting'}`);
  if (rp.venue_open_orders !== undefined) venueParts[venueParts.length-1] += ` (venue: ${rp.venue_open_orders})`;
  venueEl.textContent = venueParts.join(' · ');
}

 /* ── Render: KPI Tiles (DT2: empty states) ── */
function renderKPIs(kpi, status) {
  renderRunProfitability(kpi);
  const grid = document.getElementById('kpi-grid');
  if (!kpi || !kpi.portfolio) {
    grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1">
      <div class="empty-state-title">No trading data yet</div>
      <div class="empty-state-msg">KPIs will appear when the bot makes its first spread capture.</div>
    </div>`;
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
  `;
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

function isCancelledStatus(status) {
  if (!status) return false;
  const s = String(status).toLowerCase();
  return s === 'cancelled' || s === 'canceled';
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
    const pairOrders = byPair[pid].sort((a,b) => (a.price||0) - (b.price||0));
    for (const o of pairOrders) {
      const oFills = fillsByOrder[o.id] || [];
      const fillCount = oFills.length;
      const statusCls = pillForStatus(o.status);
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
        <td><span class="pill ${statusCls}">${fmtOrderStatus(o.status)}</span></td>
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

  // Market order: OPEN (pure) → mixed OPEN+FILLED → pure FILLED → zero active / IDLE/FINISHED at bottom
  const entries = Object.entries(kpi.by_market);
  entries.sort((a,b) => {
    const [cidA] = a; const [cidB] = b;
    const activeA = (ordersByMarket[cidA] || []).filter(o => !isCancelledStatus(o.status));
    const activeB = (ordersByMarket[cidB] || []).filter(o => !isCancelledStatus(o.status));
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
    const activeOrders = allOrders.filter(o => !isCancelledStatus(o.status));
    const cancelledCount = allOrders.length - activeOrders.length;
    const showCancelled = showCancelledByMarket.has(cid);
    // Distinct FINISHED when market is resolved or dropped from current
    // graduated universe but still has history (shadow retains it for P/L).
    // Covers: days_to_resolve <0, venue_sync resolved, or simply not in
    // kpi.funnel.graduated anymore (e.g., Mlb Lad Atl 2026-08-27 past date).
    // HasFunnel guards: when funnel empty (no scan yet) don't mark everything.
    const hasFunnel = graduatedCids.size > 0;
    const isFinished = (m.days_to_resolve !== null && m.days_to_resolve < 0)
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
      <td><span class="pill ${isFinished ? 'finished' : (m.quotes_count > 0 ? 'quoting-breathing' : 'stopped')}">${isFinished ? 'FINISHED' : (m.quotes_count > 0 ? 'QUOTING' : 'IDLE')}</span></td>
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
  const volGate = funnel?.volume_gate_usd || 250000;
  const depthGate = funnel?.depth_gate_usd || 1000;
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

/* ── Poll loop ── */
async function pollStatus() {
  try {
    const [stateRes, statusRes, kpiRes, scanRes, guardAlertsRes, guardHealthRes] = await Promise.all([
      fetch('/api/state'),
      fetch('/api/system/status'),
      fetch('/api/kpi'),
      fetch('/api/scan-state'),
      fetch('/api/guardrail-alerts'),
      fetch('/api/guardrail-health'),
    ]);

    const status = statusRes.ok ? await statusRes.json() : null;
    const kpi = kpiRes.ok ? await kpiRes.json() : null;
    const guardAlerts = guardAlertsRes.ok ? await guardAlertsRes.json() : null;
    const guardHealth = guardHealthRes.ok ? await guardHealthRes.json() : null;

    lastState = stateRes.ok ? await stateRes.json() : null;
    lastKpi = kpi;

    // Which registry these numbers came from, before anything renders them.
    renderDbMode(status);

    // Render service cards
    renderServiceCards(status, guardHealth, guardAlerts);

    // Render exposure bar (DT3)
    renderExposure(kpi);

    // USDC Balance in top nav bar
    const collateral = kpi?.portfolio?.account?.collateral_usd ?? kpi?.portfolio?.account?.account_value_usd;
    const usdcEl = document.getElementById('usdc-balance');
    if (usdcEl) {
      usdcEl.textContent = (collateral !== null && collateral !== undefined) ? `USDC: ${fmtUSD(collateral)}` : 'USDC: --';
    }

    // Render KPIs (Tab 2)
    renderKPIs(kpi, status);
    renderMarkets(kpi, lastState);

    // Render screener kanban (Tab 3)
    const scanState = scanRes.ok ? await scanRes.json() : null;
    renderScreener(kpi, scanState);

    // Wallet address
    if (status?.services?.dash) {
      // Could fetch from /api/state for wallet info
    }

  } catch (e) {
    console.error('Poll error:', e);
  }
}

/* ── Start ── */
// Skipped only when this file is loaded as a CommonJS module, which is how the
// test harness reaches the handlers. A browser has no `module`, so the page
// bootstraps exactly as it always has -- and a harness never starts the poll
// loop, the SSE reconnect timer or the carousel behind the handler it drives.
if (typeof module === 'undefined' || !module.exports) {
  initKanbanCarousel();
  pollStatus();
  renderParameters();
  setInterval(pollStatus, POLL_MS);
}

// Node-only: lets tests reach the handlers. Browsers have no `module`, so this
// is dead code in the page.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { renderDbMode, renderServiceCards, fmtLocalTime, connectSSE, marketLink, renderMarkets, groupOrdersByMarket };
}
