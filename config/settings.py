"""
Django settings for the FinWise course bot.

Configuration comes from environment variables (loaded from a .env file in
the project root if present). See .env.example for the full list.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

INSTALLED_APPS = [
    "config.apps.MongoAdminConfig",
    "config.apps.MongoAuthConfig",
    "config.apps.MongoContentTypesConfig",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_mongodb_backend",
    "courses",
    "bot",
    "webchat",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django_mongodb_backend",
        "HOST": os.getenv("MONGODB_URI", "mongodb://localhost:27017/"),
        "NAME": os.getenv("MONGODB_DB", "finwise"),
        "OPTIONS": {"serverSelectionTimeoutMS": 3000},  # fail fast when MongoDB is down
    },
}
DATABASE_ROUTERS = ["django_mongodb_backend.routers.MongoRouter"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "fa"
TIME_ZONE = "Asia/Tehran"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django_mongodb_backend.fields.ObjectIdAutoField"
MIGRATION_MODULES = {
    "admin": "mongo_migrations.admin",
    "auth": "mongo_migrations.auth",
    "contenttypes": "mongo_migrations.contenttypes",
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"httpx": {"level": "WARNING"}},
}

# --- Crawler -----------------------------------------------------------------
CRAWLER_BASE_URL = os.getenv("CRAWLER_BASE_URL", "https://finwisecenter.com/")

# --- Telegram bot ------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# Chat (user or group) that receives questions the bot could not answer.
# Admins answer by replying to the forwarded message in that chat.
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")

# --- LLM ----------------------------------------------------------------------
# "openai" (OpenAI or any OpenAI-compatible server) or "anthropic" (Claude).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").strip().lower()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# Custom endpoint, e.g. https://my-proxy.example.com/v1 . Empty = official https://api.openai.com/v1
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# The Anthropic SDK reads ANTHROPIC_API_KEY from the environment.
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
ANTHROPIC_EFFORT = os.getenv("ANTHROPIC_EFFORT", "low")

# How many previous user/assistant turns to send along with each question.
LLM_HISTORY_TURNS = int(os.getenv("LLM_HISTORY_TURNS", "6"))

# --- RAG (hybrid BM25 + vector search, LlamaIndex) -----------------------------------
# When off, the whole knowledge base is put in every prompt (fine for a small catalogue).
RAG_ENABLED = env_bool("RAG_ENABLED", True)
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "6"))  # passages sent to the LLM per question
RAG_CANDIDATES = int(os.getenv("RAG_CANDIDATES", "12"))  # per retriever, before rank fusion
RAG_CHUNK_WORDS = int(os.getenv("RAG_CHUNK_WORDS", "180"))
RAG_CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "30"))
# Embeddings use any OpenAI-compatible /embeddings endpoint; defaults to the OPENAI_* settings above.
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY") or OPENAI_API_KEY
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL") or OPENAI_BASE_URL
