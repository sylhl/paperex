/** @type {import('tailwindcss').Config} */

// 颜色全部走 CSS 变量，<html data-theme="..."> 切换主题时无需改任何组件类名。
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          950: token('ink-950'),
          900: token('ink-900'),
          850: token('ink-850'),
          800: token('ink-800'),
          750: token('ink-750'),
          700: token('ink-700')
        },
        mint: {
          300: token('mint-300'),
          400: token('mint-400'),
          500: token('mint-500')
        },
        violet: {
          400: token('violet-400'),
          500: token('violet-500')
        },
        zinc: {
          200: token('zinc-200'),
          300: token('zinc-300'),
          400: token('zinc-400'),
          500: token('zinc-500'),
          600: token('zinc-600'),
          700: token('zinc-700')
        }
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Cascadia Code', 'Consolas', 'SFMono-Regular', 'monospace'],
        sans: ['Inter', 'HarmonyOS Sans SC', 'PingFang SC', 'Microsoft YaHei', 'sans-serif']
      },
      boxShadow: {
        pane: '0 24px 60px -30px rgba(0,0,0,0.9)',
        glow: '0 0 0 1px rgb(var(--mint-400) / 0.25), 0 8px 40px -12px rgb(var(--mint-400) / 0.35)'
      },
      keyframes: {
        'overlay-in': {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' }
        },
        scan: {
          '0%': { transform: 'translateY(-100%)' },
          '100%': { transform: 'translateY(1000%)' }
        },
        float: {
          '0%,100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-8px)' }
        },
        breathe: {
          '0%,100%': { opacity: '0.35', transform: 'scale(0.96)' },
          '50%': { opacity: '1', transform: 'scale(1.04)' }
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' }
        }
      },
      animation: {
        'overlay-in': 'overlay-in .25s ease-out both',
        scan: 'scan 3.2s linear infinite',
        float: 'float 4s ease-in-out infinite',
        breathe: 'breathe 2.4s ease-in-out infinite',
        shimmer: 'shimmer 2.4s linear infinite'
      }
    }
  },
  plugins: []
}
