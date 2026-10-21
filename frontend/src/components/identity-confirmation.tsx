"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { accessApi, type SessionContext } from "@/lib/access/api";
import { sameContext } from "@/lib/access/same-context";
import { identityApi } from "@/lib/access/identity";
import { ApiError } from "@/lib/api/client";

type Props = {
  purpose?: string;
  session: SessionContext;
  onWriting: (value: boolean) => void;
  onChanged: () => void;
  onProblem: (message: string) => void;
  onConfirmed: (remaining: number) => void;
};

export default function IdentityConfirmation({
  purpose = "administrar accesos",
  session,
  onWriting,
  onChanged,
  onProblem,
  onConfirmed,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [mfa, setMfa] = useState(false);
  const [checking, setChecking] = useState(true);
  const [showPassword, setShowPassword] = useState(false);
  const callbacks = useRef({ onConfirmed, onProblem });
  useEffect(() => {
    callbacks.current = { onConfirmed, onProblem };
  }, [onConfirmed, onProblem]);
  useEffect(() => {
    let active = true;
    const started = performance.now();
    void identityApi
      .status()
      .then((value) => {
        if (!active) return;
        if (value.confirmation_context) {
          if (!sameContext(session, value.confirmation_context))
            throw new Error("Changed session");
          const remaining =
            (value.confirmation_seconds ?? 0) * 1000 -
            (performance.now() - started) -
            1000;
          if (remaining <= 0) throw new Error("Expired session");
          callbacks.current.onConfirmed(remaining);
          return;
        }
        setMfa(value.mfa_enabled);
        setChecking(false);
      })
      .catch(() => {
        if (active)
          callbacks.current.onProblem(
            "No pudimos comprobar tu sesión. Vuelve a iniciar sesión para continuar.",
          );
      });
    return () => {
      active = false;
    };
  }, [session]);
  const [message, setMessage] = useState("");
  const operation = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const form = useRef<HTMLFormElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const notice = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    const hide = () => {
      if (document.visibilityState !== "hidden") return;
      setShowPassword(false);
      form.current
        ?.querySelectorAll<HTMLInputElement>(
          'input[name="password"], input[autocomplete="one-time-code"]',
        )
        .forEach((field) => {
          field.value = "";
        });
    };
    document.addEventListener("visibilitychange", hide);
    return () => document.removeEventListener("visibilitychange", hide);
  }, []);

  useEffect(() => {
    const current = form.current;
    return () => {
      controller.current?.abort();
      current?.reset();
      onWriting(false);
    };
  }, [onWriting]);
  useEffect(() => {
    if (!checking) input.current?.focus({ preventScroll: true });
  }, [checking]);
  useEffect(() => {
    if (message) notice.current?.focus({ preventScroll: true });
  }, [message]);
  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (operation.current) return;
    const fields = new FormData(event.currentTarget);
    const password = String(fields.get("password") ?? "");
    const code = String(fields.get("code") ?? "");
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
      if (mfa) {
        await identityApi.reauthenticate(session.membership_id, password, code);
      } else {
        await accessApi.reauthenticate(session.membership_id, password, {
          signal: current.signal,
        });
      }
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
  if (checking)
    return (
      <section
        className="membership-notice"
        aria-label="Comprobación de sesión"
      >
        <p role="status">Comprobando tu sesión…</p>
      </section>
    );
  return (
    <section
      className="membership-review identity-confirmation"
      aria-label="Confirmación de identidad"
      aria-busy={busy}
    >
      <p className="panel-step">PROTECCIÓN DE ACCESOS</p>
      <h2>Confirma tu identidad</h2>
      <p id="identity-help" className="field-help">
        Para {purpose}, escribe tu contraseña actual. La confirmación dura como
        máximo cinco minutos y no prolonga tu sesión.
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
          <div className="password-field">
            <input
              ref={input}
              id="identity-password"
              name="password"
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              required
              aria-describedby="identity-help"
            />
            <button
              type="button"
              className="password-toggle"
              aria-controls="identity-password"
              aria-pressed={showPassword}
              aria-label={
                showPassword ? "Ocultar contraseña" : "Mostrar contraseña"
              }
              onClick={() => setShowPassword((value) => !value)}
            >
              {showPassword ? "Ocultar" : "Mostrar"}
            </button>
          </div>
          {mfa && (
            <>
              <label htmlFor="identity-code">Código del autenticador</label>
              <input
                id="identity-code"
                name="code"
                autoComplete="one-time-code"
                required
                maxLength={128}
              />
            </>
          )}
          <button className="action" type="submit">
            Confirmar identidad
          </button>
        </fieldset>
      </form>
    </section>
  );
}
