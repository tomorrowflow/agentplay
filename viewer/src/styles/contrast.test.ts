import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { ds } from '../../tailwind.config';

type Theme = 'light' | 'dark';

const css = fs.readFileSync(path.resolve(__dirname, 'index.css'), 'utf8');

const SURFACES = {
  card: 'var(--ds-surface-white)',
  page: 'var(--primitive-white-off-white)',
  'raised row': 'var(--ds-gray-subtlest)',
  'selected card': 'var(--ds-brand-lightest)',
};
const SECONDARY_TEXT = {
  'text-ds-text-subtle': ds.text.subtle,
  'text-ds-text-subtler': ds.text.subtler,
  '--ds-text-subtle': 'var(--ds-text-subtle)',
  '--ds-text-subtler': 'var(--ds-text-subtler)',
};

function themeVars(theme: Theme): Map<string, string> {
  const source = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const vars = new Map<string, string>();
  for (const [, selector, body] of source.matchAll(/(?:^|[{}])\s*(:root|\.dark)\s*\{([^{}]*)\}/g)) {
    if (selector === '.dark' && theme === 'light') continue;
    for (const [, name, value] of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
      vars.set(name, value.trim());
    }
  }
  return vars;
}

function resolve(vars: Map<string, string>, value: string): string {
  const ref = /^var\((--[\w-]+)\)$/.exec(value);
  if (!ref) return value;
  const next = vars.get(ref[1]);
  if (next === undefined) throw new Error(`${ref[1]} is not defined`);
  return resolve(vars, next);
}

function luminance(hex: string): number {
  const match = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!match) throw new Error(`${hex} is not a #rrggbb colour`);
  const n = parseInt(match[1], 16);
  const [r, g, b] = [n >> 16, (n >> 8) & 255, n & 255].map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe.each<Theme>(['light', 'dark'])('%s theme', (theme) => {
  const vars = themeVars(theme);
  const color = (value: string) => resolve(vars, value);

  it.each(
    Object.entries(SECONDARY_TEXT).flatMap(([text, value]) =>
      Object.entries(SURFACES).map(([surface, bg]) => [text, surface, value, bg])
    )
  )('%s on the %s surface meets WCAG AA for normal text', (_text, _surface, value, bg) => {
    expect(contrast(color(value), color(bg)), `${color(value)} on ${color(bg)}`).toBeGreaterThanOrEqual(4.5);
  });

  it.each(Object.entries(SURFACES))(
    'the focus ring colour reaches 3:1 on the %s surface',
    (_surface, bg) => {
      expect(contrast(color(ds.brand.subtle), color(bg))).toBeGreaterThanOrEqual(3);
    }
  );

  it('keeps subtler quieter than subtle, and subtle quieter than default', () => {
    const onCard = (text: string) => contrast(color(text), color(SURFACES.card));
    expect(onCard(ds.text.subtler)).toBeLessThan(onCard(ds.text.subtle));
    expect(onCard(ds.text.subtle)).toBeLessThan(onCard(ds.text.default));
  });
});
