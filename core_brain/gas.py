"""What a close actually cost in gas, and who paid it.

`closes.gas` has existed as a column since the table was written and has been
NULL in every row of `data/orders.db` -- except on the merge path, which passes
a hardcoded `gas=0.0`. That is worse than NULL. NULL says "not measured";
0.0 asserts the merge was free, and `realized_pnl = proceeds - cost_basis` is
then overstated by exactly the gas.

It matters more here than the size of the number suggests. Run 145 established
that a maker-only pair's edge is exactly the spread, and the liquid Polymarket
books this strategy trades carry a one-tick spread -- so the whole trade earns
one cent per $0.99 pair. A gas cost is not a rounding error against one cent;
it is the difference between a business and a treadmill.

Two things have to be measured, not one:

  * **How much gas the transaction burned** -- `gasUsed * effectiveGasPrice`,
    denominated in POL, converted at the POL price to dollars.
  * **Whether we paid it.** Merges are submitted through Polymarket's relayer,
    which sends the transaction from its own address. If the relayer is the
    payer then our cost is zero no matter what the receipt says, and recording
    the burn as our expense would understate PnL as badly as the hardcoded 0.0
    overstates it.

The second question is answered by comparing the receipt's `from` against our
funder. Anything we cannot determine is recorded as None.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

RPC_ENDPOINTS = [
    "https://polygon-bor-rpc.publicnode.com",
    "https://1rpc.io/matic",
    "https://rpc.ankr.com/polygon",
    "https://polygon.drpc.org",
]

WEI_PER_POL = 10 ** 18


@dataclass(frozen=True)
class GasCost:
    """What one transaction burned, and whether it was ours to pay."""
    gas_used: int
    effective_price_wei: int
    payer: str
    we_paid: bool

    @property
    def pol(self) -> float:
        return (self.gas_used * self.effective_price_wei) / WEI_PER_POL

    def usd(self, pol_usd: float) -> float:
        """Dollars burned. Zero when the relayer paid, not when gas was small."""
        return self.pol * float(pol_usd) if self.we_paid else 0.0


def _hex_int(value: Any) -> Optional[int]:
    """Parse an RPC quantity, tolerating both hex strings and plain ints."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value), 16)
    except (TypeError, ValueError):
        return None


def parse_receipt(receipt: dict, funder: str) -> Optional[GasCost]:
    """Turn an `eth_getTransactionReceipt` result into a GasCost, or None.

    None when the receipt is missing either quantity. A receipt we cannot read
    must not be reported as free -- that is the bug this module exists to fix,
    and silently substituting a zero here would reintroduce it one layer down.

    `we_paid` compares the receipt's sender against our funder, case-folded:
    addresses come back from different endpoints in different casings and an
    exact match would call every relayer-paid merge ours on some endpoints and
    not others.
    """
    if not isinstance(receipt, dict):
        return None
    gas_used = _hex_int(receipt.get("gasUsed"))
    price = _hex_int(receipt.get("effectiveGasPrice"))
    if gas_used is None or price is None:
        return None
    payer = str(receipt.get("from") or "").strip()
    # An absent payer or an absent funder means the attribution is UNKNOWN, and
    # unknown is not free. Falling through to `we_paid=False` here would make
    # `usd()` return 0.0 and `backfill` write that zero permanently -- the
    # hardcoded assertion this module exists to remove, reappearing one layer
    # down and harder to see.
    if not payer or not str(funder or "").strip():
        return None
    return GasCost(gas_used=gas_used, effective_price_wei=price,
                   payer=payer, we_paid=payer.lower() == str(funder).lower())


