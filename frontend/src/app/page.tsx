import AccessPanel from "@/components/access-panel";

export default function HomePage() {
  return (
    <main id="contenido" className="welcome" tabIndex={-1}>
      <div className="welcome-copy">
        <p className="eyebrow">TU HERRAMIENTA DE GESTIÓN</p>
        <h1>
          Tu inventario,
          <br />
          <span>bien organizado.</span>
        </h1>
        <p className="intro">
          Gestiona tus productos, existencias y equipo desde un mismo lugar.
        </p>
        <p className="welcome-detail">
          PRODUCTOS <span aria-hidden="true">/</span> EXISTENCIAS Y EQUIPO
        </p>
      </div>
      <AccessPanel />
    </main>
  );
}
