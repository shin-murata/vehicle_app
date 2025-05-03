#!/usr/bin/env python3
import os, time, subprocess

WATCH_NAME = "-d"                           # 監視したいファイル名
observed = set(os.listdir('.'))             # 最初の状態を記憶

print("» Watching for file:", WATCH_NAME)
while True:
    now = set(os.listdir('.'))
    # もし新しく '-d' ができていたら
    if WATCH_NAME in now - observed:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] Detected '{WATCH_NAME}' creation")
        print("=== Process list ===")
        # 現在動いている全プロセスを表示
        subprocess.run(["ps", "aux"])
        break
    observed = now
    time.sleep(1)  # 1秒ごとにチェック
