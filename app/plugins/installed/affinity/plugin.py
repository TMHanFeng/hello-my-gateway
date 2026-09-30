# -*- coding: utf-8 -*-
"""缓存亲和路由插件（v2.16.2）：同一调用 Key + 同一候选集 → 确定性同一模型条目。

问题：中转池的轮询（load_balance）/ 到期排序（auto_order）会把同一调用方的连续请求
分散到池内不同条目，上游提示词缓存（跟随 上游账号×模型×前缀）每次冷启动。

实现为无状态一致性哈希（不建记忆表）：
  目标 = sorted(候选条目id) 构成的哈希环上，第一个位置 ≥ hash(key_id) 的虚拟节点所属条目
- 无状态可重建：网关重启/热加载后同一 Key 仍落同一条目（记忆表方案会丢，重启后第一波
  请求全部 miss）；
- 候选集增删只影响环上相邻 Key 的落点，不会全体重新洗牌；多 Key 天然均匀分布；
- 粘性粒度是「模型条目」而非模型名：同名模型挂多个供应商时只有条目级粘性能命中缓存；
- 本插件只给「优先」建议：选模层（pool._select_from_pool）仍走完整可用性检查，目标
  不可用自动落回池内正常次序，恢复后自动回粘。Switch 定向请求、单模型锁定不参与。
"""
from __future__ import annotations

import bisect
import hashlib
import logging
import re

from app.plugins.base import GatewayPlugin

logger = logging.getLogger(__name__)

_RING_CACHE_MAX = 32  # 环缓存条数上限（候选集×虚拟节点数的组合键，超出整体清空重建）


def _h(s: str) -> int:
    """稳定哈希（sha1 前 8 字节大端）：同输入跨进程/跨重启结果一致，是亲和无状态的前提"""
    return int.from_bytes(hashlib.sha1(s.encode("utf-8")).digest()[:8], "big")


def build_ring(cands: list[str], virtual_nodes: int = 40) -> tuple[list[int], list[str]]:
    """构建一致性哈希环：返回 (排序位置数组, 对应条目数组)（纯函数，测试直接复用）。"""
    ring_cands = sorted(set(cands))
    vn = max(1, int(virtual_nodes))
    pairs = sorted((_h(f"{cand}#vn{v}"), cand) for cand in ring_cands for v in range(vn))
    return [p for p, _c in pairs], [c for _p, c in pairs]


def pick_target(cands: list[str], key_id: str, virtual_nodes: int = 40) -> str | None:
    """一致性哈希选条目（纯函数，测试直接复用断言期望落点）。

    cands 为本次请求的候选条目 id 集合（选模层已按模态/json/视觉预筛）；
    少于 2 个候选时返回 None——单候选无需亲和，正常次序必然选它。
    """
    if len(set(cands)) < 2:
        return None
    positions, owners = build_ring(cands, virtual_nodes)
    i = bisect.bisect_left(positions, _h("key:" + str(key_id)))
    if i == len(positions):
        i = 0  # 环尾回绕
    return owners[i]


class AffinityPlugin(GatewayPlugin):
    """managed 域插件：启停/配置由插件中心托管（config.json plugins.affinity）。"""

    def __init__(self, manifest):
        super().__init__(manifest)
        # (候选集, 虚拟节点数) → (位置数组, 条目数组)：候选集极少变化，环复用免重复散列
        self._ring_cache: dict[tuple, tuple[list[int], list[str]]] = {}

    def _cfg(self) -> dict:
        # managed 域配置由插件中心统一读写（load_config 按 mtime 缓存，热路径无额外磁盘 IO）
        from app.plugins.manager import plugin_center
        try:
            return plugin_center.get_config(self.manifest.id)
        except Exception:
            return {}

    def preferred_model(self, pool_name: str, key_id: str | None, candidates: list[str]) -> str | None:
        if not key_id or not candidates:
            return None
        cfg = self._cfg()
        raw_pools = str(cfg.get("pools") or "").strip()
        if raw_pools:
            allow = {p for p in re.split(r"[,;，；\s]+", raw_pools) if p}
            if allow and pool_name not in allow:
                return None
        try:
            vn = int(cfg.get("virtual_nodes") or 40)
        except (TypeError, ValueError):
            vn = 40
        ck = (tuple(sorted(set(candidates))), vn)
        ring = self._ring_cache.get(ck)
        if ring is None:
            if len(self._ring_cache) >= _RING_CACHE_MAX:
                self._ring_cache.clear()
            ring = build_ring(candidates, vn)
            self._ring_cache[ck] = ring
        positions, owners = ring
        i = bisect.bisect_left(positions, _h("key:" + str(key_id)))
        if i == len(positions):
            i = 0  # 环尾回绕
        return owners[i]


def create_plugin(manifest):
    return AffinityPlugin(manifest)
