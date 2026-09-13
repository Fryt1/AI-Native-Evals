/**
 * Environment check: can this machine actually run an evaluation?
 *
 * The check probes Docker, credentials, images and host services, so it runs in
 * the background on the server and is polled here. Two states are shown
 * distinctly on purpose:
 *
 *   missing  verified absent -- blocks a run, and needs fixing
 *   unknown  could not be determined -- never blocks, and is not an error
 *
 * Collapsing `unknown` into a failure would tell an operator their machine is
 * broken because a probe could not run, which is the opposite of helpful.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { ErrorState, Icon, SectionHeader } from "./components";
import {
  awaitAgentCheck,
  getJson,
  postJson,
  type AgentCheckResult,
  type PreflightCheck,
  type PreflightReport,
  type PreflightRun,
  type RegistryEntry,
} from "./types";

/** Human labels and the order an operator should read them in. */
const CHECK_LABELS: Record<string, { label: string; hint: string }> = {
  python: { label: "Python", hint: "运行评测框架本身" },
  uv: { label: "uv", hint: "Python 依赖与命令入口" },
  runs_root: { label: "运行目录", hint: "Run 现场写在哪里，是否可写" },
  provider_credentials: { label: "上游凭据", hint: "模型提供商的 base_url 与 key" },
  wsl: { label: "WSL", hint: "Docker 通过 WSL 访问" },
  docker: { label: "Docker", hint: "容器运行时是否可用" },
  agent_images: { label: "Agent 镜像", hint: "每个 Agent profile 的镜像是否已构建" },
  images: { label: "本次运行镜像", hint: "所选 Task 需要的镜像" },
  node: { label: "Node", hint: "仅构建 Console 前端需要" },
  pnpm: { label: "pnpm", hint: "仅构建 Console 前端需要" },
  console_dependencies: { label: "前端依赖", hint: "apps/eval-console/node_modules" },
  mcp_hosts: { label: "MCP 宿主", hint: "Task 声明的宿主服务是否可达" },
  run_spec: { label: "Run 参数", hint: "Task / Agent / 模型的选择是否完整" },
};

/**
 * Checks that hold for the machine itself, whatever Task you later choose.
 * Everything else depends on a selection, so reporting it before one exists
 * would be reporting a fact nobody has asked about yet.
 */
const MACHINE_CHECKS = [
  "python", "uv", "runs_root", "provider_credentials",
  "wsl", "docker", "agent_images",
];

/** Checks that only mean something once a Task has been chosen. */
const RUN_CHECKS = ["images", "mcp_hosts", "run_spec"];

/** Tooling needed only to build the Console itself. */
const CONSOLE_CHECKS = ["node", "pnpm", "console_dependencies"];

const CHECK_ORDER = [...MACHINE_CHECKS, ...RUN_CHECKS, ...CONSOLE_CHECKS];

function orderIndex(name: string): number {
  const index = CHECK_ORDER.indexOf(name);
  return index < 0 ? 99 : index;
}

/** Which group a check belongs to, for the section headings. */
export function checkGroup(name: string): "machine" | "run" | "console" {
  if (RUN_CHECKS.includes(name)) return "run";
  if (CONSOLE_CHECKS.includes(name)) return "console";
  return "machine";
}

function labelFor(name: string) {
  return CHECK_LABELS[name] || { label: name, hint: "" };
}

function statusLabel(check: PreflightCheck): string {
  if (check.status === "ok") return "ok";
  if (check.status === "missing") return "MISSING";
  return "unknown";
}

function CheckRow({ name, check }: { name: string; check: PreflightCheck }) {
  const { label, hint } = labelFor(name);
  const value = check.detail || hint;
  return (
    <div className={`preflight-row ${check.status}`}>
      <span className="preflight-mark" aria-hidden="true">
        {check.status === "ok" ? "✓" : check.status === "missing" ? "×" : "?"}
      </span>
      <div className="preflight-body">
        <div className="preflight-head">
          <strong>{label}</strong>
          {/* Text, not colour alone: the status must survive a greyscale screen. */}
          <span className={`preflight-status ${check.status}`}>{statusLabel(check)}</span>
        </div>
        <p className="preflight-detail">{value}</p>
        {check.hint && check.status !== "ok" && <p className="preflight-hint">→ {check.hint}</p>}
      </div>
    </div>
  );
}

/**
 * Verify each Agent profile.
 *
 * Two levels, because they answer different questions. `static` says the
 * profile is well-formed and its image exists. `smoke` starts a real container
 * and asks the Agent a question -- the only check that proves the entrypoint,
 * the credentials, and the model round trip actually work together.
 *
 * Manual on purpose: a smoke run costs several seconds per Agent, so it is not
 * something to pay for on every page visit.
 */
