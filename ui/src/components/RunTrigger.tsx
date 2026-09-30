import { useEffect, useState } from 'react';
import { Button, Modal, Switch } from '@patternfly/react-core';
import {
  createRun,
  getMaasModels,
  getMaasSubscriptions,
  type MaasModel,
  type MaasSubscription,
  type Scenario,
} from '../api/client';

interface Props {
  scenario: Scenario;
  onConfirm: (runId: string) => void;
  onCancel: () => void;
}

type ConfigValues = Record<string, string | number>;

// Fields a MaaSModelRef-backed scenario (rate_limit_validation,
// subscription_without_authpolicy) uses to target a specific model CR — these two
// always travel together, so they get one combined picker instead of two
// blank text boxes the user has to copy exact CR names/namespaces into by
// hand (previously required an `oc get maasmodelrefs -A` first).
const _MODEL_NAME_KEY = 'target_model_name';
const _MODEL_NAMESPACE_KEY = 'target_model_namespace';

// Scenarios that pin (rather than create) a MaaSSubscription — single_key_load,
// multi_key_load — expose this as a plain `subscription` config key.
const _SUBSCRIPTION_KEY = 'subscription';

// Matches harness/tasks/subscription.py's _DEFAULT_OWNER_GROUPS — the one
// group the harness's own SA identity reliably resolves to. Used only to
// annotate options, never to hide them, since owner.users could still make a
// subscription selectable in ways this UI can't detect.
const _HARNESS_OWNER_GROUP = 'system:authenticated';

function initValues(config: Scenario['config']): ConfigValues {
  const out: ConfigValues = {};
  for (const [k, v] of Object.entries(config)) {
    out[k] = v as string | number;
  }
  return out;
}

function subscriptionCoversModel(sub: MaasSubscription, namespace: string, name: string): boolean {
  return sub.model_refs.some((r) => r.namespace === namespace && r.name === name);
}

function modelHasSubscription(model: MaasModel, subName: string): boolean {
  return model.subscriptions.some((s) => s.name === subName);
}

const selectStyle = {
  padding: '0.375rem 0.5rem',
  border: '1px solid #ccc',
  borderRadius: '4px',
  fontFamily: 'inherit',
  fontSize: '0.875rem',
};

const noteStyle = { color: '#888', fontSize: '0.75rem', margin: '0.25rem 0 0' };

