export class ApiError extends Error {
  constructor(
    public readonly kind: "network" | "http" | "invalid-response" | "csrf",
    message: string,
    public readonly status: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function apiPath(path: string): string {
  const base = new URL("https://same-origin.invalid");
  const url = new URL(`/api${path}`, base);
  if (
    !path.startsWith("/") ||
    path.startsWith("//") ||
    path.includes("\\") ||
    url.origin !== base.origin ||
    !url.pathname.startsWith("/api/") ||
    url.hash
  ) {
    throw new TypeError("La ruta de API debe ser interna.");
  }
  return url.pathname + url.search;
}

export type RequestOptions = { signal?: AbortSignal };

async function request<T>(
  path: string,
  decode: (value: unknown, status: number, headers: Headers) => T,
  options: RequestOptions,
  mutation?: {
    body: unknown;
    csrf: string;
    method?: "PATCH";
    etag?: string;
    actor?: string;
  },
): Promise<T> {
  const url = apiPath(path);
  const controller = new AbortController();
  const cancel = () => controller.abort();
  if (options.signal?.aborted) controller.abort();
  options.signal?.addEventListener("abort", cancel, { once: true });
  const timeout = setTimeout(cancel, 15_000);
  try {
    const perform = () =>
      fetch(url, {
        method: mutation ? (mutation.method ?? "POST") : "GET",
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
          ...(mutation
            ? {
                "Content-Type": "application/json",
                "X-CSRF-Token": mutation.csrf,
                ...(mutation.etag ? { "If-Match": mutation.etag } : {}),
                ...(mutation.actor
                  ? { "X-Expected-Membership": mutation.actor }
                  : {}),
              }
            : {}),
        },
        body: mutation ? JSON.stringify(mutation.body) : undefined,
        signal: controller.signal,
      });
    // Serialize requests (including reads that may clear stale cookies) across
    // cooperating tabs. The timeout covers waiting for the lock too.
    const response =
      typeof navigator !== "undefined" && navigator.locks?.request
        ? await navigator.locks.request(
            "carrillo_access_http",
            { signal: controller.signal },
            perform,
          )
        : await perform();
    if (!response.ok) {
      throw new ApiError(
        "http",
        "No se pudo completar la solicitud.",
        response.status,
      );
    }
    try {
      return decode(
        response.status === 204 ? undefined : await response.json(),
        response.status,
        response.headers,
      );
    } catch (error) {
      if (controller.signal.aborted) throw error;
      throw new ApiError(
        "invalid-response",
        "La respuesta del servidor no es válida.",
        response.status,
      );
    }
  } catch (error) {
    if (options.signal?.aborted) throw error;
    if (error instanceof ApiError) throw error;
    throw new ApiError("network", "No se pudo conectar con el servidor.");
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener("abort", cancel);
  }
}

export function getJson<T>(
  path: string,
  decode: (value: unknown, status: number, headers: Headers) => T,
  options: RequestOptions = {},
): Promise<T> {
  return request(path, decode, options);
}

export function sendJson<T>(
  path: string,
  body: unknown,
  csrf: string,
  decode: (value: unknown, status: number, headers: Headers) => T,
  options: RequestOptions = {},
): Promise<T> {
  return request(path, decode, options, { body, csrf });
}

export function patchJson<T>(
  path: string,
  body: unknown,
  csrf: string,
  etag: string,
  actor: string,
  decode: (value: unknown, status: number, headers: Headers) => T,
  options: RequestOptions = {},
): Promise<T> {
  return request(path, decode, options, {
    body,
    csrf,
    method: "PATCH",
    etag,
    actor,
  });
}

export function createJson<T>(
  path: string,
  body: unknown,
  csrf: string,
  actor: string,
  decode: (value: unknown, status: number, headers: Headers) => T,
  options: RequestOptions = {},
): Promise<T> {
  return request(path, decode, options, { body, csrf, actor });
}
