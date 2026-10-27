import os
import tempfile
import unittest
from unittest.mock import Mock

from size_finder import SizeFinder, extract_size, similarity

BSP_SEARCH = """
<a href="/item/2797355/"><img alt=""><p>アニメ「鬼滅の刃」 HYOKOFIG-竈門禰豆子-</p>
<span>2025年11月13日（木） より順次</span></a>
<a href="/item/2886711/"><p>アイドルマスター ちびぐるみ</p></a>
"""
BSP_ITEM = """
<html><head><title>アニメ「鬼滅の刃」 HYOKOFIG-竈門禰豆子-|商品情報</title></head>
<body><h3>商品仕様</h3><ul><li>・全1種</li><li>・約16cm</li></ul></body></html>
"""
SEGA_DB = {"prize": [
    ["D123375", "葬送のフリーレン　Luminasta　“フリーレン”～葬送のフリーレン～", "", [], None, "", ""],
    ["D123373", "LiSA　アップリケポーチ", "", [], None, "", ""],
]}
SEGA_ITEM = "<title>フリーレン|セガプラザ</title><dl><dt>種類：全1種</dt><dd>サイズ：全長約8×19cm</dd></dl>"


def response(text="", json_data=None):
    res = Mock()
    res.raise_for_status = Mock()
    res.text = text
    res.json.return_value = json_data
    return res


class ExtractSizeTests(unittest.TestCase):
    def test_labelled_size(self):
        self.assertEqual(extract_size("種類：全1種\nサイズ：全長約8×19cm"), "全長約8×19cm")

    def test_height_word(self):
        self.assertEqual(extract_size("全高 約 20.5cm のビッグサイズ"), "全高約20.5cm")

    def test_bare_approximate_size(self):
        self.assertEqual(extract_size("・全1種\n・約16cm"), "約16cm")

    def test_full_width_digits(self):
        self.assertEqual(extract_size("サイズ：約２３ｃｍ"), "約23cm")

    def test_no_size(self):
        self.assertEqual(extract_size("全1種 箱入り"), "")


class SizeFinderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.session = Mock()
        self.finder = SizeFinder(os.path.join(self.tmp.name, "s.db"), session=self.session)

    def tearDown(self):
        self.tmp.cleanup()

    def test_banpresto_search_picks_matching_item(self):
        self.session.get.side_effect = [response(BSP_SEARCH), response(BSP_ITEM)]
        found = self.finder.find("4983164000001", "鬼滅の刃 HYOKOFIG 竈門禰豆子", "Banpresto")
        self.assertEqual(found["size"], "約16cm")
        self.assertEqual(found["url"], "https://bsp-prize.jp/item/2797355/")
        self.assertIn("HYOKOFIG", found["title"])

    def test_sega_matches_name_in_item_list(self):
        self.session.get.side_effect = [response(json_data=SEGA_DB), response(SEGA_ITEM)]
        found = self.finder.find("", '葬送のフリーレン Luminasta "フリーレン"', "SEGA")
        self.assertEqual(found["size"], "全長約8×19cm")
        self.assertEqual(found["url"], "https://segaplaza.jp/prize/D123375/")

    def test_result_is_cached_by_jan(self):
        self.session.get.side_effect = [response(BSP_SEARCH), response(BSP_ITEM)]
        self.finder.find("4983164000001", "鬼滅の刃 HYOKOFIG 竈門禰豆子", "Banpresto")
        found = self.finder.find("4983164000001", "鬼滅の刃 HYOKOFIG 竈門禰豆子", "Banpresto")
        self.assertEqual(found["size"], "約16cm")
        self.assertEqual(self.session.get.call_count, 2)

    def test_unrelated_results_are_not_used(self):
        self.session.get.return_value = response('<a href="/item/1/"><p>アイドルマスター ちびぐるみ</p></a>')
        found = self.finder.find("", "刃牙 Grandista 範馬勇次郎", "Banpresto")
        self.assertEqual(found, {"size": "", "url": "", "title": ""})

    def test_similarity(self):
        self.assertGreater(similarity("刃牙 Grandista 範馬勇次郎", "刃牙 Grandistaッッ!!-HANMA YUJIRO-"), 0.35)
        self.assertLess(similarity("刃牙 Grandista 範馬勇次郎", "アイドルマスター ちびぐるみ"), 0.1)


if __name__ == "__main__":
    unittest.main()
