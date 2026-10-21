"use client";

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
import { sameContext } from "@/lib/access/same-context";
import { ApiError } from "@/lib/api/client";
import { displayDecimal, quantityInput } from "@/lib/inventory/api";
import { stockMinimumApi, type StockMinimum } from "@/lib/inventory/minimum";

type Props = {
  session: SessionContext;
  productId: string;
  disabled: boolean;
  onWriting: (value: boolean) => void;
  onEngaged: (value: boolean) => void;
  onChanged: () => void;
  onProblem: (message: string) => void;
};
export default function StockMinimumControl({
  session,
  productId,
  disabled,
  onWriting,
  onEngaged,
  onChanged,
  onProblem,
}: Props) {
  const [value, setValue] = useState<StockMinimum | null>(null);
  const [stage, setStage] = useState<
    "closed" | "identity" | "edit" | "review" | "conflict"
  >("closed");
  const [draft, setDraft] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const controller = useRef<AbortController | null>(null);
  const operation = useRef(false);
  const deadline = useRef(0);
  const notice = useRef<HTMLParagraphElement>(null);
  const formId = useId();
  const actor = session.membership_id;
  const identityWriting = useCallback(
    (writing: boolean) => {
      setBusy(writing);
      onWriting(writing);
    },
    [onWriting],
  );
  useEffect(() => {
    const current = new AbortController();
    controller.current = current;
    void stockMinimumApi
      .read(actor, productId, { signal: current.signal })
      .then((result) => {
        if (!current.signal.aborted) {
          setValue(result);
          setDraft(result.minimum ?? "");
        }
      })
      .catch(() => {
        if (!current.signal.aborted)
          onProblem(
            "No pudimos consultar el mínimo del producto. Comprueba el acceso.",
          );
      });
    return () => {
      current.abort();
      controller.current?.abort();
      onWriting(false);
      onEngaged(false);
    };
  }, [actor, productId, attempt, onWriting, onEngaged, onProblem]);
  useEffect(() => {
    onEngaged(stage !== "closed");
  }, [stage, onEngaged]);
  useEffect(() => {
    if (message) notice.current?.focus({ preventScroll: true });
  }, [message]);
  function authorize() {
    if (performance.now() < deadline.current) return true;
    setStage("identity");
    return false;
  }
  function review(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!authorize() || operation.current) return;
    try {
      setDraft(draft.trim() ? quantityInput(draft) : "");
      setMessage("");
      setStage("review");
    } catch {
      setMessage(
        "Escribe cero o una cantidad positiva con hasta tres decimales, o deja el campo vacío.",
      );
    }
  }
  async function save() {
    if (!value || operation.current || !authorize()) return;
    operation.current = true;
    setBusy(true);
    onWriting(true);
    const current = new AbortController();
    controller.current = current;
    try {
      const result = await stockMinimumApi.save(
        actor,
        productId,
        value.etag,
        draft === "" ? null : draft,
        { signal: current.signal },
      );
      const context = await accessApi.session({ signal: current.signal });
      if (!sameContext(context, session) || context.role !== "admin")
        throw new Error("Changed context");
      if (!current.signal.aborted) {
        setValue(result);
        setStage("closed");
        setMessage("Mínimo de inventario guardado.");
      }
    } catch (error) {
      if (current.signal.aborted) return;
      if (error instanceof ApiError && error.status === 412) {
        setStage("conflict");
        setMessage(
          "El mínimo cambió en otra sesión. Recarga la configuración antes de modificarla.",
        );
      } else
        onProblem(
          "No pudimos confirmar si el mínimo cambió. Comprueba el acceso y consulta el producto antes de repetir.",
        );
    } finally {
      operation.current = false;
      onWriting(false);
      if (!current.signal.aborted) setBusy(false);
    }
  }
  return (
    <section
      className="inventory-history"
      aria-label="Mínimo de inventario"
      aria-busy={busy}
    >
      <h3>Mínimo de inventario</h3>
      {message && (
        <p role="status" ref={notice} tabIndex={-1}>
          {message}
        </p>
      )}
      {!value && <p role="status">Consultando mínimo…</p>}
      {value && stage === "closed" && (
        <>
          <p>
            {value.minimum === null
              ? "Sin mínimo configurado. Los avisos están desactivados para este producto."
              : `Avisar con ${displayDecimal(value.minimum)} unidades o menos.`}
          </p>
          {value.is_low && (
            <p className="stock-low-notice" role="status">
              <strong>Stock bajo: necesita reposición.</strong> Existencias
              consultadas: {displayDecimal(value.quantity)}.
            </p>
          )}
          {!value.is_active && value.minimum !== null && (
            <p>
              Los avisos están suspendidos mientras el producto esté inactivo.
            </p>
          )}
          <button
            className="action"
            disabled={disabled || busy}
            onClick={() => {
              setMessage("");
              setStage("identity");
            }}
          >
            Configurar mínimo
          </button>
        </>
      )}
      {stage === "identity" && (
        <IdentityConfirmation
          session={session}
          purpose="configurar el mínimo de inventario"
          onWriting={identityWriting}
          onChanged={onChanged}
          onProblem={onProblem}
          onConfirmed={(remaining) => {
            deadline.current = performance.now() + remaining;
            setStage("edit");
          }}
        />
      )}
      {stage === "edit" && (
        <form id={formId} className="catalog-form" onSubmit={review}>
          <label>
            Avisar cuando queden estas unidades o menos
            <input
              maxLength={19}
              inputMode="decimal"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
          </label>
          <p>
            Deja el campo vacío para desactivar el aviso. Usa 0 para avisar solo
            cuando no haya existencias. Hasta tres decimales. Esto no compra
            productos ni cambia las existencias.
          </p>
        </form>
      )}
      {stage === "review" && (
        <p>
          {draft === ""
            ? "Desactivar el aviso de stock bajo para este producto."
            : `Avisar cuando haya ${displayDecimal(draft)} unidades o menos.`}{" "}
          Las existencias y su costo se conservan.
        </p>
      )}
      {stage !== "closed" && (
        <div className="form-actions">
          {stage === "edit" && (
            <button
              className="action"
              type="submit"
              form={formId}
              disabled={busy}
            >
              Revisar mínimo
            </button>
          )}
          {stage === "review" && (
            <>
              <button
                className="action"
                disabled={busy}
                onClick={() => void save()}
              >
                {busy ? "Guardando…" : "Guardar mínimo"}
              </button>
              <button
                className="action secondary"
                disabled={busy}
                onClick={() => setStage("edit")}
              >
                Corregir mínimo
              </button>
            </>
          )}
          {stage === "conflict" && (
            <button
              className="action"
              onClick={() => {
                setValue(null);
                setMessage("");
                setStage("closed");
                setAttempt((v) => v + 1);
              }}
            >
              Recargar mínimo y descartar cambios
            </button>
          )}
          <button
            className="action secondary"
            disabled={busy}
            onClick={() => {
              setDraft(value?.minimum ?? "");
              setMessage("");
              setStage("closed");
            }}
          >
            Cancelar, no cambiar nada
          </button>
        </div>
      )}
    </section>
  );
}
