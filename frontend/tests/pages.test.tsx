import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ErrorPage from "@/app/error";
import NotFoundPage from "@/app/not-found";
import HomePage from "@/app/page";

describe("pantallas iniciales", () => {
  it("explica que el acceso aún no está habilitado sin simular operaciones", () => {
    render(<HomePage />);
    expect(screen.getByRole("main")).toHaveAttribute("id", "contenido");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Cada trabajo,",
    );
    expect(
      screen.getByRole("heading", { name: "Acceso aún no disponible" }),
    ).toBeVisible();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("permite regresar al inicio desde una página desconocida", () => {
    render(<NotFoundPage />);
    expect(
      screen.getByRole("link", { name: "Volver al inicio" }),
    ).toHaveAttribute("href", "/");
  });

  it("ofrece un reintento accesible sin mostrar detalles internos del error", () => {
    const reset = vi.fn();
    render(<ErrorPage reset={reset} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Inténtalo de nuevo");
    fireEvent.click(screen.getByRole("button", { name: "Volver a intentar" }));
    expect(reset).toHaveBeenCalledOnce();
  });
});
