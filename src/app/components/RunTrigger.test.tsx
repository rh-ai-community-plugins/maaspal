import { render, screen, act, fireEvent, waitFor } from '@testing-library/react';
import { RunTrigger } from './RunTrigger';
import type { Scenario } from '../api/client';

function mockFetch(
  modelsResponse: unknown = { available: true, reason: null, items: [] },
  subscriptionsResponse: unknown = { available: true, reason: null, items: [] },
) {
  global.fetch = jest.fn((url: string) => {
    if (url === '/maaspal/api/maas/models') {
      return Promise.resolve({
        ok: true,
        json: async () => modelsResponse,
      } as unknown as Response);
    }
    if (url === '/maaspal/api/maas/subscriptions') {
      return Promise.resolve({
        ok: true,
        json: async () => subscriptionsResponse,
      } as unknown as Response);
    }
    return Promise.resolve({
      ok: true,
      json: async () => ({ run_id: 'run-1' }),
    } as unknown as Response);
  }) as unknown as typeof fetch;
}

afterEach(() => {
  jest.restoreAllMocks();
});

const rateLimitScenario: Scenario = {
  name: 'rate_limit_validation',
  description: 'test',
  category: 'Rate Limiting',
  config: {
    target_model_name: '',
    target_model_namespace: '',
    token_limit: 50,
  },
};

const plainScenario: Scenario = {
  name: 'single_key_load',
  description: 'test',
  category: 'Load Testing',
  config: {
    request_count: 100,
    concurrency: 5,
  },
};

const subscriptionOnlyScenario: Scenario = {
  name: 'subscription_only',
  description: 'test',
  category: 'Load Testing',
  config: {
    subscription: '',
  },
};

const singleKeyLoadScenario: Scenario = {
  name: 'single_key_load',
  description: 'test',
  category: 'Load Testing',
  config: {
    request_count: 100,
    concurrency: 5,
    subscription: '',
    target_model_name: '',
    target_model_namespace: '',
  },
};

const modelA = {
  name: 'model-a',
  namespace: 'llm',
  display_name: 'Model A',
  ready: true,
  subscriptions: [{ name: 'sub-a', display_name: 'Sub A', description: '' }],
};
const modelB = {
  name: 'model-b',
  namespace: 'llm',
  display_name: 'Model B',
  ready: true,
  subscriptions: [{ name: 'sub-b', display_name: 'Sub B', description: '' }],
};
const subA = {
  name: 'sub-a',
  namespace: 'models-as-a-service',
  display_name: 'Sub A',
  priority: 100,
  ready: true,
  owner: { groups: ['system:authenticated'], users: [] },
  model_refs: [{ name: 'model-a', namespace: 'llm', token_rate_limits: [], display_name: 'Model A', model_exists: true, model_ready: true, has_auth_policy: true }],
};
const subB = {
  name: 'sub-b',
  namespace: 'models-as-a-service',
  display_name: 'Sub B',
  priority: 50,
  ready: true,
  owner: { groups: ['system:authenticated'], users: [] },
  model_refs: [{ name: 'model-b', namespace: 'llm', token_rate_limits: [], display_name: 'Model B', model_exists: true, model_ready: true, has_auth_policy: true }],
};

test('scenarios without target_model_name/namespace or subscription never fetch either endpoint', async () => {
  mockFetch();

  await act(async () => {
    render(<RunTrigger scenario={plainScenario} onConfirm={jest.fn()} onCancel={jest.fn()} />);
  });

  expect(screen.getByLabelText('Request count')).toBeInTheDocument();
  expect(global.fetch).not.toHaveBeenCalledWith('/maaspal/api/maas/models');
  expect(global.fetch).not.toHaveBeenCalledWith('/maaspal/api/maas/subscriptions');
});

test('renders a model picker populated from /api/maas/models', async () => {
  mockFetch({
    available: true,
    reason: null,
    items: [
      { name: 'facebook-opt-125m-simulated', namespace: 'llm', display_name: 'Simulator', ready: true, subscriptions: [] },
    ],
  });

  await act(async () => {
    render(
      <RunTrigger scenario={rateLimitScenario} onConfirm={jest.fn()} onCancel={jest.fn()} />,
    );
  });

  await waitFor(() => screen.getByLabelText('Model'));

  // Underlying raw fields are no longer shown once the picker is active.
  expect(screen.queryByLabelText('target_model_name')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('target_model_namespace')).not.toBeInTheDocument();

  const select = screen.getByLabelText('Model') as HTMLSelectElement;
  fireEvent.change(select, { target: { value: 'llm/facebook-opt-125m-simulated' } });
  expect(select.value).toBe('llm/facebook-opt-125m-simulated');
});

