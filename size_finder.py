"""商品名からフィギュアのサイズ（全高など）をメーカー公式サイトで調べる。

一般の検索エンジンは機械からのアクセスをすぐ止めるため、メーカーのプライズ公式
サイトを直接検索する。
  - バンプレスト: bsp-prize.jp の検索ページ（?kw=）
  - セガ: segaplaza.jp が配っている全商品の一覧 JSON を商品名で照合
  - フリュー: furyuprize.com の検索ページ（?keyword=）
タイトーは公式サイトに直近の商品しか載らず、サイズも無いので対象外。

商品名の似ている別の商品を拾うこともあるので、画面には必ず出典のページも出して、
人が確かめられるようにする。
"""
import html
import re
import sqlite3
import time
import unicodedata

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

BSP_SEARCH_URL = "https://bsp-prize.jp/search/"
SEGA_DB_URL = "https://segaplaza.jp/jsons/search-db.json"
FURYU_SEARCH_URL = "https://furyuprize.com/search"

# 見つからなかった商品は1日おいてから調べ直す（新作は後から公式ページが出るため）
MISS_TTL = 24 * 3600
SEGA_DB_TTL = 24 * 3600
# 商品名がこれより似ていなければ、別の商品とみなす
MIN_SIMILARITY = 0.35
MAX_PAGES = 3

NUM = r"\d+(?:\.\d+)?"
DIMS = rf"{NUM}(?:\s*[×xX]\s*{NUM})*\s*(?:cm|mm)"
SIZE_PATTERNS = [
    # 「サイズ：全長約8×19cm」「「フリーレン」のサイズ：全長約6.5×6cm」
    re.compile(rf"サイズ\s*[:：]\s*([^\n:：]{{0,12}}?約?\s*{DIMS})"),
    # 「全高約16cm」「高さ約20cm」
    re.compile(rf"((?:全高|全長|高さ|全幅)\s*[:：]?\s*約?\s*{DIMS})"),
    # バンプレストの仕様欄「・約16cm」、フリューの「サイズ」欄の次の行「約23cm」
    re.compile(rf"(約\s*{DIMS})"),
]
TITLE_NOISE_RE = re.compile(r"[\"“”「」『』〜～~【】\[\]()（）\-‐ー―−!！?？・/／]")


def normalize(text):
    return re.sub(r"[ \t]+", " ", unicodedata.normalize("NFKC", text or "")).strip()


def extract_size(text):
    """本文からサイズ表記を1つ取り出す。無ければ空文字"""
    text = normalize(text)
    for pattern in SIZE_PATTERNS:
        m = pattern.search(text)
        if m:
            return re.sub(r"\s+", "", m.group(1))
    return ""


def page_text(raw_html):
    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw_html, flags=re.S | re.I)
    return html.unescape(re.sub(r"<[^>]+>", "\n", raw))


def page_title(raw_html):
    m = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.S | re.I)
    return normalize(html.unescape(m.group(1))) if m else ""


DATE_RE = re.compile(r"\d{4}年(\d{1,2}月)?(\d{1,2}日)?(\s*\(.\))?|\d+週|より順次|順次|登場予定|登場")


def link_name(fragment):
    """検索結果のリンクから商品名を取り出す。発売日などの表記は外す"""
    return normalize(DATE_RE.sub(" ", re.sub(r"\s+", " ", page_text(fragment))))


def tokens(title):
    """検索語にする単語。記号を外して空白で区切る"""
    return [t for t in normalize(TITLE_NOISE_RE.sub(" ", title)).split(" ") if t]


def bigrams(text):
    s = re.sub(r"\s+", "", normalize(TITLE_NOISE_RE.sub(" ", text)).lower())
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


def similarity(a, b):
    """2つの商品名の似ている度合い（0〜1）。文字の2文字組の重なりで測る"""
    x, y = bigrams(a), bigrams(b)
    return len(x & y) / len(x | y) if x and y else 0.0


