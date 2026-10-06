from w2e.config import QualitySettings
from w2e.quality import cashtags, check_draft, similarity

TRADABLE = {"BTC", "ETH", "SOL"}
GOOD = ("$SOL is up 6.5% today on volume that is running well above its weekly average, which is the part I "
        "care about. Moves like this on thin volume usually fade by the next session. This one has real "
        "participation behind it. I would still wait for a retest of 145 before trusting it, because RSI is "
        "already stretched on the hourly chart and funding has turned positive. If $BTC loses 62,000 today, "
        "none of this matters and I stay flat. #Solana")


def test_good_draft_passes():
    r = check_draft(GOOD, None, QualitySettings(), TRADABLE, [])
    assert r.ok, r.problems


def test_cashtags_dedup_and_order():
    assert cashtags("$BTC then $ETH then $BTC again") == ["BTC", "ETH"]


def test_rejects_missing_and_unknown_cashtags():
    r = check_draft(GOOD.replace("$SOL", "SOL").replace("$BTC", "BTC"), None, QualitySettings(), TRADABLE, [])
    assert not r.ok and any("no $CASHTAG" in p for p in r.problems)
    r = check_draft(GOOD.replace("$SOL", "$FAKE"), None, QualitySettings(), TRADABLE, [])
    assert any("not tradable" in p for p in r.problems)


def test_rejects_hype_links_and_ai_tells():
    bad = GOOD + " Let's dive in, this is guaranteed to 100x. https://x.com"
    r = check_draft(bad, None, QualitySettings(max_similarity=1), TRADABLE, [])
    joined = " ".join(r.problems)
    assert "guaranteed" in joined and "100x" in joined and "link" in joined and "dive in" in joined


def test_rejects_near_duplicate():
    r = check_draft(GOOD, None, QualitySettings(), TRADABLE, [GOOD.replace("145", "146")])
    assert any("too similar" in p for p in r.problems)
    assert similarity(GOOD, GOOD) == 1.0


def test_length_limits_depend_on_type():
    r = check_draft(GOOD, "A title", QualitySettings(), TRADABLE, [])
    assert any("length" in p for p in r.problems)


def test_hashtag_rules():
    body = GOOD.replace(" #Solana", "")
    ok = check_draft(body + "\n\n#Solana #SolanaETF", None, QualitySettings(), TRADABLE, [])
    assert ok.ok, ok.problems
    too_many = check_draft(body + "\n\n#Solana #SOLUSDT #Layer1", None, QualitySettings(), TRADABLE, [])
    assert any("max 2" in p for p in too_many.problems)
    generic = check_draft(body + "\n\n#Solana #crypto", None, QualitySettings(), TRADABLE, [])
    assert any("generic" in p for p in generic.problems)
    middle = check_draft("#Solana " + body + "\n\n#SolanaETF", None, QualitySettings(), TRADABLE, [])
    assert any("last line" in p for p in middle.problems)
    repeat = check_draft(body + "\n\n#Solana", None, QualitySettings(), TRADABLE, ["older post\n#solana"])
    assert any("previous post" in p for p in repeat.problems)


def test_cashtags_allow_digit_tickers_but_not_money():
    assert cashtags("$0G and $1INCH beat $BTC, not $100") == ["0G", "1INCH", "BTC"]


def test_dollar_inside_trending_hashtag_is_not_a_cashtag():
    assert cashtags("$BTC is at 86,178.\n\n#BitcoinRejectedAt$87K #BNBSurpasses$790") == ["BTC"]


def test_rejects_disclaimers_and_template_labels():
    bad = GOOD.replace("I would still", "My take: I would still").replace(" #Solana", "\n\nNot financial advice.\n\n#Solana")
    joined = " ".join(check_draft(bad, None, QualitySettings(max_similarity=1), TRADABLE, []).problems)
    assert "not financial advice" in joined and "my take:" in joined
    fair = GOOD.replace("which is the part I care about", "which looks unfair to late buyers")
    assert check_draft(fair, None, QualitySettings(), TRADABLE, []).ok
