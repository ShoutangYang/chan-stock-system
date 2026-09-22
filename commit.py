#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
自动版本管理助手
================

把「改完代码 -> 记录变更 -> 升版本号 -> 打标签 -> 写更新日志」收敛成一条命令。

用法
----
    python commit.py                          # 自动汇总变更并提交（默认 chore）
    python commit.py "feat: 新增基本面分析"     # 指定提交信息
    python commit.py "feat: 新增基本面分析" --minor   # 提交 + 升次版本号 + 打标签
    python commit.py "fix: 修复扫描报错" --patch      # 提交 + 升修订号 + 打标签
    python commit.py "重构市场层" --major            # 提交 + 升主版本号 + 打标签
    python commit.py --dry-run                 # 只看会提交什么，不真提交
    python commit.py "..." --push              # 若已配置远端，提交后顺带推送

版本号规则（SemVer）
--------------------
    --patch  1.0.0 -> 1.0.1   修 bug、文案、样式微调
    --minor  1.0.0 -> 1.1.0   新增功能（向后兼容）
    --major  1.0.0 -> 2.0.0   破坏性变更 / 架构调整

做了什么
--------
    1. git add -A
    2. 按文件类型归类，生成结构化提交信息
    3. git commit（UTF-8 编码）
    4. 若指定 --patch/--minor/--major：更新 VERSION、追加 CHANGELOG.md、打 tag vX.Y.Z
    5. 可选 --push 推送到远端（含标签）
"""

import io
import os
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(ROOT, "VERSION")
CHANGELOG_FILE = os.path.join(ROOT, "CHANGELOG.md")

# 文件归类：用于自动生成可读的提交信息
GROUPS = [
    ("核心引擎", ("app/core/",)),
    ("API 接口", ("app/main.py",)),
    ("前端页面", ("app/web/",)),
    ("测试", ("test_", "tests/")),
    ("文档", ("README.md", "CHANGELOG.md", "docs/")),
    ("配置/脚本", ("requirements.txt", "start.py", "start.bat", ".gitignore",
                   "commit.py", "VERSION")),
]


# ---------------------------------------------------------------- 基础工具

def run(args, capture=True, check=False):
    """执行 git 命令，统一使用 UTF-8，避免 Windows 控制台乱码。

    自动注入 -c core.quotepath=false：否则 git 会把中文路径输出成
    "\\347\\274\\240..." 八进制转义，写进 CHANGELOG 就是一堆乱码。
    """
    if args and args[0] == "git":
        args = ["git", "-c", "core.quotepath=false"] + list(args[1:])
    p = subprocess.run(
        args, cwd=ROOT, capture_output=capture, text=True,
        encoding="utf-8", errors="replace",
    )
    if check and p.returncode != 0:
        sys.stderr.write((p.stderr or p.stdout or "") + "\n")
        sys.exit(p.returncode)
    return p


def read_version():
    if not os.path.exists(VERSION_FILE):
        return "0.0.0"
    with io.open(VERSION_FILE, "r", encoding="utf-8") as f:
        return f.read().strip() or "0.0.0"


def write_version(v):
    with io.open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(v + "\n")


def bump(v, part):
    nums = (v.split(".") + ["0", "0", "0"])[:3]
    try:
        major, minor, patch = [int(x) for x in nums]
    except ValueError:
        major, minor, patch = 1, 0, 0
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


# ---------------------------------------------------------------- 变更收集

def status_entries():
    """返回 [(状态, 路径)]，已排除 .gitignore 命中的文件。"""
    p = run(["git", "status", "--porcelain"], check=True)
    out = []
    for line in (p.stdout or "").splitlines():
        if not line.strip():
            continue
        st = line[:2].strip()
        path = line[3:].strip().strip('"')
        # 重命名格式: R  old -> new
        if " -> " in path:
            path = path.split(" -> ")[-1]
        out.append((st, path))
    return out


def group_of(path):
    path = path.replace("\\", "/")
    for name, prefixes in GROUPS:
        for pre in prefixes:
            if path.startswith(pre) or path.endswith(pre):
                return name
    return "其他"


def build_auto_message(entries):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    buckets = {}
    for st, path in entries:
        buckets.setdefault(group_of(path), []).append((st, path))
    lines = [f"chore: 更新 {len(entries)} 个文件 ({now})", ""]
    for g in [g[0] for g in GROUPS] + ["其他"]:
        items = buckets.get(g)
        if not items:
            continue
        pretty = []
        for st, path in items:
            flag = {"M": "修改", "A": "新增", "D": "删除",
                    "AM": "新增", "??": "新增"}.get(st, st)
            pretty.append(f"{flag} {path.replace(chr(92), '/')}")
        lines.append(f"- {g}: " + "、".join(pretty))
    return "\n".join(lines)


def append_changelog(version, message, entries):
    """把本次变更追加到 CHANGELOG.md 顶部（保留已有内容）。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    buckets = {}
    for st, path in entries:
        buckets.setdefault(group_of(path), []).append(path.replace("\\", "/"))

    body = [f"## v{version} · {now}", ""]
    # 主体说明：取提交信息第一行
    first = (message or "").strip().splitlines()[0] if message else "更新"
    body.append(f"**{first}**")
    body.append("")
    for g in [g[0] for g in GROUPS] + ["其他"]:
        items = buckets.get(g)
        if not items:
            continue
        body.append(f"- **{g}**：{', '.join(sorted(set(items)))}")
    body.append("")

    header = "# 更新日志\n\n"
    old = ""
    if os.path.exists(CHANGELOG_FILE):
        with io.open(CHANGELOG_FILE, "r", encoding="utf-8") as f:
            old = f.read()
        # 去掉旧的文件头，稍后统一加回去
        if old.startswith(header):
            old = old[len(header):]
        elif old.startswith("# 更新日志"):
            idx = old.find("\n")
            old = old[idx + 1:].lstrip("\n")
    with io.open(CHANGELOG_FILE, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(body) + "\n" + old)


