import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import App, { RunsPage, ScenariosPage } from './App';
import * as client from './api/client';

jest.mock('./api/client');

const mockListScenarios = client.listScenarios as jest.MockedFunction<typeof client.listScenarios>;
const mockListRuns = client.listRuns as jest.MockedFunction<typeof client.listRuns>;

beforeEach(() => {
  mockListScenarios.mockResolvedValue([]);
  mockListRuns.mockResolvedValue([]);
});

test('shows the community banner and the MaaS:PAL header', () => {
  render(<App />);

  expect(screen.getByText('Community Plugin')).toBeInTheDocument();
  expect(screen.getByRole('heading', { level: 1, name: 'MaaS:PAL' })).toBeInTheDocument();
  expect(screen.getByRole('img', { name: /maas:pal logo/i })).toBeInTheDocument();
});

test('the root path redirects to the scenarios page', () => {
  // jest.setup.tsx renders only the first <Route>, i.e. the "/" redirect.
  render(<App />);

  expect(screen.getByTestId('navigate')).toHaveAttribute('data-to', '/maaspal/scenarios');
});

test('scenarios page shows the catalog', async () => {
  render(
    <MemoryRouter>
      <ScenariosPage />
    </MemoryRouter>,
  );

  expect(screen.getByRole('heading', { name: 'Scenarios' })).toBeInTheDocument();
  expect(await screen.findByText(/no scenarios available/i)).toBeInTheDocument();
  expect(mockListScenarios).toHaveBeenCalled();
});

test('runs page shows run history on its own', async () => {
  render(<RunsPage />);

  expect(screen.getByRole('heading', { name: 'Runs' })).toBeInTheDocument();
  expect(await screen.findByText(/no runs yet/i)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /run a scenario/i })).toBeInTheDocument();
});
