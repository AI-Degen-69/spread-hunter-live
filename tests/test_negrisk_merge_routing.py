"""A negRisk merge goes to the adapter, not the CTF (#122).

Polymarket routes a negative-risk market's merge through its own collateral
adapter; a standard market calls the Conditional Tokens contract directly. The
ABI is identical, so only the call `target` differs — and the wrong target
reverts. Before this, every merge and redeem call site passed `CTF_CONTRACT`
unconditionally and `neg_risk` was carried on `LiveMarket` and only printed, so
a negRisk pair assembled here had no working merge exit: it would sit until the
market resolved, which is the state this strategy exists to avoid.

The signature covers the target, so routing has to be decided before signing,
not at submit time.
"""
from __future__ import annotations

import pytest

from core_brain.merge_pairs import (
    CTF_CONTRACT,
    NEG_RISK_CTF_COLLATERAL_ADAPTER,
    build_redeem_typed_data,
    merge_target,
)
from core_brain.order_manager import build_redeem_submit_payload

FUNDER = "0x00000000000000000000000000000000000000f1"
SIGNER = "0x00000000000000000000000000000000000000a2"
CALLDATA = "0x9e7212ad" + "00" * 32


def test_a_negrisk_market_routes_to_the_adapter():
    # Arrange / Act
    target = merge_target(True)

    # Assert
    assert target == NEG_RISK_CTF_COLLATERAL_ADAPTER
    assert target != CTF_CONTRACT


def test_a_standard_market_routes_to_the_ctf():
    # Arrange / Act / Assert
    assert merge_target(False) == CTF_CONTRACT


def test_an_unknown_flag_refuses_instead_of_guessing():
    # Arrange — None is not False. A market whose negRisk flag could not be
    # read is a market whose routing is unknown, and the cost of guessing is a
    # reverted merge on a pair we are already holding.
    with pytest.raises(ValueError) as excinfo:
        merge_target(None)

    # Assert
    assert "negRisk" in str(excinfo.value)


def test_the_signed_batch_call_carries_the_routed_target():
    # Arrange / Act — the EIP-712 message is what the key signs.
    _, _, message = build_redeem_typed_data(
        FUNDER, 1, 2_000_000_000, CALLDATA, target=NEG_RISK_CTF_COLLATERAL_ADAPTER)

    # Assert
    assert message["calls"][0]["target"] == NEG_RISK_CTF_COLLATERAL_ADAPTER


def test_the_signed_batch_call_still_defaults_to_the_ctf():
    # Arrange — redeem, and a standard market's merge, are unchanged.
    _, _, message = build_redeem_typed_data(FUNDER, 1, 2_000_000_000, CALLDATA)

    # Assert
    assert message["calls"][0]["target"] == CTF_CONTRACT


def test_the_relayer_payload_carries_the_routed_target():
    # Arrange / Act
    payload = build_redeem_submit_payload(
        from_addr=SIGNER, funder=FUNDER, nonce=1, deadline=2_000_000_000,
        signature="0x" + "00" * 65, call_data=CALLDATA,
        target=NEG_RISK_CTF_COLLATERAL_ADAPTER)

    # Assert — the submitted call and the signed call must name one contract.
    assert payload["depositWalletParams"]["calls"][0]["target"] == \
        NEG_RISK_CTF_COLLATERAL_ADAPTER


def test_the_deprecated_v1_adapter_is_not_used():
    # Arrange — the CLOB v1 Neg Risk Adapter was deprecated 2026-07-14.
    deprecated = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

    # Act / Assert
    assert NEG_RISK_CTF_COLLATERAL_ADAPTER.lower() != deprecated.lower()


class _Resp:
    """Minimal stand-in for the CLOB market response."""

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _venue_returns(monkeypatch, payload=None, boom=False):
    """Point the market read at a canned CLOB record."""
    from core_brain import markets as markets_mod

    def _get(*args, **kwargs):
        if boom:
            raise OSError("venue unreachable")
        return _Resp(payload)

    monkeypatch.setattr(markets_mod._SESSION, "get", _get)


def test_a_venue_read_that_fails_reports_unknown_not_standard(monkeypatch):
    # Arrange — an unreachable venue must not resolve to "standard market".
    from core_brain import order_manager as om

    _venue_returns(monkeypatch, boom=True)

    # Act / Assert
    assert om.resolve_neg_risk("0xdeadbeef") is None


def test_a_record_without_the_flag_reports_unknown(monkeypatch):
    # Arrange — an absent key says nothing about routing, and defaulting it to
    # False is the guess this refuses to make.
    from core_brain import order_manager as om

    _venue_returns(monkeypatch, payload={"condition_id": "0xdeadbeef"})

    # Act / Assert
    assert om.resolve_neg_risk("0xdeadbeef") is None


def test_the_flag_is_read_from_the_market_when_the_venue_answers(monkeypatch):
    # Arrange
    from core_brain import order_manager as om

    _venue_returns(monkeypatch, payload={"neg_risk": True})

    # Act / Assert
    assert om.resolve_neg_risk("0xdeadbeef") is True


def test_a_closed_market_still_reports_its_routing(monkeypatch):
    # Arrange — a merge is FOR this case: we hold a pair on a book that has
    # stopped trading and want the dollar back. A tradability filter here would
    # report "unknown" and refuse every such merge, leaving the pair stuck.
    from core_brain import order_manager as om

    _venue_returns(monkeypatch, payload={
        "neg_risk": True, "closed": True, "accepting_orders": False,
    })

    # Act / Assert
    assert om.resolve_neg_risk("0xdeadbeef") is True


# --- the merge command itself ---------------------------------------------

