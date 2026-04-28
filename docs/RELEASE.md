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

## Cutting a release

```bash
# 1. Tag the commit you want to release
git tag -a v1.0.0 -m "First public Compa OS image"
git push origin v1.0.0
```

That's it. The push triggers `.github/workflows/build-os-image.yml`,
which:
- Builds Pi OS Lite + Compa via `pi-gen` (~30-45 min)
- Compresses the image with xz
- Uploads it to B2 as both `compa-os-1.0.0.img.xz` and
  `compa-os-latest.img.xz` (so the README "latest" link always works)
- Creates a GitHub Release pointing at the B2 download

## Manual / dev builds

Use the workflow's "Run workflow" button on the Actions page if you
want to build off `main` without cutting a tag. Set the version input
to something like `dev-2026-04-28` so the artifact has a sensible
filename. No GitHub Release is created for manual builds — the
artifact still goes to B2 under that filename.

## Quick verification of an uploaded image

```bash
# Download
curl -sLO https://f004.backblazeb2.com/file/compa-os-downloads/compa-os-latest.img.xz
curl -sLO https://f004.backblazeb2.com/file/compa-os-downloads/compa-os-latest.img.xz.sha256

# Verify
sha256sum -c compa-os-latest.img.xz.sha256
```

Then flash to an SD card with Raspberry Pi Imager (Choose OS → Use
custom) and boot a Pi to confirm Compa launches on first boot.
