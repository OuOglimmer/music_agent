import type { NextConfig } from "next";

const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // 对请求跳过尾斜杠 308 重定向：后端部分路由以尾斜杠定义（如 /items/），
  // 补斜杠交给后端中间件处理，避免代理先 308 把路径改写错
  skipTrailingSlashRedirect: true,
  async rewrites() {
    // 把 /api/v1 代理到 FastAPI 后端，前端统一走同源请求（与 Vite 时代的 proxy 一致）
    // 注意带尾斜杠与不带尾斜杠两条分支：:path* 默认会吞掉尾斜杠，
    // 而后端部分路由以尾斜杠定义（如 /items/），必须显式保留
    const api = `${backendUrl}/api/v1`;
    return [
      {
        source: "/api/v1/:path*/",
        destination: `${api}/:path*/`,
      },
      {
        source: "/api/v1/:path*",
        destination: `${api}/:path*`,
      },
    ];
  },
};

export default nextConfig;
