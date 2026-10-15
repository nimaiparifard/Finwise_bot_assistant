"""Persian-aware text normalisation, used for the keyword (BM25) side of the hybrid search."""

import re

_CHAR_MAP = str.maketrans({
    "ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه", "أ": "ا", "إ": "ا", "ؤ": "و",
    "‌": " ",  # ZWNJ: "دوره‌ها" -> "دوره ها", so the singular still matches
    "‏": " ", "‎": " ", "ـ": "",  # RTL/LTR marks and tatweel
    **{p: str(i) for i, p in enumerate("۰۱۲۳۴۵۶۷۸۹")},
    **{a: str(i) for i, a in enumerate("٠١٢٣٤٥٦٧٨٩")},
})
_DIACRITICS = re.compile(r"[ً-ٰٟ]")
_NUMBER_SEPARATORS = re.compile(r"(?<=\d)[,٬،](?=\d{3})")  # 65,000,000 -> 65000000


def normalize(text):
    """Unify Arabic/Persian letter variants and digits, drop diacritics, ZWNJ and number separators."""
    text = _DIACRITICS.sub("", text.translate(_CHAR_MAP))
    return _NUMBER_SEPARATORS.sub("", text).lower()


# English words people use for this center's topics -> the Persian terms the knowledge base is written in.
# Lets an English question match Persian passages in BM25 (the embeddings handle the rest).
GLOSSARY = {
    "course": "دوره", "courses": "دوره", "class": "دوره کلاس", "classes": "دوره کلاس",
    "workshop": "کارگاه", "workshops": "کارگاه", "offline": "آفلاین ضبط‌شده", "recorded": "آفلاین ضبط‌شده",
    "online": "آنلاین زنده", "live": "آنلاین زنده", "price": "قیمت هزینه", "prices": "قیمت هزینه",
    "cost": "قیمت هزینه", "costs": "قیمت هزینه", "fee": "شهریه", "fees": "شهریه", "tuition": "شهریه",
    "tax": "مالیات مالیاتی", "taxes": "مالیات مالیاتی", "audit": "حسابرسی", "auditing": "حسابرسی",
    "accounting": "حسابداری", "financial": "مالی", "finance": "مالی", "budgeting": "بودجه‌بندی",
    "excel": "اکسل", "dashboard": "داشبورد", "register": "ثبت‌نام", "registration": "ثبت‌نام",
    "enroll": "ثبت‌نام", "payment": "پرداخت", "pay": "پرداخت", "installment": "قسط اقساط",
    "installments": "قسط اقساط", "refund": "بازپرداخت انصراف", "cancel": "انصراف", "cancellation": "انصراف",
    "certificate": "گواهینامه گواهی", "instructor": "مدرس", "teacher": "مدرس", "professor": "مدرس",
    "schedule": "زمان برگزاری", "start": "شروع", "duration": "مدت", "hours": "ساعت", "contact": "تماس",
    "phone": "تلفن", "address": "آدرس", "beginner": "مبتدی", "beginners": "مبتدی", "discount": "تخفیف",
}


def expand_query(text):
    """Append the Persian equivalents of known English words so keyword search can find Persian passages."""
    words = re.findall(r"[a-z]+", text.lower())
    extra = dict.fromkeys(term for w in words for term in GLOSSARY.get(w, "").split())
    return f"{text}\n{' '.join(extra)}" if extra else text
