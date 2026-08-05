# Validator Platform Portal

Static status page and project index for the EKS development environment.

The page contains:

- live read-only EKS, Ethereum pair, signing, and alert status from the portal
  telemetry adapter;
- public GitHub workflow, image-finding, and dependency-update status;
- links to tracked source, specifications, runbooks, dashboard definitions, and
  recorded evidence.

The public page has no AWS, Kubernetes, Prometheus, Grafana, GitHub, or secret
credentials and cannot change platform state. Its same-origin status route is
served by the separately deployed read-only telemetry adapter. Repository
security cards use unauthenticated public GitHub API responses and render
unavailable when the exact source/run binding cannot be verified.

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
