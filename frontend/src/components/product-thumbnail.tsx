"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { productImages } from "@/lib/catalog/images";
import { ApiError } from "@/lib/api/client";

export default function ProductThumbnail({
  actor,
  productId,
  name,
  onProblem,
}: {
  actor: string;
  productId: string;
  name: string;
  onProblem: (message: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    let objectUrl: string | null = null;
    let requested = false;
    const load = () => {
      if (requested) return;
      requested = true;
      void productImages
        .read(actor, productId, abort.signal)
        .then((blob) => {
          if (abort.signal.aborted) return;
          if (blob) {
            objectUrl = URL.createObjectURL(blob);
            setUrl(objectUrl);
          }
        })
        .catch((error) => {
          if (abort.signal.aborted) return;
          if (
            error instanceof ApiError &&
            [401, 403, 428].includes(error.status ?? 0)
          )
            onProblem(
              "Comprueba el acceso para ver las imágenes del catálogo.",
            );
          else setUnavailable(true);
        });
    };
    const observer =
      typeof IntersectionObserver !== "undefined"
        ? new IntersectionObserver(
            (entries) => {
              if (entries.some((entry) => entry.isIntersecting)) {
                observer?.disconnect();
                load();
              }
            },
            { rootMargin: "100px" },
          )
        : null;
    if (observer && container.current) observer.observe(container.current);
    else load();
    return () => {
      abort.abort();
      observer?.disconnect();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [actor, productId, onProblem]);
  return (
    <div ref={container} className="catalog-thumbnail">
      {url ? (
        <Image
          unoptimized
          src={url}
          alt={`Imagen de ${name}`}
          width={88}
          height={88}
        />
      ) : (
        <span
          role="img"
          aria-label={
            unavailable ? "Imagen no disponible" : "Producto sin imagen"
          }
        >
          ▧
        </span>
      )}
    </div>
  );
}
