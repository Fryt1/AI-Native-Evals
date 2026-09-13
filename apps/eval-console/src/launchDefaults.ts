/**
 * Choosing initial values for the run form.
 *
 * Opening the dialog used to present four empty dropdowns even when only one
 * answer was possible, so every run began with the same pointless round of
 * selections. The rules here encode what an operator expects:
 *
 *   1. a value already configured for this repository wins;
 *   2. otherwise, when exactly one option is usable, it is selected;
 *   3. otherwise the field stays empty and the operator chooses.
 *
 * Nothing is invented: when the choice is genuinely ambiguous the field is left
 * for a human rather than silently defaulting to something they did not ask for.
 */
import type { ProviderSummary, Registry } from "./types";

export type LaunchDefaults = {
  task_id: string;
  provider: string;
  model: string;
  agent: string;
  mcp_profile: string;
  sandbox_profile: string;
};

/** Providers that can actually serve a run right now. */
export function usableProviders(providers: ProviderSummary[]): ProviderSummary[] {
  return providers.filter((entry) => entry.configured);
}

/**
 * Pick the initial provider: the configured default when it is usable,
 * otherwise the only usable one.
 */
export function chooseProvider(
  providers: ProviderSummary[],
  preferred?: string | null,
): string {
  const usable = usableProviders(providers);
  if (preferred && usable.some((entry) => entry.id === preferred)) return preferred;
  return usable.length === 1 ? usable[0].id : "";
}

/**
 * Pick the initial model.
 *
 * Only models the provider actually serves are considered, and a single
 * candidate is selected automatically. A configured default that the provider
 * does not serve is ignored rather than submitted -- the run would fail at
 * request time.
 */
export function chooseModel(models: string[], preferred?: string | null): string {
  if (preferred && models.includes(preferred)) return preferred;
  return models.length === 1 ? models[0] : "";
}

/** Pick the only option when there is exactly one, else the configured default. */
export function chooseOne(
  options: string[],
  preferred?: string | null,
): string {
  if (preferred && options.includes(preferred)) return preferred;
  return options.length === 1 ? options[0] : "";
}

/**
 * The form's opening state, derived from the registry and the live model list.
 *
 * `models` is empty until a provider has been queried, so a model default can
 * only be applied once that answer arrives; this function is therefore called
 * again when the list loads.
 */
export function initialForm(
  registry: Registry | null,
  providers: ProviderSummary[],
  models: string[],
): LaunchDefaults {
  const defaults = (registry?.defaults || {}) as Record<string, string>;
  const tasks = (registry?.tasks || []).map((entry) => entry.id);

  const provider = chooseProvider(providers, defaults.model_provider || defaults.provider);
  return {
    // A single Task is preselected: the choice is not a decision when there is
    // only one answer.
    task_id: chooseOne(tasks, defaults.task_id),
    provider,
    // A configured model applies when it belongs to the provider that was
    // chosen. `model_provider` is the legacy spelling of that pairing; a config
    // that names only `provider` still means these two go together.
    model: chooseModel(models, preferredModelFor(defaults, provider)),
    agent: chooseOne(
      (registry?.agents || []).map((entry) => entry.id),
      defaults.agent,
    ),
    mcp_profile: chooseOne(
      (registry?.mcp || []).map((entry) => entry.id),
      defaults.mcp_profile,
    ),
    sandbox_profile: chooseOne(
      (registry?.sandboxes || []).map((entry) => entry.id),
      defaults.sandbox_profile,
    ),
  };
}

/**
 * The configured model, when it belongs to the chosen provider.
 *
 * A binding is only meaningful for the provider it names, so a model left over
 * from a different provider is ignored rather than submitted. A config that
 * names `provider` but not `model_provider` describes one pair, so its model
 * applies to that provider.
 */
export function preferredModelFor(
  defaults: Record<string, string>,
  provider: string,
): string | undefined {
  if (!defaults.model || !provider) return undefined;
  const bound = defaults.model_provider;
  if (bound && bound !== provider) return undefined;
  if (!bound && defaults.provider && defaults.provider !== provider) return undefined;
  return defaults.model;
}

/** Reasoning level for a provider/model pair: the provider's default, else the only option. */
export function chooseReasoning(levels: string[], preferred?: string | null): string {
  if (preferred && levels.includes(preferred)) return preferred;
  return levels.length === 1 ? levels[0] : "";
}

/**
 * Models that can act as an Agent.
 *
 * A relay lists image and embedding models beside chat models, and choosing one
 * starts a run that fails after the container is up. An entry with no declared
 * capability is kept: an older payload must not make every model disappear.
 */
export function agentModels<T extends { id: string; capability?: string }>(models: T[]): T[] {
  return models.filter((entry) => (entry.capability ?? "chat") === "chat");
}

/** Models that are listed but cannot run an Agent; shown as an explanation. */
export function nonAgentModels<T extends { id: string; capability?: string }>(models: T[]): T[] {
  return models.filter((entry) => (entry.capability ?? "chat") !== "chat");
}

/**
 * Whether a field offers a real decision.
 *
 * A dropdown with a single option is not a choice: it costs attention, invites
 * the operator to wonder what they are missing, and cannot be answered wrongly
 * anyway. Such a field is hidden and reported as already resolved instead.
 *
 * Callers pass the *selectable* options, so two entries where one is disabled
 * still counts as one choice.
 */
export function isMeaningfulChoice(options: unknown[]): boolean {
  return options.length > 1;
}
