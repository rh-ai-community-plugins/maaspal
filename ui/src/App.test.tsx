import { render, screen } from '@testing-library/react';
import App from './App';
import * as client from './api/client';

jest.mock('./api/client');

const mockListScenarios = client.listScenarios as jest.MockedFunction<typeof client.listScenarios>;
const mockListRuns = client.listRuns as jest.MockedFunction<typeof client.listRuns>;

test('renders the app heading', async () => {
  mockListScenarios.mockResolvedValue([]);
  mockListRuns.mockResolvedValue([]);

  render(<App />);

  // findByRole waits for async state updates from ScenarioList and RunHistory
  // The masthead brand is the logo image, not a text heading.
  expect(await screen.findByRole('img', { name: /maas:pal/i })).toBeInTheDocument();
});
