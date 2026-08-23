#!/usr/bin/env python3
"""Collect public community discussions from Reddit and optional X/Threads APIs."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "community-sources.json"
OUTPUT_PATH = ROOT / "community.json"
USER_AGENT = "PersonalOSDashboard/1.1 by flairmatrix1"
SKIP_TITLE = re.compile(r"\b(weekly|monthly|megathread|support thread|questions thread|self-promotion)\b", re.I)


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_markup(value: str, limit: int = 320) -> str:
    parser = TextExtractor()
    parser.feed(html.unescape(value))
    result = " ".join(" ".join(parser.parts).split())
    result = re.sub(r"submitted by\s+/u/\S+.*$", "", result, flags=re.I)
    return result[:limit].rstrip()


def local_name(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1].lower()


def direct_child(entry: ET.Element, name: str) -> ET.Element | None:
    return next((node for node in entry if local_name(node) == name), None)


def node_text(node: ET.Element | None) -> str:
    return "" if node is None else " ".join("".join(node.itertext()).split())


def parse_date(value: str) -> datetime | None:
    try:
        result = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def classify(title: str, fallback: str = "XR / технологии") -> str:
    value = f" {title.lower()} "
    rules = (
        ("AR / MR", ("augmented reality", "mixed reality", "smart glasses", " xreal ", " ar glasses")),
        ("VR / XR", (" virtual reality", " vr ", " xr ", "quest", "headset", "steamvr", "psvr")),
        ("Компьютеры", (" gpu ", "cpu", "nvidia", "amd", "intel", "windows", "laptop", "pc build", "hardware")),
        ("Игры", (" game", "gaming", "xbox", "playstation", "nintendo", "steam", "mod")),
    )
    for category, needles in rules:
        if any(needle in value for needle in needles):
            return category
    return fallback


def poll_options(category: str, title: str) -> list[str]:
    lower = title.lower()
    if " vs " in lower or " versus " in lower:
        return ["Первый вариант", "Второй вариант", "Зависит от цены и задач", "Сначала посмотрю сравнения"]
    if lower.startswith("should i") or "стоит ли" in lower:
        return ["Да, стоит сделать", "Только если уже есть проблемы", "Нет, пока всё работает", "Лучше доверить специалисту"]
    if "mod" in lower:
        return ["Хочу пережить любимую игру заново", "Открою пропущенную классику", "Интересны оба варианта", "Не связываюсь с VR-модами"]
    if category.startswith("VR") or category.startswith("AR"):
        return ["Хочу попробовать", "Интересно, но нужны тесты", "Подожду развития технологии", "Мне это не нужно"]
    if category == "Компьютеры":
        return ["Цена и производительность решают", "Обновил(а) бы сейчас", "Подожду новое поколение", "Мне хватает текущего ПК"]
    return ["Тема действительно цепляет", "Интересно, но подожду отзывы", "Не вижу в этом ничего нового", "Хочу больше подтверждённых фактов"]


def make_item(*, platform: str, community: str, title: str, summary: str, url: str,
              published: datetime, score: float, basis: str, category: str,
              metrics: dict[str, int] | None = None) -> dict[str, object]:
    item_id = hashlib.sha256(f"{platform}:{url}".encode()).hexdigest()[:16]
    signal = "hot" if score >= 70 else "rising" if score >= 35 else "watch"
    return {
        "id": item_id,
        "platform": platform,
        "community": community,
        "title": title,
        "summary": summary,
        "publishedAt": published.isoformat().replace("+00:00", "Z"),
        "category": classify(title, category),
        "signal": signal,
        "engagementScore": round(score, 1),
        "engagementBasis": basis,
        "metrics": metrics or {},
        "pollQuestion": f"{title} — что вы думаете?",
        "pollOptions": poll_options(classify(title, category), title),
        "sourceUrl": url,
    }


def fetch_json(url: str, headers: dict[str, str]) -> dict[str, object]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read(4_000_000))


def fetch_reddit(config: dict[str, object], now: datetime) -> tuple[list[dict[str, object]], list[str]]:
    items: list[dict[str, object]] = []
    errors: list[str] = []
    max_age = timedelta(days=int(config["maxAgeDays"]))
    categories = {source["community"].lower(): source["category"] for source in config["reddit"]}
    communities = "+".join(source["community"] for source in config["reddit"])
    url = f"https://www.reddit.com/r/{communities}/hot/.rss?limit=100"
    try:
        payload = b""
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/atom+xml"})
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = response.read(4_000_000)
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == 2:
                    raise
                time.sleep(10 * (attempt + 1))
        root = ET.fromstring(payload)
        ranks: dict[str, int] = {}
        for entry in (node for node in root if local_name(node) == "entry"):
            title = html.unescape(node_text(direct_child(entry, "title")))
            if not title or SKIP_TITLE.search(title):
                continue
            link_node = direct_child(entry, "link")
            post_url = link_node.get("href", "") if link_node is not None else ""
            match = re.search(r"reddit\.com/r/([^/]+)/", post_url, re.I)
            community = match.group(1) if match else "reddit"
            updated = parse_date(node_text(direct_child(entry, "updated")))
            if not post_url.startswith("https://") or not updated or now - updated > max_age:
                continue
            key = community.lower()
            ranks[key] = ranks.get(key, 0) + 1
            rank = ranks[key]
            content = node_text(direct_child(entry, "content"))
            freshness = max(0.35, 1 - (now - updated).total_seconds() / max_age.total_seconds())
            score = max(5, (105 - rank * 4) * freshness)
            items.append(make_item(
                platform="Reddit", community=f"r/{community}", title=title,
                summary=strip_markup(content), url=post_url, published=updated,
                score=score, category=categories.get(key, "VR / компьютеры / игры"),
                basis=f"Позиция #{rank} среди Hot-постов r/{community}; числовые апвоуты и комментарии RSS не раскрывает",
            ))
        print(f"Reddit combined Hot feed: {len(items)} posts from {len(ranks)} communities", file=sys.stderr)
    except Exception as exc:
        errors.append(f"Reddit combined feed: {exc}")
        print(errors[-1], file=sys.stderr)
    return items, errors


def fetch_x(config: dict[str, object], now: datetime) -> tuple[list[dict[str, object]], str]:
    token = os.environ.get("X_BEARER_TOKEN")
    if not token:
        return [], "disabled: X_BEARER_TOKEN is not configured"
    x_config = config["x"]
    params = urllib.parse.urlencode({
        "query": x_config["query"], "max_results": x_config["maxResults"],
        "tweet.fields": "created_at,public_metrics,lang", "expansions": "author_id",
        "user.fields": "username",
    })
    data = fetch_json(f"https://api.x.com/2/tweets/search/recent?{params}", {"Authorization": f"Bearer {token}"})
    users = {user["id"]: user["username"] for user in data.get("includes", {}).get("users", [])}
    items = []
    for post in data.get("data", []):
        metrics = post.get("public_metrics", {})
        score = metrics.get("like_count", 0) + 2 * metrics.get("retweet_count", 0) + 1.5 * metrics.get("reply_count", 0)
        author = users.get(post.get("author_id"), "unknown")
        published = parse_date(post.get("created_at", "")) or now
        items.append(make_item(
            platform="X", community=f"@{author}", title=strip_markup(post.get("text", ""), 180),
            summary=strip_markup(post.get("text", "")), url=f"https://x.com/{author}/status/{post['id']}",
            published=published, score=min(100, score / 5), category="XR / компьютеры / игры",
            basis="Публичные метрики X: лайки, репосты и ответы",
            metrics={key: int(value) for key, value in metrics.items() if isinstance(value, int)},
        ))
    return items, "ok"


def fetch_threads(config: dict[str, object], now: datetime) -> tuple[list[dict[str, object]], str]:
    token = os.environ.get("THREADS_ACCESS_TOKEN")
    if not token:
        return [], "disabled: THREADS_ACCESS_TOKEN is not configured"
    items = []
    for keyword in config["threads"]["keywords"]:
        params = urllib.parse.urlencode({
            "q": keyword, "search_type": "TOP", "limit": config["threads"]["resultsPerKeyword"],
            "fields": "id,text,permalink,timestamp,username,has_replies,is_quote_post,is_reply",
        })
        data = fetch_json(f"https://graph.threads.com/v1.0/keyword_search?{params}", {"Authorization": f"Bearer {token}"})
        for rank, post in enumerate(data.get("data", []), 1):
            published = parse_date(post.get("timestamp", "")) or now
            score = max(10, 100 - rank * 4) + (8 if post.get("has_replies") else 0)
            title = strip_markup(post.get("text", ""), 180)
            items.append(make_item(
                platform="Threads", community=f"@{post.get('username', 'unknown')}", title=title,
                summary=strip_markup(post.get("text", "")), url=post.get("permalink", ""),
                published=published, score=min(100, score), category=classify(keyword),
                basis=f"TOP-результат Threads по запросу «{keyword}»; API не отдаёт сопоставимые охваты",
            ))
    return items, "ok"


def main() -> int:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    reddit, reddit_errors = fetch_reddit(config, now)
    x_items, x_status = fetch_x(config, now)
    threads_items, threads_status = fetch_threads(config, now)
    collected = reddit + x_items + threads_items
    if not collected:
        print("No community items collected; keeping existing community.json", file=sys.stderr)
        return 1
    unique = {item["sourceUrl"]: item for item in collected if item.get("sourceUrl")}
    selected = sorted(unique.values(), key=lambda item: (item["engagementScore"], item["publishedAt"]), reverse=True)[: int(config["maxItems"])]
    payload = {
        "updatedAt": now.isoformat(timespec="seconds"),
        "methodNote": "Reddit оценивается по позиции в Hot; X — по публичным метрикам; Threads — по TOP-поиску. Это сигналы обсуждаемости, не исследование всей аудитории.",
        "items": selected,
        "sourceStatus": {
            "reddit": {"status": "ok" if reddit else "failed", "items": len(reddit), "errors": reddit_errors},
            "x": {"status": x_status, "items": len(x_items)},
            "threads": {"status": threads_status, "items": len(threads_items)},
        },
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(selected)} community signals to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
