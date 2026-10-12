# Run the bot on your own PC, for free

Your PC runs `w2e run` every 30 minutes, and the bot decides by itself whether to post
(8 posts/day max, 150 minutes apart). The writing is done by Google's **free Gemini
tier**. No Claude subscription and no paid API are needed.

Conditions:
- The PC must be on and connected to the internet for posts to go out.
- The PC must not be in the US (Binance blocks US addresses).
- Free tiers have daily limits. The bot makes about 2-3 calls per post, so about
  20-25 calls/day at 8 posts/day. That is below the free limits reported in 2026, but
  Google can change them. If posts stop, check `data\cron.log`.
- On the free tier, Google may use what you send to improve its models. This bot only
  sends public market data and post drafts, nothing private.

## 1. Get two keys (both free)

1. **Square key**: Binance app > Square > Creator Center > OpenAPI key (you already have it).
2. **Gemini key (free)**: go to https://aistudio.google.com/apikey, sign in with a
   Google account, click "Create API key". Do not add a billing account; without
   billing it stays free.

Never paste these keys into chats, posts or files inside the repository.

## 2. Install (Windows)

Install **Python 3.12** (tick "Add python.exe to PATH") and **Git for Windows**.
Then open **Command Prompt** and run:

```
cd C:\
git clone https://github.com/anasbouteffah/agentic_binance.git
cd agentic_binance
git checkout claude/beautiful-bardeen-765j10
python -m venv .venv
.venv\Scripts\pip install .
```

Save the keys as user environment variables (replace the text in quotes):

```
setx BINANCE_SQUARE_OPENAPI_KEY "your-square-key"
setx GEMINI_API_KEY "your-gemini-key"
```

Close the Command Prompt and open a new one, so the keys load.

## 3. Test before going live

```
cd C:\agentic_binance
.venv\Scripts\w2e check
.venv\Scripts\w2e run --force --mode dry-run
```

- `check` tests Binance data and the Square key (it publishes nothing).
- `dry-run` writes one full post with Gemini and prints it, without publishing. Read it.
- If you get an error about the model name, open AI Studio, copy the name of a current
  free "Flash" model, and put it in `config.yaml` at `llm.model`.

## 4. Run every 30 minutes (Windows Task Scheduler)

```
schtasks /create /sc minute /mo 30 /tn w2e /tr "cmd /c cd /d C:\agentic_binance && .venv\Scripts\w2e run >> data\cron.log 2>&1"
```

- See what happened: open `C:\agentic_binance\data\cron.log`.
- Stop it: `schtasks /delete /tn w2e /f`
- Post stats: `.venv\Scripts\w2e stats`

## Linux or macOS instead

```
git clone https://github.com/anasbouteffah/agentic_binance.git ~/agentic_binance
cd ~/agentic_binance && git checkout claude/beautiful-bardeen-765j10
python3 -m venv .venv && .venv/bin/pip install .
BINANCE_SQUARE_OPENAPI_KEY=xxx GEMINI_API_KEY=yyy .venv/bin/w2e run --force --mode dry-run
crontab -e
```

Add this line in crontab, with your real keys:

```
*/30 * * * * cd ~/agentic_binance && BINANCE_SQUARE_OPENAPI_KEY=xxx GEMINI_API_KEY=yyy .venv/bin/w2e run >> data/cron.log 2>&1
```

## Other free choices (config.yaml, section `llm`)

| Choice | Settings | Notes |
|---|---|---|
| **Gemini free tier** (default) | `base_url: https://generativelanguage.googleapis.com/v1beta/openai`, `api_key_env: GEMINI_API_KEY` | Best free quality today |
| **OpenRouter free models** | `base_url: https://openrouter.ai/api/v1`, `api_key_env: OPENROUTER_API_KEY`, model ending in `:free` | Free key at openrouter.ai, low daily limit |
| **Ollama on your PC** | `base_url: http://localhost:11434/v1`, `api_key_env: ""`, model = an installed Ollama model | No key, no limit, fully offline. Needs a strong PC (16 GB RAM or more); small models write weaker posts |

## Quality warning

Free models write less carefully than Claude. The bot's quality gate (cashtags, banned
phrases, length, duplicates) still blocks bad drafts, and a second call reviews every
number against the data. Read the first few posts on Square anyway, and run the
dry-run test again after any change.
