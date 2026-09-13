import { useEffect, useMemo, useState } from "react";
import { ComparePage, LaunchDialog, RegistryPage, RunPage, RunsPage } from "./RunPages";
import { PreflightPage } from "./PreflightPage";
import { Icon, StatusBadge } from "./components";
import { getJson } from "./types";
import type { RunSummary } from "./types";

type Route = { kind: "runs" | "run" | "compare" | "comparison" | "registry" | "environment" | "not-found"; id?: string };

function readRoute(): Route {
  const segments = window.location.pathname.split("/").filter(Boolean).map((part) => decodeURIComponent(part));
  if (!segments.length || segments[0] === "runs") return segments[1] ? { kind: "run", id: segments[1] } : { kind: "runs" };
  if (segments[0] === "compare" || segments[0] === "comparisons") return segments[1] ? { kind: "comparison", id: segments[1] } : { kind: "compare" };
  if (segments[0] === "registry") return { kind: "registry" };
  if (segments[0] === "environment") return { kind: "environment" };
  return { kind: "not-found" };
}

type Theme = "light" | "dark";

function readTheme(): Theme { return window.localStorage.getItem("eval-console-theme") === "dark" ? "dark" : "light"; }

export default function App() {
  const [route, setRoute] = useState<Route>(readRoute);
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [health, setHealth] = useState<{ ok: boolean; catalog?: { run_count?: number; last_indexed_at?: number } } | null>(null);
  const [commandOpen, setCommandOpen] = useState(false);
  const [launchOpen, setLaunchOpen] = useState(false);
  useEffect(() => { const listener = () => setRoute(readRoute()); window.addEventListener("popstate", listener); return () => window.removeEventListener("popstate", listener); }, []);
  useEffect(() => { getJson<typeof health>("/health").then(setHealth).catch(() => setHealth({ ok: false })); }, []);
  useEffect(() => { document.documentElement.dataset.theme = theme; window.localStorage.setItem("eval-console-theme", theme); }, [theme]);
  useEffect(() => { const listener = (event: KeyboardEvent) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); setCommandOpen(true); } if (event.key === "Escape") setCommandOpen(false); }; window.addEventListener("keydown", listener); return () => window.removeEventListener("keydown", listener); }, []);
  const navigate = (path: string) => { window.history.pushState({}, "", path); setRoute(readRoute()); window.scrollTo({ top: 0, behavior: "smooth" }); };
  const title = useMemo(() => route.kind === "run" ? "Run detail" : route.kind === "comparison" ? "Comparison" : route.kind === "registry" ? "Registry" : route.kind === "environment" ? "Environment" : route.kind === "compare" ? "Compare" : "Runs", [route.kind]);
  return <div className="app-shell"><aside className="sidebar"><button className="brand" onClick={() => navigate("/runs")}><span className="brand-symbol"><span /><span /><span /></span><span className="brand-word">AI-NATIVE<small>EVAL CONSOLE</small></span></button><nav className="main-nav"><NavItem icon="grid" label="Runs" active={route.kind === "runs" || route.kind === "run"} onClick={() => navigate("/runs")} /><NavItem icon="compare" label="Compare" active={route.kind === "compare" || route.kind === "comparison"} onClick={() => navigate("/comparisons")} /><NavItem icon="registry" label="Registry" active={route.kind === "registry"} onClick={() => navigate("/registry")} /><NavItem icon="pulse" label="Environment" active={route.kind === "environment"} onClick={() => navigate("/environment")} /></nav><div className="sidebar-footer"><div className="sidebar-divider" /><div className="system-status"><span className={health?.ok ? "status-led ready" : "status-led error"} /><span><strong>{health?.ok ? "Index ready" : "API unavailable"}</strong><small>{health?.catalog?.run_count ?? "—"} runs indexed</small></span></div><div className="console-version">CONSOLE <code>v0.1</code></div></div></aside><main className="main-area"><header className="topbar"><div className="breadcrumb"><span>AI-NATIVE</span><Icon name="chevron" size={13} /><strong>{title}</strong>{route.id && <><Icon name="chevron" size={13} /><code>{route.id.slice(-12)}</code></>}</div><div className="topbar-actions"><button className="theme-toggle" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"} title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}><Icon name={theme === "dark" ? "sun" : "moon"} size={16} /></button><button className="command-trigger" onClick={() => setCommandOpen(true)}><Icon name="search" size={15} /><span>Search anything</span><kbd>⌘ K</kbd></button><span className="connection"><i className={health?.ok ? "online" : "offline"} />{health?.ok ? "LOCAL" : "OFFLINE"}</span></div></header>{route.kind === "runs" && <RunsPage onOpenRun={(id) => navigate(`/runs/${encodeURIComponent(id)}`)} onNewRun={() => setLaunchOpen(true)} />}{route.kind === "run" && route.id && <RunPage runId={route.id} onBack={() => navigate("/runs")} />}{route.kind === "compare" && <ComparePage onOpenRun={(id) => navigate(`/runs/${encodeURIComponent(id)}`)} onOpenComparison={(id) => navigate(`/comparisons/${encodeURIComponent(id)}`)} />}{route.kind === "comparison" && route.id && <ComparePage comparisonId={route.id} onOpenRun={(id) => navigate(`/runs/${encodeURIComponent(id)}`)} onBack={() => navigate("/comparisons")} onOpenComparison={(id) => navigate(`/comparisons/${encodeURIComponent(id)}`)} />}{route.kind === "registry" && <RegistryPage />}{route.kind === "environment" && <PreflightPage />}{route.kind === "not-found" && <NotFound onHome={() => navigate("/runs")} />}</main>{commandOpen && <CommandPalette onClose={() => setCommandOpen(false)} onNavigate={navigate} />}{launchOpen && <LaunchDialog onClose={() => setLaunchOpen(false)} onOpenRun={(id) => navigate(`/runs/${encodeURIComponent(id)}`)} />}</div>;
}

