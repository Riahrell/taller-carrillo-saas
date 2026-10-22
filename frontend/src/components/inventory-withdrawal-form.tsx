"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";
import type { AvailableProduct } from "@/lib/inventory/availability";
import { withdraw } from "@/lib/inventory/withdrawals";
import { ApiError } from "@/lib/api/client";

type Props = {
  actor: string;
  product: AvailableProduct;
  onSaved: () => void;
  onProblem: (message: string) => void;
};
export default function InventoryWithdrawalForm({
  actor,
  product,
  onSaved,
  onProblem,
}: Props) {
  const [open, setOpen] = useState(false);
  const [review, setReview] = useState(false);
  const [quantity, setQuantity] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const running = useRef(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  function submit(event: FormEvent) {
    event.preventDefault();
    if (running.current) return;
    if (!review) {
      setReason(reason.trim());
      setReview(true);
      return;
    }
    running.current = true;
    setBusy(true);
    const current = new AbortController();
    controller.current = current;
    void withdraw(actor, product.id, quantity, reason, crypto.randomUUID(), {
      signal: current.signal,
    })
      .then(() => {
        if (!current.signal.aborted) {
          setMessage("Salida registrada.");
          setOpen(false);
          setReview(false);
          setQuantity("");
          setReason("");
          onSaved();
        }
      })
      .catch((error: unknown) => {
        if (current.signal.aborted) return;
        if (error instanceof ApiError && error.status === 422) {
          setReview(false);
          setMessage(
            "No se pudo registrar. Comprueba la cantidad disponible y el motivo.",
          );
        } else
          onProblem(
            "No pudimos confirmar la salida. Actualiza las existencias y pide al administrador revisar el historial antes de repetirla.",
          );
      })
      .finally(() => {
        running.current = false;
        if (!current.signal.aborted) setBusy(false);
      });
  }
  if (!open)
    return (
      <div>
        <button
          className="action secondary"
          disabled={busy || product.quantity === "0.000"}
          onClick={() => {
            setOpen(true);
            setMessage("");
          }}
        >
          Registrar salida de {product.name}
        </button>
        {message && <p role="status">{message}</p>}
      </div>
    );
  return (
    <form
      className="catalog-form"
      onSubmit={submit}
      aria-label={`Salida de ${product.name}`}
    >
      <h3>{review ? "Revisar salida" : "Registrar salida"}</h3>
      <p>Retira unidades del inventario. No registra una venta ni un cobro.</p>
      {message && <p role="alert">{message}</p>}
      {review ? (
        <dl>
          <dt>Unidades que salen</dt>
          <dd>{quantity}</dd>
          <dt>Motivo</dt>
          <dd>{reason}</dd>
        </dl>
      ) : (
        <>
          <label>
            Unidades que salen
            <input
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              required
              inputMode="decimal"
              pattern="(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,3})?"
              maxLength={19}
              disabled={busy}
            />
          </label>
          <p>
            Escribe una cantidad mayor que cero. Puedes usar hasta tres
            decimales con punto.
          </p>
          <label>
            ¿Por qué salen estos productos?
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              required
              maxLength={500}
              disabled={busy}
            />
          </label>
        </>
      )}
      <div className="form-actions">
        <button
          className="action"
          disabled={
            busy || !reason.trim() || !quantity || /^0(?:\.0+)?$/.test(quantity)
          }
        >
          {busy
            ? "Registrando…"
            : review
              ? "Confirmar salida"
              : "Revisar salida"}
        </button>
        {review && (
          <button
            type="button"
            className="action secondary"
            disabled={busy}
            onClick={() => setReview(false)}
          >
            Corregir datos
          </button>
        )}
        <button
          type="button"
          className="action secondary"
          disabled={busy}
          onClick={() => {
            setOpen(false);
            setReview(false);
            setQuantity("");
            setReason("");
          }}
        >
          Cancelar, no cambiar nada
        </button>
      </div>
    </form>
  );
}
