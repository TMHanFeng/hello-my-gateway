# -*- coding: utf-8 -*-
"""项目路径常量中心：所有模块的文件系统定位统一从这里取值。

工程重组（v2.14.0）后代码不再平铺在仓库根目录，任何模块里都不允许再写
`Path(__file__).parent / "config.json"` 这类"文件在哪我就在哪"的相对定位——
一律 import 本模块的常量，避免目录调整时相互调用地址失配。

布局约定：代码在 app/，配置 config.json 在根，运行时产物在 data/（库/缓存/pid），
日志在 logs/，备份归档在 backup/，插件一律在 app/plugins/installed/<插件目录>/。
"""
from pathlib import Path

# app/core/paths.py → parents: [0]=core, [1]=app, [2]=仓库根
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 运行时数据（仓库根 data/）：数据库/缓存/pid 等进程产物与代码隔离
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_PATH = PROJECT_ROOT / "config.json"
DB_PATH = DATA_DIR / "gateway.db"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "gateway.log"
STATIC_DIR = PROJECT_ROOT / "static"
DOCS_DIR = PROJECT_ROOT / "docs"
FRONTEND_PATH = STATIC_DIR / "index.html"       # /admin 面板（FRONTEND_ADMIN 的历史别名）
FRONTEND_ADMIN = FRONTEND_PATH
FRONTEND_HFADMIN = STATIC_DIR / "hfadmin.html"  # /hfadmin 面板
REASONING_PROBE_CACHE = DATA_DIR / "reasoning_probe_cache.json"
DEV_PID_PATH = DATA_DIR / "dev8651.pid"         # 8651 开发实例 pid（scripts/start|stop_dev_8651.ps1）

# 备份统一归档（仓库根 backup/）：按类型分目录，代码写入备份一律经这里的常量
BACKUP_DIR = PROJECT_ROOT / "backup"
CONFIG_BAK_PATH = BACKUP_DIR / "config" / "config.json.bak"                 # scheduler 每日备份
CONFIG_BAK_DIR = BACKUP_DIR / "config"                                      # 工具类带后缀备份（.bak_xxx）
DB_BAK_DIR = BACKUP_DIR / "db"                                              # 数据库快照备份
STATIC_BAK_DIR = BACKUP_DIR / "static"                                      # 前端历史版本备份
SNAPSHOTS_DIR = BACKUP_DIR / "snapshots"                                    # 整库改造前快照

# 插件中心：app/plugins/installed/ 下每个子目录 = 一个插件（manifest.json + plugin.py）
PLUGINS_DIR = Path(__file__).resolve().parents[1] / "plugins" / "installed"
