import type { MaasModel, MaasSubscription, Scenario, ScenarioInput } from './api/client';

// Pure helpers behind the run launch form (components/RunTrigger.tsx) — kept
// out of the component file so they're testable on their own and the
// component module only exports components (React fast refresh).

export type ConfigValues = Record<string, string | number>;

// Fields a MaaSModelRef-backed scenario uses to target a specific model CR —
// these two always travel together, so they get one combined picker instead
// of two blank text boxes the user has to copy exact CR names/namespaces into
// by hand (previously required an `oc get maasmodelrefs -A` first).
export const _MODEL_NAME_KEY = 'target_model_name';
export const _MODEL_NAMESPACE_KEY = 'target_model_namespace';

// Scenarios that pin (rather than create) a MaaSSubscription expose this as a
// plain `subscription` config key.
export const _SUBSCRIPTION_KEY = 'subscription';

// Matches harness/tasks/subscription.py's _DEFAULT_OWNER_GROUPS — the one
// group the harness's own SA identity reliably resolves to. Used only to
// annotate options, never to hide them, since owner.users could still make a
// subscription selectable in ways this UI can't detect.
export const _HARNESS_OWNER_GROUP = 'system:authenticated';

export function initValues(config: Scenario['config']): ConfigValues {
  const out: ConfigValues = {};
  for (const [k, v] of Object.entries(config)) {
    out[k] = v as string | number;
  }
  return out;
}

export function humanizeKey(key: string): string {
  const words = key.replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function subscriptionCoversModel(sub: MaasSubscription, namespace: string, name: string): boolean {
  return sub.model_refs.some((r) => r.namespace === namespace && r.name === name);
}

export function modelHasSubscription(model: MaasModel, subName: string): boolean {
  return model.subscriptions.some((s) => s.name === subName);
}

/** A config key is visible unless its `show_if` names another key whose
 * current value differs. */
export function isVisible(input: ScenarioInput | undefined, values: ConfigValues): boolean {
  return Object.entries(input?.show_if ?? {}).every(([k, v]) => String(values[k] ?? '') === String(v));
}

/** The configured token rate limit a subscription applies to a model — the
 * model's own modelRef entry when a model is chosen, otherwise the
 * subscription's first model. */
export function rateLimitFor(
  sub: MaasSubscription,
  modelNamespace: string,
  modelName: string,
): { limit: number; window: string } | null {
  const ref =
    (modelName && sub.model_refs.find((r) => r.namespace === modelNamespace && r.name === modelName)) ||
    sub.model_refs[0];
  const first = ref?.token_rate_limits?.[0];
  return first ? { limit: first.limit, window: first.window } : null;
}

/** Fill every `from_subscription`/`from_model` config field from the
 * currently selected subscription/model. Fields with nothing to fill from are
 * left as they are, so a manual value is never wiped. */
export function applyAutofill(
  values: ConfigValues,
  inputs: Record<string, ScenarioInput>,
  subscriptions: MaasSubscription[] | null,
  models: MaasModel[] | null,
): ConfigValues {
  const next = { ...values };
  const subName = String(values[_SUBSCRIPTION_KEY] ?? '');
  const modelName = String(values[_MODEL_NAME_KEY] ?? '');
  const modelNamespace = String(values[_MODEL_NAMESPACE_KEY] ?? '');
  const sub = subName ? subscriptions?.find((s) => s.name === subName) : undefined;
  const model = modelName ? models?.find((m) => m.name === modelName && m.namespace === modelNamespace) : undefined;
  const rl = sub ? rateLimitFor(sub, modelNamespace, modelName) : null;

  for (const [key, input] of Object.entries(inputs)) {
    if (input.from_subscription && rl) {
      next[key] = input.from_subscription === 'limit' ? rl.limit : rl.window;
    }
    if (input.from_model === 'http_route' && model?.http_route) {
      next[key] = model.http_route;
    }
  }
  return next;
}

/** Config keys listed in `requires` that are still blank (ignoring ones the
 * current mode hides). */
export function missingRequired(scenario: Scenario, values: ConfigValues): string[] {
  const inputs = scenario.inputs ?? {};
  return (scenario.requires ?? []).filter(
    (k) => isVisible(inputs[k], values) && String(values[k] ?? '').trim() === '',
  );
}

/** The scenario's "what this run will do" sentence with the current form
 * values substituted. ${model}/${subscription} read naturally even when blank. */
export function renderPlan(template: string, values: ConfigValues): string {
  const modelName = String(values[_MODEL_NAME_KEY] ?? '');
  const modelNamespace = String(values[_MODEL_NAMESPACE_KEY] ?? '');
  const subName = String(values[_SUBSCRIPTION_KEY] ?? '');
  return template
    .replace(/\$\{model\}/g, modelName ? `${modelNamespace}/${modelName}` : 'the default model')
    .replace(/\$\{subscription\}/g, subName ? subName : 'an auto-selected subscription')
    .replace(/\$\{config\.([A-Za-z0-9_]+)\}/g, (_m, key: string) => {
      const v = values[key];
      return v === undefined || v === '' ? '…' : String(v);
    });
}
