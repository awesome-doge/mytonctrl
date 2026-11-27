"""Fork guard — LSP v2 profit share: the fourth field of the loan request.

A controller whose approver set a profit share rejects ``send_request_loan`` without
``acceptable_profit_share`` (exit 0xfa06), and the node only logs ``WaitLoan time out``.
With the ``accept_profit_share`` setting on, the request carries the field; with it off
(the default) the node must send exactly the request it always sent.
"""

import pytest

from modules import SETTINGS
from mytoncore.mytoncore import MyTonCore
from mytoncore.utils import get_package_resource_path

LEGACY_SCRIPT = "jetton_pool/fift-scripts/generate-loan-request.fif"
NEW_SCRIPT = "contracts/liquid-staking/generate-loan-request-profit-share.fif"
CONTROLLER = "Ef_0000000000000000000000000000000000000000000000"


class _Wallet:
    name = "wallet_001"


def _run_create_loan_request(ton: MyTonCore, monkeypatch):
    """Runs CreateLoanRequest with everything that touches the node stubbed out; returns the fift calls."""
    calls = []
    ton.local.db["min_loan"] = 940000
    ton.local.db["max_loan"] = 2362965
    ton.local.db["max_interest_percent"] = 0.17
    monkeypatch.setattr(ton, "GetControllerData", lambda addr: {"borrowed_amount": 0})
    monkeypatch.setattr(ton, "calculate_loan_amount", lambda *a: 1)
    monkeypatch.setattr(ton, "GetControllerRequiredBalanceForLoan", lambda *a: (0, 1))
    monkeypatch.setattr(ton, "GetValidatorWallet", lambda: _Wallet())
    monkeypatch.setattr(ton.fift, "run", lambda args, **kw: calls.append([str(a) for a in args]))
    monkeypatch.setattr(ton, "SignBocWithWallet", lambda wallet, path, dest, coins, **kw: path)
    monkeypatch.setattr(ton, "SendFile", lambda *a, **kw: None)
    monkeypatch.setattr(ton, "WaitLoan", lambda addr: None)
    ton.CreateLoanRequest(CONTROLLER)
    return calls


def test_setting_is_registered_and_off_by_default():
    setting = SETTINGS["accept_profit_share"]
    assert setting.mode == "liquid-staking"
    assert setting.default_value is False


@pytest.mark.parametrize("value", [None, False, 0, "false", "no", "true", "1", 2, 0.99, 99, [], {}])
def test_only_an_explicit_true_turns_it_on(ton: MyTonCore, value):
    """`set` stores whatever json.loads gives; a stray string or a percent must not switch the request format."""
    if value is None:
        ton.local.db.pop("accept_profit_share", None)
    else:
        ton.local.db["accept_profit_share"] = value
    assert ton.loan_request_accepts_profit_share() is False


@pytest.mark.parametrize("value", [True, 1])
def test_true_turns_it_on(ton: MyTonCore, value):
    ton.local.db["accept_profit_share"] = value
    assert ton.loan_request_accepts_profit_share() is True


def test_off_sends_exactly_the_legacy_request(ton: MyTonCore, monkeypatch):
    """Setting absent: same script, same four arguments, in the same order as before the fork change."""
    ton.local.db.pop("accept_profit_share", None)
    calls = _run_create_loan_request(ton, monkeypatch)
    assert len(calls) == 1
    script, min_loan, max_loan, max_interest, out = calls[0]
    assert script == ton.contractsDir + LEGACY_SCRIPT
    assert (min_loan, max_loan, max_interest) == ("940000", "2362965", str(int(0.17 / 100 * 16777216)))
    assert out.endswith("wallet_001_loan_request.boc")


def test_on_sends_the_fourth_field_at_the_top_of_the_scale(ton: MyTonCore, monkeypatch):
    ton.local.db["accept_profit_share"] = True
    calls = _run_create_loan_request(ton, monkeypatch)
    assert len(calls) == 1
    script, min_loan, max_loan, max_interest, share, out = calls[0]
    assert script.endswith(NEW_SCRIPT)
    assert (min_loan, max_loan, max_interest) == ("940000", "2362965", "28521")
    assert share == "16777215" == str(2**24 - 1) == str(MyTonCore.LOAN_ACCEPTABLE_PROFIT_SHARE)
    assert out.endswith("wallet_001_loan_request.boc")


def test_the_script_ships_with_the_package_and_has_the_field():
    """The script must be package data (pyproject / setup.py), and differ from the legacy body only by one uint24."""
    with get_package_resource_path("mytoncore", NEW_SCRIPT) as path:
        text = open(path, encoding="utf-8").read()
    assert "0x6335b11a 32 u, now 64 u, min_loan Gram, max_loan Gram, max_interest 24 u, acceptable_profit_share 24 u, b>" in text
    assert "$4 parse-int =: acceptable_profit_share" in text
    for build_file in ("pyproject.toml", "setup.py"):
        assert '"contracts/liquid-staking/*"' in open(build_file, encoding="utf-8").read(), build_file
