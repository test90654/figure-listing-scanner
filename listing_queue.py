"""スマホでスキャンした商品を、PC で出品するまで預かっておく「出品待ち」の一覧。

Render の無料プランはサーバーが止まるたびにディスクが消えるため、スプレッドシートの
「出品待ち」シートに保存する。スプレッドシートに繋がらないとき（手元での動作確認など）は
SQLite に保存する。
"""
import sqlite3
import time
import uuid

SHEET_TITLE = "出品待ち"
COLUMNS = [
    "id", "added_at", "jan", "name", "description", "brand",
    "price", "stock", "weight", "w", "l", "h", "status",
]
PENDING = "出品待ち"
DONE = "出品済み"


def new_item(data):
    item = {key: str(data.get(key) or "").strip() for key in COLUMNS}
    item["id"] = uuid.uuid4().hex[:12]
    item["added_at"] = time.strftime("%Y-%m-%d %H:%M")
    item["status"] = PENDING
    return item


class SheetStore:
    def __init__(self, spreadsheet):
        try:
            self.sheet = spreadsheet.worksheet(SHEET_TITLE)
        except Exception:
            # 元の在庫アプリのシートには触らず、専用のシートを足す
            self.sheet = spreadsheet.add_worksheet(title=SHEET_TITLE, rows=1000, cols=len(COLUMNS))
            self.sheet.append_row(COLUMNS, value_input_option="RAW")

    def add(self, item):
        # RAW にしないと JAN が数値として扱われ、先頭の 0 や桁が崩れる
        self.sheet.append_row([item[key] for key in COLUMNS], value_input_option="RAW")

    def all(self):
        rows = self.sheet.get_all_values()[1:]
        return [dict(zip(COLUMNS, row + [""] * (len(COLUMNS) - len(row)))) for row in rows if row and row[0]]

    def set_status(self, item_id, status):
        ids = self.sheet.col_values(1)
        if item_id not in ids:
            return False
        self.sheet.update_cell(ids.index(item_id) + 1, COLUMNS.index("status") + 1, status)
        return True


class SqliteStore:
    def __init__(self, db_path):
        self.db_path = db_path
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS listing_queue ({', '.join(c + ' TEXT' for c in COLUMNS)})"
            )

    def add(self, item):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"INSERT INTO listing_queue ({', '.join(COLUMNS)}) VALUES ({', '.join('?' for _ in COLUMNS)})",
                [item[key] for key in COLUMNS],
            )

    def all(self):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM listing_queue ORDER BY rowid").fetchall()
        return [dict(zip(COLUMNS, row)) for row in rows]

    def set_status(self, item_id, status):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("UPDATE listing_queue SET status = ? WHERE id = ?", (status, item_id))
        return cur.rowcount > 0


class ListingQueue:
    def __init__(self, store, persistent):
        self.store = store
        # スプレッドシートに保存できているか（できていなければ再起動で消える）
        self.persistent = persistent

    def add(self, data):
        item = new_item(data)
        self.store.add(item)
        return item

    def pending(self):
        # 新しく入れた順に出す
        return [item for item in reversed(self.store.all()) if item["status"] == PENDING]

    def mark_done(self, item_id):
        return self.store.set_status(item_id, DONE)
