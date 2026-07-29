"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

type WorkspaceStatus = {
  workspace: string;
  model_mode: string;
  index: { state: string; records: number };
  connectors: Array<{ name: string; state: string }>;
  run: { state: string };
};

const fallback: WorkspaceStatus = {
  workspace: "Highland",
  model_mode: "scripted",
  index: { state: "checking", records: 0 },
  connectors: [],
  run: { state: "idle" },
};

const navigation = [
  { label: "New chat", href: "/" },
  { label: "Search", href: "/search" },
  { label: "Artifacts", href: "/artifacts" },
  { label: "Agents", href: "/agents" },
  { label: "Automations", href: "/automations" },
];

export function WorkspaceShell({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState(fallback);
  const [connection, setConnection] = useState<"connecting" | "ready" | "offline">("connecting");
  const pathname = usePathname();

  useEffect(() => {
    const controller = new AbortController();
    fetch(
      `${process.env.NEXT_PUBLIC_HIGHLAND_API_URL ?? "http://127.0.0.1:8080"}/workspace/status`,
      { signal: controller.signal },
    )
      .then((response) => (response.ok ? response.json() : Promise.reject(response)))
      .then((payload: WorkspaceStatus) => {
        setStatus(payload);
        setConnection("ready");
      })
      .catch((error) => {
        if ((error as Error).name !== "AbortError") setConnection("offline");
      });
    return () => controller.abort();
  }, []);

  const connectorState = connection === "offline"
    ? "offline"
    : connection === "connecting"
      ? "checking"
      : status.connectors.length
        ? "configured"
        : "missing";
  const indexState = connection === "offline" ? "unavailable" : status.index.state;

  return (
    <div className="workspace-shell">
      <aside className="sidebar">
        <Link className="brand" href="/">
          <span className="brand-mark" aria-hidden="true">H</span>
          <span>{status.workspace}</span>
        </Link>
        <nav aria-label="Workspace">
          {navigation.map(({ label, href }) => (
            <Link
              aria-current={pathname === href ? "page" : undefined}
              href={href}
              key={label}
            >
              {label}
            </Link>
          ))}
        </nav>
        <p className="local-note">Single local workspace</p>
      </aside>
      <div className="workspace-main">
        <header className="topbar">
          <span><b>Agent</b> General</span>
          <span>
            <i className={`dot dot-${connectorState}`} />
            Connectors {connection === "ready" ? status.connectors.length : connectorState}
          </span>
          <span>
            <i className={`dot dot-${indexState}`} />
            Index {indexState}
          </span>
          <span><b>Mode</b> {connection === "offline" ? "unavailable" : status.model_mode}</span>
          <span><b>Run</b> {connection === "offline" ? "unavailable" : status.run.state}</span>
        </header>
        <main>{children}</main>
      </div>
    </div>
  );
}