function AgentsSection({ agents }: { agents: RegistryEntry[] }) {
  const [results, setResults] = useState<Record<string, AgentCheckResult>>({});
  const [running, setRunning] = useState<Record<string, "static" | "smoke">>({});
  const [error, setError] = useState<string | null>(null);
  // Which version each Agent row is pointed at. Two versions of one Agent
  // behave differently, so the choice is per Agent and kept independently of
  // the verdict: switching versions does not carry the previous verdict over.
  const [chosen, setChosen] = useState<Record<string, string>>({});

  // Pick up verdicts from earlier in this session so a reload does not lose them.
  useEffect(() => {
    let cancelled = false;
    getJson<{ items: Record<string, AgentCheckResult> }>("/agents/checks")
      .then((data) => { if (!cancelled) setResults(data.items || {}); })
      .catch(() => { /* No prior checks is a normal first visit. */ });
    return () => { cancelled = true; };
  }, []);

  const versionOf = (entry: RegistryEntry) =>
    chosen[entry.id] || entry.agent_version || (entry.available_versions || [])[0] || "";

  const setVersion = (agentId: string, version: string) => {
    setChosen((current) => ({ ...current, [agentId]: version }));
    // The previous verdict described a different build, so it is dropped rather
    // than left on screen as if it still applied.
    setResults((current) => {
      const next = { ...current };
      delete next[agentId];
      return next;
    });
  };

  // Results are keyed by Agent and version, because one Agent now has several.
  const resultKey = (agentId: string, version: string) => (version ? `${agentId}@${version}` : agentId);

  const verify = useCallback(async (agent: string, version: string, level: "static" | "smoke") => {
    setRunning((current) => ({ ...current, [agent]: level }));
    setError(null);
    try {
      const result = await awaitAgentCheck(agent, level, { version });
      setResults((current) => ({ ...current, [resultKey(agent, version)]: result }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setRunning((current) => {
        const next = { ...current };
        delete next[agent];
        return next;
      });
    }
  }, []);

  if (!agents.length) {
    return (
      <section className="panel preflight-agents">
        <div className="preflight-group">
          <h3>Agent</h3>
          <p className="preflight-group-note">仓库里还没有定义任何 Agent profile。</p>
        </div>
      </section>
    );
  }

  return (
    <section className="panel preflight-agents">
      <div className="preflight-group">
        <h3>Agent</h3>
        <p className="preflight-group-note">
          每个 Agent 可以单独验证。<strong>静态检查</strong>看 profile 与镜像是否齐备；
          <strong>真实测试</strong>会启动一个容器并让 Agent 回答一句话 ——
          这是唯一能证明「入口脚本、凭据、模型链路」真的通的检查。
        </p>
      </div>
      {error && <div className="launch-error"><Icon name="alert" size={16} /><span>{error}</span></div>}
      <div className="agent-check-list">
        {agents.map((entry) => {
          const versions = entry.available_versions || [];
          const version = versionOf(entry);
          const result = results[resultKey(entry.id, version)];
          const level = running[entry.id];
          const report = result?.report ?? null;
          const state = level ? "running" : report ? (report.usable ? "usable" : "broken") : "unknown";
          // What was actually verified, in the row header. Without it every row
          // looked alike, and the operator could not tell a checked Agent from
          // an untouched one without reading the whole panel.
          const badge = level
            ? { text: level === "smoke" ? "测试中…" : "检查中…", tone: "running" }
            : report
              ? report.level === "smoke"
                ? { text: report.usable ? "真实测试通过" : "真实测试未通过", tone: report.usable ? "ok" : "bad" }
                : { text: report.usable ? "仅静态检查通过" : "静态检查未通过", tone: report.usable ? "partial" : "bad" }
              : { text: "未验证", tone: "none" };
          return (
            <div className={`agent-check-row ${state}`} key={entry.id}>
              <div className="agent-check-head">
                <div className="agent-check-title">
                  <strong>{entry.label || entry.id}</strong>
                  {/* A version is a choice, not a label: the same Agent at two
                      versions behaves differently, and comparing them is the
                      point of building both. Shown as a picker when there is
                      something to pick, and as plain text when there is not. */}
                  {versions.length > 1 ? (
                    <select
                      className="agent-version-select"
                      value={version}
                      disabled={Boolean(level)}
                      onChange={(event) => setVersion(entry.id, event.target.value)}
                      aria-label={`${entry.label || entry.id} 版本`}
                    >
                      {versions.map((item: string) => (
                        <option key={item} value={item}>{item}</option>
                      ))}
                    </select>
                  ) : (
                    version && (
                      /* Why there is no picker: on hover, and in the accessible
                         name, rather than as a line of text. Three Agents each
                         saying "only this version is built" is the same sentence
                         three times, and it teaches nothing the second time. The
                         state stays available to a screen reader and on touch,
                         where a title attribute alone would not reach it. */
                      <code
                        className="agent-version"
                        title={
                          versions.length === 1
                            ? `仅此一版已构建：${version}`
                            : "还没有构建任何版本"
                        }
                        aria-label={
                          versions.length === 1
                            ? `版本 ${version}，仅此一版已构建`
                            : "还没有构建任何版本"
                        }
                      >
                        v{version}
                      </code>
                    )
                  )}
                  <span className={`agent-badge ${badge.tone}`}>{badge.text}</span>
                </div>
                <div className="agent-check-actions">
                  <button className="button small" disabled={Boolean(level)}
                    onClick={() => verify(entry.id, version, "static")}>
                    {level === "static" ? "检查中…" : "静态检查"}
                  </button>
                  <button className="button small primary" disabled={Boolean(level)}
                    onClick={() => verify(entry.id, version, "smoke")}>
                    {level === "smoke" ? "测试中…" : "真实测试"}
                  </button>
                </div>
              </div>
              {report && (
                <div className="agent-check-body">
                  <span className={`preflight-status ${report.usable ? "ok" : "missing"}`}>
                    {report.level === "smoke" ? "真实测试" : "静态检查"} · {report.usable ? "通过" : "未通过"}
                  </span>
                  {
                    /* Two groups, because the findings are not the same kind of
                       fact: one is read from files, the other from a container
                       that actually started. Listing them together left the
                       operator unable to tell a well-formed profile from a
                       working one. */
                  }
                  {(["static", "smoke"] as const).map((group) => {
                    const names = Object.keys(report.checks).filter(
                      (name) => (report.checks[name].level || "static") === group,
                    );
                    if (!names.length) return null;
                    return (
                      <div className="agent-check-group" key={group}>
                        <div className="agent-check-group-head">
                          <strong>{group === "static" ? "静态检查" : "真实测试"}</strong>
                          <small>
                            {group === "static"
                              ? "读取 profile 与镜像信息，不启动任何东西"
                              : "启动容器并让 Agent 回答一句话"}
                          </small>
                        </div>
                        {names.map((name) => {
                          const check = report.checks[name];
                          return (
                            <div className={`agent-check-item ${check.status}`} key={name}>
                              <span className="agent-check-mark" aria-hidden="true">
                                {check.status === "ok" ? "✓" : check.status === "missing" ? "×" : "?"}
                              </span>
                              <div>
                                <strong>{AGENT_CHECK_LABELS[name] || name}</strong>
                                {check.detail && <p>{check.detail}</p>}
                                {check.hint && check.status !== "ok" && <p className="agent-check-hint">→ {check.hint}</p>}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    );
                  })}
                </div>
              )}
              {result?.error && <p className="agent-check-hint">→ {result.error}</p>}
            </div>
          );
        })}
      </div>
    </section>
  );
}

const AGENT_CHECK_LABELS: Record<string, string> = {
  profile: "Profile 文件",
  adapter: "Adapter",
  image: "镜像",
  version: "版本",
  credentials: "上游凭据",
  container: "容器启动",
  exit: "退出码",
  reply: "Agent 回应",
  smoke: "真实测试",
};

export function PreflightPage() {
  const [run, setRun] = useState<PreflightRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [agents, setAgents] = useState<RegistryEntry[]>([]);

  // The Agent list comes from the registry, so the page shows exactly the
  // profiles this repository defines.
  useEffect(() => {
    let cancelled = false;
    getJson<{ agents: RegistryEntry[] }>("/registry")
      .then((data) => { if (!cancelled) setAgents(data.agents || []); })
      .catch(() => { /* The machine check below reports an unreachable API. */ });
    return () => { cancelled = true; };
  }, []);

  // Poll for the result; the first load also picks up a previous check so a
  // page refresh does not lose it or force a fresh wait.
  const cancelled = useRef(false);
  useEffect(() => {
    cancelled.current = false;
    // `check` is null on a first visit. That is a normal state, and the server
    // says so with 200 rather than 404 -- a 404 would log a console error on
    // every new visitor's page for something that is not a failure.
    getJson<{ check: PreflightRun | null }>("/preflight")
      .then((value) => { if (!cancelled.current && value.check) setRun(value.check); })
      .catch(() => { /* A failed fetch is reported by the run controls. */ });
    return () => { cancelled.current = true; };
  }, []);

  useEffect(() => {
    if (!run || run.status !== "running") return;
    const timer = window.setInterval(() => {
      getJson<PreflightRun>(`/preflight/${encodeURIComponent(run.check_id)}`)
        .then((value) => { if (!cancelled.current) setRun(value); })
        .catch((reason: Error) => { if (!cancelled.current) setError(reason.message); });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [run]);

  const start = useCallback((force: boolean) => {
    setBusy(true);
    setError(null);
    postJson<PreflightRun>("/preflight", { force })
      .then(setRun)
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setBusy(false));
  }, []);

  const report: PreflightReport | null = run?.report || null;
  const running = run?.status === "running";
  const names = report
    ? Object.keys(report.checks).sort((a, b) => orderIndex(a) - orderIndex(b))
    : [];
  const machine = names.filter((name) => checkGroup(name) === "machine");
  const runSpecific = names.filter((name) => checkGroup(name) === "run");
  const consoleChecks = names.filter((name) => checkGroup(name) === "console");

  return (
    <div className="page-shell">
      <SectionHeader
        eyebrow="ENVIRONMENT"
        title="这台机器能不能跑评测"
        action={
          <div className="preflight-actions">
            <button className="button" onClick={() => start(true)} disabled={busy || running}>
              <Icon name="pulse" size={15} />
              {running ? "检查中…" : "重新检查"}
            </button>
          </div>
        }
      />

      <p className="page-lede">
        检查运行一次评测所需的全部前提：工具链、凭据、Docker、镜像与宿主服务。
        任何一项 <code>MISSING</code> 都会阻止 Run 启动；<code>unknown</code> 表示无法判定，
        不会阻止 —— <strong>探测不到不等于东西不存在</strong>。
      </p>

      {/* Agents first: verifying them is independent of the machine-wide check,
          and it is the part an operator most often wants to act on. */}
      <AgentsSection agents={agents} />

      {error && <ErrorState message={error} onRetry={() => start(true)} />}

      {!run && !error && (
        <div className="panel preflight-intro">
          <p>还没有跑过机器体检。它会核对工具链、凭据、Docker 与镜像。</p>
          <button className="button primary" onClick={() => start(false)} disabled={busy}>
            开始体检
          </button>
        </div>
      )}

      {run && (
        <div className={`panel preflight-panel ${report?.ready ? "ready" : ""}`}>
          <div className="preflight-verdict">
            <div className={`verdict-orb ${running ? "running" : report?.ready ? "ready" : "blocked"}`}>
              <Icon name={running ? "pulse" : report?.ready ? "check" : "alert"} size={22} />
            </div>
            <div>
              <h2>
                {running
                  ? "正在检查…"
                  : run.status === "error"
                    ? "检查失败"
                    : report?.ready
                      ? "可以运行评测"
                      : "还不能运行评测"}
              </h2>
              <p>
                {running
                  ? "正在探测 Docker、凭据与宿主服务。"
                  : run.status === "error"
                    ? run.error || "检查过程出错"
                    : report?.ready
                      ? "所有必需项都已通过。"
                      : `缺少：${(report?.missing_required || []).join("、") || "未知"}`}
              </p>
            </div>
          </div>

          {machine.length > 0 && (
            <section className="preflight-group">
              <h3>机器能力</h3>
              <p className="preflight-group-note">与选哪个 Task 无关，这台机器本身的状态。</p>
              <div className="preflight-list">
                {machine.map((name) => <CheckRow key={name} name={name} check={report!.checks[name]} />)}
              </div>
            </section>
          )}

          {runSpecific.length > 0 && (
            <section className="preflight-group">
              <h3>本次运行</h3>
              <p className="preflight-group-note">
                只有选定 Task 后才能核对；未选择时会显示 unknown，那不是故障。
              </p>
              <div className="preflight-list">
                {runSpecific.map((name) => <CheckRow key={name} name={name} check={report!.checks[name]} />)}
              </div>
            </section>
          )}

          {consoleChecks.length > 0 && (
            <section className="preflight-group">
              <h3>Console 前端</h3>
              <p className="preflight-group-note">只影响可视化界面，不影响评测本身。</p>
              <div className="preflight-list">
                {consoleChecks.map((name) => <CheckRow key={name} name={name} check={report!.checks[name]} />)}
              </div>
            </section>
          )}

          {report?.unknown && report.unknown.length > 0 && (
            <p className="preflight-footnote">
              无法判定：{report.unknown.map((n) => labelFor(n).label).join("、")}。
              这些项不会阻止运行。
            </p>
          )}
        </div>
      )}
    </div>
  );
}
