import { test } from 'node:test';
import assert from 'node:assert/strict';
import { allowControl } from '../../server.ts';

test('control is off by default', () => {
  delete process.env.BRIDGE_CONTROL;
  assert.equal(allowControl(), false);
});

test('control is on only for explicit 1', () => {
  process.env.BRIDGE_CONTROL = '1';
  assert.equal(allowControl(), true);
});

test('any other value keeps it off', () => {
  process.env.BRIDGE_CONTROL = 'yes';
  assert.equal(allowControl(), false);
});