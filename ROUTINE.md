# Scheduled run: Write to Earn post

Instructions for the Claude Code session that runs on schedule in the
"Binance" cloud environment (Full network access, `BINANCE_SQUARE_OPENAPI_KEY` set).
One run publishes at most one post.

1. Sync and install
   ```bash
   cd <repo root>
   git pull --ff-only origin claude/beautiful-bardeen-765j10
   pip install -q -e . 2>/dev/null || pip install -q -e . --break-system-packages
   ```
2. Get the writing task
   ```bash
   w2e brief --no-random-skip
   ```
   If it prints `status: skipped ...`, stop here. Do not commit anything.
   A skip is normal: the bot only posts when a coin moved enough or a
   follow-up is due.
3. Write the post exactly as the packet says: persona, hard rules, only numbers
   from DATA, 1-3 valid $CASHTAGS, plain text. Then review it yourself with the
   editor checklist in the packet. Rewrite until you would score it 7+ with zero
   factual errors. Save the body to `data/pending/post.txt`.
4. Publish
   ```bash
   w2e submit --text-file data/pending/post.txt            # short post
   w2e submit --text-file data/pending/post.txt --title "…" # only if the packet asks for an article
   ```
   If it prints `status: rejected`, fix every listed problem and submit again
   (at most 3 attempts). If it fails with Square error 220009 (daily limit),
   30008, 2000001 or 2000002 (account restriction), stop and report it.
5. Save history so the next run does not repeat itself
   ```bash
   git add -f data/history.jsonl
   git commit -m "chore: record Square post"
   git push origin claude/beautiful-bardeen-765j10
   ```
6. Reply with one line: the status and the post link.

Never: publish more than one post per run, change `config.yaml` or `persona.md`
(unless the owner asks in the chat),
print the API key, or add links, referral codes or engagement bait to posts.
