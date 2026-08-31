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


def test_a_venue_read_that_fails_reports_unknown_not_standard(monkeypatch):
    # Arrange — an unreachable venue must not resolve to "standard market".
    from core_brain import order_manager as om

    def _boom(*args, **kwargs):
        raise OSError("venue unreachable")

    monkeypatch.setattr("core_brain.markets.fetch_pinned_market", _boom)

    # Act
    flag = om.resolve_neg_risk("0xdeadbeef")

    # Assert
    assert flag is None


def test_a_market_the_venue_does_not_know_reports_unknown(monkeypatch):
    # Arrange
    from core_brain import order_manager as om

    monkeypatch.setattr("core_brain.markets.fetch_pinned_market",
                        lambda *a, **k: None)

    # Act / Assert
    assert om.resolve_neg_risk("0xdeadbeef") is None


def test_the_flag_is_read_from_the_market_when_the_venue_answers(monkeypatch):
    # Arrange
    from core_brain import order_manager as om

    class _Market:
        neg_risk = True

    monkeypatch.setattr("core_brain.markets.fetch_pinned_market",
                        lambda *a, **k: _Market())

    # Act / Assert
    assert om.resolve_neg_risk("0xdeadbeef") is True


# --- the merge command itself ---------------------------------------------

def _stub_merge_env(monkeypatch, neg_risk_result):
    """Everything `merge()` touches outside routing, stubbed to a no-op."""
    from core_brain import order_manager as om

    monkeypatch.setattr(om, "_check_idempotency_guard", lambda *a, **k: None)
    monkeypatch.setattr(om, "get_payout_denominator", lambda *a, **k: 0)
    monkeypatch.setattr("core_brain.markets.fetch_pinned_market",
                        lambda *a, **k: neg_risk_result)
    monkeypatch.delenv("POLY_FUNDER", raising=False)
    monkeypatch.delenv("POLY_PRIVATE_KEY", raising=False)
    return om


def test_a_dry_run_merge_refuses_when_the_routing_is_unknown(monkeypatch, capsys):
    # Arrange — the venue read came back empty, so the target is unknown.
    om = _stub_merge_env(monkeypatch, None)

    # Act
    with pytest.raises(SystemExit):
        om.merge("0xdeadbeef", amount=1.0, live=False)

    # Assert — refused for the routing, and it says so.
    out = capsys.readouterr().out
    assert "neg_risk        unknown" in out
    assert "no routable target" in out


def test_a_dry_run_negrisk_merge_previews_the_adapter(monkeypatch, capsys):
    # Arrange — the caller already knows the flag, so no venue read happens.
    om = _stub_merge_env(monkeypatch, None)

    # Act
    with pytest.raises(SystemExit):
        om.merge("0xdeadbeef", amount=1.0, neg_risk=True, live=False)

    # Assert — the preview names the contract --live would call.
    out = capsys.readouterr().out
    assert NEG_RISK_CTF_COLLATERAL_ADAPTER in out
    assert "NegRisk collateral adapter" in out


def test_a_dry_run_standard_merge_previews_the_ctf(monkeypatch, capsys):
    # Arrange
    om = _stub_merge_env(monkeypatch, None)

    # Act
    with pytest.raises(SystemExit):
        om.merge("0xdeadbeef", amount=1.0, neg_risk=False, live=False)

    # Assert
    out = capsys.readouterr().out
    assert f"target          {CTF_CONTRACT}" in out
    assert "standard -> CTF" in out
