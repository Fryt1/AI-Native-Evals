import type { ReactNode } from "react";
import type { AgentEvent, Check, RunSummary } from "./types";
import { formatDuration, formatTime, scoreLabel } from "./types";

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const common = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  const paths: Record<string, ReactNode> = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
    compare: <><path d="M8 3v18"/><path d="M16 3v18"/><path d="M4 7h8"/><path d="M12 17h8"/><circle cx="8" cy="7" r="2"/><circle cx="16" cy="17" r="2"/></>,
    registry: <><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15.5a2.5 2.5 0 0 0-2.5-2.5H4z"/><path d="M4 5.5v13A2.5 2.5 0 0 0 6.5 21H20"/><path d="M8 7h8M8 11h7"/></>,
    search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></>,
    refresh: <><path d="M20 11a8.1 8.1 0 0 0-14.8-4L3 10"/><path d="M3 4v6h6"/><path d="M4 13a8.1 8.1 0 0 0 14.8 4L21 14"/><path d="M21 20v-6h-6"/></>,
    arrow: <><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></>,
    back: <><path d="M19 12H5"/><path d="m11 18-6-6 6-6"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    x: <><path d="m6 6 12 12M18 6 6 18"/></>,
    alert: <><path d="M12 3 2.8 20h18.4z"/><path d="M12 9v4M12 17h.01"/></>,
    clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
    terminal: <><path d="m5 7 4 5-4 5"/><path d="M12 17h7"/></>,
    tool: <><path d="m14.7 6.3 3 3"/><path d="m4 20 7.2-7.2"/><path d="M14 4a4 4 0 0 0 5 5l-8 8-4-4 8-8a4 4 0 0 0-.9-4.6"/></>,
    file: <><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h6"/></>,
    folder: <><path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H10l2 2h7.5A1.5 1.5 0 0 1 21 8.5v9A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z"/></>,
    copy: <><rect x="8" y="8" width="11" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h2"/></>,
    external: <><path d="M14 4h6v6"/><path d="M20 4 11 13"/><path d="M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5"/></>,
    chevron: <path d="m9 18 6-6-6-6"/>,
    sliders: <><path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2"/><circle cx="15" cy="12" r="2"/><circle cx="8" cy="18" r="2"/></>,
    pulse: <><path d="M3 12h4l2-7 4 14 2-7h6"/></>,
    sun: <><circle cx="12" cy="12" r="4.2"/><path d="M12 2.6v2.1M12 19.3v2.1M2.6 12h2.1M19.3 12h2.1M5.3 5.3l1.5 1.5M17.2 17.2l1.5 1.5M18.7 5.3l-1.5 1.5M6.8 17.2l-1.5 1.5"/></>,
    moon: <path d="M20.2 14.4A8.4 8.4 0 0 1 9.6 3.8a8.6 8.6 0 1 0 10.6 10.6z"/>,
    layers: <><path d="m12 3 9 5-9 5-9-5z"/><path d="m3 12 9 5 9-5"/><path d="m3 16 9 5 9-5"/></>,
  };
  return <svg {...common}>{paths[name] ?? paths.grid}</svg>;
}

export function StatusBadge({ value }: { value?: string | null }) {
  const normalized = (value || "unknown").toLowerCase().replaceAll("_", " ");
  const tone = normalized.includes("pass") || normalized.includes("complete") || normalized.includes("success") || normalized === "passed" ? "pass" : normalized.includes("fail") || normalized.includes("error") || normalized.includes("stopped") ? "fail" : normalized.includes("review") || normalized.includes("running") || normalized.includes("prepared") || normalized.includes("blocked") ? "warn" : "neutral";
  return <span className={`status-badge ${tone}`}><span className="status-dot" />{normalized}</span>;
}

export function PhaseTag({ phase }: { phase?: string | null }) {
  const name = phase || "diagnostic";
  return <span className={`phase-tag phase-${name}`}>{name}</span>;
}

