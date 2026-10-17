import tailwindcssAnimate from 'tailwindcss-animate';
import tailwindPlugin from 'tailwindcss/plugin';
import type { Config } from 'tailwindcss';

/** Design token colors mapped to primitive CSS vars. */
const ds = {
  gray: {
    lightest: 'var(--primitive-gray-50)',
    subtlest: 'var(--primitive-gray-100)',
    subtler: 'var(--primitive-gray-300)',
    subtle: 'var(--primitive-gray-500)',
    default: 'var(--primitive-gray-800)',
    darker: 'var(--primitive-gray-900)',
  },
  border: {
    subtlest: 'var(--primitive-gray-100)',
    subtler: 'var(--primitive-gray-200)',
    subtle: 'var(--primitive-gray-500)',
    default: 'var(--primitive-gray-800)',
    darker: 'var(--primitive-gray-900)',
  },
  warning: {
    // Semantic var, not a primitive: this rung is picked per mode. See the
    // note in src/styles/index.css.
    subtlest: 'var(--ds-warning-subtlest)',
    subtler: 'var(--primitive-orange-300)',
    subtle: 'var(--primitive-orange-500)',
    default: 'var(--primitive-orange-700)',
    darker: 'var(--primitive-orange-800)',
  },
  // Chart series palette — categorical identity slots, fixed order.
  // Use for legend swatches / inline series dots; charts read the CSS var
  // directly.
  series: {
    1: 'var(--ds-series-1)',
    2: 'var(--ds-series-2)',
    3: 'var(--ds-series-3)',
    4: 'var(--ds-series-4)',
    5: 'var(--ds-series-5)',
    6: 'var(--ds-series-6)',
    7: 'var(--ds-series-7)',
    8: 'var(--ds-series-8)',
    other: 'var(--ds-series-other)',
  },
  brand: {
    // Semantic var, not a primitive: picked per mode so a tinted surface stays
    // ABOVE the card it sits on in both themes. See the note in src/styles/index.css.
    lightest: 'var(--ds-brand-lightest)',
    subtlest: 'var(--primitive-brand-green-200)',
    // The filled chip and its ink. See the note in src/styles/index.css: the pair is
    // chosen for AA in BOTH modes.
    fill: 'var(--primitive-brand-green-400)',
    'on-fill': 'var(--primitive-brand-green-950)',
    subtler: 'var(--primitive-brand-green-500)',
    subtle: 'var(--primitive-brand-green-700)',
    default: 'var(--primitive-brand-green-800)',
    darker: 'var(--primitive-brand-green-900)',
  },
  text: {
    subtlest: 'var(--primitive-gray-100)',
    subtler: 'var(--primitive-gray-300)',
    subtle: 'var(--primitive-gray-500)',
    default: 'var(--primitive-gray-800)',
    darker: 'var(--primitive-gray-900)',
  },
  success: {
    // Points at the SEMANTIC var, not a primitive, because this rung is picked
    // per mode -- see the note on --ds-success-subtlest in src/styles/index.css. Same
    // shape as the `series` slots below.
    subtlest: 'var(--ds-success-subtlest)',
    subtler: 'var(--primitive-green-100)',
    subtle: 'var(--primitive-green-400)',
    default: 'var(--primitive-green-600)',
    darker: 'var(--primitive-green-900)',
  },
  danger: {
    subtlest: 'var(--primitive-red-100)',
    subtler: 'var(--primitive-red-300)',
    subtle: 'var(--primitive-red-500)',
    default: 'var(--primitive-red-600)',
    darker: 'var(--primitive-red-700)',
    darkest: 'var(--primitive-red-900)',
  },
  surface: {
    // Neutral nested-card surface, picked per mode. See src/styles/index.css.
    raised: 'var(--ds-surface-raised)',
    white: 'var(--primitive-white-solid)',
    'off-white': 'var(--primitive-white-off-white)',
    // Fixed dark surface (does NOT auto-flip). For toggled-mode surfaces, pair
    // bg-ds-surface-white with dark:bg-ds-surface-dark.
    dark: 'var(--ds-surface-dark)',
  },
  purple: {
    subtlest: 'var(--ds-purple-subtlest)',
    subtler: 'var(--ds-purple-subtler)',
    subtle: 'var(--ds-purple-subtle)',
    default: 'var(--ds-purple-default)',
    darker: 'var(--ds-purple-darker)',
    darkest: 'var(--ds-purple-darkest)',
  },
  blue: {
    subtlest: 'var(--ds-blue-subtlest)',
    subtler: 'var(--ds-blue-subtler)',
    subtle: 'var(--ds-blue-subtle)',
    default: 'var(--ds-blue-default)',
    darker: 'var(--ds-blue-darker)',
    darkest: 'var(--ds-blue-darkest)',
  },
};

