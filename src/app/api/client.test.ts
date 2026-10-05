import { listScenarios } from './client';

afterEach(() => {
  (global.fetch as jest.Mock | undefined)?.mockReset?.();
});

test("a refusal shows the BFF's explanation, not just the status code", async () => {
  global.fetch = jest.fn().mockResolvedValue({
    ok: false,
    status: 403,
    json: async () => ({ detail: "You don't have access to MaaS:PAL." }),
  }) as unknown as typeof fetch;

  await expect(listScenarios()).rejects.toThrow("You don't have access to MaaS:PAL.");
  expect(global.fetch).toHaveBeenCalledWith('/maaspal/api/scenarios');
});

test('a non-JSON error falls back to the status code', async () => {
  global.fetch = jest.fn().mockResolvedValue({
    ok: false,
    status: 502,
    json: async () => {
      throw new SyntaxError('not JSON');
    },
  }) as unknown as typeof fetch;

  await expect(listScenarios()).rejects.toThrow('listScenarios failed: 502');
});
