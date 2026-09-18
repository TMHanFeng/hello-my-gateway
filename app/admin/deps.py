# -*- coding: utf-8 -*-
"""管理面共享鉴权依赖：admin 路由与插件中心路由共用同一套 Bearer 校验。"""
from fastapi import HTTPException, Request

from app.core.config import load_config


def verify_admin(request: Request):
    config = load_config()
    expected = config.get("server", {}).get("api_key", "")
    if not expected:
        return
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        auth = auth[7:]
    if auth != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
