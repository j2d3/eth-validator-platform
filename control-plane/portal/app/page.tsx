import {
  architectureSteps,
  clientMatrix,
  securitySignals,
  surfaces,
  type PortalSurface,
} from "../lib/portal-registry";

const truthLayers = [
  {
    code: "01",
    label: "Declared",
    value: "GitOps intent is versioned",
    detail:
      "Schemas, Terraform, Helm, Flux resources, dashboards, and lifecycle records on main.",
    source: "source · Git · reviewed 2026-08-02",
  },
  {
    code: "02",
    label: "Reconciled",
    value: "No current cluster observation",
    detail:
      "Prior local service evidence exists, but a later migration failure blocked apps. This portal does not promote historical evidence to live health.",
    source: "source · operator handoff · 2026-08-02",
  },
  {
    code: "03",
    label: "Observed",
    value: "EKS foundation warm-paused",
    detail:
      "Two on-demand system nodes; three zonal Ethereum Spot groups declared at zero.",
    source: "source · operator evidence · 2026-08-02",
  },
];

const posture = [
  {
    value: "0",
    label: "keys admitted",
    note: "signing disabled",
    source: "Web3Signer key bundle · reviewed 2026-08-02",
  },
  {
    value: "0",
    label: "active pairs",
    note: "safe stopped state",
    source: "assignment catalog · reviewed 2026-08-02",
  },
  {
    value: "1",
    label: "pair adapter",
    note: "Geth + Lighthouse",
    source: "Helm values · reviewed 2026-08-02",
  },
  {
    value: "3",
    label: "Spot zones",
    note: "min 0 · max 1",
    source: "Terraform + EKS · observed 2026-08-02",
  },
];

const phases = [
  { name: "GitOps foundation", state: "complete" },
  { name: "Platform services", state: "in progress" },
  { name: "First safe lifecycle", state: "started" },
  { name: "EKS parity", state: "in progress" },
];

function SurfaceCard({ surface }: { surface: PortalSurface }) {
  const body = (
    <>
      <div className="surface-card__topline">
        <span className="surface-card__category">{surface.category}</span>
        <span className={`access access--${surface.access}`}>
          {surface.access}
        </span>
      </div>
      <div>
        <h3>{surface.name}</h3>
        <p>{surface.description}</p>
      </div>
      <div className="surface-card__footer">
        <span>{surface.detail}</span>
        <span className={`surface-state surface-state--${surface.state}`}>
          {surface.state === "available" ? "Open ↗" : surface.state}
        </span>
      </div>
    </>
  );

  if (!surface.href) {
    return (
      <article className="surface-card surface-card--muted">
        {body}
      </article>
    );
  }

  return (
    <a
      className="surface-card"
      href={surface.href}
      target="_blank"
      rel="noreferrer"
      aria-label={`${surface.name} (opens in a new tab)`}
    >
      {body}
    </a>
  );
}

