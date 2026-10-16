import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "Taller Carrillo",
  description: "El espacio de gestión de Taller Carrillo.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="es">
      <body>
        <a className="skip-link" href="#contenido">
          Ir al contenido
        </a>
        <header className="site-header">
          <Link className="brand" href="/" aria-label="Taller Carrillo, inicio">
            <span className="brand-mark" aria-hidden="true">
              TC
            </span>
            <span>
              <strong>Taller Carrillo</strong>
              <span className="brand-caption">Cuidado en cada detalle</span>
            </span>
          </Link>
          <span className="header-label">Gestión del taller</span>
        </header>
        {children}
        <footer className="site-footer">
          <span>Taller Carrillo</span>
          <span>Un espacio para trabajar mejor.</span>
        </footer>
      </body>
    </html>
  );
}
