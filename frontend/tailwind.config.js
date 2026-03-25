module.exports = {
  purge: [
    "./pages/**/*.{js,ts,jsx,tsx}",
    "./Components/**/*.{js,ts,jsx,tsx}",
    "./Components/**/**/*.{js,ts,jsx,tsx}",
    "./Layout/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: false, // or 'media' or 'class'
  theme: {
    extend: {
      screens: {
        'xs': '360px',
        'sm': '640px',
        'md': '768px',
        'lg': '1024px',
        'xl': '1280px',
        '2xl': '1536px',
      },
    },
  },
  variants: {
    extend: {},
  },
  plugins: [],
  safelist: [
    "bg-teal-700",
    {
      pattern: /bg-(red|green|blue)-(400|500|600)/,
    },
    "text-green-600",
    "text-red-300",
    "text-gray-600",
    "text-purple-600",
    "text-purple-800",
    "text-purple-400",
    "text-purple-500",
    "text-purple-600",
    "text-purple-700",
    "flex-col",
    "flex-row",
    "hidden",
    "block",
    "w-full",
    "w-auto",
    "overflow-hidden",
    "overflow-auto",
  ],
};
