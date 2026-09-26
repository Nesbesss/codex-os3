import { createHash } from 'node:crypto';
import { isIP } from 'node:net';

export const MAX_BODY_BYTES = 4096;
const KINDS = new Set(['selffix', 'selffix_action', 'codex_update', 'update_failed', 'restart_agent', 'error', 'fallback', 'selftest', 'user_report']);
const LEVELS = new Set(['error', 'warn', 'info']);
const FIELDS = ['kind', 'level', 'version', 'os', 'installId', 'message'];
const ICON = { error: '🔴', warn: '🟠', info: '🟢' };

function reply(status, error) {
  return Response.json(error ? { error } : { accepted: true }, { status, headers: { 'Cache-Control': 'no-store' } });
}

export function validWebhookUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && url.hostname === 'discord.com' && !url.username && !url.password &&
      !url.search && !url.hash && /^\/api\/webhooks\/\d+\/[A-Za-z0-9._-]+$/.test(url.pathname);
  } catch { return false; }
}

async function readJson(request) {
  const length = request.headers.get('content-length');
  if (length && (!/^\d+$/.test(length) || Number(length) > MAX_BODY_BYTES)) return { error: 'too_large' };
  const reader = request.body?.getReader();
  if (!reader) return { error: 'invalid_payload' };
  const chunks = [];
  let bytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > MAX_BODY_BYTES) { await reader.cancel(); return { error: 'too_large' }; }
      chunks.push(value);
    }
    const text = new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks.map(chunk => Buffer.from(chunk))));
    return { value: JSON.parse(text) };
  } catch { return { error: 'invalid_payload' }; }
}

export function validateReport(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).length !== FIELDS.length || Object.keys(value).some(key => !FIELDS.includes(key))) return false;
  if (!KINDS.has(value.kind) || !LEVELS.has(value.level)) return false;
  if (typeof value.version !== 'string' || !/^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]{1,24})?$/.test(value.version)) return false;
  if (typeof value.os !== 'string' || !/^[A-Za-z0-9._ -]{1,80}$/.test(value.os)) return false;
  if (typeof value.installId !== 'string' || !/^[a-f0-9]{10}$/.test(value.installId)) return false;
  if (typeof value.message !== 'string' || value.message.length < 1 || value.message.length > 1500 ||
      !value.message.trim() || value.message.split('\n').length > 20 || /[\u0000-\u0008\u000b-\u001f\u007f]/.test(value.message)) return false;
  return true;
}

export function formatReport(report) {
  const icon = report.kind === 'user_report' ? '📝' : ICON[report.level];
  const text = report.kind === 'user_report' ? `>>> ${report.message}` : report.message;
  return `${icon} **${report.kind}** · os3-router ${report.version} · ${report.os} · install \`${report.installId}\`\n${text}`;
}

export function createIntake({ limit, send }) {
  return async function intake(request) {
    if (request.method !== 'POST') return reply(405, 'method_not_allowed');
    if (request.headers.get('content-type') !== 'application/json') return reply(415, 'unsupported_media_type');
    const ip = request.headers.get('x-vercel-forwarded-for') || request.headers.get('x-forwarded-for');
    if (!ip || !isIP(ip)) return reply(503, 'unavailable');
    const ipKey = createHash('sha256').update(ip).digest('hex');
    try {
      if (!await limit('ip', ipKey, request)) return reply(429, 'rate_limited');
    } catch { return reply(503, 'unavailable'); }
    const parsed = await readJson(request);
    if (parsed.error) return reply(parsed.error === 'too_large' ? 413 : 400, parsed.error);
    if (!validateReport(parsed.value)) return reply(400, 'invalid_payload');
    try {
      if (!await limit('global', 'all', request)) return reply(429, 'rate_limited');
      if (!await send(formatReport(parsed.value))) return reply(502, 'upstream_unavailable');
      return reply(202);
    } catch { return reply(503, 'unavailable'); }
  };
}
