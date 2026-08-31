/* Drives renderBrokerPortfolioOverview against a stub DOM and prints what the
 * Portfolio Overview card would show. Used by tests/test_portfolio_card_basis.py.
 *
 * Reads one JSON payload {kpi, status} on argv[2] and writes the rendered
 * values as JSON on stdout.
 */
const fs = require('fs');
const path = require('path');

const input = JSON.parse(process.argv[2]);
const elements = {};

// The chart needs a real SVG surface; returning null makes renderBrokerPortfolioChart
// bail at its own guard, which is what we want -- this harness measures the card.
const NULL_IDS = new Set(['broker-chart-svg-container', 'broker-chart-tooltip']);

function element(id) {
  if (!elements[id]) {
    elements[id] = {
      id,
      textContent: '',
      innerHTML: '',
      className: '',
      title: '',
      style: {},
      classList: { add() {}, remove() {}, contains: () => false, toggle() {} },
      dataset: {},
      querySelector: () => null,
      querySelectorAll: () => [],
      appendChild() {},
      setAttribute() {},
      getAttribute: () => null,
      addEventListener() {},
    };
  }
  return elements[id];
}

global.document = {
  getElementById: (id) => (NULL_IDS.has(id) ? null : element(id)),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener() {},
  body: { classList: { add() {}, remove() {}, toggle() {} } },
};
global.window = { addEventListener() {}, location: { href: '' } };
global.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
// app.js opens its SSE stream at load time, outside the module guard.
global.EventSource = function EventSource() {
  return { addEventListener() {}, close() {}, onerror: null, onmessage: null };
};
global.fetch = () => Promise.resolve({ ok: false, json: async () => ({}) });
global.setTimeout = global.setTimeout;

// Evaluated as CommonJS on purpose: package.json declares "type": "module",
// and app.js is a plain browser script that bootstraps itself unless a
// `module.exports` is present. Wrapping it hands it both a module object and
// the stub globals it renders against.
const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'dashboard', 'static', 'app.js'), 'utf8');
const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', 'localStorage', 'EventSource', source)(
  mod, mod.exports, global.document, global.window, global.localStorage, global.EventSource);
const app = mod.exports;
app.renderBrokerPortfolioOverview(input.kpi, input.status);

// The chart's own basis, read through the shared helper the chart uses, so the
// test can assert the headline and the chart's final point agree.
const basis = app.portfolioEquity(input.kpi, input.status);

process.stdout.write(JSON.stringify({
  chart_total: basis.totalVal,
  chart_starting_capital: basis.startingCap,
  equity: element('broker-hero-equity').textContent,
  pnl: element('broker-pnl-amount').textContent,
  starting_capital: element('broker-starting-cap').textContent,
  cash: element('broker-kpi-cash').textContent,
  wallet_row_display: element('broker-venue-wallet-row').style.display,
  wallet: element('broker-venue-wallet').textContent,
  wallet_note: element('broker-venue-wallet-note').textContent,
}));