class SizeFinder:
    def __init__(self, db_path, session=None, timeout=8.0):
        self.db_path = db_path
        self.session = session or requests.Session()
        self.timeout = timeout
        self._sega_db = None
        self._sega_db_at = 0.0
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sizes (
                    key TEXT PRIMARY KEY,
                    size TEXT NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    checked_at REAL NOT NULL
                )
            """)

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def find(self, key, title, brand=""):
        """{"size", "url", "title"} を返す。見つからなければ size は空文字"""
        key = key or normalize(title)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT size, url, title, checked_at FROM sizes WHERE key = ?", (key,)
            ).fetchone()
        if row and (row[0] or time.time() - row[3] < MISS_TTL):
            return {"size": row[0], "url": row[1], "title": row[2]}

        found = self._search(title, brand)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sizes (key, size, url, title, checked_at) VALUES (?, ?, ?, ?, ?)",
                (key, found["size"], found["url"], found["title"], time.time()),
            )
        return found

    def _search(self, title, brand):
        searchers = {
            "Banpresto": [self._candidates_bsp],
            "SEGA": [self._candidates_sega],
            "FuRyu": [self._candidates_furyu],
        }.get(brand) or [self._candidates_bsp, self._candidates_sega, self._candidates_furyu]

        candidates = []
        for search in searchers:
            try:
                candidates += search(title)
            except (requests.RequestException, ValueError) as e:
                print(f"サイズ検索スキップ ({search.__name__}): {e}")
        candidates = sorted(
            (c for c in candidates if c["score"] >= MIN_SIMILARITY),
            key=lambda c: c["score"], reverse=True,
        )
        for c in candidates[:MAX_PAGES]:
            size, name = self._size_from_page(c["url"])
            if size:
                return {"size": size, "url": c["url"], "title": name or c["title"]}
        # サイズは無くても、公式ページが見つかっていれば確認用にリンクだけ返す
        if candidates:
            return {"size": "", "url": candidates[0]["url"], "title": candidates[0]["title"]}
        return {"size": "", "url": "", "title": ""}

    def _get(self, url, **params):
        res = self.session.get(
            url, params=params or None, headers={"User-Agent": USER_AGENT}, timeout=self.timeout
        )
        res.raise_for_status()
        res.encoding = "utf-8"
        return res

    def _size_from_page(self, url):
        try:
            res = self._get(url)
        except requests.RequestException:
            return "", ""
        return extract_size(page_text(res.text)), page_title(res.text)

    def _search_by_words(self, title, fetch):
        """商品名全体で見つからなければ、先頭の単語（作品名など）を外しながら探し直す"""
        words = tokens(title)
        for start in range(len(words)):
            found = fetch(" ".join(words[start:]))
            if found:
                return [
                    {**c, "score": similarity(title, c["title"])} for c in found
                ]
        return []

    def _candidates_bsp(self, title):
        def fetch(keyword):
            text = self._get(BSP_SEARCH_URL, kw=keyword).text
            return [
                {"url": "https://bsp-prize.jp" + href, "title": link_name(name)}
                for href, name in re.findall(r'href="(/item/\d+/)"[^>]*>(.*?)</a>', text, re.S)
            ]
        return self._search_by_words(title, fetch)

    def _candidates_furyu(self, title):
        def fetch(keyword):
            text = self._get(FURYU_SEARCH_URL, keyword=keyword).text
            return [
                {"url": href, "title": link_name(name)}
                for href, name in re.findall(
                    r'href="(https://furyuprize\.com/item/\d+)"[^>]*>(.*?)</a>', text, re.S
                )
            ]
        return self._search_by_words(title, fetch)

    def _candidates_sega(self, title):
        if self._sega_db is None or time.time() - self._sega_db_at > SEGA_DB_TTL:
            self._sega_db = self._get(SEGA_DB_URL).json().get("prize", [])
            self._sega_db_at = time.time()
        scored = [
            {"url": f"https://segaplaza.jp/prize/{row[0]}/", "title": normalize(row[1]),
             "score": similarity(title, row[1])}
            for row in self._sega_db if len(row) > 1
        ]
        return sorted(scored, key=lambda c: c["score"], reverse=True)[:MAX_PAGES]
