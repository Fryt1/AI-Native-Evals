/**
 * Opening state for the run form.
 *
 * The behaviour under test is what an operator sees when the dialog opens: a
 * form that is already runnable rather than four empty dropdowns. The rules are
 * deliberately conservative -- a single candidate is selected, a configured
 * default is honoured, and a genuinely ambiguous choice is left to the human
 * rather than guessed.
 */
import { describe, expect, it } from "vitest";
import { agentModels, chooseModel, chooseOne, chooseProvider, chooseReasoning, initialForm, isMeaningfulChoice, nonAgentModels, preferredModelFor, taskExecution, usableProviders } from "./launchDefaults";
import type { ProviderSummary, Registry } from "./types";

function provider(id: string, configured: boolean, defaultReasoning = "high"): ProviderSummary {
  return {
    id,
    name: id,
    wire_api: ["responses"],
    configured,
    default_reasoning: defaultReasoning,
    declared_models: [],
    fallback_reasoning_levels: [],
  };
}

function registry(overrides: Partial<Registry> = {}): Registry {
  return {
    tasks: [{ id: "task-a", kind: "task", path: "tasks/task-a/task.yaml" }],
    agents: [{ id: "codex", kind: "agent", path: "profiles/agents/codex.yaml" }],
    providers: [],
    models: [],
    mcp: [{ id: "none", kind: "mcp", path: "profiles/mcp/none.yaml" }],
    sandboxes: [{ id: "docker-default", kind: "sandbox", path: "profiles/sandboxes/docker-default.yaml" }],
    presets: [],
    defaults: {},
    ...overrides,
  };
}

describe("usableProviders", () => {
  it("keeps only providers that can actually serve a run", () => {
    const usable = usableProviders([provider("a", true), provider("b", false)]);

    expect(usable.map((entry) => entry.id)).toEqual(["a"]);
  });
});

describe("chooseProvider", () => {
  it("selects the only usable provider", () => {
    // One candidate is not a decision.
    expect(chooseProvider([provider("only", true), provider("dead", false)])).toBe("only");
  });

  it("honours a configured default when it is usable", () => {
    const providers = [provider("a", true), provider("b", true)];

    expect(chooseProvider(providers, "b")).toBe("b");
  });

  it("ignores a configured default that cannot run", () => {
    // Selecting an unconfigured provider guarantees a failure at request time.
    const providers = [provider("live", true), provider("dead", false)];

    expect(chooseProvider(providers, "dead")).toBe("live");
  });

  it("leaves an ambiguous choice to the operator", () => {
    expect(chooseProvider([provider("a", true), provider("b", true)])).toBe("");
  });

  it("returns empty when nothing is usable", () => {
    expect(chooseProvider([provider("a", false)])).toBe("");
  });
});

describe("chooseModel", () => {
  it("selects the only model", () => {
    expect(chooseModel(["solo"])).toBe("solo");
  });

  it("honours a default the provider serves", () => {
    expect(chooseModel(["a", "b"], "b")).toBe("b");
  });

  it("ignores a default the provider does not serve", () => {
    // The run would die at request time; a stale binding must not be submitted.
    expect(chooseModel(["a", "b"], "retired")).toBe("");
  });

  it("leaves several candidates to the operator", () => {
    expect(chooseModel(["a", "b"])).toBe("");
  });

  it("returns empty when the provider serves nothing", () => {
    expect(chooseModel([], "anything")).toBe("");
  });
});

describe("chooseReasoning", () => {
  it("uses the provider default when it is legal", () => {
    expect(chooseReasoning(["low", "high"], "high")).toBe("high");
  });

  it("takes the only legal level", () => {
    expect(chooseReasoning(["high"])).toBe("high");
  });

  it("leaves an ambiguous level unset rather than guessing", () => {
    // Reasoning changes what the evaluation measures; guessing would be worse
    // than an explicit choice.
    expect(chooseReasoning(["low", "high"])).toBe("");
  });
});

describe("chooseOne", () => {
  it("prefers a configured value that exists", () => {
    expect(chooseOne(["a", "b"], "b")).toBe("b");
  });

  it("selects a single option", () => {
    expect(chooseOne(["only"])).toBe("only");
  });

  it("ignores a configured value that no longer exists", () => {
    expect(chooseOne(["a"], "gone")).toBe("a");
  });
});

