import { useEffect, useRef, useState } from 'react';
import blackBlue from './assets/logos/pal-black-blue.png';
import blackRed from './assets/logos/pal-black-red.png';
import pink from './assets/logos/pal-pink.png';
import purple from './assets/logos/pal-purple.png';
import redBlack from './assets/logos/pal-red-black.png';
import redWhite from './assets/logos/pal-red-white.png';
import whiteBlue from './assets/logos/pal-white-blue.png';
import whiteGrey from './assets/logos/pal-white-grey.png';
import whitePink from './assets/logos/pal-white-pink.png';
import whiteRed from './assets/logos/pal-white-red.png';

// The PAL mascot comes in ten colourways. A white body vanishes on the light
// theme and a black one on the dark theme, so each theme draws from its own set.
export const LOGO_SRC: Record<string, string> = {
  'black-blue': blackBlue,
  'black-red': blackRed,
  pink,
  purple,
  'red-black': redBlack,
  'red-white': redWhite,
  'white-blue': whiteBlue,
  'white-grey': whiteGrey,
  'white-pink': whitePink,
  'white-red': whiteRed,
};
export const LIGHT_LOGOS = ['red-white', 'black-red', 'red-black', 'black-blue', 'pink', 'purple'];
export const DARK_LOGOS = ['white-red', 'red-white', 'white-grey', 'red-black', 'white-blue', 'white-pink', 'pink'];

// The class the RHOAI dashboard (and PatternFly) put on <html> in dark mode.
const DARK_CLASS = 'pf-v6-theme-dark';

function isDark(): boolean {
  return document.documentElement.classList.contains(DARK_CLASS);
}

/** Whether the dashboard is in dark mode, following its theme toggle live. */
export function useIsDarkTheme(): boolean {
  const [dark, setDark] = useState(isDark);
  useEffect(() => {
    const observer = new MutationObserver(() => setDark(isDark()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    return () => observer.disconnect();
  }, []);
  return dark;
}

/** A random logo from `logos`, never `previous` when there's a choice. */
export function pickLogo(logos: string[], previous?: string): string {
  const options = logos.length > 1 ? logos.filter((l) => l !== previous) : logos;
  return options[Math.floor(Math.random() * options.length)];
}

/** A logo for the current theme that changes whenever `pageKey` does:
 * its name (see LIGHT_LOGOS/DARK_LOGOS) and image URL. */
export function useRotatingLogo(pageKey: string): { name: string; src: string } {
  const dark = useIsDarkTheme();
  const logos = dark ? DARK_LOGOS : LIGHT_LOGOS;
  const [logo, setLogo] = useState(() => pickLogo(logos));
  const previous = useRef(logo);
  const shownFor = useRef(`${pageKey}|${dark}`);
  useEffect(() => {
    const key = `${pageKey}|${dark}`;
    if (shownFor.current === key) return; // first render already picked one
    shownFor.current = key;
    const next = pickLogo(logos, previous.current);
    previous.current = next;
    setLogo(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pageKey, dark]);
  return { name: logo, src: LOGO_SRC[logo] };
}
