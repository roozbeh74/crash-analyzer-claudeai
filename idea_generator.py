#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""مولد ایده‌ی محتوا از ترندهای روز
ورودی: trends/trends-YYYY-MM-DD.jsonl (آخرین فایل موجود)
خروجی:
  - ideas/ideas-YYYY-MM-DD.jsonl (یک ایده در هر خط)
  - ideas/ideas-YYYY-MM-DD.md (خلاصه‌ی خوانا برای مرور سریع)

برای هر حوزه، از برترین ترندهای امروز یک ایده‌ی ویدیوی یوتیوب و یک ایده‌ی پست وب‌سایت
ساخته می‌شود. موضوعات سیاسی/نظامی از فهرست SKIP_TERMS رد می‌شوند (برای ایمنی
درآمدزایی و تمرکز بر محتوای همیشه‌سبز). اگر سکرت MISTRAL_API_KEY تنظیم شده باشد،
عنوان‌ها با مدل زبانی غنی‌تر بازنویسی می‌شوند (در صورت خطا، همان قالب آماده استفاده می‌شود).
"""

import glob
import json
import os
import urllib.request
from datetime import datetime, timezone

TRENDS_DIR = "trends"
IDEAS_DIR = "ideas"
PER_NICHE = 6

# موضوعات حساس که برای درآمدزایی و جلب مخاطب عمومی مناسب نیستند
SKIP_TERMS = (
    "نتانیاهو",
    "لیبرمن",
    "بازداشت",
    "کشته",
    "جنگ",
    "حمله",
    "مزدور",
    "دشمن",
    "رژیم",
    "سیاسی",
    "انتخابات",
)


def latest_trends_file():
    files = sorted(glob.glob(os.path.join(TRENDS_DIR, "trends-*.jsonl")))
    if not files:
        raise SystemExit("هیچ فایل ترندی پیدا نشد؛ اول trend_collector.py را اجرا کن")
    return files[-1]


def load_rows(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def top_trends_per_niche(rows, per_niche=PER_NICHE):
    seen = set()
    by_niche = {}
    rows = sorted(rows, key=lambda r: (r.get("rank") or 999, r.get("collected_at") or ""))
    for r in rows:
        title = (r.get("title") or "").strip()
        if not title:
            continue
        if any(term in title for term in SKIP_TERMS):
            continue
        niche = r.get("niche") or "عمومی/سرگرمی"
        key = (title + "|" + niche).lower()
        if key in seen:
            continue
        seen.add(key)
        by_niche.setdefault(niche, []).append(r)
    return {n: items[:per_niche] for n, items in by_niche.items()}


def make_ideas(date, niches):
    ideas = []
    for niche, trends in niches.items():
        tpl = TEMPLATES.get(niche) or TEMPLATES["عمومی/سرگرمی"]
        for rank, t in enumerate(trends, 1):
            label = t["title"]
            base = {
                "date": date,
                "niche": niche,
                "trend_rank": rank,
                "trend": label,
                "trend_source": t.get("source"),
                "trend_url": t.get("url"),
            }
            ideas.append(dict(
                base,
                platform="youtube",
                title=tpl["youtube"]["title"].format(t=label),
                format=tpl["youtube"]["format"],
            ))
            ideas.append(dict(
                base,
                platform="website",
                title=tpl["website"]["title"].format(t=label),
                angle=tpl["website"]["angle"],
            ))
    return ideas


def enrich_with_mistral(ideas, api_key):
    """اگر MISTRAL_API_KEY تنظیم شده باشد عنوان‌ها را جذاب‌تر بازنویسی می‌کند."""
    if not api_key or not ideas:
        return ideas
    try:
        trends = []
        seen = set()
        for i in ideas:
            if i["trend"] not in seen:
                seen.add(i["trend"])
                trends.append({"trend": i["trend"], "niche": i["niche"]})
        payload = {
            "model": "mistral-small-latest",
            "temperature": 0.7,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "برای هر ترند زیر، یک عنوان جذاب فارسی برای ویدیوی یوتیوب و "
                        "یک عنوان جذاب فارسی برای پست وب‌سایت بنویس. فقط JSON خروجی بده "
                        'با ساختار: {"ideas": [{"trend": "...", "youtube_title": "...", '
                        '"website_title": "..."}]}. ترندهای را دقیقاً همان‌طور برگردان.\n'
                        + json.dumps(trends, ensure_ascii=False)
                    ),
                }
            ],
        }
        req = urllib.request.Request(
            "https://api.mistral.ai/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"]
        parsed = json.loads(text)
        by_trend = {x["trend"]: x for x in parsed.get("ideas", []) if x.get("trend")}
        updated = 0
        for idea in ideas:
            hit = by_trend.get(idea["trend"])
            if not hit:
                continue
            if idea["platform"] == "youtube" and hit.get("youtube_title"):
                idea["title"] = hit["youtube_title"]
                idea["title_enriched"] = True
                updated += 1
            elif idea["platform"] == "website" and hit.get("website_title"):
                idea["title"] = hit["website_title"]
                idea["title_enriched"] = True
                updated += 1
        print("[ok] mistral enrichment: " + str(updated) + " titles updated")
    except Exception as exc:
        print("[warn] mistral enrichment failed, keeping template titles: " + str(exc))
    return ideas


def write_md(date, niches, ideas):
    lines = ["# ایده‌های محتوای روز — " + date, ""]
    lines.append("خروجی خودکار از ترندهای همان روز؛ هر ترند یک ایده‌ی یوتیوب و یک ایده‌ی پست وب‌سایت.")
    lines.append("")
    for niche, trends in niches.items():
        lines.append("## " + niche)
        lines.append("")
        for t in trends:
            lines.append("### " + str(t["rank"]) + ". " + t["title"])
            meta = ["منبع: " + str(t.get("source"))]
            if t.get("url"):
                meta.append("[لینک](" + t["url"] + ")")
            lines.append(meta_str := " — ".join(meta))
            for idea in ideas:
                if idea["trend"] != t["title"] or idea["niche"] != niche:
                    continue
                if idea["platform"] == "youtube":
                    extra = idea.get("format", "")
                    lines.append("- یوتیوب: **" + idea["title"] + "** (" + extra + ")")
                else:
                    extra = idea.get("angle", "")
                    lines.append("- وب‌سایت: **" + idea["title"] + "** (" + extra + ")")
            lines.append("")
    return "\n".join(lines)


def main():
    trends_path = latest_trends_file()
    date = os.path.basename(trends_path).replace("trends-", "").replace(".jsonl", "")
    rows = load_rows(trends_path)
    niches = top_trends_per_niche(rows)
    ideas = make_ideas(date, niches)

    api_key = (os.environ.get("MISTRAL_API_KEY") or "").strip()
    if api_key:
        ideas = enrich_with_mistral(ideas, api_key)

    os.makedirs(IDEAS_DIR, exist_ok=True)
    jsonl_path = os.path.join(IDEAS_DIR, "ideas-" + date + ".jsonl")
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for idea in ideas:
            f.write(json.dumps(idea, ensure_ascii=False) + "\n")

    md_path = os.path.join(IDEAS_DIR, "ideas-" + date + ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(write_md(date, niches, ideas))

    print("[done] " + str(len(ideas)) + " ideas from " + trends_path)
    print("[done] wrote " + jsonl_path + " and " + md_path)


if __name__ == "__main__":
    main()
