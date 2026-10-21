import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import StockMinimumControl from "@/components/stock-minimum";
import { stockMinimumApi } from "@/lib/inventory/minimum";
import { accessApi } from "@/lib/access/api";
import { identityApi } from "@/lib/access/identity";
import { ApiError } from "@/lib/api/client";
import { context, product } from "./inventory-data";
import { confirmIdentity } from "./confirm-identity";

const empty = {
  product_id: product.id,
  minimum: null,
  quantity: "0.000",
  is_active: true,
  is_low: false,
  etag: `"stock-minimum-v1-${product.id}-0"`,
};
const props = () => ({
  session: context,
  productId: product.id,
  disabled: false,
  onWriting: vi.fn(),
  onEngaged: vi.fn(),
  onChanged: vi.fn(),
  onProblem: vi.fn(),
});
beforeEach(() => {
  vi.spyOn(stockMinimumApi, "read").mockResolvedValue(empty);
  vi.spyOn(stockMinimumApi, "save").mockResolvedValue({
    ...empty,
    minimum: "0.000",
    is_low: true,
    etag: `"stock-minimum-v1-${product.id}-1"`,
  });
  vi.spyOn(accessApi, "session").mockResolvedValue(context);
  vi.spyOn(accessApi, "reauthenticate").mockResolvedValue(undefined);
  vi.spyOn(identityApi, "status").mockResolvedValue({
    mfa_enabled: false,
    mailbox_verified: true,
    recent_seconds: 0,
  });
});
async function draft() {
  const options = props();
  render(<StockMinimumControl {...options} />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Configurar mínimo" }),
  );
  await confirmIdentity();
  const input = await screen.findByLabelText(
    "Avisar cuando queden estas unidades o menos",
  );
  fireEvent.change(input, { target: { value: "0" } });
  return options;
}
it("sin configuración no inventa un mínimo ni muestra alerta", async () => {
  render(<StockMinimumControl {...props()} />);
  await screen.findByText(/Sin mínimo configurado/);
  expect(screen.queryByText(/necesita reposición/)).not.toBeInTheDocument();
});
it("revisa cero antes de guardar y muestra aviso con stock cero", async () => {
  await draft();
  fireEvent.click(screen.getByRole("button", { name: "Revisar mínimo" }));
  expect(stockMinimumApi.save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Guardar mínimo" }));
  await screen.findByText(/Stock bajo: necesita reposición/);
  expect(stockMinimumApi.save).toHaveBeenCalledWith(
    context.membership_id,
    product.id,
    empty.etag,
    "0.000",
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
});
it("cancelar no cambia el mínimo", async () => {
  await draft();
  fireEvent.click(
    screen.getByRole("button", { name: "Cancelar, no cambiar nada" }),
  );
  expect(stockMinimumApi.save).not.toHaveBeenCalled();
  expect(screen.getByText(/Sin mínimo configurado/)).toBeInTheDocument();
});
it("una versión antigua obliga a recargar sin reenviar", async () => {
  vi.mocked(stockMinimumApi.save).mockRejectedValue(
    new ApiError("http", "stale", 412),
  );
  await draft();
  fireEvent.click(screen.getByRole("button", { name: "Revisar mínimo" }));
  fireEvent.click(screen.getByRole("button", { name: "Guardar mínimo" }));
  await screen.findByText(/El mínimo cambió/);
  fireEvent.click(
    screen.getByRole("button", { name: "Recargar mínimo y descartar cambios" }),
  );
  await waitFor(() => expect(stockMinimumApi.read).toHaveBeenCalledTimes(2));
  expect(stockMinimumApi.save).toHaveBeenCalledOnce();
});
it("cambio de empresa después de guardar no anuncia éxito", async () => {
  const options = await draft();
  vi.mocked(accessApi.session).mockResolvedValue({
    ...context,
    tenant_id: context.user_id,
  });
  fireEvent.click(screen.getByRole("button", { name: "Revisar mínimo" }));
  fireEvent.click(screen.getByRole("button", { name: "Guardar mínimo" }));
  await waitFor(() => expect(options.onProblem).toHaveBeenCalledOnce());
  expect(
    screen.queryByText("Mínimo de inventario guardado."),
  ).not.toBeInTheDocument();
});
