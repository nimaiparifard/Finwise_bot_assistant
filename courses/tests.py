from pathlib import Path

from bs4 import BeautifulSoup
from django.test import TestCase

from .crawler import CrawlResult, FinwiseCrawler, save_crawl
from .models import Course, KnowledgeEntry

PAGES = Path(__file__).parent / "test_pages"


def page(name):
    return BeautifulSoup((PAGES / name).read_text(encoding="utf-8"), "html.parser")


class ParserTests(TestCase):
    def setUp(self):
        self.crawler = FinwiseCrawler(base_url="https://finwisecenter.com/")

    def test_parse_offline_course(self):
        data = self.crawler.parse_course(page("course_28.html"), "https://finwisecenter.com/course.cfm?id=28")
        self.assertEqual(data["title"], "کارگاه آفلاین مصون‌سازی و نحوه حسابداری آن")
        self.assertEqual(data["course_type"], Course.CourseType.OFFLINE)
        self.assertEqual(data["price_rial"], 5_000_000)
        self.assertEqual(data["duration"], "5 ساعت")
        self.assertEqual(data["instructor"], "دکتر ابراهیم نوروزبیگی")
        self.assertIn("Hedging", data["description"])
        self.assertTrue(data["image_url"].endswith("28-895.webp"))

    def test_parse_online_course_with_start_date(self):
        data = self.crawler.parse_course(page("course_66.html"), "u")
        self.assertEqual(data["course_type"], Course.CourseType.ONLINE)
        self.assertEqual(data["start_date"], "1405/07/27")
        self.assertEqual(data["price_rial"], 30_000_000)

    def test_shared_sections_keep_rule_numbering(self):
        result = CrawlResult()
        self.crawler.parse_course_shared_sections(page("course_28.html"), result)
        self.assertIn("۱ـ۱.", result.knowledge["rules"][1])
        registration = result.knowledge["registration"][1]
        self.assertIn("6037-9972-5003-1607", registration)
        self.assertEqual(registration.count("0990"), 1)  # mobile duplicate removed

    def test_list_page_categories(self):
        cards = self.crawler.parse_list_page(page("courselist_online.html"))
        self.assertEqual(cards[66]["categories"], ["حسابداری مدیریت"])
        self.assertTrue(cards[66]["summary"])

    def test_home_schedule(self):
        schedule = self.crawler.parse_home(page("index.html"), CrawlResult())
        self.assertTrue(schedule[66]["registration_open"])
        self.assertEqual(schedule[66]["schedule"], "27 مهر تا 29 آبان")
        self.assertEqual(schedule[28]["schedule"], "")


def course_data(**overrides):
    data = {
        "url": "https://finwisecenter.com/course.cfm?id=1", "title": "Course", "course_type": "online",
        "categories": [], "instructor": "", "instructor_bio": "", "duration": "", "price_rial": 100,
        "price_text": "100 ریال", "start_date": "", "schedule": "", "class_days": "", "summary": "",
        "description": "", "image_url": "", "registration_open": True,
    }
    data.update(overrides)
    return data


class SaveCrawlTests(TestCase):
    def test_create_update_and_lock(self):
        save_crawl(CrawlResult(courses={1: course_data()}, knowledge={"rules": ("Rules", "v1")}))
        course = Course.objects.get(source_id=1)
        course.price_rial = 5
        course.locked = True
        course.save()

        stats = save_crawl(CrawlResult(courses={1: course_data(price_rial=200)}, knowledge={"rules": ("Rules", "v2")}))
        self.assertEqual(stats["skipped_locked"], 1)
        self.assertEqual(Course.objects.get(source_id=1).price_rial, 5)
        self.assertEqual(KnowledgeEntry.objects.get(key="rules").content, "v2")

        save_crawl(CrawlResult(courses={1: course_data(price_rial=200)}), force=True)
        course = Course.objects.get(source_id=1)
        self.assertEqual(course.price_rial, 200)
        self.assertFalse(course.locked)

    def test_missing_courses_are_deactivated(self):
        save_crawl(CrawlResult(courses={1: course_data(), 2: course_data(title="Old")}))
        stats = save_crawl(CrawlResult(courses={1: course_data()}))
        self.assertEqual(stats["deactivated"], 1)
        self.assertFalse(Course.objects.get(source_id=2).is_active)
