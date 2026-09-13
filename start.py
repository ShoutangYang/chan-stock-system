#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股缠论交易系统 - 一键启动脚本

用法：
    python start.py                 # 启动并打开浏览器
    python start.py --restart       # 先关掉占用端口的旧进程，再启动（推荐用于更新代码后）
    python start.py --no-browser    # 只启动服务，不打开浏览器
    python start.py --port 9000     # 指定端口

关闭：在窗口按 Ctrl+C
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"

REQUIRED = ["fastapi", "uvicorn", "pandas", "numpy", "yfinance", "curl_cffi", "apscheduler"]


# ---------------------------------------------------------------- 依赖
def check_deps() -> bool:
    missing = []
    for m in REQUIRED:
        try:
            __import__(m)
        except ImportError:
            missing.append(m)
    if not missing:
        return True

    print(f"[提示] 缺少依赖: {', '.join(missing)}")
    if not sys.stdin.isatty():
        print("[提示] 非交互环境，尝试自动安装…")
        ans = "y"
    else:
        ans = input("是否现在安装？(y/n) ").strip().lower()

    if ans != "y":
        print("[错误] 依赖缺失，无法启动。请执行: pip install -r requirements.txt")
        return False

    cmd = [sys.executable, "-m", "pip", "install", *missing,
           "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"]
    return subprocess.call(cmd) == 0


# ---------------------------------------------------------------- 端口
def port_in_use(port: int, host: str = DEFAULT_HOST) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex((host, port)) == 0


def find_pid_on_port(port: int) -> int | None:
    """Windows 下查找占用指定端口的进程 PID。"""
    if sys.platform != "win32":
        return None
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, encoding="gbk", errors="ignore", timeout=10,
        ).stdout
        for line in out.splitlines():
            if f":{port}" in line and "LISTENING" in line.upper():
                parts = line.split()
                if parts:
                    try:
                        return int(parts[-1])
                    except ValueError:
                        continue
    except Exception:
        pass
    return None


def kill_port(port: int) -> bool:
    pid = find_pid_on_port(port)
    if not pid:
        return False
    print(f"[提示] 端口 {port} 被进程 {pid} 占用，正在结束旧进程…")
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True, timeout=10)
        time.sleep(1.5)
        return not port_in_use(port)
    except Exception as e:
        print(f"[错误] 结束进程失败: {e}")
        return False


def wait_port(port: int, host: str, timeout: int = 30) -> bool:
    for _ in range(timeout * 2):
        if port_in_use(port, host):
            return True
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="启动美股缠论交易系统")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="端口，默认 8765")
    ap.add_argument("--host", type=str, default=DEFAULT_HOST, help="监听地址，默认 127.0.0.1")
    ap.add_argument("--restart", action="store_true", help="先结束占用端口的旧进程再启动")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--log-level", default="info", help="uvicorn 日志级别")
    args = ap.parse_args()

    os.chdir(BASE_DIR)
    os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)

    if not check_deps():
        return 1

    if args.restart and port_in_use(args.port, args.host):
        if not kill_port(args.port):
            print("[错误] 无法释放端口，请手动关闭占用程序后重试。")
            return 1

    if port_in_use(args.port, args.host):
        print(f"[提示] 端口 {args.port} 已有服务在运行，直接复用。")
        print(f"       如需加载最新代码，请用: python start.py --restart")
    else:
        print(f"正在启动服务 http://{args.host}:{args.port} ...")
        log_path = os.path.join(BASE_DIR, "logs", "server.log")
        log = open(log_path, "a", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", args.host, "--port", str(args.port),
             "--log-level", args.log_level],
            stdout=log, stderr=subprocess.STDOUT, cwd=BASE_DIR,
        )
        if not wait_port(args.port, args.host):
            print(f"[错误] 启动超时，请查看日志: {log_path}")
            return 1
        print(f"服务已启动 (PID {proc.pid})")

    url = f"http://{args.host}:{args.port}"
    print()
    print("=" * 52)
    print(f"  系统已就绪: {url}")
    print(f"  数据目录:   {os.path.join(BASE_DIR, 'data')}")
    print(f"  日志:       {os.path.join(BASE_DIR, 'logs', 'server.log')}")
    print("  关闭服务:   在本窗口按 Ctrl+C")
    print("=" * 52)
    print()
    print("[提示] 若浏览器打不开页面，多半是系统代理拦截了本地地址，")
    print("       请在浏览器代理设置中把 127.0.0.1 加入例外，或暂时关闭代理。")
    print()

    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n已退出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
