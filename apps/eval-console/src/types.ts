export type RunSummary = {
  run_id: string;
  task_id: string;
  agent_id: string;
  model?: string | null;
  status?: string | null;
  decision?: string | null;
  outcome_score?: number | null;
  quality_score?: number | null;
  process_score?: number | null;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  failed_check_count?: number;
  error_count?: number;
  artifact_count?: number;
  run_dir?: string;
};

export type Check = {
  check_id: string;
  phase?: string | null;
  evaluator?: string | null;
  annotator_kind?: string | null;
  status?: string | null;
  passed?: boolean | null;
  score?: number | null;
  weight?: number | null;
  required?: boolean | null;
  depends_on?: string[];
  explanation?: string | null;
  details?: Record<string, unknown>;
  evidence_refs?: string[];
  evaluator_run_id?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
};

export type Artifact = {
  artifact_id: string;
  role: string;
  relative_path: string;
  kind: string;
  mime_type: string;
  size: number;
  sha256?: string | null;
  created_by?: string | null;
  previewable?: boolean;
};

export type AgentEvent = {
  schema_version?: number;
  event_id: string;
  run_id: string;
  seq: number;
  occurred_at?: string | null;
  type: string;
  actor: { kind: string; id: string };
  turn_id?: string | null;
  step_id?: string | null;
  parent_event_id?: string | null;
  call_id?: string | null;
  status?: string | null;
  duration_ms?: number | null;
  summary: string;
  payload?: unknown;
  artifact_refs?: string[];
  source?: { adapter?: string; source_type?: string };
  legacy?: boolean;
};

export type RunDetail = {
  summary: RunSummary;
  configuration: Record<string, unknown>;
  scores: { outcome: number | null; quality: number | null; process: number | null };
  decision?: string | null;
  checks: Check[];
  trace_summary: Record<string, unknown>;
  artifacts: Artifact[];
  evaluator_runs: Array<Record<string, unknown>>;
  workspace: { sections: Array<{ id: string; label: string; relative_path: string }> };
  references: Record<string, string>;
};

export type EventsResponse = {
  run_id: string;
  events: AgentEvent[];
  has_more: boolean;
  next_after_seq: number;
};

export type Comparison = {
  comparison_id: string;
  task_id?: string | null;
  created_at?: string | null;
  run_count: number;
  output_dir?: string | null;
};

export type ComparisonDetail = {
  comparison_id: string;
  created_at?: string | null;
  invariant: Record<string, unknown>;
  runs: Array<{ agent?: string; model?: string; run_id: string; status?: string; evaluation?: Record<string, unknown>; summary?: RunSummary | null }>;
  check_matrix?: Array<{ check_id: string; phase?: string | null; values: Array<{ run_id: string; agent?: string | null; status?: string | null; score?: number | null }> }>;
  output_dir?: string | null;
  fairness: { fair: boolean; message: string };
};

export type RunPlan = {
  plan_id: string;
  run_id: string;
  created_at: string;
  request: Record<string, unknown>;
  resolved: {
    task_id: string;
    agent: string;
    model: string;
    model_profile?: string | null;
    model_provider?: string | null;
    provider?: string | null;
    protocol?: string | null;
    reasoning_effort?: string | null;
    mcp_profile: string;
    sandbox_profile: string;
    snapshot_mode?: string | null;
    evaluator_agent?: string | null;
    task_prompt?: string;
    agent_profile?: Record<string, unknown>;
    evaluator_agent_profile?: Record<string, unknown>;
    sandbox?: Record<string, unknown>;
    resource_specs?: unknown[];
  };
  test_plan: Record<string, unknown>;
  checks: Array<Record<string, unknown>>;
};

export type RunJob = {
  job_id: string;
  plan_id: string;
  run_id: string;
  status: "queued" | "preparing" | "prepared" | "running" | "evaluating" | "completed" | "failed" | "error";
  decision?: string | null;
  error?: string | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
};

export type ProviderSummary = {
  id: string;
  name: string;
  wire_api: string[];
  configured: boolean;
  default_reasoning: string;
  declared_models: string[];
  fallback_reasoning_levels: string[];
};

/** Whether a model can act as an Agent, or exists for another purpose. */
export type ModelCapability = "chat" | "image" | "other";

export type ProviderModel = {
  id: string;
  name: string;
  reasoning_levels: string[];
  /** Absent on older payloads; treated as "chat" so nothing disappears. */
  capability?: ModelCapability;
};

export type ProviderModelListing = {
  provider: string;
  source: "provider" | "declared" | "unconfigured";
  error?: string | null;
  models: ProviderModel[];
};

