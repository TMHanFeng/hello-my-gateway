# -*- coding: utf-8 -*-
"""Kompress ML 权重下载器（curl 断点续传版）：直接构造 HF 缓存结构，完成后自动热激活。

背景：huggingface.co 直连被墙，python 下载器（requests/xet）经代理对大文件频繁 0 字节
卡死，唯 curl 能持续走通该路由（虽慢但稳）。本脚本用 curl 循环断点续传每个文件到
HF 缓存 blobs/，再拼装 snapshots/<rev>/ 与 refs/main，绕开 python 下载栈。

用法（独立于网关运行，随时可关，重跑自动续传）：
    .venv-headroom/Scripts/python.exe download_kompress_weights.py
    .venv-headroom/Scripts/python.exe download_kompress_weights.py --proxy http://127.0.0.1:7897
    .venv-headroom/Scripts/python.exe download_kompress_weights.py --endpoint https://hf-mirror.com --no-proxy
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).parent
HF = "https://huggingface.co"

# (repo, [需要的文件])：纯 CPU 部署走 ONNX int8（库默认后端 auto 即 ONNX CPU 优先），
# 只需 int8 模型 + ModernBERT 分词器小文件——不需要 merged.pt 与 model.safetensors（合计省 890MB）
JOBS = [
    ("chopratejas/kompress-v2-base",
     ["config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
      "onnx/kompress-int8-wo.onnx"]),
    ("answerdotai/ModernBERT-base",
     ["config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json"]),
]


def api(url: str, proxy: str, timeout: int = 60) -> dict:
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})]
    opener = urllib.request.build_opener(*handlers)
    with opener.open(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def curl_to(url: str, dest: Path, proxy: str, want: int, tag: str):
    """curl 断点续传直到文件达到期望大小。卡死自动断开重连，无限重试。"""
    cmd_base = ["curl", "-L", "-sS", "--connect-timeout", "15",
                "--speed-limit", "2048", "--speed-time", "60",   # <2KB/s 持续 60s 即断开（触发重连）
                "-C", "-"] + (["-x", proxy] if proxy else [])
    round_ = 0
    while True:
        round_ += 1
        have = dest.stat().st_size if dest.exists() else 0
        if want and have >= want:
            print(f"  [{tag}] 完成 {have/1e6:.1f}MB（共 {round_} 段）", flush=True)
            return
        subprocess.run(cmd_base + [url, "-o", str(dest)], timeout=6000)
        have = dest.stat().st_size if dest.exists() else 0
        print(f"  [{tag}] 第{round_}段后 {have/1e6:.1f}/{want/1e6:.1f}MB", flush=True)
        if want and have >= want:
            print(f"  [{tag}] 完成 {have/1e6:.1f}MB（共 {round_} 段）", flush=True)
            return
        time.sleep(5)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default="http://127.0.0.1:7897")
    ap.add_argument("--endpoint", default="", help="备用镜像（如 https://hf-mirror.com），须配 --no-proxy")
    ap.add_argument("--no-proxy", action="store_true")
    args = ap.parse_args()
    proxy = "" if (args.no_proxy or args.endpoint) else args.proxy
    base = (args.endpoint.rstrip("/") if args.endpoint else HF)
    print(f"[配置] 源={base} 代理={proxy or '无'}", flush=True)

    hub = Path(os.environ.get("HF_HUB_CACHE") or (Path.home() / ".cache/huggingface/hub"))
    plan = []
    for repo, files in JOBS:
        org, name = repo.split("/")
        info = api(f"{base}/api/models/{repo}", proxy)
        rev = info["sha"]
        tree = api(f"{base}/api/models/{repo}/tree/main?recursive=true", proxy)
        oids = {f["path"]: (f.get("lfs") or {}).get("oid") or f.get("oid") for f in tree if f["type"] == "file"}
        sizes = {f["path"]: f.get("size", 0) for f in tree if f["type"] == "file"}
        mdir = hub / f"models--{org}--{name}"
        (mdir / "refs").mkdir(parents=True, exist_ok=True)
        (mdir / "refs" / "main").write_text(rev, encoding="ascii")
        for fn in files:
            plan.append((repo, rev, mdir, fn, oids[fn], sizes.get(fn, 0)))
        print(f"[计划] {repo} rev={rev[:10]} {len(files)} 个文件 "
              f"{sum(sizes.get(f, 0) for f in files)/1e6:.0f}MB", flush=True)

    for repo, rev, mdir, fn, oid, size in plan:
        blob = mdir / "blobs" / oid
        snap = mdir / "snapshots" / rev / fn
        if snap.exists() and (not size or snap.stat().st_size == size):
            print(f"[跳过] {repo}/{fn} 已就位", flush=True)
            continue
        print(f"[下载] {repo}/{fn} ({size/1e6:.1f}MB)", flush=True)
        blob.parent.mkdir(parents=True, exist_ok=True)
        curl_to(f"{base}/{repo}/resolve/main/{fn}", blob, proxy, size, fn)
        snap.parent.mkdir(parents=True, exist_ok=True)
        if snap.exists():
            snap.unlink()
        shutil.copyfile(blob, snap)   # Windows 无符号链接语义：快照内存真实文件副本
        print(f"[就位] {repo}/{fn}", flush=True)

    print("[校验] local-first 缓存命中测试（禁网络）...", flush=True)
    sys.path.insert(0, str(REPO / ".venv-headroom/Lib/site-packages"))
    from headroom.onnx_runtime import hf_hub_download_local_first
    ok = True
    for repo, _, _, fn, _, _ in plan:
        try:
            hf_hub_download_local_first(repo, fn, allow_network=False)
            print(f"  ✓ {repo}/{fn}", flush=True)
        except Exception as e:
            ok = False
            print(f"  ✗ {repo}/{fn}: {e}", flush=True)
    if not ok:
        print("[校验] 有文件未通过缓存命中测试", flush=True)
        return 1

    try:
        cfg = json.loads((REPO / "config.json").read_text(encoding="utf-8"))
        body = json.dumps(cfg.get("headroom") or {"enabled": True}).encode()
        req = urllib.request.Request("http://127.0.0.1:8650/admin/headroom", method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + (cfg.get("server", {}).get("api_key") or ""))
        with urllib.request.urlopen(req, body, timeout=15) as resp:
            print("[激活] 网关热激活响应:", resp.read().decode()[:200], flush=True)
    except Exception as e:
        print(f"[激活] 网关回调失败（重启网关亦可激活）: {e}", flush=True)
    print("ALL_DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
