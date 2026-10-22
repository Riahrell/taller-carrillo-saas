"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { productImages } from "@/lib/catalog/images";
import { ApiError } from "@/lib/api/client";

type Photo = {
  scope: string;
  state: "loading" | "ready" | "empty" | "error";
  url: string | null;
};

export default function ProductThumbnail({
  actor,
  productId,
  name,
  onProblem,
  availability = false,
}: {
  actor: string;
  productId: string;
  name: string;
  onProblem: (message: string) => void;
  availability?: boolean;
}) {
  const container = useRef<HTMLDivElement>(null);
  const retry = useRef<() => void>(() => {});
  const [photo, setPhoto] = useState<Photo | null>(null);
  const scope = actor + ":" + productId + ":" + availability;
  useEffect(() => {
    const abort = new AbortController();
    let objectUrl: string | null = null;
    let running = false;
    let loaded = false;
    const visible = () => {
      if (document.visibilityState !== "visible") return false;
      const rect = container.current?.getBoundingClientRect();
      return (
        !!rect &&
        rect.width > 0 &&
        rect.height > 0 &&
        rect.bottom > 0 &&
        rect.top < window.innerHeight &&
        rect.right > 0 &&
        rect.left < window.innerWidth
      );
    };
    const load = async (force = false) => {
      if (abort.signal.aborted || running || (loaded && !force)) return;
      running = true;
      setPhoto({ scope, state: "loading", url: null });
      try {
        const blob = await productImages.read(
          actor,
          productId,
          abort.signal,
          availability,
          true,
        );
        if (abort.signal.aborted) return;
        const next = blob ? URL.createObjectURL(blob) : null;
        const previous = objectUrl;
        objectUrl = next;
        loaded = true;
        setPhoto({ scope, state: next ? "ready" : "empty", url: next });
        if (previous) URL.revokeObjectURL(previous);
      } catch (error) {
        if (abort.signal.aborted) return;
        loaded = false;
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        objectUrl = null;
        if (
          error instanceof ApiError &&
          [401, 403, 428].includes(error.status ?? 0)
        ) {
          onProblem("Comprueba el acceso para ver las fotos del catálogo.");
        } else setPhoto({ scope, state: "error", url: null });
      } finally {
        running = false;
      }
    };
    const refresh = () => {
      if (visible()) void load(true);
    };
    const changed = (event: Event) => {
      if (!(event instanceof CustomEvent)) return;
      const detail = event.detail;
      if (detail?.actor !== actor || detail?.productId !== productId) return;
      loaded = false;
      refresh();
    };
    retry.current = () => void load(true);
    const observer =
      typeof IntersectionObserver !== "undefined"
        ? new IntersectionObserver(
            (entries) => {
              if (entries.some((entry) => entry.isIntersecting)) void load();
            },
            { rootMargin: "100px" },
          )
        : null;
    if (observer && container.current) {
      observer.observe(container.current);
      // Start visible images immediately; observer remains for initially hidden
      // workspaces and products farther down the page.
      if (visible()) void load();
    } else void load();
    window.addEventListener("focus", refresh);
    window.addEventListener("pageshow", refresh);
    document.addEventListener("visibilitychange", refresh);
    window.addEventListener("carrillo_product_photo_changed", changed);
    return () => {
      abort.abort();
      observer?.disconnect();
      retry.current = () => {};
      window.removeEventListener("focus", refresh);
      window.removeEventListener("pageshow", refresh);
      document.removeEventListener("visibilitychange", refresh);
      window.removeEventListener("carrillo_product_photo_changed", changed);
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [actor, productId, scope, onProblem, availability]);
  const current = photo?.scope === scope ? photo : null;
  return (
    <div
      ref={container}
      className="catalog-thumbnail"
      aria-busy={!current || current.state === "loading"}
    >
      {current?.state === "ready" && current.url ? (
        <Image
          unoptimized
          src={current.url}
          alt={`Imagen de ${name}`}
          width={88}
          height={88}
          onError={() => setPhoto({ scope, state: "error", url: null })}
        />
      ) : current?.state === "error" ? (
        <button
          type="button"
          className="thumbnail-retry"
          aria-label={`Volver a cargar la foto de ${name}`}
          onClick={() => retry.current()}
        >
          Foto no disponible
          <br />
          Reintentar
        </button>
      ) : (
        <span
          role="img"
          aria-label={
            current?.state === "empty"
              ? "Producto sin imagen"
              : `Cargando foto de ${name}`
          }
        >
          {current?.state === "empty" ? "Sin foto" : "Cargando…"}
        </span>
      )}
    </div>
  );
}