export default function Home() {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>

      <header className="site-header">
        <a className="brand" href="#top" aria-label="Validator Platform home">
          <span className="brand__mark" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span>
            <strong>Validator Platform</strong>
            <small>field console / lab</small>
          </span>
        </a>

        <nav aria-label="Primary navigation">
          <a href="#posture">Posture</a>
          <a href="#security">Security</a>
          <a href="#architecture">Architecture</a>
          <a href="#surfaces">Explore</a>
          <a href="#roadmap">Roadmap</a>
        </nav>

        <div
          className="header-state"
          aria-label="Evidence mode: the portal is using static repository evidence"
        >
          <span className="pulse" />
          evidence mode
        </div>
      </header>

      <main id="main">
        <section className="hero" id="top">
          <div className="hero__grid" aria-hidden="true" />
          <div className="hero__orb hero__orb--one" aria-hidden="true" />
          <div className="hero__orb hero__orb--two" aria-hidden="true" />

          <div className="hero__content">
            <div className="eyebrow">
              <span>Ethereum validator operations</span>
              <span>Hoodi / us-west-2</span>
            </div>
            <h1>
              One view of the platform.
              <span>Committed intent, cluster state, and observed evidence — kept separate.</span>
            </h1>
            <p className="hero__lede">
              A GitOps-operated laboratory for running heterogeneous Ethereum
              validator clients on EKS with remote signing, durable slashing
              protection, and explicit lifecycle control.
            </p>

            <div className="hero__actions">
              <a className="button button--primary" href="#surfaces">
                Explore the platform
                <span aria-hidden="true">↓</span>
              </a>
              <a
                className="button button--quiet"
                href="https://github.com/j2d3/eth-validator-platform/blob/main/docs/prd/001-dynamic-validator-platform.md"
                target="_blank"
                rel="noreferrer"
              >
                Read the product contract ↗
              </a>
            </div>
          </div>

          <aside className="hero__safety" aria-label="Current safety posture">
            <div className="safety-dial" role="img" aria-label="Signing is off">
              <div className="safety-dial__inner">
                <span>signing</span>
                <strong>OFF</strong>
              </div>
            </div>
            <div>
              <span className="kicker">Fail-closed posture</span>
              <h2>Ready is not authorization.</h2>
              <p>
                The signer&apos;s key store is empty. A healthy pod, synced node, or
                reconciled release does not by itself enable validator duties.
              </p>
            </div>
          </aside>

          <div className="hero__rail" aria-label="Project context">
            <span><b>phase</b> 2 + 4 in progress</span>
            <span><b>target</b> Amazon EKS</span>
            <span><b>reconciler</b> Flux</span>
            <span><b>signer</b> Web3Signer</span>
            <span><b>data</b> PostgreSQL</span>
          </div>
        </section>

        <section className="section posture" id="posture">
          <div className="section-heading">
            <div>
              <span className="kicker">Fleet posture</span>
              <h2>What the fleet is doing right now.</h2>
            </div>
            <p>
              This first portal slice is built from committed and recorded
              evidence. It is not yet a live control plane.
            </p>
          </div>

          <div className="posture-grid">
            {posture.map((item) => (
              <article className="metric-card" key={item.label}>
                <strong>{item.value}</strong>
                <span>{item.label}</span>
                <small>{item.note}</small>
                <small className="metric-source">source · {item.source}</small>
              </article>
            ))}
          </div>

          <div className="truth-model">
            <div className="truth-model__intro">
              <span className="kicker">The truth model</span>
              <h3>Three separate answers, not one blended status.</h3>
              <p>
                “Requested active,” “Flux deployed,” and “beacon active” are
                different facts owned by different systems, and the portal
                shows them separately.
              </p>
            </div>
            <div className="truth-model__layers">
              {truthLayers.map((layer) => (
                <article key={layer.code}>
                  <div className="truth-model__number">{layer.code}</div>
                  <span className="kicker">{layer.label}</span>
                  <h4>{layer.value}</h4>
                  <p>{layer.detail}</p>
                  <small>{layer.source}</small>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="section security" id="security">
          <div className="section-heading">
            <div>
              <span className="kicker">Dependency &amp; image security</span>
              <h2>Which supply-chain controls are enabled.</h2>
            </div>
            <p>
              Enablement and findings are different facts. This static slice records
              which controls are active and links authorized operators to GitHub for
              the live finding set; it does not freeze an alert count into the page.
            </p>
          </div>

          <div className="security-grid">
            {securitySignals.map((signal) => {
              const body = (
                <>
                  <div className="security-card__topline">
                    <span className={`security-state security-state--${signal.state}`}>
                      <i aria-hidden="true" />
                      {signal.state}
                    </span>
                    <span>{signal.linkLabel ?? "evidence only"}</span>
                  </div>
                  <h3>{signal.name}</h3>
                  <p>{signal.description}</p>
                  <small>{signal.evidence}</small>
                </>
              );

              return signal.href ? (
                <a
                  className="security-card"
                  href={signal.href}
                  target="_blank"
                  rel="noreferrer"
                  key={signal.name}
                  aria-label={`${signal.name} in GitHub (opens in a new tab)`}
                >
                  {body}
                </a>
              ) : (
                <article className="security-card security-card--planned" key={signal.name}>
                  {body}
                </article>
              );
            })}
          </div>

          <p className="security-note">
            Current evidence proves repository-setting enablement, not zero risk.
            A live count will arrive with the authenticated GitHub read adapter.
          </p>
        </section>

        <section className="section architecture" id="architecture">
          <div className="section-heading section-heading--light">
            <div>
              <span className="kicker">Operating architecture</span>
              <h2>Three writers, three bounded scopes.</h2>
            </div>
            <p>
              Terraform owns the AWS foundation. Git records application intent.
              Flux writes the cluster. Web3Signer is the only signing path.
            </p>
          </div>

          <div
            className="architecture-flow"
            role="region"
            tabIndex={0}
            aria-label="Platform request flow; scroll horizontally for every step"
          >
            {architectureSteps.map((step, index) => (
              <div className="architecture-flow__item" key={step.name}>
                <article className={`node node--${step.state}`}>
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <strong>{step.name}</strong>
                  <small>{step.detail}</small>
                </article>
                {index < architectureSteps.length - 1 && (
                  <div className="connector" aria-hidden="true">
                    <i />
                    <b>→</b>
                  </div>
                )}
              </div>
            ))}
          </div>

          <div className="architecture-notes">
            <article>
              <span>01 / cloud</span>
              <h3>Terraform, applied infrequently.</h3>
              <p>VPC, EKS, capacity, IAM, RDS, KMS, EBS, and secret containers.</p>
            </article>
            <article>
              <span>02 / applications</span>
              <h3>Flux, reconciling continuously.</h3>
              <p>Controllers, policies, signer, observability, releases, and lifecycle.</p>
            </article>
            <article>
              <span>03 / safety</span>
              <h3>Signing, gated last.</h3>
              <p>Exclusive assignment, sync, key admission, slashing DB, and doppelganger checks.</p>
            </article>
          </div>
        </section>

        <section className="section matrix-section">
          <div className="matrix-copy">
            <span className="kicker">Client diversity</span>
            <h2>Every EL/CL combination is defined; one runs at a time.</h2>
            <p>
              Sixteen execution/consensus combinations are represented in the
              catalog. One pair runs at a time in the lab, exercises its
              complete lifecycle, records the gotchas, then yields capacity to
              the next adapter.
            </p>
            <div className="legend">
              <span><i className="legend__implemented" /> implemented</span>
              <span><i className="legend__planned" /> planned adapter</span>
            </div>
          </div>
          <div className="client-matrix">
            <div className="client-matrix__axis">execution × consensus</div>
            {clientMatrix.map((pair, index) => (
              <article className={`pair pair--${pair.state}`} key={`${pair.execution}-${pair.consensus}`}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <div>
                  <strong>{pair.execution}</strong>
                  <small>+ {pair.consensus}</small>
                </div>
                <b>{pair.state === "implemented" ? "qualified offline" : "queued"}</b>
              </article>
            ))}
            <article className="pair pair--more">
              <strong>+9</strong>
              <span>remaining combinations</span>
            </article>
          </div>
        </section>

        <section className="section surfaces" id="surfaces">
          <div className="section-heading">
            <div>
              <span className="kicker">Explore every surface</span>
              <h2>Entry points into the existing tools.</h2>
            </div>
            <p>
              Start here, then drill into the tool that owns the detail. Private
              endpoints stay unlinked until an authenticated environment supplies them.
            </p>
          </div>

          <div className="surface-summary" aria-label="Access legend">
            <span><i className="dot dot--public" /> public-safe</span>
            <span><i className="dot dot--operator" /> operator boundary</span>
            <span><i className="dot dot--local" /> local port-forward</span>
            <span className="surface-summary__note">static registry · live adapters later</span>
          </div>

          <div className="surface-grid">
            {surfaces.map((surface) => (
              <SurfaceCard key={surface.name} surface={surface} />
            ))}
          </div>
        </section>

        <section className="section roadmap" id="roadmap">
          <div className="roadmap__copy">
            <span className="kicker">Portal evolution</span>
            <h2>Explain, then read, then authenticate.</h2>
            <p>
              First it explains. Then it reads live state. Later it authenticates.
              Only after those boundaries are proven does it author typed,
              reviewable Git changes.
            </p>
            <a
              href="https://github.com/j2d3/eth-validator-platform/issues/40"
              target="_blank"
              rel="noreferrer"
            >
              Follow portal delivery in issue #40 ↗
            </a>
          </div>

          <div className="roadmap__phases">
            {phases.map((phase, index) => (
              <article key={phase.name}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <div>
                  <div className="phase-title">
                    <strong>{phase.name}</strong>
                    <small>{phase.state}</small>
                  </div>
                  <small className="phase-source">
                    source · README implementation status · reviewed 2026-08-02
                  </small>
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="closing">
          <span className="kicker">Current boundary</span>
          <p>
            A synced node is not automatically a validator.
            <br />
            A running signer with no admitted key cannot sign.
          </p>
          <div>
            <span>read-only portal</span>
            <span>authentication not exposed</span>
            <span>no funded key</span>
          </div>
        </section>
      </main>

      <footer>
        <div className="brand brand--footer">
          <span className="brand__mark" aria-hidden="true"><i /><i /><i /></span>
          <span><strong>Validator Platform</strong><small>reference lab</small></span>
        </div>
        <p>Ethereum validator infrastructure lab · not a production staking service</p>
        <a href="#top">Back to top ↑</a>
      </footer>
    </>
  );
}
