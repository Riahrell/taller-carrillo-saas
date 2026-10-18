"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { accessApi, type SessionContext } from "@/lib/access/api";
import CatalogManager, { uncertainCatalog } from "@/components/catalog-manager";

type View =
  | { kind: "checking" }
  | { kind: "active"; session: SessionContext }
  | { kind: "blocked"; message: string };
export default function CatalogWorkspace() {
  const [view, setView] = useState<View>({ kind: "checking" });
  const controller = useRef<AbortController | null>(null);
  const writing = useRef(false);
  const channel = useRef<BroadcastChannel | null>(null);
  const notice = useRef<HTMLParagraphElement>(null);
  const problem = useCallback((message: string) => {
    controller.current?.abort();
    setView({ kind: "blocked", message });
  }, []);
  const check = useCallback(() => {
    controller.current?.abort();
    if (writing.current) {
      problem(uncertainCatalog);
      return;
    }
    const current = new AbortController();
    controller.current = current;
    setView({ kind: "checking" });
    void accessApi
      .session({ signal: current.signal })
      .then((session) => {
        if (!current.signal.aborted)
          setView(
            session.role === "admin"
              ? { kind: "active", session }
              : {
                  kind: "blocked",
                  message: "Tu perfil no permite administrar el catálogo.",
                },
          );
      })
      .catch(() => {
        if (!current.signal.aborted)
          problem(
            "No pudimos comprobar tu sesión. Vuelve al inicio o comprueba el acceso para continuar.",
          );
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
      if (document.visibilityState === "hidden")
        problem(
          writing.current
            ? uncertainCatalog
            : "Comprueba el acceso al volver para continuar.",
        );
      else check();
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
      <p className="eyebrow">PRODUCTOS Y CATEGORÍAS</p>
      <h1>Catálogo</h1>
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
      {view.kind === "active" && (
        <>
          <p className="membership-company">
            {view.session.tenant_name} <span>· Administrador</span>
          </p>
          <CatalogManager
            key={view.session.membership_id}
            session={view.session}
            onProblem={problem}
            onWriting={onWriting}
            onChanged={announce}
          />
        </>
      )}
    </main>
  );
}
