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
  'filter|rerank_error': 'Market scan failed. The graduated list was not updated, so the Trader keeps quoting the previous universe.',
  'screener|rerank_done': 'Finished scanning all Polymarket markets and updated the graduated list.',
  'screener|rerank_error': 'Market scan failed. The graduated list was not updated, so the Trader keeps quoting the previous universe.',

  // Query — reconciliation
  'query|reconcile_ok': 'Checked the venue for new fills on our orders. All synced up.',
  'query|reconcile_error': 'Failed to sync fills from the venue. Orders may be stale until the next query.',
  'query|reconcile_contended': 'Another process is reconciling fills right now. Waiting in line to avoid double-counting.',
  'engine|reconcile_ok': 'Checked the venue for new fills on our orders. All synced up.',
  'engine|reconcile_error': 'Failed to sync fills from the venue. Orders may be stale until the next poll.',
  'engine|reconcile_contended': 'Another process is reconciling fills right now. Waiting in line to avoid double-counting.',

  // Query — account sweep
  'query|sweep_done': 'Read the live wallet balance and open positions from Polymarket. Dashboard tiles are now fresh.',
  'query|sweep_skipped': 'Skipped the wallet sweep this cycle (not due yet or rate-limited).',
  'query|sweep_error': 'Failed to read the wallet from Polymarket. Balance and exposure tiles may be stale.',
  'engine|sweep_done': 'Read the live wallet balance and open positions from Polymarket. Dashboard tiles are now fresh.',
  'engine|sweep_skipped': 'Skipped the wallet sweep this cycle (not due yet or rate-limited).',
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
      const ts = ev.ts ? ev.ts.split('T')[1]?.replace('Z','') : '';
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
  { key: 'query', name: 'Query Polymarket', cmd: 'python -m engine.order_manager poll --interval 0.5',
    desc: 'Queries CLOB every 0.5s, reconciles fills, executes account sweeps.' },
  { key: 'decide', name: 'Decide & Execute', cmd: 'python -m engine.trader_loop --live --no-reconcile --no-sweep --interval 5',
    desc: 'Runs the trading loop (decide quotes -> submit maker orders) every 5s across approved markets.' },
  { key: 'guardrail', name: 'Guardrail Watchdog', cmd: 'python -m scripts.guardrail_watch',
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

  const committed = kpi?.portfolio?.open_committed_usd;
  const cap = kpi?.bankroll || 100;
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

/* ── Render: KPI Tiles (DT2: empty states) ── */
function renderKPIs(kpi) {
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
  const startCap = p.starting_capital;
  const realized = p.realized_pnl;
  const unrealized = p.unrealized_usd;
  const total = p.total_pnl;

  grid.innerHTML = `
    <div class="kpi-tile">
      <div class="kpi-label">Net Portfolio Value</div>
      ${fmtVal(p.total_value !== null && p.total_value !== undefined ? fmtUSD(p.total_value) : null)}
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

  // Toggle bar (only when there are cancelled orders to toggle)
  if (cancelledOrders.length > 0) {
    html += `<div class="cancelled-toggle-bar">
      <button class="toggle-cancelled-btn" type="button">
        ${showCancelled ? 'Hide' : 'Show'} ${cancelledOrders.length} cancelled order${cancelledOrders.length !== 1 ? 's' : ''}
      </button>
    </div>`;
  }

  html += `<table class="orders-subtable">`;
  html += `<thead><tr>
    <th>Side</th>
    <th>Price</th>
    <th>Size</th>
    <th>Filled</th>
    <th>Remaining</th>
    <th>Status</th>
    <th>Pair</th>
    <th>Age</th>
    <th>Fills</th>
  </tr></thead><tbody>`;

  for (const o of displayOrders) {
    const oFills = fillsByOrder[o.id] || [];
    const fillCount = oFills.length;
    const statusCls = o.status === 'open' ? 'active' : (o.status === 'filled' ? 'active' : (isCancelledStatus(o.status) ? 'stopped' : 'reconnecting'));
    html += `<tr${isCancelledStatus(o.status) ? ' class="order-cancelled"' : ''}>
      <td class="mono"><span class="side-${(o.side||'').toLowerCase()}">${fmtSide(o.side)}</span></td>
      <td class="mono">${esc(o.price !== null && o.price !== undefined ? o.price.toFixed(4) : '--')}</td>
      <td class="mono">${esc(o.original_size !== null && o.original_size !== undefined ? o.original_size : '--')}</td>
      <td class="mono">${esc(o.size_matched !== null && o.size_matched !== undefined ? o.size_matched : '--')}</td>
      <td class="mono">${esc(o.size_remaining !== null && o.size_remaining !== undefined ? o.size_remaining : '--')}</td>
      <td><span class="pill ${statusCls}">${fmtOrderStatus(o.status)}</span></td>
      <td class="mono" style="font-size:10px;color:var(--text-muted)">${esc(o.pair_id ? o.pair_id.slice(0,12) : '--')}</td>
      <td class="mono" style="font-size:11px;color:var(--text-secondary)">${fmtOrderAge(o.age_sec)}</td>
      <td class="mono">${fillCount > 0 ? fillCount : '--'}</td>
    </tr>`;
  }

  html += `</tbody></table>`;
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

  body.innerHTML = '';
  for (const [cid, m] of Object.entries(kpi.by_market)) {
    const fills_count = m.fills_count || 0;
    const hedged = m.balance !== null && m.balance !== undefined && m.balance >= 0.99 ? 'Hedged' : 'One-Sided';
    const isExpanded = expandedMarkets.has(cid);
    const hasOrders = ordersByMarket[cid] && ordersByMarket[cid].length > 0;
    const allOrders = hasOrders ? ordersByMarket[cid] : [];
    const activeOrders = allOrders.filter(o => !isCancelledStatus(o.status));
    const cancelledCount = allOrders.length - activeOrders.length;
    const showCancelled = showCancelledByMarket.has(cid);

    // Badge: active count, plus muted cancelled count when present
    let badgeHtml = '';
    if (hasOrders) {
      badgeHtml = `<span class="order-count-badge">${activeOrders.length} active</span>`;
      if (cancelledCount > 0) {
        badgeHtml += ` <span class="order-count-badge cancelled-count">${cancelledCount} cancelled</span>`;
      }
    }
    // Main row — clickable to expand
    body.innerHTML += `<tr class="market-row${isExpanded ? ' expanded' : ''}" data-cid="${esc(cid)}" tabindex="0" role="button" aria-expanded="${isExpanded}" aria-label="${isExpanded ? 'Collapse' : 'Expand'} market orders for ${esc(m.title || m.slug || cid.slice(0,10))}">
      <td>
        <span class="expand-chevron${isExpanded ? ' expanded' : ''}" aria-hidden="true">${hasOrders ? '▶' : ''}</span>
        ${esc(m.title || m.slug || cid.slice(0,10))}
        ${badgeHtml}
      </td>
      <td class="mono">${fmtUSD(m.total_cost)}</td>
      <td class="mono">${esc(m.realized_pnl !== null && m.realized_pnl !== undefined ? fmtUSD(m.realized_pnl) : '--')}</td>
      <td><span class="pill ${hedged === 'Hedged' ? 'active' : 'reconnecting'}">${fills_count} (${hedged})</span></td>
      <td><span class="pill ${m.quotes_count > 0 ? 'active' : 'stopped'}">${m.quotes_count > 0 ? 'QUOTING' : 'IDLE'}</span></td>
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

    row.addEventListener('click', () => {
      if (expandedMarkets.has(cid)) {
        expandedMarkets.delete(cid);
      } else {
        expandedMarkets.add(cid);
      }
      renderMarkets(kpi, state);
    });

    row.addEventListener('keydown', (e) => {
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
  { key: 'raw', name: 'Raw Fetch', cls: 'raw' },
  { key: 'identity', name: 'Identity Gate', cls: 'rejected' },
  { key: 'volume', name: 'Volume Gate', cls: 'rejected' },
  { key: 'depth', name: 'Depth Gate', cls: 'rejected' },
  { key: 'spread', name: 'Spread Gate', cls: 'rejected' },
  { key: 'horizon', name: 'Horizon Gate', cls: 'rejected' },
  { key: 'passed', name: 'Passed (Quoting)', cls: 'passed' },
];

function fmtAge(sec) {
  if (sec === null || sec === undefined) return '--';
  if (sec < 60) return Math.round(sec) + 's ago';
  if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
  return Math.floor(sec / 3600) + 'h ago';
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
      <div class="empty-state-msg">The screener writes run/pipeline.json on each scan cycle. Data will appear here when the screener runs.</div>
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

  // Build a map of rejection buckets by cause for quick lookup
  const filterMap = {};
  for (const f of (funnel.filters || [])) {
    filterMap[f.cause] = f;
  }

  // Render the 7 buckets
  board.innerHTML = '';
  for (const def of BUCKET_DEFS) {
    let count = 0;
    let cardsHtml = '';
    let footerHtml = '';

    if (def.key === 'raw') {
      count = funnel.raw_count || 0;
      cardsHtml = `<div class="kanban-empty">${count} markets fetched from venue</div>`;
    } else if (def.key === 'passed') {
      const graduated = funnel.graduated || [];
      count = graduated.length;
      if (count === 0) {
        cardsHtml = `<div class="kanban-empty">No markets graduated. The screener hasn't found any qualifying markets.</div>`;
      } else {
        for (const m of graduated) {
          const income = m.pnl !== null && m.pnl !== undefined ? fmtUSD(m.pnl) : '--';
          cardsHtml += `<div class="market-card" role="listitem">
            <div class="card-title">${esc(m.title || m.slug || m.condition_id?.slice(0,10) || '--')}</div>
            <div class="card-metric">Fills: <span class="card-fills">${esc(m.fills || 0)}</span> | P&L: <span class="card-income">${income}</span></div>
          </div>`;
        }
      }
    } else {
      // Rejection buckets: look up by cause name
      const filter = filterMap[def.key] || filterMap[def.key + ' spread'];
      if (filter) {
        count = filter.n || 0;
        const examples = filter.examples || [];
        if (examples.length === 0) {
          cardsHtml = `<div class="kanban-empty">All markets passed this gate.</div>`;
        } else {
          for (const ex of examples) {
            cardsHtml += `<div class="market-card" role="listitem">
              <div class="card-title">${esc(ex.title || '--')}</div>
              <div class="card-reason">${esc(ex.reason || '--')}</div>
            </div>`;
          }
        }
        // Near-miss footer
        const wouldFund = filter.would_fund;
        if (wouldFund !== undefined && wouldFund > 0) {
          footerHtml = `<div class="kanban-bucket-footer">${wouldFund} would clear allocator floor</div>`;
        }
      } else {
        cardsHtml = `<div class="kanban-empty">All markets passed this gate.</div>`;
      }
    }

    const countBadgeCls = def.key === 'passed' ? 'ok' : (count > 0 && def.cls === 'rejected' ? 'alert' : '');
    board.innerHTML += `<div class="kanban-bucket ${def.cls}" role="list" aria-label="${def.name}">
      <div class="kanban-bucket-header">
        ${def.name}
        <span class="bucket-count-badge ${countBadgeCls}">${count}</span>
      </div>
      <div class="kanban-bucket-body">${cardsHtml}</div>
      ${footerHtml}
    </div>`;
  }
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

    // Render service cards
    renderServiceCards(status, guardHealth, guardAlerts);

    // Render exposure bar (DT3)
    renderExposure(kpi);

    // Render KPIs (Tab 2)
    renderKPIs(kpi);
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
pollStatus();
renderParameters();
setInterval(pollStatus, POLL_MS);
