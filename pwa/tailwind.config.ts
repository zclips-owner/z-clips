import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}", "./components/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-outfit)", "ui-sans-serif", "system-ui", "sans-serif"]
      },
      colors: {
        ink: "#0b0910",
        surface: "#15111d",
        surface2: "#1d1728",
        line: "#30243f",
        brand: "#a855f7",
        branddark: "#7e22ce",
        mutedtext: "#a99db8"
      }
    }
  },
  plugins: []
};

export default config;
