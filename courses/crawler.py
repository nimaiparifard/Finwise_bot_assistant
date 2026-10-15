"""
Crawler for https://finwisecenter.com/.

Site layout (as of the time of writing):
  * index.cfm                 - home page; #schedule table lists courses open for registration
  * courselist.cfm?type=3|2   - online / offline course lists; cards carry category tags + summary
  * course.cfm?id=<n>         - course page; ".items" rows hold label/value pairs (duration, price, ...)
  * about.cfm                 - about the center

`crawl_site()` only fetches and parses; `save_crawl()` writes the result to the database.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Course, KnowledgeEntry

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; FinWiseCourseBot/1.0)"
LIST_PAGES = {"online": "courselist.cfm?type=3", "offline": "courselist.cfm?type=2"}
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# Fields of Course that the crawler owns. Admin-only fields (extra_notes, is_active, locked) are never touched.
CRAWLED_FIELDS = [
    "url", "title", "course_type", "categories", "instructor", "instructor_bio", "duration",
    "price_rial", "price_text", "start_date", "schedule", "class_days", "summary", "description",
    "image_url", "registration_open",
]


@dataclass
class CrawlResult:
    courses: dict = field(default_factory=dict)      # source_id -> dict of course fields
    knowledge: dict = field(default_factory=dict)    # key -> (title, content)
    errors: list = field(default_factory=list)


def clean(text):
    """Collapse runs of spaces and blank lines."""
    text = text or ""
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def block_text(node):
    """Readable multi-line text from an HTML block (keeps paragraph / <br> breaks)."""
    if node is None:
        return ""
    for br in node.find_all("br"):
        br.replace_with("\n")
    lines = [clean(p.get_text(" ")) for p in node.find_all(["p", "li", "h2", "h3", "h4"])]
    lines = [line for line in lines if line]
    return "\n".join(lines) if lines else clean(node.get_text("\n"))


def parse_price(text):
    digits = re.sub(r"[^\d]", "", text.translate(PERSIAN_DIGITS))
    if digits:
        return int(digits)
    return 0 if "رایگان" in text else None


def course_id_from_href(href):
    if not href or "course.cfm" not in href:
        return None
    values = parse_qs(urlparse(href).query).get("id")
    return int(values[0]) if values and values[0].isdigit() else None


class FinwiseCrawler:
    def __init__(self, base_url=None, timeout=20, delay=0.5):
        self.base_url = base_url or settings.CRAWLER_BASE_URL
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        retry = Retry(total=3, connect=3, read=3, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.mount("http://", HTTPAdapter(max_retries=retry))
        self.timeout = timeout
        self.delay = delay  # pause between requests to be polite to the site

    def fetch(self, path):
        url = urljoin(self.base_url, path)
        time.sleep(self.delay)
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        response.encoding = response.apparent_encoding if response.encoding in (None, "ISO-8859-1") else response.encoding
        return BeautifulSoup(response.text, "html.parser"), url

    # --- Pages --------------------------------------------------------------

    def parse_home(self, soup, result):
        """Registration schedule table and the 'why FinWise' section."""
        schedule = {}
        for row in soup.select("#schedule tbody tr"):
            cells = row.find_all("td")
            link = row.find("a", href=True)
            source_id = course_id_from_href(link["href"]) if link else None
            if source_id and len(cells) >= 3:
                when = " ".join(cells[1].get_text(" ").split())
                schedule[source_id] = {
                    "schedule": "" if when == "-" else when,
                    "registration_open": "ثبت" in cells[2].get_text(),
                }
        why = soup.select_one("section#why")
        if why:
            result.knowledge["why-finwise"] = ("چرا فین‌وایز؟ (مزایای مرکز)", block_text(why) or clean(why.get_text("\n")))
        return schedule

    def parse_list_page(self, soup):
        """Course cards on a list page -> {source_id: {"categories": [...], "summary": str}}."""
        tag_names = {
            chip.get("data-filter"): clean(chip.get_text())
            for chip in soup.select("#filters .chip")
            if chip.get("data-filter") not in (None, "all")
        }
        cards = {}
        for card in soup.select(".c_items"):
            link = card.find("a", href=True)
            source_id = course_id_from_href(link["href"]) if link else None
            if not source_id:
                continue
            tags = (card.get("data-tags") or "").split()
            summary = card.select_one(".text")
            cards[source_id] = {
                "categories": [tag_names[t] for t in tags if t in tag_names],
                "summary": clean(summary.get_text()) if summary else "",
            }
        return cards

    def parse_course(self, soup, url):
        info = soup.select_one("#courseInfo")
        if info is None or info.select_one(".course_title") is None:
            raise ValueError("course page has no #courseInfo/.course_title")

        items = {}
        for item in info.select(".items"):
            parts = item.find_all("div", recursive=False)
            if len(parts) >= 2:
                # Labels use tatweel for typography ("مـدت"); normalise them to plain keys ("مدت").
                label = re.sub(r"[\s\u0640]", "", parts[0].get_text())
                items[label] = clean(parts[1].get_text(" "))

        def item(*labels):
            for label in labels:
                if items.get(label):
                    return items[label]
            return ""

        type_text = item("نوعدوره")
        if "آفلاین" in type_text:
            course_type = Course.CourseType.OFFLINE
        elif "آنلاین" in type_text:
            course_type = Course.CourseType.ONLINE
        else:
            course_type = Course.CourseType.OTHER

        cost = info.select_one(".all_cost")
        # When a discount is shown the last price is the payable one.
        prices = [clean(span.get_text()) for span in cost.select("span")] if cost else []
        price_text = prices[-1] if prices else item("قیمت")

        cover = info.select_one(".leftInfo")
        image_url = ""
        if cover and (m := re.search(r"url\(([^)]+)\)", cover.get("style", ""))):
            image_url = urljoin(self.base_url, m.group(1).strip("'\""))

        mentor = soup.select_one("#mentorsBox")
        return {
            "url": url,
            "title": clean(info.select_one(".course_title").get_text()),
            "course_type": course_type,
            "instructor": item("مدرس") or (clean(mentor.find("h3").get_text()) if mentor and mentor.find("h3") else ""),
            "instructor_bio": block_text(mentor.select_one(".mentor_ex")) if mentor else "",
            "duration": item("مدت"),
            "price_rial": parse_price(price_text) if price_text else None,
            "price_text": price_text,
            "start_date": item("زمانشروع").translate(PERSIAN_DIGITS),
            "class_days": item("روزبرگزاری"),
            "description": block_text(soup.select_one(".trip_txt:not(.law_txt)")),
            "image_url": image_url,
        }

    def parse_course_shared_sections(self, soup, result):
        """Rules and payment instructions are identical on every course page; store them once."""
        if "rules" not in result.knowledge and (rules := soup.select_one(".law_txt")):
            result.knowledge["rules"] = ("قوانین و مقررات شرکت در دوره‌ها (انصراف، بازپرداخت شهریه و ...)", block_text(rules))
        if "registration" not in result.knowledge and (acc := soup.select_one("#acc_info")):
            for duplicate in acc.select(".what_mob"):  # mobile-only copy of the phone link
                duplicate.decompose()
            lines = [" ".join(acc.find("p").get_text(" ").split())] if acc.find("p") else []
            for row in acc.select("table tr")[1:]:
                cells = [clean(td.get_text(" ")) for td in row.find_all("td")]
                if len(cells) == 3:
                    lines.append(f"بانک: {cells[0]} | شماره کارت: {cells[1]} | صاحب حساب: {cells[2]}")
            result.knowledge["registration"] = ("نحوه ثبت‌نام و پرداخت شهریه", "\n".join(lines))

    def parse_contact(self, soup, result):
        lines = []
        for a in soup.select("header nav a[href]"):
            href = a["href"]
            if href.startswith("mailto:"):
                lines.append(f"ایمیل: {href[len('mailto:'):]}")
            elif "wa.me" in href:
                lines.append(f"ثبت‌نام و پشتیبانی از طریق واتساپ / تلفن: {clean(a.get_text(' '))} ({href})")
        for a in soup.select("footer a[href]"):
            if a["href"].startswith("http") and any(s in a["href"] for s in ("instagram", "t.me", "linkedin")):
                lines.append(f"{clean(a.get_text())}: {a['href']}")
        emails = re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", soup.get_text(" "))
        lines.extend(f"ایمیل: {email}" for email in emails)
        lines.append(f"وب‌سایت: {self.base_url}")
        result.knowledge["contact"] = ("اطلاعات تماس مرکز آموزش فین‌وایز", "\n".join(dict.fromkeys(lines)))

    # --- Orchestration -------------------------------------------------------

    def crawl(self):
        result = CrawlResult()
        home, _ = self.fetch("index.cfm")
        schedule = self.parse_home(home, result)
        self.parse_contact(home, result)
        course_ids = {course_id_from_href(a["href"]) for a in home.find_all("a", href=True)} - {None}

        cards = {}
        for name, path in LIST_PAGES.items():
            try:
                soup, _ = self.fetch(path)
            except requests.RequestException as exc:
                result.errors.append(f"{name} list: {exc}")
                continue
            cards.update(self.parse_list_page(soup))
        course_ids |= set(cards)

        try:
            about, _ = self.fetch("about.cfm")
            article = about.select_one("#p_about #managers") or about.select_one("#p_about")
            if article:
                result.knowledge["about"] = ("درباره مرکز آموزش فین‌وایز", block_text(article))
        except requests.RequestException as exc:
            result.errors.append(f"about: {exc}")

        for source_id in sorted(course_ids):
            try:
                soup, url = self.fetch(f"course.cfm?id={source_id}")
                data = self.parse_course(soup, url)
                self.parse_course_shared_sections(soup, result)
            except (requests.RequestException, ValueError) as exc:
                result.errors.append(f"course {source_id}: {exc}")
                continue
            data.update(cards.get(source_id, {"categories": [], "summary": ""}))
            data.update(schedule.get(source_id, {"schedule": "", "registration_open": False}))
            result.courses[source_id] = data
            logger.info("Parsed course %s: %s", source_id, data["title"])
        return result


def crawl_site(base_url=None):
    return FinwiseCrawler(base_url).crawl()


@transaction.atomic
def save_crawl(result, deactivate_missing=True, force=False):
    """Upsert crawled data. Locked records are skipped unless force=True. Returns a stats dict."""
    now = timezone.now()
    stats = {"created": 0, "updated": 0, "skipped_locked": 0, "deactivated": 0, "knowledge": 0}

    for source_id, data in result.courses.items():
        course = Course.objects.filter(source_id=source_id).first()
        if course is None:
            Course.objects.create(source_id=source_id, last_crawled_at=now, **data)
            stats["created"] += 1
            continue
        course.last_crawled_at = now
        if course.locked and not force:
            course.save(update_fields=["last_crawled_at"])
            stats["skipped_locked"] += 1
            continue
        for name in CRAWLED_FIELDS:
            setattr(course, name, data[name])
        if force:
            course.locked = False
        course.save()
        stats["updated"] += 1

    if deactivate_missing and result.courses:
        missing = Course.objects.filter(source_id__isnull=False, is_active=True, locked=False).exclude(
            source_id__in=list(result.courses)
        )
        stats["deactivated"] = missing.update(is_active=False, registration_open=False)

    for key, (title, content) in result.knowledge.items():
        if not content:
            continue
        entry = KnowledgeEntry.objects.filter(key=key).first()
        if entry and entry.locked and not force:
            continue
        if entry is None:
            entry = KnowledgeEntry(key=key, source=KnowledgeEntry.Source.CRAWLED)
        entry.title, entry.content = title, content
        if force:
            entry.locked = False
        entry.save()
        stats["knowledge"] += 1
    return stats
