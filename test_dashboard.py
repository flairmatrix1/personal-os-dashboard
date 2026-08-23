import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class DashboardContractTests(unittest.TestCase):
    def test_required_files_exist(self):
        for name in (
            "index.html",
            "ideas.json",
            "news.json",
            "channel.json",
            "links.json",
            "news-sources.json",
            "scripts/update_news.py",
            ".github/workflows/update-news.yml",
            "README.md",
        ):
            self.assertTrue((ROOT / name).is_file(), name)

    def test_json_files_have_expected_shapes(self):
        ideas = json.loads((ROOT / "ideas.json").read_text(encoding="utf-8"))
        news = json.loads((ROOT / "news.json").read_text(encoding="utf-8"))
        channel = json.loads((ROOT / "channel.json").read_text(encoding="utf-8"))
        links = json.loads((ROOT / "links.json").read_text(encoding="utf-8"))
        self.assertIsInstance(ideas["items"], list)
        self.assertIsInstance(news["items"], list)
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

    def test_no_secret_placeholders(self):
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in ROOT.glob("*")
            if path.is_file() and path.suffix in {".html", ".json", ".md"}
        ).lower()
        for forbidden in ("api_key", "bearer ", "token="):
            self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
