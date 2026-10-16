import { describe, expect, it, vi } from "vitest";

import { ApiError, getJson } from "@/lib/api/client";

const decodeHealth = (value: unknown): { status: "ok" } => {
  if (
    typeof value !== "object" ||
    value === null ||
    !("status" in value) ||
    value.status !== "ok"
  ) {
    throw new Error("Invalid health response");
  }
  return { status: "ok" };
};

describe("cliente API", () => {
  it("utiliza ruta relativa, credenciales del mismo origen y validación de respuesta", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(Response.json({ status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getJson("/health/live", decodeHealth)).resolves.toEqual({
      status: "ok",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/health/live",
      expect.objectContaining({
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
      }),
    );
  });

  it.each([
    "https://other.example",
    "//other.example",
    "/../outside",
    "/%2e%2e/outside",
    "/a\\b",
    "/health#fragment",
  ])("rechaza rutas fuera del contrato: %s", async (path) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(getJson(path, decodeHealth)).rejects.toBeInstanceOf(TypeError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("conserva el estado HTTP sin mostrar cuerpos de error", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response("sensitive internal detail", { status: 500 }),
        ),
    );
    await expect(getJson("/health/live", decodeHealth)).rejects.toMatchObject({
      kind: "http",
      status: 500,
      message: "No se pudo completar la solicitud.",
    });
  });

  it.each(["not json", JSON.stringify({ status: "unexpected" })])(
    "rechaza respuestas inválidas",
    async (body) => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body)));
      await expect(getJson("/health/live", decodeHealth)).rejects.toMatchObject(
        { kind: "invalid-response" },
      );
    },
  );

  it("traduce fallos de red sin filtrar el error original", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("sensitive connection detail")),
    );
    await expect(getJson("/health/live", decodeHealth)).rejects.toEqual(
      new ApiError("network", "No se pudo conectar con el servidor."),
    );
  });

  it("preserva una cancelación solicitada por el llamador", async () => {
    const controller = new AbortController();
    const error = new DOMException("Aborted", "AbortError");
    controller.abort();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(error));
    await expect(
      getJson("/health/live", decodeHealth, { signal: controller.signal }),
    ).rejects.toBe(error);
  });
});
