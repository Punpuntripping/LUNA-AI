"use client";

import { useEffect, useState } from "react";
import { getApiBase } from "@/lib/api";

type Probe =
  | { state: "loading" }
  | { state: "ok"; env: string; service: string | null; version: string | null }
  | { state: "error"; message: string };

function classify(base: string): { kind: "local" | "railway" | "other"; host: string } {
  try {
    const host = new URL(base).host;
    if (host.startsWith("localhost") || host.startsWith("127.0.0.1")) {
      return { kind: "local", host };
    }
    if (host.endsWith(".up.railway.app") || host.endsWith(".railway.app")) {
      return { kind: "railway", host };
    }
    return { kind: "other", host };
  } catch {
    return { kind: "other", host: base };
  }
}

/**
 * Tiny corner pill showing which backend the browser is wired to and what
 * environment that backend reports. Visible only when:
 *   - NEXT_PUBLIC_API_URL points at localhost (dev), OR
 *   - NEXT_PUBLIC_SHOW_API_BADGE=1 is set at build time (debugging Railway)
 *
 * Renders nothing in normal prod, so end users never see it.
 */
export function ApiEnvBadge() {
  const base = getApiBase();
  const meta = classify(base);
  const forceShow = process.env.NEXT_PUBLIC_SHOW_API_BADGE === "1";
  const shouldRender = meta.kind === "local" || forceShow;

  const [probe, setProbe] = useState<Probe>({ state: "loading" });

  useEffect(() => {
    if (!shouldRender) return;
    let cancelled = false;
    fetch(`${base}/api/v1/health`, { credentials: "omit" })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const body = await r.json();
        if (cancelled) return;
        setProbe({
          state: "ok",
          env: body.environment ?? "unknown",
          service: body.service ?? null,
          version: body.version ?? null,
        });
      })
      .catch((e) => {
        if (cancelled) return;
        setProbe({ state: "error", message: String(e?.message ?? e) });
      });
    return () => {
      cancelled = true;
    };
  }, [base, shouldRender]);

  if (!shouldRender) return null;

  const color =
    probe.state === "ok"
      ? probe.env === "production"
        ? "#5b8a5c" // green
        : "#9b5c8a" // luna purple — dev
      : probe.state === "error"
      ? "#b04a4a" // red
      : "#8a8a8a"; // grey

  const label =
    probe.state === "ok"
      ? `${meta.kind}:${probe.env}`
      : probe.state === "error"
      ? `${meta.kind}:unreachable`
      : `${meta.kind}:…`;

  const title =
    probe.state === "ok"
      ? `API: ${base}\nbackend env: ${probe.env}\nservice: ${probe.service ?? "?"} v${probe.version ?? "?"}`
      : probe.state === "error"
      ? `API: ${base}\nhealth check failed: ${probe.message}`
      : `API: ${base}\nchecking…`;

  return (
    <div
      title={title}
      dir="ltr"
      style={{
        position: "fixed",
        bottom: 8,
        right: 8,
        zIndex: 9999,
        background: color,
        color: "white",
        font: "500 11px/1 ui-monospace, SFMono-Regular, Menlo, monospace",
        padding: "4px 8px",
        borderRadius: 999,
        opacity: 0.85,
        pointerEvents: "auto",
        userSelect: "none",
      }}
    >
      {label}
    </div>
  );
}
