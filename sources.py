import re
from datetime import date, datetime, timedelta


CATEGORY_KEYWORDS = [
    ("Quantitative Finance", ("quant", "trading", "trader")),
    ("Data Science", ("data scien", "machine learning", "ai engineer", "data analy", "data engineer", " ml ")),
    ("Product Management", ("product manager", "product management", "product specialist")),
    ("Hardware Engineering", ("hardware", "firmware", "asic", "silicon", "electrical")),
    ("Software Engineering", ("software engineer", "swe", "backend", "frontend", "full stack", "web developer")),
]


def classify_category(text: str) -> str:
    t = f" {text.lower()} "
    for category, keywords in CATEGORY_KEYWORDS:
        if any(kw in t for kw in keywords):
            return category
    return "Other"


def date_from_age(age_text: str, today: date):
    age_text = age_text.strip()
    m = re.match(r"^(\d+)d$", age_text)
    if m:
        days = int(m.group(1))
        label = "today" if days == 0 else f"{days}d ago"
        return today - timedelta(days=days), days, label
    m = re.match(r"^(\d+)mo$", age_text)
    if m:
        months = int(m.group(1))
        days = months * 30
        return today - timedelta(days=days), days, f"~{months}mo ago"
    return None, None, ""


def age_from_date(posted: date, today: date):
    days = max((today - posted).days, 0)
    if days == 0:
        return 0, "today"
    if days < 30:
        return days, f"{days}d ago"
    months = round(days / 30)
    return days, f"~{months}mo ago"


def parse_vansh_date(date_text: str, today: date) -> date:
    parsed = datetime.strptime(f"{date_text.strip()} {today.year}", "%b %d %Y").date()
    if abs((parsed - today).days) > 3:
        parsed = parsed.replace(year=parsed.year - 1)
    return parsed


def normalize_key(company: str, role: str) -> str:
    text = f"{company} {role}".lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
