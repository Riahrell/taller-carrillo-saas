export const dynamic = "force-dynamic";
import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "Gestión de inventario",
  description: "Herramienta de gestión de inventario para tu empresa.",
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
          <Link
            className="brand"
            href="/"
            aria-label="Gestión de inventario, inicio"
          >
            <span className="brand-mark" aria-hidden="true">
              GI
            </span>
            <span>
              <strong>Gestión de inventario</strong>
              <span className="brand-caption">Tu empresa, bien organizada</span>
            </span>
          </Link>
          <span className="header-label">Productos y existencias</span>
        </header>
        {children}
        <footer className="site-footer">
          <span>Gestión de inventario</span>
          <span>Desarrollado por Riahrell</span>
        </footer>
      </body>
    </html>
  );
}
