/**
 * The Console's environment check.
 *
 * The tri-state status is the part worth pinning: `unknown` means the probe
 * could not run, and treating it as a failure would tell an operator their
 * machine is broken when it might be perfectly fine.
 */
import { describe, expect, it } from "vitest";
import { formatDuration, numericScore, scoreLabel } from "./types";
import type { PreflightCheck, PreflightReport } from "./types";

describe("console formatting", () => {
  it("formats durations for the run list", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(930)).toBe("930 ms");
    expect(formatDuration(19600)).toBe("19.6 s");
    expect(formatDuration(65000)).toBe("1m 5s");
  });

  it("does not turn an unavailable score into zero", () => {
    expect(scoreLabel(null)).toBe("未评测");
    expect(scoreLabel(0.714)).toBe("0.71");
  });
});

describe("numericScore", () => {
  it("keeps a real zero instead of treating it as missing", () => {
    // `0 || null` used to erase a genuine zero and render it as "not evaluated".
    expect(numericScore(0)).toBe(0);
    expect(numericScore(0.0)).toBe(0);
  });

  it("rejects values that carry no score", () => {
    expect(numericScore(null)).toBeNull();
    expect(numericScore(undefined)).toBeNull();
    expect(numericScore("")).toBeNull();
    expect(numericScore("n/a")).toBeNull();
    expect(numericScore(Number.NaN)).toBeNull();
    expect(numericScore(Number.POSITIVE_INFINITY)).toBeNull();
  });

  it("accepts numeric strings from persisted JSON", () => {
    expect(numericScore("0.75")).toBe(0.75);
    expect(numericScore("0")).toBe(0);
  });
});

describe("preflight report shape", () => {
  const report: PreflightReport = {
    ready: true,
    checks: {
      python: { status: "ok", ok: true, detail: "3.12.1" },
      docker: { status: "unknown", ok: false, detail: "probe failed" },
      images: { status: "missing", ok: false, detail: "agent:local", hint: "build them" },
    },
    missing_required: ["images"],
    warnings: ["run_spec"],
    unknown: ["docker"],
  };

  it("keeps unknown distinct from missing", () => {
    // The distinction drives the whole page: unknown must never read as a fault.
    expect(report.checks.docker.status).toBe("unknown");
    expect(report.checks.images.status).toBe("missing");
    expect(report.unknown).toContain("docker");
    expect(report.missing_required).not.toContain("docker");
  });

  it("carries a hint only where an action exists", () => {
    expect(report.checks.images.hint).toBeTruthy();
    expect(report.checks.python.hint).toBeUndefined();
  });

  it("treats a check without ok as not ok", () => {
    const partial = { status: "unknown" } as PreflightCheck;
    expect(Boolean(partial.ok)).toBe(false);
  });

  it("reports readiness independently of warnings", () => {
    // A machine can be ready while one particular selection is not.
    expect(report.ready).toBe(true);
    expect(report.warnings).toContain("run_spec");
  });
});
