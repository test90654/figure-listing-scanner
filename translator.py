"""商品名を出品用の言語へ翻訳する。

作品名やキャラ名は機械翻訳だと直訳されて（例: 葬送のフリーレン → Funeral Freelen）
検索に引っかからないため、用語集で先に公式名へ置き換えてから翻訳する。
"""
import json
import re
import sqlite3
import threading
import unicodedata

import requests

TARGETS = {"en": "English", "zh-TW": "繁體中文", "th": "ไทย"}

# DeepL は対応言語のみ。それ以外の言語は Google 翻訳を使う
DEEPL_TARGETS = {"en": "EN-US", "zh-TW": "ZH-HANT"}
GOOGLE_URL = "https://translate.googleapis.com/translate_a/single"

# ひらがな・カタカナ・漢字が残っていれば、まだ翻訳が必要
JAPANESE_RE = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]")

LISTING_SUFFIX = {
    "en": ["Figure", "Authentic", "Japan", "Prize", "New"],
    "zh-TW": ["公仔", "日本正版", "景品", "全新"],
    "th": ["Figure", "ของแท้", "Japan", "Prize", "New"],
}
TITLE_MAX = 120
TITLE_NOISE_RE = re.compile(r"[\"“”「」『』〜～~]")

DESCRIPTION = {
    "en": (
        "{title}\n\n"
        "- 100% authentic Japanese prize figure{brand_line}\n"
        "- Brand new, unopened\n"
        "- JAN: {jan}\n"
        "- Ships from Japan\n\n"
        "Please note that prize figures may have minor box wear from factory and shipping."
    ),
    "zh-TW": (
        "{title}\n\n"
        "・日本正版景品公仔{brand_line}\n"
        "・全新未拆\n"
        "・JAN: {jan}\n"
        "・日本出貨\n\n"
        "景品盒裝可能有出廠及運送造成的輕微壓痕，敬請見諒。"
    ),
    "th": (
        "{title}\n\n"
        "- Authentic Japanese prize figure{brand_line}\n"
        "- New, unopened\n"
        "- JAN: {jan}\n"
        "- Ships from Japan"
    ),
}


