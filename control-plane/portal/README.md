# Validator Platform Portal

The project home and top-level operating index for the Ethereum Validator
Platform Lab.

This application explains the system, presents its safety posture, distinguishes
declared/reconciled/observed state, and links operators into specialist tools.
It does not replace Grafana, Flux, GitHub, AWS, or the chain explorers.

## Current boundary

The first slice is a static, public-safe visual shell built from repository and
recorded evidence. It has no Kubernetes, AWS, Prometheus, GitHub, or secret
credentials and cannot mutate platform state. Private specialist endpoints are
deliberately absent until an authenticated environment supplies them.

Mock or recorded values must be labeled as such. A future read model will attach
source and freshness metadata to every operational value.

## Local development

Requires Node.js `>=22.13.0`.

```bash
npm ci
npm run dev
npm test
npm audit
```

`npm test` builds the Cloudflare Worker-compatible application and verifies its
server-rendered safety, evidence-labeling, and private-endpoint contracts. CI
also audits the complete runtime and development dependency graph; build tools
are not excluded merely because they are absent from the production bundle.

## Hosting and transport boundary

The Worker enforces a single canonical origin. Requests that arrive on any
other hostname — or over plaintext HTTP — receive a permanent redirect to
the canonical origin. Successful canonical responses carry HSTS. Open
Graph, Twitter, and canonical metadata are baked in at build time and cannot
be rewritten through request host headers.

The reference deployment's canonical origin is `https://g.j2d3.com`. A fork
sets its own canonical origin at build time:

```bash
PORTAL_CANONICAL_ORIGIN="https://portal.example.org" npm run build
PORTAL_CANONICAL_ORIGIN="https://portal.example.org" npm test
```

The value must be a bare HTTPS origin: `https:` scheme, a non-empty
hostname, **no explicit port** (including `:443` — WHATWG URL parsing
strips default ports so the validator inspects the raw string before
parsing), no username or password, no path other than `/`, no query,
no fragment.

Validation lives in
[`lib/canonical-origin-validator.mjs`](lib/canonical-origin-validator.mjs)
(pure JS so Node's test runner can exercise it directly), and is
covered by [`tests/canonical-origin.test.mjs`](tests/canonical-origin.test.mjs).
Vite's config-load runs the same validator, so an invalid
`PORTAL_CANONICAL_ORIGIN` throws before any build work happens — a
deployable artifact is never produced from a misconfigured environment.

Vite `define` injects the validated origin as a compile-time string
literal into every bundle (Worker, RSC, SSR), so the compiled
artifacts carry the build-time value directly. Changing
`PORTAL_CANONICAL_ORIGIN` at runtime has no effect on a built Worker.
The build-versus-runtime separation is exercised by a dedicated test
(`build-time origin is baked into the compiled Worker`) that mutates
`process.env` post-build and confirms the Worker's redirect still
resolves to the build-time literal. A pure runtime-binding design
(where the Worker reads its canonical origin from a Cloudflare
binding at request time) is architecturally viable but is not the
mechanism this repository qualifies.

The single source of truth is
[`lib/canonical-origin.ts`](lib/canonical-origin.ts); both the Worker
and the Next.js layout import from it. If `PORTAL_CANONICAL_ORIGIN`
is unset, the build falls back to the reference deployment origin so
unmodified `npm run build` and `npm test` still work.

The hosting provider manages the certificate. The deployment is not considered
live until the provider reports both the custom domain and SSL certificate
active and an external probe verifies the certificate hostname, HTTP-to-HTTPS
redirect, and HSTS response. Route 53 routing and ownership-validation records
belong in a dedicated Terraform state so they survive pausing or replacing the
EKS lab. Initial deployment remains owner-only; broadening access is a separate
reviewed decision.

## Delivery phases

1. Static project home and specialist-surface registry.
2. Digest-pinned, Flux-managed read-only portal in Kubernetes.
3. Least-privilege live read model for Git, Flux, Prometheus, beacon, GitHub, and AWS state.
4. OIDC-authenticated public exposure with public/operator content separation.
5. Profile-constrained commands that open reviewed GitHub pull requests.

The portal never becomes a second Kubernetes or AWS writer. Git merge remains
ordinary deployment authorization and Flux remains the application reconciler.

See [issue #40](https://github.com/j2d3/eth-validator-platform/issues/40) for
the product contract and acceptance criteria.
