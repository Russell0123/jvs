"""打招呼用的圖庫：圖片檔放在資料庫旁邊的 images/分組/ 資料夾，清單記在 store 的 meta。"""
from __future__ import annotations

import re
import secrets
import time
from pathlib import Path

GROUPS = {"greet": "一般招呼", "rare": "稀有招呼", "stark": "Stark says"}   # 另外每條自訂規則有自己的 r{編號}
BUILTIN = Path(__file__).parent / "assets"
MAX_BYTES = 8 * 1024 * 1024                      # Discord 沒有 Nitro 的上傳上限是 10MB，留點空間
SIGNATURES = {b"\x89PNG": ".png", b"\xff\xd8\xff": ".jpg", b"GIF8": ".gif", b"RIFF": ".webp"}
TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def folder(db_path: str) -> Path:
    return Path(db_path).resolve().parent / "images"


def kind(data: bytes) -> str:
    """看檔頭判斷是不是圖片，回副檔名；不是就回空字串。"""
    for sig, ext in SIGNATURES.items():
        if data.startswith(sig) and (ext != ".webp" or data[8:12] == b"WEBP"):
            return ext
    return ""


def valid_group(group: str) -> bool:
    return group in GROUPS or bool(re.fullmatch(r"r\d{1,6}", group))


def path_of(root: Path, ref: str) -> Path | None:
    """「分組/檔名」→ 實際路徑；不合法或不存在回 None。builtin/ 是程式內附的圖。"""
    group, _, name = ref.partition("/")
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    if group == "builtin":
        p = BUILTIN / name
        return p if p.is_file() else None
    if not valid_group(group):
        return None
    p = root / group / name
    return p if p.is_file() else None


def save(root: Path, group: str, data: bytes) -> str:
    """存一張圖，回新檔名；不是圖片或太大就丟 ValueError。"""
    if not valid_group(group):
        raise ValueError("沒有這個分組")
    if len(data) > MAX_BYTES:
        raise ValueError("超過 8MB")
    ext = kind(data)
    if not ext:
        raise ValueError("不是 png/jpg/gif/webp")
    name = f"{int(time.time())}{secrets.token_hex(3)}{ext}"
    (root / group).mkdir(parents=True, exist_ok=True)
    (root / group / name).write_bytes(data)
    return name


def delete(root: Path, group: str, name: str):
    p = path_of(root, f"{group}/{name}")
    if p and group != "builtin":
        p.unlink()


def delete_group(root: Path, group: str):
    if valid_group(group) and (root / group).is_dir():
        for p in (root / group).iterdir():
            p.unlink()
        (root / group).rmdir()
