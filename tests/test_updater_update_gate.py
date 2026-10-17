# -*- coding: utf-8 -*-
"""updater 更新判定与执行闸门回归（2026-10-17「周期性停机重启」事故固化）。

事故现象：网关每 ~5 分钟被完整停机重启一次，版本号永远停在 v2.17.22。
成因是「版本号说有新版本（github tag v2.22.14）」与「实际拉 main 拉不到那份代码
（v2.22.14 不在 main 提交链上，main == 本地）」背离，而执行侧无论如何都会停机重启。

本文件钉住两条规则：
  A. 判定只看版本号、不看哈希：取两边 tag 中版本号更高者 latest，latest > 当前版本即更新；
     单边领先也触发；本地已等于/高于 latest 则不更新。哈希不参与判定。
  B. 执行侧：先拉代码、再按「本地 HEAD 是否真的变了」决定要不要停机；
     拉回来什么都没变时，绝不碰服务。这是真正堵死循环的那道闸。

用法: python tests/test_updater_update_gate.py
全部断言通过 exit 0；任何失败 exit 1。
"""
import ast
import importlib.util
import inspect
import logging
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPDATER = os.path.join(REPO, "scripts", "updater.py")

_spec = importlib.util.spec_from_file_location("updater_under_test", UPDATER)
u = importlib.util.module_from_spec(_spec)
sys.argv = ["updater"]
_spec.loader.exec_module(u)

# 导入时 updater 会挂一个 FileHandler(scripts/updater.log)；测试期间别往里写
for _h in list(logging.getLogger().handlers):
    logging.getLogger().removeHandler(_h)
    try:
        _h.close()
    except Exception:
        pass
logging.getLogger("updater").addHandler(logging.NullHandler())
logging.getLogger("updater").propagate = False

RESULTS = []


def check(name, got, want):
    ok = got == want
    RESULTS.append((name, ok, got, want))
    print(f"{'OK  ' if ok else 'FAIL'}  {name}" + ("" if ok else f"   got={got!r} want={want!r}"))


def gw(version):
    return {"reachable": True, "version": version, "commit": "f6f43ba"}


def gate(versions, current="v2.17.22"):
    """只走判定，不触发网络：versions / gw 全部显式传入"""
    return u.has_updates_available(versions, gw(current))


# ══ A. 判定：只看版本号，任一端更新即触发 ══════════════════════════════════

# 事故当天实况：github 领先、gitee 持平 → 按规则「有更新」
check("事故实况(github v2.22.14 领先) → 判有更新",
      gate({"gitee": "v2.17.22", "github": "v2.22.14"}), (True, "v2.17.22", "v2.22.14"))

# 只有 gitee 领先时也必须触发；latest 取「真正更高」的那个，不是「github 优先」
check("仅 gitee 领先 → 判有更新，latest 取更高者",
      gate({"gitee": "v2.18.0", "github": "v2.17.22"}), (True, "v2.17.22", "v2.18.0"))
check("仅 github 领先 → 判有更新",
      gate({"gitee": "v2.17.22", "github": "v2.22.14"}), (True, "v2.17.22", "v2.22.14"))
check("两边都领先 → latest 取较高者",
      gate({"gitee": "v2.18.0", "github": "v2.22.14"}), (True, "v2.17.22", "v2.22.14"))

# 不更新的几种情形
check("已等于其中最新 → 不更新", gate({"gitee": "v2.17.22", "github": "v2.22.14"}, "v2.22.14")[0], False)
check("两边都持平 → 不更新", gate({"gitee": "v2.17.22", "github": "v2.17.22"})[0], False)
check("本地比两边都新 → 不更新", gate({"gitee": "v2.17.0", "github": "v2.17.22"}, "v2.18.0")[0], False)
check("两边都落后 → 不更新", gate({"gitee": "v2.16.0", "github": "v2.16.0"})[0], False)

# 版本号取「最高」而非「github 优先」——旧实现会在这里漏判
check("pick_latest_version 取最高版本号(非 github 优先)",
      u.pick_latest_version({"gitee": "v2.23.0", "github": "v2.22.14"}), ("origin", "v2.23.0"))
check("pick_latest_version 同版本号时 github 优先",
      u.pick_latest_version({"gitee": "v2.22.14", "github": "v2.22.14"}), ("github", "v2.22.14"))

