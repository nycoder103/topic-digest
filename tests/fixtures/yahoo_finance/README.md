# yahoo_finance fixtures

## Provenance

`ionq_news.json`, `rgti_news.json`, and `qbts_news.json` are real
`yf.Ticker(<symbol>).news` responses, captured live with **yfinance 1.7.0**
on **2026-09-02**, for **IONQ, RGTI, QBTS** (the quantum topic's ticker
universe; see `config/tickers/quantum.yaml`). Each was saved verbatim, one
ticker per file, no items removed or edited.

All three came back in yfinance's newer shape, with each item's fields
nested under a `content` key. None came back in the older flat-dict shape
(fields directly on the item). If a future capture ever comes back flat
again, or nested under something other than `content`, that's a sign the
shape has changed again and `_unwrap()` in
`src/topic_digest/sources/yahoo_finance.py` needs a matching update, not
that this fixture is wrong.

`edge_cases.json` is **not** captured data. None of the three real
responses above happened to contain a malformed item, so it's hand-built
to exercise paths the real captures don't: a missing title, a missing URL,
a missing publish time, and one entry in the older flat (non-nested) shape
for backward-compat coverage.

If this file and the parser ever disagree with what yfinance actually
returns a year from now, re-run the capture, diff the new response shape
against what's committed here, and update `_unwrap()` / the extraction
helpers accordingly before touching the fixtures.

## What `.news` does and doesn't return

`.news` is metadata only: title, a short summary/description, publish
time, provider name, and a canonical/click-through URL. It never includes
full article body text, in either the flat or nested shape. This is why
`YahooFinanceSource.fetch()` always leaves `Article.body` empty — there is
nothing in this response to populate it with. Fetching full article text
from the article's own URL is a separate, source-agnostic concern for a
later pipeline stage, not something this adapter or these fixtures cover.

## Snippet length findings

Measured across the 3 captured files: 30 raw entries, 18 unique articles
after cross-ticker overlap (the same story showing up in more than one
ticker's feed, which the real data did naturally).

| | raw entries (n=30) | unique articles (n=18) |
|---|---|---|
| min | 34 | 34 |
| median | 141 | 140 |
| mean | 188.5 | 177.9 |
| max | 499 | 499 |
| clears 400 chars | 2 | 1 |

Only 1 of the 18 unique articles' snippets clears
`relevance.min_body_chars: 400` from `config/quantum.yaml`. Snippets alone
fail that threshold almost every time, which is the evidence that a
separate body-enrichment stage (fetching real article text, not relying on
`.news`'s snippet) does real work rather than duplicating
`Article.text_for_summary`'s existing snippet fallback.
