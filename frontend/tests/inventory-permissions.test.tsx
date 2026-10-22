import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import InventoryAvailability from "@/components/inventory-availability";
import InventoryPermissionManager from "@/components/inventory-permission-manager";
import { accessApi, type SessionContext } from "@/lib/access/api";
import { permissionsApi } from "@/lib/employees/permissions";
import { availabilityApi } from "@/lib/inventory/availability";
import { membershipsApi } from "@/lib/memberships/api";
import { catalogApi } from "@/lib/catalog/api";
import { ApiError } from "@/lib/api/client";

vi.mock("@/components/product-thumbnail", () => ({
  default: () => <span>Foto del producto</span>,
}));
const actor = "00000000-0000-4000-8000-000000000001",
  target = "00000000-0000-4000-8000-000000000002";
const context: SessionContext = {
  user_id: actor,
  membership_id: actor,
  tenant_id: target,
  tenant_name: "Empresa A",
  role: "read_only",
};
const member = {
  membership_id: target,
  email: "empleado@example.com",
  role: "read_only" as const,
  is_active: true,
  user_is_active: true,
};
const permission = {
  membership_id: target,
  granted: false,
  eligible: true,
  effective: false,
  revision: "0",
  etag: `"inventory-read-v1-${target}-0"`,
};
const product = {
  id: target,
  name: "Filtro de aceite",
  sku: "F-01",
  category: "Filtros",
  price: "12.34",
  quantity: "3.000",
};
const props = {
  session: { ...context, role: "admin" as const },
  onProblem: vi.fn(),
  onWriting: vi.fn(),
  onChanged: vi.fn(),
};
beforeEach(() => {
  vi.stubGlobal("BroadcastChannel", undefined);
  vi.spyOn(accessApi, "session").mockResolvedValue(context);
  vi.spyOn(availabilityApi, "products").mockResolvedValue({
    items: [product],
    next_after: null,
  });
  vi.spyOn(availabilityApi, "categories").mockResolvedValue({
    items: [],
    next_after: null,
  });
  vi.spyOn(catalogApi, "products").mockResolvedValue({
    items: [],
    next_after: null,
  });
  vi.spyOn(catalogApi, "categories").mockResolvedValue({
    items: [],
    next_after: null,
  });
  vi.spyOn(membershipsApi, "page").mockResolvedValue({
    items: [member],
    next_after: null,
  });
  vi.spyOn(permissionsApi, "current").mockResolvedValue(permission);
  vi.spyOn(permissionsApi, "change").mockResolvedValue({
    ...permission,
    granted: true,
    effective: true,
    revision: "1",
    etag: `"inventory-read-v1-${target}-1"`,
  });
});
describe("consulta con permiso", () => {
  it("muestra la proyección operativa y usa categorías autorizadas sin consultar administración", async () => {
    render(<InventoryAvailability />);
    await screen.findByRole("heading", { name: product.name });
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("12.34 USD")).toBeInTheDocument();
    expect(screen.queryByText(/Costo promedio/)).not.toBeInTheDocument();
    expect(catalogApi.products).not.toHaveBeenCalled();
    expect(catalogApi.categories).not.toHaveBeenCalled();
    expect(availabilityApi.products).toHaveBeenCalledWith(
      actor,
      { q: "", category: null },
      expect.any(Object),
    );
  });
  it("la revocación retira datos al consultar otra vez sin necesitar un nuevo login", async () => {
    render(<InventoryAvailability />);
    await screen.findByRole("heading", { name: product.name });
    vi.mocked(availabilityApi.products).mockRejectedValue(
      new ApiError("http", "Denied", 403),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Actualizar consulta" }),
    );
    await screen.findByRole("alert");
    expect(
      screen.queryByRole("heading", { name: product.name }),
    ).not.toBeInTheDocument();
  });
  it("cambiar a otra empresa bloquea el resultado sin mezclar productos", async () => {
    render(<InventoryAvailability />);
    await screen.findByRole("heading", { name: product.name });
    vi.mocked(accessApi.session).mockResolvedValue({
      ...context,
      tenant_id: actor,
    });
    await act(async () => window.dispatchEvent(new Event("focus")));
    await screen.findByText(
      "La sesión cambió. Vuelve al inicio y comprueba el acceso.",
    );
    expect(
      screen.queryByRole("heading", { name: product.name }),
    ).not.toBeInTheDocument();
  });
});
describe("concesión explícita", () => {
  async function open() {
    vi.mocked(accessApi.session).mockResolvedValue(props.session);
    render(<InventoryPermissionManager {...props} />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: `Revisar permiso de ${member.email}`,
      }),
    );
    await screen.findByLabelText("Permitir consultar existencias");
  }
  it("requiere revisión y confirmación, vinculando target y versión revisada", async () => {
    await open();
    expect(permissionsApi.change).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText("Permitir consultar existencias"));
    fireEvent.click(screen.getByRole("button", { name: "Revisar cambio" }));
    expect(permissionsApi.change).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirmar permiso" }));
    await screen.findByText(/Consulta de inventario autorizada/);
    expect(permissionsApi.change).toHaveBeenCalledWith(
      actor,
      target,
      true,
      permission.etag,
      expect.any(Object),
    );
  });
  it("un conflicto no repite ni sobrescribe el permiso", async () => {
    await open();
    vi.mocked(permissionsApi.change).mockRejectedValue(
      new ApiError("http", "Stale", 412),
    );
    fireEvent.click(screen.getByLabelText("Permitir consultar existencias"));
    fireEvent.click(screen.getByRole("button", { name: "Revisar cambio" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirmar permiso" }));
    await screen.findByText(/El permiso cambió mientras lo revisabas/);
    expect(permissionsApi.change).toHaveBeenCalledTimes(1);
  });
});
