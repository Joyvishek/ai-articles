#!/usr/bin/env python3
"""
Daily AI article digest.

Fetches AI-related RSS/Atom feeds, summarizes new items, and emails a digest.
Configuration lives in digest_config.json by default.
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.message
import html
import json
import os
import re
import smtplib
import ssl
import textwrap
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable


APP_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = APP_DIR / "digest_config.json"
DEFAULT_STATE = APP_DIR / "sent_articles.json"
USER_AGENT = "AIArticleDigest/1.0 (+https://example.local)"

AI_PATTERNS = [
    r"\bai\b",
    r"\bartificial intelligence\b",
    r"\bmachine learning\b",
    r"\bdeep learning\b",
    r"\bllms?\b",
    r"\blarge language models?\b",
    r"\bgenerative ai\b",
    r"\bopenai\b",
    r"\banthropic\b",
    r"\bdeepmind\b",
    r"\bchatgpt\b",
    r"\bclaude\b",
    r"\bgemini\b",
    r"\bmistral\b",
    r"\bperplexity\b",
    r"\bmidjourney\b",
    r"\bstable diffusion\b",
]


@dataclass
class FeedSource:
    name: str
    url: str


@dataclass
class Article:
    title: str
    link: str
    source: str
    published: dt.datetime | None
    summary: str


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def save_json(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def get_config(path: Path) -> dict:
    config = load_json(path, None)
    if config is None:
        raise SystemExit(
            f"Missing {path.name}. Copy digest_config.example.json to {path.name} "
            "and fill in your email settings."
        )
    return config


def request_text(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(content_type, errors="replace")


def strip_markup(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    return normalize_space(value)


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def parse_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def child_text(element: ET.Element, names: Iterable[str]) -> str | None:
    wanted = set(names)
    for child in element.iter():
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag in wanted and child.text:
            return child.text
    return None


def entry_link(element: ET.Element) -> str:
    direct = child_text(element, ["link"])
    if direct and direct.startswith(("http://", "https://")):
        return direct.strip()

    for child in element.iter():
        tag = child.tag.rsplit("}", 1)[-1].lower()
        href = child.attrib.get("href", "")
        if tag == "link" and href:
            return href.strip()
    return ""


def parse_feed(xml_text: str, source_name: str) -> list[Article]:
    root = ET.fromstring(xml_text)
    root_tag = root.tag.rsplit("}", 1)[-1].lower()
    entries = root.findall(".//item") if root_tag == "rss" else root.findall(".//{*}entry")
    if not entries:
        entries = root.findall(".//item")

    articles: list[Article] = []
    for entry in entries:
        title = normalize_space(child_text(entry, ["title"]) or "Untitled")
        link = entry_link(entry)
        if not link:
            continue

        published = parse_datetime(
            child_text(entry, ["pubdate", "published", "updated", "date"])
        )
        raw_summary = (
            child_text(entry, ["description", "summary", "content", "encoded"]) or ""
        )
        summary = strip_markup(raw_summary)
        articles.append(Article(title, link, source_name, published, summary))
    return articles


def sentence_split(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", normalize_space(text))
    return [part for part in parts if len(part) > 20]


def extract_article_text(page_html: str) -> str:
    meta_match = re.search(
        r"""<meta[^>]+(?:name|property)=["'](?:description|og:description)["'][^>]+content=["']([^"']+)["']""",
        page_html,
        flags=re.IGNORECASE,
    )
    if meta_match:
        return strip_markup(meta_match.group(1))

    paragraphs = re.findall(r"(?is)<p[^>]*>(.*?)</p>", page_html)
    text = " ".join(strip_markup(paragraph) for paragraph in paragraphs[:8])
    return normalize_space(text)


def enrich_summary(article: Article, min_chars: int) -> Article:
    if len(article.summary) >= min_chars:
        return article
    try:
        page_html = request_text(article.link, timeout=15)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return article

    page_text = extract_article_text(page_html)
    if len(page_text) > len(article.summary):
        article.summary = page_text
    return article


def summarize(text: str, max_sentences: int = 3, max_chars: int = 650) -> str:
    text = normalize_space(text)
    if not text:
        return "No summary was available from the feed."

    sentences = sentence_split(text)
    if not sentences:
        return textwrap.shorten(text, width=max_chars, placeholder="...")

    chosen = sentences[:max_sentences]
    summary = " ".join(chosen)
    return textwrap.shorten(summary, width=max_chars, placeholder="...")


def is_ai_related(article: Article) -> bool:
    haystack = f"{article.title} {article.summary}".lower()
    return any(re.search(pattern, haystack, flags=re.IGNORECASE) for pattern in AI_PATTERNS)


def is_recent(article: Article, since: dt.datetime) -> bool:
    return article.published is None or article.published >= since


def collect_articles(config: dict, state: dict) -> list[Article]:
    lookback_hours = int(config.get("lookback_hours", 30))
    max_articles = int(config.get("max_articles", 12))
    since = utc_now() - dt.timedelta(hours=lookback_hours)
    sent_links = set(state.get("sent_links", []))
    fetch_article_pages = bool(config.get("fetch_article_pages", True))
    min_summary_chars = int(config.get("min_summary_chars", 120))

    collected: list[Article] = []
    for source in config.get("feeds", []):
        feed = FeedSource(source["name"], source["url"])
        try:
            xml_text = request_text(feed.url)
            items = parse_feed(xml_text, feed.name)
        except (ET.ParseError, urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"Warning: could not read {feed.name}: {exc}")
            continue

        for item in items:
            if item.link in sent_links:
                continue
            if not is_recent(item, since):
                continue
            if fetch_article_pages:
                item = enrich_summary(item, min_summary_chars)
            if config.get("filter_ai_keywords", True) and not is_ai_related(item):
                continue
            collected.append(item)

    collected.sort(key=lambda item: item.published or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
    return collected[:max_articles]


def build_email(config: dict, articles: list[Article]) -> tuple[str, str]:
    today = dt.datetime.now().strftime("%B %d, %Y")
    subject = config.get("email", {}).get("subject", f"Daily AI Article Digest - {today}")

    plain_lines = [subject, ""]
    html_parts = [
        "<html><body>",
        f"<h2>{html.escape(subject)}</h2>",
        "<p>Here are the newest AI articles found across the configured global sources.</p>",
    ]

    if not articles:
        plain_lines.append("No new AI articles were found today.")
        html_parts.append("<p>No new AI articles were found today.</p>")
    else:
        html_parts.append("<ol>")
        for article in articles:
            published = (
                article.published.strftime("%Y-%m-%d %H:%M UTC")
                if article.published
                else "date unavailable"
            )
            summary = summarize(article.summary)
            plain_lines.extend(
                [
                    article.title,
                    f"Source: {article.source} | Published: {published}",
                    summary,
                    article.link,
                    "",
                ]
            )
            html_parts.extend(
                [
                    "<li>",
                    f"<h3><a href=\"{html.escape(article.link)}\">{html.escape(article.title)}</a></h3>",
                    f"<p><strong>{html.escape(article.source)}</strong> | {html.escape(published)}</p>",
                    f"<p>{html.escape(summary)}</p>",
                    "</li>",
                ]
            )
        html_parts.append("</ol>")

    html_parts.append("</body></html>")
    return "\n".join(plain_lines), "".join(html_parts)


def get_recipients(config: dict) -> list[str]:
    recipients = config.get("email", {}).get("to", [])
    if isinstance(recipients, str):
        recipients = [recipients]
    return [recipient.strip() for recipient in recipients if str(recipient).strip()]


def env_or_config(config: dict, key: str, env_name: str, default: str = "") -> str:
    return os.getenv(env_name) or str(config.get(key, default))


def send_email(config: dict, text_body: str, html_body: str) -> None:
    email_config = config["email"]
    smtp_config = config["smtp"]
    recipients = get_recipients(config)

    if not recipients:
        print("No recipients configured. Skipping email send.")
        return

    username = env_or_config(smtp_config, "username", "DIGEST_SMTP_USERNAME")
    password = env_or_config(smtp_config, "password", "DIGEST_SMTP_PASSWORD")
    host = env_or_config(smtp_config, "host", "DIGEST_SMTP_HOST")
    port = int(env_or_config(smtp_config, "port", "DIGEST_SMTP_PORT", "587"))

    if not username or not password or not host:
        raise SystemExit(
            "SMTP settings are incomplete. Set them in digest_config.json or use "
            "DIGEST_SMTP_HOST, DIGEST_SMTP_USERNAME, and DIGEST_SMTP_PASSWORD."
        )

    message = email.message.EmailMessage()
    message["Subject"] = email_config.get("subject", "Daily AI Article Digest")
    message["From"] = email_config.get("from", username)
    message["To"] = ", ".join(recipients)
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.starttls(context=context)
            server.login(username, password)
            refused = server.send_message(message)
            if refused:
                refused_list = ", ".join(refused.keys())
                raise SystemExit(f"SMTP server refused these recipient(s): {refused_list}")
    except smtplib.SMTPAuthenticationError as exc:
        raise SystemExit(
            "SMTP authentication failed. Gmail usually requires an App Password "
            "instead of the normal account password. Create a Gmail App Password "
            "and update smtp.password in digest_config.json."
        ) from exc


def update_state(path: Path, state: dict, articles: list[Article]) -> None:
    sent_links = list(dict.fromkeys(state.get("sent_links", []) + [item.link for item in articles]))
    state["sent_links"] = sent_links[-1000:]
    state["last_run_utc"] = utc_now().isoformat()
    save_json(path, state)


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a daily AI article email digest.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to digest config JSON.")
    parser.add_argument("--state", default=str(DEFAULT_STATE), help="Path to sent-link state JSON.")
    parser.add_argument("--dry-run", action="store_true", help="Print the digest instead of sending email.")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    state_path = Path(args.state).resolve()
    config = get_config(config_path)
    state = load_json(state_path, {"sent_links": []})

    if not args.dry_run and not get_recipients(config):
        print("No recipients configured. Scheduled run stopped before fetching or sending.")
        return 0

    articles = collect_articles(config, state)
    text_body, html_body = build_email(config, articles)

    if args.dry_run:
        print(text_body)
    elif not articles:
        update_state(state_path, state, articles)
        print("No new AI articles found. Email not sent.")
    else:
        send_email(config, text_body, html_body)
        update_state(state_path, state, articles)
        print(f"Sent digest with {len(articles)} article(s).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
