# -*- coding: utf-8 -*-
"""hmg-admin 第三面板路由：多页面真路由 + 共享静态资产（纯只读，零后端行为改动）。

与 /admin（暗黑单页）、/hfadmin（白天单页）并存的第三管理面板，设计约束见
docs/美化工作单-hmg-admin.md：

- 六页白名单路由：GET /hmg-admin/<page>（model/pools/stats/logs/keys/plugins），
  白名单外 404；GET /hmg-admin → 307 → /hmg-admin/model（无单页 tab，每菜单独立 URL）。
- 共享资产：GET /hmg-admin/assets/<file> 只放行 css/js/svg 扩展名白名单（天然防目录遍历）。
- 页面本身无需认证（与 /admin、/hfadmin 一致），业务 API 全部复用 /admin/* 现有端点
  （verify_admin Bearer 保护），本文件不做任何业务与鉴权逻辑。
- mtime 缓存 + ETag/304 + __GATEWAY_VERSION__ 替换：与 main.py 的 hfadmin_page 同一套
  机制——手工改 HTML/CSS/JS 后自动失效缓存刷新即见，无需重启。
"""
import hashlib

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse

from app.admin.routes import get_gateway_version
from app.core.paths import HMG_STATIC_DIR

router = APIRouter(prefix="/hmg-admin", include_in_schema=False)

# 六页白名单：/hmg-admin/<page> → static/hmg-admin/<page>.html
HMG_PAGES = ("model", "pools", "stats", "logs", "keys", "plugins")

# 资产扩展名白名单 → Content-Type（其余一律 404）
_HMG_ASSET_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}

# mtime 缓存：{文件绝对路径: {"mtime","text","etag"}}——文件未变零 IO，改动自动失效
_hmg_cache: dict = {}


def _serve_file(path, media_type: str, request: Request):
    """读文件 + __GATEWAY_VERSION__ 替换 + ETag 协商（hfadmin_page 同款，抽出来供页/资产共用）。"""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return Response(status_code=404)
    ent = _hmg_cache.get(str(path))
    if ent is None or ent["mtime"] != mtime:
        text = path.read_text(encoding="utf-8").replace("__GATEWAY_VERSION__", get_gateway_version())
        ent = {"mtime": mtime, "text": text,
               "etag": '"' + hashlib.md5(text.encode("utf-8")).hexdigest()[:20] + '"'}
        _hmg_cache[str(path)] = ent
    etag = ent["etag"]
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    return Response(content=ent["text"], media_type=media_type,
                    headers={"Cache-Control": "no-cache", "ETag": etag})


@router.get("")
async def hmg_root():
    """根路径落地：307 到模型管理页（多页面真路由的默认入口）。"""
    return RedirectResponse("/hmg-admin/model")


@router.get("/assets/{fname}")
async def hmg_asset(fname: str, request: Request):
    """共享静态资产（hmg.css/hmg.js/内联 svg）。扩展名白名单 + 拒绝路径分隔符。"""
    if "/" in fname or "\\" in fname or ".." in fname or "." not in fname:
        return Response(status_code=404)
    media = _HMG_ASSET_TYPES.get("." + fname.rsplit(".", 1)[-1].lower())
    if not media:
        return Response(status_code=404)
    return _serve_file(HMG_STATIC_DIR / "assets" / fname, media, request)


@router.get("/{page}")
async def hmg_page(page: str, request: Request):
    """面板页：白名单外 404（FastAPI 单段路由不会匹配到 assets/ 前缀，两路由互不遮蔽）。"""
    if page not in HMG_PAGES:
        return Response(status_code=404)
    return _serve_file(HMG_STATIC_DIR / f"{page}.html", "text/html; charset=utf-8", request)
