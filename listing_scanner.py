"""バーコードをスキャンして商品名を調べ、出品用に翻訳してコピーするための画面。

在庫管理と買取価格の機能は持たない。商品名の手がかりは
  1. 元の在庫アプリと共有しているスプレッドシート（読み取りのみ）
  2. らしんばんオンラインの販売ページ（JAN検索）
の順に探す。
"""
import json
import os
import re
import sqlite3
import time
import traceback

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
import requests

from listing_queue import ListingQueue, SheetStore, SqliteStore
from size_finder import SizeFinder
from translator import TARGETS, Translator, listing_description, listing_title

app = FastAPI(title="Figure Listing Scanner")

base_dir = os.path.dirname(os.path.abspath(__file__))
credential_path = os.path.join(base_dir, "credentials.json")
db_path = os.environ.get("LISTING_DB_PATH", os.path.join(base_dir, "listing.db"))
SPREADSHEET_ID = os.environ.get(
    "SPREADSHEET_ID", "1CHnUUP_9uiZYaWzbpoaTIjYwyFY5YBAv4x5M3gka2T0"
)

if not os.path.exists(credential_path):
  gcp_json_env = os.environ.get("GCP_CREDENTIALS_JSON")
  if gcp_json_env:
    with open(credential_path, "w", encoding="utf-8") as f:
      f.write(gcp_json_env)

translator = Translator(
    db_path,
    os.path.join(base_dir, "glossary.json"),
    deepl_key=os.environ.get("DEEPL_API_KEY", ""),
)
size_finder = SizeFinder(db_path)


# ==========================================
# 1. メーカー判定（出品タイトルに英語のブランド名を入れる）
# ==========================================

BRAND_KEYWORDS = [
    ("Banpresto", [
        "一番くじ", "バンプレスト", "banpresto", "bandai spirits", "grandista",
        "q posket", "relax time", "vibration stars", "solid and souls", "dxf",
        "fluffy puffy", "masterlise", "wcf", "glitter&glamours",
    ]),
    ("SEGA", [
        "luminasta", "ルミナスタ", "ちょこのせ", "tip'n'pop", "セガ", "sega", "spm",
        "プレミアムフィギュア",
    ]),
    ("Taito", [
        "amp+", "coreful", "タイトー", "taito", "aqua float", "desktop cute",
        "プチエット",
    ]),
    ("FuRyu", [
        "bicute", "trio-try-it", "フリュー", "furyu", "tenitol",
        "ぬーどるストッパー", "ひっかけ",
    ]),
]


def detect_brand(title: str) -> str:
  t = (title or "").lower()
  for brand, keywords in BRAND_KEYWORDS:
    if any(k in t for k in keywords):
      return brand
  return ""


# ==========================================
# 2. 商品名の保存先（SQLite。スプレッドシートの内容を起動時に写す）
# ==========================================


