"use client";

import Link from "next/link";
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
  ["New chat", "/"],
  ["Search", "/search"],
  ["Artifacts", "/artifacts"],
  ["Agents", "/agents"],
  ["Automations", "/automations"],
];

export function WorkspaceShell({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState(fallback);

  useEffect(() => {
    const controller = new AbortController();
    fetch(
      `${process.env.NEXT_PUBLIC_HIGHLAND_API_URL ?? "http://127.0.0.1:8080"}/workspace/status`,
      { signal: controller.signal },
    )
      .then((response) => (response.ok ? response.json() : Promise.reject(response)))
      .then(setStatus)
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  return (
    <div className="workspace-shell">
      <aside className="sidebar">
        <Link className="brand" href="/">
          <span className="brand-mark" aria-hidden="true">H</span>
          <span>{status.workspace}</span>
        </Link>
        <nav aria-label="Workspace">
          {navigation.map(([label, href]) => (
            <Link href={href} key={label}>{label}</Link>
          ))}
        </nav>
        <p className="local-note">Single local workspace</p>
      </aside>
      <div className="workspace-main">
        <header className="topbar">
          <span><b>Agent</b> General</span>
          <span><i className="dot" /> Connectors {status.connectors.length || "checking"}</span>
          <span><i className="dot" /> Index {status.index.state}</span>
          <span><b>Mode</b> {status.model_mode}</span>
          <span><b>Run</b> {status.run.state}</span>
        </header>
        <main>{children}</main>
      </div>
    </div>
  );
}
