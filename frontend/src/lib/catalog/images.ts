import { ApiError } from "@/lib/api/client";
import { accessApi, readCsrfCookie } from "@/lib/access/api";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const INPUT = 8 * 1024 * 1024;
const OUTPUT = 12 * 1024 * 1024;
let fallbackTail: Promise<void> = Promise.resolve();
export type ImageCommand = Readonly<{
  product_id: string;
  key: string;
  file: File;
}>;
export type ImageReceipt = {
  id: string;
  product_id: string;
  is_current: boolean;
};
function validId(value: unknown): value is string {
  return typeof value === "string" && value.length === 36 && UUID.test(value);
}
export function decodeImageReceipt(
  value: unknown,
  product: string,
): ImageReceipt {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Invalid image receipt");
  const data = value as Record<string, unknown>;
  const fields = [
    "id",
    "product_id",
    "width",
    "height",
    "size",
    "created_at",
    "is_current",
  ];
  if (
    Object.keys(data).sort().join() !== fields.sort().join() ||
    !validId(data.id) ||
    data.product_id !== product ||
    typeof data.is_current !== "boolean" ||
    !Number.isInteger(data.width) ||
    Number(data.width) < 1 ||
    Number(data.width) > 1600 ||
    !Number.isInteger(data.height) ||
    Number(data.height) < 1 ||
    Number(data.height) > 1600 ||
    !Number.isInteger(data.size) ||
    Number(data.size) < 1 ||
    Number(data.size) > OUTPUT ||
    typeof data.created_at !== "string" ||
    !Number.isFinite(Date.parse(data.created_at))
  )
    throw new Error("Invalid image receipt");
  return { id: data.id, product_id: product, is_current: data.is_current };
}
async function transfer<T>(
  actor: string,
  product: string,
  decode: (response: Response) => Promise<T>,
  signal?: AbortSignal,
  command?: ImageCommand,
  operational = false,
  thumbnail = false,
): Promise<T> {
  if (!validId(actor) || !validId(product)) throw new Error("Invalid context");
  if (
    command &&
    (!validId(command.key) ||
      command.product_id !== product ||
      command.file.size < 1 ||
      command.file.size > INPUT)
  )
    throw new Error("Invalid image command");
  const controller = new AbortController();
  const cancel = () => controller.abort();
  if (signal?.aborted) cancel();
  signal?.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(cancel, 60_000);
  try {
    let csrf = "";
    if (command) {
      await accessApi.prepare({ signal: controller.signal });
      csrf = readCsrfCookie(
        document.cookie,
        window.location.protocol === "https:",
      );
    }
    const perform = async () => {
      const response = await fetch(
        operational && !command
          ? `/api/inventory/availability/products/${product}/image${thumbnail ? "/thumbnail" : ""}`
          : `/api/catalog/products/${product}/image${thumbnail && !command ? "/thumbnail" : ""}`,
        {
          method: command ? "POST" : "GET",
          credentials: "same-origin",
          cache: "no-store",
          redirect: "error",
          signal: controller.signal,
          headers: {
            Accept: command ? "application/json" : "image/webp, image/png",
            "X-Requested-With": "XMLHttpRequest",
            "X-Expected-Membership": actor,
            ...(command
              ? {
                  "Content-Type": "application/octet-stream",
                  "X-CSRF-Token": csrf!,
                  "Idempotency-Key": command.key,
                }
              : {}),
          },
          body: command?.file,
        },
      );
      if (!response.ok)
        throw new ApiError(
          "http",
          "No se pudo completar la imagen.",
          response.status,
        );
      if (response.headers.get("X-Access-Membership") !== actor)
        throw new ApiError("invalid-response", "El contexto cambió.");
      try {
        return await decode(response);
      } catch (error) {
        if (error instanceof ApiError) throw error;
        throw new ApiError(
          "invalid-response",
          "Respuesta de imagen no válida.",
        );
      }
    };
    if (typeof navigator !== "undefined" && navigator.locks?.request)
      return await navigator.locks.request(
        "carrillo_access_http",
        { signal: controller.signal },
        perform,
      );
    const task = fallbackTail.then(() => {
      if (controller.signal.aborted)
        throw new DOMException("Aborted", "AbortError");
      return perform();
    });
    fallbackTail = task.then(
      () => undefined,
      () => undefined,
    );
    return await task;
  } catch (error) {
    if (signal?.aborted || error instanceof ApiError) throw error;
    throw new ApiError("network", "No se pudo conectar con imágenes.");
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
  }
}
export const productImages = {
  upload(actor: string, command: ImageCommand, signal?: AbortSignal) {
    return transfer(
      actor,
      command.product_id,
      async (response) => {
        if (response.status !== 200) throw new Error("Invalid status");
        return decodeImageReceipt(await response.json(), command.product_id);
      },
      signal,
      command,
    );
  },
  read(
    actor: string,
    product: string,
    signal?: AbortSignal,
    operational = false,
    thumbnail = false,
  ) {
    return transfer(
      actor,
      product,
      async (response) => {
        if (response.status === 204) return null;
        const media = response.headers.get("Content-Type")?.split(";")[0];
        if (
          response.status !== 200 ||
          !["image/png", "image/webp"].includes(media ?? "") ||
          !validId(response.headers.get("X-Image-Id"))
        )
          throw new Error("Invalid image");
        const reader = response.body?.getReader();
        if (!reader) throw new Error("No image stream");
        const declared = response.headers.get("Content-Length");
        const maximum =
          media === "image/webp"
            ? thumbnail
              ? 256 * 1024
              : 4 * 1024 * 1024
            : OUTPUT;
        const minimum = media === "image/webp" ? 30 : 33;
        const capacity = declared === null ? maximum : Number(declared);
        if (
          !Number.isInteger(capacity) ||
          capacity < minimum ||
          capacity > maximum
        )
          throw new Error("Invalid image length");
        const buffer = new Uint8Array(capacity);
        let size = 0;
        try {
          while (true) {
            const next = await reader.read();
            if (next.done) break;
            if (size + next.value.length > capacity)
              throw new Error("Image too large");
            buffer.set(next.value, size);
            size += next.value.length;
          }
        } finally {
          await reader.cancel();
          reader.releaseLock();
        }
        if (size < minimum || (declared !== null && size !== capacity))
          throw new Error("Invalid image");
        const signature = buffer.subarray(0, 8);
        const webp = media === "image/webp";
        if (
          webp
            ? String.fromCharCode(...buffer.subarray(0, 4)) !== "RIFF" ||
              String.fromCharCode(...buffer.subarray(8, 12)) !== "WEBP" ||
              new DataView(buffer.buffer).getUint32(4, true) + 8 !== size
            : signature.join() !== "137,80,78,71,13,10,26,10"
        )
          throw new Error("Invalid image signature");
        return new Blob([buffer.subarray(0, size)], { type: media! });
      },
      signal,
      undefined,
      operational,
      thumbnail,
    );
  },
};
