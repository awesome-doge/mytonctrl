"""Fork guard — LSP v2 profit share: the fourth field of the loan request.

A v2 controller whose approver set a profit share rejects ``send_request_loan`` without
``acceptable_profit_share`` (exit 0xfa06), and the node only logs ``WaitLoan time out``.

Rules checked here:
- the field is sent whenever the controller has a share (a request without it cannot succeed),
  and also when the ``accept_profit_share`` setting is on (to arm ahead of time);
- with share 0 and the setting off the node sends exactly the request it always sent;
- a v1 controller never gets the field;
- the setting can only be stored as true / false.
"""

import os
import shutil
import subprocess

import pytest

from modules import SETTINGS
from mytoncore.mytoncore import MyTonCore
from mytoncore.utils import get_package_resource_path

LEGACY_SCRIPT = "jetton_pool/fift-scripts/generate-loan-request.fif"
NEW_SCRIPT = "contracts/liquid-staking/generate-loan-request-profit-share.fif"
CONTROLLER = "Ef_0000000000000000000000000000000000000000000000"
MAX_SHARE = "16777215"
V2 = {"borrowed_amount": 0, "state": 0, "approver_set_profit_share": 0, "acceptable_profit_share": 0}
V1 = {"borrowed_amount": 0, "state": 0}  # a v1 getter has no profit-share fields


class _Wallet:
    name = "wallet_001"


def _run(ton: MyTonCore, monkeypatch, controller_data, fift=None):
    """CreateLoanRequest with the node stubbed out; returns (fift calls, signed paths, log lines)."""
    calls, signed, logs = [], [], []
    ton.local.db["min_loan"] = 940000
    ton.local.db["max_loan"] = 2362965
    ton.local.db["max_interest_percent"] = 0.17
    monkeypatch.setattr(ton, "GetControllerData", lambda addr: dict(controller_data))
    monkeypatch.setattr(ton, "calculate_loan_amount", lambda *a: 1)
    monkeypatch.setattr(ton, "GetControllerRequiredBalanceForLoan", lambda *a: (0, 1))
    monkeypatch.setattr(ton, "GetValidatorWallet", lambda: _Wallet())
    monkeypatch.setattr(ton.fift, "run", fift or (lambda args, **kw: calls.append([str(a) for a in args])))
    monkeypatch.setattr(ton, "SignBocWithWallet", lambda wallet, path, dest, coins, **kw: signed.append(path) or path)
    monkeypatch.setattr(ton, "SendFile", lambda *a, **kw: None)
    monkeypatch.setattr(ton, "WaitLoan", lambda addr: None)
    monkeypatch.setattr(ton.local, "add_log", lambda text, level="info": logs.append((level, text)))
    ton.CreateLoanRequest(CONTROLLER)
    return calls, signed, logs


def _is_legacy(ton, call):
    return call[0] == ton.contractsDir + LEGACY_SCRIPT and call[1:4] == ["940000", "2362965", "28521"] and len(call) == 5


def _is_new(call):
    return call[0].endswith(NEW_SCRIPT) and call[1:5] == ["940000", "2362965", "28521", MAX_SHARE] and len(call) == 6


def test_setting_is_registered_and_off_by_default():
    setting = SETTINGS["accept_profit_share"]
    assert setting.mode == "liquid-staking"
    assert setting.default_value is False


# ---- which request is sent: controller share x setting ------------------------------------------

def test_share_zero_setting_off_is_exactly_the_request_sent_before(ton: MyTonCore, monkeypatch):
    ton.local.db.pop("accept_profit_share", None)
    calls, signed, logs = _run(ton, monkeypatch, V2)
    assert len(calls) == 1 and _is_legacy(ton, calls[0])
    assert calls[0][4].endswith("wallet_001_loan_request.boc") and signed == [calls[0][4]]
    assert not [text for level, text in logs if level == "warning"]


def test_share_zero_setting_on_arms_the_field(ton: MyTonCore, monkeypatch):
    ton.local.db["accept_profit_share"] = True
    calls, _, logs = _run(ton, monkeypatch, V2)
    assert len(calls) == 1 and _is_new(calls[0])
    assert MAX_SHARE == str(2**24 - 1) == str(MyTonCore.LOAN_ACCEPTABLE_PROFIT_SHARE)
    assert not [text for level, text in logs if level == "warning"]


