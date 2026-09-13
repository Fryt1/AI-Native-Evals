/**
 * The launch gate: which environment findings stop a run from starting.
 *
 * Kept apart from the components so the rule can be tested directly. The rule
 * itself is the interesting part:
 *
 *   missing  verified absent -- blocks, because the run would fail anyway
 *   unknown  could not be checked -- does NOT block, because refusing to launch
 *            on an unreadable probe makes a working machine look broken
 *
 * The operator still sees `unknown` findings on the Environment page; they
 * simply are not treated as faults.
 */
import type { PreflightCheck, PreflightRun } from "./types";

export type LaunchBlocker = { name: string; check: PreflightCheck };

/** Human labels, so a dialog does not show raw check ids. */
const LABELS: Record<string, string> = {
  python: "Python 运行时",
  uv: "uv",
  runs_root: "运行目录",
  provider_credentials: "上游凭据",
  wsl: "WSL",
  docker: "Docker",
  images: "容器镜像",
  node: "Node",
  pnpm: "pnpm",
  console_dependencies: "前端依赖",
  mcp_hosts: "MCP 宿主",
  run_spec: "Run 参数",
};

export function checkLabel(name: string): string {
  return LABELS[name] || name;
}

export function blockingChecks(run: PreflightRun | null): LaunchBlocker[] {
  const checks = run?.report?.checks;
  if (!checks) return [];
  return Object.entries(checks)
    .filter(([, check]) => check.status === "missing")
    .map(([name, check]) => ({ name: checkLabel(name), check }));
}

/** Findings that were neither confirmed nor denied; shown, never enforced. */
export function unknownChecks(run: PreflightRun | null): LaunchBlocker[] {
  const checks = run?.report?.checks;
  if (!checks) return [];
  return Object.entries(checks)
    .filter(([, check]) => check.status === "unknown")
    .map(([name, check]) => ({ name: checkLabel(name), check }));
}

/** A one-line explanation for a blocked launch. */
export function blockerSummary(blockers: LaunchBlocker[]): string {
  return `这台机器还不能运行该评测：${blockers.map((item) => item.name).join("、")}`;
}