def fetch_receipt(tx_hash: str, endpoints: Optional[list[str]] = None,
                  timeout: float = 5.0, opener=None) -> Optional[dict]:
    """The transaction receipt, trying each endpoint until one answers.

    Returns None rather than raising. A close is already recorded by the time
    this runs; failing to price its gas must never be able to lose the close.
    """
    if not tx_hash:
        return None
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_getTransactionReceipt",
        "params": [tx_hash],
    }).encode()
    send = opener or urllib.request.urlopen
    for endpoint in (endpoints or RPC_ENDPOINTS):
        try:
            req = urllib.request.Request(
                endpoint, data=payload,
                headers={"Content-Type": "application/json",
                         "User-Agent": "Mozilla/5.0"})
            with send(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode())
        except Exception:                                   # noqa: BLE001
            continue
        result = body.get("result") if isinstance(body, dict) else None
        if isinstance(result, dict):
            return result
    return None


def close_gas_usd(tx_hash: str, funder: str, pol_usd: Optional[float],
                  fetch=fetch_receipt) -> Optional[float]:
    """Dollars of gas to attribute to a close, or None when unknown.

    None and 0.0 mean different things and the caller must keep them apart:
    None is "not measured", 0.0 is "measured, and the relayer paid it". Passing
    0.0 for an unreadable receipt is exactly the hardcoded assertion this
    replaces.
    """
    if pol_usd is None:
        return None
    receipt = fetch(tx_hash)
    if receipt is None:
        return None
    cost = parse_receipt(receipt, funder)
    if cost is None:
        return None
    return round(cost.usd(pol_usd), 8)


# --- what a POL is worth ------------------------------------------------------

# Chainlink's MATIC/USD aggregator on Polygon. An on-chain oracle rather than a
# price API: it needs no key, it is read over the same RPC endpoints the rest of
# this module already uses, and it is the price feed the chain itself trusts.
# `latestRoundData()` -> (roundId, answer, startedAt, updatedAt, answeredInRound)
# with the answer carrying 8 decimals.
CHAINLINK_MATIC_USD = "0xAB594600376Ec9fD91F8e885dADF0CE036862dE0"
LATEST_ROUND_DATA = "0xfeaf968c"
CHAINLINK_DECIMALS = 10 ** 8


def parse_latest_round_data(result: str) -> Optional[float]:
    """The `answer` word of a latestRoundData() return, as dollars.

    The answer is a signed int256 in the second 32-byte word. Chainlink can
    report a negative answer for some feeds, and a negative price is a broken
    feed rather than a cheap token -- so it is refused rather than passed on to
    multiply a gas cost into a credit.
    """
    if not isinstance(result, str) or not result.startswith("0x"):
        return None
    body = result[2:]
    if len(body) < 64 * 2:
        return None
    raw = int(body[64:128], 16)
    if raw >= 2 ** 255:                     # two's complement, int256
        raw -= 2 ** 256
    if raw <= 0:
        return None
    return raw / CHAINLINK_DECIMALS


def pol_usd_price(endpoints: Optional[list[str]] = None, timeout: float = 5.0,
                  opener=None) -> Optional[float]:
    """POL in dollars from the on-chain oracle, or None if no endpoint answers.

    None rather than a fallback constant. A stale hardcoded price would make
    every gas figure quietly wrong in a way nothing downstream could detect,
    and `close_gas_usd` already treats None as "not measured".
    """
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "eth_call",
        "params": [{"to": CHAINLINK_MATIC_USD, "data": LATEST_ROUND_DATA},
                   "latest"],
    }).encode()
    send = opener or urllib.request.urlopen
    for endpoint in (endpoints or RPC_ENDPOINTS):
        try:
            req = urllib.request.Request(
                endpoint, data=payload,
                headers={"Content-Type": "application/json",
                         "User-Agent": "Mozilla/5.0"})
            with send(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode())
        except Exception:                                   # noqa: BLE001
            continue
        price = parse_latest_round_data(
            (body or {}).get("result") if isinstance(body, dict) else None)
        if price is not None:
            return price
    return None


# --- backfill -----------------------------------------------------------------


