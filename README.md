# FinWise Course Bot

An assistant for [FinWise Center](https://finwisecenter.com/) that answers questions about the center's courses on **Telegram** and on a **web chat page**, using an LLM (OpenAI or any OpenAI-compatible API, or Claude). The interface and the admin panel are in Persian.

- **Crawler**: pulls courses (title, type, price, duration, start date, weekday, instructor, category, description, registration status) and center info (about, contact, rules/refunds, payment) from the website into MongoDB.
- **Hybrid RAG**: for each question it retrieves the most relevant passages with BM25 (keywords) and vector search (meaning), merged with rank fusion, using LlamaIndex.
- **Bot and web chat**: answer only from the data in MongoDB. Anything they don't know is handed to a human, never made up.
- **Admin panel** (Django admin, Persian, right-to-left): edit courses and knowledge entries, run the crawler, read every conversation, and answer escalated questions.

> See the demo: open [`demo/index.html`](demo/index.html) in a browser. It has 10 real questions with answers, screenshots of the chat and the admin panel, and a 2-minute video (`demo/demo.mp4`).

## How it works

```
question (Telegram or web chat)
   │
   ├─► BM25 (Persian-normalised) ──┐
   └─► vector search (embeddings) ─┴─► rank fusion ─► top passages
                                                        │  + each course's facts (price, dates, status)
                                                        ▼
        system prompt + course catalog  +  retrieved passages  +  recent chat history ──► LLM
                                                        │
                                   can answer? ─────────┤
                                      yes │             │ no
                                          ▼             ▼
                                   reply to user   "forwarded to our team" + Question saved (Telegram)
                                                   + notification in the admin Telegram chat
                                                                │
                            admin replies in Telegram, or writes the answer in Django admin
                                                                ▼
                                          answer delivered to the user (optionally saved to the FAQ)
```

Questions answered by an admin can be saved to the FAQ (knowledge base), so the bot can answer them itself next time.

Details worth knowing:

- **Catalog + passages**: the prompt always contains a one-line-per-course catalog (with the total and open counts) that stays cached; the retrieved passages are attached to the user's message.
- **Prices**: the model only ever sees Toman, so it cannot mistake a Rial figure for Toman.
- **Languages**: Persian by default. English questions get English answers, and an English-to-Persian keyword glossary helps them find Persian passages.
- **Plain text**: replies are plain text for Telegram; any Markdown links the model produces are flattened to bare URLs.
- **Fallbacks**: if the embedding service is down, search falls back to BM25 only. If the LLM fails, the question is handed to an admin so the user always gets an answer.

## Project layout

| Path | What |
|---|---|
| `config/` | Django settings (MongoDB via `django-mongodb-backend`), URLs |
| `courses/crawler.py` | Website crawler + upsert into MongoDB |
| `courses/models.py` | `Course`, `KnowledgeEntry` |
| `courses/admin.py` | Course / knowledge admin, "crawl website" button |
| `bot/prompts.py` | The system prompt and the helper that attaches retrieved passages |
| `bot/llm.py` | LLM call (OpenAI-compatible or Claude): grounded answer or hand-off (JSON output) |
| `bot/knowledge.py` | Course catalog, full knowledge text (when RAG is off), chat history |
| `bot/rag/` | Hybrid search: `nodes.py` (chunking), `text.py` (Persian normalisation, glossary), `retriever.py` (BM25 + vector + fusion) |
| `bot/telegram_bot.py` | Telegram handlers (users + admin chat) |
| `bot/models.py`, `bot/admin.py` | Stored messages (per channel) and questions; conversation viewer; answer from the admin |
| `webchat/` | Web chat page (`templates/webchat/index.html`) and its API |
| `templates/admin/` | Admin look and Persian fixes |
| `demo/` | Demo page, screenshots and video |
| `mongo_migrations/` | Migrations for Django's built-in apps on MongoDB |

## Quick start (Windows)

Start Docker Desktop, then double-click **`start.bat`**. It sets everything up and starts the admin panel and the bot. It's safe to run every time; steps that are already done are skipped. The manual steps are below.

## Setup

Requirements: Python 3.12+, Docker (or any MongoDB 6+), a Telegram bot token from [@BotFather](https://t.me/BotFather), and an API key for an OpenAI-compatible API (or Anthropic). Embeddings also need an OpenAI-compatible `/embeddings` endpoint (see [RAG settings](#rag-settings)).

```bash
# 1. MongoDB
docker compose up -d

# 2. Python environment
python -m venv .venv
.venv\Scripts\activate            # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt

# 3. Configuration
copy .env.example .env            # then fill in TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL

# 4. Database + admin user
python manage.py migrate
python manage.py createsuperuser

# 5. Load courses from the website
python manage.py crawl_courses
```

## Running

Run these in two terminals:

```bash
python manage.py runserver        # web chat at http://127.0.0.1:8000/  ·  admin at http://127.0.0.1:8000/admin/
python manage.py runbot           # Telegram bot (long polling)
```

The web chat works without the Telegram bot running. The first question after a start builds the search index (a few seconds).

### Web chat

Open `http://127.0.0.1:8000/`. It is a Persian right-to-left chat with suggested questions, light/dark themes, copy buttons, retry on errors, and a saved history in the browser. Every web conversation is stored, so admins can read it.

Note: when the web chat can't answer, it tells the visitor the question was forwarded, but only Telegram conversations create a staff question that can be answered from the admin. Web hand-offs are visible in the conversation view (flagged "ارجاع به کارشناس").

### Admin panel

`http://127.0.0.1:8000/admin/` (Persian). Main areas:

- **دوره‌ها** (Courses) and **اطلاعات و پرسش‌های متداول** (Knowledge): edit what the bot knows; run the crawler.
- **سؤالات کاربران** (User questions): questions the bot handed off. Write the answer and save: it is sent to the user on Telegram immediately.
- **پیام‌های گفتگو** (Chat messages): all messages from both channels, filterable by channel, role and hand-off. Click "مشاهده گفتگو" on a row to read the whole conversation as a chat thread.

### Setting up the admin chat (Telegram)

1. Create a Telegram group for your team and add the bot. You can also just use your own private chat with the bot.
2. Send `/chatid` in that chat. Put the number in `.env` as `TELEGRAM_ADMIN_CHAT_ID` and restart `runbot`.
3. In a group, either make the bot an admin or turn off its privacy mode (@BotFather → Bot Settings → Group Privacy). Otherwise the bot can't see your replies.

In the admin chat:
- **Reply** to a question notification to answer it. Start the reply with `+faq` to also add it to the knowledge base.
- `/pending` lists unanswered questions.

### Bot commands for users
- `/start`: welcome message
- `/courses`: courses currently open for registration
- Any other text is treated as a question.

## Keeping data up to date

- Re-crawl at any time with `python manage.py crawl_courses`, or the crawl button on the Courses page. Schedule it, e.g. daily with cron or Windows Task Scheduler, to stay in sync.
- **Editing a course in the admin locks it**, so the crawler won't overwrite your change. Use the unlock action to let the crawler manage it again. `crawl_courses --force` overwrites and unlocks everything.
- Courses that disappear from the website are deactivated, i.e. hidden from the bot. `--keep-missing` disables this.
- `extra_notes` on a course is never touched by the crawler. Use it for information that isn't on the website.
- Add general info (discount policy, installments, certificates…) as **Knowledge entries**. The bot uses all active entries.
- `crawl_courses --dry-run` prints what would be saved, without saving.
- The search index refreshes itself within about 20 seconds of any change to courses or knowledge entries (only new or changed text is re-embedded).

## LLM settings

Set `LLM_PROVIDER` in `.env`:

**`openai`** (default): OpenAI, or any server with an OpenAI-compatible API (a proxy, OpenRouter, Azure-style gateway, vLLM, Ollama, LiteLLM…).
```
LLM_PROVIDER=openai
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://your-server.com/v1   # leave empty for api.openai.com
OPENAI_MODEL=gpt-4o-mini                     # any model name your server offers
```
- Uses the Chat Completions endpoint (`{OPENAI_BASE_URL}/chat/completions`).
- Asks for strict JSON-schema output. If the server rejects that, it automatically retries in plain JSON mode, which more servers support.

**`anthropic`**: Claude (`ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_EFFORT`), with prompt caching and server-side refusal fallbacks.

With either provider, the model returns `{can_answer, answer, reason}`. Refusals, API errors, truncated or unreadable output are all handed to an admin, so a user is never left without a response. The hand-off reason shown to staff is in Persian.

A small model such as `gpt-4o-mini` works but occasionally slips on arithmetic and instructions (for example, counting how many more courses exist); a stronger model gives more reliable answers.

## RAG settings

| Variable | Default | Meaning |
|---|---|---|
| `RAG_ENABLED` | `true` | `false` puts the whole knowledge base in every prompt (fine for a tiny catalogue, costly as it grows) |
| `RAG_TOP_K` | `6` | Passages sent to the LLM per question |
| `RAG_CANDIDATES` | `12` | Candidates per retriever before rank fusion |
| `RAG_CHUNK_WORDS` / `RAG_CHUNK_OVERLAP` | `180` / `30` | Chunk size and overlap (in words) |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Model on an OpenAI-compatible `/embeddings` endpoint |
| `EMBEDDING_API_KEY`, `EMBEDDING_BASE_URL` | the `OPENAI_*` values | Needed even when `LLM_PROVIDER=anthropic` |

If embeddings fail, the bot keeps working with keyword (BM25) search only.

## Tests

```bash
python manage.py test courses bot webchat
```

The tests use saved copies of the website's pages (`courses/test_pages/`) and mock Telegram, the LLM and the embedding model (a small offline embedder). They need MongoDB running but no API keys.

## Security notes

- Never commit real API keys. `.env` holds secrets; `.env.example` must only contain placeholders.
- Change `DJANGO_SECRET_KEY` and set `DJANGO_DEBUG=false` before exposing the site.
