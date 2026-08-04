# Validator Platform Portal

Static status page and project index for the EKS development environment.

The page contains:

- a timestamped environment snapshot;
- component status from the latest operator check;
- links to tracked source, specifications, runbooks, dashboard definitions, and
  recorded evidence.

The page has no AWS, Kubernetes, Prometheus, Grafana, GitHub, or secret
credentials. It does not change platform state. Values from the cluster are
stored as a timestamped static snapshot until a read-only data adapter is
implemented.

## Local development

Requires Node.js `>=22.13.0`.

```bash
npm ci
npm run dev
npm test
npm audit
```

`npm test` builds the Cloudflare Worker-compatible application and checks the
rendered page, links, tracked repository paths, canonical origin, redirects,
and private-endpoint exclusions.

## Canonical origin

The Worker redirects every non-canonical request to one HTTPS origin and adds
HSTS to successful responses. The reference deployment uses
`https://g.j2d3.com`.

A fork sets its origin at build time:

```bash
PORTAL_CANONICAL_ORIGIN="https://portal.example.org" npm run build
PORTAL_CANONICAL_ORIGIN="https://portal.example.org" npm test
```

The value must be a bare HTTPS origin with no credentials, explicit port, path,
query, or fragment. Validation lives in
[`lib/canonical-origin-validator.mjs`](lib/canonical-origin-validator.mjs).
The Worker and metadata both import the validated value from
[`lib/canonical-origin.ts`](lib/canonical-origin.ts).
