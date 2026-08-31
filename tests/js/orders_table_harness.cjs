/* Drives renderExpandedOrders against a stub DOM and prints the rendered HTML
 * plus the derived active-order count. Used by tests/test_merged_pair_row.py.
 *
 * Reads {orders, fills, showCancelled} as JSON on argv[2].
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
// app.js opens its SSE stream at load time, outside the module guard.
global.EventSource = function EventSource() {
  return { addEventListener: noop, close: noop, onerror: null, onmessage: null };
};

// Evaluated as CommonJS on purpose: package.json declares "type": "module", and
// app.js is a plain browser script that bootstraps itself unless a
// `module.exports` is present. Wrapping it hands it both a module object and
// the stub globals it renders against.
const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'dashboard', 'static', 'app.js'), 'utf8');
const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', 'localStorage', 'EventSource', source)(
  mod, mod.exports, global.document, global.window, global.localStorage, global.EventSource);
const app = mod.exports;

const html = app.renderExpandedOrders(input.orders, input.fills || [], !!input.showCancelled);
process.stdout.write(JSON.stringify({
  html,
  rows: (html.match(/<tr /g) || []).length,
  merged_rows: (html.match(/>MERGED</g) || []).length,
  active_count: input.orders.filter(o => app.isActiveOrder(o)).length,
}));
