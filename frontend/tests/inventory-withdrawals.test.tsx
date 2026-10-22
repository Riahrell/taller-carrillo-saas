import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import InventoryWithdrawalForm from "@/components/inventory-withdrawal-form";
import InventoryPermissionManager from "@/components/inventory-permission-manager";
import { withdraw } from "@/lib/inventory/withdrawals";
import {
  permissionsApi,
  withdrawalPermissionsApi,
} from "@/lib/employees/permissions";
import { membershipsApi } from "@/lib/memberships/api";
import { accessApi } from "@/lib/access/api";
import { ApiError } from "@/lib/api/client";

const actor = "00000000-0000-4000-8000-000000000001";
const target = "00000000-0000-4000-8000-000000000002";
const product = {
  id: target,
  name: "Filtro",
  sku: null,
  category: null,
  quantity: "2.000",
  price: "10.00",
};
const session = {
  user_id: actor,
  tenant_id: target,
  membership_id: actor,
  tenant_name: "Empresa",
  role: "admin" as const,
};
const permission = {
  membership_id: target,
  granted: false,
  eligible: true,
  effective: false,
  revision: "0",
  etag: `"inventory-withdrawal-v1-${target}-0"`,
};
beforeEach(() => {
  vi.restoreAllMocks();
});

it("exige revisión y confirmación antes de retirar unidades, sin enviar costos", async () => {
  const write = vi
    .spyOn(await import("@/lib/inventory/withdrawals"), "withdraw")
    .mockResolvedValue({ id: actor, after_quantity: "1.000" });
  const saved = vi.fn();
  render(
    <InventoryWithdrawalForm
      actor={actor}
      product={product}
      onSaved={saved}
      onProblem={vi.fn()}
    />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Registrar salida de Filtro" }),
  );
  fireEvent.change(screen.getByLabelText("Unidades que salen"), {
    target: { value: "1" },
  });
  fireEvent.change(screen.getByLabelText("¿Por qué salen estos productos?"), {
    target: { value: "Retiro autorizado" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Revisar salida" }));
  expect(write).not.toHaveBeenCalled();
  expect(screen.getByText("Retiro autorizado")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Confirmar salida" }));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  expect(write).toHaveBeenCalledWith(
    actor,
    target,
    "1",
    "Retiro autorizado",
    expect.any(String),
    expect.any(Object),
  );
});

it("una respuesta perdida bloquea el flujo sin repetir la salida", async () => {
  const write = vi
    .spyOn(await import("@/lib/inventory/withdrawals"), "withdraw")
    .mockRejectedValue(new ApiError("network", "Lost"));
  const problem = vi.fn();
  render(
    <InventoryWithdrawalForm
      actor={actor}
      product={product}
      onSaved={vi.fn()}
      onProblem={problem}
    />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Registrar salida de Filtro" }),
  );
  fireEvent.change(screen.getByLabelText("Unidades que salen"), {
    target: { value: "1" },
  });
  fireEvent.change(screen.getByLabelText("¿Por qué salen estos productos?"), {
    target: { value: "Retiro" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Revisar salida" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirmar salida" }));
  await waitFor(() => expect(problem).toHaveBeenCalledOnce());
  expect(write).toHaveBeenCalledOnce();
});

it("usa la concesión de salidas para un cajero sin modificar su permiso de Solo lectura", async () => {
  vi.spyOn(membershipsApi, "page").mockResolvedValue({
    items: [
      {
        membership_id: target,
        email: "cajero@example.com",
        role: "cashier",
        is_active: true,
        user_is_active: true,
      },
    ],
    next_after: null,
  });
  vi.spyOn(withdrawalPermissionsApi, "current").mockResolvedValue(permission);
  const read = vi.spyOn(permissionsApi, "change");
  const write = vi.spyOn(withdrawalPermissionsApi, "change").mockResolvedValue({
    ...permission,
    granted: true,
    effective: true,
    revision: "1",
  });
  vi.spyOn(accessApi, "session").mockResolvedValue(session);
  render(
    <InventoryPermissionManager
      session={session}
      onProblem={vi.fn()}
      onWriting={vi.fn()}
      onChanged={vi.fn()}
    />,
  );
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Revisar permiso de cajero@example.com",
    }),
  );
  fireEvent.click(
    await screen.findByLabelText("Permitir consultar y registrar salidas"),
  );
  fireEvent.click(screen.getByRole("button", { name: "Revisar cambio" }));
  expect(write).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirmar permiso" }));
  await screen.findByText(/Salidas manuales autorizadas/);
  expect(write).toHaveBeenCalledWith(
    actor,
    target,
    true,
    permission.etag,
    expect.any(Object),
  );
  expect(read).not.toHaveBeenCalled();
});

it("transporta la retirada con contexto e idempotencia y rechaza campos financieros en respuesta", async () => {
  const proof = `v1.1999999999.${"a".repeat(43)}.${"b".repeat(64)}`;
  document.cookie = `carrillo_csrf=${proof}; Path=/`;
  vi.spyOn(accessApi, "prepare").mockResolvedValue(undefined);
  const fetch = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        id: actor,
        product_id: target,
        operation_key: actor,
        before_quantity: "2.000",
        after_quantity: "1.000",
        quantity_delta: "-1.000",
        reason: "Retiro",
        created_at: "2026-10-21T00:00:00Z",
        value_delta: "-3.000000",
      }),
      { status: 200, headers: { "X-Access-Membership": actor } },
    ),
  );
  vi.stubGlobal("fetch", fetch);
  await expect(
    withdraw(actor, target, "1", "Retiro", actor),
  ).rejects.toMatchObject({ kind: "invalid-response" });
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
    product_id: target,
    quantity: "1",
    reason: "Retiro",
  });
  expect(fetch.mock.calls[0][1].headers["X-Expected-Membership"]).toBe(actor);
  expect(fetch.mock.calls[0][1].headers["Idempotency-Key"]).toBe(actor);
  document.cookie = "carrillo_csrf=; Max-Age=0; Path=/";
});
