/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: "#0ea5e9",
          dark:    "#0284c7",
        },
        surface: {
          DEFAULT: "#111827",
          card:    "#1f2937",
          border:  "#374151",
        },
        mas: {
          0: "#22c55e",
          1: "#86efac",
          2: "#fbbf24",
          3: "#f97316",
          4: "#ef4444",
        },
      },
      fontFamily: {
        mono: ["JetBrains Mono", "Fira Code", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
