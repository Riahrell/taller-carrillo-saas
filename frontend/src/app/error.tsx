"use client";

export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <main id="contenido" className="message-page" tabIndex={-1}>
      <p className="eyebrow">ALGO SALIÓ MAL</p>
      <h1>No pudimos cargar esta página.</h1>
      <p role="alert">Inténtalo de nuevo en unos momentos.</p>
      <button className="action" type="button" onClick={reset}>
        Volver a intentar
      </button>
    </main>
  );
}
