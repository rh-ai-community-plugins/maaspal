import { useEffect, useState } from 'react';
import {
  Alert,
  Button,
  ExpandableSection,
  FormSelect,
  FormSelectOption,
  Modal,
  ModalBody,
  ModalFooter,
  ModalHeader,
  Popover,
  Switch,
  TextInput,
} from '@patternfly/react-core';
import { OutlinedQuestionCircleIcon } from '@patternfly/react-icons';
import {
  createRun,
  getMaasModels,
  getMaasSubscriptions,
  type MaasModel,
  type MaasSubscription,
  type Scenario,
} from '../api/client';
import {
  _HARNESS_OWNER_GROUP,
  _MODEL_NAME_KEY,
  _MODEL_NAMESPACE_KEY,
  _SUBSCRIPTION_KEY,
  applyAutofill,
  humanizeKey,
  initValues,
  isVisible,
  missingRequired,
  modelHasSubscription,
  renderPlan,
  subscriptionCoversModel,
  type ConfigValues,
} from '../launchForm';
import { scenarioTitle } from '../scenarioTitles';
import { ScenarioBadges } from './ScenarioCatalog';
import { COLOR, toneColor } from '../styles/colors';

interface Props {
  scenario: Scenario;
  onConfirm: (runId: string) => void;
  onCancel: () => void;
}

const noteStyle = { color: COLOR.muted, fontSize: '0.75rem', margin: '0.25rem 0 0' };