@pytest.mark.parametrize("share", [1, 16273900, 16609443, 16777215])
def test_share_set_setting_on_sends_the_field(ton: MyTonCore, monkeypatch, share):
    ton.local.db["accept_profit_share"] = True
    calls, _, _ = _run(ton, monkeypatch, {**V2, "approver_set_profit_share": share})
    assert len(calls) == 1 and _is_new(calls[0])


@pytest.mark.parametrize("setting", [None, False, 0, "True", "on"])
def test_share_set_setting_off_still_sends_the_field_and_says_so(ton: MyTonCore, monkeypatch, setting):
    """The case that used to mean a silently missed election: the request without the field cannot succeed."""
    if setting is None:
        ton.local.db.pop("accept_profit_share", None)
    else:
        ton.local.db["accept_profit_share"] = setting
    calls, _, logs = _run(ton, monkeypatch, {**V2, "approver_set_profit_share": 16609443})
    assert len(calls) == 1 and _is_new(calls[0])
    warnings = [text for level, text in logs if level == "warning"]
    assert any("has profit share 16609443/16777216" in text and "0xfa06" in text for text in warnings)


@pytest.mark.parametrize("setting", [False, True])
def test_v1_controller_always_gets_the_legacy_request(ton: MyTonCore, monkeypatch, setting):
    """A v1 controller ends parsing after max_interest: the extra field would be rejected."""
    ton.local.db["accept_profit_share"] = setting
    calls, _, _ = _run(ton, monkeypatch, V1)
    assert len(calls) == 1 and _is_legacy(ton, calls[0])


def test_a_body_left_from_an_earlier_run_is_removed_before_the_new_one_is_built(ton: MyTonCore, monkeypatch, tmp_path):
    ton.local.db["accept_profit_share"] = True
    stale = ton.tempDir + ton.nodeName + "wallet_001_loan_request.boc"
    os.makedirs(os.path.dirname(stale), exist_ok=True)
    open(stale, "wb").write(b"old")
    seen = []
    _run(ton, monkeypatch, V2, fift=lambda args, **kw: seen.append(os.path.isfile(stale)))
    assert seen == [False]


def test_nothing_is_signed_when_the_script_fails(ton: MyTonCore, monkeypatch):
    ton.local.db["accept_profit_share"] = True

    def boom(args, **kw):
        raise Exception("fift failed")

    signed = []
    monkeypatch.setattr(ton, "SignBocWithWallet", lambda *a, **kw: signed.append(a))
    with pytest.raises(Exception, match="fift failed"):
        _run(ton, monkeypatch, V2, fift=boom)
    assert signed == []


# ---- the setting itself ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [None, False, 0, "false", "no", "true", "True", "on", "1", 2, 0.99, 99, [], {}])
def test_reading_only_a_boolean_true_or_one_counts_as_on(ton: MyTonCore, value):
    if value is None:
        ton.local.db.pop("accept_profit_share", None)
    else:
        ton.local.db["accept_profit_share"] = value
    assert ton.loan_request_accepts_profit_share() is False


@pytest.mark.parametrize("value", [True, 1])
def test_reading_true_is_on(ton: MyTonCore, value):
    ton.local.db["accept_profit_share"] = value
    assert ton.loan_request_accepts_profit_share() is True


@pytest.mark.parametrize("typed, stored", [("true", True), ("True", True), ("TRUE", True), ("false", False), ("False", False)])
def test_set_stores_a_real_boolean(cli, ton: MyTonCore, monkeypatch, typed, stored):
    """Through the console and the real SetSettings: "True" must not end up as the string 'True' (shown as on, read as off)."""
    monkeypatch.setattr(ton, "get_mode_value", lambda mode: True)
    monkeypatch.setattr(ton, "create_self_db_backup", lambda: None)
    output = cli.execute(f"set accept_profit_share {typed}", no_color=True)
    assert "SetSettings - OK" in output
    assert ton.local.db["accept_profit_share"] is stored
    assert ton.loan_request_accepts_profit_share() is stored


@pytest.mark.parametrize("typed", ["on", "yes", "1", "0", "0.97", "97", "16777215"])
@pytest.mark.parametrize("force", ["", " --force"])
def test_set_refuses_anything_else_even_with_force(cli, ton: MyTonCore, monkeypatch, typed, force):
    monkeypatch.setattr(ton, "get_mode_value", lambda mode: True)
    monkeypatch.setattr(ton, "create_self_db_backup", lambda: None)
    ton.local.db.pop("accept_profit_share", None)
    output = cli.execute(f"set accept_profit_share {typed}{force}", no_color=True)
    assert "accept_profit_share must be true or false" in output
    assert "SetSettings - OK" not in output
    assert "accept_profit_share" not in ton.local.db


