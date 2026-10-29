"""自訂規則：誰說的、有沒有叫它、有沒有交代事情、包含哪些詞 → 回一句、附一張圖。

規則存在 store 的 meta「rules」（照順序比對，第一條符合的生效），圖片放在圖庫的「r{規則編號}」分組。
"""
from __future__ import annotations

import re

WHO = {"any": "任何人", "owner": "只有我", "others": "我以外的人", "list": "指定的人"}
CALL = {"yes": "要叫它", "no": "不用叫也會"}
TASK = {"any": "都可以", "yes": "有交代事情", "no": "沒交代事情"}
MODE = {"add": "原本的照做，另外再回", "replace": "取代原本的回覆"}
BLANK = {"id": 0, "name": "", "on": True, "who": "any", "users": [], "call": "yes", "task": "any",
         "words": "", "not_words": "", "mode": "add", "replies": "", "cooldown": 0}


def split_words(raw: str) -> list[str]:
    return [w.strip().lower() for w in re.split(r"[/、,，\n]+", raw or "") if w.strip()]


def load(store) -> list[dict]:
    return [{**BLANK, **r} for r in (store.get_meta("rules") or [])]


def save(store, rules: list[dict]):
    store.set_meta("rules", rules)


def from_form(form: dict, rid: int) -> tuple[dict, list[str]]:
    """網頁表單 → 規則；有問題的欄位回在 errors。"""
    def pick(key, choices):
        v = str(form.get(key, BLANK[key]))
        return v if v in choices else BLANK[key]
    errors = []
    users = [int(u) for u in re.findall(r"\d{5,}", str(form.get("users", "")))]
    try:
        cooldown = max(0, int(str(form.get("cooldown", "0")).strip() or 0))
    except ValueError:
        cooldown, _ = 0, errors.append("冷卻要填分鐘數")
    rule = {
        "id": rid, "name": str(form.get("name", "")).strip()[:40] or f"規則 {rid}",
        "on": str(form.get("on", "")).lower() in ("on", "true", "1"),
        "who": pick("who", WHO), "users": users, "call": pick("call", CALL), "task": pick("task", TASK),
        "words": str(form.get("words", "")).strip(), "not_words": str(form.get("not_words", "")).strip(),
        "mode": pick("mode", MODE), "replies": str(form.get("replies", "")).replace("\r\n", "\n").strip(),
        "cooldown": cooldown,
    }
    if rule["who"] == "list" and not users:
        errors.append("「指定的人」要填 Discord 使用者 ID")
    if rule["call"] == "no" and not split_words(rule["words"]):
        errors.append("不用叫它的規則至少要有一個關鍵字，不然每句話都會回")
    return rule, errors


def matches(rule: dict, *, author_id: int, owner: bool, called: bool, has_task: bool, text: str) -> bool:
    if not rule["on"]:
        return False
    who = rule["who"]
    if (who == "owner" and not owner) or (who == "others" and owner) or \
            (who == "list" and author_id not in rule["users"]):
        return False
    if rule["call"] == "yes" and not called:
        return False
    if (rule["task"] == "yes" and not has_task) or (rule["task"] == "no" and has_task):
        return False
    t = text.lower()
    words = split_words(rule["words"])
    if words and not any(w in t for w in words):
        return False
    return not any(w in t for w in split_words(rule["not_words"]))


def summary(rule: dict) -> str:
    """一行看懂這條規則：「我以外的人・不用叫・包含 早安/晚安 → 另外回」。"""
    who = WHO[rule["who"]] if rule["who"] != "list" else f"{len(rule['users'])} 個指定的人"
    parts = [who, CALL[rule["call"]]]
    if rule["task"] != "any":
        parts.append(TASK[rule["task"]])
    if rule["words"]:
        parts.append("包含 " + "/".join(split_words(rule["words"])))
    if rule["not_words"]:
        parts.append("不含 " + "/".join(split_words(rule["not_words"])))
    return "・".join(parts) + (" → 取代" if rule["mode"] == "replace" else " → 另外回")
