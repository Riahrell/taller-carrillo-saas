import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import CatalogWorkspace from "@/components/catalog-workspace";
import { accessApi, type SessionContext } from "@/lib/access/api";
import { identityApi } from "@/lib/access/identity";
import { ApiError } from "@/lib/api/client";
import { catalogApi, type Product } from "@/lib/catalog/api";
import { confirmIdentity } from "./confirm-identity";

const actor = "00000000-0000-4000-8000-000000000001";
const target = "00000000-0000-4000-8000-000000000002";
const createdId = "00000000-0000-4000-8000-000000000003";
const context: SessionContext = {
  user_id: actor,
  membership_id: actor,
  tenant_id: target,
  tenant_name: "Empresa A",
  role: "admin",
};
const product: Product = {
  id: target,
  name: "Filtro existente",
  is_active: true,
  sku: null,
  category_id: null,
  price: "10.00",
};
const created: Product = {
  id: createdId,
  name: "Filtro nuevo",
  is_active: true,
  sku: "F-1",
  category_id: target,
  price: "12.34",
};
const category = { id: target, name: "Repuestos", is_active: true };
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
  vi.spyOn(catalogApi, "categories").mockResolvedValue({
    items: [category],
    next_after: null,
  });
  vi.spyOn(catalogApi, "createProduct").mockResolvedValue(created);
  vi.spyOn(catalogApi, "createCategory").mockResolvedValue({
    ...category,
    name: "Nueva",
  });
  vi.spyOn(catalogApi, "detail").mockResolvedValue(created);
});
afterEach(() => vi.useRealTimers());
async function open() {
  render(<CatalogWorkspace />);
  await screen.findByText("Filtro existente");
}
async function draft() {
  await open();
  fireEvent.click(screen.getByRole("button", { name: "Nuevo producto" }));
  await confirmIdentity();
  await screen.findByLabelText("Nombre");
  fireEvent.change(screen.getByLabelText("Nombre"), {
    target: { value: "Filtro nuevo" },
  });
  fireEvent.change(screen.getByLabelText("Precio de venta (USD)"), {
    target: { value: "12,34" },
  });
  fireEvent.change(screen.getByLabelText("SKU (opcional)"), {
    target: { value: "F-1" },
  });
  fireEvent.change(screen.getByLabelText("Categoría (opcional)"), {
    target: { value: target },
  });
  fireEvent.click(screen.getByRole("button", { name: "Revisar producto" }));
}
it("consulta sin pedir contraseña y la exige antes de montar campos de alta", async () => {
  await open();
  expect(accessApi.reauthenticate).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Nuevo producto" }));
  await screen.findByLabelText("Tu contraseña actual");
  expect(
    screen.queryByLabelText("Precio de venta (USD)"),
  ).not.toBeInTheDocument();
  expect(catalogApi.createProduct).not.toHaveBeenCalled();
});
it("revisa el precio exacto y comprueba sesión y detalle tras crear", async () => {
  await draft();
  expect(screen.getByText("12.34 USD")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Registrar producto" }));
  expect(
    await screen.findByText("Producto creado: Filtro nuevo."),
  ).toBeInTheDocument();
  expect(catalogApi.createProduct).toHaveBeenCalledWith(
    actor,
    { name: "Filtro nuevo", price: "12.34", sku: "F-1", category_id: target },
    expect.anything(),
  );
  expect(catalogApi.detail).toHaveBeenCalledWith(
    actor,
    createdId,
    expect.anything(),
  );
  expect(screen.queryByLabelText("Nombre")).not.toBeInTheDocument();
  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);
});
it("permite crear categoría sin precio ni campos de inventario", async () => {
  await open();
  fireEvent.click(screen.getByRole("button", { name: "Categorías" }));
  fireEvent.click(screen.getByRole("button", { name: "Nueva categoría" }));
  await confirmIdentity();
  fireEvent.change(await screen.findByLabelText("Nombre"), {
    target: { value: "Nueva" },
  });
  expect(
    screen.queryByLabelText("Precio de venta (USD)"),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Revisar categoría" }));
  fireEvent.click(screen.getByRole("button", { name: "Registrar categoría" }));
  expect(
    await screen.findByText("Categoría creada: Nueva."),
  ).toBeInTheDocument();
  expect(catalogApi.createCategory).toHaveBeenCalledWith(
    actor,
    "Nueva",
    expect.anything(),
  );
});

it("reutiliza la confirmación vigente para otra alta sin prolongarla", async () => {
  await draft();
  fireEvent.click(screen.getByRole("button", { name: "Registrar producto" }));
  await screen.findByText("Producto creado: Filtro nuevo.");
  fireEvent.click(screen.getByRole("button", { name: "Nuevo producto" }));
  expect(await screen.findByLabelText("Nombre")).toHaveValue("");
  expect(
    screen.queryByLabelText("Tu contraseña actual"),
  ).not.toBeInTheDocument();
  expect(accessApi.reauthenticate).toHaveBeenCalledOnce();
});
it.each(["cashier", "mechanic", "read_only"] as const)(
  "no consulta catálogo administrativo para %s",
  async (role) => {
    vi.mocked(accessApi.session).mockResolvedValue({ ...context, role });
    render(<CatalogWorkspace />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Tu perfil no permite",
    );
    expect(catalogApi.products).not.toHaveBeenCalled();
    expect(catalogApi.categories).not.toHaveBeenCalled();
  },
);
it("conflicto de SKU conserva el borrador para revisión, sin reintento", async () => {
  vi.mocked(catalogApi.createProduct).mockRejectedValue(
    new ApiError("http", "private", 409),
  );
  await draft();
  fireEvent.click(screen.getByRole("button", { name: "Registrar producto" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "SKU ya está en uso",
  );
  expect(screen.getByLabelText("Nombre")).toHaveValue("Filtro nuevo");
  expect(catalogApi.createProduct).toHaveBeenCalledOnce();
});
it.each([428, 503])(
  "retira borrador tras %s y comprobar acceso no repite alta",
  async (status) => {
    vi.mocked(catalogApi.createProduct).mockRejectedValue(
      new ApiError("http", "private", status),
    );
    await draft();
    fireEvent.click(screen.getByRole("button", { name: "Registrar producto" }));
    await screen.findByRole("alert");
    expect(screen.queryByLabelText("Nombre")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Comprobar acceso" }));
    await screen.findByText("Filtro existente");
    expect(catalogApi.createProduct).toHaveBeenCalledOnce();
  },
);
it("descarta resultado de alta después de perder foco, sin afirmar éxito", async () => {
  let finish!: (value: Product) => void;
  vi.mocked(catalogApi.createProduct).mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  await draft();
  fireEvent.click(screen.getByRole("button", { name: "Registrar producto" }));
  await waitFor(() => expect(catalogApi.createProduct).toHaveBeenCalledOnce());
  fireEvent.focus(window);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "No pudimos confirmar el resultado",
  );
  await act(async () => finish(created));
  expect(
    screen.queryByText("Producto creado: Filtro nuevo."),
  ).not.toBeInTheDocument();
  expect(catalogApi.detail).not.toHaveBeenCalled();
});
it("cambio de empresa tras POST retira los datos y exige revisar el resultado", async () => {
  await draft();
  vi.mocked(accessApi.session).mockResolvedValueOnce({
    ...context,
    tenant_id: createdId,
  });
  fireEvent.click(screen.getByRole("button", { name: "Registrar producto" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "revisa el catálogo",
  );
  expect(catalogApi.detail).not.toHaveBeenCalled();
});
it("caducidad local retira campos y no genera una escritura", async () => {
  await open();
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole("button", { name: "Nuevo producto" }));
  await act(async () => {
    await Promise.resolve();
  });
  fireEvent.change(screen.getByLabelText("Tu contraseña actual"), {
    target: { value: "synthetic password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Confirmar identidad" }));
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(screen.getByLabelText("Nombre")).toBeInTheDocument();
  await act(async () => vi.advanceTimersByTime(270_001));
  expect(screen.queryByLabelText("Nombre")).not.toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "confirmación de identidad venció",
  );
  expect(catalogApi.createProduct).not.toHaveBeenCalled();
});

it("conserva el borrador al volver y lo retira si cambia la empresa", async () => {
  await open();
  fireEvent.click(screen.getByRole("button", { name: "Nuevo producto" }));
  await confirmIdentity();
  const input = await screen.findByLabelText("Nombre");
  fireEvent.change(input, { target: { value: "Borrador conservado" } });
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "hidden",
  });
  fireEvent(document, new Event("visibilitychange"));
  expect(input).toBeVisible();
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
  fireEvent(document, new Event("visibilitychange"));
  await waitFor(() => expect(input).toBeVisible());
  expect(screen.getByLabelText("Nombre")).toBe(input);
  expect(input).toHaveValue("Borrador conservado");
  expect(catalogApi.createProduct).not.toHaveBeenCalled();
  vi.mocked(accessApi.session).mockResolvedValue({
    ...context,
    tenant_id: createdId,
  });
  fireEvent.focus(window);
  await screen.findByText(/La sesión cambió/);
  expect(screen.queryByLabelText("Nombre")).not.toBeInTheDocument();
});
it("busca en el servidor y permite limpiar el filtro del catálogo", async () => {
  await open();
  vi.mocked(catalogApi.products).mockResolvedValueOnce({
    items: [],
    next_after: null,
  });
  fireEvent.change(screen.getByLabelText("Buscar producto por nombre o SKU"), {
    target: { value: "  filtro  " },
  });
  fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
  expect(
    await screen.findByText("No se encontraron productos con esos filtros."),
  ).toBeInTheDocument();
  expect(catalogApi.products).toHaveBeenLastCalledWith(
    actor,
    undefined,
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
    "filtro",
    null,
  );
  fireEvent.click(screen.getByRole("button", { name: "Limpiar búsqueda" }));
  expect(await screen.findByText("Filtro existente")).toBeInTheDocument();
  expect(catalogApi.products).toHaveBeenLastCalledWith(
    actor,
    undefined,
    expect.anything(),
    "",
    null,
  );
});
it("no permite buscar mientras se revisa un producto por registrar", async () => {
  await draft();
  expect(
    screen.getByLabelText("Buscar producto por nombre o SKU"),
  ).toBeDisabled();
});
it("combina categoría y texto en catálogo y limpia ambos filtros", async () => {
  await open();
  vi.mocked(catalogApi.products).mockResolvedValue({
    items: [{ ...product, category_id: category.id }],
    next_after: null,
  });
  await screen.findByRole("option", { name: "Repuestos" });
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
    expect(screen.getByRole("button", { name: "Buscar" })).not.toBeDisabled(),
  );
  fireEvent.change(screen.getByLabelText("Buscar producto por nombre o SKU"), {
    target: { value: "filtro" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
  await waitFor(() =>
    expect(catalogApi.products).toHaveBeenLastCalledWith(
      actor,
      undefined,
      expect.anything(),
      "filtro",
      category.id,
    ),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Limpiar búsqueda" }),
    ).not.toBeDisabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Limpiar búsqueda" }));
  await waitFor(() =>
    expect(catalogApi.products).toHaveBeenLastCalledWith(
      actor,
      undefined,
      expect.anything(),
      "",
      null,
    ),
  );
});

it("conserva la lista durante la consulta y al abrir el formulario", async () => {
  await open();
  let resolve!: (value: { items: Product[]; next_after: null }) => void;
  vi.mocked(catalogApi.products).mockReturnValueOnce(
    new Promise((done) => {
      resolve = done;
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Volver al principio" }));
  const item = screen.getByText("Filtro existente");
  expect(item).toBeInTheDocument();
  expect(item.closest("[inert]")).not.toBeNull();
  await act(async () => resolve({ items: [product], next_after: null }));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Nuevo producto" }),
    ).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Nuevo producto" }));
  expect(screen.getByText("Filtro existente")).toBeInTheDocument();
  expect(
    screen.getByText("Filtro existente").closest("[inert]"),
  ).not.toBeNull();
});

it("edita un producto existente y refresca su estado sin crear otro", async () => {
  vi.spyOn(catalogApi, "editable").mockResolvedValue({
    product,
    etag: `"product-v1-${target}-0"`,
  });
  vi.spyOn(catalogApi, "updateProduct").mockResolvedValue({
    product: { ...product, is_active: false },
    etag: `"product-v1-${target}-1"`,
  });
  await open();
  fireEvent.click(
    screen.getByRole("button", { name: "Editar producto de Filtro existente" }),
  );
  await confirmIdentity();
  await screen.findByLabelText("Nombre del producto");
  fireEvent.change(screen.getByLabelText("Estado del producto"), {
    target: { value: "inactive" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Revisar cambios" }));
  vi.mocked(catalogApi.products).mockResolvedValue({
    items: [{ ...product, is_active: false }],
    next_after: null,
  });
  fireEvent.click(screen.getByRole("button", { name: "Guardar cambios" }));
  await screen.findByText("Cambios del producto guardados.");
  expect(screen.getByText("Inactivo")).toBeInTheDocument();
  expect(catalogApi.createProduct).not.toHaveBeenCalled();
});

it("al cancelar un producto devuelve el foco al botón de registro", async () => {
  await draft();
  fireEvent.click(screen.getByRole("button", { name: "Cancelar, no guardar" }));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Nuevo producto" }),
    ).toHaveFocus(),
  );
});
