import LiveStatus from "../components/live-status";
import RepositorySecurity from "../components/repository-security";
import {
  grafanaBase,
  grafanaDashboards,
  repository,
  resources,
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
          <a href={grafanaBase}>Grafana</a>
        </nav>
      </header>

      <main id="main">
        <LiveStatus />

        <RepositorySecurity />

        <section className="panel" aria-labelledby="dashboards-title">
          <div className="panel-heading">
            <h2 id="dashboards-title">Grafana dashboards</h2>
          </div>
          <div className="resource-list">
            {grafanaDashboards.map((dashboard) => (
              <a
                className="resource-link"
                href={dashboard.href}
                key={dashboard.name}
              >
                <span>
                  <strong>{dashboard.name}</strong>
                  <small>{dashboard.description}</small>
                </span>
                <span aria-hidden="true">↗</span>
              </a>
            ))}
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
