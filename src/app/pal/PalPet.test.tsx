import { act, render, screen } from '@testing-library/react';
import { HATCH_FRAMES, PalPet } from './PalPet';
import { PalProvider } from './usePal';
import { newPal, savePal } from './palState';
import * as client from '../api/client';

jest.mock('../api/client');

const mockListRuns = client.listRuns as jest.MockedFunction<typeof client.listRuns>;

beforeEach(() => {
  jest.useFakeTimers();
  mockListRuns.mockResolvedValue([]);
  window.localStorage.setItem('maaspal.pal.active', '1');
});

afterEach(() => {
  jest.useRealTimers();
  window.localStorage.clear();
  delete (window as { matchMedia?: unknown }).matchMedia;
});

function renderPet() {
  return render(
    <PalProvider>
      <PalPet />
    </PalProvider>,
  );
}

const phase = (container: HTMLElement) => container.querySelector('.maaspal-pal__hatch')?.getAttribute('data-phase');

test('a new PAL hatches frame by frame, then walks and says hello', () => {
  savePal(newPal('pink', Date.now()));
  const { container } = renderPet();

  const seen: (string | null | undefined)[] = [];
  for (const [, ms] of HATCH_FRAMES) {
    seen.push(phase(container));
    act(() => {
      jest.advanceTimersByTime(ms);
    });
  }
  expect(seen).toEqual(['drop', 'wobble', 'crack1', 'crack2', 'split']);
  expect(container.querySelector('.maaspal-pal__hatch')).toBeNull();
  expect(screen.getByRole('img', { name: /baby pal/i })).toBeInTheDocument();
  expect(screen.getByText('Hello, world!')).toBeInTheDocument();
});

test('with reduced motion it skips straight to the hatched PAL', () => {
  window.matchMedia = jest.fn().mockReturnValue({ matches: true }) as unknown as typeof window.matchMedia;
  savePal(newPal('pink', Date.now()));
  const { container } = renderPet();

  expect(container.querySelector('.maaspal-pal__hatch')).toBeNull();
  expect(screen.getByRole('img', { name: /baby pal/i })).toBeInTheDocument();
});

test('an already hatched PAL does not hatch again', () => {
  savePal({ ...newPal('pink', Date.now()), hatchedAt: Date.now() });
  const { container } = renderPet();

  expect(container.querySelector('.maaspal-pal__hatch')).toBeNull();
});
