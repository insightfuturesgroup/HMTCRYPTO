import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#182230",
        panel: "#ffffff",
        canvas: "#f6f7f9",
        accent: "#2563eb",
      },
      boxShadow: {
        card: "0 2px 8px rgb(24 34 48 / 0.05)",
      },
    },
  },
  plugins: [],
} satisfies Config;
