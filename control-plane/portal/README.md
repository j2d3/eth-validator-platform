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

`https://g.j2d3.com` is the portal's sole canonical origin. The Worker returns
a permanent redirect for plaintext HTTP and for every noncanonical hostname;
successful canonical responses carry HSTS. Open Graph, Twitter, and canonical
metadata are static and cannot be rewritten through request host headers.

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
