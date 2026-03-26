/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0f172a",
        mist: "#e2e8f0",
        blush: "#f8fafc",
        accent: "#f97316",
      },
    },
  },
  plugins: [],
};
