/* Renders the decision-gates panel from a kpi payload and prints the HTML plus
 * the per-row verdicts. Used by tests/test_decision_gates.py.
 *
 * Reads {trade_analytics, statistical_analytics, n} as JSON on argv[2].
 */
const fs = require('fs');
const path = require('path');

const input = JSON.parse(process.argv[2]);

const noop = () => {};
function stubElement(id) {
  return {
    id,
    textContent: '',
    innerHTML: '',
    className: '',
    title: '',
    style: {},
    dataset: {},
    classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
    addEventListener: noop,
    querySelector: () => null,
    querySelectorAll: () => [],
    appendChild: noop,
    setAttribute: noop,
    getAttribute: () => null,
  };
}

global.document = {
  getElementById: (id) => stubElement(id),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: noop,
  body: { classList: { add: noop, remove: noop, toggle: noop } },
};
global.window = { addEventListener: noop, location: { href: '' } };
global.localStorage = { getItem: () => null, setItem: noop, removeItem: noop };
global.EventSource = function EventSource() {
  return { addEventListener: noop, close: noop, onerror: null, onmessage: null };
};

const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'dashboard', 'static', 'app.js'), 'utf8');
const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', 'localStorage', 'EventSource', source)(
  mod, mod.exports, global.document, global.window, global.localStorage, global.EventSource);
const app = mod.exports;

const html = app.decisionGatesHtml(input.trade_analytics, input.statistical_analytics, input.n);
const rows = app.decisionGatesRows(input.trade_analytics || {}, input.statistical_analytics || {}, input.n || 0);

process.stdout.write(JSON.stringify({
  html,
  verdicts: rows.map(r => ({ name: String(r.name).replace(/<[^>]*>/g, ''), state: r.state, measured: !!r.measured })),
  badge_classes: (html.match(/analytics-gate-badge [a-z]+/g) || []),
}));
