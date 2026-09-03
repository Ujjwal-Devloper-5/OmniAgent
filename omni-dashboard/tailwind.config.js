export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        surface: '#0F1117',
        overlay: '#161B27',
        subtle:  '#1E2336',
        border:  '#1E2336',
        'border-strong': '#2A3048',
        primary: '#E8EAF0',
        secondary: '#8892A4',
        muted: '#4A5568',
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
    },
  },
  plugins: [],
};
