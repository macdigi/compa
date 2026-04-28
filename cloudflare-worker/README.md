# Compa OS Cloudflare download Worker

This Worker gives Compa OS a stable, pretty download URL layer in front of the public Backblaze B2 bucket.

## What it does

It redirects friendly URLs to the public files in `compa-os-downloads`:

- `/latest` → `compa-os-latest.img.xz`
- `/latest.sha256` → `compa-os-latest.img.xz.sha256`
- `/v/1.0.0` → `compa-os-1.0.0.img.xz`
- `/v/1.0.0/sha256` → `compa-os-1.0.0.img.xz.sha256`
- `/compa-os-latest.img.xz` → same as `/latest`
- `/compa-os-1.0.0.img.xz` → direct version redirect

Those filename-compatible routes let the GitHub release workflow switch from raw B2 links to pretty URLs by changing one repo variable instead of rewriting release body formatting.

## Local dev

```bash
cd cloudflare-worker
npm install
npm run check
npm run dev
```

## Deploy

1. Authenticate Wrangler:
   ```bash
   npx wrangler login
   ```
2. Deploy:
   ```bash
   npm install
   npm run deploy
   ```
3. Bind your preferred route in Cloudflare, for example:
   - `downloads.raredata.net/*`
   - `raredata.net/compa/downloads/*`
4. After the route is live, set the GitHub repo variable so release notes can use the pretty base URL:
   ```bash
   gh variable set COMPA_OS_DOWNLOAD_BASE_URL \
     --repo macdigi/compa \
     --body 'https://downloads.example.com'
   ```

If `COMPA_OS_DOWNLOAD_BASE_URL` is unset, the GitHub workflow falls back to the raw B2 URL.
