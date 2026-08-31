/* Renders the position-return distribution chart against a stub DOM and prints
 * the badge and footer text. Used by tests/test_statistical_analytics.py.
 *
 * Reads {statistical_analytics} as JSON on argv[2].
 */
const fs = require('fs');
const path = require('path');

const input = JSON.parse(process.argv[2]);
const noop = () => {};
const elements = {};

function stubElement(id) {
  if (!elements[id]) {
    elements[id] = {
      id, textContent: '', innerHTML: '', className: '', title: '',
      style: {}, dataset: {},
      classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
      addEventListener: noop,
      querySelector: () => null, querySelectorAll: () => [],
      appendChild: noop, setAttribute: noop, getAttribute: () => null,
    };
  }
  return elements[id];
}

global.document = {
  getElementById: stubElement,
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

app.renderPositionDistributionChart(input.statistical_analytics || {});

process.stdout.write(JSON.stringify({
  badge: stubElement('dist-ci-badge').textContent,
  footer: stubElement('position-dist-footer').innerHTML.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim(),
  banner: stubElement('dist-power-banner').innerHTML.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim(),
  chartEmpty: /No closed positions recorded/.test(stubElement('position-dist-svg-container').innerHTML),
}));
