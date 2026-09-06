"""Rank live venue markets by what a merged UP+DOWN pair actually earns.

The strategy buys one UP share and one DOWN share of the same binary market and
merges the pair back into $1.00 of USDC. Profit is therefore

    edge_per_pair = 1.00 - (up_price + down_price)

and the only question a scanner can answer is WHERE that number is positive and
reachable. Two measurements decide it, and they are not the same thing:

* `bid_pair` -- the two best BIDS summed. This is the price the strategy pays,
  because both legs are quoted passively. A live scan on 2026-09-06 found 50 of
  70 markets with `bid_pair` under $1.00.
* `ask_pair` -- the two best ASKS summed. This is what CROSSING would cost. The
  same scan found 0 of 25 sampled markets under $1.00 there, median $1.002.
  Taking a leg is a booked loss on every market that has ever been measured
  here, which is why `taker_pair_is_profitable` exists: to keep that fact in the
  output rather than in a comment.

`queue_ahead_usd` is a COST, not available size. It is the money already resting
at the touch that has to be consumed before our own order trades, and a pair
needs BOTH legs, so the constraint is the leg with MORE queue in front of it,
not less. Ranking on `edge * depth` gets this exactly backwards -- it rewards
the markets that are hardest to get filled in. `turnover_score` divides instead:

    turnover_score = edge_per_pair * (volume_24h / max(queue_ahead_usd, 1))

which reads as "cents per pair, times how many times a day the queue in front of
us clears". A 1c edge behind $45,000 of queue on $333k of daily flow scores below
a 2c edge behind $600 of queue on $172k. Daily volume is a coarse proxy for the
flow that actually reaches the touch; it is the best figure the public API
exposes per market, and it is why the score ranks rather than predicts.

Read-only. This module places no orders and holds no signer; running it cannot
reach the venue's trading surface.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

import requests

log = logging.getLogger("pair_scanner")

GAMMA_HOST = os.environ.get("GAMMA_HOST", "https://gamma-api.polymarket.com")
CLOB_HOST = os.environ.get("CLOB_HOST", "https://clob.polymarket.com")

# (connect, read). A scan walks up to `limit` markets and issues two book calls
# per market, so a single hung host must not hold the whole sweep.
SCAN_TIMEOUT = (3.05, 5.0)

# Slugs and questions come from the venue and are later printed to a terminal
# and embedded in reports. Restrict them at the boundary so a hostile value
# never reaches either place.
_SAFE_SLUG_RE = re.compile(r"[^A-Za-z0-9._~-]")
_SAFE_TEXT_RE = re.compile(r"[^A-Za-z0-9 ._~:/()'+,%$#&?!-]")

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Mozilla/5.0"})
for _scheme in ("https://", "http://"):
    _SESSION.mount(_scheme, requests.adapters.HTTPAdapter(
        pool_connections=8, pool_maxsize=8, max_retries=0))


@dataclass(frozen=True)
class Candidate:
    """A venue market row that survived parsing, before its book is read."""

    condition_id: str
    slug: str
    question: str
    up_token: str
    down_token: str
    volume_24h: float


@dataclass(frozen=True)
class PairQuote:
    """One market's pair economics at the current touch."""

    condition_id: str
    slug: str
    question: str
    up_token: str
    down_token: str
    volume_24h: float
    bid_pair: float
    ask_pair: float
    queue_ahead_usd: float

    @property
    def edge_per_pair(self) -> float:
        """Dollars earned per merged pair when both legs fill at the touch."""
        return 1.0 - self.bid_pair

    @property
    def taker_pair_is_profitable(self) -> bool:
        """True only if CROSSING both legs would still assemble under $1.00."""
        return self.ask_pair < 1.0

    @property
    def queue_turns_per_day(self) -> float:
        """How many times a day the money in front of us is consumed."""
        return self.volume_24h / max(self.queue_ahead_usd, 1.0)

    @property
    def turnover_score(self) -> float:
        """Edge weighted by how reachable it is. Ranking only, not a forecast."""
        return self.edge_per_pair * self.queue_turns_per_day


def _sanitize_slug(slug: str) -> str:
    return _SAFE_SLUG_RE.sub("", slug or "")


def _sanitize_text(text: str) -> str:
    return _SAFE_TEXT_RE.sub("", text or "")