def normalize(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip()


def needs_translation(text):
    return bool(JAPANESE_RE.search(text or ""))


def listing_title(translated, brand, target):
    """翻訳済みの商品名に、検索されやすい定型語を足して出品タイトルにする"""
    # 引用符や波ダッシュは検索の役に立たず、文字数だけ使うので外す
    translated = normalize(TITLE_NOISE_RE.sub(" ", translated))
    words = [translated]
    if brand and brand.lower() not in translated.lower():
        words.append(brand)
    for word in LISTING_SUFFIX.get(target, LISTING_SUFFIX["en"]):
        if word.lower() not in " ".join(words).lower():
            words.append(word)
    title = " ".join(words)
    if len(title) <= TITLE_MAX:
        return title
    # 長すぎる場合は定型語を後ろから削り、それでも長ければ商品名を切る
    while len(words) > 1 and len(" ".join(words)) > TITLE_MAX:
        words.pop()
    return " ".join(words)[:TITLE_MAX].rstrip()


def listing_description(title, brand, jan, target):
    template = DESCRIPTION.get(target, DESCRIPTION["en"])
    brand_line = f" ({brand})" if brand else ""
    return template.format(title=title, brand_line=brand_line, jan=jan or "-")


class Translator:
    def __init__(self, db_path, glossary_path, deepl_key="", session=None, timeout=8.0):
        self.db_path = db_path
        self.deepl_key = (deepl_key or "").strip()
        self.session = session or requests.Session()
        self.timeout = timeout
        self.lock = threading.Lock()
        with open(glossary_path, encoding="utf-8") as f:
            raw = json.load(f)
        self.builtin = {
            target: {normalize(ja): term for ja, term in terms.items()}
            for target, terms in raw.items()
            if not target.startswith("_")
        }
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS translations (
                    text TEXT NOT NULL,
                    target TEXT NOT NULL,
                    result TEXT NOT NULL,
                    PRIMARY KEY (text, target)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS glossary (
                    ja TEXT NOT NULL,
                    target TEXT NOT NULL,
                    term TEXT NOT NULL,
                    PRIMARY KEY (ja, target)
                )
            """)

    def glossary(self, target):
        # 用語集の無い言語（タイ語など）は、作品名を英語の公式名で残す
        terms = dict(self.builtin.get(target) or self.builtin.get("en", {}))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ja, term FROM glossary WHERE target = ?", (target,)
            ).fetchall()
        terms.update(rows)
        return terms

    def user_glossary(self, target):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ja, term FROM glossary WHERE target = ? ORDER BY ja",
                (target,),
            ).fetchall()
        return [{"ja": ja, "term": term} for ja, term in rows]

    def add_term(self, ja, term, target):
        ja, term = normalize(ja), normalize(term)
        if not ja or not term:
            raise ValueError("用語と訳語の両方が必要です")
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO glossary (ja, target, term) VALUES (?, ?, ?)",
                (ja, target, term),
            )
            # 用語が変わると過去の翻訳結果は古くなるので、その語を含むものを捨てる
            conn.execute(
                "DELETE FROM translations WHERE target = ? AND instr(text, ?) > 0",
                (target, ja),
            )

    def remove_term(self, ja, target):
        ja = normalize(ja)
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM glossary WHERE ja = ? AND target = ?", (ja, target)
            )
            conn.execute(
                "DELETE FROM translations WHERE target = ? AND instr(text, ?) > 0",
                (target, ja),
            )

    def split_by_glossary(self, text, target):
        """用語集の語とそれ以外に分ける。[(訳語, True), (原文, False), ...]

        訳語をそのまま機械翻訳に渡すと、漢字の訳語（葬送的芙莉蓮など）が
        日本語として訳し直されて崩れるため、用語集の語は翻訳に回さない。
        """
        terms = self.glossary(target)
        # 「フリーレン」より先に「葬送のフリーレン」を当てるよう、長い語から順に
        keys = sorted(terms, key=len, reverse=True)
        parts, plain, i = [], "", 0
        while i < len(text):
            ja = next((k for k in keys if text.startswith(k, i)), None)
            if ja is None:
                plain += text[i]
                i += 1
                continue
            if plain.strip():
                parts.append((plain.strip(), False))
            plain = ""
            parts.append((terms[ja], True))
            i += len(ja)
        if plain.strip():
            parts.append((plain.strip(), False))
        return parts

    def apply_glossary(self, text, target):
        return normalize(" ".join(part for part, _ in self.split_by_glossary(text, target)))

    def translate(self, text, target="en"):
        if target not in TARGETS:
            raise ValueError(f"未対応の言語です: {target}")
        text = normalize(text)
        if not text:
            return ""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result FROM translations WHERE text = ? AND target = ?",
                (text, target),
            ).fetchone()
        if row:
            return row[0]

        parts = self.split_by_glossary(text, target)
        pending = [part for part, is_term in parts if not is_term and needs_translation(part)]
        translated = {}
        if pending:
            with self.lock:
                translated = dict(zip(pending, self._machine_translate_many(pending, target)))
        result = normalize(" ".join(
            translated.get(part, part) if not is_term else part for part, is_term in parts
        ))

        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO translations (text, target, result) VALUES (?, ?, ?)",
                (text, target, result),
            )
        return result

    def _machine_translate_many(self, texts, target):
        # 1回の通信で済むよう改行でつないで送り、行ごとに戻す。行数が合わなければ1つずつ訳す
        if len(texts) > 1:
            lines = self._machine_translate("\n".join(texts), target).split("\n")
            if len(lines) == len(texts):
                return [normalize(line) for line in lines]
        return [normalize(self._machine_translate(t, target)) for t in texts]

    def _machine_translate(self, text, target):
        if self.deepl_key and target in DEEPL_TARGETS:
            return self._deepl(text, DEEPL_TARGETS[target])
        return self._google(text, target)

    def _deepl(self, text, target_lang):
        # 無料版のキーは末尾が「:fx」で、送り先のURLが違う
        host = "api-free.deepl.com" if self.deepl_key.endswith(":fx") else "api.deepl.com"
        res = self.session.post(
            f"https://{host}/v2/translate",
            headers={"Authorization": f"DeepL-Auth-Key {self.deepl_key}"},
            data={"text": text, "source_lang": "JA", "target_lang": target_lang},
            timeout=self.timeout,
        )
        res.raise_for_status()
        return res.json()["translations"][0]["text"]

    def _google(self, text, target):
        res = self.session.get(
            GOOGLE_URL,
            params={"client": "gtx", "sl": "ja", "tl": target, "dt": "t", "q": text},
            timeout=self.timeout,
        )
        res.raise_for_status()
        return "".join(part[0] for part in res.json()[0] if part and part[0])