# 哈希不参与判定
_src = inspect.getsource(u.has_updates_available)
check("判定路径不调用 get_latest_commit（不看远端哈希）", "get_latest_commit" in _src, False)
check("判定路径不调用 get_current_commit（不做哈希兜底）", "get_current_commit" in _src, False)
check("两边版本号都取不到 → 不更新（不做哈希兜底）",
      gate({"gitee": None, "github": None})[0], False)
check("单边版本号缺失 + 另一边有更新 → 仍判更新",
      gate({"gitee": None, "github": "v2.22.14"})[0], True)

# ══ B. 执行侧：拉取无变化 → 绝不碰服务（真正堵死循环的闸）════════════════

def run_perform_update(head_before, head_after, pull_results):
    """在受控环境下跑一遍 perform_update，返回 (返回值, 关键调用序列)"""
    calls = []
    heads = [head_before]
    pulls = list(pull_results)
    orig = {n: getattr(u, n) for n in (
        "is_tailscale_online", "get_inner_address", "has_updates_available",
        "resolve_update_source", "get_current_commit", "git_pull",
        "stop_service", "start_service", "record_error", "is_service_active")}
    try:
        u.is_tailscale_online = lambda: True
        u.get_inner_address = lambda: {"address": "100.67.39.90:8650", "dns": "host.ts.net"}
        u.has_updates_available = lambda *a, **k: (True, "v2.17.22", "v2.22.14")
        u.resolve_update_source = lambda source=None: ("github", "f6f43ba0cb4b21ad136af928e2b42fc0eb8e569e")
        u.record_error = lambda *a, **k: calls.append("record_error")
        u.is_service_active = lambda: True

        def _pull(remote="origin", branch=""):
            calls.append(f"pull:{remote}")
            heads[0] = head_after
            return pulls.pop(0) if pulls else (False, "fetch failed", "main")

        def _stop():
            calls.append("stop")
            return True

        def _start():
            calls.append("start")
            return True

        u.get_current_commit = lambda: heads[0]
        u.git_pull = _pull
        u.stop_service = _stop
        u.start_service = _start
        u.time.sleep = lambda *_: None      # 跳过「等端口释放」
        result = u.perform_update()
        return result, calls
    finally:
        for n, fn in orig.items():
            setattr(u, n, fn)


HEAD = "f6f43ba0cb4b21ad136af928e2b42fc0eb8e569e"
NEW = "7806b7bfd47434ee1bba50a043b82c8b2cf157ba"

# 事故当天的那一轮：pull main 回来是空的 → 不停机、不重启（循环在这里被切断）
check("pull 无变化 → 不停机不重启，返回 True",
      run_perform_update(HEAD, HEAD, [(True, "Already up to date.", "main")]),
      (True, ["pull:github"]))

# 真拿到新代码 → 照旧 stop → start
check("HEAD 变化 → 停机后重启",
      run_perform_update(HEAD, NEW, [(True, "Fast-forward", "main")]),
      (True, ["pull:github", "stop", "start"]))

# 两边都拉失败且代码没变 → 不碰服务，返回 False
check("pull 全失败且 HEAD 未变 → 不碰服务，返回 False",
      run_perform_update(HEAD, HEAD, [(False, "fetch failed", "main"), (False, "fetch failed", "main")])[0],
      False)
check("pull 全失败且 HEAD 未变 → 调用序列不含 stop/start",
      [c for c in run_perform_update(HEAD, HEAD, [(False, "f1", "main"), (False, "f2", "main")])[1]
       if c in ("stop", "start")],
      [])

# pull 报错但代码确实变了（硬重置兜底）→ 仍按有更新处理，照常重启
check("pull 报错但 HEAD 变化 → 仍重启",
      run_perform_update(HEAD, NEW, [(False, "merge conflict", "main"), (False, "f", "main")]),
      (True, ["pull:github", "pull:origin", "stop", "start"]))

# ══ C. 间隔与闸门结构 ═══════════════════════════════════════════════════
check("GIT_PULL_INTERVAL = 1800（30 分钟）", u.GIT_PULL_INTERVAL, 1800)
with open(UPDATER, encoding="utf-8") as f:
    _full = f.read()
check("主循环仍保留 if has_update: 条件判断", "if has_update:" in _full, True)
check("主循环仍受 GIT_PULL_INTERVAL 门控", "now - last_git_pull_check > GIT_PULL_INTERVAL" in _full, True)
check("stop_service 已排到 git_pull 之后",
      _full.index("success, output, branch = git_pull(remote)") < _full.index("if not stop_service():"), True)

