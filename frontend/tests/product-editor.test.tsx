import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import ProductEditor from "@/components/product-editor";
import { catalogApi } from "@/lib/catalog/api";
import { accessApi } from "@/lib/access/api";
import { identityApi } from "@/lib/access/identity";
import { ApiError } from "@/lib/api/client";
import { context, product } from "./inventory-data";
import { confirmIdentity } from "./confirm-identity";

const version = { product, etag: `"product-v1-${product.id}-0"` };
const props = () => ({
  target: product.id,
  session: context,
  onWriting: vi.fn(),
  onChanged: vi.fn(),
  onProblem: vi.fn(),
  onClose: vi.fn(),
  onSaved: vi.fn(),
});
beforeEach(() => {
  vi.spyOn(catalogApi, "editable").mockResolvedValue(version);
  vi.spyOn(catalogApi, "categories").mockResolvedValue({
    items: [],
    next_after: null,
  });
  vi.spyOn(catalogApi, "updateProduct").mockResolvedValue({
    product: { ...product, is_active: false },
    etag: `"product-v1-${product.id}-1"`,
  });
  vi.spyOn(accessApi, "session").mockResolvedValue(context);
  vi.spyOn(accessApi, "reauthenticate").mockResolvedValue(undefined);
  vi.spyOn(identityApi, "status").mockResolvedValue({
    mfa_enabled: false,
    mailbox_verified: true,
    recent_seconds: 0,
  });
});
async function edit() {
  const options = props();
  render(<ProductEditor {...options} />);
  await confirmIdentity();
  await screen.findByLabelText("Nombre del producto");
  return options;
}
async function reviewInactive() {
  const options = await edit();
  fireEvent.change(screen.getByLabelText("Estado del producto"), {
    target: { value: "inactive" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Revisar cambios" }));
  return options;
}
it("carga valores y guarda el estado solo después de revisar, sin stock ni costo", async () => {
  const options = await reviewInactive();
  expect(catalogApi.updateProduct).not.toHaveBeenCalled();
  expect(screen.queryByLabelText(/Existencias/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Guardar cambios" }));
  await waitFor(() => expect(options.onSaved).toHaveBeenCalledOnce());
  expect(catalogApi.updateProduct).toHaveBeenCalledWith(
    context.membership_id,
    product.id,
    version.etag,
    {
      name: product.name,
      sku: product.sku,
      price: product.price,
      category_id: null,
      is_active: false,
    },
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
});
it("conflicto de versión exige recargar sin reenviar automáticamente", async () => {
  vi.mocked(catalogApi.updateProduct).mockRejectedValue(
    new ApiError("http", "stale", 412),
  );
  const options = await reviewInactive();
  fireEvent.click(screen.getByRole("button", { name: "Guardar cambios" }));
  await screen.findByText(/Otro administrador cambió/);
  expect(
    screen.queryByRole("button", { name: "Guardar cambios" }),
  ).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", {
      name: "Recargar producto y descartar cambios",
    }),
  );
  await waitFor(() => expect(catalogApi.editable).toHaveBeenCalledTimes(2));
  expect(catalogApi.updateProduct).toHaveBeenCalledOnce();
  expect(options.onSaved).not.toHaveBeenCalled();
});
it("un SKU ocupado conserva el borrador y permite corregirlo", async () => {
  vi.mocked(catalogApi.updateProduct).mockRejectedValue(
    new ApiError("http", "sku", 409),
  );
  await reviewInactive();
  fireEvent.click(screen.getByRole("button", { name: "Guardar cambios" }));
  await screen.findByText(/Ese SKU ya está en uso/);
  expect(screen.getByLabelText("Estado del producto")).toHaveValue("inactive");
});
it.each([503, 403])(
  "resultado %s no afirma éxito ni permite repetir a ciegas",
  async (status) => {
    vi.mocked(catalogApi.updateProduct).mockRejectedValue(
      new ApiError("http", "failed", status),
    );
    const options = await reviewInactive();
    fireEvent.click(screen.getByRole("button", { name: "Guardar cambios" }));
    await waitFor(() => expect(options.onProblem).toHaveBeenCalledOnce());
    expect(options.onSaved).not.toHaveBeenCalled();
  },
);
it("no anuncia éxito si cambia el contexto después de guardar", async () => {
  const options = await reviewInactive();
  vi.mocked(accessApi.session).mockResolvedValue({
    ...context,
    tenant_id: context.user_id,
  });
  fireEvent.click(screen.getByRole("button", { name: "Guardar cambios" }));
  await waitFor(() => expect(options.onProblem).toHaveBeenCalledOnce());
  expect(options.onSaved).not.toHaveBeenCalled();
});
it("cancelar no guarda cambios", async () => {
  const options = await edit();
  fireEvent.click(screen.getByRole("button", { name: "Cancelar, no guardar" }));
  expect(options.onClose).toHaveBeenCalledOnce();
  expect(catalogApi.updateProduct).not.toHaveBeenCalled();
});
it("omite resultados tardíos al desmontar el editor", async () => {
  let resolve!: (value: typeof version) => void;
  vi.mocked(catalogApi.editable).mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const options = props();
  const view = render(<ProductEditor {...options} />);
  view.unmount();
  await act(async () => resolve(version));
  expect(options.onProblem).not.toHaveBeenCalled();
  expect(options.onSaved).not.toHaveBeenCalled();
});
