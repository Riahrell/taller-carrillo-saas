import Link from "next/link";

export default function NotFoundPage() {
  return (
    <main id="contenido" className="message-page" tabIndex={-1}>
      <p className="eyebrow">PÁGINA NO ENCONTRADA</p>
      <h1>No encontramos esta página.</h1>
      <p>Comprueba la dirección o vuelve al inicio.</p>
      <Link className="action" href="/">
        Volver al inicio
      </Link>
    </main>
  );
}
