# Recall

Photograph business cards, keep every detail, and ask questions like
*"give me details of all furniture suppliers"* or *"emails of CEOs based in Guangzhou"*.

- **Web app / PWA** — works in any phone or desktop browser; "Add to Home Screen" for an app icon.
- **Claude reads the card** (front and optional back, Chinese/English bilingual cards included) and fills in name, title, company, phones, emails, address, WeChat, etc. City/province/country are normalised to English (广州 → Guangzhou).
- **You add the context** — industry and description (what they supply, where you met) are typed in while the card is being read. Your typing is never overwritten by the AI.
- **Plain-English search** — Claude turns your question into a filter; the app runs it on your own contacts and shows a table you can export to CSV or Excel.
- **Strictly private per user** — every user's cards are visible only to that user.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # set ANTHROPIC_API_KEY and ADMIN_PASSWORD
uvicorn app.main:get_app --factory --port 8000
```

Open http://localhost:8000 and sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`
(the admin account is created on first start). Use the **Users** tab to add accounts.

For plain-HTTP testing from a phone on your LAN set `COOKIE_SECURE=false`
(phones also need HTTPS for the PWA install prompt — use a real deployment for that).

Command-line account management:

```bash
python -m app.manage create-user alice           # add --admin for an admin
python -m app.manage reset-password alice
python -m app.manage list-users
```

## Deploying

Any small server or container host with a persistent disk works (~20 users is a tiny load).

```bash
docker build -t recall .
docker run -d -p 8000:8000 -v recall-data:/data --env-file .env recall
```

Put it behind HTTPS (Caddy, nginx, or your platform's TLS). Run **one** worker process:
card-reading jobs are tracked in memory. Back up the data volume (`DATA_DIR`) — it holds
everything.

## Costs

| Step | Model (configurable) | Approx. cost |
|---|---|---|
| Reading a card | `claude-sonnet-5-5` | ~1.2¢ per card |
| Checking a photo is a business card | `claude-haiku-5-5` | ~0.01¢ per photo |
| Answering a question | `claude-haiku-5-5` | ~0.03¢ per question |

At ~50 cards a month the AI bill is well under $1/month. Each user sees their own usage
under their account menu; admins see per-user AI cost on the Users page.
If Sonnet's safety classifier ever declines a card, the request is retried on a fallback
model automatically (`EXTRACT_FALLBACKS=true`); if reading still fails, the form stays
available for manual entry.

## Abuse protection

The AI is only used for reading business cards and searching your contacts — not as a general chatbot.

- **Only business cards reach the card reader.** Each upload is first checked by a cheap model on a
  small thumbnail; anything that isn't a business card (screenshots, documents, receipts, ID cards,
  selfies…) is rejected before the card-reading model runs. The card reader double-checks, too, and
  its output is capped and clipped to contact-field sizes.
- **Only contact searches reach the query model.** Questions are limited to 200 characters on one line
  and screened locally for prompt injection and chatbot-style requests ("write a poem", "ignore your
  instructions", links, code…) — those never reach the AI. The model must then confirm the question is a
  contact search; anything else is refused.
- **No AI-written text is shown.** Search results contain only your contacts plus a summary of the filter
  built by the server from short search terms, so the model can't be used to answer other questions.
- **Per-user limits** (0 disables one): `CARDS_PER_DAY` (100), `QUERIES_PER_DAY` (300),
  `AI_CALLS_PER_MINUTE` (10), `MAX_PENDING_JOBS` (3). Rejected questions count towards the limits.

## Privacy model

- **Separate storage per user.** Each user has their own SQLite database and photo folder
  under `DATA_DIR/users/<user-id>/`. A request can only open the folder of the signed-in
  user; no code path reads another user's folder.
- **Identity comes only from the server-side session.** Never from request data, and never from the AI.
- **Admins manage accounts, not cards.** They can add, disable, reset passwords and delete
  users (deleting a user also deletes all their cards and photos), but there is no way for
  an admin to view, search or export anyone else's cards.
- Other users' records return **404**, so their existence isn't revealed.
- Card photos are only served to their owner, re-encoded on upload (EXIF/GPS metadata
  stripped), and never cached by the PWA. Signing out reloads the page so nothing stays on screen.
- Passwords are hashed with scrypt; sessions are HttpOnly/Secure/SameSite cookies that can
  be revoked (logout, disable, password change); failed logins are rate limited; API writes
  require a custom header (CSRF protection); strict Content-Security-Policy.
- Card photos and question text are sent to the Anthropic API for processing. By default
  Anthropic does not train models on API data.

## How search works

Claude Haiku receives your question plus *your own* list of industries and tags and returns
a structured filter (industries/keywords, title words, exclusions such as "Deputy/副",
cities with native spellings, required fields, columns to show). The app turns that into
parameterised SQL over your contacts — the AI never writes SQL. Matching is
case-insensitive substring matching across both English and native-script fields, which
handles short Chinese terms like 家具. The one-line explanation above the results shows how
your question was interpreted.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

Tests use a fake AI, so no API key is needed. `tests/test_isolation.py` covers the
per-user privacy guarantees.

```
app/
  main.py      FastAPI routes + security middleware
  auth.py      session → user resolution, per-user store dependency
  accounts.py  users, sessions, login throttling, AI usage (no card data)
  store.py     per-user SQLite storage, search, images
  ai.py        Claude calls (card reading, question → filter), image preparation
  schemas.py   Pydantic models
  export.py    CSV / Excel
  static/      frontend (vanilla JS PWA)
```