test('falls back to manual name/namespace fields when models are unavailable', async () => {
  mockFetch({ available: false, reason: 'forbidden', items: [] });

  await act(async () => {
    render(
      <RunTrigger scenario={rateLimitScenario} onConfirm={jest.fn()} onCancel={jest.fn()} />,
    );
  });

  await waitFor(() => screen.getByLabelText('Target model name'));

  expect(screen.getByLabelText('Target model name')).toBeInTheDocument();
  expect(screen.getByLabelText('Target model namespace')).toBeInTheDocument();
  expect(screen.getByText(/couldn't load models/i)).toBeInTheDocument();
});

test('renders a subscription picker populated from /api/maas/subscriptions, with no model fields to cross-filter', async () => {
  mockFetch(undefined, { available: true, reason: null, items: [subA, subB] });

  await act(async () => {
    render(
      <RunTrigger scenario={subscriptionOnlyScenario} onConfirm={jest.fn()} onCancel={jest.fn()} />,
    );
  });

  await waitFor(() => screen.getByLabelText('Subscription'));

  const select = screen.getByLabelText('Subscription') as HTMLSelectElement;
  expect(select.options).toHaveLength(3); // auto-select + sub-a + sub-b
  fireEvent.change(select, { target: { value: 'sub-a' } });
  expect(select.value).toBe('sub-a');
});

test('falls back to manual subscription field when subscriptions are unavailable', async () => {
  mockFetch(undefined, { available: false, reason: 'forbidden', items: [] });

  await act(async () => {
    render(
      <RunTrigger scenario={subscriptionOnlyScenario} onConfirm={jest.fn()} onCancel={jest.fn()} />,
    );
  });

  await waitFor(() => screen.getByLabelText('Subscription'));

  const field = screen.getByLabelText('Subscription');
  expect(field.tagName).toBe('INPUT');
  expect(screen.getByText(/couldn't load subscriptions/i)).toBeInTheDocument();
});

test('subscription and model pickers cross-filter each other', async () => {
  mockFetch(
    { available: true, reason: null, items: [modelA, modelB] },
    { available: true, reason: null, items: [subA, subB] },
  );

  await act(async () => {
    render(
      <RunTrigger scenario={singleKeyLoadScenario} onConfirm={jest.fn()} onCancel={jest.fn()} />,
    );
  });

  await waitFor(() => screen.getByLabelText('Subscription'));
  const subSelect = screen.getByLabelText('Subscription') as HTMLSelectElement;
  const modelSelect = screen.getByLabelText('Model') as HTMLSelectElement;

  // Before any selection, both pickers show everything.
  expect(subSelect.options).toHaveLength(3);
  expect(modelSelect.options).toHaveLength(3);

  // Picking sub-a narrows the model picker to model-a only (+ the placeholder).
  fireEvent.change(subSelect, { target: { value: 'sub-a' } });
  await waitFor(() => expect(modelSelect.options).toHaveLength(2));
  expect(screen.getByText(/showing models covered by the selected subscription/i)).toBeInTheDocument();

  // Reset, then pick model-b first — narrows the subscription picker to sub-b only.
  fireEvent.change(subSelect, { target: { value: '' } });
  await waitFor(() => expect(modelSelect.options).toHaveLength(3));
  fireEvent.change(modelSelect, { target: { value: 'llm/model-b' } });
  await waitFor(() => expect(subSelect.options).toHaveLength(2));
  expect(screen.getByText(/showing subscriptions available for the selected model/i)).toBeInTheDocument();
});

test('picking an incompatible model clears a subscription pin the fetched models could not confirm', async () => {
  // sub-c targets a model that never shows up in /api/maas/models (e.g. a
  // dangling modelRef) — narrowing would be empty, so the model picker falls
  // back to showing everything unfiltered, and remains genuinely selectable.
  const subC = { ...subA, name: 'sub-c', display_name: 'Sub C', model_refs: [{ ...subA.model_refs[0], name: 'model-zzz' }] };
  mockFetch(
    { available: true, reason: null, items: [modelA, modelB] },
    { available: true, reason: null, items: [subC] },
  );

  await act(async () => {
    render(
      <RunTrigger scenario={singleKeyLoadScenario} onConfirm={jest.fn()} onCancel={jest.fn()} />,
    );
  });

  await waitFor(() => screen.getByLabelText('Subscription'));
  const subSelect = screen.getByLabelText('Subscription') as HTMLSelectElement;
  const modelSelect = screen.getByLabelText('Model') as HTMLSelectElement;

  fireEvent.change(subSelect, { target: { value: 'sub-c' } });
  await waitFor(() =>
    expect(screen.getByText(/no known models for the selected subscription/i)).toBeInTheDocument(),
  );
  expect(modelSelect.options).toHaveLength(3); // fallback: unfiltered

  fireEvent.change(modelSelect, { target: { value: 'llm/model-a' } });
  await waitFor(() => expect(subSelect.value).toBe(''));
});

test('auto cleanup defaults on and is sent with the launch request', async () => {
  mockFetch();

  await act(async () => {
    render(<RunTrigger scenario={plainScenario} onConfirm={jest.fn()} onCancel={jest.fn()} />);
  });

  const toggle = screen.getByLabelText('Auto cleanup') as HTMLInputElement;
  expect(toggle.checked).toBe(true);

  fireEvent.click(screen.getByRole('button', { name: /launch run/i }));

  await waitFor(() => expect(global.fetch).toHaveBeenCalledWith('/maaspal/api/runs', expect.anything()));
  const [, init] = (global.fetch as jest.Mock).mock.calls.find(([url]) => url === '/maaspal/api/runs')!;
  const body = JSON.parse(init.body as string);
  expect(body.auto_cleanup).toBe(true);
});

test('unchecking auto cleanup sends auto_cleanup: false', async () => {
  mockFetch();

  await act(async () => {
    render(<RunTrigger scenario={plainScenario} onConfirm={jest.fn()} onCancel={jest.fn()} />);
  });

  fireEvent.click(screen.getByLabelText('Auto cleanup'));
  fireEvent.click(screen.getByRole('button', { name: /launch run/i }));

  await waitFor(() => expect(global.fetch).toHaveBeenCalledWith('/maaspal/api/runs', expect.anything()));
  const [, init] = (global.fetch as jest.Mock).mock.calls.find(([url]) => url === '/maaspal/api/runs')!;
  const body = JSON.parse(init.body as string);
  expect(body.auto_cleanup).toBe(false);
});

const subWithLimit = {
  ...subA,
  model_refs: [{ ...subA.model_refs[0], token_rate_limits: [{ limit: 100, window: '1m' }] }],
};
const modelAWithRoute = { ...modelA, http_route: 'llm/model-a-kserve-route' };

const rateLimitV2: Scenario = {
  name: 'verify_subscription_rate_limit',
  title: "Is my subscription's rate limit enforced?",
  summary: 'Shows exactly when MaaS starts throttling.',
  description: 'How it works text',
  category: 'Rate limits',
  kind: 'verify',
  requires: ['target_model_name', 'target_model_namespace'],
  plan_template: 'Send to ${model} on ${subscription}, expect throttling at ${config.token_limit} per ${config.token_window}.',
  inputs: {
    mode: { label: 'Subscription to test', choices: ['existing', 'temporary'] },
    subscription: { label: 'Subscription', show_if: { mode: 'existing' } },
    token_limit: { label: 'Token limit', from_subscription: 'limit' },
    token_window: { label: 'Window', from_subscription: 'window' },
    limitador_namespace: { advanced: true, from_model: 'http_route' },
    new_subscription_name: { advanced: true, show_if: { mode: 'temporary' } },
  },
  config: {
    mode: 'existing',
    subscription: '',
    target_model_name: '',
    target_model_namespace: '',
    token_limit: 50,
    token_window: '24h',
    limitador_namespace: '',
    new_subscription_name: 'maaspal-rate-limit-test',
  },
};

test('uses the scenario title, labels, and a "what this run will do" preview', async () => {
  mockFetch({ available: true, reason: null, items: [modelAWithRoute] }, { available: true, reason: null, items: [subWithLimit] });

  await act(async () => {
    render(<RunTrigger scenario={rateLimitV2} onConfirm={jest.fn()} onCancel={jest.fn()} />);
  });

  expect(screen.getByText("Is my subscription's rate limit enforced?")).toBeInTheDocument();
  expect(screen.getByLabelText('Token limit')).toBeInTheDocument();
  expect(screen.getByText(/send to the default model on an auto-selected subscription, expect throttling at 50 per 24h/i)).toBeInTheDocument();
});

test('picking a subscription and model autofills the limit, window and HTTPRoute', async () => {
  mockFetch({ available: true, reason: null, items: [modelAWithRoute] }, { available: true, reason: null, items: [subWithLimit] });

  await act(async () => {
    render(<RunTrigger scenario={rateLimitV2} onConfirm={jest.fn()} onCancel={jest.fn()} />);
  });
  await waitFor(() => screen.getByLabelText('Subscription'));

  fireEvent.change(screen.getByLabelText('Model *'), { target: { value: 'llm/model-a' } });
  fireEvent.change(screen.getByLabelText('Subscription'), { target: { value: 'sub-a' } });

  expect((screen.getByLabelText('Token limit') as HTMLInputElement).value).toBe('100');
  expect((screen.getByLabelText('Window') as HTMLInputElement).value).toBe('1m');
  // Advanced field, filled even while collapsed — check what gets launched.
  fireEvent.click(screen.getByRole('button', { name: /launch run/i }));
  await waitFor(() => expect(global.fetch).toHaveBeenCalledWith('/maaspal/api/runs', expect.anything()));
  const call = (global.fetch as jest.Mock).mock.calls.find((c) => c[0] === '/maaspal/api/runs');
  expect(JSON.parse(call[1].body).config_overrides).toMatchObject({
    token_limit: 100,
    token_window: '1m',
    limitador_namespace: 'llm/model-a-kserve-route',
  });
});

test('launch stays disabled until required fields are filled', async () => {
  mockFetch({ available: true, reason: null, items: [modelAWithRoute] }, { available: true, reason: null, items: [subWithLimit] });

  await act(async () => {
    render(<RunTrigger scenario={rateLimitV2} onConfirm={jest.fn()} onCancel={jest.fn()} />);
  });
  await waitFor(() => screen.getByLabelText('Model *'));

  expect(screen.getByRole('button', { name: /launch run/i })).toBeDisabled();
  expect(screen.getByText(/required before launching: model/i)).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText('Model *'), { target: { value: 'llm/model-a' } });
  expect(screen.getByRole('button', { name: /launch run/i })).toBeEnabled();
});

test('show_if hides fields that do not apply to the selected mode', async () => {
  mockFetch({ available: true, reason: null, items: [modelAWithRoute] }, { available: true, reason: null, items: [subWithLimit] });

  await act(async () => {
    render(<RunTrigger scenario={rateLimitV2} onConfirm={jest.fn()} onCancel={jest.fn()} />);
  });
  await waitFor(() => screen.getByLabelText('Subscription'));

  fireEvent.change(screen.getByLabelText('Subscription to test'), { target: { value: 'temporary' } });
  expect(screen.queryByLabelText('Subscription')).not.toBeInTheDocument();
});

test('help text lives in an info popover, and the description under "More details"', async () => {
  mockFetch({ available: true, reason: null, items: [modelAWithRoute] }, { available: true, reason: null, items: [subWithLimit] });
  const scenario: Scenario = {
    ...rateLimitV2,
    inputs: { ...rateLimitV2.inputs, token_limit: { label: 'Token limit', help: 'How many tokens per window.' } },
  };

  await act(async () => {
    render(<RunTrigger scenario={scenario} onConfirm={jest.fn()} onCancel={jest.fn()} />);
  });

  expect(screen.queryByText('How it works')).not.toBeInTheDocument();
  expect(screen.queryByText('How many tokens per window.')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'More info about Token limit' }));
  expect(await screen.findByText('How many tokens per window.')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'More details' }));
  expect(screen.getByText('How it works text')).toBeInTheDocument();
});

