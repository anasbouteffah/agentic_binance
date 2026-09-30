# agentic_binance: Write to Earn automation for Binance Square

This bot writes data-backed crypto posts with Claude, checks them hard, and
publishes them to Binance Square through Binance's **official Square OpenAPI**.
The goal is commission from the **Write to Earn** program.

## How Write to Earn pays (research, September 2026)

| Point | What it means |
|---|---|
| **Who can earn** | Any KYC-verified Binance user. No registration needed since Feb 2026. |
| **What you earn** | A share of the **trading fees** paid by readers who trade **after clicking a $CASHTAG / trade widget in your post**. Spot, Margin, Futures and Convert count. |
| **Rate** | **20% base.** Weekly top creators get more: rank 31-100 about 30%, rank 1-30 up to **50%**. |
| **Payout** | Weekly, in USDC. |
| **Where to track** | Binance app: Square > Creator Center > Write to Earn. There is no API for earnings. |
| **Official posting API** | `POST https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add` with header `X-Square-OpenAPI-Key`. Key from Square > Creator Center. Limits: **100 posts/day, 400 uploads/day**. |

Sources: Binance Academy "Write to Earn on Binance Square: All You Need to Know"
(Jul 2026); Binance announcement "Earn Up to 50% Commission on Every Binance Square
Post" (Feb 2026); the official `square-post` skill in
[binance/binance-skills-hub](https://github.com/binance/binance-skills-hub);
Binance Square team update of 20 Aug 2026 on spam enforcement.

### What gets accounts demonetized

Binance says it plainly: **high-volume, repetitive, templated or AI-generated
content** can lose monetization eligibility and get the account muted. In Aug 2026
they removed rewards from accounts for exactly this. "Entirely AI-generated"
posts score zero in CreatorPad campaigns.

This shapes the whole design: **few posts, real data, a real voice, strict filters.**
Posting 100 generic posts a day is the fastest way to earn nothing.

### Account milestones (Creator Academy, read Sept 2026)

| Milestone | Requirement | Why it matters |
|---|---|---|
| **Tips** | 1,000 followers, then enable in Creator Center > Tips | Readers can tip in crypto; you keep 100% |
| **Verified (gold badge)** | Full profile + 30K followers, no violation record, at least 1 post per quarter | Priority ranking in search and recommendations |
| **Verified+** | Verified + 300K views or 1M USD Write to Earn volume in the quarter | Same, renewed each quarter |

A single violation record blocks the badge. That is one more reason the bot
filters hard for quality instead of volume.

### Realistic expectations

- Income = your readers' trading fees x 20%. A new account with no followers
  earns close to **$0** for weeks. Earnings follow audience size and trust, not post count.
- Example: 50 readers trade $1,000 each at a 0.1% fee = $50 in fees, so **$10** for you.
- Costs (estimate, not measured): with `claude-opus-5` about $0.10-0.40 per published post
  (writer + editor + occasional rewrite), so roughly $10-35/month at 3 posts/day.
  Setting `llm.model: claude-sonnet-5` in `config.yaml` cuts that by about 60%.

## How the bot works

```
cron (every 30 min)
  -> schedule gate      active hours, daily cap, minimum gap, random skip
  -> market data        Binance public API: 24h tickers, 7d candles, funding, Fear & Greed
  -> news               RSS headlines (Cointelegraph, CoinDesk, Decrypt)
  -> planner            picks a format and a coin (no repeats, coin cooldown)
  -> writer (Claude)    writes as YOUR persona, only with numbers from the data
  -> quality gate       cashtags valid, no hype, no links, no AI cliches, not a near-duplicate
  -> editor (Claude)    checks every number against the data, scores 1-10
                        fail -> one rewrite with the feedback -> still fails -> dropped
  -> chart              7-day price chart PNG attached to single-coin posts
  -> publish / queue    auto: Square OpenAPI | review: wait for approval | dry-run: print only
```

Formats (weights in `config.yaml`): `market_pulse`, `coin_focus`, `news_take`,
`explainer`, and a long-form `weekly_article` every Sunday.

Every post carries 1-3 valid **$CASHTAGS**, because the cashtag is what opens the
trade widget, and that trade is where commission comes from.

## Two ways to run it

| | **A. Claude Code routine (no Anthropic key)** | **B. Standalone server** |
|---|---|---|
| Who writes | Claude, inside a scheduled Claude Code session on your Claude plan | Claude via the Anthropic API |
| Keys needed | Square key only | Square key + Anthropic API key |
| Commands | `w2e brief` then `w2e submit` | `w2e run` |
| Extra cost | none beyond your Claude plan usage | about $10-35/month API |

### A. Claude Code routine (current setup)

Each firing: `w2e brief --no-random-skip` gathers data, checks the schedule gates and
prints a writing packet (rules, persona, data, recent posts, editor checklist).
Claude writes and self-reviews the post, then `w2e submit --text-file post.txt`
runs the same quality gate and publishes. `data/history.jsonl` is committed back to
the repo so the next firing knows what was already posted.

Needs, in the cloud environment settings:
- **Network access** that allows Binance: `www.binance.com`, `data-api.binance.vision`,
  `api.binance.com`, `fapi.binance.com`, `api.alternative.me`, the RSS sites, and the
  image upload host Binance returns (an S3-style URL). The simplest is the unrestricted access level.
- **Environment variable** `BINANCE_SQUARE_OPENAPI_KEY` with your Square key.

### B. Standalone server


1. **Binance Square key**: Binance app/web > Square > Creator Center > create an
   OpenAPI key. Account must be KYC-verified.
2. **Anthropic key**: https://console.anthropic.com/
3. **Server**: Binance blocks some countries (for example the US). Run the bot on a
   machine in a country where Binance works for you (a small VPS or your own PC).
4. Install:
   ```bash
   git clone <this repo> /opt/agentic_binance && cd /opt/agentic_binance
   python3 -m venv .venv && .venv/bin/pip install .
   cp .env.example .env   # put both keys in .env
   ```
5. **Edit `persona.md`.** This is the biggest quality lever. Put your real views,
   style and rules there.
6. Test without publishing:
   ```bash
   .venv/bin/w2e run --force --mode dry-run --format coin_focus --coin BTC
   ```
7. Start in review mode for the first days, then switch `mode: auto` in `config.yaml`.
8. Schedule: add the line from `deploy/crontab.txt` with `crontab -e`.
   (Docker: `docker build -t w2e .` and use the Docker line.)

## Commands

| Command | What it does |
|---|---|
| `w2e run` | One scheduler tick. Cron calls this. |
| `w2e brief [--no-random-skip]` | No-API mode: gates + data, prints the writing packet. |
| `w2e submit --text-file F [--title T]` | No-API mode: quality gate, then publish. |
| `w2e run --force [--mode dry-run] [--format F] [--coin SOL]` | Ignore the schedule, generate now. |
| `w2e queue` | Show drafts waiting for review (review mode). |
| `w2e approve <id> [--text-file edited.txt]` | Publish a draft, optionally your edited text. |
| `w2e reject <id>` | Delete a draft. |
| `w2e stats` | Posts per format and coin, recent links. |
| `w2e save-key` | Save `BINANCE_SQUARE_OPENAPI_KEY` to `~/.config/binance-square/openapi-key`. |

## Plan and roadmap

**Phase 1 (this repo, done)**: data collection, planner, Claude writer + editor,
quality gate, charts, official Square API client, review queue, scheduler, tests.

**Phase 2 (first 2 weeks, your part)**
- Fill `persona.md` with your real opinions.
- Run `mode: review`, 2-3 posts/day. Edit drafts with your own sentences before
  approving. A human touch is what separates you from the accounts Binance demonetizes.
- Reply to comments on your posts yourself. Engagement drives reach; the bot does not comment (by design).

**Phase 3 (after 2 weeks)**
- Check Creator Center: which posts got views and trades. Raise the weight of formats that work.
- Switch to `mode: auto` only if drafts rarely need edits.
- Keep `max_posts_per_day` at 3-5.

**Possible next steps**
- Pull post performance to weight formats automatically (needs a Square read API; none is public yet).
- More data sources: open interest, ETF flows, token unlocks, on-chain data.
- Multilingual posts (set `language`) to reach less crowded audiences.

## Tests

```bash
pip install -e ".[dev]" && pytest
```
Network calls are mocked; tests run offline.
