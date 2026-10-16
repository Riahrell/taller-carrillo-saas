import AccessPanel from "@/components/access-panel";

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
          Accede a tu espacio de trabajo, con la atención que cada tarea merece.
        </p>
        <p className="welcome-detail">
          TALLER CARRILLO <span aria-hidden="true">/</span> GESTIÓN DEL TALLER
        </p>
      </div>
      <AccessPanel />
    </main>
  );
}