const primitive = {
  'brand-green': {
    100: 'var(--primitive-brand-green-100)',
    200: 'var(--primitive-brand-green-200)',
    300: 'var(--primitive-brand-green-300)',
    400: 'var(--primitive-brand-green-400)',
    500: 'var(--primitive-brand-green-500)',
    600: 'var(--primitive-brand-green-600)',
    700: 'var(--primitive-brand-green-700)',
    800: 'var(--primitive-brand-green-800)',
    900: 'var(--primitive-brand-green-900)',
    950: 'var(--primitive-brand-green-950)',
  },
  white: {
    'off-white': 'var(--primitive-white-off-white)',
    solid: 'var(--primitive-white-solid)',
  },
  gray: {
    50: 'var(--primitive-gray-50)',
    100: 'var(--primitive-gray-100)',
    200: 'var(--primitive-gray-200)',
    300: 'var(--primitive-gray-300)',
    400: 'var(--primitive-gray-400)',
    500: 'var(--primitive-gray-500)',
    600: 'var(--primitive-gray-600)',
    700: 'var(--primitive-gray-700)',
    800: 'var(--primitive-gray-800)',
    900: 'var(--primitive-gray-900)',
    950: 'var(--primitive-gray-950)',
  },
  purple: {
    50: 'var(--primitive-purple-50)',
    100: 'var(--primitive-purple-100)',
    200: 'var(--primitive-purple-200)',
    300: 'var(--primitive-purple-300)',
    400: 'var(--primitive-purple-400)',
    500: 'var(--primitive-purple-500)',
    600: 'var(--primitive-purple-600)',
    700: 'var(--primitive-purple-700)',
    800: 'var(--primitive-purple-800)',
    900: 'var(--primitive-purple-900)',
    950: 'var(--primitive-purple-950)',
  },
  'soft-purple': {
    50: 'var(--primitive-soft-purple-50)',
    100: 'var(--primitive-soft-purple-100)',
    200: 'var(--primitive-soft-purple-200)',
    300: 'var(--primitive-soft-purple-300)',
    400: 'var(--primitive-soft-purple-400)',
    500: 'var(--primitive-soft-purple-500)',
    600: 'var(--primitive-soft-purple-600)',
    700: 'var(--primitive-soft-purple-700)',
    800: 'var(--primitive-soft-purple-800)',
    900: 'var(--primitive-soft-purple-900)',
    950: 'var(--primitive-soft-purple-950)',
  },
  red: {
    100: 'var(--primitive-red-100)',
    200: 'var(--primitive-red-200)',
    300: 'var(--primitive-red-300)',
    400: 'var(--primitive-red-400)',
    500: 'var(--primitive-red-500)',
    600: 'var(--primitive-red-600)',
    700: 'var(--primitive-red-700)',
    800: 'var(--primitive-red-800)',
    900: 'var(--primitive-red-900)',
    950: 'var(--primitive-red-950)',
  },
  orange: {
    100: 'var(--primitive-orange-100)',
    200: 'var(--primitive-orange-200)',
    300: 'var(--primitive-orange-300)',
    400: 'var(--primitive-orange-400)',
    500: 'var(--primitive-orange-500)',
    600: 'var(--primitive-orange-600)',
    700: 'var(--primitive-orange-700)',
    800: 'var(--primitive-orange-800)',
    900: 'var(--primitive-orange-900)',
    950: 'var(--primitive-orange-950)',
  },
  green: {
    50: 'var(--primitive-green-50)',
    100: 'var(--primitive-green-100)',
    200: 'var(--primitive-green-200)',
    300: 'var(--primitive-green-300)',
    400: 'var(--primitive-green-400)',
    500: 'var(--primitive-green-500)',
    600: 'var(--primitive-green-600)',
    700: 'var(--primitive-green-700)',
    800: 'var(--primitive-green-800)',
    900: 'var(--primitive-green-900)',
    950: 'var(--primitive-green-950)',
  },
  violet: {
    57: 'var(--primitive-violet-57)',
    67: 'var(--primitive-violet-67)',
    70: 'var(--primitive-violet-70)',
  },
  azure: {
    46: 'var(--primitive-azure-46)',
    50: 'var(--primitive-azure-50)',
    53: 'var(--primitive-azure-53)',
    59: 'var(--primitive-azure-59)',
    78: 'var(--primitive-azure-78)',
    87: 'var(--primitive-azure-87)',
    89: 'var(--primitive-azure-89)',
  },
  blue: {
    8: 'var(--primitive-blue-8)',
    12: 'var(--primitive-blue-12)',
    85: 'var(--primitive-blue-85)',
    100: 'var(--primitive-blue-100)',
    200: 'var(--primitive-blue-200)',
    300: 'var(--primitive-blue-300)',
    400: 'var(--primitive-blue-400)',
    500: 'var(--primitive-blue-500)',
    600: 'var(--primitive-blue-600)',
    700: 'var(--primitive-blue-700)',
    800: 'var(--primitive-blue-800)',
    900: 'var(--primitive-blue-900)',
    950: 'var(--primitive-blue-950)',
  },
  magenta: {
    37: 'var(--primitive-magenta-37)',
  },
  cyan: {
    29: 'var(--primitive-cyan-29)',
    36: 'var(--primitive-cyan-36)',
    43: 'var(--primitive-cyan-43)',
    68: 'var(--primitive-cyan-68)',
    82: 'var(--primitive-cyan-82)',
  },
  teal: {
    29: 'var(--primitive-teal-29)',
    36: 'var(--primitive-teal-36)',
    43: 'var(--primitive-teal-43)',
    68: 'var(--primitive-teal-68)',
    82: 'var(--primitive-teal-82)',
  },
};

