"use client";

import Link from "next/link";
import { sameContext } from "@/lib/access/same-context";
import { useCallback, useEffect, useRef, useState } from "react";
import { accessApi, type SessionContext } from "@/lib/access/api";
import type { ScopedImagePending } from "@/components/product-image";
import InventoryManager, {
  uncertainInventory,
  sameInventorySession,
  type ScopedPending,
} from "@/components/inventory-manager";

type View =
  | { kind: "checking" }
  | {
      kind: "active";
      session: SessionContext;
      pending: ScopedPending | null;
      imagePending: ScopedImagePending | null;
    }
  | { kind: "blocked"; message: string };
export default function InventoryWorkspace() {
  const [view, setView] = useState<View>({ kind: "checking" });
  const [paused, setPaused] = useState(true);
  const controller = useRef<AbortController | null>(null);
  const writing = useRef(false);
  const pending = useRef<ScopedPending | null>(null);
  const imagePending = useRef<ScopedImagePending | null>(null);
  const rememberImage = useCallback((value: ScopedImagePending | null) => {
    imagePending.current = value;
  }, []);
  const remember = useCallback((value: ScopedPending | null) => {
    pending.current = value;
  }, []);
  const channel = useRef<BroadcastChannel | null>(null);
  const notice = useRef<HTMLParagraphElement>(null);
  const problem = useCallback((message: string) => {
    controller.current?.abort();
    setView({ kind: "blocked", message });
  }, []);
  const check = useCallback(() => {
    controller.current?.abort();
    if (writing.current) {
      problem(uncertainInventory);
      return;
    }
    const current = new AbortController();
    controller.current = current;
    setPaused(true);
    setView((previous) =>
      previous.kind === "active" ? previous : { kind: "checking" },
    );
    void accessApi
      .session({ signal: current.signal })
      .then((session) => {
        if (!current.signal.aborted)
          setView((previous) =>
            previous.kind === "active" &&
            !sameContext(previous.session, session)
              ? {
                  kind: "blocked",
                  message:
                    "La sesión cambió. Recupera la sesión original antes de continuar.",
                }
              : previous.kind === "active" && session.role === "admin"
                ? previous
                : session.role === "admin" &&
                    (!pending.current ||
                      sameInventorySession(session, pending.current.session)) &&
                    (!imagePending.current ||
                      sameInventorySession(
                        session,
                        imagePending.current.session,
                      ))
                  ? {
                      kind: "active",
                      session,
                      pending: pending.current,
                      imagePending: imagePending.current,
                    }
                  : {
                      kind: "blocked",
                      message: pending.current
                        ? "Hay un conteo pendiente de resolver en la sesión original. Vuelve al inicio para recuperar el acceso de esa empresa."
                        : imagePending.current
                          ? "Hay una imagen pendiente en la sesión original. Recupera el acceso de esa empresa."
                          : "Tu perfil no permite consultar el inventario.",
                    },
          );
      })
      .catch(() => {
        if (!current.signal.aborted)
          problem(
            "No pudimos comprobar tu sesión. Vuelve al inicio o comprueba el acceso para continuar.",
          );
      })
      .finally(() => {
        if (!current.signal.aborted)
          setPaused(document.visibilityState === "hidden");
      });
  }, [problem]);
  const onWriting = useCallback((value: boolean) => {
    writing.current = value;
  }, []);
  const announce = useCallback(
    () => channel.current?.postMessage("changed"),
    [],
  );
  useEffect(() => {
    check();
    const focus = () => {
      if (document.visibilityState === "visible") check();
    };
    const visibility = () => {
      if (document.visibilityState === "hidden") {
        controller.current?.abort();
        setPaused(true);
        if (writing.current) problem(uncertainInventory);
      } else check();
    };
    window.addEventListener("focus", focus);
    window.addEventListener("pageshow", focus);
    document.addEventListener("visibilitychange", visibility);
    if (typeof BroadcastChannel !== "undefined") {
      channel.current = new BroadcastChannel("carrillo_access");
      channel.current.onmessage = (event: MessageEvent<unknown>) => {
        if (event.data === "changed") check();
      };
    }
    return () => {
      controller.current?.abort();
      channel.current?.close();
      window.removeEventListener("focus", focus);
      window.removeEventListener("pageshow", focus);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [check, problem]);
  useEffect(() => {
    if (view.kind === "blocked") notice.current?.focus();
  }, [view]);
  return (
    <main id="contenido" className="memberships-workspace" tabIndex={-1}>
      <nav aria-label="Navegación">
        <Link href="/" className="text-action">
          ← Volver al inicio
        </Link>
      </nav>
      <p className="eyebrow">EXISTENCIAS Y VALORACIÓN</p>
      <h1>Inventario</h1>
      {view.kind === "checking" && <p role="status">Comprobando acceso…</p>}
      {view.kind === "blocked" && (
        <section className="membership-notice">
          <p role="alert" tabIndex={-1} ref={notice}>
            {view.message}
          </p>
          <button className="action" onClick={check}>
            Comprobar acceso
          </button>
        </section>
      )}
      {view.kind === "active" && paused && (
        <p role="status">Comprobando acceso…</p>
      )}
      {view.kind === "active" && (
        <div hidden={paused} inert={paused}>
          <p className="membership-company">
            {view.session.tenant_name} <span>· Administrador</span>
          </p>
          <InventoryManager
            key={view.session.membership_id}
            session={view.session}
            onProblem={problem}
            onWriting={onWriting}
            onChanged={announce}
            initialPending={view.pending}
            onPending={remember}
            initialImagePending={view.imagePending}
            onImagePending={rememberImage}
          />
        </div>
      )}
    </main>
  );
}