describe("agentModels", () => {
  const models = [
    { id: "gpt-5.6-luna", capability: "chat" },
    { id: "gpt-image-1", capability: "image" },
    { id: "text-embedding-3", capability: "other" },
  ];

  it("keeps only models that can act as an Agent", () => {
    // An image model would start a run that fails with the container already up.
    expect(agentModels(models).map((entry) => entry.id)).toEqual(["gpt-5.6-luna"]);
  });

  it("treats a missing capability as chat", () => {
    // An older payload must not make every model disappear.
    expect(agentModels([{ id: "legacy-model" }]).map((entry) => entry.id)).toEqual(["legacy-model"]);
  });

  it("reports what it excluded so the UI can explain", () => {
    expect(nonAgentModels(models).map((entry) => entry.id)).toEqual([
      "gpt-image-1",
      "text-embedding-3",
    ]);
  });

  it("handles an empty list", () => {
    expect(agentModels([])).toEqual([]);
    expect(nonAgentModels([])).toEqual([]);
  });
});

describe("initialForm", () => {
  it("opens in a runnable state when there is one of everything", () => {
    const form = initialForm(
      registry({ defaults: { agent: "codex", mcp_profile: "none", sandbox_profile: "docker-default" } }),
      [provider("sub2api", true)],
      ["gpt-5.6-luna"],
    );

    expect(form).toEqual({
      task_id: "task-a",
      provider: "sub2api",
      model: "gpt-5.6-luna",
      agent: "codex",
      mcp_profile: "none",
      sandbox_profile: "docker-default",
    });
  });

  it("leaves the model empty until the provider has been asked", () => {
    // Models come from the provider, so they cannot be known at first render.
    const form = initialForm(registry(), [provider("sub2api", true)], []);

    expect(form.provider).toBe("sub2api");
    expect(form.model).toBe("");
  });

  it("does not preset a task when there are several", () => {
    const form = initialForm(
      registry({
        tasks: [
          { id: "a", kind: "task", path: "tasks/a/task.yaml" },
          { id: "b", kind: "task", path: "tasks/b/task.yaml" },
        ],
      }),
      [provider("p", true)],
      [],
    );

    expect(form.task_id).toBe("");
  });

  it("does not preset an agent when several are available", () => {
    const form = initialForm(
      registry({
        agents: [
          { id: "codex", kind: "agent", path: "a" },
          { id: "dsh", kind: "agent", path: "b" },
        ],
      }),
      [provider("p", true)],
      [],
    );

    expect(form.agent).toBe("");
  });

  it("survives an empty registry", () => {
    const form = initialForm(null, [], []);

    expect(form.task_id).toBe("");
    expect(form.provider).toBe("");
  });

  it("uses the configured model only for the provider it belongs to", () => {
    const form = initialForm(
      registry({ defaults: { model_provider: "other", model: "stale-model" } }),
      [provider("sub2api", true)],
      ["gpt-5.6-luna"],
    );

    // The binding names a different provider, so it must not be applied.
    expect(form.model).toBe("gpt-5.6-luna");
  });

  it("applies the configured model when it matches the chosen provider", () => {
    const form = initialForm(
      registry({ defaults: { model_provider: "sub2api", model: "gpt-5.6-sol" } }),
      [provider("sub2api", true)],
      ["gpt-5.6-luna", "gpt-5.6-sol"],
    );

    expect(form.model).toBe("gpt-5.6-sol");
  });

  it("applies a model configured with only a provider key", () => {
    // The shape this repository actually ships: `provider` + `model`, no
    // `model_provider`.
    const form = initialForm(
      registry({ defaults: { provider: "sub2api", model: "gpt-5.6-luna" } }),
      [provider("sub2api", true)],
      ["gpt-5.6-luna", "gpt-5.6-sol", "gpt-6-astra"],
    );

    expect(form.model).toBe("gpt-5.6-luna");
    expect(form.provider).toBe("sub2api");
  });
});

/** A Task's declared requirements outrank the global defaults. */
describe("taskExecution", () => {
  const withTasks = {
    tasks: [
      { id: "blender-task", kind: "task", path: "t", execution: { agent: "codex-blender", mcp_profile: "blender-host" } },
      { id: "plain-task", kind: "task", path: "t" },
    ],
  } as unknown as Registry;

  it("returns what the named Task declares", () => {
    expect(taskExecution(withTasks, "blender-task")).toEqual({
      agent: "codex-blender",
      mcp_profile: "blender-host",
    });
  });

  it("returns nothing for a Task that declares nothing", () => {
    expect(taskExecution(withTasks, "plain-task")).toEqual({});
  });

  it("returns nothing for an unknown Task rather than throwing", () => {
    expect(taskExecution(withTasks, "missing")).toEqual({});
    expect(taskExecution(null, "any")).toEqual({});
  });
});

