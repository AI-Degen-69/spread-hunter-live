"""The Orders & Trades table: three views of the same run.

A market the screener graduated, an order resting in the book, and a filled
leg the account is holding are three stages of one object, and the operator
reads them in that order. They are three views of one table rather than three
panels because only the last stage has PnL: an order in the book is not a
position, and putting a PnL column beside it invites the reading that it is.

The column sets encode that. ACTIVE MARKETS carries no share count -- nothing
is owned yet. OPEN ORDERS carries no PnL -- nothing is exposed yet. POSITIONS
carries both.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "dashboard" / "static"
HARNESS = Path(__file__).resolve().parent / "js" / "orders_trades_harness.cjs"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")

CID_QUOTED = "0xquoted"
CID_HELD = "0xheld"
CID_DONE = "0xresolved"


def _render(view: str, kpi: dict | None = None, state: dict | None = None) -> dict:
    payload = {"view": view, "kpi": kpi or {}, "state": state or {}}
    out = subprocess.run([shutil.which("node"), str(HARNESS), json.dumps(payload)],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def _quote(token: str, side: str, mid: float, ts: float, order_id: str | None = None,
           queue: float | None = None, price: float | None = None) -> dict:
    # The bot quotes under the mid; that gap is the edge the pair is chasing.
    return {"token_id": token, "side": side, "mid": mid, "ts": ts,
            "price": mid - 0.025 if price is None else price,
            "order_id": order_id, "queue_ahead": queue}


def _kpi() -> dict:
    return {
        "by_market": {
            CID_QUOTED: {
                "condition_id": CID_QUOTED, "title": "Quoted Market", "category": "MLB",
                "days_to_resolve": 6.9, "volume_24h": 165792.64, "resolved": False,
                "quotes_count": 2, "up_sh": 0, "dn_sh": 0, "total_sh": 0,
                "total_cost": 0, "pair_cost": None, "realized_pnl": 0,
                # Mids sum to $1.00 -- they always do, the legs are two sides
                # of one binary. The quotes sum to $0.93, and that gap is the
                # only edge there is.
                "quotes": [
                    _quote("tok-up", "UP", 0.49, 100.0, "ord-up", 5186.53, price=0.45),
                    _quote("tok-dn", "DN", 0.51, 101.0, "ord-dn", 1200.0, price=0.48),
                ],
            },
            CID_HELD: {
                "condition_id": CID_HELD, "title": "Held Market", "category": "NBA",
                "days_to_resolve": 2.0, "volume_24h": 42000.0, "resolved": False,
                "quotes_count": 2, "up_sh": 10, "dn_sh": 6, "total_sh": 16,
                "total_cost": 15.0, "pair_cost": 0.98, "realized_pnl": 0.25,
                "quotes": [
                    _quote("tok-h-up", "UP", 0.60, 200.0),
                    _quote("tok-h-dn", "DN", 0.42, 201.0),
                ],
            },
            CID_DONE: {
                "condition_id": CID_DONE, "title": "Resolved Market", "category": "MLB",
                "days_to_resolve": -1.0, "volume_24h": 9000.0, "resolved": True,
                "quotes_count": 4, "up_sh": 0, "dn_sh": 0, "total_sh": 0,
                "total_cost": 0, "pair_cost": None, "realized_pnl": 1.5,
                "quotes": [],
            },
        }
    }


def _state() -> dict:
    return {
        "orders": [
            # Posted DOWN-leg-first on purpose: the table has to put UP above
            # DOWN whatever order the registry hands them over in.
            {"order_id": "ord-dn", "condition_id": CID_QUOTED, "token_id": "tok-dn",
             "pair_id": "pair-a", "side": "BUY", "price": 0.51, "original_size": 5.0,
             "size_matched": 2.0, "size_remaining": 3.0, "status": "partial",
             "posted_ts": 900, "age_sec": 120.0},
            {"order_id": "ord-up", "condition_id": CID_QUOTED, "token_id": "tok-up",
             "pair_id": "pair-a", "side": "BUY", "price": 0.47, "original_size": 5.0,
             "size_matched": 0.0, "size_remaining": 5.0, "status": "open",
             "posted_ts": 1000, "age_sec": 90.0},
            {"order_id": "ord-filled", "condition_id": CID_HELD, "token_id": "tok-h-up",
             "side": "BUY", "price": 0.60, "original_size": 10.0, "size_matched": 10.0,
             "size_remaining": 0.0, "status": "filled", "posted_ts": 800, "age_sec": 300.0},
            {"order_id": "ord-gone", "condition_id": CID_HELD, "token_id": "tok-h-dn",
             "side": "BUY", "price": 0.40, "original_size": 4.0, "size_matched": 0.0,
             "size_remaining": 4.0, "status": "cancelled", "posted_ts": 700, "age_sec": 400.0},
        ]
    }


# ── The shape of the three views ────────────────────────────────────────────

@requires_node
def test_the_three_views_are_the_three_stages_of_a_trade():
    # Arrange / Act
    rendered = _render("active-markets")

    # Assert
    assert rendered["views"] == ["active-markets", "open-orders", "positions"]


@requires_node
def test_active_markets_carries_no_share_count():
    # Arrange — nothing is owned at this stage, so a size column would be
    # describing a position the account has not taken.
    rendered = _render("active-markets", _kpi(), _state())

    # Act / Assert
    joined = " ".join(rendered["columns"]).lower()
    assert "shares" not in joined
    assert "size" not in joined
    assert rendered["columns"] == ["Market", "Category", "UP Quote", "DOWN Quote",
                                   "Pair Cost", "Edge", "24h Volume", "Resolves",
                                   "Status"]


@requires_node
def test_open_orders_carries_no_pnl():
    # Arrange — an order resting in the book is not a position. A PnL column
    # beside it invites exactly the reading the strategy cannot afford.
    rendered = _render("open-orders", _kpi(), _state())

    # Act / Assert
    joined = " ".join(rendered["columns"]).lower()
    assert "pnl" not in joined
    assert "unrealized" not in joined
    assert rendered["columns"] == ["Market", "Leg", "Price", "Shares", "Filled",
                                   "Remaining", "Total Cost", "Queue Ahead",
                                   "Age", "Status"]


@requires_node
def test_positions_carries_the_pnl_columns():
    # Arrange — this is the only stage where the account is exposed.
    rendered = _render("positions", _kpi(), _state())

    # Act / Assert
    assert "Unrealized" in rendered["columns"]
    assert "Realized" in rendered["columns"]
    assert "UP Shares" in rendered["columns"]


# ── Active markets ──────────────────────────────────────────────────────────

@requires_node
def test_active_markets_lists_the_markets_being_quoted():
    # Arrange / Act
    rendered = _render("active-markets", _kpi(), _state())

    # Assert — the resolved market is done, not active.
    assert "Quoted Market" in rendered["html"]
    assert "Held Market" in rendered["html"]
    assert "Resolved Market" not in rendered["html"]
    assert rendered["rows"] == 2


@requires_node
def test_active_markets_prices_the_pair_off_the_bot_s_own_quotes():
    # Arrange — the pair is only worth quoting while UP + DOWN is under $1.00,
    # so the row shows what the bot is bidding on each leg and what the pair
    # would cost if both filled.
    rendered = _render("active-markets", _kpi(), _state())

    # Act / Assert — 0.45 + 0.48 = 0.93, so 7.0 cents of edge.
    assert "$0.450" in rendered["html"]
    assert "$0.480" in rendered["html"]
    assert "$0.930" in rendered["html"]
    assert "7.0¢" in rendered["html"]


@requires_node
def test_active_markets_edge_is_not_computed_from_mids():
    # Arrange — UP mid and DOWN mid sum to $1.00 by construction: they are two
    # sides of one binary. An edge computed from them reads 0.0¢ on every row
    # of every real database, which is what it did before this.
    kpi = _kpi()
    mids = kpi["by_market"][CID_QUOTED]["quotes"]
    assert round(mids[0]["mid"] + mids[1]["mid"], 6) == 1.0

    # Act
    rendered = _render("active-markets", kpi, _state())

    # Assert
    assert "0.0¢" not in rendered["html"]
    assert "$1.000" not in rendered["html"]


@requires_node
def test_active_markets_reads_the_down_leg_however_it_is_spelled():
    # Arrange — the quote log writes the down leg as `DOWN`; orders and the
    # pair summary call it `DN`. Accepting only one spelling left every real
    # database showing a blank DOWN mid, pair cost and edge.
    kpi = _kpi()
    kpi["by_market"][CID_QUOTED]["quotes"] = [
        _quote("tok-up", "UP", 0.49, 100.0, price=0.45),
        _quote("tok-dn", "DOWN", 0.51, 101.0, price=0.48),
    ]

    # Act
    rendered = _render("active-markets", kpi, _state())

    # Assert
    assert "$0.480" in rendered["html"]
    assert "$0.930" in rendered["html"]
    assert "7.0¢" in rendered["html"]


@requires_node
def test_active_markets_says_so_when_nothing_is_quoted():
    # Arrange / Act
    rendered = _render("active-markets", {"by_market": {}}, {"orders": []})

    # Assert
    assert "No markets are being quoted" in rendered["html"]


# ── Open orders ─────────────────────────────────────────────────────────────

@requires_node
def test_open_orders_shows_only_orders_still_resting():
    # Arrange — a filled or cancelled order is not open.
    rendered = _render("open-orders", _kpi(), _state())

    # Act / Assert
    assert rendered["rows"] == 2
    assert "ord-up" in rendered["html"]
    assert "ord-dn" in rendered["html"]
    assert "ord-filled" not in rendered["html"]
    assert "ord-gone" not in rendered["html"]


@requires_node
def test_open_orders_prices_the_order_and_its_cost():
    # Arrange — size, price and what the order would cost if it all filled,
    # which is the number that has to clear the per-order cap.
    rendered = _render("open-orders", _kpi(), _state())

    # Act / Assert — 5 shares at $0.47 is $2.35.
    assert "$0.470" in rendered["html"]
    assert "$2.35" in rendered["html"]


@requires_node
def test_open_orders_names_the_leg_each_order_is_on():
    # Arrange — orders carry a token id; only the quote log knows which side
    # of the market that token is. A single buy is the failure mode this
    # column exists to make visible.
    rendered = _render("open-orders", _kpi(), _state())

    # Act / Assert
    assert ">UP<" in rendered["html"]
    assert ">DOWN<" in rendered["html"]


@requires_node
def test_open_orders_names_the_leg_that_never_got_a_quote_logged():
    # Arrange — a leg with no quote in the log leaves its token unnamed, and
    # that is exactly the order most worth labelling: a lone filled leg is the
    # single buy the strategy exists to avoid. The market is binary, so a
    # token that is not the named leg is the other one.
    kpi = _kpi()
    kpi["by_market"][CID_QUOTED]["quotes"] = [
        _quote("tok-up", "UP", 0.47, 100.0, "ord-up", 5186.53),
    ]

    # Act
    rendered = _render("open-orders", kpi, _state())

    # Assert
    assert ">UP<" in rendered["html"]
    assert ">DOWN<" in rendered["html"]


@requires_node
def test_open_orders_leaves_the_leg_blank_when_neither_side_was_quoted():
    # Arrange — with nothing named, guessing a side would be inventing it.
    kpi = _kpi()
    kpi["by_market"][CID_QUOTED]["quotes"] = []

    # Act
    rendered = _render("open-orders", kpi, _state())

    # Assert
    assert ">UP<" not in rendered["html"]
    assert ">DOWN<" not in rendered["html"]
    assert ">--<" in rendered["html"]


@requires_node
def test_open_orders_falls_back_to_the_condition_id_for_an_unreported_market():
    # Arrange — an order outlives its market's entry in the KPI report once
    # that market leaves the graduated universe. A truncated condition id is
    # still something the operator can search the registry for; `--` is not.
    state = _state()
    for order in state["orders"]:
        if order.get("pair_id") == "pair-a":
            order["condition_id"] = "0xabc123def456"

    # Act
    rendered = _render("open-orders", _kpi(), state)

    # Assert
    assert "0xabc123d" in rendered["html"]


@requires_node
def test_open_orders_lists_a_pair_as_two_rows_up_first():
    # Arrange — both legs have to fill for the pair to merge back into $1.00,
    # so the two orders that belong together are read together, and the leg
    # that decides the direction is read first.
    rendered = _render("open-orders", _kpi(), _state())

    # Act
    html = rendered["html"]

    # Assert — UP above DOWN, whatever order the registry handed them over in.
    assert html.index(">UP<") < html.index(">DOWN<")
    assert html.count('data-pair="pair-a"') == 2


@requires_node
def test_open_orders_names_the_market_once_across_both_legs():
    # Arrange — one market, one name: a name repeated on both rows reads as
    # two unrelated orders that happen to share a title.
    rendered = _render("open-orders", _kpi(), _state())

    # Act
    html = rendered["html"]

    # Assert — the merged cell spans the pair, and the second row has no
    # market cell of its own.
    assert 'rowspan="2"' in html
    assert html.count("Quoted Market") == 1


@requires_node
def test_open_orders_prices_the_pair_the_two_legs_would_make():
    # Arrange — under $1.00 the merge books a profit; over it books a loss.
    # That is the number the pair exists to hit, so it belongs on the pair.
    rendered = _render("open-orders", _kpi(), _state())

    # Act / Assert — 0.47 + 0.51 = $0.980.
    assert "pair $0.980" in rendered["html"]


@requires_node
def test_open_orders_does_not_call_a_break_even_pair_a_loss():
    # Arrange — a pair costing exactly $1.00 merges back into $1.00. That is
    # zero profit, not a loss, and colouring it red reads as money lost.
    state = _state()
    for order in state["orders"]:
        order["price"] = 0.50

    # Act
    rendered = _render("open-orders", _kpi(), state)

    # Assert
    assert "pair $1.000" in rendered["html"]
    assert "negative" not in rendered["html"]


@requires_node
def test_open_orders_flags_a_pair_with_only_one_leg_resting():
    # Arrange — a lone resting leg is half a pair. If it fills the account is
    # holding a directional bet nobody decided to take.
    state = _state()
    state["orders"] = [o for o in state["orders"] if o["order_id"] != "ord-dn"]

    # Act
    rendered = _render("open-orders", _kpi(), state)

    # Assert
    assert "one leg resting" in rendered["html"]
    assert "pair $" not in rendered["html"]


@requires_node
def test_open_orders_bands_alternate_pairs():
    # Arrange — two pairs back to back are four rows; without the banding the
    # boundary between them is invisible.
    state = _state()
    state["orders"] = state["orders"] + [
        {"order_id": "ord-b-up", "condition_id": CID_HELD, "token_id": "tok-h-up",
         "pair_id": "pair-b", "side": "BUY", "price": 0.55, "original_size": 5.0,
         "size_matched": 0.0, "size_remaining": 5.0, "status": "open",
         "posted_ts": 500, "age_sec": 60.0},
        {"order_id": "ord-b-dn", "condition_id": CID_HELD, "token_id": "tok-h-dn",
         "pair_id": "pair-b", "side": "BUY", "price": 0.40, "original_size": 5.0,
         "size_matched": 0.0, "size_remaining": 5.0, "status": "open",
         "posted_ts": 499, "age_sec": 61.0},
    ]

    # Act
    rendered = _render("open-orders", _kpi(), state)

    # Assert — every pair opens with a rule, and every second pair is shaded.
    assert rendered["html"].count("ot-pair-start") == 2
    assert rendered["html"].count("ot-pair-alt") == 2
    assert rendered["rows"] == 4


def test_the_market_column_has_a_width_floor():
    # Arrange — the market name is the only cell that wraps, and a three-word
    # title folded onto three lines squeezes every number column beside it.
    css = (_STATIC / "styles.css").read_text(encoding="utf-8")

    # Act
    block = css.split("#orders-trades-table td.ot-market")[1].split("}")[0]

    # Assert
    assert "min-width" in block


def test_the_width_floor_does_not_target_the_first_cell_by_position():
    # Arrange — in a paired row the market cell uses `rowspan`, so the second
    # row's first cell is the Leg. Keyed off position, the 200px floor would
    # land on the Leg column instead of the market name.
    css = (_STATIC / "styles.css").read_text(encoding="utf-8")

    # Act / Assert
    assert "#orders-trades-table td:first-child" not in css


@requires_node
@pytest.mark.parametrize("view", ["active-markets", "open-orders", "positions"])
def test_every_view_tags_its_market_cell(view):
    # Arrange — the width floor is keyed off the class now, so a view that
    # forgets it loses the floor and folds the title onto three lines.
    rendered = _render(view, _kpi(), _state())

    # Act / Assert
    assert 'class="ot-market"' in rendered["html"]


@requires_node
def test_open_orders_says_so_when_the_book_is_empty():
    # Arrange / Act
    rendered = _render("open-orders", _kpi(), {"orders": []})

    # Assert
    assert "No orders resting in the book" in rendered["html"]


# ── Positions ───────────────────────────────────────────────────────────────

@requires_node
def test_positions_lists_only_markets_with_filled_shares():
    # Arrange / Act
    rendered = _render("positions", _kpi(), _state())

    # Assert
    assert rendered["rows"] == 1
    assert "Held Market" in rendered["html"]
    assert "Quoted Market" not in rendered["html"]


@requires_node
def test_positions_marks_the_merged_pair_at_par_and_the_rest_at_mid():
    # Arrange — 10 UP and 6 DOWN: six pairs merge back into $1.00 each, and
    # the four naked UP shares are worth whatever UP is trading at.
    #   6 * 1.00 + 4 * 0.60 = $8.40, against a $15.00 cost basis.
    rendered = _render("positions", _kpi(), _state())

    # Act / Assert
    assert "$8.40" in rendered["html"]
    assert "-$6.60" in rendered["html"]


@requires_node
def test_positions_leaves_the_mark_blank_when_the_naked_leg_has_no_mid():
    # Arrange — an unhedged leg with no observed mid cannot be valued, and a
    # made-up number here is worse than an empty cell.
    kpi = _kpi()
    kpi["by_market"][CID_HELD]["quotes"] = []

    # Act
    rendered = _render("positions", kpi, _state())

    # Assert -- read the Mark Value cell itself. A bare `"--" in html` passes
    # on any row, so it would stay green if the naked shares were valued at 0.
    cells = rendered["html"].split("</td>")
    mark_cell = cells[rendered["columns"].index("Mark Value")]
    assert ">--" in mark_cell
    assert "$8.40" not in rendered["html"]


@requires_node
def test_positions_flags_a_single_leg():
    # Arrange — one leg filled and the other not is a directional bet nobody
    # decided to take, so the row has to name it.
    kpi = _kpi()
    kpi["by_market"][CID_HELD]["dn_sh"] = 0
    kpi["by_market"][CID_HELD]["total_sh"] = 10

    # Act
    rendered = _render("positions", kpi, _state())

    # Assert
    assert "Single Leg" in rendered["html"]


@requires_node
def test_positions_says_so_when_nothing_is_held():
    # Arrange / Act
    rendered = _render("positions", {"by_market": {}}, {"orders": []})

    # Assert
    assert "No filled legs" in rendered["html"]


# ── Tab counts ──────────────────────────────────────────────────────────────

@requires_node
def test_each_tab_counts_its_own_rows():
    # Arrange — the count on the tab is what tells the operator there is
    # something on a view they are not looking at.
    rendered = _render("active-markets", _kpi(), _state())

    # Act / Assert
    assert rendered["counts"] == {
        "active-markets": 2,
        "open-orders": 2,
        "positions": 1,
    }


# ── Wiring ──────────────────────────────────────────────────────────────────

def test_the_panel_is_on_the_served_page():
    # Arrange / Act
    index = (_STATIC / "index.html").read_text(encoding="utf-8")

    # Assert
    assert 'id="orders-trades-card"' in index
    assert 'id="orders-trades-head"' in index
    assert 'id="orders-trades-body"' in index
    for view in ("active-markets", "open-orders", "positions"):
        assert f'data-ot-view="{view}"' in index


def test_the_panel_redraws_on_every_poll():
    # Arrange — the table is worthless if it only fills in on a tab click.
    app = (_STATIC / "app.js").read_text(encoding="utf-8")

    # Act / Assert
    assert "renderOrdersTrades(currentKpi, lastState)" in app
    assert "initOrdersTradesTabs();" in app


def test_the_panel_lands_on_the_dashboard_page():
    # Arrange — the sidebar layout has to claim it, or it stays behind on the
    # tabbed page when the layout is switched over.
    prototype = (_STATIC / "prototype.js").read_text(encoding="utf-8")

    # Act
    home = prototype.split("page: 'home'")[1].split("page: 'data-markets'")[0]

    # Assert
    assert "#orders-trades-card" in home
    assert "#broker-portfolio-overview" in home
    assert "label: 'Dashboard'" in home
