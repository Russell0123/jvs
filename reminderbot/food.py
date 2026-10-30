"""「我要吃什麼」：一題一題問，再從食物庫挑前 5 名。問題樹和食物庫都是純文字，在設定網頁的「吃什麼」頁改。

食物庫一行一項：「編號. 品項　路線｜價位｜感覺｜形式｜冷熱｜畫面｜爽法｜吃法」，每個字是一個標記，— 是沒有。
問題樹一行一題：「題目｜欄位｜代碼=選項／代碼=選項／不知道」，題目裡有「複選」就是複選題；
第一題是分路線的那題，之後用【代碼】分段，寫各路線接著問什麼。

計分：只挑該路線的；預算是硬條件，價位高於所選的排除、剛好落在所選的 +1；
其他題選到的標記每符合一個 +1；「不知道」不計分；取前 5 名，同分隨機。
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

FIELDS = ["路線", "價位", "感覺", "形式", "冷熱", "畫面", "爽法", "吃法"]
ROUTE, PRICE = "路線", "價位"
NONE_MARKS = {"—", "-", "－", "─", ""}

DEFAULT_QUESTIONS = """這一餐是？｜路線｜正=正餐／爽=吃爽的／隨=隨便吃

【正】正餐線
預算？｜價位｜1=100內／2=100–200／3=200–400／4=400以上
今天想要什麼感覺？可複選｜感覺｜重=重口味／清=清爽／油=油炸／健=健康／肉=多肉／菜=多菜／不知道
想吃什麼形式？可複選｜形式｜飯=飯／麵=麵／餅=餅包捲夾：水餃、漢堡、taco、蔥抓餅／無=不用主食：鍋物、炸物、沙拉／不知道
想吃熱的還是涼的？｜冷熱｜湯=熱湯／熱=熱的乾的／涼=涼的／不知道

【爽】吃爽線
預算？｜價位｜1=100內／2=100–200／3=200–400／4=400以上
想要哪種爽？可複選｜爽法｜肉=大口吃肉／炸=炸物／湯=熱湯／飽=吃到飽／辣=辣／豪=豪華感／不知道
腦中比較接近哪個畫面？可複選｜畫面｜日=日式：拉麵、丼飯、壽司／韓=韓式：炸雞、烤肉、部隊鍋／西=西式：漢堡、牛排、披薩、taco／南=東南亞：打拋、河粉、咖哩／台=台式：夜市、熱炒味、滷肉／不知道
想吃什麼形式？可複選｜形式｜飯=飯／麵=麵／餅=餅包捲夾：水餃、漢堡、taco、蔥抓餅／無=不用主食：鍋物、炸物、沙拉／不知道

【隨】隨便吃線
預算？｜價位｜1=100內／2=100–200／3=200–400／4=400以上
今天想要什麼感覺？可複選｜感覺｜重=重口味／清=清爽／油=油炸／健=健康／肉=多肉／菜=多菜／甜=甜的／不知道
怎麼吃？｜吃法｜走=邊走邊吃／帶=帶回去吃／店=在店裡快速吃完／不知道
想吃熱的還是涼的？｜冷熱｜湯=熱湯／熱=熱的乾的／涼=涼的／不知道"""

DEFAULT_LIBRARY = """【台式飯類】
1. 便當　正隨｜1｜肉菜｜飯｜熱｜台｜—｜帶
2. 自助餐　正｜1｜健菜｜飯｜熱｜台｜—｜—
3. 素食自助餐　正｜1｜清健菜｜飯｜熱｜台｜—｜—
4. 滷肉飯　正隨｜1｜重肉｜飯｜熱｜台｜—｜店帶
5. 雞肉飯　正隨｜1｜清肉｜飯｜熱｜台｜—｜店
6. 鴨肉飯　正隨｜1｜肉｜飯｜熱｜台｜—｜店
7. 燒臘飯　正｜1｜重肉｜飯｜熱｜台｜—｜—
8. 豬腳飯　正爽｜1｜重肉｜飯｜熱｜台｜肉｜—
9. 炒飯　正隨｜1｜重油｜飯｜熱｜台｜—｜帶店
10. 燴飯　正｜1｜重｜飯｜熱｜台｜—｜—
11. 筒仔米糕　隨｜1｜重肉｜飯｜熱｜台｜—｜店
12. 廣東粥　正隨｜1｜清｜飯｜湯｜台｜—｜店帶
13. 飯糰　隨｜1｜油肉｜飯｜熱｜台｜—｜走
14. 簡餐　正｜2｜肉菜｜飯｜熱｜台｜—｜—
15. 鐵板燒　正爽｜2｜重肉菜｜飯｜熱｜台｜肉豪｜—
16. 健康餐盒　正｜2｜清健肉菜｜飯｜熱｜—｜—｜—

