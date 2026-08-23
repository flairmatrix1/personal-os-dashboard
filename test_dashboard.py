import json
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_community_module():
    path = ROOT / "scripts/update_community.py"
    spec = importlib.util.spec_from_file_location("update_community", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class DashboardContractTests(unittest.TestCase):
    def test_required_files_exist(self):
        for name in (
            "index.html",
            "ideas.json",
            "news.json",
            "community.json",
            "channel.json",
            "links.json",
            "news-sources.json",
            "community-sources.json",
            "scripts/update_news.py",
            "scripts/update_community.py",
            ".github/workflows/update-news.yml",
            "README.md",
        ):
            self.assertTrue((ROOT / name).is_file(), name)

    def test_json_files_have_expected_shapes(self):
        ideas = json.loads((ROOT / "ideas.json").read_text(encoding="utf-8"))
        news = json.loads((ROOT / "news.json").read_text(encoding="utf-8"))
        community = json.loads((ROOT / "community.json").read_text(encoding="utf-8"))
        channel = json.loads((ROOT / "channel.json").read_text(encoding="utf-8"))
        links = json.loads((ROOT / "links.json").read_text(encoding="utf-8"))
        self.assertIsInstance(ideas["items"], list)
        self.assertIsInstance(news["items"], list)
        self.assertIsInstance(community["items"], list)
        self.assertIsInstance(channel["topContent"], list)
        self.assertIsInstance(links["groups"], list)

    def test_html_contains_core_dashboard_behaviour(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        for value in (
            "Asia/Shanghai",
            "Europe/Moscow",
            "America/Los_Angeles",
            "ideas.json",
            "news.json",
            "community.json",
            "channel.json",
            "links.json",
            "navigator.clipboard",
            "localStorage",
            "prefers-reduced-motion",
            "setInterval(loadDashboard, 10 * 60 * 1000)",
        ):
            self.assertIn(value, html)

    def test_real_data_replaces_examples(self):
        ideas = json.loads((ROOT / "ideas.json").read_text(encoding="utf-8"))
        news = json.loads((ROOT / "news.json").read_text(encoding="utf-8"))
        self.assertFalse(any(item.get("isExample") for item in ideas["items"]))
        self.assertGreaterEqual(len(news["items"]), 5)
        for item in news["items"]:
            self.assertTrue(item.get("publishedAt"), item)
            self.assertTrue(item.get("sourceUrl", "").startswith("https://"), item)

    def test_news_automation_contract(self):
        sources = json.loads((ROOT / "news-sources.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(sources["feeds"]), 3)
        self.assertTrue(all(item["url"].startswith("https://") for item in sources["feeds"]))
        workflow = (ROOT / ".github/workflows/update-news.yml").read_text(encoding="utf-8")
        self.assertIn("schedule:", workflow)
        self.assertIn("scripts/update_news.py", workflow)
        self.assertIn("scripts/update_community.py", workflow)
        self.assertIn("OPENAI_API_KEY", workflow)

    def test_community_signal_contract(self):
        community = json.loads((ROOT / "community.json").read_text(encoding="utf-8"))
        self.assertIn("llm", community["sourceStatus"])
        self.assertGreaterEqual(len(community["items"]), 5)
        for item in community["items"]:
            self.assertIn(item["platform"], {"Reddit", "X", "Threads"})
            self.assertTrue(item["sourceUrl"].startswith("https://"))
            self.assertIsInstance(item["pollOptions"], list)
            self.assertGreaterEqual(len(item["pollOptions"]), 3)
            self.assertTrue(item["engagementBasis"])

    def test_llm_enrichment_is_optional_without_api_key(self):
        module = load_community_module()
        items = [{"id": "one", "title": "A topic", "summary": "Context", "pollOptions": ["fallback"]}]
        enriched, status = module.apply_llm_enrichment(items, api_key="")
        self.assertEqual(enriched, items)
        self.assertEqual(status, "disabled: OPENAI_API_KEY is not configured")

    def test_llm_enrichment_updates_only_matching_items(self):
        module = load_community_module()
        items = [{"id": "one", "title": "RTX vs Radeon", "summary": "Original", "pollOptions": ["fallback"], "sourceUrl": "https://example.com/post"}]

        def fake_request(payload, api_key):
            self.assertEqual(api_key, "secret")
            self.assertIn("RTX vs Radeon", json.dumps(payload, ensure_ascii=False))
            return {"choices": [{"message": {"content": json.dumps({"items": [{
                "id": "one",
                "summaryRu": "Пользователи сравнивают видеокарты по цене и объёму памяти.",
                "pollQuestion": "Что бы вы выбрали при одинаковой цене?",
                "pollOptions": ["RTX", "Radeon", "Подожду тестов", "Останусь на старой карте"],
            }]}, ensure_ascii=False)}}]}

        enriched, status = module.apply_llm_enrichment(items, api_key="secret", request_fn=fake_request)
        self.assertEqual(status, "ok")
        self.assertEqual(enriched[0]["summary"], "Пользователи сравнивают видеокарты по цене и объёму памяти.")
        self.assertEqual(enriched[0]["pollOptions"][0], "RTX")
        self.assertEqual(enriched[0]["sourceUrl"], "https://example.com/post")

    def test_no_secret_placeholders(self):
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in ROOT.glob("*")
            if path.is_file() and path.suffix in {".html", ".json", ".md"}
        ).lower()
        for forbidden in ("sk-proj-", "ghp_", '"access_token":', "token="):
            self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
