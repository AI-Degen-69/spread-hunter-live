/* Harness: test fmtLocalTime without a browser.
 *
 * Loaded by tests/test_dashboard_server.py.
 * Prints one JSON line with sample conversion results.
 */
'use strict';

const appJsPath = process.argv[2];

function fakeClassList() {
  const set = new Set();
  return {
    add: (c) => set.add(c),
    remove: (c) => set.delete(c),
    contains: (c) => set.has(c),
  };
}

class FakeEl {
  constructor(id) {
    this.id = id;
    this._html = '';
    this.className = '';
    this.textContent = '';
    this.title = '';
    this.style = {};
    this.dataset = {};
    this.classList = fakeClassList();
  }
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
  addEventListener() {}
  querySelectorAll() { return []; }
  appendChild() {}
  setAttribute() {}
  getAttribute() { return null; }
}

const elements = new Map();
global.document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new FakeEl(id));
    return elements.get(id);
  },
  querySelectorAll() { return []; },
  createElement() { return new FakeEl('created'); },
  addEventListener() {},
  body: new FakeEl('body'),
};
global.window = { addEventListener() {}, matchMedia: () => ({ matches: false }) };
global.CONTROL_TOKEN = 'harness-token';
global.EventSource = function () { return { addEventListener() {}, close() {} }; };
global.setInterval = () => 0;
global.localStorage = {
  _v: {},
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._v, k) ? this._v[k] : null; },
  setItem(k, v) { this._v[k] = String(v); },
};

const mod = require(appJsPath);

const results = {
  valid: typeof mod.fmtLocalTime === 'function' ? mod.fmtLocalTime('2024-01-15T14:28:00Z') : null,
  empty: typeof mod.fmtLocalTime === 'function' ? mod.fmtLocalTime('') : null,
  invalid: typeof mod.fmtLocalTime === 'function' ? mod.fmtLocalTime('not-a-date') : null,
  nullVal: typeof mod.fmtLocalTime === 'function' ? mod.fmtLocalTime(null) : null,
};

process.stdout.write(JSON.stringify(results));
