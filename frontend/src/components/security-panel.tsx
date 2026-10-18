"use client";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { accessApi, type SessionContext } from "@/lib/access/api";
import {
  identityApi,
  ownSessions,
  ownEvents,
  type OwnSession,
  type OwnEvent,
  type SecurityState,
} from "@/lib/access/identity";
export default function SecurityPanel() {
  const [context, setContext] = useState<SessionContext | null>(null);
  const [sessions, setSessions] = useState<OwnSession[]>([]);
  const [events, setEvents] = useState<OwnEvent[]>([]);
  const [security, setSecurity] = useState<SecurityState | null>(null);
  const [message, setMessage] = useState("");
  const [codes, setCodes] = useState<string[]>([]);
  const [rotation, setRotation] = useState<{ seed: string; qr: string } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const epoch = useRef(0);
  const running = useRef(false);
  const form = useRef<HTMLFormElement>(null);
  const load = useCallback(async () => {
    try {
      const current = await accessApi.session();
      const state = await identityApi.status();
      const [sessions, events] = await Promise.all([
        ownSessions(),
        ownEvents(),
      ]);
      setSessions(sessions);
      setEvents(events);
      setContext(current);
      setSecurity(state);
    } catch {
      setContext(null);
      setSecurity(null);
      setMessage("Necesitas una sesión vigente para gestionar tu cuenta.");
    }
  }, []);
  useEffect(() => {
    const mountedForm = form.current;
    // Fetch the external session state when this account view mounts.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    const onVisibility = () => {
      epoch.current += 1;
      form.current?.reset();
      setCodes([]);
      setRotation(null);
      if (document.visibilityState === "visible" && !running.current)
        void load();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      mountedForm?.reset();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [load]);
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
        "No pudimos confirmar la operación. Revisa tu sesión, la vigencia de la confirmación y el código. No se reintenta automáticamente.",
      );
    } finally {
      running.current = false;
      setBusy(false);
    }
  }
  function confirm(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!context) return;
    const data = new FormData(e.currentTarget);
    e.currentTarget.reset();
    const password = String(data.get("password") ?? "");
    const code = String(data.get("totp") ?? "");
    void run(async () => {
      await identityApi.reauthenticate(context.membership_id, password, code);
      setMessage(
        "Identidad confirmada por cinco minutos desde la primera comprobación.",
      );
      await load();
    });
  }
  return (
    <section className="access-panel identity-panel" aria-busy={busy}>
      <h1>Seguridad de mi cuenta</h1>
      {message && <p role="status">{message}</p>}
      {context && security && (
        <>
          <p>
            Autenticador: {security.mfa_enabled ? "activo" : "sin configurar"}.
            Correo:{" "}
            {security.mailbox_verified
              ? "verificado"
              : "pendiente de verificar"}
            .
          </p>
          {!security.mailbox_verified && (
            <button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await identityApi.verifyRequest(context.membership_id);
                  setMessage(
                    "Mensaje solicitado. Usa este mismo navegador para confirmarlo.",
                  );
                })
              }
            >
              Verificar mi correo
            </button>
          )}
          {security.mfa_enabled && (
            <>
              <h2>Confirmar identidad</h2>
              <form
                ref={form}
                onSubmit={confirm}
                method="post"
                action="/seguridad"
              >
                <fieldset disabled={busy}>
                  <label htmlFor="security-password">Contraseña actual</label>
                  <input
                    id="security-password"
                    name="password"
                    type="password"
                    autoComplete="current-password"
                    required
                    maxLength={128}
                  />
                  <label htmlFor="security-code">Código del autenticador</label>
                  <input
                    id="security-code"
                    name="totp"
                    type="text"
                    inputMode="numeric"
                    pattern="[0-9]{6}"
                    autoComplete="one-time-code"
                    spellCheck={false}
                    required
                    minLength={6}
                    maxLength={6}
                  />
                  <button type="submit">Confirmar identidad</button>
                </fieldset>
              </form>
              <h2>Cambiar autenticador</h2>
              <p>
                El factor anterior se conserva hasta confirmar el nuevo.
                Necesitas una confirmación reciente de identidad.
              </p>
              <button
                disabled={busy}
                onClick={() =>
                  void run(async (current) => {
                    const setup = await identityApi.rotationBegin(
                      context.membership_id,
                    );
                    if (!current()) return;
                    setRotation({ seed: setup.seed, qr: setup.qr });
                  })
                }
              >
                Configurar autenticador nuevo
              </button>
              {rotation && (
                <div>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={rotation.qr}
                    width={240}
                    height={240}
                    alt="QR para el nuevo autenticador"
                  />
                  <code>{rotation.seed}</code>
                  <form
                    method="post"
                    action="/seguridad"
                    onSubmit={(e) => {
                      e.preventDefault();
                      const code = String(
                        new FormData(e.currentTarget).get("totp") ?? "",
                      );
                      e.currentTarget.reset();
                      void run(async (current) => {
                        const result = await identityApi.rotationActivate(
                          context.membership_id,
                          code,
                        );
                        if (!current()) return;
                        setRotation(null);
                        setCodes(result.codes);
                        setContext(null);
                        setMessage(
                          "Autenticador cambiado. Guarda los códigos nuevos y vuelve a iniciar sesión.",
                        );
                      });
                    }}
                  >
                    <label htmlFor="rotation-code">
                      Código del autenticador nuevo
                    </label>
                    <input
                      id="rotation-code"
                      name="totp"
                      type="text"
                      inputMode="numeric"
                      pattern="[0-9]{6}"
                      autoComplete="one-time-code"
                      spellCheck={false}
                      minLength={6}
                      maxLength={6}
                      required
                    />
                    <button type="submit" disabled={busy}>
                      Confirmar cambio y cerrar sesiones
                    </button>
                  </form>
                </div>
              )}
              <h2>Códigos de recuperación</h2>
              <p>
                Regenerar invalida los códigos anteriores y cierra todas las
                sesiones. Requiere contraseña y TOTP recientes.
              </p>
              <button
                disabled={busy}
                onClick={() =>
                  void run(async (current) => {
                    const result = await identityApi.codes(
                      context.membership_id,
                    );
                    if (!current()) return;
                    setCodes(result.codes);
                    setContext(null);
                    setMessage(
                      "Guarda estos diez códigos fuera del sistema. No volverán a mostrarse. Vuelve a iniciar sesión.",
                    );
                  })
                }
              >
                Regenerar códigos y cerrar sesiones
              </button>
            </>
          )}
          <h2>Sesiones</h2>
          <ul>
            {sessions.map((item) => (
              <li key={item.session_id}>
                {item.current ? "Esta sesión" : "Otra sesión"}: último uso{" "}
                {new Date(item.last_seen_at).toLocaleString("es")}; vence{" "}
                {new Date(item.expires_at).toLocaleString("es")}.
              </li>
            ))}
          </ul>
          <button
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await identityApi.revokeAll(context.membership_id);
                form.current?.reset();
                setRotation(null);
                setCodes([]);
                setContext(null);
                setSecurity(null);
                setMessage("Se cerraron todas tus sesiones.");
              })
            }
          >
            Cerrar todas mis sesiones
          </button>
          <h2>Actividad de seguridad reciente</h2>
          <ul>
            {events.slice(0, 10).map((item, index) => (
              <li key={`${item.occurred_at}-${index}`}>
                {(
                  {
                    login_succeeded: "Inicio de sesión",
                    login_failed: "Intento de acceso rechazado",
                    identity_reauthenticated: "Identidad confirmada",
                    mfa_enrolled: "Autenticador activado",
                    mfa_replaced: "Autenticador recuperado",
                    mfa_rotated: "Autenticador cambiado",
                    mfa_codes_regenerated: "Códigos regenerados",
                    password_reset_completed: "Contraseña restablecida",
                    mailbox_verified: "Correo verificado",
                    security_revision_advanced:
                      "Accesos anteriores invalidados",
                    mfa_totp_consumed: "Código de autenticador verificado",
                    mfa_recovery_consumed: "Código de recuperación utilizado",
                    mfa_enrollment_email_confirmed:
                      "Buzón de incorporación confirmado",
                    mfa_recovery_started:
                      "Recuperación del autenticador iniciada",
                    mfa_seed_rewrapped: "Protección del autenticador renovada",
                  } as Record<string, string>
                )[item.action] ?? "Actividad de seguridad"}
                : {new Date(item.occurred_at).toLocaleString("es")}
              </li>
            ))}
          </ul>
        </>
      )}
      {codes.length > 0 && (
        <>
          <ol>
            {codes.map((code) => (
              <li key={code}>
                <code>{code}</code>
              </li>
            ))}
          </ol>
          <button onClick={() => setCodes([])}>Ya guardé mis códigos</button>
        </>
      )}
      <p>
        <Link href="/">Volver al acceso</Link>
      </p>
    </section>
  );
}
