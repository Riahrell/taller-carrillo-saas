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
import { productImages } from "@/lib/catalog/images";
import { stockMinimumApi } from "@/lib/inventory/minimum";
import { inventoryApi } from "@/lib/inventory/api";
import {
  actor,
  context,
  product,
  empty,
  evidence,
  historyEvidence,
} from "./inventory-data";
import { confirmIdentity } from "./confirm-identity";

beforeEach(() => {
  vi.spyOn(productImages, "read").mockResolvedValue(null);
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
  vi.spyOn(stockMinimumApi, "read").mockResolvedValue({
    product_id: product.id,
    minimum: null,
    quantity: "0.000",
    is_active: true,
    is_low: false,
    etag: `"stock-minimum-v1-${product.id}-0"`,
  });
  vi.spyOn(inventoryApi, "balance").mockResolvedValue(empty);
  vi.spyOn(inventoryApi, "history").mockResolvedValue({
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
    screen.getByRole("button", { name: "Actualizar existencias" }),
  );
  await confirmIdentity();
  await screen.findByLabelText("Existencias reales en bodega");
  fireEvent.change(screen.getByLabelText("Existencias reales en bodega"), {
    target: { value: "2" },
  });
  fireEvent.change(screen.getByLabelText("¿Por qué cambian las existencias?"), {
    target: { value: "Conteo inicial" },
  });
  fireEvent.change(screen.getByLabelText(/Costo por unidad/), {
    target: { value: "3" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Actualizar inventario" }),
  );
}
it("muestra datos privados solo tras comprobar sesión y separa costo del precio", async () => {
  await open();
  expect(screen.getByText("Sin costo calculable")).toBeInTheDocument();
  expect(screen.getByText("12.34 USD")).toBeInTheDocument();
  expect(
    screen.getByRole("img", { name: "Producto sin imagen" }),
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
  fireEvent.click(
    screen.getByRole("button", { name: "Confirmar actualización" }),
  );
  await screen.findByText(/Existencias actualizadas/);
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
  fireEvent.click(
    screen.getByRole("button", { name: "Confirmar actualización" }),
  );
  await screen.findByRole("heading", { name: "Resultado por confirmar" });
  expect(inventoryApi.record).toHaveBeenCalledTimes(1);
  const first = vi.mocked(inventoryApi.record).mock.calls[0][1];
  fireEvent.click(
    screen.getByRole("button", { name: "Comprobar actualización pendiente" }),
  );
  await confirmIdentity();
  await screen.findByRole("button", {
    name: "Comprobar actualización pendiente",
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Comprobar actualización pendiente" }),
  );
  await screen.findByText(/Existencias actualizadas/);
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
  const button = screen.getByRole("button", {
    name: "Confirmar actualización",
  });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(inventoryApi.record).toHaveBeenCalledTimes(1);
  await act(async () => finish(evidence));
  await screen.findByText(/Existencias actualizadas/);
});

it("no intenta registrar si falta generación segura de claves", async () => {
  await draft();
  vi.stubGlobal("crypto", { randomUUID: undefined });
  fireEvent.click(
    screen.getByRole("button", { name: "Confirmar actualización" }),
  );
  await screen.findByText(/Comprueba tu acceso desde un navegador actualizado/);
  expect(inventoryApi.record).not.toHaveBeenCalled();
});
it("oculta datos al perder visibilidad y conserva el pendiente para su mismo contexto", async () => {
  vi.mocked(inventoryApi.record).mockRejectedValueOnce(
    new ApiError("network", "Disconnected"),
  );
  await draft();
  fireEvent.click(
    screen.getByRole("button", { name: "Confirmar actualización" }),
  );
  await screen.findByRole("heading", { name: "Resultado por confirmar" });
  const first = vi.mocked(inventoryApi.record).mock.calls[0][1];
  const content = screen
    .getByRole("heading", { name: "Filtro de aceite" })
    .closest("main > div");
  expect(content).not.toBeNull();
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "hidden",
  });
  fireEvent(document, new Event("visibilitychange"));
  expect(content).toHaveStyle({ visibility: "hidden" });
  expect(content).not.toHaveAttribute("hidden");
  expect(content).toHaveAttribute("aria-hidden", "true");
  expect(content).toHaveAttribute("inert");
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
  fireEvent.click(
    screen.getByRole("button", { name: "Confirmar actualización" }),
  );
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
  fireEvent.click(
    screen.getByRole("button", { name: "Confirmar actualización" }),
  );
  await screen.findByText(/No se guardó la actualización. Revisa/);
  expect(
    screen.getByLabelText("Existencias reales en bodega"),
  ).toBeInTheDocument();
  expect(inventoryApi.record).toHaveBeenCalledTimes(1);
});
it("descarta un borrador cuando cambia el contexto antes de mostrar éxito", async () => {
  await draft();
  vi.mocked(accessApi.session).mockResolvedValue({
    ...context,
    tenant_id: actor,
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Confirmar actualización" }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole("heading", { name: "Filtro de aceite" }),
    ).not.toBeInTheDocument(),
  );
  expect(
    screen.queryByText(/Existencias actualizadas/),
  ).not.toBeInTheDocument();
});

it("mantiene cantidad y motivo sin enviar al cambiar de pestaña", async () => {
  await open();
  fireEvent.click(
    screen.getByRole("button", { name: "Actualizar existencias" }),
  );
  await confirmIdentity();
  const quantity = await screen.findByLabelText("Existencias reales en bodega");
  fireEvent.change(quantity, { target: { value: "2.125" } });
  fireEvent.change(screen.getByLabelText("¿Por qué cambian las existencias?"), {
    target: { value: "Borrador pendiente" },
  });
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "hidden",
  });
  fireEvent(document, new Event("visibilitychange"));
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
  fireEvent(document, new Event("visibilitychange"));
  await waitFor(() => expect(quantity).toBeVisible());
  expect(screen.getByLabelText("Existencias reales en bodega")).toBe(quantity);
  expect(quantity).toHaveValue("2.125");
  expect(
    screen.getByLabelText("¿Por qué cambian las existencias?"),
  ).toHaveValue("Borrador pendiente");
  expect(inventoryApi.record).not.toHaveBeenCalled();
});
it.each(["IN", "OUT"] as const)(
  "envía %s como unidades de movimiento, sin calcular saldo final",
  async (direction) => {
    await open();
    fireEvent.click(
      screen.getByRole("button", {
        name: direction === "IN" ? "Registrar entrada" : "Registrar salida",
      }),
    );
    await confirmIdentity();
    const field =
      direction === "IN" ? "Unidades que entran" : "Unidades que salen";
    fireEvent.change(await screen.findByLabelText(field), {
      target: { value: "2" },
    });
    fireEvent.change(
      screen.getByLabelText("¿Por qué cambian las existencias?"),
      { target: { value: "Movimiento manual" } },
    );
    if (direction === "IN")
      fireEvent.change(screen.getByLabelText(/Costo por unidad/), {
        target: { value: "3" },
      });
    else
      expect(
        screen.queryByLabelText(/Costo por unidad/),
      ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", {
        name: direction === "IN" ? "Revisar entrada" : "Revisar salida",
      }),
    );
    expect(inventoryApi.record).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", {
        name: direction === "IN" ? "Confirmar entrada" : "Confirmar salida",
      }),
    );
    await waitFor(() => expect(inventoryApi.record).toHaveBeenCalledOnce());
    const pending = vi.mocked(inventoryApi.record).mock.calls[0][1];
    expect(pending.data).toEqual({
      product_id: product.id,
      direction,
      quantity: "2.000",
      reason: "Movimiento manual",
      unit_cost: direction === "IN" ? "3.000000" : null,
    });
    expect(pending.data).not.toHaveProperty("counted_quantity");
  },
);
it("una entrada de cero unidades no llega a confirmación", async () => {
  await open();
  fireEvent.click(screen.getByRole("button", { name: "Registrar entrada" }));
  await confirmIdentity();
  fireEvent.change(await screen.findByLabelText("Unidades que entran"), {
    target: { value: "0" },
  });
  fireEvent.change(screen.getByLabelText("¿Por qué cambian las existencias?"), {
    target: { value: "Motivo" },
  });
  fireEvent.change(screen.getByLabelText(/Costo por unidad/), {
    target: { value: "3" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Revisar entrada" }));
  expect(
    screen.queryByRole("button", { name: "Confirmar entrada" }),
  ).not.toBeInTheDocument();
  expect(inventoryApi.record).not.toHaveBeenCalled();
});
it("busca productos de inventario sin conservar la ficha anterior al no encontrar resultados", async () => {
  await open();
  vi.mocked(catalogApi.products).mockResolvedValueOnce({
    items: [],
    next_after: null,
  });
  fireEvent.change(screen.getByLabelText("Buscar producto por nombre o SKU"), {
    target: { value: "XT-77" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
  expect(
    await screen.findByText("No se encontraron productos con esos filtros."),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: "Filtro de aceite" }),
  ).not.toBeInTheDocument();
  expect(catalogApi.products).toHaveBeenLastCalledWith(
    actor,
    undefined,
    expect.anything(),
    "XT-77",
    null,
  );
  expect(inventoryApi.record).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Limpiar búsqueda" }));
  expect(
    await screen.findByRole("heading", { name: "Filtro de aceite" }),
  ).toBeInTheDocument();
  expect(catalogApi.products).toHaveBeenLastCalledWith(
    actor,
    undefined,
    expect.anything(),
    "",
    null,
  );
});
it("conserva el filtro al volver al inicio de productos", async () => {
  await open();
  fireEvent.change(screen.getByLabelText("Buscar producto por nombre o SKU"), {
    target: { value: "filtro" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Inicio de productos" }),
    ).not.toBeDisabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Inicio de productos" }));
  await waitFor(() =>
    expect(catalogApi.products).toHaveBeenLastCalledWith(
      actor,
      undefined,
      expect.anything(),
      "filtro",
      null,
    ),
  );
});
it("aplica categoría en inventario y conserva el filtro al paginar", async () => {
  const category = { id: product.id, name: "Filtros", is_active: true };
  vi.mocked(catalogApi.categories).mockResolvedValue({
    items: [category],
    next_after: null,
  });
  await open();
  vi.mocked(catalogApi.products).mockResolvedValue({
    items: [{ ...product, category_id: category.id }],
    next_after: null,
  });
  vi.mocked(catalogApi.detail).mockResolvedValue({
    ...product,
    category_id: category.id,
  });
  await screen.findByRole("option", { name: "Filtros" });
  fireEvent.change(screen.getByLabelText("Filtrar por categoría"), {
    target: { value: category.id },
  });
  await waitFor(() =>
    expect(catalogApi.products).toHaveBeenLastCalledWith(
      actor,
      undefined,
      expect.anything(),
      "",
      category.id,
    ),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Inicio de productos" }),
    ).not.toBeDisabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Inicio de productos" }));
  await waitFor(() =>
    expect(catalogApi.products).toHaveBeenLastCalledWith(
      actor,
      undefined,
      expect.anything(),
      "",
      category.id,
    ),
  );
  expect(inventoryApi.record).not.toHaveBeenCalled();
});

it("muestra un solo historial con responsable, origen y paginación", async () => {
  const row = historyEvidence;
  vi.mocked(inventoryApi.history).mockResolvedValue({
    items: [row],
    next_after: `COUNT:${row.id}`,
  });
  await open();
  expect(
    screen.getByRole("heading", { name: "Historial de inventario" }),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/Registrado por: admin@example.test/),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: "Entradas y salidas manuales" }),
  ).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", { name: "Movimientos anteriores" }),
  );
  await waitFor(() =>
    expect(inventoryApi.history).toHaveBeenCalledWith(
      actor,
      product.id,
      row,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ),
  );
});

it("bloquea movimientos mientras se configura el mínimo y los recupera al cancelar", async () => {
  await open();
  fireEvent.click(
    await screen.findByRole("button", { name: "Configurar mínimo" }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Registrar entrada" }),
    ).toBeDisabled(),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Cancelar, no cambiar nada" }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Registrar entrada" }),
    ).toBeEnabled(),
  );
  expect(inventoryApi.record).not.toHaveBeenCalled();
});

