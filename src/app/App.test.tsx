import { render, screen } from '@testing-library/react';
import App, { RunsPage } from './App';
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
  expect(screen.getByRole('img', { name: /maas:pal/i })).toBeInTheDocument();
});

test('the root path redirects to the runs page', () => {
  // jest.setup.tsx renders only the first <Route>, i.e. the "/" redirect.
  render(<App />);

  expect(screen.getByTestId('navigate')).toHaveAttribute('data-to', '/maaspal/runs');
});

test('runs page lists scenarios and run history', async () => {
  render(<RunsPage />);

  expect(await screen.findByText(/no runs yet/i)).toBeInTheDocument();
  expect(mockListScenarios).toHaveBeenCalled();
});