def unpriced_closes(conn) -> list[dict]:
    """Closes that carry a tx_hash but no gas figure yet.

    The merge path writes the close immediately and leaves gas NULL, because
    at write time the receipt is usually not mined. This is the query that
    finds the ones still waiting.
    """
    rows = conn.execute(
        "SELECT id, tx_hash, method, shares, realized_pnl FROM closes"
        " WHERE gas IS NULL AND tx_hash IS NOT NULL AND tx_hash != ''"
        " ORDER BY ts"
    ).fetchall()
    return [dict(r) for r in rows]


def backfill(conn, funder: str, pol_usd: Optional[float],
             fetch=fetch_receipt) -> dict:
    """Price every unpriced close, and correct its realized PnL.

    `realized_pnl` was written gross, so the gas is subtracted here at the
    same moment it becomes known. Doing it in one statement per row keeps the
    two columns from ever disagreeing: a reader that saw gas filled in but PnL
    not yet adjusted would double-count the cost when it subtracted for
    itself.

    Rows whose receipt still will not load are left NULL and counted, not
    zeroed. They are pending, not free.
    """
    out = {"seen": 0, "priced": 0, "unreadable": 0, "usd": 0.0}
    if pol_usd is None:
        return out
    for row in unpriced_closes(conn):
        out["seen"] += 1
        usd = close_gas_usd(row["tx_hash"], funder, pol_usd, fetch=fetch)
        if usd is None:
            out["unreadable"] += 1
            continue
        # `AND gas IS NULL` makes the write idempotent. Two backfills can
        # select the same row before either commits; without the guard the
        # second one subtracts the gas from `realized_pnl` a second time and
        # the loss is silent -- the row looks priced either way. Counting off
        # `rowcount` rather than off the loop keeps the statistics honest about
        # which process actually did the work.
        cur = conn.execute(
            "UPDATE closes SET gas = ?,"
            " realized_pnl = COALESCE(realized_pnl, 0) - ?"
            " WHERE id = ? AND gas IS NULL",
            (usd, usd, row["id"]))
        if cur.rowcount:
            out["priced"] += 1
            out["usd"] += usd
    conn.commit()
    return out


# --- entrypoint ---------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    """Price every close still carrying a NULL gas figure.

    Without a runnable caller `backfill` is a function nothing invokes, and
    merge closes keep NULL gas and a gross `realized_pnl` forever -- which is
    the same reporting failure as the hardcoded zero, just quieter. Run it
    after a merge has had time to mine, or on a schedule beside the fleet.

    Read-mostly and safe to repeat: the update is guarded on `gas IS NULL`, so
    a second run over the same rows changes nothing.
    """
    import argparse
    import os
    import sqlite3

    from core_brain.order_registry import DEFAULT_DB_PATH

    ap = argparse.ArgumentParser(
        prog="python -m core_brain.gas",
        description="Fill in closes.gas from on-chain receipts.")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH),
                    help="registry to backfill (default: the production one)")
    ap.add_argument("--funder", default=None,
                    help="funder address; defaults to POLY_FUNDER")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be priced, write nothing")
    args = ap.parse_args(argv)

    funder = args.funder or os.environ.get("POLY_FUNDER", "")
    if not funder:
        print("no funder: pass --funder or set POLY_FUNDER. Without it every "
              "receipt is unattributable and would be recorded as unknown.")
        return 2

    price = pol_usd_price()
    if price is None:
        print("no POL price from any endpoint; nothing priced.")
        return 1

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.dry_run:
            pending = unpriced_closes(conn)
            print(f"POL/USD {price:.6f}; {len(pending)} close(s) awaiting a "
                  f"gas figure in {args.db}")
            return 0
        stats = backfill(conn, funder, price)
    finally:
        conn.close()

    print(f"POL/USD {price:.6f}  seen {stats['seen']}  priced {stats['priced']}"
          f"  unreadable {stats['unreadable']}  total ${stats['usd']:.5f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
