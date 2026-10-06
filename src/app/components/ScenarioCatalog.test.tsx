import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { ScenarioCatalog } from './ScenarioCatalog';
import * as client from '../api/client';
import type { Scenario } from '../api/client';
import { PalProvider } from '../pal/usePal';

jest.mock('../api/client');

const mockListScenarios = client.listScenarios as jest.MockedFunction<typeof client.listScenarios>;

const smoke: Scenario = {
  name: 'smoke_test',
  title: 'Is MaaS working end to end?',
  summary: 'The 30-second check.',
  description: 'long description',
  config: {},
  category: 'Quick check',
  kind: 'verify',
  mutates: ['api_keys'],
  est_duration: '~30 s',
};
const window_: Scenario = {
  name: 'rate_limit_window_recovery',
  title: 'Does access come back?',
  description: 'd',
  config: {},
  category: 'Rate limits',
  kind: 'explore',
  mutates: ['subscriptions'],
};
const health: Scenario = {
  name: 'model_config_health',
  title: 'Are my models wired up?',
  description: 'd',
  config: {},
  category: 'Diagnostics',
  kind: 'verify',
};

function renderCatalog(url = '/maaspal/scenarios', onRun: (s: Scenario) => void = () => undefined) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <ScenarioCatalog onRun={onRun} />
    </MemoryRouter>,
  );
}

function groupTitles(): string[] {
  return screen.getAllByRole('region').map((r) => r.getAttribute('aria-label') ?? '');
}

function cardTitles(region: string): string[] {
  return within(screen.getByRole('region', { name: region }))
    .getAllByRole('button', { name: /^run /i })
    .map((b) => (b.getAttribute('aria-label') ?? '').replace(/^Run /, ''));
}

test('shows titles and summaries, grouped by category, with counts in the rail', async () => {
  mockListScenarios.mockResolvedValue([smoke, window_]);

  renderCatalog();

  expect(await screen.findByText('Is MaaS working end to end?')).toBeInTheDocument();
  expect(screen.getByText('The 30-second check.')).toBeInTheDocument();
  expect(screen.getByText('Does access come back?')).toBeInTheDocument();
  expect(groupTitles()).toEqual(['Quick check', 'Rate limits']);
  expect(screen.getByRole('button', { name: /^all\s*2$/i })).toBeInTheDocument();
});

test('falls back to the title-cased name when a scenario has no title', async () => {
  mockListScenarios.mockResolvedValue([
    { name: 'my_scenario', description: 'A user-authored scenario', config: {}, category: 'Custom', custom: true },
  ]);

  renderCatalog();

  expect(await screen.findByText('My Scenario')).toBeInTheDocument();
});

test('badges say whether a scenario creates temporary resources, needs extra RBAC, or is custom', async () => {
  mockListScenarios.mockResolvedValue([
    smoke,
    { ...window_, needs_rbac: ['user-provisioning'], custom: true },
    health,
  ]);

  renderCatalog();

  const cards = await screen.findAllByRole('button', { name: /^run /i });
  expect(cards).toHaveLength(3);
  const rails = screen.getByRole('complementary', { name: /scenario filters/i });
  const main = (el: HTMLElement) => !rails.contains(el);
  expect(screen.getAllByText('Uses your setup').filter(main)).toHaveLength(1);
  expect(screen.getAllByText('Creates temporary resources').filter(main)).toHaveLength(1);
  expect(screen.getAllByText('Read-only').filter(main)).toHaveLength(1);
  expect(screen.getByText('Needs extra RBAC')).toBeInTheDocument();
  expect(screen.getAllByText('Custom').filter(main)).toHaveLength(1);
  expect(screen.getByText('~30 s')).toBeInTheDocument();
});

test('custom scenarios come first: their own Custom category leads, and inside a built-in category they precede built-ins', async () => {
  mockListScenarios.mockResolvedValue([
    { name: 'a_verify', title: 'Verify one', description: 'd', config: {}, category: 'Rate limits', kind: 'verify' },
    { name: 'b_explore', title: 'Explore one', description: 'd', config: {}, category: 'Rate limits', kind: 'explore' },
    { name: 'team', title: 'Team one', description: 'd', config: {}, category: 'Rate limits', kind: 'explore', custom: true },
    smoke,
    { name: 'nightly', title: 'Nightly one', description: 'd', config: {}, category: 'Our team', custom: true },
  ]);

  renderCatalog();

  await screen.findByText('Verify one');
  expect(groupTitles()).toEqual(['Custom', 'Quick check', 'Rate limits']);
  expect(cardTitles('Custom')).toEqual(['Nightly one']);
  expect(cardTitles('Rate limits')).toEqual(['Team one', 'Verify one', 'Explore one']);
});

test('no Custom category or Source filter when every scenario is built-in', async () => {
  mockListScenarios.mockResolvedValue([smoke]);

  renderCatalog();

  await screen.findByText('Is MaaS working end to end?');
  expect(groupTitles()).toEqual(['Quick check']);
  expect(screen.queryByText('Source')).not.toBeInTheDocument();
});

