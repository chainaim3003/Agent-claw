# SETUP.md — Step-by-step

Target: `C:\SATHYA\CHAINAIM3003\mcp-servers\Agent-claw\Restaurant-claw`

(`Agent-claw\` is the parent umbrella; this project lives in the `Restaurant-claw\`
subfolder. Sibling agents — e.g. `Hotel-claw\` — would live next to it.)

Everything below assumes Windows (PowerShell). Linux/macOS commands are
analogous — replace `py -3` with `python3` and `\` paths with `/`.

---

## 1. Place the project

Place the project so the path is exactly:

```
C:\SATHYA\CHAINAIM3003\mcp-servers\Agent-claw\Restaurant-claw
```

Open PowerShell there:

```powershell
cd C:\SATHYA\CHAINAIM3003\mcp-servers\Agent-claw\Restaurant-claw
```

---

## 2. Create a virtual environment and install deps

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Confirm:

```powershell
python -c "import agent_claw; print(agent_claw.__version__)"
```

Should print `1.0.0`.

---

## 3. Configure environment

```powershell
copy .env.example .env
notepad .env
```

You must set the four sections that correspond to the services you'll use:

### 3a. Nominatim (required — geocoding)
Just put a real contact in the User-Agent. No account needed.
```
NOMINATIM_UA=AgentClaw/1.0 (you@yourdomain.com)
```

### 3b. Cal.com (required — booking + availability)
Choose one path:

- **Cloud (fastest to set up):** Sign up at https://cal.com.
  - Create an event type representing the venue you want bookable.
  - Generate an API key: `Settings → Developer → API keys`.
  - Find your event type id: open the event type, copy the integer in the URL.
  - Fill `CALCOM_API_KEY` and `CALCOM_EVENT_TYPE_ID`. Keep
    `CALCOM_BASE_URL=https://api.cal.com`.

- **Self-host (free):** Run Cal.com locally via Docker (`calcom/cal.com:latest`
  plus Postgres). After the setup wizard, create an event type and API key the
  same way. Set `CALCOM_BASE_URL` to your local URL (for example
  `https://localhost:3000/api`).

Pinned API versions in `.env.example` (`2024-09-04` for slots, `2024-08-13`
for bookings) come from Cal.com's docs and must not be changed unless their
docs change.

### 3c. Twilio (required — SMS)
- Create an account at https://twilio.com. Get the Account SID, Auth Token,
  and a phone number from the console.
- If you're on a trial, verify your own phone number under
  `Phone Numbers → Manage → Verified Caller IDs` so you can send to it.
- US local numbers: complete A2P 10DLC registration before serious traffic.
- Fill `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`.

### 3d. Planner (pick one)
- **Rule planner (no LLM, no key):** nothing to set; works out of the box.
- **Local Ollama (free LLM):** install from https://ollama.com, then:
  ```powershell
  ollama pull qwen2.5
  ollama serve
  ```
  Leave `OLLAMA_BASE_URL=http://localhost:11434` and `OLLAMA_MODEL=qwen2.5`.
- **Anthropic Claude (paid):** set `ANTHROPIC_API_KEY` from console.anthropic.com.

### 3e. Google Calendar (optional — silently skipped if unconfigured)

After `book_reservation` and `send_sms`, the agent creates a Google Calendar
event with popup reminders. If Google isn't configured the step logs
`skipped` and the booking still completes; nothing else breaks.

**One-time Google Cloud Console setup:**

1. Go to https://console.cloud.google.com/ and pick (or create) a project.
2. **APIs & Services → Library** → search "Google Calendar API" → **Enable**.
3. **APIs & Services → OAuth consent screen:**
   - User Type: **External** for personal Google accounts, **Internal** for
     Workspace.
   - Fill the required app fields (app name, support email).
   - **Scopes:** Add `.../auth/calendar.events` (read/write events only, no
     calendar listing). Save.
   - **Test users:** Add your own Google email. (External apps stay in
     "testing" mode until verification, which is fine for personal use —
     refresh tokens issued to test users are long-lived.)
4. **APIs & Services → Credentials → + Create credentials → OAuth client ID:**
   - Application type: **Desktop app**. Name it (e.g. `agent-claw-cli`).
   - **Download JSON.** This file (`client_secret_*.json`) is your client
     secrets file.
5. Save the downloaded file inside the project, e.g.:
   ```
   C:\SATHYA\CHAINAIM3003\mcp-servers\Agent-claw\Restaurant-claw\data\client_secret.json
   ```
   (Create the `data\` folder if it doesn't exist; it's where the agent
   keeps its SQLite DB and tokens.)
6. In `.env`, set:
   ```
   GOOGLE_CLIENT_SECRETS_PATH=data/client_secret.json
   GOOGLE_CALENDAR_ID=primary
   GOOGLE_REMINDER_MINUTES=60,1440          # 1 hour + 1 day before
   GOOGLE_EVENT_DURATION_MIN=90             # how long to block on calendar
   ```
7. **Run the one-time consent flow:**
   ```powershell
   python -m agent_claw --gcal-setup
   ```
   A browser tab opens → sign in with the Google account that has the calendar
   → grant the `calendar.events` scope. The refresh token is saved to
   `data\google_token.json`. **You won't need to do this again** — the agent
   refreshes the access token transparently on every run.

**Service-account variant (Google Workspace only):** if your org uses
Workspace and you want server-to-server auth with no browser, see the
comment block at the bottom of `agent_claw/providers/calendar.py`. You'd
replace `_load_credentials()` with a service-account loader plus
domain-wide delegation. Everything else stays the same.

---

## 4. Sanity check (no external calls)

The agent has a built-in preflight that loads your `.env`, prints which env
vars are set vs missing, and exits without making any API calls:

```powershell
python -m agent_claw --check
```

A green `[OK]` line per required variable means you're ready. A red `[!! ]`
line tells you exactly which var to set in `.env`. (`[opt]` = optional —
used only by `--planner claude` or for overriding defaults.)

Under the hood you can also probe individual pieces if you prefer:

```powershell
python -c "from agent_claw.agent.tools import TOOL_SCHEMAS; print(len(TOOL_SCHEMAS), 'tools')"
```
Expected: `8 tools`.

---

## 5. First real run (rule planner — no LLM)

This walks the diagram deterministically and hits every live service:

```powershell
python -m agent_claw "italian dinner for 2" `
    --address "Bandra, Mumbai" `
    --cuisine italian `
    --party 2 `
    --date 2026-06-01 `
    --time 19:30 `
    --contact +919876543210 `
    --planner rule
```

What you should see, in order:
1. `[get_user_location]` resolves the address via Nominatim.
2. `[search_restaurants]` lists nearby restaurants from Overpass (real OSM names).
3. `[check_availability]` per candidate — real Cal.com slots.
4. `[book_reservation]` — Cal.com returns a real `confirmation_id`.
5. `[send_sms]` — Twilio queues a real SMS (status `queued` → `delivered`).
6. `[save_booking]` — SQLite row + PDF written under `outputs/`.
7. `[create_calendar_event]` — Google Calendar event with reminders, OR
   `skipped: <reason>` if Google isn't configured.
8. `[Done]`.

Verify externally:
- The Cal.com web UI shows a new booking on your event type.
- Your phone receives the SMS.
- `data\agent_claw.db` contains a new row; `outputs\invoice_<id>.pdf` exists.
- Your Google Calendar shows a new event on the booking date with popup
  reminders at the configured offsets (open the event → "Notifications" panel
  to confirm).

---

## 6. Run with the local LLM in the loop

```powershell
python -m agent_claw "book me italian for 2 tonight" `
    --address "Bandra, Mumbai" `
    --contact +919876543210 `
    --planner ollama
```

The model decides the tool sequence; your code executes the calls. Same real
services, same real booking outcome.

---

## 7. Common failures and what they mean

| Symptom | Cause | Fix |
|---|---|---|
| `nominatim 429` or no result | rate limit / unknown place | Wait, refine address, check `NOMINATIM_UA` is set to your real contact |
| `cal.com booking 400: error_required_field` | event type has required booking fields (e.g. `{title}`) | Either remove the required custom field on the event type, or extend `book_reservation` to pass `bookingFieldsResponses` |
| `cal.com booking 401` | bad/missing API key | Re-issue key in Cal.com Settings → Developer → API keys |
| `twilio 400` "unverified" | trial account sending to unverified number | Verify the recipient under `Phone Numbers → Verified Caller IDs` |
| `twilio 401` | wrong SID/Token | Re-copy from Twilio console |
| `google calendar 401` after working before | refresh token revoked or test-user expired (External apps in testing) | Re-run `python -m agent_claw --gcal-setup` |
| `create_calendar_event: skipped: oauth not completed` | client secrets present but no token yet | Run `python -m agent_claw --gcal-setup` once |
| `create_calendar_event: skipped: google not configured` | `GOOGLE_CLIENT_SECRETS_PATH` is unset | Set it in `.env`, or ignore if you don't want calendar |
| `google calendar 403 "insufficientPermissions"` | the scope on the OAuth client doesn't include `calendar.events` | Re-create the OAuth consent screen scopes; re-run `--gcal-setup` |
| `ollama` connection refused | `ollama serve` not running | Start Ollama; confirm with `curl http://localhost:11434/api/tags` |
| `INSERT OR IGNORE` keeps row count flat on retry | working correctly — idempotency on `confirmation_id` | Not a bug |

---

## 8. Logs

Default level is INFO; add `--debug` for the full HTTP trace including request
URLs (no secrets are logged). Logs go to stderr so they don't mix with the
agent's `[node] detail` trace on stdout.

```powershell
python -m agent_claw ... --debug 2> agent_claw.log
```

---

## 9. Production-shaping notes (when you outgrow CLI)

The same code runs unchanged behind a web framework. To accept inbound SMS:

1. Add FastAPI/Flask in a new file (e.g. `agent_claw/web.py`).
2. Expose `POST /sms` that verifies Twilio's `X-Twilio-Signature`, loads a
   session keyed by `From`, feeds `Body` into the agent loop, and returns
   empty TwiML. Outbound replies use the existing `send_sms`.
3. The `ask_human` pause becomes "store state, exit; resume on next inbound
   SMS." No change to `tools.dispatch` or the providers.
4. Expose `POST /twilio/status` for delivery callbacks if you set
   `TWILIO_STATUS_CALLBACK`.
5. Tunnel localhost to a public HTTPS URL with Cloudflare Tunnel or ngrok.
