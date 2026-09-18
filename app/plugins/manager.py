# -*- coding: utf-8 -*-
"""插件中心管理器：发现 / 装载 / 热插拔 / 配置持久化。

热插拔语义：
  enable/disable 只翻转持久化状态 + 即时调用插件 on_enable/on_disable
  （Switch 插件在 hook 里注册/注销 POST /{池名} 路由；Headroom 的判定读 config 节点），
  全程无需重启网关、不依赖 /admin/reload。

持久化：managed 域插件状态存 config.json "plugins" 节点——
  "plugins": { "<plugin_id>": {"enabled": true, "config": {...}} }
dedicated 域插件（headroom）沿用自身 config 节点，插件自管，管理器不落盘。

新插件接入：把含 manifest.json（可选 plugin.py）的文件夹丢进
app/plugins/installed/，然后 POST /admin/plugins/scan（或前端「扫描新插件」）。
"""
from __future__ import annotations

import importlib
import logging
import traceback
from pathlib import Path

from app.core.config import load_config, save_config
from app.core.paths import PLUGINS_DIR
from app.plugins.base import (GatewayPlugin, GenericPlugin, PluginManifest,
                              validate_config_against_schema)

logger = logging.getLogger(__name__)


class PluginManager:
    def __init__(self):
        self._plugins: dict[str, GatewayPlugin] = {}
        self._dirs: dict[str, Path] = {}
        self._started = False

    # ---------- 状态存取（managed 域） ----------

    @staticmethod
    def _managed_states() -> dict:
        cfg = load_config()
        p = cfg.get("plugins")
        return p if isinstance(p, dict) else {}

    def _state_of(self, pid: str) -> dict:
        st = self._managed_states().get(pid)
        return st if isinstance(st, dict) else {}

    def _persist_state(self, pid: str, state: dict):
        cfg = load_config()
        # load_config 命中缓存时返回的是同一 dict：复制后改再写，避免半写污染缓存
        cfg = dict(cfg)
        plugins = dict(cfg.get("plugins") or {})
        plugins[pid] = state
        cfg["plugins"] = plugins
        save_config(cfg)

    # ---------- 发现与装载 ----------

    def scan(self, app=None) -> list[str]:
        """扫描插件目录：新增的导入装载，目录已删除的注销（启用中的先禁用）。"""
        events: list[str] = []
        seen: set[str] = set()
        if PLUGINS_DIR.is_dir():
            for d in sorted(PLUGINS_DIR.iterdir()):
                if not d.is_dir() or d.name.startswith(("_", ".")):
                    continue
                mf_path = d / "manifest.json"
                if not mf_path.is_file():
                    continue
                try:
                    mf = PluginManifest.load(mf_path)
                except Exception as e:
                    logger.error(f"[插件中心] 清单无效 {d.name}: {e}")
                    events.append(f"✗ 清单无效 {d.name}: {e}")
                    continue
                seen.add(mf.id)
                if mf.id in self._plugins:
                    continue
                plugin = self._import_plugin(mf, d)
                if plugin is None:
                    continue
                self._plugins[mf.id] = plugin
                self._dirs[mf.id] = d
                events.append(f"+ 发现插件 {mf.name}({mf.id}) v{mf.version}")
                if app is not None:
                    self._mount_router(app, plugin)
        # 目录已删除的插件：注销
        for pid in [p for p in list(self._plugins) if p not in seen]:
            gone = self._plugins.pop(pid)
            if app is not None and self.is_enabled(pid):
                try:
                    gone.on_disable(app)
                except Exception:
                    logger.exception(f"[插件中心] 注销插件 {pid} 的 on_disable 失败")
            self._dirs.pop(pid, None)
            events.append(f"- 插件已移除 {pid}")
        return events

    def _import_plugin(self, mf: PluginManifest, d: Path) -> GatewayPlugin | None:
        entry = d / "plugin.py"
        if not entry.is_file():
            return GenericPlugin(mf, lambda pid=mf.id: self._state_of(pid))
        mod_name = f"app.plugins.installed.{d.name}.plugin"
        try:
            mod = importlib.import_module(mod_name)
        except Exception as e:
            logger.error(f"[插件中心] 插件代码导入失败 {mod_name}: {e}\n{traceback.format_exc()}")
            return None
        obj = None
        factory = getattr(mod, "create_plugin", None)
        if callable(factory):
            obj = factory(mf)
        if obj is None:
            obj = getattr(mod, "PLUGIN", None)
        if isinstance(obj, type) and issubclass(obj, GatewayPlugin):
            obj = obj(mf)
        if not isinstance(obj, GatewayPlugin):
            logger.error(f"[插件中心] {mod_name} 未暴露 GatewayPlugin 实例（需模块级 PLUGIN 或 create_plugin 工厂）")
            return None
        if obj.manifest.id != mf.id:
            obj.manifest = mf  # 以 manifest.json 为准
        return obj

    def load_all(self, app) -> None:
        """应用装配：扫描 + 挂载插件路由 + 恢复启用态。在 app.main 路由注册完成后调用。"""
        self.scan(app)
        for pid, plugin in self._plugins.items():
            self._mount_router(app, plugin)
            if self.is_enabled(pid):
                try:
                    plugin.on_enable(app)
                    logger.info(f"[插件中心] 已启用 {pid}")
                except Exception:
                    logger.exception(f"[插件中心] 插件 {pid} on_enable 失败（保持禁用态）")
        self._started = True

    def _mount_router(self, app, plugin: GatewayPlugin):
        router = getattr(plugin, "router", None)
        if router is None:
            return
        # 去重：新版 FastAPI include_router 会挂成 _IncludedRouter 包装（保留 original_router），
        # 旧版是平铺的 APIRoute（用 plugin_router_of 标记）。两种形态都认。
        for r in app.router.routes:
            if getattr(r, "original_router", None) is router or getattr(r, "plugin_router_of", "") == plugin.manifest.id:
                return
        before = len(app.router.routes)
        app.include_router(router)
        for r in app.router.routes[before:]:
            try:
                r.plugin_router_of = plugin.manifest.id
            except AttributeError:
                pass

    # ---------- 启停（热） ----------

    def is_enabled(self, pid: str) -> bool:
        """热路径判定：dedicated 域插件委托其自身（如 headroom 读 config.headroom.enabled）；
        managed 域读持久化状态，未持久化过时回退 manifest.default_enabled。"""
        plugin = self._plugins.get(pid)
        if plugin is None:
            return False
        if plugin.manifest.config_scope == "dedicated":
            try:
                return plugin.is_enabled()
            except Exception:
                return False
        st = self._state_of(pid)
        return bool(st.get("enabled", plugin.manifest.default_enabled))

    def enable(self, pid: str, app) -> bool:
        plugin = self._plugins.get(pid)
        if plugin is None:
            raise KeyError(pid)
        if plugin.manifest.config_scope != "dedicated":
            st = self._state_of(pid)
            self._persist_state(pid, {**st, "enabled": True})
        elif hasattr(plugin, "set_enabled"):
            plugin.set_enabled(True)
        plugin.on_enable(app)
        return True

    def disable(self, pid: str, app) -> bool:
        plugin = self._plugins.get(pid)
        if plugin is None:
            raise KeyError(pid)
        if plugin.manifest.config_scope != "dedicated":
            st = self._state_of(pid)
            self._persist_state(pid, {**st, "enabled": False})
        elif hasattr(plugin, "set_enabled"):
            plugin.set_enabled(False)
        plugin.on_disable(app)
        return True

    # ---------- 配置 ----------

    @staticmethod
    def _is_managed(plugin: GatewayPlugin) -> bool:
        """managed 域（含 GenericPlugin）配置由管理器统一托管；dedicated 域由插件自管。"""
        return plugin.manifest.config_scope != "dedicated"

    def get_config(self, pid: str) -> dict:
        plugin = self._plugins[pid]
        if self._is_managed(plugin):
            return validate_config_against_schema(
                plugin.manifest.config_schema, self._state_of(pid).get("config") or {})
        return plugin.get_config()

    def set_config(self, pid: str, cfg: dict) -> dict:
        plugin = self._plugins[pid]
        if self._is_managed(plugin):
            new_cfg = validate_config_against_schema(plugin.manifest.config_schema, cfg or {})
            st = self._state_of(pid)
            self._persist_state(pid, {**st, "config": new_cfg})
            return self.get_config(pid)
        plugin.set_config(cfg)  # dedicated 域：先校验后落盘由插件负责
        return plugin.get_config()

    # ---------- 请求管线 hook ----------

    def active_compressor(self):
        """出站压缩 hook：返回第一个启用的压缩插件（热路径 O(n插件数)，通常为 0~1）。"""
        for plugin in self._plugins.values():
            if not self.is_enabled(plugin.manifest.id):
                continue
            c = plugin.active_compressor()
            if c is not None:
                return c
        return None

    # ---------- 生命周期 ----------

    def startup(self, app) -> None:
        for pid, plugin in self._plugins.items():
            if not self.is_enabled(pid):
                continue
            try:
                plugin.on_startup(app)
            except Exception:
                logger.exception(f"[插件中心] 插件 {pid} on_startup 失败（不影响其余插件）")

    def shutdown(self) -> None:
        for plugin in self._plugins.values():
            try:
                plugin.on_shutdown()
            except Exception:
                pass

    # ---------- 视图 ----------

    def list_plugins(self) -> list[dict]:
        out = []
        for pid, plugin in self._plugins.items():
            try:
                enabled = self.is_enabled(pid)
                cfg = self.get_config(pid)
            except Exception:
                logger.exception(f"[插件中心] 插件 {pid} 状态/配置读取失败")
                enabled, cfg = False, {}
            out.append({
                **plugin.manifest.to_dict(),
                "enabled": enabled,
                "config": cfg,
                "has_code": not isinstance(plugin, GenericPlugin),
                "dir": self._dirs.get(pid, Path("")).name,
            })
        out.sort(key=lambda x: (x.get("order", 100), x["id"]))
        return out

    def get(self, pid: str) -> GatewayPlugin | None:
        return self._plugins.get(pid)

    @property
    def plugins_dir(self) -> str:
        return str(PLUGINS_DIR)


plugin_center = PluginManager()