/** Design system typography: one utility per token (e.g. ds-text-body-xs-medium). */
const dsTypographyPlugin = function ({
  addUtilities,
}: {
  addUtilities: (utilities: Record<string, Record<string, string>>) => void;
}) {
  const bodyFont = 'var(--ds-font-body)';
  const codeFont = 'var(--ds-font-code)';
  addUtilities({
    '.ds-text-body-xs-medium': {
      fontSize: 'var(--ds-text-body-xs-medium)',
      fontFamily: bodyFont,
      fontWeight: '500',
      lineHeight: 'var(--ds-text-body-xs-medium-lh)',
    },
    '.ds-text-body-xs-semibold': {
      fontSize: 'var(--ds-text-body-xs-semibold)',
      fontFamily: bodyFont,
      fontWeight: '600',
      lineHeight: 'var(--ds-text-body-xs-semibold-lh)',
    },
    '.ds-text-body-sm-medium': {
      fontSize: 'var(--ds-text-body-sm-medium)',
      fontFamily: bodyFont,
      fontWeight: '500',
      lineHeight: 'var(--ds-text-body-sm-medium-lh)',
    },
    '.ds-text-body-sm-semibold': {
      fontSize: 'var(--ds-text-body-sm-semibold)',
      fontFamily: bodyFont,
      fontWeight: '600',
      lineHeight: 'var(--ds-text-body-sm-semibold-lh)',
    },
    '.ds-text-body-md-medium': {
      fontSize: 'var(--ds-text-body-md-medium)',
      fontFamily: bodyFont,
      fontWeight: '500',
      lineHeight: 'var(--ds-text-body-md-medium-lh)',
    },
    '.ds-text-body-md-semibold': {
      fontSize: 'var(--ds-text-body-md-semibold)',
      fontFamily: bodyFont,
      fontWeight: '600',
      lineHeight: 'var(--ds-text-body-md-semibold-lh)',
    },
    '.ds-text-title-sm-medium': {
      fontSize: 'var(--ds-text-title-sm-medium)',
      fontFamily: bodyFont,
      fontWeight: '500',
      lineHeight: 'var(--ds-text-title-sm-medium-lh)',
    },
    '.ds-text-title-sm-semibold': {
      fontSize: 'var(--ds-text-title-sm-semibold)',
      fontFamily: bodyFont,
      fontWeight: '600',
      lineHeight: 'var(--ds-text-title-sm-semibold-lh)',
    },
    '.ds-text-title-md-medium': {
      fontSize: 'var(--ds-text-title-md-medium)',
      fontFamily: bodyFont,
      fontWeight: '500',
      lineHeight: 'var(--ds-text-title-md-medium-lh)',
    },
    '.ds-text-title-md-semibold': {
      fontSize: 'var(--ds-text-title-md-semibold)',
      fontFamily: bodyFont,
      fontWeight: '600',
      lineHeight: 'var(--ds-text-title-md-semibold-lh)',
    },
    '.ds-text-title-lg-medium': {
      fontSize: 'var(--ds-text-title-lg-medium)',
      fontFamily: bodyFont,
      fontWeight: '500',
      lineHeight: 'var(--ds-text-title-lg-medium-lh)',
    },
    '.ds-text-title-lg-semibold': {
      fontSize: 'var(--ds-text-title-lg-semibold)',
      fontFamily: bodyFont,
      fontWeight: '600',
      lineHeight: 'var(--ds-text-title-lg-semibold-lh)',
    },
    '.ds-text-title-xl-medium': {
      fontSize: 'var(--ds-text-title-xl-medium)',
      fontFamily: bodyFont,
      fontWeight: '500',
      lineHeight: 'var(--ds-text-title-xl-medium-lh)',
    },
    '.ds-text-title-xl-semibold': {
      fontSize: 'var(--ds-text-title-xl-semibold)',
      fontFamily: bodyFont,
      fontWeight: '600',
      lineHeight: 'var(--ds-text-title-xl-semibold-lh)',
    },
    '.ds-text-title-2xl-medium': {
      fontSize: 'var(--ds-text-title-2xl-medium)',
      fontFamily: bodyFont,
      fontWeight: '500',
      lineHeight: 'var(--ds-text-title-2xl-medium-lh)',
    },
    '.ds-text-title-2xl-semibold': {
      fontSize: 'var(--ds-text-title-2xl-semibold)',
      fontFamily: bodyFont,
      fontWeight: '600',
      lineHeight: 'var(--ds-text-title-2xl-semibold-lh)',
    },
    '.ds-text-code-sm': {
      fontSize: 'var(--ds-text-code-sm)',
      fontFamily: codeFont,
      fontWeight: '500',
      lineHeight: 'var(--ds-text-code-sm-lh)',
    },
    '.ds-text-code-md': {
      fontSize: 'var(--ds-text-code-md)',
      fontFamily: codeFont,
      fontWeight: '500',
      lineHeight: 'var(--ds-text-code-md-lh)',
    },
    '.ds-text-code-md-regular': {
      fontSize: 'var(--ds-text-code-md-regular)',
      fontFamily: codeFont,
      fontWeight: '400',
      lineHeight: 'var(--ds-text-code-md-regular-lh)',
    },
    '.ds-text-code-lg': {
      fontSize: 'var(--ds-text-code-lg)',
      fontFamily: codeFont,
      fontWeight: '700',
      lineHeight: 'var(--ds-text-code-lg-lh)',
    },
  });
};

