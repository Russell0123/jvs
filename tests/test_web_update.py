"""deploy/web-update.sh 的整套流程：用假的 runuser／systemctl／curl 和本機 git repo 跑。"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="要 git 和 bash")


def sh(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def env(tmp_path):
    origin, src, app, stub = tmp_path / "origin.git", tmp_path / "src", tmp_path / "app", tmp_path / "bin"
    for d in (app / "data", app / ".venv" / "bin", stub):
        d.mkdir(parents=True)
    sh("git", "init", "-q", "--bare", "-b", "main", str(origin))
    sh("git", "clone", "-q", str(origin), str(src))
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        sh("git", "config", k, v, cwd=src)
    shutil.copytree(ROOT / "reminderbot", src / "reminderbot", ignore=shutil.ignore_patterns("__pycache__"))
    (src / "requirements.txt").write_text("")
    (src / "deploy").mkdir()
    # 假的 install.sh：記下裝了哪一版；health 由 app/healthy 這個檔決定
    (src / "deploy" / "install.sh").write_text(
        f'#!/bin/bash\ngit -C "{src}" rev-parse --short HEAD >> "{app}/installed"\n'
        f'if grep -q BROKEN "{src}/reminderbot/__init__.py"; then rm -f "{app}/healthy"; else touch "{app}/healthy"; fi\n')
    sh("git", "add", "-A", cwd=src)
    sh("git", "commit", "-qm", "v1", cwd=src)
    sh("git", "push", "-q", "origin", "HEAD:main", cwd=src)
    (app / "VERSION.json").write_text(json.dumps({"src": str(src)}))
    (app / ".venv" / "bin" / "python").write_text(f'#!/bin/bash\nexec "{sys.executable}" "$@"\n')
    (app / ".venv" / "bin" / "pip").write_text("#!/bin/bash\nexit 0\n")
    stubs = {
        "runuser": 'while [[ "$1" != "--" ]]; do shift; done; shift; exec "$@"',
        "systemctl": f'[[ "$1" == is-active ]] && [[ -f "{app}/healthy" ]]',
        "curl": f'[[ -f "{app}/healthy" ]]',
        "journalctl": "exit 0",
    }
    for name, body in stubs.items():
        (stub / name).write_text(f"#!/bin/bash\n{body}\n")
    for f in list(stub.iterdir()) + list((app / ".venv" / "bin").iterdir()):
        f.chmod(0o755)
    other = tmp_path / "other"                 # 另一台電腦推新版到 GitHub
    sh("git", "clone", "-q", str(origin), str(other))
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        sh("git", "config", k, v, cwd=other)
    return {"src": src, "app": app, "stub": stub, "other": other}


def push(env, msg, edit):
    p = env["other"] / "reminderbot" / "__init__.py"
    p.write_text(p.read_text() + edit)
    sh("git", "commit", "-qam", msg, cwd=env["other"])
    sh("git", "push", "-q", "origin", "HEAD:main", cwd=env["other"])


def run(env):
    (env["app"] / "data" / "update-request").write_text("")
    e = {**os.environ, "PATH": f"{env['stub']}:{os.environ['PATH']}", "JVS_APP_DIR": str(env["app"]),
         "JVS_HEALTH_WAIT": "0"}
    subprocess.run(["bash", str(ROOT / "deploy" / "web-update.sh")], env=e, check=False, timeout=120)
    assert not (env["app"] / "data" / "update-request").exists()       # 跑完刪掉觸發檔
    return json.loads((env["app"] / "data" / "update-status.json").read_text())


def head(env):
    return subprocess.run(["git", "-C", str(env["src"]), "log", "-1", "--format=%s"], capture_output=True,
                          text=True).stdout.strip()


def test_already_latest(env):
    st = run(env)
    assert st["state"] == "done" and st["ok"] and st["message"] == "已經是最新版"


def test_update_ok(env):
    push(env, "加新功能", "\nX = 1\n")
    st = run(env)
    assert st["ok"] and st["message"] == "更新完成：加新功能" and head(env) == "加新功能"
    assert (env["app"] / "installed").read_text().count("\n") == 1
    assert "從設定網頁更新" in (env["app"] / "data" / "update.log").read_text()


def test_check_fails_no_install(env):
    push(env, "語法錯誤", "\ndef (:\n")
    st = run(env)
    assert not st["ok"] and "檢查沒過" in st["message"]
    assert head(env) == "v1" and not (env["app"] / "installed").exists()   # 沒裝、原始碼退回


def test_broken_rolls_back(env):
    push(env, "壞掉的版本", "\nBROKEN = 1\n")
    st = run(env)
    assert not st["ok"] and "已退回" in st["message"] and head(env) == "v1"
    installed = (env["app"] / "installed").read_text().split()
    assert len(installed) == 2 and installed[0] != installed[1]            # 裝新版 → 再裝回舊版
