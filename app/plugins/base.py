# -*- coding: utf-8 -*-
"""插件中心基础层：manifest 模型、插件协议、无代码通用插件。

插件目录约定（app/plugins/installed/<dir>/）：
  manifest.json   必需。插件清单（id/name/version/description/...）
  plugin.py       可选。定义模块级 `PLUGIN`（GatewayPlugin 实例，或类由管理器实例化）。
                  只有 manifest 没有 plugin.py 时，管理器以 GenericPlugin 包装——
                  纯配置型自定义插件（仅启用/停用/按 schema 存配置）零代码即可接入。

manifest.json 字段：
  id              必填，全局唯一（建议 <dir> 同名）
  name            必填，显示名
  version         必填，语义化版本
  description     简介（卡片显示）
  author / icon / homepage / kind(builtin|custom) / order(排序权重)
  config_scope    managed（默认，配置存 config.json "plugins".<id>）| dedicated（插件自管，如 headroom 用 "headroom" 节点）
  default_enabled manifest 内 unknown 时的兜底默认态（真实态以持久化配置为准）
  config_schema   [ {key,type(int|float|bool|str|enum),default,min,max,options:[{value,label}],title,desc} ]
                  managed 域插件的设置页据此自动渲染与校验
  ui_hint         前端卡片上的设置引导文案（可选）
  requirements    可选：插件专属依赖清单文件名（相对插件目录，如 "requirements.txt"）——
                  插件自己的依赖与插件放在一起，不污染仓库根的 requirements.txt
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "manifest.json"


@dataclass
class PluginManifest:
    id: str
    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    icon: str = "🧩"
    homepage: str = ""
    kind: str = "custom"                 # builtin | custom
    order: int = 100
    config_scope: str = "managed"        # managed | dedicated
    default_enabled: bool = False
    config_schema: list = field(default_factory=list)
    ui_hint: str = ""
    requirements: str = ""

    @classmethod
    def load(cls, path: Path) -> "PluginManifest":
        raw = json.loads(path.read_text(encoding="utf-8"))
        mid = str(raw.get("id") or "").strip()
        if not mid:
            raise ValueError(f"{path}: manifest 缺少 id")
        if not str(raw.get("name") or "").strip():
            raise ValueError(f"{path}: manifest 缺少 name")
        schema = raw.get("config_schema") or []
        if not isinstance(schema, list):
            raise ValueError(f"{path}: config_schema 必须为数组")
        m = cls(
            id=mid,
            name=str(raw["name"]).strip(),
            version=str(raw.get("version") or "0.0.0"),
            description=str(raw.get("description") or ""),
            author=str(raw.get("author") or ""),
            icon=str(raw.get("icon") or "🧩"),
            homepage=str(raw.get("homepage") or ""),
            kind=str(raw.get("kind") or "custom"),
            order=int(raw.get("order") or 100),
            config_scope=str(raw.get("config_scope") or "managed"),
            default_enabled=bool(raw.get("default_enabled", False)),
            config_schema=schema,
            ui_hint=str(raw.get("ui_hint") or ""),
            requirements=str(raw.get("requirements") or ""),
        )
        if m.config_scope not in ("managed", "dedicated"):
            raise ValueError(f"{path}: config_scope 仅支持 managed / dedicated")
        return m

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "version": self.version,
            "description": self.description, "author": self.author, "icon": self.icon,
            "homepage": self.homepage, "kind": self.kind, "order": self.order,
            "config_scope": self.config_scope, "default_enabled": self.default_enabled,
            "config_schema": self.config_schema, "ui_hint": self.ui_hint,
            "requirements": self.requirements,
        }


def validate_config_against_schema(schema: list, cfg: dict) -> dict:
    """按 manifest config_schema 校验并归一化用户配置（返回全新 dict，不产生半写）。

    规则：schema 未提及的键原样保留；类型做 int/float/bool 归一；min/max 裁剪；
    enum 校验取值；缺省键补 default。任一不可转换即抛 ValueError。"""
    out = dict(cfg or {})
    for item in schema or []:
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        typ = str(item.get("type") or "str")
        has_default = "default" in item
        if key not in out or out[key] is None:
            if has_default:
                out[key] = item["default"]
            continue
        v = out[key]
        try:
            if typ == "int":
                v = int(v)
                if "min" in item:
                    v = max(int(item["min"]), v)
                if "max" in item:
                    v = min(int(item["max"]), v)
            elif typ == "float":
                v = float(v)
                if "min" in item:
                    v = max(float(item["min"]), v)
                if "max" in item:
                    v = min(float(item["max"]), v)
            elif typ == "bool":
                v = bool(v) if not isinstance(v, str) else v.strip().lower() in ("1", "true", "yes", "on")
            elif typ == "enum":
                allowed = [o.get("value") for o in (item.get("options") or [])]
                if allowed and v not in allowed:
                    raise ValueError(f"选项 {v!r} 不在允许范围 {allowed}")
            else:  # str
                v = str(v)
        except ValueError:
            raise
        except (TypeError, Exception) as e:  # noqa: BLE001 —— 逐字段归一化失败即整体拒绝
            raise ValueError(f"字段 {key} 校验失败: {e}") from e
        out[key] = v
    return out


class GatewayPlugin:
    """插件协议。自定义插件继承并按需覆写；最简实现只需 manifest（见 GenericPlugin）。

    生命周期：scan(import) → load_all(挂路由) → lifespan(on_startup) → …请求期 hooks… → on_shutdown
    热插拔：enable/disable 即时调用 on_enable/on_disable（可注册/注销路由等），
    并落盘持久化状态——全程无需重启网关。"""

    def __init__(self, manifest: PluginManifest):
        self.manifest = manifest

    # ---- 生命周期钩子（按需覆写）----
    def on_enable(self, app) -> None: ...
    def on_disable(self, app) -> None: ...
    def on_startup(self, app) -> None: ...
    def on_shutdown(self) -> None: ...

    # ---- 请求管线 hook（返回 None 表示本插件不介入该 hook）----
    def active_compressor(self):
        """提供出站压缩能力的插件返回自身，否则 None（pool.py 据此旁路）。"""
        return None

    def preferred_model(self, pool_name: str, key_id: str | None, candidates: list[str]) -> str | None:
        """路由亲和 hook（v2.16.2）：返回该调用方在本池应优先使用的模型条目 id（须在
        candidates 内），None = 不介入。选模层拿到后仍走完整可用性检查，目标不可用
        自动落回池内正常次序（sequential/auto_order/load_balance），恢复后回粘。"""
        return None

    # ---- 配置面（managed 域由管理器托管；dedicated 域由插件自管）----
    @property
    def router(self):
        """插件可选自带 APIRouter（如设置读写/统计），由管理器统一挂载。"""
        return None

    def is_enabled(self) -> bool:
        raise NotImplementedError

    def set_enabled(self, flag: bool) -> None:
        """dedicated 域插件的启停落盘由本方法实现（managed 域管理器代管，无需覆写）。"""
        raise NotImplementedError

    def get_config(self) -> dict:
        raise NotImplementedError

    def set_config(self, cfg: dict) -> dict:
        raise NotImplementedError


class GenericPlugin(GatewayPlugin):
    """无代码插件：只有 manifest.json。启用/停用与配置由管理器托管（managed 域）。"""

    def __init__(self, manifest: PluginManifest, state_provider):
        super().__init__(manifest)
        self._state = state_provider  # callable -> {"enabled": bool, "config": dict}

    def is_enabled(self) -> bool:
        return bool(self._state().get("enabled", self.manifest.default_enabled))

    def get_config(self) -> dict:
        return validate_config_against_schema(self.manifest.config_schema, self._state().get("config") or {})

    def set_config(self, cfg: dict) -> dict:
        return validate_config_against_schema(self.manifest.config_schema, cfg)