test('search narrows the gallery and the rail counts', async () => {
  mockListScenarios.mockResolvedValue([smoke, window_]);
  renderCatalog();
  await screen.findByText('Is MaaS working end to end?');

  await userEvent.type(screen.getByRole('textbox', { name: /search scenarios/i }), 'access');

  expect(screen.queryByText('Is MaaS working end to end?')).not.toBeInTheDocument();
  expect(screen.getByText('Does access come back?')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /^all\s*1$/i })).toBeInTheDocument();
});

test('picking a category shows only that category', async () => {
  mockListScenarios.mockResolvedValue([smoke, window_]);
  renderCatalog();
  await screen.findByText('Is MaaS working end to end?');

  await userEvent.click(screen.getByRole('button', { name: /^rate limits\s*1$/i }));

  expect(groupTitles()).toEqual(['Rate limits']);
});

test('source and type filters combine', async () => {
  mockListScenarios.mockResolvedValue([smoke, { ...window_, custom: true }, health]);
  renderCatalog();
  await screen.findByText('Is MaaS working end to end?');

  await userEvent.click(screen.getByRole('checkbox', { name: 'Built-in' }));
  expect(groupTitles()).toEqual(['Quick check', 'Diagnostics']);

  await userEvent.click(screen.getByRole('checkbox', { name: 'Read-only' }));
  expect(groupTitles()).toEqual(['Diagnostics']);

  await userEvent.click(screen.getByRole('button', { name: /clear filters/i }));
  expect(groupTitles()).toEqual(['Quick check', 'Rate limits', 'Diagnostics']);
});

test('filters are read from the URL, so coming back restores them', async () => {
  mockListScenarios.mockResolvedValue([smoke, window_, health]);

  renderCatalog('/maaspal/scenarios?category=Diagnostics');

  await screen.findByText('Are my models wired up?');
  expect(groupTitles()).toEqual(['Diagnostics']);
});

test('shows a no-match state that clears the filters', async () => {
  mockListScenarios.mockResolvedValue([smoke]);
  renderCatalog('/maaspal/scenarios?q=nothing-matches');

  expect(await screen.findByText(/no scenarios match/i)).toBeInTheDocument();
  await userEvent.click(screen.getAllByRole('button', { name: /clear filters/i })[0]);
  expect(screen.getByText('Is MaaS working end to end?')).toBeInTheDocument();
});

test('clicking anywhere on a card launches that scenario; there is no separate Run button', async () => {
  mockListScenarios.mockResolvedValue([smoke]);
  const onRun = jest.fn();
  renderCatalog('/maaspal/scenarios', onRun);

  await userEvent.click(await screen.findByText('The 30-second check.'));
  await userEvent.click(screen.getByText('Is MaaS working end to end?'));

  expect(onRun).toHaveBeenCalledTimes(2);
  expect(onRun).toHaveBeenCalledWith(smoke);
  expect(screen.queryByText(/^Run$/)).not.toBeInTheDocument();
});

test('a card can be launched from the keyboard', async () => {
  mockListScenarios.mockResolvedValue([smoke]);
  const onRun = jest.fn();
  renderCatalog('/maaspal/scenarios', onRun);

  const card = await screen.findByRole('button', { name: /run is maas working/i });
  card.focus();
  await userEvent.keyboard('{Enter}');
  await userEvent.keyboard(' ');

  expect(onRun).toHaveBeenCalledTimes(2);
});

test('renders empty state when no scenarios are returned', async () => {
  mockListScenarios.mockResolvedValue([]);

  renderCatalog();

  expect(await screen.findByText(/no scenarios available/i)).toBeInTheDocument();
});

describe('the PAL easter egg scenario', () => {
  const feedPal: Scenario = {
    name: 'feed_pal',
    title: 'Is PAL hungry?',
    description: 'd',
    config: {},
    category: 'Quick check',
    kind: 'explore',
    easter_egg: true,
  };

  afterEach(() => window.localStorage.clear());

  test('is hidden until PAL has hatched', async () => {
    mockListScenarios.mockResolvedValue([smoke, feedPal]);
    renderCatalog();

    expect(await screen.findByText('Is MaaS working end to end?')).toBeInTheDocument();
    expect(screen.queryByText('Is PAL hungry?')).not.toBeInTheDocument();
    expect(groupTitles()).toEqual(['Quick check']);
  });

  test('is listed first, in PAL’s own colour, once the egg is active', async () => {
    window.localStorage.setItem('maaspal.pal.active', '1');
    mockListScenarios.mockResolvedValue([smoke, feedPal]);
    render(
      <PalProvider>
        <MemoryRouter initialEntries={['/maaspal/scenarios']}>
          <ScenarioCatalog onRun={() => undefined} />
        </MemoryRouter>
      </PalProvider>,
    );

    expect(await screen.findByText('Is PAL hungry?')).toBeInTheDocument();
    expect(groupTitles()).toEqual(['PAL’s corner', 'Quick check']);
    expect(screen.getByRole('button', { name: 'Run Is PAL hungry?' })).toHaveAttribute('data-pal', 'true');
    expect(screen.getByText('Easter egg')).toBeInTheDocument();
  });
});
