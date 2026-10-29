"""使用者提供的 60 句測試集：每句應該被理解成什麼（起點 10/29（四）18:00，延後那段前先快轉 30 分鐘）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sim"))

from simbridge import Sim  # noqa: E402

START = 1793268000000   # 2026-10-29（四）18:00 台灣時間

# (句子, 判斷, 判斷細節裡要出現的字)
CASES = [
    ("明天早上7點叫我起床", "reminder", "10/30（五）07:00"),
    ("今天晚上9點提醒我洗衣服", "reminder", "10/29（四）21:00"),
    ("11月5日下午2點提醒我看牙醫", "reminder", "11/5（四）14:00"),
    ("週五晚上8點提醒我繳信用卡", "reminder", "10/30（五）20:00"),
    ("下週三早上10點提醒我開會", "reminder", "11/4（三）10:00"),
    ("今天18:30提醒我買晚餐", "reminder", "10/29（四）18:30"),
    ("明天下午4點提醒我交報告", "reminder", "10/30（五）16:00"),
    ("12月1日早上提醒我續訂Netflix", "reminder", "12/1（二）09:00"),
    ("晚上11點提醒我睡覺", "reminder", "10/29（四）23:00"),
    ("下午3點提醒我喝咖啡", "reminder", "10/30（五）15:00"),
    ("明天提醒我打電話給媽媽", "reminder", "10/30（五）09:00"),
    ("下週提醒我整理房間", "reminder", "11/2（一）09:00"),
    ("找個晚上提醒我看那部電影", "reminder", "太模糊：找個晚上"),
    ("有空的時候提醒我研究一下NAS", "reminder", "太模糊：有空的時候"),
    ("這幾天提醒我訂車票", "reminder", "10/31（六）09:00"),
    ("月底提醒我檢查帳戶餘額", "reminder", "照你說的「月底」"),
    ("發薪日提醒我存錢", "reminder", "發薪日"),
    ("下雨天提醒我把雨衣拿出來", "reminder", "下雨天"),
    ("出門前提醒我帶學生證", "reminder", "看事件：出門"),
    ("回到宿舍後提醒我讀書", "reminder", "看事件：回到宿舍"),
    ("一小時後提醒我休息", "reminder", "10/29（四）19:00"),
    ("30分鐘後提醒我去曬衣服", "reminder", "10/29（四）18:30"),
    ("三天後提醒我追蹤這件事", "reminder", "11/1（日）09:00"),
    ("十分鐘後提醒我關瓦斯", "reminder", "10/29（四）18:10"),
    ("半夜前提醒我寫日記", "reminder", "內容「寫日記」"),
    ("吃完飯後提醒我吃藥", "reminder", "看事件：吃完飯"),
    ("開完會半小時後提醒我寄信", "reminder", "內容「寄信」"),
    ("到家一小時後提醒我洗澡", "reminder", "內容「洗澡」"),
    ("兩週後提醒我檢查體重", "reminder", "11/12（四）09:00"),
    ("看完影片後提醒我整理筆記", "reminder", "內容「整理筆記」"),
    ("⏩30", None, None),
    ("這個提醒延到明天早上", "snooze", "10/30（五）09:00"),
    ("幫我晚兩個小時再提醒", "snooze", "兩個小時後"),
    ("先不要，三天後再提醒我", "snooze", "11/1（日）09:00"),
    ("這件事下週一再說", "snooze", "11/2（一）09:00"),
    ("睡醒再提醒我一次", "snooze", "看事件：睡醒"),
    ("一小時後如果我還沒完成再提醒我", "snooze", "一小時後"),
    ("每天早上7點提醒我量體重", "reminder", "每天"),
    ("每週日提醒我整理電腦桌面", "reminder", "每週日"),
    ("每兩小時提醒我喝水", "reminder", "每 2 小時"),
    ("每天晚上提醒我寫日記", "reminder", "每天"),
    ("每個月1號提醒我備份資料", "reminder", "每月 1 號"),
    ("每週三和週五提醒我運動", "reminder", "每週三、五"),
    ("提醒我三次，如果我沒理你就算了", "reminder", "共 3 次"),
    ("每15分鐘提醒一次，共提醒四次", "reminder", "共 4 次"),
    ("每天提醒我背單字，持續一週", "reminder", "持續一週"),
    ("提醒五次後自動取消", "reminder", "共 5 次"),
    ("每隔半小時問我有沒有出門，只問今天", "reminder", "只有今天"),
    ("取消今天所有提醒", "cancel_match", "今天・全部"),
    ("不用再提醒我喝水了", "cancel_match", "找「喝水」"),
    ("刪掉跟報告有關的提醒", "cancel_match", "找「報告」"),
    ("我突然想畫一隻穿西裝的柴犬", "wish", "想畫：一隻穿西裝的柴犬"),
    ("記一下創業點子：校園二手書交換平台", "note", "清單：靈感"),
    ("我想看跟時間旅行有關的電影", "wish", "想看：跟時間旅行有關的電影"),
    ("記住我想買一台機械鍵盤", "wish", "想買：一台機械鍵盤"),
    ("之後提醒我查一下NAS怎麼組", "reminder", "太模糊：之後"),
    ("先幫我收著這個想法", "note", "要記什麼"),
    ("等等，我剛剛那個買鍵盤的想法刪掉", "cancel_match", "記事・找「買鍵盤」"),
    ("我最近想學投資", "wish", "想學：投資"),
    ("有推薦我下班後可以看的影片嗎", "suggest", "清單：想看"),
    ("我想規劃一次日本旅行", "wish", "想做：規劃一次日本旅行"),
]


def test_sixty_sentences():
    sim = Sim(START)
    problems = []
    for text, kind, detail in CASES:
        if text.startswith("⏩"):
            sim.advance(int(text[1:]) * 60)
            continue
        st = json.loads(sim.say(1, text))
        it = next(m for m in reversed(st["messages"]) if m["author"] == 1)["intent"]
        if it["kind"] != kind or detail not in it["detail"]:
            problems.append(f"{text} → {it['label']}｜{it['detail']}")
    assert not problems, "\n".join(problems)


def test_snoozes_hit_the_latest_fired_reminder():
    sim = Sim(START)
    sim.say(1, "十分鐘後提醒我關瓦斯")
    sim.advance(15 * 60)
    st = json.loads(sim.say(1, "這個提醒延到明天早上"))
    reply = st["messages"][-1]["text"]
    assert "明天早上" in reply and "10/30（五）09:00" in reply
