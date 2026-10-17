"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { accessApi, type SessionContext } from "@/lib/access/api";
import { ApiError } from "@/lib/api/client";

type Props = {
  session: SessionContext;
  onWriting: (value: boolean) => void;
  onChanged: () => void;
  onProblem: (message: string) => void;
  onConfirmed: (remaining: number) => void;
};

export default function IdentityConfirmation({
  session,
  onWriting,
  onChanged,
  onProblem,
  onConfirmed,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const operation = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const form = useRef<HTMLFormElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const notice = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    const current = form.current;
    input.current?.focus();
    return () => {
      controller.current?.abort();
      current?.reset();
      onWriting(false);
    };
  }, [onWriting]);
  useEffect(() => {
    if (message) notice.current?.focus();
  }, [message]);
  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (operation.current) return;
    const fields = new FormData(event.currentTarget);
    const password = String(fields.get("password") ?? "");
    event.currentTarget.reset();
    fields.delete("password");
    if (Array.from(password).length < 1 || Array.from(password).length > 128) {
      setMessage("Escribe tu contraseña actual, de hasta 128 caracteres.");
      return;
    }
    operation.current = true;
    const current = new AbortController();
    controller.current = current;
    const started = performance.now();
    setBusy(true);
    setMessage("");
    onWriting(true);
    try {
      await accessApi.reauthenticate(session.membership_id, password, {
        signal: current.signal,
      });
      if (current.signal.aborted) return;
      onChanged();
      const context = await accessApi.session({ signal: current.signal });
      if (
        context.user_id !== session.user_id ||
        context.tenant_id !== session.tenant_id ||
        context.membership_id !== session.membership_id ||
        context.role !== "admin"
      )
        throw new Error("Changed context");
      // Conservative UX timeout only. The server decides freshness in every write.
      const remaining = 270_000 - (performance.now() - started);
      if (remaining <= 0) throw new Error("Expired confirmation");
      if (!current.signal.aborted) {
        onWriting(false);
        onConfirmed(remaining);
      }
    } catch (error) {
      if (current.signal.aborted) return;
      onProblem(
        error instanceof ApiError && error.status === 401
          ? "No pudimos confirmar tu identidad. Comprueba el acceso o vuelve al inicio para iniciar sesión."
          : "No pudimos confirmar el resultado. Comprueba el acceso antes de continuar. Usa un navegador actualizado en HTTPS o localhost.",
      );
    } finally {
      operation.current = false;
      onWriting(false);
      if (!current.signal.aborted) setBusy(false);
    }
  }
  return (
    <section
      className="membership-review"
      aria-label="Confirmación de identidad"
      aria-busy={busy}
    >
      <p className="panel-step">PROTECCIÓN DE ACCESOS</p>
      <h2>Confirma tu identidad</h2>
      <p id="identity-help" className="field-help">
        Para administrar accesos, escribe tu contraseña actual. La confirmación
        dura como máximo cinco minutos y no prolonga tu sesión.
      </p>
      {message && (
        <p className="access-message" role="alert" ref={notice} tabIndex={-1}>
          {message}
        </p>
      )}
      {busy && <p role="status">Confirmando identidad…</p>}
      <form
        ref={form}
        onSubmit={(event) => void confirm(event)}
        method="post"
        action="/membresias"
      >
        <fieldset disabled={busy} className="membership-fields">
          <legend>Identidad del administrador</legend>
          <label htmlFor="identity-password">Tu contraseña actual</label>
          <input
            ref={input}
            id="identity-password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            aria-describedby="identity-help"
          />
          <button className="action" type="submit">
            Confirmar identidad
          </button>
        </fieldset>
      </form>
    </section>
  );
}
