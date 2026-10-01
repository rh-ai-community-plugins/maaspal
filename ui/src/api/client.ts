// Per-config-key launch-form metadata (scenario YAML `inputs:`, ADR-025).
// Every field is optional; a config key with no entry renders as a plain
// labelled input exactly as before.
export interface ScenarioInput {
  label?: string;
  help?: string;
  // Collapsed into the launch form's "Advanced" section.
  advanced?: boolean;
  // Rendered as a select instead of a free-text input.
  choices?: string[];
  // The blank option's text on the model/subscription pickers, e.g. "All
  // models" when leaving it blank means "every model".
  placeholder?: string;
  // Only shown while every listed config key currently has the given value.
  show_if?: Record<string, string>;
  // Filled automatically when the user picks a subscription: the first
  // tokenRateLimits entry of the selected model (or the subscription's
  // first model) — "limit" or "window".
  from_subscription?: 'limit' | 'window';
  // Filled automatically when the user picks a model.
  from_model?: 'http_route';
}

// Fields after `category` are optional so the UI degrades gracefully against
// an older backend — api/routes/scenarios.py always sends them today.
export interface Scenario {
  name: string;
  // Plain-language question this scenario answers — what the UI shows.
  title?: string;
  summary?: string;
  description: string;
  config: Record<string, string | number | boolean>;
  // Always present — the backend defaults a scenario with no `category:`
  // field to "Custom" (api/routes/scenarios.py).
  category: string;
  kind?: 'verify' | 'explore';
  // What kinds of cluster objects a run creates/changes.
  mutates?: string[];
  // Config keys that must be non-empty before the run can launch.
  requires?: string[];
  needs_rbac?: string[];
  est_duration?: string;
  // Position within its category (lower first).
  order?: number;
  inputs?: Record<string, ScenarioInput>;
  // "What this run will do" sentence, ${config.x}-substituted live.
  plan_template?: string;
  // Older ids of this scenario, so history rows from before a rename still
  // resolve to its current title.
  previous_names?: string[];
}

export type CleanupStatus = 'pending' | 'cleaning' | 'skipped' | 'done' | 'failed';

export interface Run {
  id: string;
  scenario: string;
  status: string;
  created_at: string;
  updated_at: string;
  duration_ms?: number | null;
  auto_cleanup: boolean;
  cleanup_status: CleanupStatus;
  cleanup_error?: string | null;
}

export interface AssertionState {
  task: string | null;
  name: string;
  status: 'PENDING' | 'PASSING' | 'FAILING';
  value: number | null;
  expected_value?: number | null;
  expression?: string;
  // Display-only metadata from the scenario YAML (ADR-025).
  label?: string | null;
  description?: string | null;
  unit?: string | null;
  // Human-readable rendering of the check ("50 – 150", "< 5").
  target?: string | null;
}

export interface CreateRunResponse {
  run_id: string;
  scenario: string;
  status: string;
}

export async function listScenarios(): Promise<Scenario[]> {
  const r = await fetch('/api/scenarios');
  if (!r.ok) throw new Error(`listScenarios failed: ${r.status}`);
  return r.json() as Promise<Scenario[]>;
}

export async function createRun(
  scenario: string,
  config_overrides: Record<string, string | number> = {},
  auto_cleanup = true,
): Promise<CreateRunResponse> {
  const r = await fetch('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scenario, config_overrides, auto_cleanup }),
  });
  if (!r.ok) throw new Error(`createRun failed: ${r.status}`);
  return r.json() as Promise<CreateRunResponse>;
}

