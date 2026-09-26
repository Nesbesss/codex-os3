import { checkRateLimit } from '@vercel/firewall';
import { createIntake, validWebhookUrl } from '../lib/intake.js';

const webhook = process.env.DISCORD_REPORTS_WEBHOOK;
let intake;

if (validWebhookUrl(webhook)) {
  intake = createIntake({
    limit: async (scope, key, request) => {
      const result = await checkRateLimit('os3-report-intake', { request, rateLimitKey: `${scope}:${key}` });
      if (result.error) throw new Error('firewall unavailable');
      return !result.rateLimited;
    },
    send: async content => {
      const response = await fetch(webhook, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: 'os3-router reports', content, allowed_mentions: { parse: [] } }),
        signal: AbortSignal.timeout(5000),
      });
      return response.ok;
    },
  });
}

export async function POST(request) {
  if (!intake) return Response.json({ error: 'unavailable' }, { status: 503, headers: { 'Cache-Control': 'no-store' } });
  return intake(request);
}