【日韓飯類】
17. 牛丼　正隨｜1｜重肉｜飯｜熱｜日｜—｜店帶
18. 豬排丼　正爽｜2｜重油肉｜飯｜熱｜日｜炸肉｜—
19. 炸豬排定食　正爽｜3｜油肉｜飯｜熱｜日｜炸肉豪｜—
20. 日式咖哩飯　正｜2｜重肉｜飯｜熱｜日｜—｜—
21. 蛋包飯　正｜2｜重｜飯｜熱｜日｜—｜—
22. 壽司　正爽｜3｜清｜飯｜涼｜日｜豪｜—
23. 迴轉壽司　正爽｜2｜清｜飯｜涼｜日｜飽｜—
24. 生魚片丼　正爽｜3｜清健｜飯｜涼｜日｜豪｜—
25. 鰻魚飯　爽｜4｜重肉｜飯｜熱｜日｜豪｜—
26. 韓式拌飯　正｜2｜重菜｜飯｜熱｜韓｜—｜—
27. 韓式飯捲　隨｜1｜清菜｜飯｜涼｜韓｜—｜走帶

【其他飯類】
28. 夏威夷飯　正｜2｜清健｜飯｜涼｜西｜—｜—
29. 美墨碗　正｜2｜健肉菜｜飯｜涼｜西｜—｜—
30. 海南雞飯　正｜2｜清肉｜飯｜熱｜南｜—｜—
31. 打拋豬飯　正爽｜2｜重肉｜飯｜熱｜南｜辣｜—
32. 綠咖哩飯　正｜2｜重｜飯｜熱｜南｜—｜—
33. 焗烤飯　正｜2｜重油｜飯｜熱｜西｜—｜—
34. 義式燉飯　正｜3｜重｜飯｜熱｜西｜—｜—
35. 酸菜魚　正爽｜3｜重肉｜飯｜湯｜—｜辣湯｜—
36. 港式茶餐廳　正｜2｜重油｜飯｜熱｜—｜—｜—

【台式麵類】
37. 牛肉麵　正爽｜2｜重肉｜麵｜湯｜台｜肉湯｜—
38. 陽春麵　正隨｜1｜清｜麵｜湯｜台｜—｜店
39. 烏醋拌麵　正隨｜1｜重｜麵｜熱｜台｜—｜店
40. 炸醬麵　正隨｜1｜重肉｜麵｜熱｜台｜—｜店
41. 涼麵　正隨｜1｜清菜｜麵｜涼｜台｜—｜帶走
42. 鍋燒意麵　正隨｜1｜清｜麵｜湯｜台｜—｜店
43. 米粉湯　隨｜1｜清｜麵｜湯｜台｜—｜店
44. 肉羹麵　正隨｜1｜重肉｜麵｜湯｜台｜—｜店
45. 蚵仔麵線　隨｜1｜重｜麵｜湯｜台｜—｜走店
46. 刀削麵　正｜1｜重｜麵｜湯｜台｜—｜—
47. 紅油抄手　正隨｜1｜重肉｜餅｜湯｜台｜—｜店
48. 酸辣粉　隨｜1｜重｜麵｜湯｜台｜—｜店
49. 麻醬麵　正隨｜1｜重｜麵｜熱｜台｜—｜店

【異國麵類】
50. 拉麵　正爽｜3｜重油肉｜麵｜湯｜日｜湯肉｜—
51. 沾麵　正爽｜3｜重肉｜麵｜熱｜日｜肉｜—
52. 烏龍麵　正｜2｜清｜麵｜湯｜日｜—｜—
53. 蕎麥涼麵　正｜2｜清健｜麵｜涼｜日｜—｜—
54. 義大利麵　正｜2｜重｜麵｜熱｜西｜—｜—
55. 越南河粉　正｜2｜清肉｜麵｜湯｜南｜—｜—
56. 泰式炒河粉　正｜2｜重｜麵｜熱｜南｜—｜—
57. 韓式冷麵　正｜2｜清｜麵｜涼｜韓｜—｜—
58. 部隊鍋　爽｜2｜重肉｜麵｜湯｜韓｜辣湯｜—
59. 平價牛排　正爽｜2｜重肉｜麵｜熱｜西｜肉｜—

