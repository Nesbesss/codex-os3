import test from 'node:test';
import assert from 'node:assert/strict';
import { createIntake, formatReport, validWebhookUrl, validateReport } from '../lib/intake.js';

const report = { kind: 'user_report', level: 'info', version: '0.3.2', os: 'Darwin 25.2.0', installId: 'abcdef1234', message: 'broken after update' };
function request(body = report, headers = {}) {
  return new Request('https://example.vercel.app/api/report', { method: 'POST', headers: { 'content-type': 'application/json', 'x-forwarded-for': '203.0.113.10', ...headers }, body: typeof body === 'string' ? body : JSON.stringify(body) });
}
function setup({ limit = async () => true, send = async () => true } = {}) {
  return createIntake({ limit, send });
}

test('accepts a valid report and preserves the importer format', async () => {
  let content;
  const result = await setup({ send: async value => { content = value; return true; } })(request());
  assert.equal(result.status, 202);
  assert.match(content, /^📝 \*\*user_report\*\* · os3-router 0\.3\.2/);
  assert.match(content, /install `abcdef1234`\n>>> broken after update$/);
  assert.equal(result.headers.get('cache-control'), 'no-store');
});

test('strict payload validation and size limit', async () => {
  let sent = 0;
  const intake = setup({ send: async () => { sent++; return true; } });
  for (const body of [{ ...report, extra: true }, { ...report, kind: 'forged' }, { ...report, installId: 'bad' }, { ...report, message: '' }, { ...report, message: 'x'.repeat(1501) }, { ...report, os: 'Windows\n@everyone' }]) {
    assert.equal((await intake(request(body))).status, 400);
  }
  assert.equal((await intake(request('not-json'))).status, 400);
  assert.equal((await intake(request({ ...report, message: 'x'.repeat(4000) }))).status, 413);
  assert.equal(sent, 0);
});

test('limits by WAF scopes and fails closed on limiter errors', async () => {
  const scopes = [];
  const intake = setup({ limit: async (scope, key) => { scopes.push([scope, key]); return scope !== 'global'; } });
  assert.equal((await intake(request())).status, 429);
  assert.deepEqual(scopes.map(([scope]) => scope), ['ip', 'global']);
  assert.notEqual(scopes[0][1], '203.0.113.10');
  assert.equal((await setup({ limit: async () => { throw Error('redis down'); } })(request())).status, 503);
});

test('safe upstream failures and no spoofable missing IP', async () => {
  assert.equal((await setup({ send: async () => false })(request())).status, 502);
  assert.equal((await setup()(request(report, { 'x-forwarded-for': '1.2.3.4, 5.6.7.8' }))).status, 503);
  assert.equal((await setup()(request(report, { 'content-type': 'text/plain' }))).status, 415);
});

test('webhook URL must be a Discord HTTPS webhook', () => {
  assert.equal(validWebhookUrl('https://discord.com/api/webhooks/123/abc_def'), true);
  assert.equal(validWebhookUrl('https://evil.example/api/webhooks/123/abc_def'), false);
  assert.equal(validWebhookUrl('http://discord.com/api/webhooks/123/abc_def'), false);
  assert.equal(validWebhookUrl('https://discord.com/api/webhooks/123/abc_def?x=1'), false);
  assert.equal(validateReport(report), true);
  assert.match(formatReport({ ...report, kind: 'error', level: 'error' }), /^🔴 \*\*error\*\*/);
});
