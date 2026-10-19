# alpha_vantage fixtures

## Provenance

**`aapl_news_demo.json` is a real, live capture**, made 2026-10-19 via one
GET to Alpha Vantage's public, keyless demo endpoint:
`https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers=AAPL&apikey=demo`.
Saved verbatim (pretty-printed only), 50 items, no items removed or edited.

This replaces an earlier hand-built version of this fixture: the overnight
build session that first wrote this adapter hit a sandbox network policy
that blocked `www.alphavantage.co`, so it shipped a fixture shaped to match
the spec's description of the response rather than a verified real payload,
and flagged that explicitly as a deviation needing follow-up. This capture
is that follow-up, made from a session without that restriction, and the
parsing logic has now been exercised against it directly (see
`tests/test_sources_alpha_vantage.py`) with no changes needed — the shape
the adapter was written against matches what the API actually returns.

Worth knowing for future captures: unlike `tickers=demo`'s behavior on some
Alpha Vantage endpoints, this one isn't a fixed canned dataset — it returns
whatever's currently live for AAPL, so a re-capture tomorrow will have
different items. That's fine for this fixture's purpose (exercising the
parser against a real payload shape), same as `tests/fixtures/yahoo_finance/`
being a point-in-time real capture rather than a stable golden file.

`edge_cases.json` is hand-built either way (same convention as
`tests/fixtures/yahoo_finance/edge_cases.json`): it exercises a
quota-exceeded response, and feed items each missing exactly one required
field (title, url, a parseable `time_published`), none of which a single
healthy capture would reliably contain.

## What NEWS_SENTIMENT does and doesn't return

Per the spec, there is no full article body in this API's response, only
`summary` (a short snippet). `AlphaVantageSource.fetch()` always leaves
`Article.body` empty for the same reason `YahooFinanceSource.fetch()` does:
there's nothing in the response to populate it with, and
`Article.text_for_summary` already falls back to `snippet`.
