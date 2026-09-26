# OS3 report intake

This standalone Vercel Function accepts anonymous reports from os3-router installs and forwards accepted messages to the existing Discord reports webhook. The webhook exists only as the sensitive `DISCORD_REPORTS_WEBHOOK` Production environment variable on the Vercel project. The Discord-channel-to-GitHub importer remains on the maintainer Mac. No shared secret belongs in client code.

## Request contract

`POST /api/report` with `Content-Type: application/json` and at most 4096 body bytes:

```json
{"kind":"user_report","level":"info","version":"0.3.2","os":"Darwin 25.2.0","installId":"abcdef1234","message":"What went wrong"}
```

Only the six named fields are accepted. `kind` must be a known router event or `user_report`; `level` must be `error`, `warn`, or `info`. The version, OS and 10-character install ID have bounded formats; message is nonblank, up to 1500 characters and 20 lines. The function constructs the Discord header itself, disables mentions, and returns generic errors without exposing upstream details. It returns `202` only after Discord accepts the post.

## Abuse protection on Vercel Hobby

`waf-config.json` defines the one free Vercel WAF rate-limit rule for this project. The function calls the `os3-report-intake` rule twice: once with a hashed client IP before parsing the body, and once with a constant key after validation. Each bucket allows 12 requests per 10 minutes, using Vercel's fixed-window counters. Missing or faulty WAF configuration fails closed with `503`. This avoids an in-memory limiter and a paid Redis resource. Vercel's [Hobby limits](https://vercel.com/docs/vercel-firewall/vercel-waf/rate-limiting) include one rate-limit rule and 1,000,000 allowed rate-limit requests per month. Counters are per region; traffic spread across regions can exceed one region's limit. Distributed abuse can also consume the shared global bucket and delay legitimate reports. The router client still limits automatic reports locally and requires opt-in.

## Maintainer deployment

Keep this source on the Mac mini. Run `npm test` in this directory. Use the signed-in Vercel CLI on the MacBook Pro to link the dedicated `os3-router-report-intake` project and deploy a temporary copy of the `api`, `lib`, `package.json` and `package-lock.json` files. Apply and verify `waf-config.json` on that project before deployment. Set `DISCORD_REPORTS_WEBHOOK` through `vercel env add ... production --sensitive`; never put it in a file or the repository. Remove the temporary deployment copy after verification. No production os3-router release is changed by deploying this function.