# ══ D. 版本号只认 main 提交链上的 tag（事故的源头过滤）════════════════════
# 用临时仓库真实复现：一侧分支打了版本号更高的 tag（= 废弃分支撞版本号），
# 判定必须只看到 main 上那些。不打网络、不碰本仓库。

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **_GIT_ENV})


def _build_branch_vs_main_repo():
    """main 线最高 v1.3.0；另一条分支打了 v9.9.9（模拟 hellonew 式的废弃线撞版本号）"""
    root = tempfile.mkdtemp(prefix="hmg_gate_")
    up, bare, clone = (os.path.join(root, n) for n in ("up", "bare.git", "clone"))
    _git(["init", "-q", "-b", "main", up], root)
    _git(["-c", "core.autocrlf=false", "commit", "-q", "--allow-empty", "-m", "c1"], up)
    _git(["tag", "v1.0.0"], up)
    _git(["commit", "-q", "--allow-empty", "-m", "c2"], up)
    _git(["tag", "v1.2.0"], up)
    _git(["commit", "-q", "--allow-empty", "-m", "c3 (main)"], up)
    _git(["tag", "v1.3.0"], up)
    side = _git(["rev-parse", "HEAD~2"], up).stdout.strip()
    _git(["branch", "side", side], up)
    _git(["checkout", "-q", "side"], up)
    _git(["commit", "-q", "--allow-empty", "-m", "c2s (废弃分支)"], up)
    _git(["tag", "v9.9.9"], up)
    _git(["checkout", "-q", "main"], up)
    _git(["clone", "-q", "--bare", up, bare], root)
    _git(["push", "-q", bare, "main", "side", "--tags"], up)
    _git(["clone", "-q", bare, clone], root)
    _git(["fetch", "-q", "origin", "--tags"], clone)
    return clone


try:
    _clone = _build_branch_vs_main_repo()
    _orig_repo_dir = u.REPO_DIR
    try:
        u.REPO_DIR = _clone                  # 让取版本号的函数在临时仓库里跑
        check("main 线之外的高版本 tag 被忽略（事故场景复现）",
              u.get_remote_latest_tag("origin"), "v1.3.0")
        # 废弃分支被删掉后结果不变——说明过滤不依赖「远端恰好没有多余 tag」
        _git(["push", "-q", "origin", "--delete", "side"], _clone)
        _git(["push", "-q", "origin", "--delete", "refs/tags/v9.9.9"], _clone)
        check("废弃分支与 tag 删除后判定不变",
              u.get_remote_latest_tag("origin"), "v1.3.0")
    finally:
        u.REPO_DIR = _orig_repo_dir
except Exception as e:                      # git 不可用等环境问题，明确报出来而不是静默跳过
    check("main-only tag 过滤（环境搭建失败）", f"{type(e).__name__}: {e}", "fixture-built")

# 查「代码」而不是 docstring——docstring 里会引用旧实现的名字（ls-remote）
_fn_code = ast.dump(ast.Module(
    body=ast.parse(inspect.getsource(u.get_remote_latest_tag)).body[0].body[1:], type_ignores=[]))
check("版本查询代码不再使用 ls-remote --tags（会看到非 main 的 tag）",
      "ls-remote" in _fn_code, False)
check("版本查询代码改用 git tag --merged 限定 main", "--merged" in _fn_code, True)

# fetch 要传对象，预算不能沿用只取 ref 时的 15/20s——否则网络一抖就判成「版本号取不到」
check("fetch 有独立且更宽的预算（VERSION_FETCH_TIMEOUT）",
      "VERSION_FETCH_TIMEOUT" in _fn_code, True)
check("VERSION_FETCH_TIMEOUT 大于调用方的 github 超时", u.VERSION_FETCH_TIMEOUT > 20, True)
_vr_code = ast.dump(ast.Module(
    body=ast.parse(inspect.getsource(u.get_remote_versions)).body[0].body[1:], type_ignores=[]))
check("get_remote_versions 的等待预算已计入 fetch 预算",
      "VERSION_FETCH_TIMEOUT" in _vr_code, True)

# ── 汇总 ────────────────────────────────────────────────────────────────
_failed = [r for r in RESULTS if not r[1]]
print(f"\n{len(RESULTS) - len(_failed)}/{len(RESULTS)} 通过")
if _failed:
    for name, _ok, got, want in _failed:
        print(f"  FAIL {name}: got={got!r} want={want!r}")
    sys.exit(1)
print("全部通过 ✅")
