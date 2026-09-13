import { useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent } from "react";
import { getJson, awaitPreflight, formatDuration, formatTime, numericScore, postJson, scoreLabel } from "./types";
import type { AgentEvent, Artifact, Check, ComparisonDetail, EventsResponse, PreflightCheck, ProviderModelListing, ProviderSummary, ReasoningLevels, Registry, RunDetail, RunJob, RunPlan, RunSummary } from "./types";
import { blockerSummary, blockingChecks } from "./launchGate";
import { chooseModel, chooseReasoning, initialForm, agentModels, nonAgentModels, isMeaningfulChoice, preferredModelFor, taskExecution } from "./launchDefaults";
import { CheckRow, ConfigValue, EmptyState, ErrorState, EventRow, Icon, MetricCard, PhaseTag, RunMeta, ScoreBars, SectionHeader, StatusBadge } from "./components";

type RunBucket = "" | "running" | "failed" | "review" | "passed";

type RunListResponse = {
  items: RunSummary[];
  total: number;
  stats?: {
    status?: Record<string, number>;
    decision?: Record<string, number>;
    bucket?: Record<string, number>;
  };
};

export function RunsPage({ onOpenRun, onNewRun }: { onOpenRun: (id: string) => void; onNewRun: () => void }) {
  const [items, setItems] = useState<RunSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<RunListResponse["stats"]>();
  const [query, setQuery] = useState("");
  const [bucket, setBucket] = useState<RunBucket>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Typing fires overlapping requests; only the newest one may write state.
  const requestId = useRef(0);
  const load = () => {
    const id = ++requestId.current;
    setLoading(true);
    setError("");
    const params = new URLSearchParams({ limit: "100" });
    if (query) params.set("query", query);
    if (bucket) params.set("bucket", bucket);
    getJson<RunListResponse>(`/runs?${params}`)
      .then((data) => {
        if (id !== requestId.current) return;
        setItems(data.items);
        setTotal(data.total);
        setStats(data.stats);
      })
      .catch((reason: Error) => {
        if (id !== requestId.current) return;
        setError(reason.message);
      })
      .finally(() => {
        if (id === requestId.current) setLoading(false);
      });
  };

  useEffect(() => {
    const timer = window.setTimeout(load, 120);
    return () => window.clearTimeout(timer);
  }, [query, bucket]);

  const counts = useMemo(() => {
    const bucketCounts = stats?.bucket || {};
    return {
      running: bucketCounts.running || 0,
      failed: bucketCounts.failed || 0,
      review: bucketCounts.review || 0,
      passed: bucketCounts.passed || 0,
    };
  }, [stats]);

  const description = query || bucket
    ? "换一个筛选条件，或者清除当前筛选。"
    : "先通过 CLI 执行一个 Task，完成后 Run 会自动出现在这里。";

  return <div className="page-shell">
    <div className="page-heading">
      <div><div className="eyebrow">EVALUATION INBOX</div><h1>Runs</h1><p>从最近的评测现场开始，找到需要解释的行为。</p></div>
      <div className="heading-actions"><button className="button primary" onClick={onNewRun}><Icon name="pulse" size={16} />运行测试</button><button className="button ghost" onClick={load}><Icon name="refresh" size={16} />刷新索引</button></div>
    </div>
    <div className="filter-bar">
      <label className="search-box"><Icon name="search" size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索 Task、Agent、Model 或 Run ID" /></label>
      <div className="filter-tabs">
        <button className={!bucket ? "active" : ""} onClick={() => setBucket("")}>全部 <b>{stats ? Object.values(stats.status || {}).reduce((sum, value) => sum + value, 0) : total}</b></button>
        <button className={bucket === "running" ? "active" : ""} onClick={() => setBucket("running")}>运行中 <b>{counts.running}</b></button>
        <button className={bucket === "failed" ? "active" : ""} onClick={() => setBucket("failed")}>失败 <b>{counts.failed}</b></button>
        <button className={bucket === "review" ? "active" : ""} onClick={() => setBucket("review")}>Review <b>{counts.review}</b></button>
        <button className={bucket === "passed" ? "active" : ""} onClick={() => setBucket("passed")}>通过 <b>{counts.passed}</b></button>
      </div>
    </div>
    {error && <ErrorState message={error} onRetry={load} />}
    {loading ? <LoadingRows /> : items.length === 0 ? <EmptyState icon="grid" title="没有找到 Run" description={description} /> : <div className="panel table-panel">
      <div className="table-head"><span>Run / Task</span><span>Agent / Model</span><span>Scores</span><span>Decision</span><span>Finished</span><span /></div>
      {items.map((item) => <button className="run-row" key={item.run_id} onClick={() => onOpenRun(item.run_id)}>
        <div className="run-id-cell"><span className={`status-marker ${item.status || "unknown"}`} /><div><strong>{item.task_id || "Untitled task"}</strong><small>{item.run_id}</small></div></div>
        <div className="agent-cell"><span className="agent-avatar">{(item.agent_id || "?").slice(0, 1).toUpperCase()}</span><div><strong>{item.agent_id || "—"}</strong><small>{item.model || "model unavailable"}</small></div></div>
        <ScoreBars summary={item} />
        <StatusBadge value={item.decision || item.status} />
        <div className="finished-cell"><strong>{formatTime(item.finished_at || item.started_at)}</strong><small>{isActiveRun(item.status) ? "正在运行" : formatDuration(item.duration_ms)}</small></div>
        <Icon name="chevron" size={16} />
      </button>)}
    </div>}
    <div className="page-foot"><span>{total} 个匹配 Run · 数据来自 EvalRuns</span><span className="index-state"><i /> Index ready</span></div>
  </div>;
}

const ACTIVE_RUN_STATUSES = new Set(["starting", "preparing", "running", "evaluating"]);

function isActiveRun(status?: string | null) { return !!status && ACTIVE_RUN_STATUSES.has(status); }

function LoadingRows() { return <div className="panel table-panel loading-panel">{[1, 2, 3, 4, 5].map((value) => <div className="skeleton-row" key={value}><i /><i /><i /><i /><i /></div>)}</div>; }

const tabs = ["overview", "trace", "evaluations", "artifacts", "configuration"] as const;
type RunTab = typeof tabs[number];

