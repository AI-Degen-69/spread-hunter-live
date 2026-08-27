/* Harness: test marketLink helper without a browser.
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
    this.children = [];
    this.classList = fakeClassList();
  }
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
  addEventListener() {}
  querySelectorAll() { return []; }
  querySelector() { return null; }
  appendChild(c) { this.children.push(c); }
  insertBefore(c) { this.children.unshift(c); }
  removeChild(c) {
    const idx = this.children.indexOf(c);
    if (idx !== -1) this.children.splice(idx, 1);
  }
  get firstChild() { return this.children[0] || null; }
  get lastChild() { return this.children[this.children.length - 1] || null; }
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
  createElement(tag) { return new FakeEl(tag); },
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
  withSlug: typeof mod.marketLink === 'function' ? mod.marketLink({ slug: 'btc-up', title: 'BTC UP' }) : null,
  withUrl: typeof mod.marketLink === 'function' ? mod.marketLink({ url: 'https://polymarket.com/market/eth-down', title: 'ETH DOWN' }) : null,
  noUrlOrSlug: typeof mod.marketLink === 'function' ? mod.marketLink({ title: 'Plain Market' }) : null,
  nullVal: typeof mod.marketLink === 'function' ? mod.marketLink(null) : null,
  xss: typeof mod.marketLink === 'function' ? mod.marketLink({ slug: 'test"onclick="alert(1)', title: '<script>alert(1)</script>' }) : null,
};

process.stdout.write(JSON.stringify(results));
