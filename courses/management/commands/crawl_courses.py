import json

import requests
from django.core.management.base import BaseCommand, CommandError

from courses.crawler import crawl_site, save_crawl


class Command(BaseCommand):
    help = "Crawl finwisecenter.com and upsert course information into MongoDB."

    def add_arguments(self, parser):
        parser.add_argument("--base-url", help="Override CRAWLER_BASE_URL.")
        parser.add_argument("--dry-run", action="store_true", help="Print what was parsed without saving.")
        parser.add_argument(
            "--force", action="store_true",
            help="Overwrite records that were edited in the admin (locked) and unlock them.",
        )
        parser.add_argument(
            "--keep-missing", action="store_true",
            help="Do not deactivate courses that no longer appear on the website.",
        )

    def handle(self, *args, **options):
        try:
            result = crawl_site(options["base_url"])
        except requests.RequestException as exc:
            raise CommandError(f"Could not reach the website: {exc}") from exc
        for error in result.errors:
            self.stderr.write(self.style.WARNING(f"Error: {error}"))
        if not result.courses:
            raise CommandError("No courses were parsed; nothing saved.")

        if options["dry_run"]:
            payload = {"courses": result.courses, "knowledge": result.knowledge}
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
            return

        stats = save_crawl(result, deactivate_missing=not options["keep_missing"], force=options["force"])
        self.stdout.write(self.style.SUCCESS(
            f"Parsed {len(result.courses)} courses. Created {stats['created']}, updated {stats['updated']}, "
            f"skipped {stats['skipped_locked']} locked, deactivated {stats['deactivated']}; "
            f"saved {stats['knowledge']} knowledge entries."
        ))