const requestApiScenario: Scenario = {
  name: 'smoke_test',
  description: 'test',
  category: 'Quick check',
  inputs: {
    request_api: {
      label: 'Request API',
      advanced: true,
      choices: ['chat_completions', 'completions'],
      choice_labels: { chat_completions: '/v1/chat/completions', completions: '/v1/completions' },
    },
    stream: { label: 'Streaming', advanced: true },
  },
  config: { request_count: 3, request_api: 'chat_completions', stream: false },
};

test('Request API shows endpoint paths, Streaming is a checkbox, and both are sent as typed values', async () => {
  mockFetch();

  await act(async () => {
    render(<RunTrigger scenario={requestApiScenario} onConfirm={jest.fn()} onCancel={jest.fn()} />);
  });
  fireEvent.click(screen.getByRole('button', { name: /advanced settings/i }));

  const api = screen.getByLabelText('Request API') as HTMLSelectElement;
  expect(screen.getByRole('option', { name: '/v1/chat/completions' })).toBeInTheDocument();
  fireEvent.change(api, { target: { value: 'completions' } });

  const stream = screen.getByLabelText('Streaming') as HTMLInputElement;
  expect(stream.type).toBe('checkbox');
  expect(stream.checked).toBe(false);
  fireEvent.click(stream);

  fireEvent.click(screen.getByRole('button', { name: /launch run/i }));
  await waitFor(() => expect(global.fetch).toHaveBeenCalledWith('/maaspal/api/runs', expect.anything()));
  const [, init] = (global.fetch as jest.Mock).mock.calls.find(([url]) => url === '/maaspal/api/runs')!;
  const body = JSON.parse(init.body as string);
  expect(body.config_overrides.request_api).toBe('completions');
  expect(body.config_overrides.stream).toBe(true);
});
