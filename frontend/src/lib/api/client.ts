export class ApiError extends Error {
  constructor(
    public readonly kind: "network" | "http" | "invalid-response",
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

// Read-only foundation client. Mutations need the session/CSRF design of hito 2.
export async function getJson<T>(
  path: string,
  decode: (value: unknown) => T,
  options: { signal?: AbortSignal } = {},
): Promise<T> {
  const url = apiPath(path);
  let response: Response;
  try {
    response = await fetch(url, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: options.signal,
    });
  } catch (error) {
    if (options.signal?.aborted) throw error;
    throw new ApiError("network", "No se pudo conectar con el servidor.");
  }
  if (!response.ok) {
    throw new ApiError(
      "http",
      "No se pudo completar la solicitud.",
      response.status,
    );
  }
  try {
    return decode(await response.json());
  } catch {
    throw new ApiError(
      "invalid-response",
      "La respuesta del servidor no es válida.",
      response.status,
    );
  }
}
