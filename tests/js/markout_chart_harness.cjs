/* Renders the adverse-selection markout chart against a stub DOM and prints the
 * SVG plus the footer text. Used by tests/test_markout_chart.py.
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

app.renderMarkoutChart(input.statistical_analytics || {});

const svg = stubElement('markout-svg-container').innerHTML;

// Every `height="..."` and `y="..."` the chart emitted, so a test can assert
// what SVG itself would reject rather than reading it back off a screenshot.
const heights = (svg.match(/height="(-?[\d.]+)"/g) || [])
  .map(m => parseFloat(m.slice(8, -1)));
const rects = (svg.match(/<rect[^>]*>/g) || []).map(tag => ({
  y: parseFloat((tag.match(/\by="(-?[\d.]+)"/) || [])[1]),
  height: parseFloat((tag.match(/height="(-?[\d.]+)"/) || [])[1]),
  fill: (tag.match(/fill="([^"]+)"/) || [])[1] || '',
}));

process.stdout.write(JSON.stringify({
  svg,
  heights,
  rects,
  labels: (svg.match(/>([+-][\d.]+ bps)</g) || []).map(m => m.slice(1, -1)),
  zeroLineY: parseFloat(
    (svg.match(/<line x1="[\d.]+" y1="([\d.]+)"/) || [])[1]),
  footer: stubElement('markout-footer').innerHTML
    .replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim(),
  chartEmpty: /Markout unmeasured/.test(svg),
}));