def init_db():
  with sqlite3.connect(db_path) as conn:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS items (
            jan TEXT PRIMARY KEY,
            title TEXT,
            image_url TEXT
        )
    """)


def save_item(jan: str, title: str, image_url: str):
  with sqlite3.connect(db_path) as conn:
    conn.execute(
        "INSERT OR REPLACE INTO items (jan, title, image_url) VALUES (?, ?, ?)",
        (jan, title, image_url),
    )


def find_item(jan: str):
  with sqlite3.connect(db_path) as conn:
    row = conn.execute(
        "SELECT title, image_url FROM items WHERE jan = ?", (jan,)
    ).fetchone()
  if row and row[0] and row[0].strip():
    return {"title": row[0].strip(), "image_url": row[1] or ""}
  return None


def open_spreadsheet():
  import gspread
  from oauth2client.service_account import ServiceAccountCredentials

  scope = [
      "https://spreadsheets.google.com/feeds",
      "https://www.googleapis.com/auth/drive",
  ]
  creds = ServiceAccountCredentials.from_json_keyfile_name(credential_path, scope)
  client = gspread.authorize(creds)
  client.set_timeout(30)
  return client.open_by_key(SPREADSHEET_ID)


def sync_sheet_to_local():
  """元の在庫アプリのスプレッドシートから JAN と商品名だけを読む（在庫のシートには書き込まない）"""
  if not os.path.exists(credential_path):
    print("credentials.json が無いため、スプレッドシートの商品名は読み込みません。")
    return
  try:
    rows = open_spreadsheet().sheet1.get_all_values()

    count = 0
    with sqlite3.connect(db_path) as conn:
      for row in rows:
        # 列: JAN, 商品名, 画像URL, 買取価格（買取価格は使わない）
        if len(row) < 2:
          continue
        jan = row[0].strip()
        if not jan.isdigit() or not row[1].strip():
          continue
        image_url = row[2].strip() if len(row) > 2 else ""
        conn.execute(
            "INSERT OR REPLACE INTO items (jan, title, image_url) VALUES (?, ?, ?)",
            (jan, row[1].strip(), image_url),
        )
        count += 1
    print(f"スプレッドシートから商品名を {count} 件読み込みました")
  except Exception as e:
    print(f"スプレッドシート読み込みスキップ: {e}")


init_db()
sync_sheet_to_local()


# ==========================================
# 3. らしんばんオンライン（販売側）の JAN 検索
# ==========================================

LASHINBANG_SEARCH_URL = "https://lashinbang-f-s.snva.jp/"
LASHINBANG_MISS_TTL = 6 * 3600
lashinbang_miss_cache = {}


def codes_of(value):
  """keyword2 には「JAN;別コード」のように複数のコードが入っていることがある"""
  return [code for code in re.split(r"[;,\s]+", value or "") if code]


def lookup_lashinbang_by_jan(jan: str):
  """らしんばんオンラインの販売ページから商品名を引く。見つからなければ None"""
  missed_at = lashinbang_miss_cache.get(jan)
  if missed_at and time.time() - missed_at < LASHINBANG_MISS_TTL:
    return None
  try:
    params = {
        "q": jan,
        "searchbox": "1",
        "pl": "1",
        "sort": "Score,Number7",
        "limit": "10",
        "o": "0",
        "controller": "lashinbang_front",
        "callback": "callback",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://shop.lashinbang.com/",
    }
    res = requests.get(
        LASHINBANG_SEARCH_URL, params=params, headers=headers, timeout=5.0
    )
    if res.status_code != 200:
      return None

    body = res.text.strip()
    body = body[body.index("(") + 1 : body.rindex(")")]
    items = json.loads(body)["kotohaco"]["result"]["items"]

    # 全文検索なので部分一致も混ざる。JANが完全一致する商品だけを採用する
    for item in items:
      if jan not in codes_of(item.get("keyword2")):
        continue
      # 「【未開封】」などの状態表記を商品名から外す
      title = re.sub(r"^(【[^】]*】\s*)+", "", item.get("title") or "").strip()
      if title:
        return {"title": title, "image_url": item.get("image") or ""}

    lashinbang_miss_cache[jan] = time.time()
    return None
  except Exception as e:
    print(f"らしんばんJAN検索スキップ (JAN: {jan}): {e}")
    return None


# ==========================================
# 4. API
# ==========================================


def clean_jan(raw: str) -> str:
  """読み取ったコードから JAN（13桁）を取り出す。元の在庫アプリと同じ規則"""
  digits = "".join(filter(str.isdigit, raw or ""))
  if digits.startswith("2510") and len(digits) > 13:
    return digits[-13:]
  if len(digits) >= 18:
    return digits[-18:]
  if len(digits) >= 13:
    return digits[-13:]
  if len(digits) == 8:
    return digits
  raise HTTPException(status_code=400, detail=f"JANとして読めないコードです: {raw}")


def absolute_image_url(url: str) -> str:
  if url and url.startswith("/"):
    return "https://www.pasomaru.co.jp" + url
  return url or ""


class LookupRequest(BaseModel):
  jan: str


class TranslateRequest(BaseModel):
  title: str
  target: str = "en"
  jan: str = ""


class SizeRequest(BaseModel):
  title: str
  jan: str = ""


class QueueRequest(BaseModel):
  jan: str = ""
  name: str
  description: str = ""
  brand: str = ""
  price: str = ""
  stock: str = "1"
  weight: str = ""
  parcel: list = []


class QueueDoneRequest(BaseModel):
  id: str


class TermRequest(BaseModel):
  ja: str
  term: str = ""
  target: str = "en"


@app.post("/api/lookup")
def lookup(data: LookupRequest):
  jan = clean_jan(data.jan)
  found = find_item(jan)
  source = "local"
  if not found:
    found = lookup_lashinbang_by_jan(jan)
    source = "lashinbang"
    if found:
      save_item(jan, found["title"], found["image_url"])
  if not found:
    return JSONResponse(
        status_code=404,
        content={"detail": f"商品名が見つかりませんでした (JAN: {jan})", "jan": jan},
    )
  return {
      "jan": jan,
      "title": found["title"],
      "image_url": absolute_image_url(found["image_url"]),
      "brand": detect_brand(found["title"]),
      "source": source,
  }


@app.post("/api/translate")
def translate(data: TranslateRequest):
  if data.target not in TARGETS:
    raise HTTPException(status_code=400, detail="未対応の言語です")
  if not data.title.strip():
    raise HTTPException(status_code=400, detail="商品名が空です")
  try:
    translated = translator.translate(data.title, data.target)
  except requests.RequestException as e:
    traceback.print_exc()
    raise HTTPException(status_code=502, detail=f"翻訳サービスに接続できませんでした: {e}")
  brand = detect_brand(data.title)
  title = listing_title(translated, brand, data.target)
  return {
      "translated": translated,
      "listing_title": title,
      "description": listing_description(title, brand, data.jan, data.target),
      "brand": brand,
  }


@app.post("/api/size")
def find_size(data: SizeRequest):
  """メーカー公式サイトでフィギュアのサイズを調べる。出典のページも返す"""
  if not data.title.strip():
    raise HTTPException(status_code=400, detail="商品名が空です")
  return size_finder.find(data.jan, data.title, detect_brand(data.title))


listing_queue = None


def get_listing_queue():
  """出品待ちの保存先。スプレッドシートに繋がらなければ、このサーバーの中に保存する"""
  global listing_queue
  if listing_queue is None:
    try:
      if not os.path.exists(credential_path):
        raise RuntimeError("credentials.json がありません")
      listing_queue = ListingQueue(SheetStore(open_spreadsheet()), persistent=True)
    except Exception as e:
      print(f"出品待ちはスプレッドシートに保存できないため、サーバー内に保存します: {e}")
      listing_queue = ListingQueue(SqliteStore(db_path), persistent=False)
  return listing_queue


@app.post("/api/queue")
def add_to_queue(data: QueueRequest):
  """スマホでスキャンした商品を、PC で出品するまで預かる"""
  if not data.name.strip():
    raise HTTPException(status_code=400, detail="出品タイトルが空です")
  parcel = [str(v) for v in data.parcel] + ["", "", ""]
  queue = get_listing_queue()
  item = queue.add({
      "jan": data.jan, "name": data.name, "description": data.description,
      "brand": data.brand, "price": data.price, "stock": data.stock or "1",
      "weight": data.weight, "w": parcel[0], "l": parcel[1], "h": parcel[2],
  })
  return {"id": item["id"], "persistent": queue.persistent}


@app.get("/api/queue")
def list_queue():
  queue = get_listing_queue()
  return {"items": queue.pending(), "persistent": queue.persistent}


@app.post("/api/queue/done")
def mark_queue_done(data: QueueDoneRequest):
  if not get_listing_queue().mark_done(data.id):
    raise HTTPException(status_code=404, detail="出品待ちに見つかりません")
  return {"status": "ok"}


@app.get("/api/glossary")
def get_glossary(target: str = "en"):
  return {"target": target, "terms": translator.user_glossary(target)}


@app.post("/api/glossary")
def add_glossary_term(data: TermRequest):
  if data.target not in TARGETS:
    raise HTTPException(status_code=400, detail="未対応の言語です")
  try:
    translator.add_term(data.ja, data.term, data.target)
  except ValueError as e:
    raise HTTPException(status_code=400, detail=str(e))
  return {"status": "ok"}


@app.post("/api/glossary/delete")
def delete_glossary_term(data: TermRequest):
  translator.remove_term(data.ja, data.target)
  return {"status": "ok"}


@app.get("/api/targets")
def get_targets():
  return {"targets": TARGETS}


@app.get("/shopee-autofill.user.js")
def get_userscript(request: Request):
  # Tampermonkey は「.user.js」で終わる URL を開くと、そのままインストール画面になる。
  # スクリプトがこのサーバーの出品待ちを読めるよう、このサーバーの URL を書き込んで配る
  host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
  scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
  with open(os.path.join(base_dir, "shopee-autofill.user.js"), encoding="utf-8") as f:
    script = f.read()
  script = script.replace("__SCANNER_URL__", f"{scheme}://{host}")
  script = script.replace("__SCANNER_HOST__", host.split(":")[0])
  return Response(content=script, media_type="text/javascript; charset=utf-8")


@app.get("/")
def get_page():
  return FileResponse(
      os.path.join(base_dir, "listing-ui.html"),
      media_type="text/html",
      headers={"Cache-Control": "no-cache"},
  )


if __name__ == "__main__":
  import uvicorn

  port = int(os.environ.get("PORT", 8000))
  uvicorn.run(app, host="0.0.0.0", port=port)
