export default function HomePage() {
  return (
    <main id="contenido" className="welcome" tabIndex={-1}>
      <div className="welcome-copy">
        <p className="eyebrow">BIENVENIDO A TALLER CARRILLO</p>
        <h1>
          Cada trabajo,
          <br />
          <span>bien organizado.</span>
        </h1>
        <p className="intro">
          Estamos preparando un espacio para acompañarte en el día a día del
          taller, con la atención que cada trabajo merece.
        </p>
        <div className="access-note" aria-labelledby="access-title">
          <span className="status-dot" aria-hidden="true" />
          <div>
            <h2 id="access-title">Acceso aún no disponible</h2>
            <p>El inicio de sesión se habilitará próximamente.</p>
          </div>
        </div>
      </div>
      <div className="workshop-art" aria-hidden="true">
        <div className="art-grid" />
        <span className="art-label">TALLER / CARRILLO</span>
        <svg viewBox="0 0 400 400" className="workshop-symbol">
          <path
            d="M252 70a80 80 0 0 0-98 101L75 250a40 40 0 0 0 57 57l79-79a80 80 0 0 0 101-98l-50 50-42-12-12-42Z"
            fill="none"
            stroke="currentColor"
            strokeWidth="15"
            strokeLinejoin="round"
          />
          <circle cx="102" cy="279" r="10" fill="currentColor" />
        </svg>
        <span className="art-caption">Orden para lo que viene.</span>
      </div>
    </main>
  );
}