describe("a Task's own execution beats the global default", () => {
  it("preselects the MCP profile the Task declares", () => {
    // The global default is `none`. Letting it win started a Blender run with
    // no MCP tools at all, which the Agent experienced as "the tools I was told
    // to use are not here".
    const registry = {
      tasks: [
        { id: "blender-task", kind: "task", path: "t", execution: { mcp_profile: "blender-host", agent: "codex-blender" } },
      ],
      agents: [{ id: "codex", kind: "agent", path: "p" }, { id: "codex-blender", kind: "agent", path: "p" }],
      mcp: [{ id: "none", kind: "mcp", path: "p" }, { id: "blender-host", kind: "mcp", path: "p" }],
      sandboxes: [{ id: "docker-default", kind: "sandbox", path: "p" }],
      defaults: { task_id: "blender-task", agent: "codex", mcp_profile: "none", sandbox_profile: "docker-default" },
    } as unknown as Registry;

    const form = initialForm(registry, [provider("sub2api", true)], ["m"]);

    expect(form.mcp_profile).toBe("blender-host");
    expect(form.agent).toBe("codex-blender");
  });

  it("still uses the global default when the Task declares nothing", () => {
    const registry = {
      tasks: [{ id: "plain", kind: "task", path: "t" }],
      agents: [{ id: "codex", kind: "agent", path: "p" }],
      mcp: [{ id: "none", kind: "mcp", path: "p" }, { id: "blender-host", kind: "mcp", path: "p" }],
      sandboxes: [{ id: "docker-default", kind: "sandbox", path: "p" }],
      defaults: { task_id: "plain", agent: "codex", mcp_profile: "none" },
    } as unknown as Registry;

    const form = initialForm(registry, [provider("sub2api", true)], ["m"]);

    expect(form.mcp_profile).toBe("none");
  });

  it("ignores a Task profile that is not a registered option", () => {
    const registry = {
      tasks: [{ id: "t", kind: "task", path: "p", execution: { mcp_profile: "deleted-profile" } }],
      agents: [{ id: "codex", kind: "agent", path: "p" }],
      mcp: [{ id: "none", kind: "mcp", path: "p" }],
      sandboxes: [{ id: "docker-default", kind: "sandbox", path: "p" }],
      defaults: { task_id: "t", mcp_profile: "none" },
    } as unknown as Registry;

    const form = initialForm(registry, [provider("sub2api", true)], ["m"]);

    expect(form.mcp_profile).toBe("none");
  });
});

/** The configured model, when it belongs to the chosen provider. */
describe("preferredModelFor", () => {
  it("applies a model configured alongside its provider", () => {
    // A config naming `provider` but not `model_provider` still describes one
    // pair. Requiring both keys silently discarded the default model, which
    // left the form empty and made the operator discover the field was required
    // only when the run was refused.
    expect(preferredModelFor({ provider: "sub2api", model: "gpt-5.6-luna" }, "sub2api"))
      .toBe("gpt-5.6-luna");
  });

  it("applies a model bound through the legacy key", () => {
    expect(preferredModelFor({ model_provider: "sub2api", model: "m" }, "sub2api")).toBe("m");
  });

  it("ignores a model bound to a different provider", () => {
    // Submitting another provider's model fails at request time.
    expect(preferredModelFor({ model_provider: "other", model: "m" }, "sub2api")).toBeUndefined();
    expect(preferredModelFor({ provider: "other", model: "m" }, "sub2api")).toBeUndefined();
  });

  it("has nothing to offer without a configured model", () => {
    expect(preferredModelFor({ provider: "sub2api" }, "sub2api")).toBeUndefined();
    expect(preferredModelFor({}, "sub2api")).toBeUndefined();
  });

  it("has nothing to offer without a provider", () => {
    expect(preferredModelFor({ model: "m" }, "")).toBeUndefined();
  });
});

describe("isMeaningfulChoice", () => {
  it("treats a single option as no choice at all", () => {
    // One option costs attention and cannot be answered wrongly; it is hidden.
    expect(isMeaningfulChoice(["only"])).toBe(false);
  });

  it("treats several options as a real decision", () => {
    expect(isMeaningfulChoice(["a", "b"])).toBe(true);
  });

  it("treats nothing as nothing to decide", () => {
    expect(isMeaningfulChoice([])).toBe(false);
  });

  it("counts only what the caller passes, so disabled entries must be excluded first", () => {
    // The caller filters to selectable options; this documents the contract.
    expect(isMeaningfulChoice([{ id: "a" }])).toBe(false);
    expect(isMeaningfulChoice([{ id: "a" }, { id: "b" }])).toBe(true);
  });
});
