import {
  componentStatuses,
  observedAt,
  observedRevision,
  repository,
  resources,
  summary,
} from "../lib/portal-registry";

export default function Home() {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>

      <header className="site-header">
        <a className="brand" href="#main">
          Ethereum Validator Platform
        </a>
        <nav aria-label="Project links">
          <a href={repository}>Repository</a>
          <a
            href={`${repository}/blob/main/docs/prd/001-dynamic-validator-platform.md`}
          >
            Specification
          </a>
          <a href={`${repository}/tree/main/docs/runbooks`}>Runbooks</a>
        </nav>
      </header>

      <main id="main">
        <section className="environment-heading" aria-labelledby="page-title">
          <div>
            <p className="context">Development environment · AWS us-west-2</p>
            <h1 id="page-title">Environment status</h1>
            <p className="observation">
              Observed {observedAt} · revision{" "}
              <a href={`${repository}/commit/${observedRevision}`}>
                {observedRevision.slice(0, 7)}
              </a>
            </p>
          </div>
          <div className="signing-state" aria-label="Signing disabled">
            <span>Signing</span>
            <strong>Disabled</strong>
          </div>
        </section>

        <section className="summary-grid" aria-label="Environment summary">
          {summary.map((item) => (
            <article className="summary-item" key={item.label}>
              <span className="summary-item__label">{item.label}</span>
              <strong>{item.value}</strong>
              <span className={`detail detail--${item.tone}`}>
                {item.detail}
              </span>
            </article>
          ))}
        </section>

        <section className="panel" aria-labelledby="components-title">
          <div className="panel-heading">
            <h2 id="components-title">Components</h2>
            <span>Last observed {observedAt}</span>
          </div>
          <div className="status-table-wrap">
            <table className="status-table">
              <thead>
                <tr>
                  <th scope="col">Component</th>
                  <th scope="col">Status</th>
                  <th scope="col">Details</th>
                  <th scope="col">Source</th>
                </tr>
              </thead>
              <tbody>
                {componentStatuses.map((item) => (
                  <tr key={item.component}>
                    <th scope="row">{item.component}</th>
                    <td>
                      <span className={`status status--${item.tone}`}>
                        <i aria-hidden="true" />
                        {item.status}
                      </span>
                    </td>
                    <td>{item.detail}</td>
                    <td>
                      <a href={item.href}>{item.linkLabel}</a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel" aria-labelledby="resources-title">
          <div className="panel-heading">
            <h2 id="resources-title">Project links</h2>
          </div>
          <div className="resource-list">
            {resources.map((resource) => (
              <a className="resource-link" href={resource.href} key={resource.name}>
                <span>
                  <strong>{resource.name}</strong>
                  <small>{resource.description}</small>
                </span>
                <span aria-hidden="true">↗</span>
              </a>
            ))}
          </div>
        </section>
      </main>

      <footer>
        <span>Ethereum Validator Platform</span>
        <a href={repository}>GitHub</a>
      </footer>
    </>
  );
}
