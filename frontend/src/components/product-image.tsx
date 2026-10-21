"use client";

import Image from "next/image";

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
} from "react";
import IdentityConfirmation from "@/components/identity-confirmation";
import { accessApi, type SessionContext } from "@/lib/access/api";
import { ApiError } from "@/lib/api/client";
import { productImages, type ImageCommand } from "@/lib/catalog/images";

export type ScopedImagePending = {
  session: SessionContext;
  command: ImageCommand;
};
type Props = {
  session: SessionContext;
  productId: string;
  productName: string;
  disabled: boolean;
  initialPending: ScopedImagePending | null;
  onPending: (value: ScopedImagePending | null) => void;
  onEngaged: (value: boolean) => void;
  onWriting: (value: boolean) => void;
  onChanged: () => void;
  onProblem: (message: string) => void;
};
const uncertain =
  "La imagen puede haberse guardado. Confirma tu identidad y reintenta la misma operación. Al cerrar o recargar se pierde este pendiente; revisa la imagen actual antes de iniciar otra carga.";
export default function ProductImage(props: Props) {
  const {
    session,
    productId,
    productName,
    disabled,
    initialPending,
    onPending,
    onEngaged,
    onWriting,
    onChanged,
    onProblem,
  } = props;
  const photoFormId = useId();
  const [url, setUrl] = useState<string | null>(null);
  const [available, setAvailable] = useState(true);
  const [stage, setStage] = useState<"closed" | "identity" | "pick" | "review">(
    "closed",
  );
  const [file, setFile] = useState<File | null>(null);
  const [pending, setPending] = useState<ImageCommand | null>(
    initialPending?.command ?? null,
  );
  const [busy, setBusy] = useState(false);
  const [deadline, setDeadline] = useState(0);
  const [message, setMessage] = useState(initialPending ? uncertain : "");
  const [revision, setRevision] = useState(0);
  const controller = useRef<AbortController | null>(null);
  const alive = useRef(false);
  const lock = useRef(false);
  const blobUrl = useRef<string | null>(null);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      controller.current?.abort();
      if (blobUrl.current) URL.revokeObjectURL(blobUrl.current);
    };
  }, []);
  useEffect(() => {
    const abort = new AbortController();
    void productImages
      .read(session.membership_id, productId, abort.signal)
      .then((blob) => {
        if (abort.signal.aborted) return;
        if (blobUrl.current) URL.revokeObjectURL(blobUrl.current);
        blobUrl.current = blob ? URL.createObjectURL(blob) : null;
        setUrl(blobUrl.current);
        setAvailable(true);
      })
      .catch((error) => {
        if (abort.signal.aborted) return;
        if (
          error instanceof ApiError &&
          (error.status === 401 || error.status === 403 || error.status === 428)
        )
          onProblem("Comprueba el acceso para consultar la imagen.");
        else {
          setAvailable(false);
          setMessage(
            "Las imágenes no están disponibles. Puedes seguir consultando el inventario.",
          );
        }
      });
    return () => abort.abort();
  }, [session.membership_id, productId, revision, onProblem]);
  useEffect(() => {
    onEngaged(stage !== "closed" || pending !== null);
  }, [stage, pending, onEngaged]);
  const identityWriting = useCallback(
    (value: boolean) => {
      setBusy(value);
      onWriting(value);
    },
    [onWriting],
  );
  function remember(command: ImageCommand | null) {
    setPending(command);
    onPending(command ? { session, command } : null);
  }
  function review(event: FormEvent) {
    event.preventDefault();
    if (!file || file.size < 1 || file.size > 8 * 1024 * 1024) {
      setMessage("Selecciona una imagen de hasta 8 MiB.");
      return;
    }
    setStage("review");
    setMessage("");
  }
  async function upload() {
    if (lock.current) return;
    if (performance.now() >= deadline) {
      setStage("identity");
      setMessage("Confirma tu identidad de nuevo.");
      return;
    }
    if (!pending && !file) return;
    if (!pending && !globalThis.crypto?.randomUUID) {
      setMessage("Se requiere una conexión segura para cargar imágenes.");
      return;
    }
    const command =
      pending ??
      Object.freeze({
        product_id: productId,
        key: crypto.randomUUID(),
        file: file!,
      });
    remember(command);
    lock.current = true;
    setBusy(true);
    onWriting(true);
    const abort = new AbortController();
    controller.current = abort;
    try {
      await productImages.upload(session.membership_id, command, abort.signal);
      const current = await accessApi.session({ signal: abort.signal });
      if (abort.signal.aborted || !alive.current) return;
      if (
        current.user_id !== session.user_id ||
        current.membership_id !== session.membership_id ||
        current.tenant_id !== session.tenant_id ||
        current.role !== "admin"
      ) {
        onProblem(uncertain);
        return;
      }
      remember(null);
      setFile(null);
      setStage("closed");
      setRevision((value) => value + 1);
      window.dispatchEvent(
        new CustomEvent("carrillo_product_photo_changed", {
          detail: { actor: session.membership_id, productId },
        }),
      );
      onChanged();
      setMessage("Foto guardada. Solo está disponible dentro de tu empresa.");
    } catch (error) {
      if (abort.signal.aborted || !alive.current) return;
      if (
        error instanceof ApiError &&
        [401, 403, 412].includes(error.status ?? 0)
      ) {
        onProblem(uncertain);
        return;
      }
      if (
        error instanceof ApiError &&
        [422, 410, 413, 415].includes(error.status ?? 0)
      ) {
        remember(null);
        setFile(null);
        setStage("closed");
        setRevision((value) => value + 1);
        setMessage(
          error.status === 422
            ? "El archivo no es una imagen estática válida. Usa JPEG, PNG o WebP."
            : "La operación no puede continuar. Revisa la imagen actual y elige de nuevo.",
        );
      } else {
        setStage("closed");
        setMessage(uncertain);
      }
    } finally {
      if (alive.current) {
        lock.current = false;
        setBusy(false);
        onWriting(false);
      }
    }
  }
  const chosen = pending?.file ?? file;
  return (
    <section
      className="product-image"
      data-editing={stage !== "closed"}
      aria-label="Imagen del producto"
    >
      {url ? (
        <Image
          unoptimized
          width={1600}
          height={1600}
          src={url}
          alt={`Imagen de ${productName}`}
          className="product-image-photo"
        />
      ) : (
        <div
          className="inventory-image-placeholder"
          role="img"
          aria-label="Producto sin imagen"
        >
          <span aria-hidden="true">▧</span>
          <small>Sin imagen</small>
        </div>
      )}
      {message && <p role="status">{message}</p>}
      {stage === "closed" && (
        <div className="form-actions photo-actions">
          {!available && (
            <button
              type="button"
              className="action secondary"
              disabled={disabled || busy}
              onClick={() => {
                setMessage("Cargando foto…");
                setRevision((value) => value + 1);
              }}
            >
              Volver a cargar la foto
            </button>
          )}
          <button
            className="action"
            disabled={disabled || busy || !available}
            onClick={() => {
              setStage("identity");
              setMessage("");
            }}
          >
            {pending
              ? "Comprobar foto pendiente"
              : url
                ? "Cambiar foto del producto"
                : "Agregar foto del producto"}
          </button>
          {!available && (
            <button
              className="action secondary"
              disabled={busy}
              onClick={() => setRevision((value) => value + 1)}
            >
              Comprobar imágenes
            </button>
          )}
        </div>
      )}
      {stage === "identity" && (
        <IdentityConfirmation
          purpose={
            pending
              ? "resolver la imagen pendiente"
              : "cambiar la imagen del producto"
          }
          session={session}
          onWriting={identityWriting}
          onChanged={onChanged}
          onProblem={onProblem}
          onConfirmed={(remaining) => {
            setDeadline(performance.now() + remaining);
            setStage(pending ? "review" : "pick");
          }}
        />
      )}
      {stage === "pick" && (
        <form id={photoFormId} className="photo-form" onSubmit={review}>
          <div className="photo-source-options">
            <label>
              Tomar foto
              <input
                type="file"
                accept="image/*"
                capture="environment"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </label>
            <label>
              Elegir foto de la galería
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp"
                onChange={(event) => {
                  setFile(event.target.files?.[0] ?? null);
                }}
              />
            </label>
          </div>
          {file && <p>Archivo elegido: {file.name}</p>}
          <p>
            JPEG, PNG o WebP estático. Máximo 8 MiB. Se eliminarán metadatos y
            se ajustará el tamaño.
          </p>
          <p>
            La cámara depende del navegador del móvil. Si no se abre, usa la
            galería.
          </p>
        </form>
      )}
      {stage === "review" && (
        <div>
          <h3>Revisa la foto antes de guardarla</h3>
          <p>Producto: {productName}</p>
          <p>
            Archivo: {chosen?.name} ·{" "}
            {chosen ? Math.ceil(chosen.size / 1024) : 0} KiB
          </p>
          <p>
            Reemplazará la imagen principal privada. No publica el producto ni
            modifica costo, precio o existencias.
          </p>
        </div>
      )}
      {stage !== "closed" && (
        <div className="form-actions">
          {stage === "pick" && (
            <>
              <button
                className="action primary"
                type="submit"
                form={photoFormId}
                disabled={busy}
              >
                Continuar y revisar foto
              </button>
            </>
          )}
          {stage === "review" && (
            <>
              <button
                className="action primary"
                disabled={busy}
                onClick={() => void upload()}
              >
                {busy
                  ? "Guardando…"
                  : pending
                    ? "Reintentar guardar esta foto"
                    : "Guardar foto del producto"}
              </button>
            </>
          )}
          {!pending && (
            <button
              className="action secondary"
              type="button"
              disabled={busy}
              onClick={() => {
                setStage("closed");
                setFile(null);
              }}
            >
              Cancelar, conservar la foto actual
            </button>
          )}
        </div>
      )}
      {!available && stage !== "closed" && (
        <button
          className="action"
          disabled={busy}
          onClick={() => setRevision((value) => value + 1)}
        >
          Comprobar imágenes
        </button>
      )}
    </section>
  );
}
