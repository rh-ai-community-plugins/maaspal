import { act, renderHook } from '@testing-library/react';
import { DARK_LOGOS, LIGHT_LOGOS, pickLogo, useRotatingLogo } from './logos';


afterEach(() => {
  document.documentElement.classList.remove('pf-v6-theme-dark');
  jest.restoreAllMocks();
});

test('white-bodied logos are only used on the dark theme, black-bodied only on the light one', () => {
  expect(LIGHT_LOGOS.some((l) => l.startsWith('white-'))).toBe(false);
  expect(DARK_LOGOS.some((l) => l.startsWith('black-'))).toBe(false);
});

test('pickLogo never repeats the previous logo', () => {
  for (let i = 0; i < 50; i++) {
    expect(pickLogo(LIGHT_LOGOS, 'pink')).not.toBe('pink');
  }
  expect(pickLogo(['only'], 'only')).toBe('only');
});

test('the logo changes on navigation and follows the dashboard theme', async () => {
  const { result, rerender } = renderHook(({ page }) => useRotatingLogo(page).name, {
    initialProps: { page: '/maaspal/scenarios' },
  });
  const first = result.current;
  expect(LIGHT_LOGOS).toContain(first);

  rerender({ page: '/maaspal/runs' });
  expect(result.current).not.toBe(first);
  expect(LIGHT_LOGOS).toContain(result.current);

  await act(async () => {
    document.documentElement.classList.add('pf-v6-theme-dark');
    await Promise.resolve(); // let the MutationObserver fire
  });
  expect(DARK_LOGOS).toContain(result.current);
});

test('re-rendering the same page keeps the logo', () => {
  const { result, rerender } = renderHook(({ page }) => useRotatingLogo(page).name, {
    initialProps: { page: '/maaspal/runs' },
  });
  const first = result.current;
  rerender({ page: '/maaspal/runs' });
  expect(result.current).toBe(first);
});
