import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import InventoryWorkspace from "@/components/inventory-workspace";
import { accessApi } from "@/lib/access/api";
import { identityApi } from "@/lib/access/identity";
import { ApiError } from "@/lib/api/client";
import { catalogApi } from "@/lib/catalog/api";
import { inventoryApi } from "@/lib/inventory/api";
import { actor, context, product, empty, evidence } from "./inventory-data";
import { confirmIdentity } from "./confirm-identity";

beforeEach(() => {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
  vi.stubGlobal("BroadcastChannel", undefined);
  vi.spyOn(accessApi, "session").mockResolvedValue(context);
  vi.spyOn(accessApi, "reauthenticate").mockResolvedValue(undefined);
  vi.spyOn(identityApi, "status").mockResolvedValue({
    mfa_enabled: false,
    mailbox_verified: true,
    recent_seconds: 0,
  });
  vi.spyOn(catalogApi, "products").mockResolvedValue({
    items: [product],
    next_after: null,
  });
  vi.spyOn(catalogApi, "detail").mockResolvedValue(product);
  vi.spyOn(catalogApi, "categories").mockResolvedValue({
    items: [],
    next_after: null,
  });
  vi.spyOn(inventoryApi, "balance").mockResolvedValue(empty);
  vi.spyOn(inventoryApi, "counts").mockResolvedValue({
    items: [],
    next_after: null,
  });
  vi.spyOn(inventoryApi, "record").mockImplementation(async (_, pending) => ({
    ...evidence,
    operation_key: pending.key,
  }));
});
async function open() {
  render(<InventoryWorkspace />);
  await screen.findByRole("heading", { name: "Filtro de aceite" });
}
async function draft() {
  await open();
  fireEvent.click(
    screen.getByRole("button", { name: "Registrar conteo físico" }),
  );
  await confirmIdentity();
  await screen.findByLabelText("Cantidad contada");
  fireEvent.change(screen.getByLabelText("Cantidad contada"), {
    target: { value: "2" },
  });
  fireEvent.change(screen.getByLabelText("Motivo"), {
    target: { value: "Conteo inicial" },
  });
  fireEvent.change(screen.getByLabelText(/Costo unitario declarado/), {
    target: { value: "3" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Revisar conteo" }));
}
it("muestra datos privados solo tras comprobar sesión y separa costo del precio", async () => {
  await open();
  expect(screen.getByText("Sin costo calculable")).toBeInTheDocument();
  expect(screen.getByText("12.34 USD")).toBeInTheDocument();
  expect(
    screen.getByRole("img", { name: /pendiente de integrar/ }),
  ).toBeInTheDocument();
  expect(accessApi.reauthenticate).not.toHaveBeenCalled();
});
it("no consulta datos con un perfil no administrador", async () => {
  vi.mocked(accessApi.session).mockResolvedValue({
    ...context,
    role: "mechanic",
  });
  render(<InventoryWorkspace />);
  await screen.findByText("Tu perfil no permite consultar el inventario.");
  expect(catalogApi.products).not.toHaveBeenCalled();
  expect(inventoryApi.balance).not.toHaveBeenCalled();
});
it("exige identidad antes de mostrar campos y revisión antes del POST", async () => {
  await draft();
  expect(inventoryApi.record).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirmar conteo" }));
  await screen.findByText(/Conteo confirmado/);
  expect(inventoryApi.record).toHaveBeenCalledWith(
    actor,
    {
      key: expect.any(String),
      data: {
        product_id: product.id,
        counted_quantity: "2.000",
        reason: "Conteo inicial",
        declared_unit_cost: "3.000000",
      },
    },
    expect.anything(),
  );
  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);
});
it("conserva la misma clave y contenido tras una respuesta perdida, sin reintento automático", async () => {
  vi.mocked(inventoryApi.record).mockRejectedValueOnce(
    new ApiError("network", "Disconnected"),
  );
  await draft();
  fireEvent.click(screen.getByRole("button", { name: "Confirmar conteo" }));
  await screen.findByRole("heading", { name: "Resultado por confirmar" });
  expect(inventoryApi.record).toHaveBeenCalledTimes(1);
  const first = vi.mocked(inventoryApi.record).mock.calls[0][1];
  fireEvent.click(
    screen.getByRole("button", { name: "Resolver conteo pendiente" }),
  );
  await confirmIdentity();
  await screen.findByRole("button", { name: "Resolver con la misma clave" });
  fireEvent.click(
    screen.getByRole("button", { name: "Resolver con la misma clave" }),
  );
  await screen.findByText(/Conteo confirmado/);
  expect(vi.mocked(inventoryApi.record).mock.calls[1][1]).toEqual(first);
});
it("doble clic no inicia dos conteos", async () => {
  let finish!: (value: typeof evidence) => void;
  vi.mocked(inventoryApi.record).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  await draft();
  const button = screen.getByRole("button", { name: "Confirmar conteo" });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(inventoryApi.record).toHaveBeenCalledTimes(1);
  await act(async () => finish(evidence));
  await screen.findByText(/Conteo confirmado/);
});

it("no intenta registrar si falta generación segura de claves", async () => {
  await draft();
  vi.stubGlobal("crypto", { randomUUID: undefined });
  fireEvent.click(screen.getByRole("button", { name: "Confirmar conteo" }));
  await screen.findByText(/navegador actualizado en HTTPS o localhost/);
  expect(inventoryApi.record).not.toHaveBeenCalled();
});
it("oculta datos al perder visibilidad y conserva el pendiente para su mismo contexto", async () => {
  vi.mocked(inventoryApi.record).mockRejectedValueOnce(
    new ApiError("network", "Disconnected"),
  );
  await draft();
  fireEvent.click(screen.getByRole("button", { name: "Confirmar conteo" }));
  await screen.findByRole("heading", { name: "Resultado por confirmar" });
  const first = vi.mocked(inventoryApi.record).mock.calls[0][1];
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "hidden",
  });
  fireEvent(document, new Event("visibilitychange"));
  expect(
    screen.queryByRole("heading", { name: "Filtro de aceite" }),
  ).not.toBeInTheDocument();
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
  fireEvent(document, new Event("visibilitychange"));
  await screen.findByRole("heading", { name: "Resultado por confirmar" });
  expect(screen.getByText(first.key)).toBeInTheDocument();
  expect(inventoryApi.record).toHaveBeenCalledTimes(1);
});
it("no revela ni reintenta el pendiente en otra empresa", async () => {
  vi.mocked(inventoryApi.record).mockRejectedValueOnce(
    new ApiError("network", "Disconnected"),
  );
  await draft();
  fireEvent.click(screen.getByRole("button", { name: "Confirmar conteo" }));
  await screen.findByRole("heading", { name: "Resultado por confirmar" });
  vi.mocked(accessApi.session).mockResolvedValue({
    ...context,
    tenant_id: actor,
  });
  fireEvent(window, new Event("focus"));
  await screen.findByText(/sesión original/);
  expect(screen.queryByText("Conteo inicial")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: "Filtro de aceite" }),
  ).not.toBeInTheDocument();
  expect(inventoryApi.record).toHaveBeenCalledTimes(1);
});
it("rechazo 422 vuelve a edición y actualiza el saldo sin repetir automáticamente", async () => {
  vi.mocked(inventoryApi.record).mockRejectedValueOnce(
    new ApiError("http", "Invalid", 422),
  );
  await draft();
  fireEvent.click(screen.getByRole("button", { name: "Confirmar conteo" }));
  await screen.findByText(/conteo fue rechazado y actualizamos/);
  expect(screen.getByLabelText("Cantidad contada")).toBeInTheDocument();
  expect(inventoryApi.record).toHaveBeenCalledTimes(1);
});
it("descarta un borrador cuando cambia el contexto antes de mostrar éxito", async () => {
  await draft();
  vi.mocked(accessApi.session).mockResolvedValue({
    ...context,
    tenant_id: actor,
  });
  fireEvent.click(screen.getByRole("button", { name: "Confirmar conteo" }));
  await waitFor(() =>
    expect(
      screen.queryByRole("heading", { name: "Filtro de aceite" }),
    ).not.toBeInTheDocument(),
  );
  expect(screen.queryByText(/Conteo confirmado/)).not.toBeInTheDocument();
});