# ---------------------------------------------------------------- 主流程

def main(argv):
    args = [a for a in argv[1:]]
    dry = "--dry-run" in args
    push = "--push" in args
    part = None
    for flag in ("--major", "--minor", "--patch"):
        if flag in args:
            part = flag[2:]
    msg_args = [a for a in args
                if not a.startswith("--")]

    # 仓库检查
    if run(["git", "rev-parse", "--is-inside-work-tree"]).stdout.strip() != "true":
        print("✗ 当前目录不是 Git 仓库，请先执行：git init")
        return 1

    run(["git", "add", "-A"], check=True)
    entries = status_entries()
    if not entries:
        print("✓ 工作区干净，没有需要提交的变更")
        return 0

    message = " ".join(msg_args).strip()
    if not message:
        message = build_auto_message(entries)

    print("=" * 62)
    print(f"待提交 {len(entries)} 项：")
    for st, path in entries[:40]:
        print(f"  [{st:2}] {path}")
    if len(entries) > 40:
        print(f"  ... 其余 {len(entries) - 40} 项省略")
    print("-" * 62)

    version = read_version()
    new_version = version
    if part:
        new_version = bump(version, part)
        print(f"版本号: v{version}  ->  v{new_version}  ({part})")

    print("提交信息：")
    for line in message.splitlines():
        print("  " + line)

    if dry:
        # 关键：dry-run 必须只读。早期版本在这里之前就写了 VERSION / CHANGELOG，
        # 导致 --dry-run 后再正式执行会连跳两级版本号（1.1.3 → 1.2.0 → 1.3.0）。
        print("\n[dry-run] 未实际提交；VERSION / CHANGELOG / 标签均未改动")
        return 0

    if part:
        write_version(new_version)
        append_changelog(new_version, message, entries)
        run(["git", "add", "-A"], check=True)   # 把 VERSION / CHANGELOG 一并纳入

    p = subprocess.run(["git", "commit", "-F", "-"], cwd=ROOT,
                       input=message, text=True, encoding="utf-8",
                       capture_output=True)
    out = (p.stdout or "") + (p.stderr or "")
    print(out.strip())
    if p.returncode != 0:
        print("\n✗ 提交失败")
        return p.returncode

    if part:
        tag = f"v{new_version}"
        run(["git", "tag", "-a", tag, "-m", f"{tag}: "
             + message.strip().splitlines()[0]], check=True)
        print(f"✓ 已打标签 {tag}")

    p_sha = run(["git", "rev-parse", "--short", "HEAD"])
    sha = p_sha.stdout.strip() if p_sha.returncode == 0 else "(首次提交)"
    print("=" * 62)
    print(f"✓ 提交成功  {sha}   当前版本 v{read_version()}")

    if push:
        remotes = run(["git", "remote"]).stdout.strip()
        if not remotes:
            print("! 未配置远端仓库，跳过推送（git remote add origin <url>）")
        else:
            branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
            run(["git", "push", "origin", branch], check=True)
            if part:
                run(["git", "push", "origin", f"v{new_version}"], check=True)
            print(f"✓ 已推送到 origin/{branch}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