export function RunPage({ runId, onBack }: { runId: string; onBack: () => void }) {
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [subjectEvents, setSubjectEvents] = useState<AgentEvent[]>([]);
  const [traceTitle, setTraceTitle] = useState("Agent Trace");
  const [tab, setTab] = useState<RunTab>("overview");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [traceError, setTraceError] = useState("");
  useEffect(() => { setLoading(true); setError(""); setTraceError(""); setTraceTitle("Agent Trace"); Promise.all([getJson<RunDetail>(`/runs/${encodeURIComponent(runId)}`), getJson<EventsResponse>(`/runs/${encodeURIComponent(runId)}/events?limit=500`)]).then(([run, trace]) => { setDetail(run); setEvents(trace.events); setSubjectEvents(trace.events); }).catch((reason: Error) => setError(reason.message)).finally(() => setLoading(false)); }, [runId]);
  const openEvaluatorTrace = (evaluatorRunId: string) => { setTraceError(""); getJson<EventsResponse>(`/runs/${encodeURIComponent(runId)}/evaluator-runs/${encodeURIComponent(evaluatorRunId)}/events?limit=500`).then((trace) => { setEvents(trace.events); setTraceTitle(`Evaluator · ${evaluatorRunId}`); setTab("trace"); }).catch((reason: Error) => setTraceError(`Evaluator Trace 读取失败：${reason.message}`)); };
  if (loading) return <div className="page-shell"><button className="back-link" onClick={onBack}><Icon name="back" size={16} />返回 Runs</button><LoadingDetail /></div>;
  if (error || !detail) return <div className="page-shell"><button className="back-link" onClick={onBack}><Icon name="back" size={16} />返回 Runs</button><ErrorState message={error || "Run not found"} /></div>;
  const summary = detail.summary;
  return <div className="page-shell detail-page"><button className="back-link" onClick={onBack}><Icon name="back" size={16} />返回 Runs</button><div className="detail-heading"><div><div className="run-kicker"><span>{summary.task_id}</span><span>/</span><code>{summary.run_id}</code></div><h1>{summary.agent_id || "Unknown agent"} <span className="heading-muted">on</span> {summary.task_id}</h1><RunMeta summary={summary} /></div><StatusBadge value={summary.decision || summary.status} /></div><div className="run-spine"><span className="spine-node done">Prepared</span><i /><span className="spine-node done">Agent run</span><i /><span className={`spine-node ${summary.status === "evaluating" ? "active" : "done"}`}>Evaluating</span><i /><span className={`spine-node ${summary.decision === "fail" ? "fail" : summary.decision === "pass" ? "pass" : "active"}`}>Verdict</span></div><div className="metric-grid"><MetricCard label="Outcome" value={scoreLabel(detail.scores.outcome)} hint="结果是否完成" tone="outcome" icon="check" /><MetricCard label="Quality" value={scoreLabel(detail.scores.quality)} hint="产物质量" tone="quality" icon="pulse" /><MetricCard label="Process" value={scoreLabel(detail.scores.process)} hint="Agent 执行过程" tone="process" icon="layers" /><MetricCard label="Duration" value={formatDuration(summary.duration_ms)} hint={`${detail.trace_summary.events || events.length} events`} tone="default" icon="clock" /></div><div className="tab-bar">{tabs.map((value) => <button key={value} className={tab === value ? "active" : ""} onClick={() => setTab(value)}>{tabLabel(value)}{value === "trace" && <b>{events.length}</b>}{value === "evaluations" && <b>{detail.checks.length}</b>}{value === "artifacts" && <b>{detail.artifacts.length}</b>}</button>)}</div>{tab === "overview" && <Overview detail={detail} events={subjectEvents} onTab={setTab} onOpenEvaluator={openEvaluatorTrace} />}{traceError && <div className="inline-warning"><Icon name="alert" size={16} />{traceError}<button className="text-button" onClick={() => setTraceError("")}>关闭</button></div>}{tab === "trace" && <><TraceView events={events} title={traceTitle} onBackToSubject={traceTitle === "Agent Trace" ? undefined : () => { setEvents(subjectEvents); setTraceTitle("Agent Trace"); }} /></>}{tab === "evaluations" && <EvaluationsView detail={detail} onOpenEvaluator={openEvaluatorTrace} />}{tab === "artifacts" && <ArtifactsView runId={runId} artifacts={detail.artifacts} />}{tab === "configuration" && <ConfigurationView detail={detail} />}</div>;
}

function tabLabel(tab: RunTab) { return ({ overview: "Overview", trace: "Trace", evaluations: "Evaluations", artifacts: "Artifacts", configuration: "Configuration" })[tab]; }

function LoadingDetail() { return <><div className="skeleton-heading" /><div className="metric-grid">{[1, 2, 3, 4].map((value) => <div className="metric-card skeleton-card" key={value} />)}</div><div className="panel skeleton-large" /></>; }

function Overview({ detail, events, onTab, onOpenEvaluator }: { detail: RunDetail; events: AgentEvent[]; onTab: (tab: RunTab) => void; onOpenEvaluator: (id: string) => void }) {
  const failed = detail.checks.filter((check) => check.status === "failed" || check.status === "error");
  const problemEvents = events.filter((event) => event.type === "error" || event.status === "failed");
  return <div className="overview-grid"><div className="stack"><section className="panel evidence-panel"><SectionHeader eyebrow="EVIDENCE SPINE" title="从任务到结论" action={<button className="text-button" onClick={() => onTab("evaluations")}>查看完整证据 <Icon name="arrow" size={14} /></button>} /><div className="evidence-spine"><div className="evidence-step done"><span>01</span><strong>Prompt</strong><small>Task instructions</small></div><div className="evidence-connector done" /><div className="evidence-step done"><span>02</span><strong>Agent actions</strong><small>{events.length} trace events</small></div><div className="evidence-connector done" /><div className={`evidence-step ${detail.artifacts.length ? "done" : "pending"}`}><span>03</span><strong>Artifacts</strong><small>{detail.artifacts.length} files</small></div><div className="evidence-connector" /><div className={`evidence-step ${detail.checks.length ? "done" : "pending"}`}><span>04</span><strong>Checks</strong><small>{detail.checks.length} checks</small></div><div className="evidence-connector" /><div className={`evidence-step ${detail.decision === "pass" ? "pass" : detail.decision === "fail" ? "fail" : "pending"}`}><span>05</span><strong>Verdict</strong><small>{detail.decision || "not evaluated"}</small></div></div></section><section className="panel"><SectionHeader eyebrow="CHECKS" title={`${detail.checks.filter((check) => check.status === "passed" || check.passed).length} passed · ${failed.length} need attention`} action={<button className="text-button" onClick={() => onTab("evaluations")}>Open graph <Icon name="arrow" size={14} /></button>} />{detail.checks.length ? <div className="check-list">{detail.checks.slice(0, 6).map((check) => <CheckRow key={check.check_id} check={check} onClick={() => onTab("evaluations")} />)}</div> : <EmptyState icon="check" title="没有 Evaluation" description="本次 Run 尚未生成 Evaluation Report。" />}</section></div><aside className="stack"><section className="panel last-action"><SectionHeader eyebrow="LAST OBSERVED" title="最近发生了什么" />{detail.trace_summary.last_agent_message ? <><p>{String(detail.trace_summary.last_agent_message)}</p><div className="last-action-meta"><span><Icon name="clock" size={14} />{formatTime(detail.summary.finished_at)}</span><button className="text-button" onClick={() => onTab("trace")}>打开 Trace <Icon name="arrow" size={14} /></button></div></> : <EmptyState icon="pulse" title="没有 Agent message" description="这个 Run 没有可显示的最终消息。" />}</section><section className="panel stats-panel"><SectionHeader eyebrow="RUN SIGNALS" title="执行信号" /><div className="signal-grid"><span><small>Tool calls</small><strong>{String(detail.trace_summary.tool_calls ?? "—")}</strong></span><span><small>Commands</small><strong>{String(detail.trace_summary.commands ?? "—")}</strong></span><span><small>Errors</small><strong className={Number(detail.trace_summary.errors) > 0 ? "coral" : ""}>{String(detail.trace_summary.errors ?? "—")}</strong></span><span><small>Artifacts</small><strong>{detail.artifacts.length}</strong></span></div>{problemEvents.length > 0 && <div className="inline-warning"><Icon name="alert" size={15} />{problemEvents.length} 个 Trace event 需要查看</div>}</section><section className="panel quick-links"><SectionHeader eyebrow="EVALUATOR RUNS" title={`${detail.evaluator_runs.length} nested runs`} />{detail.evaluator_runs.length ? detail.evaluator_runs.map((item) => <button className="nested-run" key={String(item.evaluator_run_id)} onClick={() => onOpenEvaluator(String(item.evaluator_run_id))}><span className="judge-mark"><Icon name="pulse" size={14} /></span><span><strong>{String(item.role || "evaluator")}</strong><small>{String(item.evaluator_run_id)}</small></span><Icon name="chevron" size={15} /></button>) : <p className="muted-copy">本次 Run 没有独立 Evaluator 子运行。</p>}</section></aside></div>;
}

