"""Discord 內建表情的名字 → emoji（bot 送出的 :milk: 不會自己變成 🥛，要先換掉）。"""
from __future__ import annotations

from functools import lru_cache

# Discord 跟一般表情名字不一樣的幾個
DISCORD_NAMES = {
    "milk": "🥛", "salute": "🫡", "wilted_rose": "🥀", "thumbsdown": "👎", "-1": "👎", "slight_smile": "🙂",
    "slight_frown": "🙁", "hugging": "🤗", "rofl": "🤣", "face_palm": "🤦", "shrug": "🤷", "man_shrugging": "🤷‍♂️",
    "woman_shrugging": "🤷‍♀️", "speaking_head": "🗣️", "flag_tw": "🇹🇼",
}


@lru_cache(maxsize=512)
def unicode_emoji(name: str) -> str | None:
    if name in DISCORD_NAMES:
        return DISCORD_NAMES[name]
    try:
        import emoji
    except ImportError:
        return None
    for cand in (name, f"{name}_face", f"face_with_{name}", name.removesuffix("_face")):
        for lang in ("alias", "en"):
            got = emoji.emojize(f":{cand}:", language=lang)
            if got != f":{cand}:":
                return got
    return None