async function reviewReversal() {
  vi.mocked(inventoryApi.history).mockResolvedValue({
    items: [historyEvidence],
    next_after: null,
  });
  vi.mocked(inventoryApi.balance).mockResolvedValue({
    ...empty,
    quantity: "2.000",
    value: "6.000000",
    average_unit_cost: "3.000000",
  });
  await open();
  fireEvent.click(
    screen.getByRole("button", { name: "Deshacer último movimiento" }),
  );
  await confirmIdentity();
  await screen.findByLabelText("¿Por qué cambian las existencias?");
  expect(
    screen.queryByLabelText("Existencias reales en bodega"),
  ).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("¿Por qué cambian las existencias?"), {
    target: { value: "Registro equivocado" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Revisar corrección" }));
}
it("revisa la reversa y conserva referencia y clave si la respuesta se pierde", async () => {
  vi.mocked(inventoryApi.record).mockRejectedValueOnce(
    new ApiError("network", "lost"),
  );
  await reviewReversal();
  expect(inventoryApi.record).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirmar corrección" }));
  await screen.findByRole("heading", { name: "Resultado por confirmar" });
  const original = vi.mocked(inventoryApi.record).mock.calls[0][1];
  expect(original.data).toEqual({
    product_id: product.id,
    source: "COUNT",
    original_id: historyEvidence.id,
    reason: "Registro equivocado",
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Comprobar actualización pendiente" }),
  );
  await confirmIdentity();
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Comprobar actualización pendiente",
    }),
  );
  await waitFor(() => expect(inventoryApi.record).toHaveBeenCalledTimes(2));
  expect(vi.mocked(inventoryApi.record).mock.calls[1][1]).toEqual(original);
});
it("una reversa rechazada por cambios posteriores refresca sin repetir", async () => {
  vi.mocked(inventoryApi.record).mockRejectedValueOnce(
    new ApiError("http", "later movement", 422),
  );
  await reviewReversal();
  fireEvent.click(screen.getByRole("button", { name: "Confirmar corrección" }));
  await screen.findByText(/No se revirtió el movimiento/);
  expect(
    screen.queryByRole("heading", { name: "Resultado por confirmar" }),
  ).not.toBeInTheDocument();
  expect(inventoryApi.record).toHaveBeenCalledOnce();
});

it("mantiene el foco en la operación y lo devuelve al botón al cancelar", async () => {
  await open();
  fireEvent.click(screen.getByRole("button", { name: "Registrar salida" }));
  await confirmIdentity();
  const title = await screen.findByRole("heading", {
    name: "Registrar salida",
  });
  await waitFor(() => expect(title).toHaveFocus());
  fireEvent.click(
    screen.getByRole("button", { name: "Cancelar, no cambiar nada" }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Registrar salida" }),
    ).toHaveFocus(),
  );
  expect(inventoryApi.record).not.toHaveBeenCalled();
});

it("al regresar de otra pestaña restaura el campo solo tras comprobar la sesión", async () => {
  await open();
  fireEvent.click(screen.getByRole("button", { name: "Registrar salida" }));
  await confirmIdentity();
  const input = await screen.findByLabelText("Unidades que salen");
  input.focus();
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "hidden",
  });
  fireEvent(document, new Event("visibilitychange"));
  input.blur();
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
  fireEvent(document, new Event("visibilitychange"));
  await waitFor(() => expect(input).toBeVisible());
  await waitFor(() => expect(input).toHaveFocus());
  expect(inventoryApi.record).not.toHaveBeenCalled();
});
