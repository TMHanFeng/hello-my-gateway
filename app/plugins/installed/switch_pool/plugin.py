# -*- coding: utf-8 -*-
"""Switch 切换池插件（池级 local/net 定向路由，v2.13.3 功能的插件化封装）。

功能（详见 docs/Switch切换路由说明.md）：
  对开启「🔀 Switch 切换」的池，调用方不走 /v1，直接 POST /{池名} + switch=local|net，
  网关把请求定向路由到池内本地侧（令牌类型 local）或云端侧模型。
  语义红线：绝不静默跨侧——本侧全部不可用直接 503，不升级兜底池、不受单模型锁定影响。

热插拔：on_enable 向 app 注册 POST /{pool_name} 路由，on_disable 即时注销。
插件停用期间这些请求得到 404（路由不存在）；重新启用立即恢复，全程无需重启。
每个池自己的 Switch 开关仍由「模型池」页控制（pool 配置 switch_enabled），双重开关。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.plugins.base import GatewayPlugin

logger = logging.getLogger(__name__)

ROUTE_TAG_ATTR = "plugin_route_switch_pool"


async def switch_chat(pool_name: str, request: Request):
    """POST /{池名}：Switch 定向调用入口（经插件中心启停，动态注册/注销）。

    注意：app.main 的符号一律函数级导入——本模块可能在 app.main 尚未装配完成时
    被插件中心扫描导入，模块级导入会造成循环导入。"""
    from app.core import keyauth
    from app.gateway.pool import load_config
    from app.main import _chat_handler, pool, verify_key

    if pool_name not in pool.pools:
        raise HTTPException(status_code=404,
                            detail=f"未知模型池 '{pool_name}'：对外仅可调用模型池，不能直接指定单个模型")
    auth = await verify_key(request)  # 与 /v1 完全一致的鉴权（服务器密钥/管理员 Key/用户 Key）
    if auth["kind"] == "key_user" and not keyauth.is_pool_allowed(auth.get("key"), pool_name):
        raise HTTPException(status_code=403, detail=f"该 API Key 无权访问模型池 '{pool_name}'")
    if not pool.pools[pool_name].get("switch_enabled"):
        raise HTTPException(status_code=403,
                            detail=f"模型池 '{pool_name}' 未开启 Switch 切换，无法按 local/net 定向调用")
    switch = (request.query_params.get("switch") or "").strip().lower()
    body = None
    if not switch:
        # query 未带 switch 时读 body 兜底：?switch=local 与 {"switch":"local"} 两种传参都兼容
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        switch = str(body.get("switch") or "").strip().lower() if isinstance(body, dict) else ""
    if switch not in ("local", "net"):
        raise HTTPException(status_code=422,
                            detail=f"非法 switch 参数 '{switch or '(缺失)'}'：仅支持 local（本地模型）或 net（云端模型）")
    if isinstance(body, dict):
        body.pop("switch", None)  # 网关自有路由参数，不透传上游
    return await _chat_handler(request, auth, forced_pool=pool_name, switch_role=switch, body=body)


class SwitchPoolPlugin(GatewayPlugin):
    """managed 域插件：启停状态由插件中心持久化（config.json plugins.switch_pool）。"""

    @property
    def router(self):
        # 本插件无独立设置 API（每池开关在模型池页），返回 None 由管理器跳过挂载
        return None

    def on_enable(self, app) -> None:
        self.on_disable(app)  # 幂等：先摘旧路由再挂新的，避免重复注册
        app.add_api_route("/{pool_name}", switch_chat, methods=["POST"],
                          summary="Switch 切换池定向调用（插件）", include_in_schema=False)
        # add_api_route 在部分 FastAPI 版本不返回路由对象：注册后按路径+方法找回并打热拔标记
        for r in app.router.routes:
            if (getattr(r, "path", "") == "/{pool_name}"
                    and "POST" in (getattr(r, "methods", None) or set())
                    and not getattr(r, ROUTE_TAG_ATTR, False)):
                try:
                    setattr(r, ROUTE_TAG_ATTR, True)
                except AttributeError:
                    pass  # 无 __dict__ 的包装对象打不了标，跳过（卸载时按 path 兜底）
        logger.info("[switch_pool] 已注册 POST /{池名} Switch 定向路由")

    def on_disable(self, app) -> None:
        before = len(app.router.routes)
        app.router.routes[:] = [r for r in app.router.routes
                                if not (getattr(r, ROUTE_TAG_ATTR, False)
                                        or (getattr(r, "path", "") == "/{pool_name}"
                                            and "POST" in (getattr(r, "methods", None) or set())))]
        if len(app.router.routes) != before:
            logger.info("[switch_pool] 已注销 POST /{池名} Switch 定向路由（热拔）")


def create_plugin(manifest):
    return SwitchPoolPlugin(manifest)
