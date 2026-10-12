# Run the bot on your own PC

The PC runs `w2e run` every 30 minutes. The bot decides by itself whether to post
(8 posts/day max, 150 minutes apart). Claude writes through the Anthropic API,
which you pay per use. No Claude Pro subscription is needed.

Conditions:
- The PC must be on and connected to the internet for posts to go out.
- The PC must not be in the US (Binance blocks US addresses).

## 1. Get two keys

1. **Square key**: Binance app > Square > Creator Center > OpenAPI key (you already have it).
2. **Anthropic API key**: https://console.anthropic.com > API Keys > Create key.
   Add a small credit (for example 5 dollars) under Billing.
   Set a monthly spend limit there, so the bill can never surprise you.

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
setx ANTHROPIC_API_KEY "your-anthropic-key"
```

Close the Command Prompt and open a new one, so the keys load.

## 3. Test before going live

```
cd C:\agentic_binance
.venv\Scripts\w2e check
.venv\Scripts\w2e run --force --mode dry-run
```

`check` tests Binance data and the Square key (it publishes nothing).
`dry-run` writes one full post and prints it, without publishing. Read it.
If it looks good, go to step 4.

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
.venv/bin/w2e check && .venv/bin/w2e run --force --mode dry-run
crontab -e
```

Add this line in crontab, with your real keys:

```
*/30 * * * * cd ~/agentic_binance && BINANCE_SQUARE_OPENAPI_KEY=xxx ANTHROPIC_API_KEY=yyy .venv/bin/w2e run >> data/cron.log 2>&1
```

## Cost control (config.yaml)

| Setting | Effect |
|---|---|
| `llm.model: claude-sonnet-5-5` (current) | Good quality. Estimate: about 0.05-0.10 dollars per post, so about 12-25 dollars/month at 8 posts/day |
| `llm.model: claude-haiku-5-5` | About 20x cheaper than Sonnet (cents per month), lower writing quality. Try it with a dry-run first |
| `schedule.max_posts_per_day` | Fewer posts = lower cost |

The cost figures are estimates from list prices, not measured. Check the real
spend in the Anthropic console after the first days.

## Free option (no API key, manual)

```
.venv\Scripts\w2e brief --no-random-skip
```

Copy the printed packet into free Claude.ai, ask it to write the post following
the packet, save the answer to `data\pending\post.txt` (create the `pending` folder if it is missing), then:

```
.venv\Scripts\w2e submit --text-file data\pending\post.txt
```

The quality gate still checks every post before it is published.
