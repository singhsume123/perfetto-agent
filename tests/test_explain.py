import unittest

from perfetto_agent.explain.llm import build_llm_input, validate_llm_output
from perfetto_agent.analyzer import _prefer_non_unknown_suspects


class TestExplain(unittest.TestCase):
    def test_llm_input_trims_lists(self):
        analysis = {
            "summary": {"startup_dominant_category": "app"},
            "features": {
                "suspects": [{"label": f"s{i}"} for i in range(10)],
                "long_slices_attributed": {"top": [{"name": f"slice{i}"} for i in range(20)]},
                "app_sections": {"top_by_total_ms": [{"name": f"a{i}"} for i in range(12)]},
                "window_breakdown": {"startup": {"by_category_ms": {"app": 1.0}}},
                "work_breakdown": {"by_category_ms": {"app": 1.0}}
            },
            "assumptions": {"classification": "test"}
        }

        llm_input = build_llm_input(analysis)
        current = llm_input["current"]
        self.assertEqual(len(current["features"]["suspects"]), 5)
        self.assertEqual(len(current["features"]["long_slices_attributed"]["top"]), 10)
        self.assertEqual(len(current["features"]["app_sections"]["top_by_total_ms"]), 5)

    def test_validate_llm_output(self):
        valid_output = {
            "title": "Performance Summary",
            "high_level": "Summary text.",
            "key_findings": [{"text": "Finding", "evidence": ["summary.startup_dominant_category"]}],
            "suspects": [{"text": "Suspect", "evidence": ["features.suspects[0]"]}],
            "next_steps": [{"text": "Inspect main thread", "evidence": ["summary.main_thread_blocked_by"]}],
            "limitations": [{"text": "Classification best-effort", "evidence": ["assumptions.classification"]}]
        }
        self.assertEqual(validate_llm_output(valid_output), [])

        invalid_output = {
            "title": "Performance Summary",
            "high_level": "Summary text.",
            "key_findings": [{"text": "Finding"}],
            "suspects": [],
            "next_steps": "not-a-list",
            "limitations": []
        }
        self.assertTrue(validate_llm_output(invalid_output))

    def test_prefer_non_unknown_suspects(self):
        suspects = [
            {"label": "a", "category": "unknown"},
            {"label": "b", "category": "framework"}
        ]
        filtered, used = _prefer_non_unknown_suspects(suspects)
        self.assertTrue(used)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["label"], "b")

        suspects_unknown = [{"label": "a", "category": "unknown"}]
        filtered, used = _prefer_non_unknown_suspects(suspects_unknown)
        self.assertFalse(used)
        self.assertEqual(filtered, suspects_unknown)


if __name__ == "__main__":
    unittest.main()
