/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // 深色书房基底（v5.0 低饱和度）
        ink: {
          950: '#0d0e14',
          900: '#12131a',
          850: '#161822',
          800: '#1a1d29',
          700: '#222634',
          600: '#2d3242',
        },
        // 暖金 / 琥珀 品牌色
        gold: {
          400: '#d4af37',
          500: '#c9a227',
          600: '#a8841c',
        },
        // 状态色
        st: {
          planned: '#6b7280', // 灰
          draft: '#6b7280', // 灰
          in_progress: '#3b82f6', // 蓝
          finalized: '#22c55e', // 绿
          warning: '#f59e0b', // 琥珀色（警示）
        },
      },
      fontFamily: {
        sans: ['"Noto Sans SC"', 'system-ui', 'sans-serif'],
        serif: ['"Noto Serif SC"', 'Georgia', 'serif'],
      },
      maxWidth: {
        manuscript: '680px',
      },
    },
  },
  plugins: [],
}
