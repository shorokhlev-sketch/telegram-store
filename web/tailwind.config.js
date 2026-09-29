/*
 * Styling lives in src/index.css (tokens + component classes ported from the
 * approved mockup, mockups/apple-language/index.html). Tailwind provides the
 * reset and utilities; its colours point at the same CSS variables.
 */
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        label: 'var(--label)',
        'label-2': 'var(--label-2)',
        'label-3': 'var(--label-3)',
        'label-4': 'var(--label-4)',
        bg: 'var(--bg)',
        fill: 'var(--fill)',
        accent: 'var(--accent)',
        danger: 'var(--danger)',
      },
      fontFamily: {
        sans: ['-apple-system', '"SF Pro Text"', 'Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