function NavItem({ icon, label, active, onClick }: { icon: string; label: string; active: boolean; onClick: () => void }) { return <button className={`nav-item ${active ? "active" : ""}`} onClick={onClick}><Icon name={icon} size={17} /><span>{label}</span>{active && <i />}</button>; }

function CommandPalette({ onClose, onNavigate }: { onClose: () => void; onNavigate: (path: string) => void }) {
  const [query, setQuery] = useState("");
  const [runResults, setRunResults] = useState<RunSummary[]>([]);
  useEffect(() => {
    if (query.trim().length < 2) {
      setRunResults([]);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      getJson<{ items: RunSummary[] }>(`/runs?query=${encodeURIComponent(query.trim())}&limit=6`, controller.signal)
        .then((data) => setRunResults(data.items))
        .catch(() => setRunResults([]));
    }, 120);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [query]);
  const actions = [
    { label: "Open Runs", hint: "evaluation inbox", path: "/runs", icon: "grid" },
    { label: "Open Compare", hint: "controlled experiments", path: "/comparisons", icon: "compare" },
    { label: "Open Registry", hint: "system catalog", path: "/registry", icon: "registry" },
    { label: "Open Environment", hint: "can this machine run a test?", path: "/environment", icon: "pulse" },
  ].filter((item) => !query || `${item.label} ${item.hint}`.toLowerCase().includes(query.toLowerCase()));
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><div className="command-palette"><div className="command-input"><Icon name="search" size={18} /><input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search pages, Runs, Tasks…" /><kbd>ESC</kbd></div><div className="command-results">{actions.map((item) => <button key={item.path} onClick={() => { onNavigate(item.path); onClose(); }}><span className="command-icon"><Icon name={item.icon} size={16} /></span><span><strong>{item.label}</strong><small>{item.hint}</small></span><Icon name="arrow" size={15} /></button>)}{runResults.length > 0 && <div className="command-section-label">RUNS</div>}{runResults.map((run) => <button key={run.run_id} onClick={() => { onNavigate(`/runs/${encodeURIComponent(run.run_id)}`); onClose(); }}><span className="command-icon"><Icon name="pulse" size={16} /></span><span><strong>{run.task_id || run.run_id}</strong><small>{run.agent_id} · {run.run_id}</small></span><StatusBadge value={run.decision || run.status} /></button>)}{query.length >= 2 && !runResults.length && !actions.length && <div className="command-empty">No pages or Runs found.</div>}</div></div></div>;
}

function NotFound({ onHome }: { onHome: () => void }) { return <div className="page-shell"><div className="not-found"><div className="eyebrow">404 / NOT FOUND</div><h1>This route does not exist.</h1><p>Return to the evaluation inbox and choose a Run.</p><button className="button primary" onClick={onHome}>Open Runs <Icon name="arrow" size={15} /></button></div></div>; }

export { StatusBadge };
