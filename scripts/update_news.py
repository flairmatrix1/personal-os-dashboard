#!/usr/bin/env python3
"""Build news.json from public XR/VR RSS and Atom feeds."""

from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "news-sources.json"
OUTPUT_PATH = ROOT / "news.json"
USER_AGENT = "PersonalOSDashboard/1.0 (+https://github.com/flairmatrix1/personal-os-dashboard)"
NON_NEWS_TITLE = re.compile(r"\b(review|hands[ -]on|opinion|editorial|weekly roundup|best of)\b", re.I)


def text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def child(entry: ET.Element, names: tuple[str, ...]) -> ET.Element | None:
    for node in entry:
        if node.tag.rsplit("}", 1)[-1].lower() in names:
            return node
    return None


def parse_date(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
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


def clean_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme != "https" or not parts.netloc:
        return ""
    path = re.sub(r"/+$", "", parts.path) or "/"
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, "", ""))


def classify(title: str) -> str:
    lower = title.lower()
    rules = (
        ("AR / smart glasses", (" ar ", "augmented reality", "smart glasses", "android xr", "xreal", "ray-ban")),
        ("MR hardware", ("pico", "headset", "mixed reality")),
        ("Apple Vision", ("apple", "vision pro", "visionos")),
        ("Meta Quest", ("meta quest", "quest 3", "quest pro")),
        ("VR games", ("game", "quest release", "steamvr", "playstation vr", "psvr")),
        ("AI + XR", (" ai ", "artificial intelligence", "assistant")),
    )
    padded = f" {lower} "
    for category, needles in rules:
        if any(needle in padded for needle in needles):
            return category
    return "XR industry"


def content_angle(category: str) -> str:
    angles = {
        "AR / smart glasses": "Коротко объяснить, что изменилось для рынка AR-очков и кому это важно",
        "Apple Vision": "Разобрать влияние новости на стратегию Apple в spatial computing",
        "MR hardware": "Коротко разобрать устройство, подтверждённые характеристики и место на XR-рынке",
        "Meta Quest": "Объяснить, что новость меняет для владельцев Quest и экосистемы Meta",
        "VR games": "Сделать короткую новость и спросить аудиторию, будет ли она играть",
        "AI + XR": "Показать, как новость связывает ИИ с пространственными интерфейсами",
        "XR industry": "Выделить подтверждённые факты и объяснить, почему это важно для XR-рынка",
    }
    return angles[category]


def read_feed(source: dict[str, str]) -> list[dict[str, object]]:
    request = urllib.request.Request(source["url"], headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, text/xml"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read(2_000_000)
    root = ET.fromstring(payload)
    entries = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}]
    result: list[dict[str, object]] = []
    for entry in entries:
        title = html.unescape(text(child(entry, ("title",))))
        link_node = child(entry, ("link",))
        link = (link_node.get("href", "") if link_node is not None else "") or text(link_node)
        published = text(child(entry, ("pubdate", "published", "updated", "date")))
        published_at = parse_date(published)
        url = clean_url(link)
        if title and url and published_at and not NON_NEWS_TITLE.search(title):
            result.append({"title": title, "sourceUrl": url, "published": published_at, "source": source["name"]})
    return result


def build_item(raw: dict[str, object], now: datetime) -> dict[str, str]:
    published = raw["published"]
    assert isinstance(published, datetime)
    age = now - published
    signal = "hot" if age <= timedelta(hours=36) else "rising" if age <= timedelta(days=4) else "watch"
    category = classify(str(raw["title"]))
    url = str(raw["sourceUrl"])
    item_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return {
        "id": item_id,
        "title": str(raw["title"]),
        "source": str(raw["source"]),
        "publishedAt": published.isoformat().replace("+00:00", "Z"),
        "category": category,
        "signal": signal,
        "reason": f"Свежая публикация профильного источника {raw['source']}",
        "contentAngle": content_angle(category),
        "sourceUrl": url,
    }


def main() -> int:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=int(config.get("maxAgeDays", 7)))
    collected: list[dict[str, object]] = []
    errors: list[str] = []
    for source in config["feeds"]:
        try:
            items = read_feed(source)
            collected.extend(item for item in items if item["published"] >= cutoff)
            print(f"{source['name']}: {len(items)} parsed", file=sys.stderr)
        except Exception as exc:  # one broken publisher must not break the whole scan
            errors.append(f"{source['name']}: {exc}")
            print(errors[-1], file=sys.stderr)

    if not collected:
        print("No recent feed items were collected; keeping the existing news.json", file=sys.stderr)
        return 1

    unique: dict[str, dict[str, object]] = {}
    for item in sorted(collected, key=lambda value: value["published"], reverse=True):
        unique.setdefault(str(item["sourceUrl"]), item)
    selected = list(unique.values())[: int(config.get("maxItems", 12))]
    payload = {
        "updatedAt": now.isoformat(timespec="seconds"),
        "items": [build_item(item, now) for item in selected],
        "feedStatus": {"successful": len(config["feeds"]) - len(errors), "failed": len(errors), "errors": errors},
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(selected)} fresh items to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