export function ScoreBars({ summary }: { summary: RunSummary }) {
  const values = [["O", summary.outcome_score, "outcome"], ["Q", summary.quality_score, "quality"], ["P", summary.process_score, "process"]] as const;
  return <div className="score-bars" aria-label="Outcome Quality Process scores">
    {values.map(([label, value, tone]) => <div className="score-mini" key={label} title={`${label}: ${scoreLabel(value)}`}><span>{label}</span><div className="score-track"><i className={tone} style={{ width: `${Math.max(0, Math.min(100, (value ?? 0) * 100))}%` }} /></div><strong>{value == null ? "—" : value.toFixed(2)}</strong></div>)}
  </div>;
}

export function MetricCard({ label, value, hint, tone = "default", icon }: { label: string; value: string; hint?: string; tone?: string; icon?: string }) {
  return <div className={`metric-card ${tone}`}><div className="metric-label">{icon && <Icon name={icon} size={15} />}{label}</div><div className="metric-value">{value}</div>{hint && <div className="metric-hint">{hint}</div>}</div>;
}

export function SectionHeader({ eyebrow, title, action }: { eyebrow?: string; title: string; action?: ReactNode }) {
  return <div className="section-header"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h2>{title}</h2></div>{action}</div>;
}

export function EmptyState({ icon = "layers", title, description, action }: { icon?: string; title: string; description: string; action?: ReactNode }) {
  return <div className="empty-state"><div className="empty-icon"><Icon name={icon} size={24} /></div><h3>{title}</h3><p>{description}</p>{action}</div>;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return <div className="error-state"><Icon name="alert" size={20} /><div><strong>数据读取失败</strong><p>{message}</p>{onRetry && <button className="button ghost small" onClick={onRetry}>重新读取</button>}</div></div>;
}

export function CheckRow({ check, onClick }: { check: Check; onClick?: () => void }) {
  const status = check.status || (check.passed ? "passed" : "unknown");
  const tone = status.includes("pass") ? "pass" : status.includes("fail") || status === "error" ? "fail" : "warn";
  return <button className="check-row" onClick={onClick}><span className={`check-icon ${tone}`}><Icon name={tone === "pass" ? "check" : tone === "fail" ? "x" : "alert"} size={14} /></span><span className="check-main"><strong>{check.check_id}</strong><span>{check.evaluator || "Evaluator unavailable"}</span></span><PhaseTag phase={check.phase} /><span className="check-score">{check.score == null ? "—" : check.score.toFixed(2)}</span><Icon name="chevron" size={15} /></button>;
}

export function EventIcon({ event }: { event: AgentEvent }) {
  const name = event.type.includes("error") ? "alert" : event.type.includes("tool") ? "tool" : event.type.includes("command") ? "terminal" : event.type.includes("file") ? "file" : event.type.includes("reason") ? "pulse" : event.type.includes("message") ? "layers" : "clock";
  return <span className={`event-icon event-${event.type}`}><Icon name={name} size={15} /></span>;
}

export function EventRow({ event, selected, onClick }: { event: AgentEvent; selected?: boolean; onClick?: () => void }) {
  return <button className={`event-row ${selected ? "selected" : ""}`} onClick={onClick}><div className="event-rail"><EventIcon event={event} /><span className="event-seq">{String(event.seq).padStart(3, "0")}</span></div><div className="event-content"><div className="event-meta"><span>{event.type.replaceAll("_", " ")}</span>{event.turn_id && <span>{event.turn_id}</span>}{event.duration_ms != null && <span>{formatDuration(event.duration_ms)}</span>}</div><strong>{event.summary || event.type}</strong><span className="event-source">{event.actor?.id || "agent"} · {event.source?.adapter || "legacy"}</span></div><Icon name="chevron" size={15} /></button>;
}

export function ConfigValue({ label, value }: { label: string; value: unknown }) {
  let rendered = "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") rendered = String(value);
  else if (value != null) rendered = JSON.stringify(value, null, 2);
  return <div className="config-item"><span>{label.replaceAll("_", " ")}</span><code>{rendered}</code></div>;
}

export function RunMeta({ summary }: { summary: RunSummary }) {
  return <div className="run-meta"><span className="agent-mark">{(summary.agent_id || "?").slice(0, 1).toUpperCase()}</span><span><strong>{summary.agent_id || "Unknown agent"}</strong><small>{summary.model || "model unavailable"}</small></span><span className="meta-divider" /><span><small>{formatTime(summary.finished_at || summary.started_at)}</small><small>{formatDuration(summary.duration_ms)}</small></span></div>;
}
