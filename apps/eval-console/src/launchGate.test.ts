/**
 * The launch gate.
 *
 * The rule that matters: only a *verified absent* finding stops a run. A probe
 * that could not run is reported, never enforced -- telling an operator their
 * machine is broken because Docker could not be reached over WSL would block
 * runs that would have worked.
 */
import { describe, expect, it } from "vitest";
import { blockerSummary, blockingChecks, checkLabel, unknownChecks } from "./launchGate";
import type { PreflightRun } from "./types";

function run(checks: Record<string, { status: "ok" | "missing" | "unknown"; detail?: string; hint?: string }>): PreflightRun {
  return {
    check_id: "check-1",
    status: "completed",
    selector: {},
    created_at: "2026-01-01T00:00:00+00:00",
    report: {
      ready: !Object.values(checks).some((c) => c.status === "missing"),
      checks: Object.fromEntries(
        Object.entries(checks).map(([name, check]) => [
          name,
          { ok: check.status === "ok", status: check.status, detail: check.detail, hint: check.hint },
        ]),
      ),
      missing_required: Object.entries(checks)
        .filter(([, c]) => c.status === "missing")
        .map(([name]) => name),
    },
  };
}

describe("blockingChecks", () => {
  it("blocks on a missing finding", () => {
    const blockers = blockingChecks(run({ docker: { status: "missing", detail: "no daemon" } }));

    expect(blockers).toHaveLength(1);
    expect(blockers[0].check.detail).toBe("no daemon");
  });

  it("never blocks on an unknown finding", () => {
    // The distinction the whole design rests on: a failed probe is not a fault.
    const blockers = blockingChecks(run({ docker: { status: "unknown", detail: "probe failed" } }));

    expect(blockers).toHaveLength(0);
  });

  it("does not block on a healthy machine", () => {
    const blockers = blockingChecks(run({ python: { status: "ok" }, docker: { status: "ok" } }));

    expect(blockers).toHaveLength(0);
  });

  it("reports every blocker, not just the first", () => {
    const blockers = blockingChecks(
      run({ docker: { status: "missing" }, images: { status: "missing" }, python: { status: "ok" } }),
    );

    expect(blockers).toHaveLength(2);
  });

  it("has no opinion before a check has run", () => {
    expect(blockingChecks(null)).toHaveLength(0);
    expect(blockingChecks({ ...run({}), report: null })).toHaveLength(0);
  });

  it("uses a readable label rather than the raw check id", () => {
    const blockers = blockingChecks(run({ provider_credentials: { status: "missing" } }));

    expect(blockers[0].name).toBe("上游凭据");
    expect(blockers[0].name).not.toContain("_");
  });

  it("keeps the hint so the operator knows what to do", () => {
    const blockers = blockingChecks(
      run({ images: { status: "missing", detail: "agent:local", hint: "build them" } }),
    );

    expect(blockers[0].check.hint).toBe("build them");
  });
});

describe("unknownChecks", () => {
  it("surfaces unknown findings for display without enforcing them", () => {
    const unknown = unknownChecks(
      run({ mcp_hosts: { status: "unknown" }, docker: { status: "ok" }, images: { status: "missing" } }),
    );

    expect(unknown).toHaveLength(1);
    expect(unknown[0].name).toBe("MCP 宿主");
  });

  it("is empty when nothing is unknown", () => {
    expect(unknownChecks(run({ python: { status: "ok" } }))).toHaveLength(0);
    expect(unknownChecks(null)).toHaveLength(0);
  });
});

describe("blockerSummary", () => {
  it("names what is blocking", () => {
    const blockers = blockingChecks(run({ docker: { status: "missing" } }));
    const summary = blockerSummary(blockers);

    expect(summary).toContain("Docker");
    expect(summary).toContain("还不能运行");
  });

  it("lists several blockers", () => {
    const summary = blockerSummary(
      blockingChecks(run({ docker: { status: "missing" }, images: { status: "missing" } })),
    );

    expect(summary).toContain("Docker");
    expect(summary).toContain("容器镜像");
  });
});

describe("checkLabel", () => {
  it("falls back to the id for a check it does not know", () => {
    // A new server-side check must still render, not disappear.
    expect(checkLabel("brand_new_check")).toBe("brand_new_check");
  });
});