def _stub_merge_env(monkeypatch, venue_payload=None, approved=True):
    """Everything `merge()` touches outside routing, stubbed to a no-op."""
    from core_brain import order_manager as om

    monkeypatch.setattr(om, "_check_idempotency_guard", lambda *a, **k: None)
    monkeypatch.setattr(om, "get_payout_denominator", lambda *a, **k: 0)
    monkeypatch.setattr(om, "is_approved_for_all", lambda *a, **k: approved)
    _venue_returns(monkeypatch, payload=venue_payload)
    monkeypatch.delenv("POLY_FUNDER", raising=False)
    monkeypatch.delenv("POLY_PRIVATE_KEY", raising=False)
    return om


def test_a_dry_run_defers_the_routing_read_instead_of_touching_the_venue(monkeypatch, capsys):
    # Arrange — a dry run makes no network call by contract, so the negRisk
    # flag is NOT read from the venue there. It says so rather than implying a
    # target it never resolved.
    om = _stub_merge_env(monkeypatch, venue_payload={"condition_id": "0xdeadbeef"})
    called = []
    monkeypatch.setattr(om, "resolve_neg_risk",
                        lambda cid: called.append(cid) or None)

    # Act
    with pytest.raises(SystemExit):
        om.merge("0xdeadbeef", amount=1.0, live=False)

    # Assert — no venue read, and the preview is honest about why.
    out = capsys.readouterr().out
    assert called == []
    assert "not read in a dry run" in out
    assert "(read on --live)" in out


def test_a_dry_run_still_previews_a_target_when_the_flag_is_given(monkeypatch, capsys):
    # Arrange
    om = _stub_merge_env(monkeypatch)

    # Act
    with pytest.raises(SystemExit):
        om.merge("0xdeadbeef", amount=1.0, neg_risk=True, live=False)

    # Assert
    out = capsys.readouterr().out
    assert NEG_RISK_CTF_COLLATERAL_ADAPTER in out
    assert "NegRisk collateral adapter" in out


def test_a_dry_run_standard_merge_previews_the_ctf(monkeypatch, capsys):
    # Arrange
    om = _stub_merge_env(monkeypatch)

    # Act
    with pytest.raises(SystemExit):
        om.merge("0xdeadbeef", amount=1.0, neg_risk=False, live=False)

    # Assert
    out = capsys.readouterr().out
    assert f"target          {CTF_CONTRACT}" in out
    assert "standard -> CTF" in out


# --- the live path, where routing and approval are enforced -----------------

def _live_merge_env(monkeypatch, approved, neg_risk_result=None):
    """A live merge whose only remaining question is routing/approval."""
    import os
    from unittest.mock import MagicMock
    from eth_account import Account

    from core_brain import order_manager as om

    acc = Account.create()
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    for k, v in {
        "POLY_PRIVATE_KEY": "0x" + acc.key.hex(),
        "POLY_FUNDER": funder,
        "POLY_SIG_TYPE": "3",
        "RELAYER_API_KEY": "test_key",
        "RELAYER_API_KEY_ADDRESS": "0x1234567890123456789012345678901234567890",
    }.items():
        monkeypatch.setenv(k, v)

    client = MagicMock()
    client.get_balance_allowance.return_value = {"balance": "10000000"}  # 10 shares
    monkeypatch.setattr(om, "client", lambda *a, **k: client)
    monkeypatch.setattr(om, "_check_idempotency_guard", lambda *a, **k: None)
    monkeypatch.setattr(om, "get_payout_denominator", lambda *a, **k: 0)
    monkeypatch.setattr(om, "is_approved_for_all", lambda *a, **k: approved)
    monkeypatch.setattr(om, "resolve_neg_risk", lambda cid: neg_risk_result)
    return om, funder


def test_a_live_merge_refuses_when_the_routing_is_unknown(monkeypatch):
    # Arrange — the venue read came back empty, so the target is unknown.
    om, _ = _live_merge_env(monkeypatch, approved=True, neg_risk_result=None)

    # Act
    with pytest.raises(SystemExit) as excinfo:
        om.merge("0xdeadbeef", amount=1.0, live=True)

    # Assert
    assert "no routable target" in str(excinfo.value)


def test_a_live_merge_refuses_when_the_target_is_not_an_approved_operator(monkeypatch):
    # Arrange — a wallet that has only ever merged standard markets has never
    # approved the negRisk adapter, so the first negRisk merge would revert on
    # approval rather than on routing.
    om, funder = _live_merge_env(monkeypatch, approved=False)

    # Act
    with pytest.raises(SystemExit) as excinfo:
        om.merge("0xdeadbeef", amount=1.0, neg_risk=True, live=True)

    # Assert
    message = str(excinfo.value)
    assert "has not approved" in message
    assert NEG_RISK_CTF_COLLATERAL_ADAPTER in message


def test_an_unreadable_approval_refuses_rather_than_assuming_approved(monkeypatch):
    # Arrange — "not approved" and "could not check" are different states.
    om, _ = _live_merge_env(monkeypatch, approved=None)

    # Act
    with pytest.raises(SystemExit) as excinfo:
        om.merge("0xdeadbeef", amount=1.0, neg_risk=True, live=True)

    # Assert
    assert "Could not read whether" in str(excinfo.value)


def test_an_approved_live_merge_clears_the_routing_guards(monkeypatch):
    # Arrange — routing and approval both fine; the merge proceeds past those
    # guards and stops only at the relayer call this test does not stub.
    om, _ = _live_merge_env(monkeypatch, approved=True)

    # Act
    with pytest.raises(SystemExit) as excinfo:
        om.merge("0xdeadbeef", amount=1.0, neg_risk=True, live=True)

    # Assert — whatever stopped it, it was not routing or approval.
    message = str(excinfo.value)
    assert "no routable target" not in message
    assert "has not approved" not in message
    assert "Could not read whether" not in message
