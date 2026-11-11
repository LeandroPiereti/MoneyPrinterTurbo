import ast
import unittest
from pathlib import Path

from app.services import llm

WEBUI_MAIN = Path(__file__).parent.parent.parent / "webui" / "Main.py"


class TestRecommendedTermCount(unittest.TestCase):
    def test_short_scripts_keep_historical_amounts(self):
        script = "Diz a lenda que o café foi descoberto por cabras na Etiópia."
        self.assertEqual(llm.recommended_term_count(script, False), 5)
        self.assertEqual(llm.recommended_term_count(script, True), 8)
        self.assertEqual(llm.recommended_term_count("", True), 8)

    def test_long_scripts_get_more_terms_up_to_a_cap(self):
        five_minutes = " ".join(["palavra"] * 700)
        self.assertEqual(llm.recommended_term_count(five_minutes, False), 14)
        self.assertEqual(llm.recommended_term_count(five_minutes, True), 17)
        very_long = " ".join(["palavra"] * 5000)
        self.assertEqual(
            llm.recommended_term_count(very_long, True), llm.MAX_RECOMMENDED_TERMS
        )

    def test_scripts_without_spaces_count_characters(self):
        chinese = "春天的花海如诗如画般展现在眼前" * 50
        self.assertGreater(llm.recommended_term_count(chinese, False), 5)


def _load_default_subtitle_font():
    tree = ast.parse(WEBUI_MAIN.read_text(encoding="utf-8"))
    wanted = {"LATIN_SUBTITLE_FONT", "LATIN_SUBTITLE_FONT_LANGUAGES", "default_subtitle_font"}
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            body.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in wanted for target in node.targets
        ):
            body.append(node)
    namespace = {"DEFAULT_SUBTITLE_SETTINGS": {"font_name": "MicrosoftYaHeiBold.ttc"}}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(WEBUI_MAIN), "exec"), namespace)
    return namespace["default_subtitle_font"]


class TestDefaultSubtitleFont(unittest.TestCase):
    def setUp(self):
        self.default_font = _load_default_subtitle_font()

    def test_latin_languages_use_latin_font(self):
        for language in ("pt", "en", "es-ES", "pt_BR"):
            self.assertEqual(self.default_font(language), "BeVietnamPro-Bold.ttf")

    def test_other_languages_keep_cjk_default(self):
        for language in ("zh", "ru", "ko", "az", "", None):
            self.assertEqual(self.default_font(language), "MicrosoftYaHeiBold.ttc")

    def test_missing_latin_font_falls_back(self):
        self.assertEqual(
            self.default_font("pt", ["MicrosoftYaHeiBold.ttc"]), "MicrosoftYaHeiBold.ttc"
        )


if __name__ == "__main__":
    unittest.main()
