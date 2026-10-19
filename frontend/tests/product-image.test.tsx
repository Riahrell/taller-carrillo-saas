import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import ProductImage from "@/components/product-image";
import { productImages } from "@/lib/catalog/images";
import { accessApi } from "@/lib/access/api";
import { identityApi } from "@/lib/access/identity";
import { ApiError } from "@/lib/api/client";
import { context, product } from "./inventory-data";
import { confirmIdentity } from "./confirm-identity";

beforeEach(() => {
  vi.spyOn(productImages, "read").mockResolvedValue(null);
  vi.spyOn(productImages, "upload").mockResolvedValue({
    id: product.id,
    product_id: product.id,
    is_current: true,
  });
  vi.spyOn(accessApi, "session").mockResolvedValue(context);
  vi.spyOn(accessApi, "reauthenticate").mockResolvedValue(undefined);
  vi.spyOn(identityApi, "status").mockResolvedValue({
    mfa_enabled: false,
    mailbox_verified: true,
    recent_seconds: 0,
  });
  vi.stubGlobal("crypto", {
    randomUUID: () => "a1a1a1a1-1111-4111-8111-a1a1a1a1a1a1",
  });
});
function open(overrides = {}) {
  const callbacks = {
    onPending: vi.fn(),
    onEngaged: vi.fn(),
    onWriting: vi.fn(),
    onChanged: vi.fn(),
    onProblem: vi.fn(),
  };
  const view = render(
    <ProductImage
      session={context}
      productId={product.id}
      productName={product.name}
      disabled={false}
      initialPending={null}
      {...callbacks}
      {...overrides}
    />,
  );
  return { ...callbacks, ...view };
}
async function prepare() {
  fireEvent.click(
    await screen.findByRole("button", { name: "Agregar imagen" }),
  );
  await confirmIdentity();
  const input = await screen.findByLabelText("Subir desde la galería", {
    selector: "input",
  });
  const file = new File(["synthetic bytes"], "producto.png", {
    type: "image/png",
  });
  fireEvent.change(input, { target: { files: [file] } });
  fireEvent.submit(input.closest("form")!);
  await screen.findByText("Revisar cambio de imagen");
  return file;
}
it("exige identidad y revisión antes de enviar un archivo privado", async () => {
  const state = open();
  const file = await prepare();
  expect(productImages.upload).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirmar imagen" }));
  await screen.findByText("Imagen guardada. Esta imagen sigue siendo privada.");
  expect(productImages.upload).toHaveBeenCalledWith(
    context.membership_id,
    { key: expect.any(String), product_id: product.id, file },
    expect.any(AbortSignal),
  );
  expect(state.onPending).toHaveBeenLastCalledWith(null);
});

it("ofrece cámara trasera y galería separadas sin enviar antes de revisar", async () => {
  open();
  fireEvent.click(
    await screen.findByRole("button", { name: "Agregar imagen" }),
  );
  await confirmIdentity();
  const camera = await screen.findByLabelText("Tomar foto", {
    selector: "input",
  });
  const gallery = screen.getByLabelText("Subir desde la galería", {
    selector: "input",
  });
  expect(camera).toHaveAttribute("capture", "environment");
  expect(camera).toHaveAttribute("accept", "image/*");
  expect(gallery).not.toHaveAttribute("capture");
  const photo = new File(["synthetic camera bytes"], "camera.jpg", {
    type: "image/jpeg",
  });
  fireEvent.change(camera, { target: { files: [photo] } });
  fireEvent.submit(camera.closest("form")!);
  expect(await screen.findByText(/Archivo: camera.jpg/)).toBeInTheDocument();
  expect(productImages.upload).not.toHaveBeenCalled();
});
it("conserva mismo archivo y llave ante una respuesta incierta; nunca reintenta solo", async () => {
  vi.mocked(productImages.upload).mockRejectedValueOnce(
    new ApiError("network", "lost"),
  );
  open();
  await prepare();
  fireEvent.click(screen.getByRole("button", { name: "Confirmar imagen" }));
  await screen.findByText(/La imagen puede haberse guardado/);
  expect(productImages.upload).toHaveBeenCalledTimes(1);
  const first = vi.mocked(productImages.upload).mock.calls[0][1];
  fireEvent.click(
    screen.getByRole("button", { name: "Resolver imagen pendiente" }),
  );
  await confirmIdentity();
  fireEvent.click(
    await screen.findByRole("button", { name: "Reintentar la misma imagen" }),
  );
  await screen.findByText("Imagen guardada. Esta imagen sigue siendo privada.");
  expect(vi.mocked(productImages.upload).mock.calls[1][1]).toBe(first);
});
it("una reserva terminal permite revisar otra imagen sin reutilizar la operación", async () => {
  vi.mocked(productImages.upload).mockRejectedValueOnce(
    new ApiError("http", "superseded", 410),
  );
  const state = open();
  await prepare();
  fireEvent.click(screen.getByRole("button", { name: "Confirmar imagen" }));
  await screen.findByText(/La operación no puede continuar/);
  expect(state.onPending).toHaveBeenLastCalledWith(null);
  expect(screen.getByRole("button", { name: "Agregar imagen" })).toBeEnabled();
});
it("retira la vista ante cambio de empresa después del envío", async () => {
  vi.mocked(accessApi.session)
    .mockResolvedValueOnce(context)
    .mockResolvedValueOnce({ ...context, tenant_id: context.user_id });
  const state = open();
  await prepare();
  fireEvent.click(screen.getByRole("button", { name: "Confirmar imagen" }));
  await waitFor(() => expect(state.onProblem).toHaveBeenCalled());
  expect(state.onPending).not.toHaveBeenLastCalledWith(null);
});
it("la cancelación desmonta la solicitud y no registra éxito tardío", async () => {
  let complete!: (value: {
    id: string;
    product_id: string;
    is_current: boolean;
  }) => void;
  vi.mocked(productImages.upload).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  const state = open();
  await prepare();
  fireEvent.click(screen.getByRole("button", { name: "Confirmar imagen" }));
  await waitFor(() => expect(productImages.upload).toHaveBeenCalledTimes(1));
  const signal = vi.mocked(productImages.upload).mock.calls[0][2]!;
  state.unmount();
  expect(signal.aborted).toBe(true);
  complete({ id: product.id, product_id: product.id, is_current: true });
  expect(state.onPending).not.toHaveBeenLastCalledWith(null);
});
