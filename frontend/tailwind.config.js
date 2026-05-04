/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        poly: {
          dark: '#0e1014',
          card: '#161920',
          up: '#00c853',
          down: '#ff3d00',
          border: '#2a2e39',
          text: '#ffffff',
          textMuted: '#8b949e',
        }
      }
    },
  },
  plugins: [],
}
