#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""کلکتور ترند روزانه
جمع‌آوری ترندهای روز از منابع رایگان برای چهار حوزه محتوایی:
فناوری/هوش مصنوعی، کریپتو/مالی، آموزشی/سرگرمی.

خروجی: trends/trends-YYYY-MM-DD.jsonl (یک رکورد JSON در هر خط)
اجرا: با GitHub Actions (trend-collect.yml) دو بار در روز، یا دستی:
    python trend_collector.py

منابع:
  - Google Trends (ایران و آمریکا)
  - Hacker News (برترین‌های فناوری/هوش مصنوعی)
  - Reddit (r/artificial، r/technology، r/CryptoCurrency — برترین‌های ۲۴ ساعت)
  - CoinGecko trending (کریپتو/مالی)
  - YouTube trending (فقط اگر YOUTUBE_API_KEY تنظیم شده باشد؛ منطقه ایران و آمریکا)
"""

import json
import os
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

OUT_DIR = "trends"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 trend-collector/1.0"
)


def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def http_get_json(url, timeout=25):
    return json.loads(http_get(url, timeout))


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(source, niche, rank, title, **extra):
    row = {
        "collected_at": now_iso(),
        "source": source,
        "niche": niche,
        "rank": rank,
        "title": title,
    }
    row.update({k: v for k, v in extra.items() if v is not None})
    return row


# ---------- XML helpers (namespace-agnostic) ----------

def _local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def atom_entries(xml_text):
    root = ET.fromstring(xml_text)
    return [el for el in root.iter() if _local(el.tag) == "entry"]


def entry_fields(entry):
    fields, links = {}, []
    for child in entry:
        name = _local(child.tag)
        if name == "link":
            href = child.attrib.get("href")
            if href:
                links.append(href)
        else:
            text = (child.text or "").strip()
            if text:
                fields.setdefault(name, text)
    fields["links"] = links
    return fields


# ---------- منابع ----------

def google_trends(geo, niche="عمومی/سرگرمی", limit=20):
    xml_text = http_get("https://trends.google.com/trending/rss?geo=" + geo)
    rows = []
    for i, entry in enumerate(atom_entries(xml_text)[:limit], 1):
        f = entry_fields(entry)
        rows.append(
            record(
                "google_trends",
                niche,
                i,
                f.get("title", ""),
                geo=geo,
                approx_traffic=f.get("approx_traffic"),
                news_title=f.get("news_item_title"),
                url=(f.get("links") or [None])[0],
            )
        )
    return rows


def hacker_news(limit=20):
    ids = http_get_json("https://hacker-news.firebaseio.com/v0/topstories.json")[:limit]
    rows = []
    for i, item_id in enumerate(ids, 1):
        item = http_get_json(
            "https://hacker-news.firebaseio.com/v0/item/" + str(item_id) + ".json"
        )
        if not item or not item.get("title"):
            continue
        rows.append(
            record(
                "hacker_news",
                "فناوری/هوش مصنوعی",
                i,
                item["title"],
                url=item.get("url"),
                score=item.get("score"),
                comments=item.get("descendants"),
            )
        )
    return rows


REDDIT_SUBS = [
    ("artificial", "فناوری/هوش مصنوعی"),
    ("technology", "فناوری/هوش مصنوعی"),
    ("CryptoCurrency", "کریپتو/مالی"),
]


def reddit_top(sub, niche, limit=10):
    xml_text = http_get("https://www.reddit.com/r/" + sub + "/top/.rss?t=day")
    rows = []
    for i, entry in enumerate(atom_entries(xml_text)[:limit], 1):
        f = entry_fields(entry)
        rows.append(
            record(
                "reddit",
                niche,
                i,
                f.get("title", ""),
                sub=sub,
                url=(f.get("links") or [None])[0],
            )
        )
    return rows


def coingecko_trending(limit=15):
    data = http_get_json("https://api.coingecko.com/api/v3/search/trending")
    rows = []
    for i, c in enumerate((data.get("coins") or [])[:limit], 1):
        item = c.get("item", {}) or {}
        rows.append(
            record(
                "coingecko_trending",
                "کریپتو/مالی",
                i,
                str(item.get("name", "")) + " (" + str(item.get("symbol", "")) + ")",
                coin_id=item.get("id"),
                market_cap_rank=item.get("market_cap_rank"),
            )
        )
    return rows


def youtube_trending(api_key, region, niche="سرگرمی/آموزشی", limit=25):
    url = (
        "https://www.googleapis.com/youtube/v3/videos"
        "?part=snippet,statistics&chart=mostPopular"
        "&maxResults=" + str(limit) + "&regionCode=" + region + "&key=" + api_key
    )
    data = http_get_json(url)
    rows = []
    for i, v in enumerate(data.get("items", []), 1):
        s = v.get("snippet", {}) or {}
        st = v.get("statistics", {}) or {}
        rows.append(
            record(
                "youtube_trending",
                niche,
                i,
                s.get("title", ""),
                geo=region,
                channel=s.get("channelTitle"),
                category_id=s.get("categoryId"),
                views=st.get("viewCount"),
                published_at=s.get("publishedAt"),
            )
        )
    return rows


# ---------- ذخیره ----------

def save(rows):
    os.makedirs(OUT_DIR, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(OUT_DIR, "trends-" + day + ".jsonl")
    with open(path, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def main():
    collectors = [
        ("google_trends_IR", lambda: google_trends("IR")),
        ("google_trends_US", lambda: google_trends("US")),
        ("hacker_news", hacker_news),
    ]
    for sub, niche in REDDIT_SUBS:
        collectors.append(("reddit_" + sub, lambda s=sub, n=niche: reddit_top(s, n)))
    collectors.append(("coingecko_trending", coingecko_trending))

    api_key = (os.environ.get("YOUTUBE_API_KEY") or "").strip()
    if api_key:
        collectors.append(("youtube_trending_IR", lambda: youtube_trending(api_key, "IR")))
        collectors.append(("youtube_trending_US", lambda: youtube_trending(api_key, "US")))
    else:
        print("[info] YOUTUBE_API_KEY تنظیم نشده؛ ترندهای یوتیوب رد شد")

    total = 0
    for name, fn in collectors:
        try:
            rows = fn()
            save(rows)
            total += len(rows)
            print("[ok] " + name + ": " + str(len(rows)) + " rows")
        except Exception as exc:
            print("[warn] " + name + " failed: " + str(exc))
    print("[done] total " + str(total) + " rows -> " + OUT_DIR + "/")


if __name__ == "__main__":
    main()
