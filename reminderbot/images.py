"""打招呼用的圖庫：圖片檔放在資料庫旁邊的 images/分組/ 資料夾，清單記在 store 的 meta。"""
from __future__ import annotations

import hashlib
import io
import re
import secrets
import time
from pathlib import Path

GROUPS = {"greet": "一般招呼", "rare": "稀有招呼", "stark": "Stark says", "pics": "圖庫"}   # 另外每條自訂規則有自己的 r{編號}
BUILTIN = Path(__file__).parent / "assets"
MAX_BYTES = 8 * 1024 * 1024                      # Discord 沒有 Nitro 的上傳上限是 10MB，留點空間
SIGNATURES = {b"\x89PNG": ".png", b"\xff\xd8\xff": ".jpg", b"GIF8": ".gif", b"RIFF": ".webp"}
TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def folder(db_path: str) -> Path:
    """賈維斯（bot.db）的圖在 data/images，其他 bot 各自在 data/images-<代號>。"""
    p = Path(db_path).resolve()
    return p.parent / ("images" if p.stem == "bot" else f"images-{p.stem}")


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


def save(root: Path, group: str, data: bytes, stem: str = "") -> str:
    """存一張圖，回新檔名；stem 有給就沿用那個檔名（換副檔名）。不是圖片或太大就丟 ValueError。"""
    if not valid_group(group):
        raise ValueError("沒有這個分組")
    if len(data) > MAX_BYTES:
        raise ValueError("超過 8MB")
    ext = kind(data)
    if not ext:
        raise ValueError("不是 png/jpg/gif/webp")
    name = (stem if re.fullmatch(r"\w+", stem) else f"{int(time.time())}{secrets.token_hex(3)}") + ext
    (root / group).mkdir(parents=True, exist_ok=True)
    (root / group / name).write_bytes(data)
    return name


def delete(root: Path, group: str, name: str):
    p = path_of(root, f"{group}/{name}")
    if p and group != "builtin":
        p.unlink()


def clear_group(root: Path, group: str):
    """刪掉這個分組的所有圖（資料夾留著）。"""
    if valid_group(group) and (root / group).is_dir():
        for p in (root / group).iterdir():
            if p.is_file():
                p.unlink()


def delete_group(root: Path, group: str):
    if valid_group(group) and (root / group).is_dir():
        for p in (root / group).iterdir():
            p.unlink()
        (root / group).rmdir()


def fingerprint(data: bytes) -> list | str:
    """圖片指紋：縮成 9×8 灰階比相鄰亮暗（dHash，64 位元）＋整張的平均顏色。
    轉存、縮放、壓縮、換格式的同一張圖指紋幾乎一樣。解不開的圖退回用內容雜湊（只認得一模一樣的檔案）。"""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            im.seek(0)
            rgb = im.convert("RGB")
            gray = rgb.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            px = [gray.getpixel((x, y)) for y in range(8) for x in range(9)]      # 各版 Pillow 都能用
            bits = 0
            for y in range(8):
                for x in range(8):
                    bits = (bits << 1) | (px[y * 9 + x] > px[y * 9 + x + 1])
            r, g, b = rgb.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
            return [bits, r, g, b]
    except Exception:
        return hashlib.sha1(data).hexdigest()


def same_picture(a, b) -> bool:
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    return bin(a[0] ^ b[0]).count("1") <= 6 and max(abs(x - y) for x, y in zip(a[1:], b[1:])) <= 24


def add_unique(core, root: Path, group: str, data: bytes) -> str:
    """存一張圖並加進分組清單；圖庫裡已經有同一張（指紋相同）就丟 ValueError("已經有了")。"""
    names = core.images(group)
    prints = {n: p for n, p in (core.store.get_meta(f"imgfp:{group}") or {}).items() if n in names}
    for n in names:                         # 以前上傳、還沒算過指紋的補算
        if n not in prints and (p := path_of(root, f"{group}/{n}")):
            prints[n] = fingerprint(p.read_bytes())
    mine = fingerprint(data)
    if any(same_picture(mine, p) for p in prints.values()):
        core.store.set_meta(f"imgfp:{group}", prints)
        raise ValueError("已經有了")
    name = save(root, group, data)
    prints[name] = mine
    core.store.set_meta(f"imgfp:{group}", prints)
    core.set_images(group, names + [name])
    return name