def _as_float(value: Any) -> Optional[float]:
    """A venue number -> float, or None when the venue sent something else."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_candidate(row: Any) -> Optional[Candidate]:
    """One gamma market row -> Candidate, or None when the row is unusable.

    Returns None rather than raising: a row the venue mangled is a market to
    skip, not a reason to abort the caller's whole scan.
    """
    if not isinstance(row, dict):
        return None

    condition_id = str(row.get("conditionId") or "")
    if not condition_id:
        return None

    raw_tokens = row.get("clobTokenIds")
    tokens: Sequence[Any]
    if isinstance(raw_tokens, str):
        try:
            tokens = json.loads(raw_tokens)
        except (ValueError, TypeError):
            log.debug("unparseable clobTokenIds on %s", condition_id)
            return None
    elif isinstance(raw_tokens, list):
        tokens = raw_tokens
    else:
        return None

    if not isinstance(tokens, list) or len(tokens) != 2:
        return None

    return Candidate(
        condition_id=condition_id,
        slug=_sanitize_slug(str(row.get("slug") or "")),
        question=_sanitize_text(str(row.get("question") or "")),
        up_token=str(tokens[0]),
        down_token=str(tokens[1]),
        volume_24h=_as_float(row.get("volume24hr")) or 0.0,
    )


def _touch(book: Any, side: str) -> Optional[tuple[float, float]]:
    """Best (price, size) on one side of a book, or None when it is unusable."""
    if not isinstance(book, dict):
        return None
    levels = book.get(side)
    if not isinstance(levels, list) or not levels:
        return None

    parsed: list[tuple[float, float]] = []
    for level in levels:
        if not isinstance(level, dict):
            return None
        price = _as_float(level.get("price"))
        size = _as_float(level.get("size"))
        if price is None or size is None:
            return None
        parsed.append((price, size))

    # Gamma returns bids ascending and asks descending in places; sort rather
    # than trust the order, so "best" means best on both sides.
    return max(parsed) if side == "bids" else min(parsed)


def build_quote(
    candidate: Candidate,
    up_book: Any,
    down_book: Any,
) -> Optional[PairQuote]:
    """Both books -> PairQuote, or None when no profitable pair exists there."""
    up_bid = _touch(up_book, "bids")
    down_bid = _touch(down_book, "bids")
    up_ask = _touch(up_book, "asks")
    down_ask = _touch(down_book, "asks")
    if not (up_bid and down_bid and up_ask and down_ask):
        return None

    bid_pair = up_bid[0] + down_bid[0]
    if bid_pair >= 1.0:
        return None

    # A pair needs BOTH legs, so the binding constraint is the leg with MORE
    # money resting in front of us, not less.
    queue_ahead = max(up_bid[0] * up_bid[1], down_bid[0] * down_bid[1])

    return PairQuote(
        condition_id=candidate.condition_id,
        slug=candidate.slug,
        question=candidate.question,
        up_token=candidate.up_token,
        down_token=candidate.down_token,
        volume_24h=candidate.volume_24h,
        bid_pair=bid_pair,
        ask_pair=up_ask[0] + down_ask[0],
        queue_ahead_usd=queue_ahead,
    )


def rank(quotes: Iterable[PairQuote]) -> list[PairQuote]:
    """Best-first by `turnover_score`, tie-broken by the wider edge."""
    return sorted(quotes, key=lambda q: (q.turnover_score, q.edge_per_pair), reverse=True)


def _fetch_book(token_id: str, clob_host: str, session: Any) -> Any:
    try:
        r = session.get(f"{clob_host}/book", params={"token_id": token_id},
                        timeout=SCAN_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as exc:
        log.debug("book fetch failed for %s: %s", token_id[:12], exc)
        return None


def scan(
    min_volume_24h: float = 50_000.0,
    limit: int = 70,
    gamma_host: str = GAMMA_HOST,
    clob_host: str = CLOB_HOST,
    session: Any = None,
) -> list[PairQuote]:
    """Live markets ranked by pair economics. Read-only; places no orders."""
    session = session or _SESSION

    r = session.get(
        f"{gamma_host}/markets",
        params={"closed": "false", "active": "true", "limit": 500,
                "order": "volume24hr", "ascending": "false"},
        timeout=SCAN_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json()
    if not isinstance(rows, list):
        return []

    quotes: list[PairQuote] = []
    for row in rows:
        if len(quotes) >= limit:
            break
        candidate = parse_candidate(row)
        if candidate is None or candidate.volume_24h < min_volume_24h:
            continue
        quote = build_quote(
            candidate,
            _fetch_book(candidate.up_token, clob_host, session),
            _fetch_book(candidate.down_token, clob_host, session),
        )
        if quote is not None:
            quotes.append(quote)

    return rank(quotes)


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--min-volume", type=float, default=50_000.0,
                    help="24h volume bar a market must clear to be booked")
    ap.add_argument("--limit", type=int, default=70,
                    help="stop after this many priced markets")
    ap.add_argument("--top", type=int, default=20, help="rows to print")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    quotes = scan(min_volume_24h=args.min_volume, limit=args.limit)

    print(f"{'edge/pair':>10}{'queue$':>12}{'turns/day':>11}{'score':>9}"
          f"{'pair':>8}{'vol24h':>13}  market")
    for q in quotes[:args.top]:
        print(f"{q.edge_per_pair * 100:>9.2f}c{q.queue_ahead_usd:>12,.0f}"
              f"{q.queue_turns_per_day:>11,.1f}{q.turnover_score:>9.2f}"
              f"{q.bid_pair:>8.3f}{q.volume_24h:>13,.0f}  {q.question[:46]}")

    takeable = [q for q in quotes if q.taker_pair_is_profitable]
    print(f"\n{len(quotes)} markets with a pair under $1.00 at the bid; "
          f"{len(takeable)} of them are also under $1.00 at the ask.")


if __name__ == "__main__":
    _main()
