/* Harness: test fmtLocalTime and SSE ticker formatting without a browser.
 *
 * Loaded by tests/test_dashboard_server.py.
 * Prints one JSON line with sample conversion results and ticker event output.
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

let sseInstance = null;
global.EventSource = function (url) {
  this.url = url;
  this.addEventListener = () => {};
  this.close = () => {};
  sseInstance = this;
};
global.setInterval = () => 0;
global.localStorage = {
  _v: {},
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._v, k) ? this._v[k] : null; },
  setItem(k, v) { this._v[k] = String(v); },
};

const mod = require(appJsPath);

const sampleTs = '2024-01-15T14:28:00Z';
const expectedTime = new Date(sampleTs).toLocaleTimeString();

// Direct helper verification
const valid = typeof mod.fmtLocalTime === 'function' ? mod.fmtLocalTime(sampleTs) : null;
const empty = typeof mod.fmtLocalTime === 'function' ? mod.fmtLocalTime('') : null;
const invalid = typeof mod.fmtLocalTime === 'function' ? mod.fmtLocalTime('not-a-date') : null;
const nullVal = typeof mod.fmtLocalTime === 'function' ? mod.fmtLocalTime(null) : null;

// SSE Ticker integration verification
let tickerHtml = null;
if (typeof mod.connectSSE === 'function') {
  mod.connectSSE();
  if (sseInstance && typeof sseInstance.onmessage === 'function') {
    sseInstance.onmessage({
      data: JSON.stringify({
        ts: sampleTs,
        service: 'decide',
        action: 'eval_market',
        market_slug: 'btc-up',
      }),
    });
    const tickerEl = document.getElementById('event-ticker');
    const firstEvent = tickerEl.firstChild;
    tickerHtml = firstEvent ? firstEvent.innerHTML : null;
  }
}

const results = {
  expected: expectedTime,
  valid,
  empty,
  invalid,
  nullVal,
  tickerHtml,
};

process.stdout.write(JSON.stringify(results));
