import fs from 'fs';
import path from 'path';

// Every colour comes from a token (src/app/styles/tokens.css), so both themes
// stay right without anyone having to remember to check dark mode. See
// docs/design/colors-and-components.md.

const SRC = path.resolve(__dirname, '../..');

// The only files allowed to hold colour literals.
const ALLOWED = new Set([
  'app/styles/tokens.css', // the palette itself
  'app/components/CommunityBanner.css', // [SHARED] — never edit
  'rhoai/CommunityNavIcon.tsx', // [SHARED] — never edit
  'app/components/MaaspalNavIcon.tsx', // sidebar icon, drawn by the host outside our theme
]);

const COLOUR_LITERAL = /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/g;

function sourceFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) return e.name === '__mocks__' ? [] : sourceFiles(full);
    return /\.(tsx?|css)$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [full] : [];
  });
}

test('no hard-coded colours outside tokens.css', () => {
  const offenders: string[] = [];
  for (const file of sourceFiles(SRC)) {
    const rel = path.relative(SRC, file).split(path.sep).join('/');
    if (ALLOWED.has(rel)) continue;
    fs.readFileSync(file, 'utf8')
      .split('\n')
      .forEach((line, i) => {
        // `#id` selectors and the like aren't colours; only flag real values.
        const hits = (line.match(COLOUR_LITERAL) ?? []).filter((m) => !/^#[0-9a-fA-F]{3,8}$/.test(m) || isColourContext(line));
        if (hits.length) offenders.push(`${rel}:${i + 1}: ${line.trim()}`);
      });
  }
  expect(offenders).toEqual([]);
});

// A hex literal counts when it's a CSS value or a quoted JS/JSX string.
function isColourContext(line: string): boolean {
  return /:\s*#|(?<!href=)['"`]#[0-9a-fA-F]{3,8}['"`]|,\s*#[0-9a-fA-F]{3,8}\)/.test(line);
}
