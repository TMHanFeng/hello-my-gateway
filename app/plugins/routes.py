# -*- coding: utf-8 -*-
"""插件中心 API（/admin/plugins/*）：列表 / 扫描 / 启停 / 配置，全部热生效。

自定义插件接入：把含 manifest.json（可选 plugin.py）的文件夹放入
app/plugins/installed/<目录名>/，POST /admin/plugins/scan 即被发现——
无需改网关任何代码、无需重启。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.admin.deps import verify_admin
from app.plugins.manager import plugin_center

router = APIRouter(prefix="/admin/plugins", tags=["plugins"])


def _app():
    from app.main import app
    return app


@router.get("")
@router.get("/")
async def list_plugins(_=Depends(verify_admin)):
    """插件列表：manifest 元数据 + 启用态 + 当前配置 + config_schema（前端渲染设置表单）。"""
    return {"plugins": plugin_center.list_plugins(), "plugins_dir": plugin_center.plugins_dir}


@router.post("/scan")
async def scan_plugins(_=Depends(verify_admin)):
    """重扫插件目录：新拖入的插件立即被发现并挂载（启用态按其持久化状态恢复）。"""
    events = plugin_center.scan(_app())
    return {"events": events, "plugins": plugin_center.list_plugins()}


@router.get("/{pid}")
async def plugin_detail(pid: str, _=Depends(verify_admin)):
    p = plugin_center.get(pid)
    if p is None:
        raise HTTPException(status_code=404, detail=f"插件 '{pid}' 不存在")
    detail = next(x for x in plugin_center.list_plugins() if x["id"] == pid)
    return detail


@router.post("/{pid}/enable")
async def enable_plugin(pid: str, _=Depends(verify_admin)):
    try:
        plugin_center.enable(pid, _app())
    except KeyError:
        raise HTTPException(status_code=404, detail=f"插件 '{pid}' 不存在")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启用失败: {e}")
    return {"ok": True, "id": pid, "enabled": True}


@router.post("/{pid}/disable")
async def disable_plugin(pid: str, _=Depends(verify_admin)):
    try:
        plugin_center.disable(pid, _app())
    except KeyError:
        raise HTTPException(status_code=404, detail=f"插件 '{pid}' 不存在")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"停用失败: {e}")
    return {"ok": True, "id": pid, "enabled": False}


@router.post("/{pid}/config")
@router.put("/{pid}/config")
async def set_plugin_config(pid: str, request: Request, _=Depends(verify_admin)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if pid not in {x["id"] for x in plugin_center.list_plugins()}:
        raise HTTPException(status_code=404, detail=f"插件 '{pid}' 不存在")
    try:
        cfg = plugin_center.set_config(pid, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "id": pid, "config": cfg}
