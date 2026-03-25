/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // images: {
  //   domains: ['localhost']
  // }
  transpilePackages: ['react-tradingview-embed'],
};

module.exports = nextConfig;
