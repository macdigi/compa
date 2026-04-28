# Releasing Compa

Two artifacts ship with each release:
- `install.sh` (lives at `setup/install.sh` — no version, always pulls
  whatever's at `main` when curl'd)
- `compa-os-X.Y.Z.img.xz` (built by GitHub Actions, uploaded to B2)

## First-time setup (one-off)

The image-build workflow uploads to the `compa-os-downloads` Backblaze
B2 bucket. You need to give GitHub Actions credentials:

1. **Backblaze B2 console** → App Keys → "Add a New Application Key"
   - Name: `compa-os-github-actions`
   - Allow access to: `compa-os-downloads`
   - Capabilities: **Read and Write Files**
   - Click Create New Key — copy the keyID + applicationKey **now**,
     they only appear once.

2. **GitHub repo** → Settings → Secrets and variables → Actions →
   New repository secret. Add two:
   - `B2_KEY_ID` — the keyID from step 1
   - `B2_APP_KEY` — the applicationKey from step 1

That's it. Secrets are scoped to this repo and only readable by
workflow runs.

### Optional: stable pretty download URL

If you want release notes to point at a Cloudflare-backed pretty URL
instead of the raw B2 hostname, deploy the Worker in
`cloudflare-worker/` and then set this repo variable:

```bash
gh variable set COMPA_OS_DOWNLOAD_BASE_URL \
  --repo macdigi/compa \
  --body 'https://downloads.example.com'
```

If the variable is unset, the workflow falls back to the raw B2 URL.

## Cutting a release

```bash
# 1. Tag the commit you want to release
git tag -a v1.0.0 -m "First public Compa OS image"
git push origin v1.0.0
```

That's it. The push triggers `.github/workflows/build-os-image.yml`,
which:
- Builds Pi OS Lite + Compa via `pi-gen` (~30 min on the native arm64 runner)
- Compresses the image with xz
- Uploads it to B2 as both `compa-os-1.0.0.img.xz` and
  `compa-os-latest.img.xz` (so the public download flow always
  resolves to the newest build)
- Creates a GitHub Release pointing at either the pretty download
  base URL (`COMPA_OS_DOWNLOAD_BASE_URL`) or the raw B2 URL when
  that variable is unset

## How users actually download

Public-facing downloads go through **[raredata.net/compa](https://raredata.net/compa#download)**:
producer drops their email → Resend fires a 24-hour B2 presigned URL →
Brevo records the contact in the "Compa OS downloads" list (id 15) for
release announcements. The unauthed B2 URL is *not* the user-facing
link — it's the build target only. Source for the email-gated flow
lives in [`raredata-net-site`](https://github.com/macdigi/raredata-net-site)
under `app/api/compa/download/route.ts` + `lib/b2Download.ts`.

If the public URL ever needs to be moved (off raredata.net, to a
Cloudflare worker, etc.), the only thing the compa repo needs to know
about is `README.md` line ~500 — that's the single user-facing link.

## Manual / dev builds

Use the workflow's "Run workflow" button on the Actions page if you
want to build off `main` without cutting a tag. Set the version input
to something like `dev-2026-04-28` so the artifact has a sensible
filename. No GitHub Release is created for manual builds — the
artifact still goes to B2 under that filename, and `compa-os-latest.img.xz`
gets overwritten with the dev build (so the email-gated download
will hand out the dev build until the next release run).

## Quick verification of an uploaded image (sysadmin path)

The `.sha256` sidecar stays unauthenticated on B2 — small, no PII —
so you can curl it directly without going through the email flow:

```bash
curl -sLO https://f004.backblazeb2.com/file/compa-os-downloads/compa-os-latest.img.xz.sha256
# Then download the image via raredata.net/compa, save next to the
# .sha256 file with the matching name, and:
sha256sum -c compa-os-latest.img.xz.sha256
```

Then flash to an SD card with Raspberry Pi Imager (Choose OS → Use
custom) and boot a Pi to confirm Compa launches on first boot.