def test_other_settings_are_stored_as_before(cli, ton: MyTonCore, monkeypatch):
    monkeypatch.setattr(ton, "get_mode_value", lambda mode: True)
    monkeypatch.setattr(ton, "create_self_db_backup", lambda: None)
    assert "SetSettings - OK" in cli.execute("set max_loan 2362965", no_color=True)
    assert ton.local.db["max_loan"] == 2362965


# ---- the script -----------------------------------------------------------------------------------

def test_the_script_is_package_data():
    with get_package_resource_path("mytoncore", NEW_SCRIPT) as path:
        text = open(path, encoding="utf-8").read()
    assert "max_interest 24 u, acceptable_profit_share 24 u, b>" in text
    for build_file in ("pyproject.toml", "setup.py"):
        assert '"contracts/liquid-staking/*"' in open(build_file, encoding="utf-8").read(), build_file


FIFT = os.getenv("FIFT_BIN") or shutil.which("fift")
FIFT_LIB = os.getenv("FIFT_LIB")
# the same body construction as igroman787/jetton_pool fift-scripts/generate-loan-request.fif (the script the hosts download), usage text shortened
LEGACY_SOURCE = '''#!/usr/bin/fift -s
"TonUtil.fif" include
$# dup 3 < swap 4 > or { 1 halt } if
4 :$1..n
$1 $>GR =: min_loan
$2 $>GR =: max_loan
$3 parse-int =: max_interest
$4 "loan_request.boc" replace-if-null =: file-base
<b 0x6335b11a 32 u, now 64 u, min_loan Gram, max_loan Gram, max_interest 24 u, b> 2 boc+>B
file-base B>file
'''


@pytest.mark.skipif(not (FIFT and FIFT_LIB), reason="set FIFT_BIN and FIFT_LIB to run the scripts for real")
def test_real_fift_the_new_body_is_the_legacy_body_plus_one_uint24(tmp_path):
    legacy_script = tmp_path / "legacy.fif"
    legacy_script.write_text(LEGACY_SOURCE)

    def body_bits(script, *args):
        out = tmp_path / (os.path.basename(str(script)) + ".boc")
        subprocess.run([FIFT, "-I", FIFT_LIB, "-s", str(script), *args, str(out)], check=True, capture_output=True)
        boc = out.read_bytes()
        assert boc[:4] == bytes.fromhex("b5ee9c72") and boc[4] & 7 == 1 and boc[5] == 1 and boc[6] == 1  # one cell, one root
        d1, d2 = boc[11], boc[12]
        assert d1 == 0  # no references
        return boc[13:13 + (d2 + 1) // 2], d2

    with get_package_resource_path("mytoncore", NEW_SCRIPT) as new_script:
        new, new_d2 = body_bits(new_script, "940000", "2362965", "28521", MAX_SHARE)
    legacy, legacy_d2 = body_bits(legacy_script, "940000", "2362965", "28521")
    assert legacy_d2 % 2 == 0 and new_d2 == legacy_d2 + 6  # byte aligned; 24 more bits
    assert new[:4] == legacy[:4] == bytes.fromhex("6335b11a")
    assert new[12:-3] == legacy[12:] and new[-3:] == b"\xff\xff\xff"  # same after the 64-bit query id, then the share
    with get_package_resource_path("mytoncore", NEW_SCRIPT) as new_script:  # a wrong argument count or an oversized share must fail loudly
        assert subprocess.run([FIFT, "-I", FIFT_LIB, "-s", str(new_script), "1", "2", "3", "16777216", str(tmp_path / "x.boc")], capture_output=True).returncode != 0


def test_wait_loan_timeout_names_the_controller_state_and_share(ton: MyTonCore, monkeypatch):
    """A rejected request must not surface as a bare "time out"."""
    monkeypatch.setattr("mytoncore.mytoncore.time.sleep", lambda s: None)
    monkeypatch.setattr(ton, "GetControllerData", lambda addr: {**V2, "approver_set_profit_share": 16609443})
    ton.local.db.pop("accept_profit_share", None)
    with pytest.raises(Exception) as err:
        ton.WaitLoan(CONTROLLER)
    text = str(err.value)
    assert text.startswith("WaitLoan error: time out")
    assert "controller state 0" in text and "approver_set_profit_share 16609443" in text and "setting False" in text