export function TraceView({ events, title = "Agent Trace", onBackToSubject }: { events: AgentEvent[]; title?: string; onBackToSubject?: () => void }) {
  const [selected, setSelected] = useState<AgentEvent | null>(events[events.length - 1] || null);
  const [filter, setFilter] = useState("");
  useEffect(() => setSelected(events[events.length - 1] || null), [events]);
  const [onlyErrors, setOnlyErrors] = useState(false);
  const filtered = events.filter((event) => (!filter || event.type.includes(filter) || event.summary.toLowerCase().includes(filter.toLowerCase())) && (!onlyErrors || event.type === "error" || event.status === "failed"));
  return <div className="trace-layout"><section className="panel trace-panel"><div className="trace-toolbar"><span className="trace-title"><Icon name="pulse" size={15} />{title}</span>{onBackToSubject && <button className="text-button" onClick={onBackToSubject}>返回 Agent Trace</button>}<label className="mini-search"><Icon name="search" size={15} /><input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="搜索 Trace" /></label><label className="toggle-filter"><input type="checkbox" checked={onlyErrors} onChange={(event) => setOnlyErrors(event.target.checked)} />Only errors</label><span className="trace-count">{filtered.length} / {events.length}</span></div><div className="trace-list">{filtered.length ? filtered.map((event) => <EventRow key={`${event.seq}-${event.event_id}`} event={event} selected={selected?.event_id === event.event_id} onClick={() => setSelected(event)} />) : <EmptyState icon="search" title="没有匹配事件" description="换一个关键词或关闭 Only errors。" />}</div></section><aside className="panel event-inspector"><div className="inspector-header"><div><div className="eyebrow">EVENT INSPECTOR</div><h3>{selected?.type.replaceAll("_", " ") || "Select an event"}</h3></div>{selected && <PhaseTag phase={selected.status || "observed"} />}</div>{selected ? <div className="inspector-body"><div className="inspector-summary"><EventRow event={selected} selected /></div><div className="inspector-meta-grid"><span><small>Sequence</small><strong>#{selected.seq}</strong></span><span><small>Actor</small><strong>{selected.actor?.id}</strong></span><span><small>Turn</small><strong>{selected.turn_id || "—"}</strong></span><span><small>Step</small><strong>{selected.step_id || "—"}</strong></span></div><div className="payload-block"><div className="payload-title">Summary</div><p>{selected.summary}</p></div><details className="payload-details"><summary>Raw payload</summary><pre>{JSON.stringify(selected.payload ?? {}, null, 2)}</pre></details></div> : <EmptyState icon="pulse" title="选择一个事件" description="点击左侧 Trace 中的事件查看详细信息。" />}</aside></div>;
}

function EvaluationsView({ detail, onOpenEvaluator }: { detail: RunDetail; onOpenEvaluator: (id: string) => void }) {
  const [selected, setSelected] = useState<Check | null>(detail.checks[0] || null);
  return <div className="evaluation-layout"><section className="panel check-graph"><SectionHeader eyebrow="TEST PLAN GRAPH" title="Checks" /><div className="graph-list">{detail.checks.map((check, index) => <div key={check.check_id} className="graph-node-wrap"><button className={`graph-node ${selected?.check_id === check.check_id ? "selected" : ""}`} onClick={() => setSelected(check)}><span className="graph-index">{String(index + 1).padStart(2, "0")}</span><span><strong>{check.check_id}</strong><small>{check.phase} · {check.annotator_kind}</small></span><StatusBadge value={check.status} /></button>{index < detail.checks.length - 1 && <div className="graph-line" />}</div>)}</div></section><aside className="panel check-detail">{selected ? <><div className="inspector-header"><div><div className="eyebrow">CHECK DETAIL</div><h3>{selected.check_id}</h3></div><StatusBadge value={selected.status} /></div><div className="check-detail-body"><div className="check-detail-grid"><span><small>Phase</small><PhaseTag phase={selected.phase} /></span><span><small>Evaluator</small><strong>{selected.evaluator || "—"}</strong></span><span><small>Method</small><strong>{selected.annotator_kind || "—"}</strong></span><span><small>Score</small><strong className="large-score">{scoreLabel(selected.score)}</strong></span></div><div className="detail-block"><div className="payload-title">Why</div><p>{selected.explanation || "No explanation was recorded for this check."}</p></div>{selected.depends_on?.length ? <div className="detail-block"><div className="payload-title">Depends on</div><div className="tag-row">{selected.depends_on.map((id) => <span className="soft-tag" key={id}>{id}</span>)}</div></div> : null}<div className="detail-block"><div className="payload-title">Evidence references</div>{selected.evidence_refs?.length ? selected.evidence_refs.map((ref) => <div className="reference-row" key={ref}><Icon name="file" size={14} /><code>{ref}</code></div>) : <p className="muted-copy">No evidence references.</p>}</div>{selected.evaluator_run_id && <button className="button secondary full" onClick={() => onOpenEvaluator(selected.evaluator_run_id || "")}><Icon name="pulse" size={15} />打开 Evaluator Trace <Icon name="arrow" size={14} /></button>}<details className="payload-details"><summary>Check details</summary><pre>{JSON.stringify(selected.details || {}, null, 2)}</pre></details></div></> : <EmptyState icon="check" title="没有 Check" description="这个 Run 没有可显示的检查项。" />}</aside></div>;
}

function ArtifactsView({ runId, artifacts }: { runId: string; artifacts: Artifact[] }) {
  const [selected, setSelected] = useState<Artifact | null>(artifacts[0] || null);
  return <div className="artifact-layout"><section className="panel artifact-list"><SectionHeader eyebrow="DECLARED OUTPUTS" title={`${artifacts.length} artifacts`} />{artifacts.length ? artifacts.map((artifact) => <button className={`artifact-row ${selected?.artifact_id === artifact.artifact_id ? "selected" : ""}`} key={artifact.artifact_id} onClick={() => setSelected(artifact)}><span className="file-icon"><Icon name={artifact.mime_type.startsWith("image/") ? "layers" : "file"} size={16} /></span><span><strong>{artifact.relative_path}</strong><small>{artifact.role} · {formatBytes(artifact.size)}</small></span><Icon name="chevron" size={15} /></button>) : <EmptyState icon="file" title="没有声明产物" description="新 Run 应写入 artifacts/manifest.json。" />}</section><aside className="panel artifact-preview">{selected ? <ArtifactPreview runId={runId} artifact={selected} /> : <EmptyState icon="file" title="选择一个产物" description="从左侧列表选择 Artifact。" />}</aside></div>;
}

function ArtifactPreview({ runId, artifact }: { runId: string; artifact: Artifact }) {
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(false);
  const url = `/api/v1/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifact.artifact_id)}`;
  const isImage = artifact.mime_type.startsWith("image/");
  useEffect(() => {
    // Clear first: switching artifacts must not leave the previous file on screen.
    setContent("");
    if (!artifact.previewable || isImage) return;
    setLoading(true);
    let cancelled = false;
    fetch(url)
      .then((response) => response.text())
      .then((body) => { if (!cancelled) setContent(body); })
      .catch(() => { if (!cancelled) setContent("Unable to load preview"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [url, artifact.previewable, isImage]);
  return <><div className="preview-header"><div><div className="eyebrow">{artifact.role.replaceAll("_", " ")}</div><h3>{artifact.relative_path.split("/").pop()}</h3><small>{formatBytes(artifact.size)} · {artifact.mime_type}</small></div><a className="icon-button" href={`${url}?download=true`} title="下载"><Icon name="external" size={16} /></a></div>{isImage ? <div className="image-preview"><img src={url} alt={artifact.relative_path} /></div> : !artifact.previewable ? <EmptyState icon="file" title="无法预览这个文件" description="这是二进制产物。用右上角的下载按钮取回原文件。" /> : loading ? <div className="preview-loading">Loading preview…</div> : <pre className="artifact-code">{prettyContent(content, artifact.kind)}</pre>}</>;
}

function prettyContent(content: string, kind: string) { if (!content) return "No preview available."; if (kind === "json" || kind === "jsonl") { try { return JSON.stringify(JSON.parse(content), null, 2); } catch { return content; } } return content; }
function formatBytes(value: number) { if (value < 1024) return `${value} B`; if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1024 / 1024).toFixed(1)} MB`; }

function ConfigurationView({ detail }: { detail: RunDetail }) {
  const entries = Object.entries(detail.configuration);
  return <div className="configuration-layout"><section className="panel"><SectionHeader eyebrow="RESOLVED CONFIGURATION" title="这次 Run 用了什么" /><div className="config-list">{entries.map(([label, value]) => <ConfigValue key={label} label={label} value={value} />)}</div></section><aside className="panel config-note"><div className="eyebrow">READ-ONLY PROVENANCE</div><h3>配置是评测证据</h3><p>这里展示的是 Run 开始时解析出的配置快照。它不会读取或修改你本机的 Codex、DSH 全局配置。</p><div className="provenance-item"><Icon name="layers" size={16} /><span><strong>Sandbox isolated</strong><small>Agent 在 Docker 中运行</small></span></div><div className="provenance-item"><Icon name="tool" size={16} /><span><strong>MCP recorded</strong><small>使用 Run manifest 中的声明</small></span></div></aside></div>;
}

export function ComparePage({ comparisonId, onOpenRun, onBack, onOpenComparison }: { comparisonId?: string; onOpenRun: (id: string) => void; onBack?: () => void; onOpenComparison: (id: string) => void }) {
  const [items, setItems] = useState<Array<{ comparison_id: string; task_id?: string; created_at?: string; run_count: number }>>([]);
  const [detail, setDetail] = useState<ComparisonDetail | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { getJson<{ items: typeof items }>("/comparisons").then((data) => setItems(data.items)).catch((reason: Error) => setError(reason.message)); }, []);
  useEffect(() => { if (comparisonId) getJson<ComparisonDetail>(`/comparisons/${encodeURIComponent(comparisonId)}`).then(setDetail).catch((reason: Error) => setError(reason.message)); }, [comparisonId]);
  if (error) return <div className="page-shell"><ErrorState message={error} /></div>;
  if (!comparisonId || !detail) return <div className="page-shell"><div className="page-heading"><div><div className="eyebrow">CONTROLLED EXPERIMENTS</div><h1>Compare</h1><p>在相同 Task 条件下，解释 Agent 之间的行为差异。</p></div></div>{items.length ? <div className="comparison-grid">{items.map((item) => <button className="comparison-card" key={item.comparison_id} onClick={() => onOpenComparison(item.comparison_id)}><div className="comparison-top"><span className="comparison-mark"><Icon name="compare" size={18} /></span><StatusBadge value="ready" /></div><h3>{item.task_id || "Untitled task"}</h3><code>{item.comparison_id}</code><div className="comparison-foot"><span>{item.run_count} runs</span><span>{formatTime(item.created_at)}</span><Icon name="arrow" size={15} /></div></button>)}</div> : <EmptyState icon="compare" title="还没有 Comparison" description="用 compare 命令运行同一 Task 的多个 Agent。" />}</div>;
  const runs = detail.runs;
  const metric = (key: "outcome_score" | "quality_score" | "process_score") => runs.map((run) => Number(run.summary?.[key] ?? run.evaluation?.[key] ?? NaN));
  const matrixStyle = { gridTemplateColumns: `140px repeat(${Math.max(runs.length, 1)}, minmax(150px, 1fr)) 90px` };
  return <div className="page-shell detail-page"><button className="back-link" onClick={onBack || (() => window.history.pushState({}, "", "/comparisons"))}><Icon name="back" size={16} />返回 Compare</button><div className="page-heading comparison-heading"><div><div className="eyebrow">COMPARISON</div><h1>{detail.invariant.task_id ? String(detail.invariant.task_id) : detail.comparison_id}</h1><p><code>{detail.comparison_id}</code> · {formatTime(detail.created_at)}</p></div></div><div className={`fairness-banner ${detail.fairness.fair ? "fair" : "warn"}`}><Icon name={detail.fairness.fair ? "check" : "alert"} size={17} /><div><strong>{detail.fairness.fair ? "Fair comparison" : "Configuration needs review"}</strong><span>{detail.fairness.message}</span></div></div><section className="panel comparison-matrix"><div className="matrix-head" style={matrixStyle}><span>Metric</span>{runs.map((run) => <span key={run.run_id}>{run.agent || run.summary?.agent_id}<small>{run.run_id}</small></span>)}<span>Delta</span></div>{([["Outcome", "outcome_score"], ["Quality", "quality_score"], ["Process", "process_score"]] as const).map(([label, key]) => { const values = metric(key); const finite = values.filter((value) => Number.isFinite(value)); const best = finite.length ? Math.max(...finite) : null; const worst = finite.length ? Math.min(...finite) : null; return <div className="matrix-row" style={matrixStyle} key={key}><strong>{label}</strong>{values.map((value, index) => <span key={runs[index].run_id} className={best !== null && value === best && values.length > 1 ? "best" : ""}>{Number.isNaN(value) ? "—" : value.toFixed(2)}</span>)}<span className="delta">{best === null || worst === null ? "—" : (best - worst).toFixed(2)}</span></div>; })}<div className="matrix-row" style={matrixStyle}><strong>Duration</strong>{runs.map((run) => <span key={run.run_id}>{formatDuration(run.summary?.duration_ms)}</span>)}<span>—</span></div><div className="matrix-row" style={matrixStyle}><strong>Decision</strong>{runs.map((run) => <span key={run.run_id}><StatusBadge value={run.summary?.decision || run.status} /></span>)}<span>—</span></div></section>{detail.check_matrix?.length ? <section className="panel check-comparison"><SectionHeader eyebrow="CHECK-BY-CHECK" title="验证差异" /><div className="check-matrix-list">{detail.check_matrix.map((check) => <div className="check-matrix-row" style={{ gridTemplateColumns: `minmax(210px, 1.2fr) repeat(${Math.max(runs.length, 1)}, minmax(140px, 1fr))` }} key={check.check_id}><div><strong>{check.check_id}</strong><small>{check.phase || "diagnostic"}</small></div>{runs.map((run) => { const value = check.values.find((item) => item.run_id === run.run_id); return <span key={run.run_id}><StatusBadge value={value?.status || "missing"} /><small>{value?.score == null ? "—" : value.score.toFixed(2)}</small></span>; })}</div>)}</div></section> : null}<section className="compare-runs"><SectionHeader eyebrow="RUNS IN THIS COMPARISON" title="打开单次现场" />{runs.map((run) => <button className="compare-run-card" key={run.run_id} onClick={() => onOpenRun(run.run_id)}><span className="agent-avatar">{(run.agent || run.summary?.agent_id || "?").slice(0, 1).toUpperCase()}</span><div><strong>{run.agent || run.summary?.agent_id}</strong><small>{run.run_id}</small></div><ScoreBars summary={run.summary || { run_id: run.run_id, task_id: "", agent_id: run.agent || "", outcome_score: numericScore(run.evaluation?.outcome_score), quality_score: numericScore(run.evaluation?.quality_score), process_score: numericScore(run.evaluation?.process_score) }} /><Icon name="arrow" size={16} /></button>)}</section></div>;
}

export function RegistryPage() {
  const [data, setData] = useState<Registry | null>(null);
  const [selected, setSelected] = useState<"tasks" | "agents" | "providers" | "models" | "mcp" | "sandboxes" | "presets">("tasks");
  const [error, setError] = useState("");
  useEffect(() => { getJson<Registry>("/registry").then(setData).catch((reason: Error) => setError(reason.message)); }, []);
  if (error) return <div className="page-shell"><ErrorState message={error} /></div>;
  if (!data) return <div className="page-shell"><LoadingDetail /></div>;
  const entries = data[selected] || [];
  const registryKeys = ["tasks", "agents", "providers", "models", "mcp", "sandboxes", "presets"] as const;
  return <div className="page-shell"><div className="page-heading"><div><div className="eyebrow">SYSTEM CATALOG</div><h1>Registry</h1><p>查看这次评测系统能使用的 Task、Agent、Model 和运行 Profile。</p></div></div><div className="registry-layout"><nav className="panel registry-nav">{registryKeys.map((key) => <button key={key} className={selected === key ? "active" : ""} onClick={() => setSelected(key)}><span><Icon name={key === "tasks" ? "grid" : key === "providers" ? "tool" : key === "models" ? "pulse" : key === "agents" ? "layers" : "sliders"} size={16} />{key}</span><b>{data[key].length}</b></button>)}</nav><section className="panel registry-list"><div className="registry-list-head"><div><div className="eyebrow">{selected.toUpperCase()}</div><h2>{entries.length} definitions</h2></div><span className="read-only-label"><Icon name="check" size={13} />READ ONLY</span></div>{entries.length ? entries.map((entry) => <div className="registry-row" key={entry.id}><span className="registry-symbol">{entry.id.slice(0, 1).toUpperCase()}</span><div><strong>{entry.id}</strong><small>{selected === "models" && entry.provider_id ? `${entry.provider_id} · ${entry.model_id || entry.id}` : selected === "providers" ? `${(entry.model_ids || []).length} models · ${entry.path}` : `${entry.summary || entry.kind} · ${entry.path}`}</small></div>{entry.checks != null && <span className="registry-count">{entry.checks} checks</span>}<Icon name="chevron" size={15} /></div>) : <EmptyState icon="registry" title="没有定义" description="这个 Registry 目前是空的。" />}</section></div></div>;
}

// What a person actually chooses: a task, an upstream, a model on that
// upstream, and how hard it should think. Everything else is derived or comes
// from the task and project defaults.
type LaunchForm = {
  task_id: string;
  provider: string;
  model: string;
  reasoning_effort: string;
  agent: string;
  mcp_profile: string;
  sandbox_profile: string;
};

type LaunchStep = "configure" | "preview" | "running";

export function LaunchDialog({ onClose, onOpenRun }: { onClose: () => void; onOpenRun: (runId: string) => void }) {  const [registry, setRegistry] = useState<Registry | null>(null);
  const [providers, setProviders] = useState<ProviderSummary[]>([]);
  const [models, setModels] = useState<ProviderModelListing | null>(null);
  const [modelsBusy, setModelsBusy] = useState(false);
  const [levels, setLevels] = useState<string[]>([]);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [form, setForm] = useState<LaunchForm>({ task_id: "", provider: "", model: "", reasoning_effort: "", agent: "", mcp_profile: "", sandbox_profile: "" });
  const [plan, setPlan] = useState<RunPlan | null>(null);
  const [job, setJob] = useState<RunJob | null>(null);
  const [step, setStep] = useState<LaunchStep>("configure");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Environment findings for the current selection. Null means "not checked
  // yet"; the launch gate fills it before anything reaches Docker.
  const [blockers, setBlockers] = useState<Array<{ name: string; check: PreflightCheck }> | null>(null);

  useEffect(() => {
    Promise.all([
      getJson<Registry>("/registry"),
      getJson<{ items: ProviderSummary[] }>("/providers"),
    ])
      .then(([data, providerData]) => {
        setRegistry(data);
        setProviders(providerData.items || []);
        // A single candidate is not a choice: preselect it so the dialog opens
        // in a runnable state instead of asking for four obvious answers.
        setForm((current) => ({ ...current, ...initialForm(data, providerData.items || [], []) }));
      })
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setLoading(false));
  }, []);

  // Models come from the provider itself, not from a checked-in list that can
  // name a model nobody serves.
  useEffect(() => {
    if (!form.provider) {
      setModels(null);
      setLevels([]);
      return;
    }
    let cancelled = false;
    setModelsBusy(true);
    getJson<ProviderModelListing>(`/providers/${encodeURIComponent(form.provider)}/models`)
      .then((data) => {
        if (cancelled) return;
        setModels(data);
        const available = data.models.map((entry) => entry.id);
        setForm((current) => {
          if (available.includes(current.model)) return current;
          // The previously chosen model is gone (or was never set): take the
          // configured default when this provider serves it, else the only
          // candidate when there is exactly one.
          const next = chooseModel(
            available,
            preferredModelFor(registry?.defaults || {}, current.provider),
          );
          return { ...current, model: next, reasoning_effort: next ? current.reasoning_effort : "" };
        });
      })
      .catch((reason: Error) => { if (!cancelled) setError(reason.message); })
      .finally(() => { if (!cancelled) setModelsBusy(false); });
    return () => { cancelled = true; };
  }, [form.provider, registry]);

  // Legal reasoning levels depend on the model and the agent, so they are
  // resolved rather than hardcoded in the browser.
  useEffect(() => {
    if (!form.provider || !form.model) {
      setLevels([]);
      return;
    }
    let cancelled = false;
    const body: Record<string, string> = { provider: form.provider, model: form.model };
    if (form.agent) body.agent = form.agent;
    postJson<ReasoningLevels>("/reasoning-levels", body)
      .then((data) => {
        if (cancelled) return;
        setLevels(data.levels || []);
        setForm((current) => {
          if (current.reasoning_effort && (data.levels || []).includes(current.reasoning_effort)) {
            return current;
          }
          const provider = providers.find((item) => item.id === current.provider);
          return {
            ...current,
            reasoning_effort: chooseReasoning(data.levels || [], provider?.default_reasoning),
          };
        });
      })
      .catch((reason: Error) => { if (!cancelled) setError(reason.message); });
    return () => { cancelled = true; };
  }, [form.provider, form.model, form.agent]);

  useEffect(() => {
    if (!job || ["completed", "failed", "error"].includes(job.status)) return;
    const timer = window.setInterval(() => {
      getJson<RunJob>(`/jobs/${encodeURIComponent(job.job_id)}`)
        .then(setJob)
        .catch((reason: Error) => setError(reason.message));
    }, 1200);
    return () => window.clearInterval(timer);
  }, [job]);

  const update = (key: keyof LaunchForm, value: string) => setForm((current) => ({ ...current, [key]: value }));

  // Switching Task re-applies that Task's own requirements. Carrying the
  // previous Task's MCP or sandbox profile into a different Task starts a run
  // whose tools do not match what the Task declares.
  const changeTask = (value: string) => {
    const declared = taskExecution(registry, value);
    setForm((current) => ({
      ...current,
      task_id: value,
      agent: declared.agent || current.agent,
      mcp_profile: declared.mcp_profile || current.mcp_profile,
      sandbox_profile: declared.sandbox_profile || current.sandbox_profile,
    }));
  };
  // Only chat-capable models can act as an Agent; an image model would start a
  // run that fails once the container is already up.
  const modelOptions = agentModels(models?.models || []);
  const excludedModels = nonAgentModels(models?.models || []);
  const selectedAgent = (registry?.agents || []).find((entry) => entry.id === form.agent);
  const agentNote = selectedAgent?.capabilities?.length
    ? `可用能力：${selectedAgent.capabilities.join("、")}`
    : "";
  // A field with one option is not a decision, so it is hidden rather than
  // shown; the resolved value still appears in the plan preview.
  const mcpProfiles = (registry?.mcp || []).map((entry) => entry.id);
  const sandboxProfiles = (registry?.sandboxes || []).map((entry) => entry.id);
  const resolvedOnly = [
    !isMeaningfulChoice(mcpProfiles) ? `MCP: ${form.mcp_profile || "默认"}` : "",
    !isMeaningfulChoice(sandboxProfiles) ? `Sandbox: ${form.sandbox_profile || "默认"}` : "",
  ].filter(Boolean);
  const advancedSummary = resolvedOnly.length
    ? `（${resolvedOnly.join(" · ")}，无需选择）`
    : "（MCP / Sandbox）";
  // Why the dialog cannot offer a run, when that is the case. Checked in the
  // order the operator would hit it: no Task to test, then no way to reach a
  // model.
  const emptyReason = !registry
    ? null
    : !(registry.tasks || []).length
      ? {
          title: "仓库里还没有 Task",
          detail: "评测需要一个 Task Bundle 来定义测试内容和验收标准。",
          hint: "uv run ai-native-evals task new my-task",
        }
      : !providers.some((entry) => entry.configured)
        ? {
            title: "没有可用的模型提供商",
            detail: "所有 provider 都缺少凭据文件，无法调用任何模型。",
            hint: "复制 config/.env.example 为 config/.env.local 并填入 API Key",
          }
        : null;
  const changeProvider = (value: string) => {
    const next = providers.find((entry) => entry.id === value);
    setForm((current) => ({
      ...current,
      provider: value,
      model: "",
      reasoning_effort: next?.default_reasoning || "",
    }));
  };
  const modelsNote = modelsBusy
    ? "正在向提供商查询可用模型…"
    : models?.source === "provider"
      ? `来自提供商的 /v1/models（${modelOptions.length} 个可用于 Agent${excludedModels.length ? `，已排除 ${excludedModels.length} 个图片/其他模型` : ""}）`
      : models?.source === "declared"
        ? `无法查询提供商（${models.error || "未知原因"}），显示配置中声明的模型`
        : models?.source === "unconfigured"
          ? "该提供商尚未配置，无法查询模型"
          : "";
  const reasoningNote = !form.model
    ? ""
    : levels.length
      ? "可选值由提供商与该 Agent 的能力取交集"
      : "该模型未声明任何推理强度";
  const compactForm = () => Object.fromEntries(Object.entries(form).filter(([, value]) => value !== ""));
  const preview = async () => {
    setBusy(true); setError(""); setBlockers(null);
    try {
      const result = await postJson<RunPlan>("/run-plans", compactForm());
      setPlan(result);
      // Gate the launch on the environment: a missing image or a stopped Docker
      // is knowable now, and finding out here costs seconds instead of a failed
      // container start and a half-written run directory.
      const selection = Object.fromEntries(
        Object.entries({ ...compactForm(), task_id: form.task_id }).filter(([, value]) => value !== ""),
      );
      const check = await awaitPreflight(selection);
      const blockers = blockingChecks(check);
      if (blockers.length > 0) {
        setBlockers(blockers);
        setError(
          check.status === "error"
            ? `环境检查未完成：${check.error || "未知原因"}`
            : blockerSummary(blockers),
        );
        return;
      }
      setStep("preview");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  };
  const execute = async () => {
    if (!plan) return;
    setBusy(true); setError("");
    try {
      const result = await postJson<RunJob>(`/run-plans/${encodeURIComponent(plan.plan_id)}/execute`, {});
      setJob(result); setStep("running");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  };
  const closeOnBackdrop = (event: MouseEvent<HTMLDivElement>) => { if (event.target === event.currentTarget && !busy && !(job && !["completed", "failed", "error"].includes(job.status))) onClose(); };
  const terminal = job && ["completed", "failed", "error"].includes(job.status);
  return <div className="modal-backdrop launch-backdrop" onMouseDown={closeOnBackdrop}><div className="launch-dialog" role="dialog" aria-modal="true" aria-labelledby="launch-title">
    <div className="launch-header"><div><div className="eyebrow">NEW EVALUATION</div><h2 id="launch-title">Run a configured test</h2><p>选择已有配置，先预览解析结果，再在 Docker Sandbox 中执行。</p></div><button className="close-button" onClick={onClose} disabled={busy && !terminal} aria-label="关闭"><Icon name="x" size={18} /></button></div>
    <div className="launch-steps"><span className={step === "configure" ? "active" : "done"}><b>01</b>Configure</span><i /><span className={step === "preview" ? "active" : step === "running" ? "done" : ""}><b>02</b>Review plan</span><i /><span className={step === "running" ? "active" : ""}><b>03</b>Execute</span></div>
    {error && <div className="launch-error"><Icon name="alert" size={16} /><span>{error}</span></div>}
    {blockers && blockers.length > 0 && <div className="launch-blockers">
      {blockers.map((item) => <div className="launch-blocker" key={item.name}>
        <span className="launch-blocker-mark" aria-hidden="true">×</span>
        <div>
          <strong>{item.name}</strong>
          {item.check.detail && <p>{item.check.detail}</p>}
          {item.check.hint && <p className="launch-blocker-hint">→ {item.check.hint}</p>}
        </div>
      </div>)}
      <p className="launch-blocker-foot">在「Environment」页可以看到完整检查结果。</p>
    </div>}
    {loading ? <div className="launch-loading"><div /><div /><div /><div /></div> : emptyReason ? <>
      {/* Nothing to configure: explaining why beats presenting controls that
          cannot be answered. */}
      <div className="launch-empty">
        <Icon name="alert" size={22} />
        <div>
          <strong>{emptyReason.title}</strong>
          <p>{emptyReason.detail}</p>
          <p className="launch-empty-hint">→ {emptyReason.hint}</p>
        </div>
      </div>
      <div className="launch-footer"><button className="button ghost" onClick={onClose}>关闭</button></div>
    </> : step === "configure" ? <>
      <div className="launch-form">
        <div className="form-section"><div className="form-section-title"><span>Task</span><small>测试内容和 TestPlan 来自任务包</small></div><SelectField label="Task Bundle" value={form.task_id} onChange={changeTask} options={(registry?.tasks || []).map((entry) => [entry.id, `${entry.id}${entry.checks != null ? ` · ${entry.checks} checks` : ""}`] as [string, string])} /></div>
        <div className="form-section"><div className="form-section-title"><span>执行者</span><small>用哪个 Agent 完成这个 Task</small></div><div className="form-grid">
          <SelectField label="Agent" value={form.agent} onChange={(value) => update("agent", value)} options={(registry?.agents || []).map((entry) => [entry.id, agentLabel(entry)] as [string, string])} disabled={!registry?.agents?.length} />
        </div>
        {agentNote && <div className="form-hint"><small>{agentNote}</small></div>}
        </div>
        <div className="form-section"><div className="form-section-title"><span>模型</span><small>选择提供商、模型和推理强度</small></div><div className="form-grid">
          <ProviderField value={form.provider} onChange={changeProvider} providers={providers} disabled={!providers.some((entry) => entry.configured)} />
          <SelectField label="Model" value={form.model} onChange={(value) => update("model", value)} options={modelOptions.map((entry) => [entry.id, entry.name === entry.id ? entry.id : `${entry.name} · ${entry.id}`] as [string, string])} placeholder={modelsBusy ? "加载中…" : modelOptions.length ? "选择模型" : "该提供商没有可用于 Agent 的模型"} disabled={!form.provider || modelsBusy || modelOptions.length === 0} />
          <SelectField label="Reasoning" value={form.reasoning_effort} onChange={(value) => update("reasoning_effort", value)} options={levels.map((value) => [value, value] as [string, string])} placeholder={form.model ? "该模型没有可选强度" : "先选择模型"} disabled={levels.length === 0} />
        </div>
        {(modelsNote || reasoningNote) && <div className="form-hint"><small>{modelsNote}{modelsNote && reasoningNote ? " · " : ""}{reasoningNote}</small></div>}
        </div>
        <div className="form-section"><button type="button" className="advanced-toggle" onClick={() => setShowAdvanced((value) => !value)} aria-expanded={showAdvanced}><Icon name="chevron" size={14} />高级选项{advancedSummary && <small>{advancedSummary}</small>}</button>
          {showAdvanced && <div className="form-grid advanced-grid">
            {isMeaningfulChoice(mcpProfiles) && <SelectField label="MCP" value={form.mcp_profile} onChange={(value) => update("mcp_profile", value)} options={mcpProfiles.map((id) => [id, id] as [string, string])} />}
            {isMeaningfulChoice(sandboxProfiles) && <SelectField label="Sandbox" value={form.sandbox_profile} onChange={(value) => update("sandbox_profile", value)} options={sandboxProfiles.map((id) => [id, id] as [string, string])} />}
          </div>}
          {showAdvanced && !isMeaningfulChoice(mcpProfiles) && !isMeaningfulChoice(sandboxProfiles) && (
            <div className="form-hint"><small>没有可选项：当前只有一种 MCP 与 Sandbox 配置，已在预览中显示最终取值。</small></div>
          )}
        </div>
      </div>
      <div className="launch-note"><Icon name="layers" size={16} /><div><strong>先预览，不会立即运行</strong><span>Agent、协议、MCP、Sandbox 和 Evaluator 会在下一步显示最终解析值。</span></div></div><div className="launch-footer"><button className="button ghost" onClick={onClose}>取消</button><button className="button primary" disabled={busy || !form.task_id} onClick={preview}>{busy ? "解析中…" : "预览运行方案"}<Icon name="arrow" size={15} /></button></div>
    </> : step === "preview" && plan ? <>
      <div className="plan-identity"><div><small>RUN PLAN</small><strong>{plan.resolved.task_id}</strong></div><code>{plan.run_id}</code></div><div className="resolved-grid"><ResolvedValue label="Agent" value={plan.resolved.agent} tone="blue" /><ResolvedValue label="Provider" value={plan.resolved.provider || plan.resolved.model_provider || "—"} tone="amber" /><ResolvedValue label="Model" value={plan.resolved.model} tone="violet" /><ResolvedValue label="Protocol" value={plan.resolved.protocol || "—"} tone="cyan" /><ResolvedValue label="Reasoning" value={plan.resolved.reasoning_effort || "—"} tone="amber" /><ResolvedValue label="MCP" value={plan.resolved.mcp_profile} tone="amber" /><ResolvedValue label="Sandbox" value={plan.resolved.sandbox_profile} tone="cyan" /><ResolvedValue label="Evaluator" value={plan.resolved.evaluator_agent || "—"} tone="mint" /><ResolvedValue label="Snapshot" value={plan.resolved.snapshot_mode || "—"} tone="default" /></div><section className="plan-section"><div className="form-section-title"><span>TestPlan</span><small>{plan.checks.length} checks · {plan.resolved.resource_specs?.length || 0} resources</small></div><div className="plan-checks">{plan.checks.map((check, index) => <div className="plan-check" key={String(check.id || index)}><span>{String(index + 1).padStart(2, "0")}</span><strong>{String(check.id || "check")}</strong><PhaseTag phase={String(check.phase || "diagnostic")} /><small>{String(check.evaluator || "—")}</small></div>)}</div></section><details className="plan-prompt"><summary>查看 Task Prompt</summary><pre>{plan.resolved.task_prompt || "No prompt preview."}</pre></details><div className="launch-note caution"><Icon name="alert" size={16} /><div><strong>确认后会启动真实 Docker 评测</strong><span>Agent 只使用本次解析的配置，不会修改你其他 Codex 或 DSH 会话。</span></div></div><div className="launch-footer"><button className="button ghost" onClick={() => setStep("configure")}>返回修改</button><button className="button primary" disabled={busy} onClick={execute}>{busy ? "启动中…" : "确认并开始测试"}<Icon name="arrow" size={15} /></button></div>
    </> : <>
      <div className="job-state"><div className={`job-orb ${terminal ? (job?.status === "completed" && job?.decision !== "fail" ? "done" : "failed") : "active"}`}><Icon name={terminal ? (job?.status === "completed" && job?.decision !== "fail" ? "check" : "alert") : "pulse"} size={27} /></div><div><div className="eyebrow">{terminal ? "RUN FINISHED" : "RUN IN PROGRESS"}</div><h3>{job?.status === "preparing" ? "正在准备 Workspace…" : job?.status === "prepared" ? "Workspace 已准备，正在启动 Docker…" : job?.status === "running" ? "Agent 正在执行…" : job?.status === "evaluating" ? "Evaluator 正在评分…" : job?.status === "completed" && job?.decision === "fail" ? "执行完成，但验收未通过" : job?.status === "completed" ? "测试完成" : job?.status === "failed" ? "Agent 执行失败" : job?.status === "error" ? "运行出错" : "等待启动…"}</h3><p>{job?.error || (job?.decision ? `Decision: ${job.decision} · Run ID: ${job.run_id}` : `Run ID: ${job?.run_id || plan?.run_id || "—"}`)}</p></div></div><div className="job-progress"><span className={job && ["preparing", "prepared", "running", "evaluating", "completed"].includes(job.status) ? "done" : ""}>Workspace</span><i /><span className={job && ["prepared", "running", "evaluating", "completed"].includes(job.status) ? "done" : ""}>Docker Agent</span><i /><span className={job && ["evaluating", "completed"].includes(job.status) ? "done" : ""}>Evaluation</span><i /><span className={job?.status === "completed" ? "done" : ""}>Verdict</span></div><div className="launch-footer">{terminal && job?.run_id && <button className="button primary" onClick={() => { onClose(); onOpenRun(job.run_id); }}>打开 Run Detail <Icon name="arrow" size={15} /></button>}{!terminal && <button className="button ghost" onClick={onClose}>后台运行</button>}{terminal && <button className="button ghost" onClick={onClose}>关闭</button>}</div>
    </>}
  </div></div>;
}

function SelectField({ label, value, onChange, options, placeholder, disabled }: { label: string; value: string; onChange: (value: string) => void; options: Array<[string, string]>; placeholder?: string; disabled?: boolean }) {
  // The placeholder is only an option while nothing is chosen. Keeping it once a
  // value exists leaves a selectable blank whose only effect is clearing the
  // field, and its text shows up first in the closed control.
  const entries: Array<[string, string]> = placeholder && !value ? [["", placeholder], ...options] : options;
  return <label className="select-field"><span>{label}</span><select value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>{entries.map(([optionValue, optionLabel]) => <option key={`${label}-${optionValue}`} value={optionValue}>{optionLabel}</option>)}</select><Icon name="chevron" size={14} /></label>;
}

/** A dropdown whose unusable entries cannot be picked, only seen. */
function ProviderField({ value, onChange, providers, disabled }: { value: string; onChange: (value: string) => void; providers: ProviderSummary[]; disabled?: boolean }) {  return <label className="select-field"><span>Provider</span>
    <select value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
      {!value && <option value="">选择提供商</option>}
      {providers.map((entry) => <option key={entry.id} value={entry.id} disabled={!entry.configured}>
        {entry.configured ? entry.name : `${entry.name}（未配置）`}
      </option>)}
    </select>
    <Icon name="chevron" size={14} />
  </label>;
}

function ResolvedValue({ label, value, tone }: { label: string; value: string; tone: string }) {
  return <div className={`resolved-value ${tone}`}><small>{label}</small><strong>{value || "—"}</strong></div>;
}

/** What an Agent profile is for, so the picker does not show a bare id. */
function agentLabel(entry: { id: string; label?: string; summary?: string }): string {
  const name = entry.label || entry.id;
  return name === entry.id ? entry.id : `${name} · ${entry.id}`;
}
