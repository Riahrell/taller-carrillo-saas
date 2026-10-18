"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { identityApi } from "@/lib/access/identity";
import { type LoginResult } from "@/lib/access/api";

type Props = {
  initial: "mfa_totp" | "mfa_enrollment";
  onLogin: (result: LoginResult) => void;
  onCancel: () => void;
};
export default function MfaFlow({ initial, onLogin, onCancel }: Props) {
  const [stage, setStage] = useState<
    "totp" | "email" | "setup" | "codes" | "lost"
  >(initial === "mfa_totp" ? "totp" : "email");
  const [seed, setSeed] = useState("");
  const [qr, setQr] = useState("");
  const [codes, setCodes] = useState<string[]>([]);
  const [offline, setOffline] = useState(false);
  const [replacement, setReplacement] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const epoch = useRef(0);
  const running = useRef(false);
  const form = useRef<HTMLFormElement>(null);
  useEffect(() => {
    const mountedForm = form.current;
    const hide = () => {
      if (document.visibilityState === "hidden") {
        epoch.current += 1;
        form.current?.reset();
        setOffline(false);
        setSeed("");
        setQr("");
        setCodes([]);
        setStage((s) => (s === "setup" || s === "codes" ? "lost" : s));
      }
    };
    document.addEventListener("visibilitychange", hide);
    return () => {
      document.removeEventListener("visibilitychange", hide);
      mountedForm?.reset();
    };
  }, []);
  async function run(task: (current: () => boolean) => Promise<void>) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setMessage("");
    try {
      const serial = epoch.current;
      await task(
        () =>
          serial === epoch.current && document.visibilityState === "visible",
      );
    } catch {
      setMessage(
        "No pudimos confirmar la operación. Comprueba el código y su vigencia; si perdiste la respuesta, reinicia el acceso.",
      );
    } finally {
      running.current = false;
      setBusy(false);
    }
  }
  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    const code = String(data.get("code") ?? "").trim();
    const offline = data.get("offline") === "on";
    e.currentTarget.reset();
    setOffline(false);
    void run(async (current) => {
      if (stage === "totp") {
        if (offline) {
          const result = await identityApi.recover(code);
          if (!current()) {
            setStage("lost");
            return;
          }
          setSeed(result.seed);
          setQr(result.qr);
          setReplacement(true);
          setStage("setup");
        } else onLogin(await identityApi.totp(code));
      } else if (stage === "email") {
        const result = await identityApi.confirmEnrollment(code);
        if (!current()) {
          setStage("lost");
          return;
        }
        setSeed(result.seed);
        setQr(result.qr);
        setStage("setup");
      } else if (stage === "setup") {
        const result = await identityApi.activate(code, replacement);
        if (!current()) {
          setStage("lost");
          return;
        }
        setSeed("");
        setQr("");
        setCodes(result.codes);
        setStage("codes");
      }
    });
  }
  return (
    <div className="identity-flow" aria-busy={busy}>
      <h3>
        {stage === "totp"
          ? "Confirma tu autenticador"
          : stage === "email"
            ? "Verifica tu correo"
            : stage === "setup"
              ? "Configura tu autenticador"
              : stage === "codes"
                ? "Guarda tus códigos de recuperación"
                : "Reinicia el acceso"}
      </h3>
      {message && <p role="alert">{message}</p>}
      {stage === "email" && (
        <>
          <p>
            Solicita el mensaje y confirma el código del correo desde este
            navegador.
          </p>
          <button
            disabled={busy}
            type="button"
            onClick={() =>
              void run(async () => {
                await identityApi.requestEnrollment();
                setMessage(
                  "Mensaje solicitado. Revisa tu correo; el permiso caduca quince minutos después de introducir tu contraseña.",
                );
              })
            }
          >
            Enviar correo de verificación
          </button>
        </>
      )}
      {stage === "setup" && (
        <>
          <p>
            Agrega una cuenta TOTP en tu aplicación: seis dígitos, SHA-1,
            intervalo de 30 segundos. Introduce esta clave manualmente y
            confirma su primer código.
          </p>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={qr}
            width={240}
            height={240}
            alt="Código QR para configurar tu autenticador"
          />
          <code aria-label="Clave de configuración">{seed}</code>
          <p>No compartas esta clave. Se muestra una sola vez.</p>
        </>
      )}
      {stage === "codes" && (
        <>
          <p>
            Guárdalos fuera del sistema. Cada código se usa una sola vez y no
            volverá a mostrarse.
          </p>
          <ol>
            {codes.map((code) => (
              <li key={code}>
                <code>{code}</code>
              </li>
            ))}
          </ol>
          <button
            type="button"
            onClick={() => {
              setCodes([]);
              onCancel();
            }}
          >
            Ya los guardé; volver a iniciar sesión
          </button>
          <p>
            Espera al siguiente código del autenticador: el primero ya fue
            utilizado.
          </p>
        </>
      )}
      {stage === "lost" && (
        <p>
          La clave o los códigos se retiraron al ocultar la página. Si ya
          activaste el factor, inicia sesión y regenera los códigos; si no,
          reinicia la incorporación.
        </p>
      )}
      {(stage === "totp" || stage === "email" || stage === "setup") && (
        <form ref={form} onSubmit={submit} method="post" action="/">
          <fieldset disabled={busy}>
            <label htmlFor="factor-code">
              {stage === "email"
                ? "Código del correo"
                : "Código de verificación"}
            </label>
            <input
              id="factor-code"
              name="code"
              autoComplete="one-time-code"
              type="text"
              required
              maxLength={stage === "email" || offline ? 128 : 6}
              minLength={stage === "email" || offline ? undefined : 6}
              pattern={stage === "email" || offline ? undefined : "[0-9]{6}"}
              inputMode={stage === "email" || offline ? "text" : "numeric"}
              spellCheck={false}
            />
            {stage === "totp" && (
              <label>
                <input
                  type="checkbox"
                  name="offline"
                  checked={offline}
                  onChange={(e) => setOffline(e.target.checked)}
                />{" "}
                Perdí mi autenticador; usar un código de recuperación
              </label>
            )}
            <button type="submit">Confirmar</button>
          </fieldset>
        </form>
      )}
      {stage !== "codes" && (
        <button disabled={busy} type="button" onClick={onCancel}>
          Cancelar y cerrar acceso pendiente
        </button>
      )}
    </div>
  );
}