export async function setAutoCleanup(runId: string, enabled: boolean): Promise<{ auto_cleanup: boolean }> {
  const r = await fetch(`/api/runs/${runId}/auto-cleanup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  if (!r.ok) throw new Error(`setAutoCleanup failed: ${r.status}`);
  return r.json() as Promise<{ auto_cleanup: boolean }>;
}

export async function cleanupRun(runId: string): Promise<{ cleanup_status: CleanupStatus }> {
  const r = await fetch(`/api/runs/${runId}/cleanup`, { method: 'POST' });
  if (!r.ok) throw new Error(`cleanupRun failed: ${r.status}`);
  return r.json() as Promise<{ cleanup_status: CleanupStatus }>;
}

export async function listRuns(): Promise<Run[]> {
  const r = await fetch('/api/runs');
  if (!r.ok) throw new Error(`listRuns failed: ${r.status}`);
  return r.json() as Promise<Run[]>;
}

export async function getRun(runId: string): Promise<Run> {
  const r = await fetch(`/api/runs/${runId}`);
  if (!r.ok) throw new Error(`getRun failed: ${r.status}`);
  return r.json() as Promise<Run>;
}

export async function stopRun(runId: string): Promise<void> {
  const r = await fetch(`/api/runs/${runId}/stop`, { method: 'POST' });
  if (!r.ok) throw new Error(`stopRun failed: ${r.status}`);
}

export interface TaskProgressEntry {
  name: string;
  status: 'PENDING' | 'RUNNING' | 'DONE' | 'FAIL' | 'CANCELLED';
  // total null = open-ended (e.g. "send until throttled"); unit labels it
  // ("tokens" when the bar tracks progress toward a rate limit).
  progress?: { current: number; total: number | null; unit?: string };
  assertions_status?: 'PASSING' | 'FAILING' | 'PENDING';
  started_at?: string;
  duration_ms?: number;
  // One-line plain-language narration of what the task did/is doing.
  summary?: string;
}

// Timeline point: [seconds since burst start, cumulative tokens, outcome, latency ms]
export type TrafficOutcome = 'ok' | 'throttled' | 'denied' | 'not_found' | 'server_error' | 'error';
export type TrafficPoint = [number, number, TrafficOutcome, number];

export interface TrafficSummary {
  total_requests?: number;
  http_attempts?: number;
  success_count?: number;
  rate_limited_count?: number;
  unauthorized_count?: number;
  server_error_count?: number;
  other_error_count?: number;
  total_tokens_sent?: number;
  prompt_tokens_sent?: number;
  completion_tokens_sent?: number;
  p50_latency_ms?: number;
  p95_latency_ms?: number;
  p99_latency_ms?: number;
  throughput_rps?: number;
  token_throughput_per_sec?: number;
  tokens_before_first_429?: number;
  requests_before_first_429?: number;
  seconds_to_first_429?: number;
  successes_after_first_429?: number;
  not_found_count?: number;
  // The most common failure reasons, e.g. "HTTP 404 Not Found".
  error_samples?: { message: string; count: number }[];
  // Rate-limit bursts sent "until throttled".
  allowed_overshoot?: number;
  concurrency_at_first_429?: number;
  peak_concurrency?: number;
  required_tokens_per_s?: number;
  limit_reached?: 0 | 1;
  not_throttled_bound?: string;
  // Step load: one entry per concurrency step.
  stages?: LoadStage[];
}

export interface LoadStage {
  concurrency: number;
  requests: number;
  requests_per_s: number;
  tokens_per_s: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  p99_latency_ms: number;
  error_rate_pct: number;
  throttled_pct: number;
}

export interface TrafficBurst {
  task: string;
  result_key: string;
  label?: string;
  // The configured token limit, drawn as the chart's reference line.
  limit?: number | null;
  // Whether this burst's shape over time answers the scenario's question —
  // otherwise it's shown as a one-line summary, no chart.
  chart?: boolean;
  // Bursts sharing a group are drawn on one timeline, placed by start time.
  chart_group?: string | null;
  // Wall-clock start, seconds since the epoch.
  t0?: number;
  planned?: number;
  summary: TrafficSummary;
  timeline: TrafficPoint[];
}

export type ResourceStatus = 'active' | 'removed' | 'restored' | 'revoked' | 'cleanup failed' | 'left in place';

export interface RunResource {
  kind: string;
  name: string;
  // The task that created it — its step on the run page.
  task?: string;
  // "patched" = an object that already existed, changed for the run.
  action?: 'created' | 'patched';
  status?: ResourceStatus;
  subscription?: string;
  owner?: string;
}

export interface RunFinding {
  title: string;
  text: string;
  outcome?: string;
}

// [seconds since run start, value reported by MaaS, value counted by the harness]
export type MetricsPoint = [number, number, number];

export interface MetricsChart {
  title: string;
  unit: string;
  maas_label: string;
  harness_label: string;
  points: MetricsPoint[];
}

export interface RunTable {
  title: string;
  columns: string[];
  rows: string[][];
}

export interface RunVerdict {
  status: 'PASS' | 'FAIL' | 'CANCELLED';
  text: string;
  checks_passed: number;
  checks_total: number;
}

export interface ProgressResponse {
  tasks: TaskProgressEntry[];
  run_started_at?: string;
  traffic?: TrafficBurst[];
  resources?: RunResource[];
  tables?: RunTable[];
  findings?: RunFinding[];
  metrics_charts?: MetricsChart[];
  verdict?: RunVerdict;
}

export async function getProgress(runId: string): Promise<ProgressResponse> {
  try {
    const r = await fetch(`/api/runs/${runId}/progress`);
    if (!r.ok) return { tasks: [] };
    const data = (await r.json()) as ProgressResponse;
    return { ...data, tasks: data.tasks ?? [] };
  } catch {
    return { tasks: [] };
  }
}

export async function getRunConfig(runId: string): Promise<string | null> {
  try {
    const r = await fetch(`/api/runs/${runId}/config`);
    if (!r.ok) return null;
    const data = (await r.json()) as { config_yaml: string | null };
    return data.config_yaml ?? null;
  } catch {
    return null;
  }
}

export async function getAssertions(runId: string): Promise<AssertionState[]> {
  try {
    const r = await fetch(`/api/runs/${runId}/assertions`);
    if (!r.ok) return [];
    const data = (await r.json()) as { assertions: AssertionState[] };
    return data.assertions ?? [];
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------------------
// MaaS Setup — live cluster visibility (see ADR-017 /
// docs/architecture/maas-domain-reference.md). Every endpoint here degrades
// to { available: false, reason } instead of a 4xx/5xx when the SA lacks
// RBAC for a given CRD or it isn't installed on this cluster — callers
// should render an unavailable notice for that reason, not treat it as an error.
// ---------------------------------------------------------------------------

export interface MaasTokenRateLimit {
  limit: number;
  window: string;
}

export interface MaasSubscription {
  name: string;
  namespace: string;
  display_name: string;
  description: string;
  priority: number | null;
  owner: { groups: string[]; users: string[] };
  // Entries here are references into the MaaSModelRef/model catalog
  // (spec.modelRefs[]), not full model records — see MaasModel for those.
  model_refs: {
    name: string;
    namespace: string;
    token_rate_limits: MaasTokenRateLimit[];
    display_name: string;
    // model_exists is null only when MaaSModelRef couldn't be read at all
    // (RBAC/connectivity) — false means the reference is genuinely dangling.
    model_exists: boolean | null;
    model_ready: boolean | null;
    has_auth_policy: boolean | null;
  }[];
  phase: string | null;
  ready: boolean;
  priority_conflict: boolean;
  raw: unknown;
  raw_yaml: string;
}

export interface MaasModelSubscriptionRef {
  name: string;
  display_name: string | null;
  description: string | null;
}

export interface MaasServingInfo {
  replicas: number | null;
  resources: unknown;
  conditions: { type: string; status: string }[];
}

export interface MaasExternalProviderRef {
  provider_name: string | null;
  target_model: string | null;
  api_format: string | null;
  path: string | null;
  endpoint: string | null;
  credential_secret_name: string | null;
  // null means "couldn't read the secret" — never collapse into false.
  credential_secret_label_ok: boolean | null;
  // This provider's own ExternalProvider CR YAML — null when it couldn't be
  // resolved. Never merged into the model's own raw_yaml.
  raw_yaml: string | null;
}

export interface MaasModelAuthPolicyRef {
  name: string;
  namespace: string;
  display_name: string;
  ready: boolean;
  raw_yaml: string;
}

export interface MaasModel {
  name: string;
  namespace: string;
  display_name: string;
  description: string;
  kind: string | null;
  hosting: 'internal' | 'external';
  backing_name: string | null;
  phase: string | null;
  ready: boolean;
  endpoint: string | null;
  // "<namespace>/<HTTPRoute name>" — Limitador's limitador_namespace label.
  http_route?: string | null;
  subscriptions: MaasModelSubscriptionRef[];
  // null means "couldn't tell" (RBAC/read failure) — never collapse into false.
  has_auth_policy: boolean | null;
  // Empty when has_auth_policy is false; populated with enough to open each
  // matching MaaSAuthPolicy's raw YAML directly from the Models tab.
  auth_policies: MaasModelAuthPolicyRef[];
  gateway_access_label: boolean | null;
  external_providers: MaasExternalProviderRef[];
  serving: MaasServingInfo | null;
  raw: unknown;
  // Just this model's own CR (MaaSModelRef or ExternalModel) — the backing
  // LLMInferenceService gets its own serving_raw_yaml below, never merged
  // into one synthetic multi-object document.
  raw_yaml: string;
  // The backing LLMInferenceService's own YAML (internal models only) —
  // null for external models or when it couldn't be resolved.
  serving_raw_yaml: string | null;
}

export interface MaasAuthPolicyModelRef {
  name: string;
  namespace: string;
  display_name: string;
  model_exists: boolean | null;
  model_ready: boolean | null;
}

export interface MaasAuthPolicy {
  name: string;
  namespace: string;
  display_name: string;
  description: string;
  owner: { groups: string[]; users: string[] };
  model_refs: MaasAuthPolicyModelRef[];
  phase: string | null;
  ready: boolean;
  raw_yaml: string;
}

export interface MaasAccessSubscriptionRef {
  name: string;
  display_name: string;
  priority: number | null;
  raw_yaml: string;
}

export interface MaasAccessPolicyRef {
  name: string;
  display_name: string;
  ready: boolean;
  raw_yaml: string;
}

export interface MaasAccessRow {
  name: string;
  users: string[] | null;
  raw_yaml: string | null;
  subscriptions: MaasAccessSubscriptionRef[];
  auth_policies: MaasAccessPolicyRef[];
  quota_without_access: string[];
  access_without_quota: string[];
}

export interface MaasTokenRateLimitPolicy {
  name: string;
  namespace: string;
  target_kind: string | null;
  target_name: string | null;
  limit_names: string[];
  accepted: boolean;
  enforced: boolean;
  raw_yaml: string;
}

export interface MaasLimitador {
  name: string;
  namespace: string;
  limit_count: number;
  ready: boolean;
  service_host: string | null;
  raw_yaml: string;
}

export interface MaasGateway {
  name: string;
  namespace: string;
  gateway_class: string | null;
  address: string | null;
  programmed: boolean;
  raw_yaml: string;
}

export interface MaasHttpRoute {
  name: string;
  namespace: string;
  parent_gateway: string | null;
  parent_gateway_namespace: string | null;
  owning_model: string | null;
  raw_yaml: string;
}

export interface MaasTenant {
  name: string;
  namespace: string;
  gateway_ref: { name: string; namespace: string } | null;
  max_api_key_expiration_days: number | null;
  telemetry_enabled: boolean | null;
  phase: string | null;
  raw_yaml: string;
}

export interface MaasDataScienceCluster {
  name: string;
  maas_management_state: string | null;
  maas_field_path: string | null;
  raw_yaml: string;
}

export interface MaasOdhDashboardConfig {
  name: string;
  model_as_service: boolean | null;
  external_models: boolean | null;
  gen_ai_studio: boolean | null;
  observability_dashboard: boolean | null;
  raw_yaml: string;
}

export interface MaasPlatformSection<T> {
  available: boolean;
  reason: string | null;
  item: T | null;
}

export interface MaasPlatform {
  tenants: MaasSectionResult<MaasTenant>;
  data_science_cluster: MaasPlatformSection<MaasDataScienceCluster>;
  odh_dashboard_config: MaasPlatformSection<MaasOdhDashboardConfig>;
}

export interface MaasSectionResult<T> {
  available: boolean;
  reason: string | null;
  items: T[];
}

async function fetchMaasSection<T>(path: string): Promise<MaasSectionResult<T>> {
  try {
    const r = await fetch(path);
    if (!r.ok) return { available: false, reason: `http_${r.status}`, items: [] };
    return (await r.json()) as MaasSectionResult<T>;
  } catch {
    return { available: false, reason: 'network_error', items: [] };
  }
}

export function getMaasSubscriptions(): Promise<MaasSectionResult<MaasSubscription>> {
  return fetchMaasSection('/api/maas/subscriptions');
}

export function getMaasModels(): Promise<MaasSectionResult<MaasModel>> {
  return fetchMaasSection('/api/maas/models');
}

export function getMaasAccess(): Promise<MaasSectionResult<MaasAccessRow>> {
  return fetchMaasSection('/api/maas/access');
}

export function getMaasAuthPolicies(): Promise<MaasSectionResult<MaasAuthPolicy>> {
  return fetchMaasSection('/api/maas/auth-policies');
}

export function getMaasRateLimitPolicies(): Promise<MaasSectionResult<MaasTokenRateLimitPolicy>> {
  return fetchMaasSection('/api/maas/rate-limit-policies');
}

export function getMaasLimitador(): Promise<MaasSectionResult<MaasLimitador>> {
  return fetchMaasSection('/api/maas/limitador');
}

export function getMaasGateways(): Promise<MaasSectionResult<MaasGateway>> {
  return fetchMaasSection('/api/maas/gateways');
}

export function getMaasHttpRoutes(): Promise<MaasSectionResult<MaasHttpRoute>> {
  return fetchMaasSection('/api/maas/http-routes');
}

const UNAVAILABLE_PLATFORM: MaasPlatform = {
  tenants: { available: false, reason: 'network_error', items: [] },
  data_science_cluster: { available: false, reason: 'network_error', item: null },
  odh_dashboard_config: { available: false, reason: 'network_error', item: null },
};

export async function getMaasPlatform(): Promise<MaasPlatform> {
  try {
    const r = await fetch('/api/maas/platform');
    if (!r.ok) return UNAVAILABLE_PLATFORM;
    return (await r.json()) as MaasPlatform;
  } catch {
    return UNAVAILABLE_PLATFORM;
  }
}

