"""自訂規則：誰說的、有沒有叫它、有沒有交代事情、包含哪些詞 → 回一句、附一張圖。

規則存在 store 的 meta「rules」（照順序比對，第一條符合的生效），圖片放在圖庫的「r{規則編號}」分組。
"""
from __future__ import annotations

import re

WHO = {"any": "任何人", "owner": "只有我", "others": "我以外的人", "list": "指定的人"}
CALL = {"yes": "要叫它", "no": "不用叫也會"}
TASK = {"any": "都可以", "yes": "有交代事情", "no": "沒交代事情"}
MODE = {"add": "原本的照做，另外再回", "replace": "取代原本的回覆"}
BOTS = {"others": "會觸發，但不會被這條自己回的話觸發", "loop": "可以循環觸發：自己回的話也會再觸發這條",
        "no": "不會觸發：只有人講的才算"}
SEND = {"one": "隨機挑一句、一張", "all": "全部依序送出：一句／一張一則訊息"}
BLANK = {"id": 0, "name": "", "on": True, "who": "any", "users": [], "call": "yes", "task": "any",
         "words": "", "not_words": "", "mode": "add", "replies": "", "cooldown": 0, "send": "one", "bots": "others"}


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
        "cooldown": cooldown, "send": pick("send", SEND), "bots": pick("bots", BOTS),
    }
    if rule["who"] == "list" and not users:
        errors.append("「指定的人」要填 Discord 使用者 ID")
    if rule["call"] == "no" and not split_words(rule["words"]):
        errors.append("不用叫它的規則至少要有一個關鍵字，不然每句話都會回")
    return rule, errors


def matches(rule: dict, *, author_id: int, owner: bool, called: bool, has_task: bool, text: str,
            from_bot: bool = False, skip: tuple = ()) -> bool:
    if not rule["on"]:
        return False
    bots = rule.get("bots", "others")
    if from_bot and (bots == "no" or (bots == "others" and rule["id"] in skip)):
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
    return "・".join(parts) + (" → 取代" if rule["mode"] == "replace" else " → 另外回") + \
        ("・依序全部送" if rule.get("send") == "all" else "") + \
        ("・可循環" if rule.get("bots") == "loop" else "・機器人不觸發" if rule.get("bots") == "no" else "")


# 每個 bot 預設帶的規則（第一次啟動時加進去，之後在網頁改；刪掉也不會再加回來）
SEED_RULES: dict[str, list[dict]] = {
    "jbao": [{"name": "可愛三連拍", "call": "yes", "words": "可愛三連拍/三連拍", "mode": "replace", "send": "all",
              "replies": ""}],
}


def seed_rules(store, key: str):
    done = set(store.get_meta("_seed_rules") or [])
    todo = [r for r in SEED_RULES.get(key, []) if r["name"] not in done]
    if not todo:
        return
    all_rules = load(store)
    rid = max([int(store.get_meta("rule_seq") or 0)] + [r["id"] for r in all_rules])
    for r in todo:
        rid += 1
        rule, _ = from_form({**BLANK, **r, "on": "on"}, rid)
        all_rules.append(rule)
        done.add(r["name"])
    save(store, all_rules)
    store.set_meta("rule_seq", rid)
    store.set_meta("_seed_rules", sorted(done))
