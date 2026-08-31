/* Drives the sidebar prototype's navigation against a stub DOM and prints
 * which page is visible after a sequence of clicks. Used by
 * tests/test_sidebar_prototype.py.
 *
 * Reads {clicks: [pageName...], stored: <page|null>} as JSON on argv[2].
 */
const fs = require('fs');
const path = require('path');

const input = JSON.parse(process.argv[2]);
const noop = () => {};

const PAGES = ['home', 'data-markets', 'strategy', 'trades', 'reports'];

const sections = {};
const buttons = [];
for (const page of PAGES) {
  sections['page-' + page] = { id: 'page-' + page, hidden: page !== 'home' };
  const attrs = {};
  buttons.push({
    dataset: { page },
    attrs,
    handlers: [],
    addEventListener(_evt, fn) { this.handlers.push(fn); },
    setAttribute(name, value) { attrs[name] = value; },
    removeAttribute(name) { delete attrs[name]; },
    getAttribute(name) { return name in attrs ? attrs[name] : null; },
    click() { this.handlers.forEach(fn => fn()); },
  });
}

const toggleAttrs = {};
const toggle = {
  id: 'proto-nav-toggle',
  handlers: [],
  addEventListener(_evt, fn) { this.handlers.push(fn); },
  setAttribute(name, value) { toggleAttrs[name] = value; },
  getAttribute(name) { return name in toggleAttrs ? toggleAttrs[name] : null; },
  click() { this.handlers.forEach(fn => fn()); },
};

const bodyClasses = new Set();
const doc = {
  getElementById: (id) => (id === 'proto-nav-toggle' ? toggle : (sections[id] || null)),
  querySelectorAll: (sel) => (sel === '.proto-nav-btn' ? buttons : []),
  body: {
    classList: {
      toggle(name, on) { if (on) bodyClasses.add(name); else bodyClasses.delete(name); },
      contains: (name) => bodyClasses.has(name),
    },
  },
};

let stored = input.stored === undefined ? null : input.stored;
global.document = doc;
global.window = {
  localStorage: {
    getItem: () => stored,
    setItem: (_k, v) => { stored = v; },
  },
};

const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'dashboard', 'static', 'prototype.js'), 'utf8');
const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', source)(
  mod, mod.exports, doc, global.window);
const proto = mod.exports;

proto.initPrototype(doc);
for (const page of (input.clicks || [])) {
  const button = buttons.find(b => b.dataset.page === page);
  if (!button) throw new Error('no nav button for ' + page);
  button.click();
}
if (input.toggleNav) toggle.click();

process.stdout.write(JSON.stringify({
  visible: PAGES.filter(p => sections['page-' + p].hidden === false),
  current: buttons.filter(b => b.getAttribute('aria-current') === 'page').map(b => b.dataset.page),
  stored,
  nav_open: bodyClasses.has('proto-nav-open'),
  toggle_expanded: toggle.getAttribute('aria-expanded'),
}));
