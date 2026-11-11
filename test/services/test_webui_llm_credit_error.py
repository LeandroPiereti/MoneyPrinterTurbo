import ast
import re
import unittest
from pathlib import Path

WEBUI_MAIN = Path(__file__).parent.parent.parent / "webui" / "Main.py"


def _load_format_llm_connection_error(translations):
    """Compile only format_llm_connection_error so the test does not boot Streamlit."""
    tree = ast.parse(WEBUI_MAIN.read_text(encoding="utf-8"))
    func = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "format_llm_connection_error"
    )
    namespace = {
        "re": re,
        "tr": lambda key: translations.get(key, key),
        "tr_optional": lambda key, fallback_language="": translations.get(key, ""),
        "get_llm_provider": lambda provider_id: None,
    }
    exec(compile(ast.Module(body=[func], type_ignores=[]), str(WEBUI_MAIN), "exec"), namespace)
    return namespace["format_llm_connection_error"]


class TestLlmInsufficientCreditError(unittest.TestCase):
    def setUp(self):
        self.format_error = _load_format_llm_connection_error(
            {"LLM Insufficient Credit Error": "No API credits. Details: {error}"}
        )

    def test_anthropic_low_credit_balance_gets_explained(self):
        error = (
            "Error code: 400 - {'error': {'message': 'Your credit balance is too low "
            "to access the Anthropic API.'}}"
        )
        message = self.format_error("anthropic", "", error)
        self.assertTrue(message.startswith("No API credits."))
        self.assertIn(error, message)

    def test_openai_insufficient_quota_gets_explained(self):
        message = self.format_error("openai", "", "Error code: 429 - insufficient_quota")
        self.assertTrue(message.startswith("No API credits."))

    def test_unrelated_errors_are_returned_unchanged(self):
        self.assertEqual(self.format_error("anthropic", "", "Request timed out."), "Request timed out.")


if __name__ == "__main__":
    unittest.main()
