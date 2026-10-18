"use client";
import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { identityApi } from "@/lib/access/identity";
export default function PasswordRecovery() {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [resetMode, setResetMode] = useState(false);
  const [token, setToken] = useState("");
  const form = useRef<HTMLFormElement>(null);
  const running = useRef(false);
  useEffect(() => {
    const mountedForm = form.current;
    const match = /^#reset=([A-Za-z0-9_-]{43})$/.exec(location.hash);
    history.replaceState(null, "", location.pathname);
    if (match) {
      // Synchronize the one-time external URL fragment after hydration.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setToken(match[1]);
      setResetMode(true);
    }
    const hide = () => {
      if (document.visibilityState === "hidden") {
        form.current?.reset();
        setToken("");
        setMessage(
          "Por seguridad, vuelve a abrir el enlace si necesitas continuar.",
        );
      }
    };
    document.addEventListener("visibilitychange", hide);
    return () => {
      setToken("");
      mountedForm?.reset();
      document.removeEventListener("visibilitychange", hide);
    };
  }, []);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (running.current) return;
    const fields = new FormData(e.currentTarget);
    e.currentTarget.reset();
    const email = String(fields.get("email") ?? "");
    const password = String(fields.get("password") ?? "");
    const confirmation = String(fields.get("confirmation") ?? "");
    const code = token || String(fields.get("token") ?? "").trim();
    if (
      resetMode &&
      (password !== confirmation ||
        Array.from(password).length < 15 ||
        Array.from(password).length > 128)
    ) {
      setMessage(
        "Las contraseñas deben coincidir y tener entre 15 y 128 caracteres.",
      );
      return;
    }
    running.current = true;
    setBusy(true);
    try {
      if (resetMode) {
        await identityApi.reset(code, password);
        setToken("");
        setMessage(
          "Contraseña cambiada. Se cerraron las sesiones anteriores. Inicia sesión con tu autenticador o conserva tus códigos de recuperación.",
        );
      } else {
        await identityApi.resetRequest(email);
        setMessage(
          "Si la cuenta tiene un correo verificado y puede recibir mensajes, enviaremos las instrucciones. Revisa tu bandeja y espera antes de solicitar otra vez.",
        );
      }
    } catch {
      setMessage(
        "No pudimos confirmar la operación. El enlace puede haber caducado o ya estar utilizado. No se inicia sesión automáticamente.",
      );
    } finally {
      running.current = false;
      setBusy(false);
    }
  }
  return (
    <section className="access-panel identity-panel" aria-busy={busy}>
      <h1>Recuperar contraseña</h1>
      <p>No se desactiva el autenticador ni se reemplazan los códigos MFA.</p>
      {message && <p role="status">{message}</p>}
      <form ref={form} onSubmit={submit} method="post" action="/recuperar">
        <fieldset disabled={busy}>
          {!resetMode ? (
            <>
              <label htmlFor="recovery-email">Correo de tu cuenta</label>
              <input
                id="recovery-email"
                name="email"
                type="email"
                autoComplete="username"
                required
                maxLength={254}
              />
            </>
          ) : (
            <>
              {!token && (
                <>
                  <label htmlFor="reset-token">Código del mensaje</label>
                  <input
                    id="reset-token"
                    name="token"
                    required
                    autoComplete="off"
                    maxLength={43}
                  />
                </>
              )}
              <label htmlFor="new-password">Contraseña nueva</label>
              <input
                id="new-password"
                name="password"
                type="password"
                autoComplete="new-password"
                required
                minLength={15}
                maxLength={128}
              />
              <label htmlFor="new-confirmation">Repite la contraseña</label>
              <input
                id="new-confirmation"
                name="confirmation"
                type="password"
                autoComplete="new-password"
                required
                minLength={15}
                maxLength={128}
              />
            </>
          )}
          <button type="submit">
            {resetMode ? "Cambiar contraseña" : "Solicitar instrucciones"}
          </button>
        </fieldset>
      </form>
      <button
        type="button"
        disabled={busy}
        onClick={() => {
          setToken("");
          form.current?.reset();
          setResetMode(!resetMode);
          setMessage("");
        }}
      >
        {resetMode
          ? "Solicitar un enlace nuevo"
          : "Tengo un código de recuperación de contraseña"}
      </button>
      <p>
        <Link href="/">Volver al acceso</Link>
      </p>
    </section>
  );
}