export function RunTrigger({ scenario, onConfirm, onCancel }: Props) {
  const [loading, setLoading] = useState(false);
  const [values, setValues] = useState<ConfigValues>(() => initValues(scenario.config));
  const [autoCleanup, setAutoCleanup] = useState(true);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showMoreDetails, setShowMoreDetails] = useState(false);
  const inputs = scenario.inputs ?? {};
  const requires = new Set(scenario.requires ?? []);

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

  function handleCheck(key: string, checked: boolean) {
    setValues((prev) => ({ ...prev, [key]: checked }));
  }

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
      return applyAutofill(next, inputs, subscriptions, models);
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
      return applyAutofill(next, inputs, subscriptions, models);
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
  const missing = missingRequired(scenario, values);

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

  const showingModelPicker = needsModelPicker && !!models && models.length > 0;
  const showingSubscriptionPicker = needsSubscriptionPicker && !!subscriptions && subscriptions.length > 0;

  function labelFor(key: string, fallback?: string): string {
    const base = inputs[key]?.label ?? fallback ?? humanizeKey(key);
    return requires.has(key) ? `${base} *` : base;
  }

  /** ⓘ next to a label: what the setting means, for someone new to MaaS. */
  function infoFor(key: string) {
    const text = inputs[key]?.help;
    if (!text) return null;
    const label = inputs[key]?.label ?? humanizeKey(key);
    return (
      <Popover headerContent={label} bodyContent={text} position="top">
        <button
          type="button"
          className="maaspal-config-form__info"
          aria-label={`More info about ${label}`}
        >
          <OutlinedQuestionCircleIcon />
        </button>
      </Popover>
    );
  }

  function help(key: string) {
    const auto = inputs[key]?.from_subscription
      ? 'Auto-filled when you pick a subscription.'
      : inputs[key]?.from_model
        ? 'Auto-filled when you pick a model.'
        : null;
    return auto ? <p style={noteStyle}>{auto}</p> : null;
  }

  function renderField(key: string, defaultVal: Scenario['config'][string]) {
    // target_model_namespace is set together with target_model_name by the
    // picker below — no separate field for it. Only hidden once the picker
    // is actually rendering; if models never load (RBAC/network issue) or
    // the list comes back empty, this stays a normal manual field alongside
    // target_model_name's fallback.
    if (key === _MODEL_NAMESPACE_KEY && showingModelPicker) return null;

    if (key === _MODEL_NAME_KEY && showingModelPicker) {
      const currentKey =
        selectedModelName && selectedModelNamespace ? `${selectedModelNamespace}/${selectedModelName}` : '';
      const blankLabel =
        inputs[_MODEL_NAME_KEY]?.placeholder ??
        (requires.has(_MODEL_NAME_KEY) ? 'Select a model exposed through MaaS…' : 'Cluster default model');
      return (
        <div key={key} className="maaspal-config-form__field">
          <div className="maaspal-config-form__label-row">
            <label className="maaspal-config-form__label" htmlFor="cfg-target_model">
                        {labelFor(key, 'Model')}
            </label>
            {infoFor(key)}
          </div>
          <FormSelect
            id="cfg-target_model"
            value={currentKey}
            onChange={(_e, value) => {
              const [namespace, name] = value.split('/');
              if (name && namespace) handleModelSelect(name, namespace);
              else
                setValues((prev) => ({ ...prev, [_MODEL_NAME_KEY]: '', [_MODEL_NAMESPACE_KEY]: '' }));
            }}
          >
            <FormSelectOption value="" label={blankLabel} />
            {visibleModels.map((m) => (
              <FormSelectOption
                key={`${m.namespace}/${m.name}`}
                value={`${m.namespace}/${m.name}`}
                label={`${m.display_name} (${m.namespace}/${m.name})${m.ready ? '' : ' — not ready'}`}
              />
            ))}
          </FormSelect>
          {modelListNote && <p style={noteStyle}>{modelListNote}</p>}
          {help(key)}
        </div>
      );
    }

    // Fallback: plain text inputs — used for every other field, and for
    // target_model_name/target_model_namespace too when the MaaS overview
    // models list isn't available (RBAC/network issue) or came back empty,
    // so the scenario stays usable by hand.
    if (key === _MODEL_NAME_KEY && needsModelPicker && modelsUnavailable) {
      return (
        <div key={key} className="maaspal-config-form__field">
          <div className="maaspal-config-form__label-row">
            <label className="maaspal-config-form__label" htmlFor={`cfg-${key}`}>
                        {labelFor(key)}
            </label>
            {infoFor(key)}
          </div>
          <TextInput
            id={`cfg-${key}`}
            type="text"
            value={String(values[key] ?? '')}
            onChange={(_e, value) => handleChange(key, value, false)}
          />
          <p style={noteStyle}>
            Couldn&apos;t load models from the MaaS overview — enter the MaaSModelRef name manually (and its
            namespace below).
          </p>
        </div>
      );
    }

    if (key === _SUBSCRIPTION_KEY && showingSubscriptionPicker) {
      return (
        <div key={key} className="maaspal-config-form__field">
          <div className="maaspal-config-form__label-row">
            <label className="maaspal-config-form__label" htmlFor="cfg-subscription">
                        {labelFor(key, 'Subscription')}
            </label>
            {infoFor(key)}
          </div>
          <FormSelect
            id="cfg-subscription"
            value={selectedSubscriptionName}
            onChange={(_e, value) => handleSubscriptionSelect(value)}
          >
            <FormSelectOption
              value=""
              label={
                inputs[_SUBSCRIPTION_KEY]?.placeholder ??
                (requires.has(_SUBSCRIPTION_KEY) ? 'Select a subscription…' : 'Auto-select (highest eligible priority)')
              }
            />
            {visibleSubscriptions.map((s) => {
              const notes: string[] = [];
              if (!s.ready) notes.push('not ready');
              if (!s.owner.groups.includes(_HARNESS_OWNER_GROUP)) notes.push('not eligible for this SA');
              const suffix = notes.length ? ` — ${notes.join(', ')}` : '';
              return (
                <FormSelectOption
                  key={`${s.namespace}/${s.name}`}
                  value={s.name}
                  label={`${s.display_name || s.name} (${s.namespace}/${s.name}) · priority ${s.priority ?? '—'}${suffix}`}
                />
              );
            })}
          </FormSelect>
          {subscriptionListNote && <p style={noteStyle}>{subscriptionListNote}</p>}
          {help(key)}
        </div>
      );
    }

    if (key === _SUBSCRIPTION_KEY && needsSubscriptionPicker && subscriptionsUnavailable) {
      return (
        <div key={key} className="maaspal-config-form__field">
          <div className="maaspal-config-form__label-row">
            <label className="maaspal-config-form__label" htmlFor={`cfg-${key}`}>
                        {labelFor(key)}
            </label>
            {infoFor(key)}
          </div>
          <TextInput
            id={`cfg-${key}`}
            type="text"
            value={String(values[key] ?? '')}
            onChange={(_e, value) => handleChange(key, value, false)}
          />
          <p style={noteStyle}>
            Couldn&apos;t load subscriptions from the MaaS overview — enter the MaaSSubscription name
            manually, or leave blank for auto-selection.
          </p>
        </div>
      );
    }

    if (typeof defaultVal === 'boolean') {
      // Same shape as every other field — small label row on top, the control
      // where an input would be — so it lines up in the grid.
      return (
        <div key={key} className="maaspal-config-form__field">
          <div className="maaspal-config-form__label-row">
            <label className="maaspal-config-form__label" htmlFor={`cfg-${key}`}>
              {labelFor(key)}
            </label>
            {infoFor(key)}
          </div>
          <div className="maaspal-config-form__toggle">
            <Switch
              id={`cfg-${key}`}
              aria-label={inputs[key]?.label ?? humanizeKey(key)}
              hasCheckIcon
              isChecked={Boolean(values[key])}
              onChange={(_e, checked) => handleCheck(key, checked)}
            />
          </div>
          {help(key)}
        </div>
      );
    }

    const choices = inputs[key]?.choices;
    if (choices) {
      return (
        <div key={key} className="maaspal-config-form__field">
          <div className="maaspal-config-form__label-row">
            <label className="maaspal-config-form__label" htmlFor={`cfg-${key}`}>
                        {labelFor(key)}
            </label>
            {infoFor(key)}
          </div>
          <FormSelect
            id={`cfg-${key}`}
            value={String(values[key] ?? '')}
            onChange={(_e, value) => handleChange(key, value, false)}
          >
            {choices.map((c) => (
              <FormSelectOption key={c} value={c} label={inputs[key]?.choice_labels?.[c] ?? humanizeKey(c)} />
            ))}
          </FormSelect>
          {help(key)}
        </div>
      );
    }

    const isNumber = typeof defaultVal === 'number';
    return (
      <div key={key} className="maaspal-config-form__field">
        <div className="maaspal-config-form__label-row">
          <label className="maaspal-config-form__label" htmlFor={`cfg-${key}`}>
                    {labelFor(key)}
          </label>
          {infoFor(key)}
        </div>
        <TextInput
          id={`cfg-${key}`}
          type={isNumber ? 'number' : key.endsWith('_token') ? 'password' : 'text'}
          value={String(values[key] ?? '')}
          placeholder={inputs[key]?.placeholder}
          onChange={(_e, value) => handleChange(key, value, isNumber)}
        />
        {help(key)}
      </div>
    );
  }

  const visibleEntries = Object.entries(scenario.config).filter(([k]) => isVisible(inputs[k], values));
  const mainEntries = visibleEntries.filter(([k]) => !inputs[k]?.advanced);
  const advancedEntries = visibleEntries.filter(([k]) => inputs[k]?.advanced);

  return (
    <Modal
      isOpen
      onClose={onCancel}
      aria-labelledby="maaspal-run-trigger-title"
      variant="medium"
      className="maaspal-modal"
    >
      <ModalHeader title={scenarioTitle(scenario)} labelId="maaspal-run-trigger-title" />
      <ModalBody aria-label={`Run ${scenario.name}`}>
      <p style={{ color: COLOR.subtle, margin: '0 0 0.5rem' }}>{scenario.summary || scenario.description}</p>
      <div style={{ marginBottom: '0.5rem' }}>
        <ScenarioBadges scenario={scenario} />
      </div>
      {scenario.plan_template && (
        <Alert variant="info" isInline isPlain title="What this run will do" style={{ marginTop: '0.75rem' }}>
          {renderPlan(scenario.plan_template, values)}
          {scenario.description && scenario.description !== scenario.summary && (
            <ExpandableSection
              toggleText="More details"
              isExpanded={showMoreDetails}
              onToggle={(_e, expanded) => setShowMoreDetails(expanded)}
              style={{ marginTop: '0.35rem' }}
            >
              <p style={{ color: COLOR.subtle, fontSize: '0.85rem', margin: 0 }}>{scenario.description}</p>
            </ExpandableSection>
          )}
        </Alert>
      )}

      <div className="maaspal-config-form__field" style={{ margin: '1rem 0 1.25rem' }}>
        <Switch
          id="cfg-auto-cleanup"
          label="Auto cleanup"
          isChecked={autoCleanup}
          onChange={(_e, checked) => setAutoCleanup(checked)}
        />
        <p style={noteStyle}>
          Automatically revoke API keys and restore/delete any subscriptions this run creates.
          Turn off to inspect what a run leaves behind — you can turn it back on mid-run, or clean
          up manually afterward from the run&apos;s detail page.
        </p>
      </div>

      {hasConfig && (
        <>
          <p className="maaspal-section-heading" style={{ marginBottom: '0.75rem' }}>
            Configuration
          </p>
          <div className="maaspal-config-form">
            {mainEntries.map(([key, defaultVal]) => renderField(key, defaultVal))}
          </div>
          {advancedEntries.length > 0 && (
            <ExpandableSection
              toggleText={`Advanced settings (${advancedEntries.length})`}
              isExpanded={showAdvanced}
              onToggle={(_e, expanded) => setShowAdvanced(expanded)}
              style={{ marginTop: '0.75rem' }}
            >
              <div className="maaspal-config-form">
                {advancedEntries.map(([key, defaultVal]) => renderField(key, defaultVal))}
              </div>
            </ExpandableSection>
          )}
        </>
      )}

      {missing.length > 0 && (
        <p style={{ ...noteStyle, color: toneColor('danger'), marginTop: '0.75rem' }}>
          Required before launching:{' '}
          {missing
            // The model picker sets name and namespace together — one entry.
            .filter((k) => !(k === _MODEL_NAMESPACE_KEY && missing.includes(_MODEL_NAME_KEY)))
            .map((k) => inputs[k]?.label ?? (k === _MODEL_NAME_KEY ? 'Model' : humanizeKey(k)))
            .join(', ')}
        </p>
      )}
      </ModalBody>
      <ModalFooter>
        <Button
          key="confirm"
          variant="primary"
          onClick={() => void handleConfirm()}
          isLoading={loading}
          isDisabled={loading || missing.length > 0}
        >
          Launch Run
        </Button>
        <Button key="cancel" variant="link" onClick={onCancel} isDisabled={loading}>
          Cancel
        </Button>
      </ModalFooter>
    </Modal>
  );
}