export function RunTrigger({ scenario, onConfirm, onCancel }: Props) {
  const [loading, setLoading] = useState(false);
  const [values, setValues] = useState<ConfigValues>(() => initValues(scenario.config));
  const [autoCleanup, setAutoCleanup] = useState(true);

  const needsModelPicker =
    _MODEL_NAME_KEY in scenario.config && _MODEL_NAMESPACE_KEY in scenario.config;
  const [models, setModels] = useState<MaasModel[] | null>(null);
  const [modelsUnavailable, setModelsUnavailable] = useState(false);

  const needsSubscriptionPicker = _SUBSCRIPTION_KEY in scenario.config;
  const [subscriptions, setSubscriptions] = useState<MaasSubscription[] | null>(null);
  const [subscriptionsUnavailable, setSubscriptionsUnavailable] = useState(false);

  useEffect(() => {
    if (!needsModelPicker) return;
    let cancelled = false;
    void getMaasModels().then((res) => {
      if (cancelled) return;
      if (res.available) setModels(res.items);
      else setModelsUnavailable(true);
    });
    return () => {
      cancelled = true;
    };
  }, [needsModelPicker]);

  useEffect(() => {
    if (!needsSubscriptionPicker) return;
    let cancelled = false;
    void getMaasSubscriptions().then((res) => {
      if (cancelled) return;
      if (res.available) setSubscriptions(res.items);
      else setSubscriptionsUnavailable(true);
    });
    return () => {
      cancelled = true;
    };
  }, [needsSubscriptionPicker]);

  function handleChange(key: string, raw: string, isNumber: boolean) {
    setValues((prev) => ({
      ...prev,
      [key]: isNumber ? (raw === '' ? 0 : Number(raw)) : raw,
    }));
  }

  function handleModelSelect(name: string, namespace: string) {
    setValues((prev) => {
      const next: ConfigValues = { ...prev, [_MODEL_NAME_KEY]: name, [_MODEL_NAMESPACE_KEY]: namespace };
      const subName = String(prev[_SUBSCRIPTION_KEY] ?? '');
      const sub = subName ? subscriptions?.find((s) => s.name === subName) : undefined;
      // Clear a now-incompatible subscription pin rather than let the two
      // fields silently mismatch (the create request would just fail).
      if (sub && !subscriptionCoversModel(sub, namespace, name)) {
        next[_SUBSCRIPTION_KEY] = '';
      }
      return next;
    });
  }

  function handleSubscriptionSelect(subName: string) {
    setValues((prev) => {
      const next: ConfigValues = { ...prev, [_SUBSCRIPTION_KEY]: subName };
      const sub = subscriptions?.find((s) => s.name === subName);
      const modelName = String(prev[_MODEL_NAME_KEY] ?? '');
      const modelNamespace = String(prev[_MODEL_NAMESPACE_KEY] ?? '');
      if (sub && modelName && modelNamespace && !subscriptionCoversModel(sub, modelNamespace, modelName)) {
        next[_MODEL_NAME_KEY] = '';
        next[_MODEL_NAMESPACE_KEY] = '';
      }
      return next;
    });
  }

  async function handleConfirm() {
    setLoading(true);
    try {
      const result = await createRun(scenario.name, values, autoCleanup);
      onConfirm(result.run_id);
    } finally {
      setLoading(false);
    }
  }

  const hasConfig = Object.keys(scenario.config).length > 0;

  const selectedSubscriptionName = String(values[_SUBSCRIPTION_KEY] ?? '');
  const selectedSubscription = selectedSubscriptionName
    ? subscriptions?.find((s) => s.name === selectedSubscriptionName)
    : undefined;
  const selectedModelName = String(values[_MODEL_NAME_KEY] ?? '');
  const selectedModelNamespace = String(values[_MODEL_NAMESPACE_KEY] ?? '');
  const selectedModel =
    selectedModelName && selectedModelNamespace
      ? models?.find((m) => m.name === selectedModelName && m.namespace === selectedModelNamespace)
      : undefined;

  // Cross-filter each picker's options by the other's current selection —
  // but never filter down to a dead end: if narrowing would leave nothing to
  // pick, fall back to the full list with a note instead of an empty select.
  let visibleModels = models ?? [];
  let modelListNote: string | null = null;
  if (models && selectedSubscription) {
    const narrowed = models.filter((m) => subscriptionCoversModel(selectedSubscription, m.namespace, m.name));
    if (narrowed.length > 0) {
      visibleModels = narrowed;
      modelListNote = 'Showing models covered by the selected subscription.';
    } else {
      modelListNote = 'No known models for the selected subscription — showing all models.';
    }
  }

  let visibleSubscriptions = subscriptions ?? [];
  let subscriptionListNote: string | null = null;
  if (subscriptions && selectedModel) {
    const narrowed = subscriptions.filter((s) => modelHasSubscription(selectedModel, s.name));
    if (narrowed.length > 0) {
      visibleSubscriptions = narrowed;
      subscriptionListNote = 'Showing subscriptions available for the selected model.';
    } else {
      subscriptionListNote = 'No known subscriptions cover the selected model — showing all subscriptions.';
    }
  }

  return (
    <Modal
      isOpen
      onClose={onCancel}
      aria-label={`Run ${scenario.name}`}
      title={`Run: ${scenario.name.split('_').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')}`}
      variant="medium"
      actions={[
        <Button
          key="confirm"
          variant="primary"
          onClick={() => void handleConfirm()}
          isLoading={loading}
          isDisabled={loading}
        >
          Launch Run
        </Button>,
        <Button key="cancel" variant="link" onClick={onCancel} isDisabled={loading}>
          Cancel
        </Button>,
      ]}
    >
      <p style={{ color: '#555', marginBottom: '1rem' }}>{scenario.description}</p>

      <div className="maaspal-config-form__field" style={{ marginBottom: hasConfig ? '1.25rem' : 0 }}>
        <Switch
          id="cfg-auto-cleanup"
          label="Auto cleanup"
          isChecked={autoCleanup}
          onChange={(_e, checked) => setAutoCleanup(checked)}
        />
        <p style={noteStyle}>
          Automatically revoke API keys and restore/delete any subscriptions this run creates.
          Turn off to inspect what a run leaves behind — you can turn it back on mid-run, or clean
          up manually afterward from the run's detail page.
        </p>
      </div>

      {hasConfig && (
        <>
          <p className="maaspal-section-heading" style={{ marginBottom: '0.75rem' }}>
            Configuration
          </p>
          <div className="maaspal-config-form">
            {Object.entries(scenario.config).map(([key, defaultVal]) => {
              const showingModelPicker = needsModelPicker && !!models && models.length > 0;
              const showingSubscriptionPicker =
                needsSubscriptionPicker && !!subscriptions && subscriptions.length > 0;

              // target_model_namespace is set together with target_model_name
              // by the picker below — no separate field for it. Only hidden
              // once the picker is actually rendering; if models never load
              // (RBAC/network issue) or the list comes back empty, this stays
              // a normal manual field alongside target_model_name's fallback.
              if (key === _MODEL_NAMESPACE_KEY && showingModelPicker) return null;

              if (key === _MODEL_NAME_KEY && showingModelPicker) {
                const currentName = String(values[_MODEL_NAME_KEY] ?? '');
                const currentNamespace = String(values[_MODEL_NAMESPACE_KEY] ?? '');
                const currentKey =
                  currentName && currentNamespace ? `${currentNamespace}/${currentName}` : '';
                return (
                  <div key={key} className="maaspal-config-form__field">
                    <label className="maaspal-config-form__label" htmlFor="cfg-target_model">
                      target model
                    </label>
                    <select
                      id="cfg-target_model"
                      value={currentKey}
                      onChange={(e) => {
                        const [namespace, name] = e.target.value.split('/');
                        if (name && namespace) handleModelSelect(name, namespace);
                      }}
                      style={selectStyle}
                    >
                      <option value="">Select a model exposed through MaaS…</option>
                      {visibleModels.map((m) => (
                        <option key={`${m.namespace}/${m.name}`} value={`${m.namespace}/${m.name}`}>
                          {m.display_name} ({m.namespace}/{m.name}){m.ready ? '' : ' — not ready'}
                        </option>
                      ))}
                    </select>
                    {modelListNote && <p style={noteStyle}>{modelListNote}</p>}
                  </div>
                );
              }

              // Fallback: plain text inputs — used for every other field, and
              // for target_model_name/target_model_namespace too when the
              // MaaS Setup models list isn't available (RBAC/network issue)
              // or came back empty, so the scenario stays usable by hand.
              if (key === _MODEL_NAME_KEY && needsModelPicker && modelsUnavailable) {
                return (
                  <div key={key} className="maaspal-config-form__field">
                    <label className="maaspal-config-form__label" htmlFor={`cfg-${key}`}>
                      {key}
                    </label>
                    <input
                      id={`cfg-${key}`}
                      type="text"
                      value={values[key] ?? ''}
                      onChange={(e) => handleChange(key, e.target.value, false)}
                      style={selectStyle}
                    />
                    <p style={noteStyle}>
                      Couldn&apos;t load models from MaaS Setup — enter the MaaSModelRef name
                      manually (and its namespace below).
                    </p>
                  </div>
                );
              }

              if (key === _SUBSCRIPTION_KEY && showingSubscriptionPicker) {
                return (
                  <div key={key} className="maaspal-config-form__field">
                    <label className="maaspal-config-form__label" htmlFor="cfg-subscription">
                      subscription
                    </label>
                    <select
                      id="cfg-subscription"
                      value={selectedSubscriptionName}
                      onChange={(e) => handleSubscriptionSelect(e.target.value)}
                      style={selectStyle}
                    >
                      <option value="">Auto-select (highest eligible priority)</option>
                      {visibleSubscriptions.map((s) => {
                        const notes: string[] = [];
                        if (!s.ready) notes.push('not ready');
                        if (!s.owner.groups.includes(_HARNESS_OWNER_GROUP)) notes.push('not eligible for this SA');
                        const suffix = notes.length ? ` — ${notes.join(', ')}` : '';
                        return (
                          <option key={`${s.namespace}/${s.name}`} value={s.name}>
                            {s.display_name || s.name} ({s.namespace}/{s.name}) · priority{' '}
                            {s.priority ?? '—'}
                            {suffix}
                          </option>
                        );
                      })}
                    </select>
                    {subscriptionListNote && <p style={noteStyle}>{subscriptionListNote}</p>}
                  </div>
                );
              }

              if (key === _SUBSCRIPTION_KEY && needsSubscriptionPicker && subscriptionsUnavailable) {
                return (
                  <div key={key} className="maaspal-config-form__field">
                    <label className="maaspal-config-form__label" htmlFor={`cfg-${key}`}>
                      {key}
                    </label>
                    <input
                      id={`cfg-${key}`}
                      type="text"
                      value={values[key] ?? ''}
                      onChange={(e) => handleChange(key, e.target.value, false)}
                      style={selectStyle}
                    />
                    <p style={noteStyle}>
                      Couldn&apos;t load subscriptions from MaaS Setup — enter the MaaSSubscription
                      name manually, or leave blank for auto-selection.
                    </p>
                  </div>
                );
              }

              const isNumber = typeof defaultVal === 'number';
              const current = values[key];
              return (
                <div key={key} className="maaspal-config-form__field">
                  <label className="maaspal-config-form__label" htmlFor={`cfg-${key}`}>
                    {key}
                  </label>
                  <input
                    id={`cfg-${key}`}
                    type={isNumber ? 'number' : 'text'}
                    value={current ?? ''}
                    onChange={(e) => handleChange(key, e.target.value, isNumber)}
                    style={selectStyle}
                  />
                </div>
              );
            })}
          </div>
        </>
      )}
    </Modal>
  );
}
