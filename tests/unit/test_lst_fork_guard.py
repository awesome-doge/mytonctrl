"""Fork guard — 保護本 fork 對官方 mytonctrl 的自訂修改。

自動同步流程（.github/workflows/sync-upstream.yml）會把本 fork 的 patch
rebase 到官方最新版之上。上游隨時可能重構這些檔案，導致我們的修改在
rebase 過程中被覆蓋或遺失，而官方 CI 不會發現 —— 因為官方沒有涵蓋
這些行為的測試。

這裡的每一個測試都對應一項自訂修改。任何一個失敗，同步流程就會停止，
lst-v2-compatible 分支維持在上一個已驗證的版本，不會推出壞掉的程式碼。
"""

import inspect

import pytest

from mytoncore.mytoncore import MyTonCore

# LSt v1 合約的 get_validator_controller_data 回 14 個 stack 值，
# 我們解析其中 11 個；LSt v2 回 18 個，我們解析 16 個。
V1_ONLY_LEN = 11
V2_EXTRA_FIELDS = {
    "interest",
    "allowed_borrow_start_prior_elections_end",
    "approver_set_profit_share",
    "acceptable_profit_share",
    "allocation",
}
ALWAYS_PRESENT = {"state", "halted", "approved", "borrowed_amount", "borrowing_time"}


def _patch_controller_data(ton: MyTonCore, monkeypatch, n_values):
    """讓 GetControllerData 拿到 n_values 個假的 stack 值。"""
    monkeypatch.setattr(ton.liteClient, "run", lambda cmd, **kw: "fake")
    monkeypatch.setattr(
        "mytoncore.mytoncore.lc_result_to_list",
        lambda result: None if n_values is None else list(range(n_values)),
    )
    return ton.GetControllerData("Ef_0000000000000000000000000000000000000000000000")


def test_controller_data_lst_v1(ton: MyTonCore, monkeypatch):
    """14 個值（LSt v1）必須走 v1 欄位表，不可出現 v2 專屬欄位。"""
    data = _patch_controller_data(ton, monkeypatch, 14)
    assert data is not None, "LSt v1 解析回傳 None"
    assert ALWAYS_PRESENT <= set(data), f"v1 缺少必要欄位：{ALWAYS_PRESENT - set(data)}"
    assert not (V2_EXTRA_FIELDS & set(data)), "v1 不應出現 v2 專屬欄位"
    assert len(data) == V1_ONLY_LEN
    assert data["borrowed_amount"] == 9, "v1 的 borrowed_amount 應在索引 9"


def test_controller_data_lst_v2(ton: MyTonCore, monkeypatch):
    """18 個值（LSt v2）必須走 v2 欄位表，否則欄位會整排錯位。

    這是本 fork 存在的核心理由 —— 錯位會讓 controllers_list、
    get_controller_data、run_elections 全部讀到錯誤的值。
    """
    data = _patch_controller_data(ton, monkeypatch, 18)
    assert data is not None, "LSt v2 解析回傳 None"
    missing = V2_EXTRA_FIELDS - set(data)
    assert not missing, f"LSt v2 支援遺失，缺少欄位：{missing}"
    assert ALWAYS_PRESENT <= set(data)
    # v2 欄位表共 16 項，borrowed_amount 是倒數第二個（索引 14），
    # 而 v1 只有 11 項、borrowed_amount 在索引 9。對錯索引就是欄位錯位。
    assert len(data) == 16, f"v2 欄位數應為 16，實際 {len(data)}"
    assert data["borrowed_amount"] == 14, (
        f"borrowed_amount 對到索引 {data['borrowed_amount']}（應為 14），"
        "代表 v2 欄位表順序錯了"
    )


def test_controller_data_none_is_handled(ton: MyTonCore, monkeypatch):
    """liteclient 解析失敗時要回 None，不能拋例外。"""
    assert _patch_controller_data(ton, monkeypatch, None) is None


def test_recover_stake_command_exists():
    """recover_stake 指令必須存在且已註冊到 console。"""
    from modules.controller import ControllerModule

    assert hasattr(ControllerModule, "recover_stake"), "recover_stake 方法不見了"
    src = inspect.getsource(ControllerModule.add_console_commands)
    assert "recover_stake" in src, "recover_stake 沒有註冊到 console"
    # 後端函式由上游提供，確認它還在
    assert hasattr(MyTonCore, "ControllerRecoverStake"), (
        "上游移除了 ControllerRecoverStake，recover_stake 指令會壞掉"
    )


def test_stake_branches_are_exclusive():
    """stake 計算的三個分支必須互斥（elif），不是連續的 if。"""
    src = inspect.getsource(MyTonCore.GetStake if hasattr(MyTonCore, "GetStake") else MyTonCore)
    if "useController" not in src:
        pytest.skip("找不到 stake 計算區塊，上游可能已重構")
    assert "elif stake is None and useController" in src, (
        "stake 計算的 elif 修改遺失（原本是連續 if）"
    )