const config: Config = {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        code: ['var(--font-jetbrains-mono)', 'ui-monospace', 'monospace'],
      },
      transitionTimingFunction: {
        'motion-out': 'var(--motion-ease-out)',
      },
      colors: {
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        ds,
        primitive,
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
    },
  },
  plugins: [
    tailwindcssAnimate,
    dsTypographyPlugin,
    // Base UI (@base-ui/react) exposes state as boolean-presence data-attributes
    // (data-open, data-checked, data-starting-style, ...). Register them as
    // shorthand Tailwind variants so `data-open:`, `group-data-active:`, etc.
    // resolve. Parameterized attrs (data-[side=top], data-[state=...]) use the
    // native bracket form and need no registration.
    tailwindPlugin(({ addVariant }: { addVariant: (name: string, selector: string) => void }) => {
      const states = [
        'open', 'closed', 'checked', 'unchecked', 'indeterminate',
        'disabled', 'readonly', 'required', 'valid', 'invalid', 'dirty',
        'touched', 'filled', 'focused', 'active', 'inactive', 'selected',
        'unselected', 'highlighted', 'pressed', 'current', 'popup-open',
        'starting-style', 'ending-style', 'scrolling', 'nested',
      ];
      for (const s of states) {
        addVariant(`data-${s}`, `&[data-${s}]`);
        addVariant(`group-data-${s}`, `:merge(.group)[data-${s}] &`);
        addVariant(`peer-data-${s}`, `:merge(.peer)[data-${s}] ~ &`);
      }
    }),
  ],
};
export default config;
