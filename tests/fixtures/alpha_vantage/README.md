# alpha_vantage fixtures

## Provenance

**`aapl_news_demo.json` is NOT a live capture**, despite what this adapter's
spec called for. The spec asked for exactly one live GET to
`https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers=AAPL&apikey=demo`
(Alpha Vantage's public, keyless demo endpoint, intended for exactly this
kind of exploration) to capture a real response and commit it verbatim.

That call could not be made: this sandbox's outbound network policy denies
`www.alphavantage.co` (confirmed via the agent proxy, which returned a 403
policy denial on the CONNECT attempt). No other network call was attempted or
made as a substitute, live or otherwise, per the one-call limit in the spec.

Instead, `aapl_news_demo.json` is hand-built to match the NEWS_SENTIMENT
response shape as described in the task spec (which states the shape was
"verified live against the real API tonight, Oct 2026"): a top-level object
with a `feed` list of items, each with `title`, `url`, `time_published`
(`YYYYMMDDTHHMMSS`), `summary`, `source`, and `ticker_sentiment` (a list of
`{ticker, relevance_score, ticker_sentiment_score, ticker_sentiment_label}`).
Headlines, summaries, and URLs below are fictional, use the `.invalid` TLD
where a link needs to resolve to nothing, and are not real Alpha Vantage
output.

**This is a flagged deviation from the spec, not a silent substitution.**
Before this adapter is relied on in production, the maintainer should:
1. Either widen this environment's network policy to allow
   `www.alphavantage.co` and have a future session make the real capture, or
2. Make the capture by hand from a machine with unrestricted network access,
   following the exact command above, and replace this fixture with the real
   response (keeping this README's shape documentation as a cross-check).

If the real response's shape differs from what's hand-built here, the
parsing logic in `src/topic_digest/sources/alpha_vantage.py` was written
against the spec's description, not against a verified real payload, and
should be re-checked against the real capture first.

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
