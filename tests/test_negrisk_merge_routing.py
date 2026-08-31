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


def test_a_dry_run_merge_refuses_when_the_routing_is_unknown(monkeypatch, capsys):
    # Arrange — the venue read came back empty, so the target is unknown.
    om = _stub_merge_env(monkeypatch, venue_payload={"condition_id": "0xdeadbeef"})

    # Act
    with pytest.raises(SystemExit):
        om.merge("0xdeadbeef", amount=1.0, live=False)

    # Assert — refused for the routing, and it says so.
    out = capsys.readouterr().out
    assert "neg_risk        unknown" in out
    assert "no routable target" in out


def test_a_dry_run_negrisk_merge_previews_the_adapter(monkeypatch, capsys):
    # Arrange — the caller already knows the flag, so no venue read happens.
    om = _stub_merge_env(monkeypatch)

    # Act
    with pytest.raises(SystemExit):
        om.merge("0xdeadbeef", amount=1.0, neg_risk=True, live=False)

    # Assert — the preview names the contract --live would call.
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


# --- operator approval on the adapter --------------------------------------

def test_a_merge_refuses_when_the_target_is_not_an_approved_operator(monkeypatch, capsys):
    # Arrange — a wallet that has only ever merged standard markets has never
    # approved the negRisk adapter, so the first negRisk merge would revert on
    # approval rather than on routing.
    om = _stub_merge_env(monkeypatch, approved=False)
    monkeypatch.setenv("POLY_FUNDER", FUNDER)

    # Act
    with pytest.raises(SystemExit):
        om.merge("0xdeadbeef", amount=1.0, neg_risk=True, live=False)

    # Assert
    out = capsys.readouterr().out
    assert "has not approved" in out
    assert NEG_RISK_CTF_COLLATERAL_ADAPTER in out


def test_an_unreadable_approval_refuses_rather_than_assuming_approved(monkeypatch, capsys):
    # Arrange — "not approved" and "could not check" are different states.
    om = _stub_merge_env(monkeypatch, approved=None)
    monkeypatch.setenv("POLY_FUNDER", FUNDER)

    # Act
    with pytest.raises(SystemExit):
        om.merge("0xdeadbeef", amount=1.0, neg_risk=True, live=False)

    # Assert
    out = capsys.readouterr().out
    assert "Could not read whether" in out


def test_an_approved_operator_clears_that_guard(monkeypatch, capsys):
    # Arrange
    om = _stub_merge_env(monkeypatch, approved=True)
    monkeypatch.setenv("POLY_FUNDER", FUNDER)

    # Act — it still refuses (no key, so balances are unknown), but not for
    # approval.
    with pytest.raises(SystemExit):
        om.merge("0xdeadbeef", amount=1.0, neg_risk=True, live=False)

    # Assert
    out = capsys.readouterr().out
    assert "has not approved" not in out
    assert "Could not read whether" not in out


def test_the_approval_check_reads_the_ctf_and_decodes_the_word(monkeypatch):
    # Arrange — one canned eth_call reply per case.
    from core_brain import order_manager as om

    def _fake_urlopen(replies):
        import contextlib, io as _io, json as _json

        def _open(req, timeout=None):
            body = _json.dumps({"jsonrpc": "2.0", "id": 1, "result": replies}).encode()

            @contextlib.contextmanager
            def _cm():
                yield _io.BytesIO(body)

            return _cm()

        return _open

    import urllib.request

    true_word = "0x" + "0" * 63 + "1"
    false_word = "0x" + "0" * 64

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(true_word))
    assert om.is_approved_for_all(FUNDER, CTF_CONTRACT) is True

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(false_word))
    assert om.is_approved_for_all(FUNDER, CTF_CONTRACT) is False


def test_an_unset_funder_cannot_be_checked(monkeypatch):
    # Arrange / Act / Assert — no owner, no answer, and never a False.
    from core_brain import order_manager as om

    assert om.is_approved_for_all("", CTF_CONTRACT) is None
