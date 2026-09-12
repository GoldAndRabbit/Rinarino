#!/usr/bin/env bash
# 上线：构建静态站（scripts/build_site.py）+ 上传到 Cloudflare Pages。
#
# 整站是静态的——后端那三个接口读的都是不会变的产物，构建时算好落成文件就行，
# 线上没有常驻进程，也就没有服务器要守、没有隧道会掉（和 pitchasso 不一样，
# 它有动态内容，所以才要 cloudflared 把后端捅出去）。
#
# 需要 wrangler 已登录（wrangler login），或者环境里有 CLOUDFLARE_API_TOKEN。
set -e
cd "$(cd "$(dirname "$0")" && pwd)"

# wrangler 只认环境变量，而 token 存在 .env 里（.env 不进仓库）。
# 真实环境变量优先，和 util/ 那边解析 key 的口径一致。
if [[ -z "${CLOUDFLARE_API_TOKEN:-}" && -f .env ]]; then
  export CLOUDFLARE_API_TOKEN="$(grep -E '^CLOUDFLARE_API_TOKEN=' .env | head -1 | cut -d= -f2-)"
fi

uv run python scripts/build_site.py --clean
wrangler pages deploy site --project-name rinarino --branch main --commit-dirty=true

echo ""
echo "✓ 已部署 → https://rinarino.com  (素材是 WebP，hash 变了用户下次加载即新版)"
