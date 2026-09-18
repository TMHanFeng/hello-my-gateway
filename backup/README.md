# backup/ — 备份统一归档

代码写入备份一律经 `app/core/paths.py` 的常量定位到本目录，不要再把 *.bak 散落在仓库根目录。

| 目录 | 内容 | 写入方 |
|:--|:--|:--|
| `config/` | `config.json.bak`（scheduler 每日 14:00 覆盖写）及工具带后缀备份 `.bak_<用途>_<日期>` | app/core/scheduler.py、app/tools/probe_reasoning.py |
| `db/` | gateway.db 快照备份 | 手工/工具 |
| `static/` | 前端历史版本（如改版前的 hfadmin.html） | 手工 |
| `snapshots/` | 大改造前的整库快照（含 BACKUP_INFO.txt 说明） | 手工 |

命名约定：`<原名>.bak_<用途>_<yyyymmdd>`。本目录已加入 .gitignore，不入版本库。
