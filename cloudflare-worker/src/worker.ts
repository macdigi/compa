export interface Env {
  RAW_DOWNLOAD_BASE: string;
}

const VERSION_TOKEN = '[0-9A-Za-z][0-9A-Za-z._-]*';
const VERSION_FILE_RE = new RegExp(`^/compa-os-(${VERSION_TOKEN})\\.img\\.xz$`);
const VERSION_CHECKSUM_RE = new RegExp(`^/compa-os-(${VERSION_TOKEN})\\.img\\.xz\\.sha256$`);
const VERSION_ALIAS_RE = new RegExp(`^/v/(${VERSION_TOKEN})$`);
const VERSION_ALIAS_CHECKSUM_RE = new RegExp(`^/v/(${VERSION_TOKEN})/sha256$`);

function normalizeBase(base: string): string {
  return base.replace(/\/+$/, '');
}

function redirect(location: string): Response {
  return new Response(null, {
    status: 302,
    headers: {
      Location: location,
      'Cache-Control': 'no-store',
    },
  });
}

function joinTarget(base: string, filename: string): string {
  return `${normalizeBase(base)}/${filename}`;
}

function imageFilename(version: string): string {
  return `compa-os-${version}.img.xz`;
}

function checksumFilename(version: string): string {
  return `${imageFilename(version)}.sha256`;
}

function json(data: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(data, null, 2), {
    ...init,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      ...(init?.headers ?? {}),
    },
  });
}

function resolveTarget(pathname: string): string | null {
  if (pathname === '/' || pathname === '/latest') {
    return imageFilename('latest');
  }
  if (pathname === '/latest.sha256') {
    return checksumFilename('latest');
  }

  const versionFileMatch = pathname.match(VERSION_FILE_RE);
  if (versionFileMatch) {
    return imageFilename(versionFileMatch[1]);
  }

  const versionChecksumMatch = pathname.match(VERSION_CHECKSUM_RE);
  if (versionChecksumMatch) {
    return checksumFilename(versionChecksumMatch[1]);
  }

  const versionAliasMatch = pathname.match(VERSION_ALIAS_RE);
  if (versionAliasMatch) {
    return imageFilename(versionAliasMatch[1]);
  }

  const versionAliasChecksumMatch = pathname.match(VERSION_ALIAS_CHECKSUM_RE);
  if (versionAliasChecksumMatch) {
    return checksumFilename(versionAliasChecksumMatch[1]);
  }

  return null;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return new Response('Method Not Allowed', {
        status: 405,
        headers: { Allow: 'GET, HEAD' },
      });
    }

    const url = new URL(request.url);
    const pathname = url.pathname.replace(/\/+$/, '') || '/';
    const base = normalizeBase(env.RAW_DOWNLOAD_BASE);

    if (pathname === '/healthz') {
      return json({
        ok: true,
        rawDownloadBase: base,
        endpoints: {
          latest: `${url.origin}/latest`,
          latestChecksum: `${url.origin}/latest.sha256`,
          filenameLatest: `${url.origin}/compa-os-latest.img.xz`,
          filenameLatestChecksum: `${url.origin}/compa-os-latest.img.xz.sha256`,
          versionExample: `${url.origin}/v/1.0.0`,
          versionFilenameExample: `${url.origin}/compa-os-1.0.0.img.xz`,
        },
      });
    }

    const target = resolveTarget(pathname);
    if (!target) {
      return json(
        {
          error: 'not_found',
          message: 'Use /latest, /latest.sha256, /v/:version, /v/:version/sha256, or direct compa-os-*.img.xz paths.',
        },
        { status: 404 },
      );
    }

    return redirect(joinTarget(base, target));
  },
};