export type ReasoningLevels = {
  provider: string;
  model: string;
  agent: string;
  source: "provider" | "declared";
  levels: string[];
};

/** One environment fact. `unknown` means "could not check", never "absent". */
export type PreflightCheck = {
  status: "ok" | "missing" | "unknown";
  ok: boolean;
  /** How the finding was obtained: read from files, or from a started container. */
  level?: "static" | "smoke";
  detail?: string;
  hint?: string;
};

export type PreflightReport = {
  ready: boolean;
  checks: Record<string, PreflightCheck>;
  missing_required: string[];
  warnings?: string[];
  unknown?: string[];
};

export type PreflightRun = {
  check_id: string;
  status: "running" | "completed" | "error";
  selector: Record<string, unknown>;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  report?: PreflightReport | null;
  error?: string | null;
};

/**
 * Verifying one Agent profile.
 *
 * `static` inspects the profile and its image; `smoke` starts a real container
 * and asks the Agent a question. Only the second proves the pieces fit together,
 * which is why both exist.
 */
export type AgentCheckResult = {
  check_id: string;
  agent: string;
  level: "static" | "smoke";
  status: "running" | "completed" | "error";
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  report?: {
    agent: string;
    image: string;
    level: string;
    usable: boolean;
    checked_at: string;
    checks: Record<string, PreflightCheck>;
    failed: string[];
    unknown: string[];
  } | null;
  error?: string | null;
};

/**
 * Start an Agent check and wait for its result.
 *
 * The smoke level starts a container, so the wait is generous; a static check
 * returns in about a second.
 */
export async function awaitAgentCheck(
  agent: string,
  level: "static" | "smoke",
  {
    version = "",
    timeoutMs = 240_000,
    intervalMs = 900,
  }: { version?: string; timeoutMs?: number; intervalMs?: number } = {},
): Promise<AgentCheckResult> {
  const body: Record<string, string> = { level };
  // A version runs that build rather than the profile's default, so the verdict
  // describes a specific one.
  if (version) body.version = version;
  let run = await postJson<AgentCheckResult>(`/agents/${encodeURIComponent(agent)}/check`, body);
  const deadline = Date.now() + timeoutMs;
  while (run.status === "running") {
    if (Date.now() > deadline) {
      return { ...run, status: "error", error: "Agent 检查超时" };
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
    run = await getJson<AgentCheckResult>(`/agents/checks/${encodeURIComponent(run.check_id)}`);
  }
  return run;
}

/**
 * Start an environment check and wait for its result.
 *
 * The endpoint is asynchronous because probing Docker and host services takes
 * seconds; a caller that must decide before launching (the run dialog) needs the
 * settled value, so the polling lives here rather than in every caller.
 */
export async function awaitPreflight(
  selector: Record<string, unknown> = {},
  { timeoutMs = 90_000, intervalMs = 800 }: { timeoutMs?: number; intervalMs?: number } = {},
): Promise<PreflightRun> {
  let run = await postJson<PreflightRun>("/preflight", selector);
  const deadline = Date.now() + timeoutMs;
  while (run.status === "running") {
    if (Date.now() > deadline) {
      // Report the timeout as a result, not an exception: the caller decides
      // whether an unverifiable environment blocks it.
      return { ...run, status: "error", error: "环境检查超时" };
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
    run = await getJson<PreflightRun>(`/preflight/${encodeURIComponent(run.check_id)}`);
  }
  return run;
}

export type RegistryEntry = { id: string; kind: string; path: string; label?: string; summary?: string; fields?: string[]; checks?: number; provider_id?: string; model_id?: string; workdir?: string; model_ids?: string[]; model_profiles?: string[]; execution?: Record<string, string>; agent_version?: string; image?: string; available_versions?: string[] };
export type RegistryKey = "tasks" | "agents" | "providers" | "models" | "mcp" | "sandboxes" | "presets";
export type Registry = Record<RegistryKey, RegistryEntry[]> & { defaults?: Record<string, string> };

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const payload = (await response.json()) as { detail?: string };
      detail = payload.detail || detail;
    } catch {
      // Keep the HTTP status as the useful error.
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { signal });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: string };
      detail = body.detail || detail;
    } catch {
      // Keep the HTTP status as the useful error.
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export function numericScore(value: unknown): number | null {
  // A real 0 is a score, not a missing value: never let `0 || null` erase it.
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function formatDuration(value?: number | null): string {
  if (value == null) return "—";
  if (value < 1000) return `${value} ms`;
  const seconds = value / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

export function formatTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
}

export function scoreLabel(value?: number | null): string {
  return value == null ? "未评测" : value.toFixed(2);
}
