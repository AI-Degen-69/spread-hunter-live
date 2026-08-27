/* Harness: test renderMarkets helper without a browser.
 *
 * Loaded by tests/test_dashboard_server.py.
 * Prints one JSON line with market table render results.
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
  querySelectorAll(sel) {
    return [];
  }
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

// Sample test data with distinct values for each metric, plus a market with null realized_pnl
const sampleKpi = {
  by_market: {
    '0xcond1234': {
      title: 'Will BTC hit 100k by March?',
      slug: 'btc-100k-march',
      total_cost: 42.50,
      realized_pnl: 15.75,
      balance: 1.0, // => Hedged
      fills_count: 7,
      quotes_count: 3, // => QUOTING
    },
    '0xcond5678': {
      title: 'Will ETH hit 4k by April?',
      slug: 'eth-4k-april',
      total_cost: 10.00,
      realized_pnl: null, // => null realized PnL should render '--'
      balance: 0.5, // => One-Sided
      fills_count: 2,
      quotes_count: 0, // => IDLE
    },
  },
};

const sampleState = {
  orders: [
    {
      condition_id: '0xcond1234',
      id: 'ord-1',
      status: 'open',
      side: 'BUY',
      token_side: 'UP',
      price: 0.48,
      original_size: 10,
      size_matched: 5,
      size_remaining: 5,
      pair_id: 'pair-abc',
      age_sec: 12,
    },
  ],
  fills: [],
};

let output = {};
if (typeof mod.renderMarkets === 'function') {
  mod.renderMarkets(sampleKpi, sampleState);
  const marketBody = document.getElementById('market-body');
  const html = marketBody.innerHTML;

  // Extract all market-row elements
  const rowMatches = [...html.matchAll(/<tr[^>]*class="market-row[^"]*"[^>]*>([\s\S]*?)<\/tr>/gi)];
  if (rowMatches.length > 0) {
    const rows = rowMatches.map((rm) => {
      const rowContent = rm[1];
      const tdMatches = [...rowContent.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/gi)];
      return tdMatches.map((m) => m[1].replace(/<[^>]+>/g, '').trim());
    });
    output = {
      rendered: true,
      html: html,
      cellCount: rows[0].length,
      cells: rows[0],
      nullPnlCells: rows[1] || [],
    };
  } else {
    output = { rendered: false, html: html, error: 'No market-row found' };
  }
} else {
  output = { rendered: false, error: 'renderMarkets not exported' };
}

process.stdout.write(JSON.stringify(output));
