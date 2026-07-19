/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // 暖灰书房基底：减少纯黑与蓝紫倾向，保持长时间写作舒适度。
        ink: {
          950: '#10110f',
          900: '#151713',
          850: '#1a1c18',
          800: '#20231e',
          700: '#2a2e28',
          600: '#373d35',
        },
        // 暖金只承担品牌和少量重点，主交互使用翡翠色。
        gold: {
          400: '#dfbd78',
          500: '#cda65b',
          600: '#a98242',
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
        sans: [
          '"Microsoft YaHei UI"',
          '"Microsoft YaHei"',
          '"PingFang SC"',
          '"Source Han Sans SC"',
          'system-ui',
          'sans-serif',
        ],
        serif: ['"STSong"', '"SimSun"', '"Songti SC"', 'Georgia', 'serif'],
      },
      maxWidth: {
        manuscript: '680px',
      },
    },
  },
  plugins: [],
}
