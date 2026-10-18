"use client";
import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { accessApi } from "@/lib/access/api";
import { identityApi } from "@/lib/access/identity";
export default function MailboxConfirmation() {
  const [token, setToken] = useState("");
  const form = useRef<HTMLFormElement>(null);
  const [enrollment, setEnrollment] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const mountedForm = form.current;
    const match = /^#(verify|enrollment)=([A-Za-z0-9_-]{43})$/.exec(
      location.hash,
    );
    history.replaceState(null, "", location.pathname);
    if (match) {
      // Synchronize the one-time external URL fragment after hydration.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setToken(match[2]);
      setEnrollment(match[1] === "enrollment");
    }
    const hide = () => {
      if (document.visibilityState === "hidden") {
        setToken("");
        form.current?.reset();
        setMessage("Vuelve a abrir el enlace para continuar.");
      }
    };
    document.addEventListener("visibilitychange", hide);
    return () => {
      mountedForm?.reset();
      document.removeEventListener("visibilitychange", hide);
    };
  }, []);
  async function confirm(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (busy) return;
    const value =
      token || String(new FormData(e.currentTarget).get("token") ?? "");
    e.currentTarget.reset();
    setBusy(true);
    try {
      const context = await accessApi.session();
      await identityApi.verify(value, context.membership_id);
      setToken("");
      setMessage("Correo verificado. Ya puedes volver a tu cuenta.");
    } catch {
      setMessage(
        "No pudimos verificar el correo. Debes usar la sesión que solicitó el mensaje y un código vigente.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="access-panel">
      <h1>Verificar correo</h1>
      {enrollment ? (
        <>
          <p>
            Vuelve a la pantalla de incorporación en el navegador que solicitó
            el mensaje y copia allí este código. Abrir este enlace no confirma
            el buzón ni activa un factor.
          </p>
          <code>{token}</code>
        </>
      ) : (
        <form ref={form} onSubmit={confirm} method="post" action="/verificar">
          <label htmlFor="mail-token">Código del mensaje</label>
          <input
            id="mail-token"
            name="token"
            autoComplete="off"
            maxLength={43}
            required={!token}
          />
          <button disabled={busy} type="submit">
            Confirmar correo
          </button>
        </form>
      )}
      {message && <p role="status">{message}</p>}
      <Link href="/">Volver al acceso</Link>
    </section>
  );
}
