import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ImportScenarioModal } from './ImportScenarioModal';

// Monaco doesn't run under jsdom — a textarea stands in for the editor.
jest.mock('./YamlEditor', () => ({
  YamlEditor: ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
    <textarea aria-label="Scenario YAML" value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}));

const YAML = 'name: my_check\ntasks: [{name: stub_pass}]\n';

function respond(status: number, body: unknown) {
  return Promise.resolve({ ok: status < 400, status, json: async () => body } as unknown as Response);
}

afterEach(() => jest.restoreAllMocks());

async function paste(text: string) {
  fireEvent.change(await screen.findByLabelText('Scenario YAML'), { target: { value: text } });
}

test('imports pasted YAML and hands back the new scenario', async () => {
  global.fetch = jest.fn(() => respond(201, { name: 'my_check' })) as unknown as typeof fetch;
  const onImported = jest.fn();
  render(<ImportScenarioModal onImported={onImported} onClose={jest.fn()} />);

  expect(screen.getByRole('button', { name: 'Import' })).toBeDisabled();
  await paste(YAML);
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
  });

  expect(global.fetch).toHaveBeenCalledWith('/maaspal/api/scenarios/import', expect.objectContaining({
    method: 'POST',
    body: JSON.stringify({ yaml: YAML, replace: false }),
  }));
  expect(onImported).toHaveBeenCalledWith({ name: 'my_check' });
});

test('a file fills the editor', async () => {
  render(<ImportScenarioModal onImported={jest.fn()} onClose={jest.fn()} />);
  const file = new File([YAML], 'mine.yaml', { type: 'text/yaml' });
  Object.defineProperty(file, 'text', { value: async () => YAML });
  await act(async () => {
    fireEvent.change(screen.getByTestId('import-file-input'), { target: { files: [file] } });
  });
  expect(await screen.findByLabelText('Scenario YAML')).toHaveValue(YAML);
  expect(screen.getByText('mine.yaml')).toBeInTheDocument();
});

test("shows the BFF's reason as received", async () => {
  const detail = 'Unknown task(s): send_reqests. Known tasks: pause, send_requests.';
  global.fetch = jest.fn(() => respond(400, { detail })) as unknown as typeof fetch;
  const onImported = jest.fn();
  render(<ImportScenarioModal onImported={onImported} onClose={jest.fn()} />);
  await paste(YAML);
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
  });
  expect(screen.getByText(detail)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Replace it' })).not.toBeInTheDocument();
  expect(onImported).not.toHaveBeenCalled();
});

test('a name taken by an earlier import can be replaced', async () => {
  global.fetch = jest
    .fn()
    .mockImplementationOnce(() => respond(409, { detail: 'An imported scenario is already called my_check.' }))
    .mockImplementationOnce(() => respond(201, { name: 'my_check' })) as unknown as typeof fetch;
  const onImported = jest.fn();
  render(<ImportScenarioModal onImported={onImported} onClose={jest.fn()} />);
  await paste(YAML);
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Replace it' }));
  });
  expect(global.fetch).toHaveBeenLastCalledWith('/maaspal/api/scenarios/import', expect.objectContaining({
    body: JSON.stringify({ yaml: YAML, replace: true }),
  }));
  await waitFor(() => expect(onImported).toHaveBeenCalled());
});

test('a built-in name cannot be replaced', async () => {
  global.fetch = jest.fn(() =>
    respond(409, { detail: 'A built-in scenario is already called smoke_test. Pick another `name:`.' }),
  ) as unknown as typeof fetch;
  render(<ImportScenarioModal onImported={jest.fn()} onClose={jest.fn()} />);
  await paste(YAML);
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
  });
  expect(screen.getByText(/built-in scenario is already called smoke_test/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Replace it' })).not.toBeInTheDocument();
});
