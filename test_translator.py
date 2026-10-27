import os
import tempfile
import unittest
from unittest.mock import Mock

from translator import Translator, listing_description, listing_title, needs_translation

GLOSSARY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "glossary.json")


def google_response(text):
    res = Mock()
    res.raise_for_status = Mock()
    res.json.return_value = [[[text, "原文", None, None]], None, "ja"]
    return res


class TranslatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.session = Mock()
        self.translator = Translator(
            os.path.join(self.tmp.name, "t.db"), GLOSSARY, session=self.session
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_glossary_covers_title_without_machine_translation(self):
        result = self.translator.translate("葬送のフリーレン Luminasta フリーレン", "en")
        self.assertEqual(result, "Frieren: Beyond Journey's End Luminasta Frieren")
        self.session.get.assert_not_called()

    def test_remaining_japanese_goes_to_machine_translation_after_glossary(self):
        self.session.get.return_value = google_response("Wings")
        result = self.translator.translate("鬼滅の刃 猗窩座 フィギュア 翼", "en")
        self.assertEqual(result, "Demon Slayer Akaza Figure Wings")
        sent = self.session.get.call_args.kwargs["params"]["q"]
        self.assertEqual(sent, "翼")

    def test_kanji_glossary_terms_are_not_sent_to_machine_translation(self):
        self.session.get.return_value = google_response("的日常")
        result = self.translator.translate("葬送のフリーレン Luminasta ～フリーレンの日常～", "zh-TW")
        self.assertTrue(result.startswith("葬送的芙莉蓮 Luminasta"))
        self.assertIn("芙莉蓮", result)
        sent = self.session.get.call_args.kwargs["params"]["q"]
        self.assertNotIn("芙莉蓮", sent)

    def test_language_without_glossary_keeps_english_names(self):
        self.assertEqual(self.translator.translate("葬送のフリーレン", "th"), "Frieren: Beyond Journey's End")

    def test_several_untranslated_parts_share_one_request(self):
        self.session.get.return_value = google_response("Wings\nNight")
        result = self.translator.translate("翼 フリーレン 夜", "en")
        self.assertEqual(result, "Wings Frieren Night")
        self.assertEqual(self.session.get.call_count, 1)

    def test_result_is_cached(self):
        self.session.get.return_value = google_response("Wings")
        self.translator.translate("翼", "en")
        self.translator.translate("翼", "en")
        self.assertEqual(self.session.get.call_count, 1)

    def test_user_term_overrides_builtin_and_clears_stale_cache(self):
        self.translator.translate("フリーレン", "en")
        self.translator.add_term("フリーレン", "Frieren the Slayer", "en")
        self.assertEqual(self.translator.translate("フリーレン", "en"), "Frieren the Slayer")
        self.translator.remove_term("フリーレン", "en")
        self.assertEqual(self.translator.translate("フリーレン", "en"), "Frieren")

    def test_full_width_input_matches_glossary(self):
        self.assertEqual(self.translator.translate("ＯＮＥ　ＰＩＥＣＥ", "en"), "One Piece")

    def test_deepl_used_when_key_set(self):
        session = Mock()
        res = Mock()
        res.raise_for_status = Mock()
        res.json.return_value = {"translations": [{"text": "Wings"}]}
        session.post.return_value = res
        translator = Translator(
            os.path.join(self.tmp.name, "d.db"), GLOSSARY, deepl_key="abc:fx", session=session
        )
        self.assertEqual(translator.translate("翼", "en"), "Wings")
        self.assertIn("api-free.deepl.com", session.post.call_args.args[0])
        session.get.assert_not_called()

    def test_unknown_target_rejected(self):
        with self.assertRaises(ValueError):
            self.translator.translate("翼", "fr")


class ListingTextTests(unittest.TestCase):
    def test_title_adds_brand_and_search_words_once(self):
        self.assertEqual(
            listing_title("Frieren Luminasta Figure", "SEGA", "en"),
            "Frieren Luminasta Figure SEGA Authentic Japan Prize New",
        )

    def test_title_drops_quotes_and_wave_dashes(self):
        self.assertEqual(
            listing_title('Luminasta "Frieren" ~ Daily Life ~', "SEGA", "en"),
            "Luminasta Frieren Daily Life SEGA Figure Authentic Japan Prize New",
        )

    def test_title_trims_suffix_before_name_when_too_long(self):
        name = "A" * 110
        title = listing_title(name, "SEGA", "en")
        self.assertLessEqual(len(title), 120)
        self.assertTrue(title.startswith(name))

    def test_description_includes_jan_and_brand(self):
        text = listing_description("Frieren Figure", "SEGA", "4580779526881", "en")
        self.assertIn("JAN: 4580779526881", text)
        self.assertIn("(SEGA)", text)

    def test_needs_translation(self):
        self.assertTrue(needs_translation("Frieren 翼"))
        self.assertFalse(needs_translation("Frieren: Beyond Journey's End"))


if __name__ == "__main__":
    unittest.main()
