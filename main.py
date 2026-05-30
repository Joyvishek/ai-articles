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

_SOURCE_COLORS = [
    ("teal",   "#E1F5EE", "#085041"),
    ("purple", "#EEEDFE", "#3C3489"),
    ("amber",  "#FAEEDA", "#633806"),
    ("coral",  "#FAECE7", "#712B13"),
    ("green",  "#EAF3DE", "#27500A"),
    ("blue",   "#E6F1FB", "#0C447C"),
]
_source_color_cache: dict[str, tuple[str, str]] = {}


def _source_colors(source: str) -> tuple[str, str]:
    if source not in _source_color_cache:
        idx = len(_source_color_cache) % len(_SOURCE_COLORS)
        _, bg, fg = _SOURCE_COLORS[idx]
        _source_color_cache[source] = (bg, fg)
    return _source_color_cache[source]


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
        content = handle.read().strip()
    if not content:
        return default
    return json.loads(content)


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

    collected.sort(
        key=lambda item: item.published or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
        reverse=True,
    )
    return collected[:max_articles]


# ---------------------------------------------------------------------------
# Relative time helper
# ---------------------------------------------------------------------------

def _relative_time(published: dt.datetime | None) -> str:
    if published is None:
        return "date unknown"
    delta = utc_now() - published
    hours = int(delta.total_seconds() // 3600)
    if hours < 1:
        minutes = int(delta.total_seconds() // 60)
        return f"{max(minutes, 1)}m ago"
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


# ---------------------------------------------------------------------------
# HTML email builder — table-based layout, mobile-first
# ---------------------------------------------------------------------------

def _pill(text: str, bg: str, fg: str) -> str:
    return (
        f'<span style="display:inline-block;font-family:-apple-system,BlinkMacSystemFont,'
        f'\'Segoe UI\',Helvetica,Arial,sans-serif;font-size:11px;font-weight:600;'
        f'line-height:1;padding:3px 9px;border-radius:20px;'
        f'background:{bg};color:{fg};letter-spacing:0.02em">{text}</span>'
    )


def _section_label(text: str) -> str:
    return f"""
<table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">
  <tr>
    <td style="padding:20px 0 8px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
               font-size:11px;font-weight:600;letter-spacing:0.06em;color:#888780;text-transform:uppercase;
               border-bottom:1px solid #e2e0d8">{html.escape(text)}</td>
  </tr>
</table>"""


def _article_card_html(article: Article, index: int) -> str:
    e = html.escape
    is_top = index == 0
    summary = e(summarize(article.summary))
    title = e(article.title)
    link = e(article.link)
    source = e(article.source)
    rel_time = e(_relative_time(article.published))
    bg, fg = _source_colors(article.source)

    left_border = "border-left:3px solid #1D9E75;" if is_top else ""
    border_radius = "border-radius:0 10px 10px 0;" if is_top else "border-radius:10px;"

    top_tag = ""
    if is_top:
        top_tag = (
            '<span style="display:inline-block;font-family:-apple-system,BlinkMacSystemFont,'
            '\'Segoe UI\',Helvetica,Arial,sans-serif;font-size:11px;font-weight:600;'
            'color:#085041;margin-right:6px">&#9889; Top story</span>'
        )

    source_pill = _pill(source, bg, fg)

    return f"""
<table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation"
       style="margin-bottom:10px">
  <tr>
    <td style="background:#ffffff;{left_border}{border_radius}
               border:1px solid #e8e6de;padding:16px 18px;
               font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif">

      <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">
        <tr>
          <td style="padding-bottom:8px;line-height:1.4">
            {top_tag}{source_pill}
            <span style="font-size:12px;color:#b4b2a9;float:right;
                         font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif">{rel_time}</span>
          </td>
        </tr>
        <tr>
          <td style="padding-bottom:7px">
            <a href="{link}"
               style="font-size:15px;font-weight:600;color:#1a1a1a;text-decoration:none;
                      line-height:1.45;display:block;
                      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif">{title}</a>
          </td>
        </tr>
        <tr>
          <td style="padding-bottom:10px">
            <p style="margin:0;font-size:13px;color:#5F5E5A;line-height:1.65;
                      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif">{summary}</p>
          </td>
        </tr>
        <tr>
          <td>
            <a href="{link}"
               style="font-size:12px;font-weight:500;color:#0F6E56;text-decoration:none;
                      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif">Read full article &#8599;</a>
          </td>
        </tr>
      </table>

    </td>
  </tr>
</table>"""


def build_email(config: dict, articles: list[Article]) -> tuple[str, str]:
    today = dt.datetime.now().strftime("%B %d, %Y")
    subject = config.get("email", {}).get("subject", f"Daily AI Article Digest — {today}")
    lookback = int(config.get("lookback_hours", 30))
    feed_count = len(config.get("feeds", []))
    article_count = len(articles)
    e = html.escape

    # ── plain-text ──────────────────────────────────────────────────────────
    plain_lines = [subject, ""]
    if not articles:
        plain_lines.append("No new AI articles were found today.")
    else:
        for article in articles:
            published = (
                article.published.strftime("%Y-%m-%d %H:%M UTC")
                if article.published else "date unavailable"
            )
            plain_lines.extend([
                article.title,
                f"Source: {article.source} | Published: {published}",
                summarize(article.summary),
                article.link,
                "",
            ])

    # ── HTML ────────────────────────────────────────────────────────────────
    sender_address = e(config.get("email", {}).get("from", "digest@example.com"))

    cutoff = utc_now() - dt.timedelta(hours=6)
    latest  = [a for a in articles if a.published and a.published >= cutoff]
    earlier = [a for a in articles if a not in latest and a.published is not None]
    no_date = [a for a in articles if a.published is None]
    latest  += no_date

    cards_html = ""
    global_idx = 0
    if latest:
        cards_html += _section_label("Latest")
        for a in latest:
            cards_html += _article_card_html(a, global_idx)
            global_idx += 1
    if earlier:
        cards_html += _section_label("Earlier today")
        for a in earlier:
            cards_html += _article_card_html(a, global_idx)
            global_idx += 1

    if not articles:
        cards_html = (
            "<p style='font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Helvetica,"
            "Arial,sans-serif;font-size:14px;color:#888780;margin:20px 0'>"
            "No new AI articles were found today.</p>"
        )

    # Stats row — three cells, each 33%, stacks on mobile via max-width trick
    def stat_cell(value: str, label: str) -> str:
        return (
            f'<td width="33%" style="padding:0 5px">'
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">'
            f'<tr><td style="background:#ffffff;border:1px solid #e8e6de;border-radius:8px;'
            f'padding:10px 14px;font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\','
            f'Helvetica,Arial,sans-serif">'
            f'<span style="display:block;font-size:22px;font-weight:600;color:#1a1a1a">{e(value)}</span>'
            f'<span style="display:block;font-size:12px;color:#888780;margin-top:2px">{e(label)}</span>'
            f'</td></tr></table></td>'
        )

    stats_row = (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation"'
        ' style="margin:0 -5px 20px">'
        "<tr>"
        + stat_cell(str(article_count), "Articles today")
        + stat_cell(str(feed_count), "Sources scanned")
        + stat_cell(f"{lookback}h", "Lookback window")
        + "</tr></table>"
    )

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <title>{e(subject)}</title>
  <style>
    body, table, td, p, a, span {{
      -webkit-text-size-adjust: 100%;
      -ms-text-size-adjust: 100%;
    }}
    table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
    img {{ -ms-interpolation-mode: bicubic; border: 0; }}
    @media only screen and (max-width: 520px) {{
      .outer-wrap {{ width: 100% !important; padding: 0 12px !important; }}
      .stat-cell {{ display: block !important; width: 100% !important;
                    padding: 0 0 8px 0 !important; }}
      .stat-inner {{ width: 100% !important; }}
      .card-time  {{ float: none !important; display: block !important;
                    margin-top: 4px !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background:#f5f5f3">

<table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation"
       style="background:#f5f5f3">
  <tr>
    <td align="center" style="padding:32px 0 40px">

      <table class="outer-wrap" width="600" cellpadding="0" cellspacing="0" border="0"
             role="presentation" style="width:600px;max-width:600px">

        <!-- HEADER -->
        <tr>
          <td style="padding-bottom:16px;border-bottom:1px solid #e2e0d8;margin-bottom:20px">
            <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">
              <tr>
                <td style="vertical-align:middle">
                  <table cellpadding="0" cellspacing="0" border="0" role="presentation">
                    <tr>
                      <td style="width:36px;height:36px;border-radius:8px;background:#E1F5EE;
                                 text-align:center;vertical-align:middle;font-size:20px;
                                 padding:0 8px">&#129504;</td>
                      <td style="padding-left:10px;vertical-align:middle">
                        <span style="display:block;font-family:-apple-system,BlinkMacSystemFont,
                          'Segoe UI',Helvetica,Arial,sans-serif;font-size:15px;font-weight:600;
                          color:#1a1a1a">AI Article Digest</span>
                        <span style="display:block;font-family:-apple-system,BlinkMacSystemFont,
                          'Segoe UI',Helvetica,Arial,sans-serif;font-size:12px;color:#888780;
                          margin-top:2px">Your curated daily intelligence briefing</span>
                      </td>
                    </tr>
                  </table>
                </td>
                <td align="right" style="vertical-align:middle">
                  <span style="display:inline-block;font-family:-apple-system,BlinkMacSystemFont,
                    'Segoe UI',Helvetica,Arial,sans-serif;font-size:12px;font-weight:500;
                    color:#085041;background:#E1F5EE;border-radius:20px;
                    padding:4px 12px;white-space:nowrap">{e(today)}</span>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- SPACER -->
        <tr><td style="height:20px"></td></tr>

        <!-- INTRO -->
        <tr>
          <td style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,
                     sans-serif;font-size:13px;color:#5F5E5A;line-height:1.6;padding-bottom:16px">
            Here are today&rsquo;s
            <strong style="color:#1a1a1a;font-weight:600">{article_count} AI article{'s' if article_count != 1 else ''}</strong>
            pulled from
            <strong style="color:#1a1a1a;font-weight:600">{feed_count} source{'s' if feed_count != 1 else ''}</strong>
            across the past
            <strong style="color:#1a1a1a;font-weight:600">{lookback} hours</strong>.
            Everything below matched your AI keyword filter.
          </td>
        </tr>

        <!-- STATS -->
        <tr>
          <td style="padding-bottom:4px">{stats_row}</td>
        </tr>

        <!-- ARTICLES -->
        <tr>
          <td>{cards_html}</td>
        </tr>

        <!-- FOOTER -->
        <tr>
          <td style="padding-top:20px;border-top:1px solid #e2e0d8;
                     font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,
                     sans-serif;font-size:12px;color:#b4b2a9;line-height:1.7">
            Sent by AI Article Digest &middot; {sender_address}<br>
            Fetched from {feed_count} feed{'s' if feed_count != 1 else ''} &middot;
            state saved to sent_articles.json<br><br>
            <a href="#" style="color:#b4b2a9;text-decoration:underline">Unsubscribe</a>
          </td>
        </tr>

      </table>
    </td>
  </tr>
</table>

</body>
</html>"""

    return "\n".join(plain_lines), html_body


# ---------------------------------------------------------------------------
# Everything below is unchanged
# ---------------------------------------------------------------------------

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
    sent_links = list(
        dict.fromkeys(state.get("sent_links", []) + [item.link for item in articles])
    )
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