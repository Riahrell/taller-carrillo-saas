import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ErrorPage from "@/app/error";
import NotFoundPage from "@/app/not-found";
import HomePage from "@/app/page";
import { accessApi } from "@/lib/access/api";
import { ApiError } from "@/lib/api/client";

describe("pantallas iniciales", () => {
  it("muestra el acceso sin simular módulos de gestión", async () => {
    vi.spyOn(accessApi, "session").mockRejectedValue(
      new ApiError("http", "", 401),
    );
    vi.spyOn(accessApi, "prepare").mockResolvedValue(undefined);
    render(<HomePage />);
    expect(screen.getByRole("main")).toHaveAttribute("id", "contenido");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Cada trabajo,",
    );
    expect(await screen.findByLabelText("Correo electrónico")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Iniciar sesión" }),
    ).toBeEnabled();
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
