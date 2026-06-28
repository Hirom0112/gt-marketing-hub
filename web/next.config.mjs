/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The Marketing Hub talks to the existing FastAPI backend over fetch.
  // Proxy /api/* to it in dev so the browser never hits CORS.
  async rewrites() {
    const base = process.env.GT_API_BASE_URL || 'http://localhost:8000';
    return [{ source: '/api/:path*', destination: `${base}/:path*` }];
  },
};
export default nextConfig;
