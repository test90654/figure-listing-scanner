import os
import tempfile
import unittest

from listing_queue import COLUMNS, ListingQueue, SheetStore, SqliteStore


class FakeWorksheet:
    def __init__(self):
        self.rows = []
        self.input_options = []

    def append_row(self, values, value_input_option=None):
        self.rows.append([str(v) for v in values])
        self.input_options.append(value_input_option)

    def get_all_values(self):
        return [list(row) for row in self.rows]

    def col_values(self, col):
        return [row[col - 1] for row in self.rows]

    def update_cell(self, row, col, value):
        self.rows[row - 1][col - 1] = value


class FakeSpreadsheet:
    def __init__(self):
        self.sheets = {}

    def worksheet(self, title):
        return self.sheets[title]

    def add_worksheet(self, title, rows, cols):
        self.sheets[title] = FakeWorksheet()
        return self.sheets[title]


ITEM = {"jan": "0573102737915", "name": "Itachi Figure", "brand": "Banpresto", "price": "37.90", "w": "20"}


class QueueTests(unittest.TestCase):
    def check_queue(self, queue):
        first = queue.add(ITEM)
        second = queue.add({**ITEM, "name": "Frieren Figure"})
        pending = queue.pending()
        self.assertEqual([i["name"] for i in pending], ["Frieren Figure", "Itachi Figure"])
        self.assertEqual(pending[1]["jan"], "0573102737915")
        self.assertEqual(pending[1]["w"], "20")
        self.assertTrue(queue.mark_done(first["id"]))
        self.assertEqual([i["id"] for i in queue.pending()], [second["id"]])
        self.assertFalse(queue.mark_done("missing"))

    def test_sqlite_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.check_queue(ListingQueue(SqliteStore(os.path.join(tmp, "q.db")), persistent=False))

    def test_sheet_store_creates_own_sheet_with_header(self):
        spreadsheet = FakeSpreadsheet()
        self.check_queue(ListingQueue(SheetStore(spreadsheet), persistent=True))
        sheet = spreadsheet.sheets["出品待ち"]
        self.assertEqual(sheet.rows[0], COLUMNS)
        # JAN の先頭の 0 が消えないよう、書き込みはすべて RAW
        self.assertEqual(set(sheet.input_options), {"RAW"})


if __name__ == "__main__":
    unittest.main()
