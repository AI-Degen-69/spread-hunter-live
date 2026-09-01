/* Prints the sidebar layout map and the path gate from prototype.js, so
 * tests/test_sidebar_prototype.py can check which live panels land on which
 * page without a browser.
 */
const fs = require('fs');
const path = require('path');

const source = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'dashboard', 'static', 'prototype.js'), 'utf8');

const mod = { exports: {} };
new Function('module', 'exports', 'document', 'window', source)(
  mod, mod.exports, undefined, undefined);
const proto = mod.exports;

process.stdout.write(JSON.stringify({
  pages: proto.PAGES,
  layout: proto.PAGE_LAYOUT.map(entry => ({
    page: entry.page,
    label: entry.label,
    selectors: entry.selectors,
  })),
  layout_paths: proto.LAYOUT_PATHS,
  mounts_on_prototype: proto.shouldMount({ pathname: '/prototype' }),
  mounts_on_prototype_slash: proto.shouldMount({ pathname: '/prototype/' }),
  mounts_on_root: proto.shouldMount({ pathname: '/' }),
}));
