"""Fork guard — 保護本 fork 對官方 mytonctrl 的自訂修改。

官方 CI 完全沒有涵蓋這些行為，所以上游重構時把它們洗掉不會被任何測試發現。
每一項對應一個自訂修改；任一失敗即代表改動遺失，自動同步流程會停止推進分支。
"""

import inspect

import pytest

from mytoncore.mytoncore import MyTonCore

# controller 的欄位順序，唯一來源是
# KTON-IO/liquid-staking-contract contracts/controller.func:620
# get_validator_controller_data()（已與 wrappers/Controller.ts:344 交叉驗證）。
# v1 回 14 個值、v2 回 19 個，兩者尾端都是 validator / pool / sudoer 三個位址，
# mytonctrl 不解析那三個。
CONTRACT_ORDER_V2 = [
    "state", "halted", "approved",
    "stake_amount_sent", "stake_at",
    "saved_validator_set_hash",
    "validator_set_changes_count",
    "validator_set_change_time",
    "stake_held_for",
    "interest", "allowed_borrow_start_prior_elections_end",
    "approver_set_profit_share", "acceptable_profit_share", "allocation",
    "borrowed_amount", "borrowing_time",
]
CONTRACT_TAIL = 3
V2_STACK_LEN = len(CONTRACT_ORDER_V2) + CONTRACT_TAIL   # 19
V1_STACK_LEN = 11 + CONTRACT_TAIL                        # 14
V2_ONLY = {"interest", "allowed_borrow_start_prior_elections_end",
           "approver_set_profit_share", "acceptable_profit_share", "allocation"}


def _controller_data(ton, monkeypatch, n_values):
    monkeypatch.setattr(ton.liteClient, "run", lambda cmd, **kw: "fake")
    monkeypatch.setattr(
        "mytoncore.mytoncore.lc_result_to_list",
        lambda result: None if n_values is None else list(range(n_values)),
    )
    return ton.GetControllerData("Ef_0000000000000000000000000000000000000000000000")


def test_controller_data_lst_v1(ton: MyTonCore, monkeypatch):
    """14 個值走 v1 欄位表，不可出現 v2 專屬欄位。"""
    data = _controller_data(ton, monkeypatch, V1_STACK_LEN)
    assert data is not None
    assert not (V2_ONLY & set(data)), "v1 不該出現 v2 專屬欄位"
    assert data["borrowed_amount"] == 9, "v1 的 borrowed_amount 應在索引 9"


def test_controller_data_lst_v2(ton: MyTonCore, monkeypatch):
    """19 個值必須逐欄對上合約的回傳順序。

    這是本 fork 存在的核心理由 —— 錯位會讓 controllers_list、
    get_controller_data、run_elections 全部讀到錯誤的值，而且不會報錯。
    """
    data = _controller_data(ton, monkeypatch, V2_STACK_LEN)
    assert data is not None
    assert list(data.keys()) == CONTRACT_ORDER_V2, (
        "欄位表與合約 get_validator_controller_data() 不一致\n"
        f"  實際: {list(data.keys())}\n  合約: {CONTRACT_ORDER_V2}"
    )
    for index, name in enumerate(CONTRACT_ORDER_V2):
        assert data[name] == index, f"{name} 對到索引 {data[name]}，應為 {index}"


def test_controller_data_none_is_handled(ton: MyTonCore, monkeypatch):
    """liteclient 解析失敗時回 None，不拋例外。"""
    assert _controller_data(ton, monkeypatch, None) is None


def test_recover_stake_command_exists():
    """recover_stake 指令必須存在且已註冊；後端函式由上游提供。"""
    from modules.controller import ControllerModule

    assert hasattr(ControllerModule, "recover_stake")
    assert "recover_stake" in inspect.getsource(ControllerModule.add_console_commands)
    assert hasattr(MyTonCore, "ControllerRecoverStake"), (
        "上游移除了 ControllerRecoverStake，recover_stake 指令會壞掉"
    )


def test_stake_branches_are_exclusive():
    """stake 計算的三個分支必須互斥（elif），不是連續的 if。"""
    src = inspect.getsource(MyTonCore)
    if "useController" not in src:
        pytest.skip("找不到 stake 計算區塊，上游可能已重構")
    assert "elif stake is None and useController" in src


# ── 四個防禦性修正 ────────────────────────────────────────────────
# 這些 bug 會讓 liquid staking 靜默停擺（沒有錯誤訊息），
# 上游若在重構中改回原樣，這裡要擋下來。

def test_using_controllers_has_default():
    """缺 list() 預設時，尚未 create_controllers 就會每 600 秒 TypeError。"""
    src = inspect.getsource(MyTonCore.ControllersUpdateValidatorSet)
    assert 'db.get("using_controllers", list())' in src, "using_controllers 讀取缺預設值"


def test_controllers_update_isolates_each_controller():
    """任一 controller 出錯不得中斷整個迴圈 —— 否則連帶讓
    run_elections 後面的 RecoverStake 與 ElectionEntry 整輪跳過。"""
    src = inspect.getsource(MyTonCore.ControllersUpdateValidatorSet)
    assert "try:" in src and "continue" in src, "缺少 per-controller 的例外隔離"


def test_periods_key_is_guarded():
    """db["periods"] 全 repo 沒有寫入處，直接索引會 KeyError
    導致 ElectionEntry 永久失敗。"""
    src = inspect.getsource(MyTonCore.ElectionEntry)
    assert 'db["periods"]' not in src, "periods 仍以直接索引存取，會 KeyError"


def test_pending_withdraw_is_saved():
    """pop 之後沒有 save()，重啟或 db 還原後會重複送出同一筆提款。"""
    src = inspect.getsource(MyTonCore.HandleControllerPendingWithdraw)
    assert "self.local.save()" in src, "pop 之後缺少 save()"