【餅包捲夾】
60. 美式漢堡店　正爽｜3｜重油肉｜餅｜熱｜西｜肉豪｜—
61. 速食店　隨爽｜2｜重油肉｜餅｜熱｜西｜炸｜帶店
62. 潛艇堡　正隨｜2｜清健菜｜餅｜涼｜西｜—｜帶
63. 早午餐　正｜3｜油肉｜餅｜熱｜西｜—｜—
64. 早餐店　隨｜1｜油｜餅｜熱｜台｜—｜帶走
65. Taco　隨｜2｜重肉｜餅｜熱｜西｜—｜走店
66. 墨西哥捲餅　正隨｜2｜肉菜｜餅｜熱｜西｜—｜帶走
67. 披薩　爽｜3｜重油｜餅｜熱｜西｜飽｜—
68. 沙威瑪　隨｜1｜重肉｜餅｜熱｜—｜—｜走
69. 貝果　隨｜1｜清｜餅｜涼｜西｜—｜走帶
70. 蔥抓餅　隨｜1｜油｜餅｜熱｜台｜—｜走
71. 潤餅　正隨｜1｜清健菜｜餅｜涼｜台｜—｜走帶
72. 刈包　隨｜1｜重肉｜餅｜熱｜台｜—｜走
73. 水煎包　隨｜1｜油｜餅｜熱｜台｜—｜走
74. 水餃　正隨｜1｜肉｜餅｜熱｜台｜—｜店帶
75. 鍋貼　正隨｜1｜油肉｜餅｜熱｜台｜—｜帶
76. 小籠包　正爽｜2｜肉｜餅｜熱｜台｜豪｜—
77. 印度咖哩烤餅　正｜3｜重｜餅｜熱｜南｜—｜—

【炸物小吃】
78. 鹽酥雞　隨爽｜1｜重油｜無｜熱｜台｜炸｜帶
79. 雞排　隨爽｜1｜油肉｜無｜熱｜台｜炸肉｜走
80. 韓式炸雞　爽｜3｜重油肉｜無｜熱｜韓｜炸肉｜—
81. 臭豆腐　隨｜1｜重油｜無｜熱｜台｜—｜店帶
82. 蚵仔煎　隨｜1｜重油｜無｜熱｜台｜—｜店
83. 肉圓　隨｜1｜重｜餅｜熱｜台｜—｜店
84. 大腸包小腸　隨｜1｜重肉｜餅｜熱｜台｜—｜走
85. 滷味　正隨｜1｜重菜｜無｜熱｜台｜—｜帶
86. 麻辣燙　正隨｜2｜重菜｜無｜湯｜—｜—｜帶店
87. 關東煮　隨｜1｜清｜無｜湯｜日｜—｜帶店
88. 青菜豆腐湯　隨｜1｜清健菜｜無｜湯｜台｜—｜店
89. 沙拉　正隨｜2｜清健菜｜無｜涼｜西｜—｜帶

【鍋物燒肉】
90. 小火鍋　正｜2｜肉菜｜無｜湯｜台｜—｜—
91. 石頭火鍋　正爽｜2｜重肉菜｜無｜湯｜台｜湯｜—
92. 麻辣鍋　爽｜3｜重肉｜無｜湯｜台｜辣湯｜—
93. 火鍋吃到飽　爽｜3｜肉菜｜無｜湯｜台｜飽湯肉｜—
94. 薑母鴨　爽｜3｜重肉｜無｜湯｜台｜湯｜—
95. 一人壽喜燒　爽｜3｜肉｜無｜湯｜日｜肉豪｜—
96. 一人燒肉　爽｜3｜重肉｜無｜熱｜日｜肉｜—
97. 燒肉吃到飽　爽｜4｜重肉｜無｜熱｜日韓｜肉飽｜—
98. 牛排館　爽｜4｜肉｜無｜熱｜西｜肉豪｜—
99. Buffet吃到飽　爽｜4｜—｜無｜熱｜西｜飽豪｜—

