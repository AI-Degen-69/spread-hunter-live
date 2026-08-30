import { test } from 'node:test';
import assert from 'node:assert/strict';

// Import the pure functions from server.ts (type stripping handles the .ts import).
import { scrapeControlToken, injectToken } from '../../server.ts';

test('scrapeControlToken extracts the token Python baked into HTML', () => {
  const html = '<html><script>const CONTROL_TOKEN = "abc123";</script></html>';
  assert.equal(scrapeControlToken(html), 'abc123');
});

test('scrapeControlToken returns empty string when token missing', () => {
  assert.equal(scrapeControlToken('<html>no token</html>'), '');
});

test('injectToken replaces the placeholder', () => {
  const html = 'const CONTROL_TOKEN = "__LIVE_DASH_CONTROL_TOKEN__";';
  assert.equal(injectToken(html, 'xyz'), 'const CONTROL_TOKEN = "xyz";');
});

test('injectToken leaves HTML alone when token is empty', () => {
  const html = 'const CONTROL_TOKEN = "__LIVE_DASH_CONTROL_TOKEN__";';
  assert.equal(injectToken(html, ''), html);
});