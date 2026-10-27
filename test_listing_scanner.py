import os
import tempfile
import unittest
from unittest.mock import patch

_tmp = tempfile.TemporaryDirectory()
os.environ["LISTING_DB_PATH"] = os.path.join(_tmp.name, "listing.db")

from fastapi.testclient import TestClient  # noqa: E402

import listing_scanner  # noqa: E402


class ListingScannerTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(listing_scanner.app)

    def test_lookup_uses_local_item_and_detects_brand(self):
        listing_scanner.save_item("4580779526881", "葬送のフリーレン Luminasta フリーレン", "/img/a.jpg")
        res = self.client.post("/api/lookup", json={"jan": "4580779526881"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["brand"], "SEGA")
        self.assertEqual(data["image_url"], "https://www.pasomaru.co.jp/img/a.jpg")
        self.assertEqual(data["source"], "local")

    def test_lookup_falls_back_to_lashinbang_and_caches(self):
        found = {"title": "刃牙 Grandista 範馬勇次郎", "image_url": "https://x/y.jpg"}
        with patch.object(listing_scanner, "lookup_lashinbang_by_jan", return_value=found) as m:
            res = self.client.post("/api/lookup", json={"jan": "4983164000001"})
            self.assertEqual(res.json()["brand"], "Banpresto")
            res = self.client.post("/api/lookup", json={"jan": "4983164000001"})
            self.assertEqual(res.json()["source"], "local")
            self.assertEqual(m.call_count, 1)

    def test_lookup_not_found_returns_jan(self):
        with patch.object(listing_scanner, "lookup_lashinbang_by_jan", return_value=None):
            res = self.client.post("/api/lookup", json={"jan": "4900000000002"})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["jan"], "4900000000002")

    def test_lookup_rejects_short_code(self):
        res = self.client.post("/api/lookup", json={"jan": "12345"})
        self.assertEqual(res.status_code, 400)

    def test_translate_builds_listing_text(self):
        res = self.client.post("/api/translate", json={
            "title": "葬送のフリーレン Luminasta フリーレン", "target": "en", "jan": "4580779526881",
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["translated"], "Frieren: Beyond Journey's End Luminasta Frieren")
        self.assertEqual(
            data["listing_title"],
            "Frieren: Beyond Journey's End Luminasta Frieren SEGA Figure Authentic Japan Prize New",
        )
        self.assertIn("JAN: 4580779526881", data["description"])

    def test_glossary_add_list_delete(self):
        res = self.client.post("/api/glossary", json={"ja": "テスト語", "term": "Test", "target": "en"})
        self.assertEqual(res.status_code, 200)
        terms = self.client.get("/api/glossary?target=en").json()["terms"]
        self.assertIn({"ja": "テスト語", "term": "Test"}, terms)
        self.client.post("/api/glossary/delete", json={"ja": "テスト語", "target": "en"})
        terms = self.client.get("/api/glossary?target=en").json()["terms"]
        self.assertNotIn({"ja": "テスト語", "term": "Test"}, terms)

    def test_page_served(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("出品名スキャナー", res.text)


if __name__ == "__main__":
    unittest.main()