【甜點飲料】
100. 手搖飲　隨｜1｜甜｜無｜涼｜台｜—｜走帶
101. 豆花　隨｜1｜甜清健｜無｜涼熱｜台｜—｜店帶
102. 雪花冰　隨爽｜2｜甜｜無｜涼｜台｜豪｜店
103. 仙草凍　隨｜1｜甜清｜無｜涼｜台｜—｜帶
104. 燒仙草　隨｜1｜甜｜無｜熱｜台｜—｜帶
105. 芒果冰　隨爽｜2｜甜｜無｜涼｜台｜豪｜店
106. 紅豆湯圓　隨｜1｜甜｜無｜湯｜台｜—｜店
107. 愛玉　隨｜1｜甜清｜無｜涼｜台｜—｜走
108. 鬆餅　隨爽｜2｜甜油｜餅｜熱｜西｜豪｜店
109. 雞蛋糕、車輪餅　隨｜1｜甜｜餅｜熱｜台｜—｜走
110. 冰淇淋　隨｜1｜甜｜無｜涼｜西｜—｜走
111. 地瓜球　隨｜1｜甜油｜無｜熱｜台｜—｜走
112. 烤地瓜　隨｜1｜甜健｜無｜熱｜台｜—｜走"""


@dataclass
class Option:
    code: str | None            # None = 不知道（不計分）
    label: str


@dataclass
class Question:
    text: str
    field: str
    options: list[Option]
    multi: bool = False


@dataclass
class Tree:
    first: Question | None = None
    branches: dict[str, list[Question]] = field(default_factory=dict)

    def questions(self, route: str | None) -> list[Question]:
        return [self.first] + self.branches.get(route or "", []) if self.first else []

    def length(self, route: str | None) -> int:
        if route:
            return len(self.questions(route))
        return 1 + max((len(b) for b in self.branches.values()), default=0)


@dataclass
class Food:
    name: str
    tags: dict[str, set[str]]


def _marks(raw: str) -> set[str]:
    raw = raw.strip()
    return set() if raw in NONE_MARKS else {c for c in raw if c not in NONE_MARKS and not c.isspace()}


def parse_library(raw: str) -> tuple[list[Food], list[str]]:
    foods, errors, fields = [], [], FIELDS
    for n, line in enumerate(str(raw).splitlines(), 1):
        line = line.strip()
        if not line or line.startswith(("#", "【", "[")):
            continue
        if line.startswith(("欄位：", "欄位:")):
            fields = [f.strip() for f in re.split(r"[｜|]", line[3:]) if f.strip()]
            continue
        parts = [p.strip() for p in re.split(r"[｜|]", line)]
        head = re.sub(r"^\d+\s*[.．、]\s*", "", parts[0])
        m = re.match(r"^(.*\S)[\s　]+(\S+)$", head)
        if not m or len(parts) != len(fields):
            errors.append(f"第 {n} 行看不懂：{line[:30]}")
            continue
        tags = {f: _marks(v) for f, v in zip(fields, [m.group(2)] + parts[1:])}
        if PRICE in tags and (len(tags[PRICE]) != 1 or not next(iter(tags[PRICE])).isdigit()):
            errors.append(f"第 {n} 行價位要是一個數字：{line[:30]}")
            continue
        foods.append(Food(m.group(1).strip(), tags))
    return foods, errors


def parse_questions(raw: str) -> tuple[Tree, list[str]]:
    tree, errors, route = Tree(), [], None
    for n, line in enumerate(str(raw).splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if m := re.match(r"^[【\[]\s*([^】\]]+?)\s*[】\]]", line):
            route = m.group(1)
            tree.branches.setdefault(route, [])
            continue
        parts = [p.strip() for p in re.split(r"[｜|]", line)]
        if len(parts) != 3 or not parts[0] or not parts[1]:
            errors.append(f"第 {n} 行要寫成「題目｜欄位｜選項／選項」：{line[:30]}")
            continue
        opts = []
        for o in re.split(r"[／/]", parts[2]):
            o = o.strip()
            if not o:
                continue
            code, eq, label = o.partition("=")
            opts.append(Option(code.strip(), label.strip() or code.strip()) if eq else Option(None, o))
        if not opts:
            errors.append(f"第 {n} 行沒有選項")
            continue
        q = Question(parts[0], parts[1], opts, "複選" in parts[0])
        if route is None:
            if tree.first is not None:
                errors.append(f"第 {n} 行：分路線的題目只能有一題，其他題要放在【代碼】下面")
                continue
            tree.first = q
        else:
            tree.branches[route].append(q)
    if tree.first is None:
        errors.append("第一題要是分路線的題目，例如「這一餐是？｜路線｜正=正餐／爽=吃爽的」")
    else:
        for o in tree.first.options:
            if o.code and o.code not in tree.branches:
                errors.append(f"路線「{o.label}」下面沒有題目，要加一段【{o.code}】")
    return tree, errors


def recommend(foods: list[Food], tree: Tree, answers: list[list[str]], seed: int, top: int = 5) -> list[str]:
    """answers：每題選到的代碼（不知道 = 空的）。回前幾名的品項名稱。"""
    qs = tree.questions(answers[0][0] if answers and answers[0] else None)
    scored = []
    for f in foods:
        score, ok = 0, True
        for q, picked in zip(qs, answers):
            tags = f.tags.get(q.field, set())
            if not picked:
                continue
            if q.field == ROUTE:
                ok = ok and bool(tags & set(picked))
            elif q.field == PRICE:
                budget, price = max(int(p) for p in picked), int(next(iter(tags), "0"))
                ok = ok and price <= budget
                score += price == budget
            else:
                score += len(tags & set(picked))
        if ok:
            scored.append((score, f.name))
    rng = random.Random(seed)
    rng.shuffle(scored)
    scored.sort(key=lambda x: -x[0])
    return [name for _, name in scored[:top]]
