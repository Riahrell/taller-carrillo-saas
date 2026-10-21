"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { identityApi } from "@/lib/access/identity";
import type { LoginResult } from "@/lib/access/api";
import { ApiError } from "@/lib/api/client";

export default function EmailLoginFlow({
  onLogin,
  onCancel,
}: {
  onLogin: (value: LoginResult) => void;
  onCancel: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [remaining, setRemaining] = useState(0);
  const [requested, setRequested] = useState(false);
  const deadline = useRef(0);
  const running = useRef(false);
  const mounted = useRef(true);
  const form = useRef<HTMLFormElement>(null);
  useEffect(() => {
    mounted.current = true;
    const update = () =>
      setRemaining(
        Math.max(0, Math.ceil((deadline.current - performance.now()) / 1000)),
      );
    const hide = () => {
      if (document.visibilityState === "hidden") form.current?.reset();
      else update();
    };
    const timer = setInterval(update, 1000);
    document.addEventListener("visibilitychange", hide);
    return () => {
      mounted.current = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", hide);
    };
  }, []);
  async function send() {
    if (running.current || performance.now() < deadline.current) return;
    running.current = true;
    setBusy(true);
    setMessage("");
    deadline.current = performance.now() + 60_000;
    setRemaining(60);
    try {
      await identityApi.requestLoginEmail();
      if (mounted.current) {
        setRequested(true);
        setMessage(
          "Mensaje solicitado. Revisa tu bandeja y spam. Solo es válido el último código enviado; caduca diez minutos después de iniciar este acceso.",
        );
      }
    } catch (error) {
      if (mounted.current)
        setMessage(
          error instanceof ApiError && error.status === 429
            ? "No se pudo solicitar otro correo. Espera al menos 60 segundos entre envíos. El límite es de 3 mensajes por cuenta cada 15 minutos. Si el intento venció, cancela y vuelve a iniciar sesión."
            : "No pudimos confirmar el envío. Espera antes de solicitar otro mensaje; si persiste, cancela y vuelve a iniciar sesión.",
        );
    } finally {
      running.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (running.current) return;
    const code = String(new FormData(event.currentTarget).get("code") ?? "")
      .trim()
      .toUpperCase();
    event.currentTarget.reset();
    if (!/^[A-Z0-9]{6}$/.test(code)) {
      setMessage(
        "Escribe los 6 caracteres del correo. Puedes usar mayúsculas o minúsculas.",
      );
      return;
    }
    running.current = true;
    setBusy(true);
    setMessage("");
    try {
      const result = await identityApi.confirmLoginEmail(code);
      if (mounted.current) onLogin(result);
    } catch {
      if (mounted.current)
        setMessage(
          "No pudimos verificar el código. Usa el último correo, comprueba sus 6 caracteres y su vigencia. Hay un máximo de 5 intentos; si el acceso venció, cancela y vuelve a iniciar sesión.",
        );
    } finally {
      running.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  return (
    <section aria-label="Verificación por correo" aria-busy={busy}>
      <h3>Verifica tu inicio de sesión</h3>
      <p>
        Introduce los 6 caracteres que recibas por correo. Recordaremos este
        navegador durante 15 días; seguirás usando tu contraseña en cada acceso.
      </p>
      <p>
        El código caduca diez minutos después de iniciar el acceso. Espera 60
        segundos entre envíos; máximo 3 mensajes por cuenta cada 15 minutos.
      </p>
      {message && <p role="status">{message}</p>}
      <button
        type="button"
        disabled={busy || remaining > 0}
        onClick={() => void send()}
      >
        {remaining > 0
          ? `Espera ${remaining} s para solicitar otro correo`
          : requested
            ? "Solicitar otro código"
            : "Enviar código por correo"}
      </button>
      <form
        ref={form}
        onSubmit={(event) => void confirm(event)}
        method="post"
        action="/"
      >
        <fieldset disabled={busy}>
          <label htmlFor="email-login-code">Código del correo</label>
          <input
            id="email-login-code"
            name="code"
            type="text"
            inputMode="text"
            autoCapitalize="characters"
            pattern="[A-Za-z0-9]{6}"
            minLength={6}
            maxLength={6}
            autoComplete="one-time-code"
            spellCheck={false}
            required
          />
          <button type="submit">Verificar código</button>
        </fieldset>
      </form>
      <button type="button" disabled={busy} onClick={onCancel}>
        Cancelar y cerrar acceso pendiente
      </button>
    </section>
  );
}
