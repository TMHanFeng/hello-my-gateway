# -*- coding: utf-8 -*-
"""config.json 读写（带 mtime 缓存）：原 pool.py 的 load_config/save_config 原样迁出。

迁出原因：配置读写是横切能力（admin/headroom/scheduler/keyauth 都要用），
不应寄生在"模型池"这个业务模块里；抽出后 pool.py 反向从这里 import 再 re-export，
旧代码 `from app.gateway.pool import load_config` 也仍然成立。
"""
import json
import logging
import threading
from pathlib import Path

from app.core.paths import CONFIG_PATH, CONFIG_BAK_PATH

logger = logging.getLogger(__name__)

_cache_lock = threading.Lock()
_cache: dict = {"mtime": None, "config": {}}


def load_config() -> dict:
    """读 config.json（全项目唯一入口）。mtime 未变时直接命中缓存，热路径零 IO；
    外部改动（含手工编辑）会因 mtime 变化自动失效。"""
    p = Path(CONFIG_PATH)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return _cache.get("config") or {}
    if _cache["mtime"] == mtime and _cache["config"]:
        return _cache["config"]
    with _cache_lock:
        if _cache["mtime"] == mtime and _cache["config"]:
            return _cache["config"]
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"[config] config.json 解析失败，沿用上次缓存: {e}")
            return _cache.get("config") or {}
        _cache["config"] = cfg
        _cache["mtime"] = mtime
        return cfg


def save_config(config: dict):
    """原子写回 config.json 并立即刷新缓存（调用方自行保证已改在深拷贝上）。"""
    tmp = Path(str(CONFIG_PATH) + ".tmp")
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)
    with _cache_lock:
        _cache["config"] = config
        try:
            _cache["mtime"] = Path(CONFIG_PATH).stat().st_mtime
        except OSError:
            _cache["mtime"] = None
