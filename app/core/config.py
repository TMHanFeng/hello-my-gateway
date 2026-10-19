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

from app.core.paths import CONFIG_PATH, CONFIG_BAK_PATH, UI_GROUPS_LAST_GOOD

logger = logging.getLogger(__name__)

_cache_lock = threading.Lock()
_cache: dict = {"mtime": None, "config": {}}

# 分组收纳（ui_groups）相关的 kind：只有这两个维度存成员表，settings 存开关
UG_KINDS = ("poolGroups", "providerGroups")


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


def _ug_has_groups(ug) -> bool:
    """ui_groups 节点里是否还有分组（任一维度非空即算有）。"""
    if not isinstance(ug, dict):
        return False
    for k in UG_KINDS:
        v = ug.get(k)
        if isinstance(v, list) and v:
            return True
    return False


def write_ui_groups_last_good(ug: dict) -> None:
    """把当前分组数据另存一份「最后一次已知良好」快照。

    分组数据只有 config.json 一个存储点，误写一次就是永久丢失（2026-10-17 生产事故）。
    这份快照让「写坏之后回滚」变成一条命令，也让 config.json 被外部脚本整体改写
    （绕开 API 的路径）时仍有一条自动自愈的路可走。写失败不阻断主流程。
    """
    try:
        UI_GROUPS_LAST_GOOD.parent.mkdir(parents=True, exist_ok=True)
        UI_GROUPS_LAST_GOOD.write_text(json.dumps(ug, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[config] 分组数据已知良好快照写入失败: {e}")


def read_ui_groups_last_good() -> dict:
    """读「最后一次已知良好」快照；没有/坏了返回空 dict。"""
    try:
        p = Path(UI_GROUPS_LAST_GOOD)
        if not p.exists():
            return {}
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception as e:
        logger.warning(f"[config] 分组数据已知良好快照读取失败: {e}")
        return {}


def restore_ui_groups_if_empty() -> bool:
    """启动自愈：config.json 里的分组数据被清空时，用已知良好快照回填。

    只在「当前一个分组都不剩」且「快照里还有分组」时动手——这是事故的特征签名
    （2026-10-17：16 个成员被整片抹掉，config.json 里 ui_groups 变成空）。
    用户主动「清空所有分组」是带 force 保存的，那次保存会把快照同步成空，
    因此不会在这里被"复活"。返回是否发生了回填。
    """
    cfg = load_config()
    cur = cfg.get("ui_groups")
    if _ug_has_groups(cur):
        return False
    snap = read_ui_groups_last_good()
    if not _ug_has_groups(snap):
        return False
    cfg["ui_groups"] = snap
    save_config(cfg)
    logger.warning(
        "[config] 检测到 config.json 的分组数据已被清空，已用已知良好快照自动回填"
        f"（{UI_GROUPS_LAST_GOOD}）——若这是有意清空请删除该快照文件"
    )
    return True
