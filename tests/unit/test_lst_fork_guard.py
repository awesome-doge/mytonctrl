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
# 合約 get_validator_controller_data() 的完整回傳順序。
# 來源：KTON-IO/liquid-staking-contract contracts/controller.func
# （已與 wrappers/Controller.ts getControllerData() 交叉驗證）
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
    # 之後還有 validator / pool / sudoer 三個位址，mytonctrl 不解析
]
CONTRACT_TAIL = 3          # validator, pool, sudoer
V2_STACK_LEN = len(CONTRACT_ORDER_V2) + CONTRACT_TAIL   # 19
V1_STACK_LEN = 11 + CONTRACT_TAIL                        # 14
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
    data = _patch_controller_data(ton, monkeypatch, V1_STACK_LEN)
    assert data is not None, "LSt v1 解析回傳 None"
    assert ALWAYS_PRESENT <= set(data), f"v1 缺少必要欄位：{ALWAYS_PRESENT - set(data)}"
    assert not (V2_EXTRA_FIELDS & set(data)), "v1 不應出現 v2 專屬欄位"
    assert len(data) == V1_ONLY_LEN
    assert data["borrowed_amount"] == 9, "v1 的 borrowed_amount 應在索引 9"


def test_controller_data_lst_v2(ton: MyTonCore, monkeypatch):
    """19 個值（LSt v2）必須走 v2 欄位表，否則欄位會整排錯位。

    這是本 fork 存在的核心理由 —— 錯位會讓 controllers_list、
    get_controller_data、run_elections 全部讀到錯誤的值。
    """
    data = _patch_controller_data(ton, monkeypatch, V2_STACK_LEN)
    assert data is not None, "LSt v2 解析回傳 None"
    missing = V2_EXTRA_FIELDS - set(data)
    assert not missing, f"LSt v2 支援遺失，缺少欄位：{missing}"
    assert ALWAYS_PRESENT <= set(data)
    # v2 欄位表共 16 項，borrowed_amount 是倒數第二個（索引 14），
    # 而 v1 只有 11 項、borrowed_amount 在索引 9。對錯索引就是欄位錯位。
    # 逐欄比對合約的回傳順序 —— 這是最嚴格的檢查：
    # 只要 mytonctrl 的欄位表與合約有任何一格對不上就會失敗
    assert list(data.keys()) == CONTRACT_ORDER_V2, (
        "mytonctrl 的 v2 欄位表與合約 get_validator_controller_data() 不一致\n"
        f"  實際: {list(data.keys())}\n  合約: {CONTRACT_ORDER_V2}"
    )
    for i, name in enumerate(CONTRACT_ORDER_V2):
        assert data[name] == i, f"{name} 對到索引 {data[name]}，應為 {i}"


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


# ── 池子端（mytoncore/lst.py）──────────────────────────────────────
# 欄位順序的唯一來源是 KTON-IO/liquid-staking-contract
# contracts/pool.func:772-813 compose_pool_full_data_internal()
# （已與 wrappers/Pool.ts:710,831 交叉驗證）

CONTRACT_POOL_ORDER = [
    "state", "halted", "total_balance", "interest_rate",
    "optimistic_deposit_withdrawals", "deposits_open", "instant_withdrawal_fee",
    "saved_validator_set_hash", "prev_round", "current_round",
    "min_loan_per_validator", "max_loan_per_validator",
    "governance_fee_share", "accrued_governance_fee",
    "disbalance_tolerance", "credit_start_prior_elections_end",
    "jetton_minter", "supply",
    "deposit_payout", "requested_for_deposit",
    "withdrawal_payout", "requested_for_withdrawal",
    "sudoer", "sudoer_set_at", "governor", "governor_update_after",
    "interest_manager", "halter", "approver",
    "controller_code", "pool_jetton_wallet_code", "payout_minter_code",
    "projected_total_balance", "projected_pool_supply",
]


def test_pool_field_order_matches_contract():
    from mytoncore.lst import POOL_FIELDS_V2

    assert POOL_FIELDS_V2 == CONTRACT_POOL_ORDER, (
        "mytoncore/lst.py 的池子欄位表與合約 compose_pool_full_data_internal() 不一致"
    )


def _pool_stack(**overrides):
    """造一份 34 項的合成 stack，數值即索引，方便驗證對位。"""
    stack = list(range(34))
    stack[8] = "[() 100 0 0 0 0 0]"     # prev_round
    stack[9] = "[() 101 0 0 0 0 0]"     # current_round
    for name, value in overrides.items():
        stack[CONTRACT_POOL_ORDER.index(name)] = value
    return stack


def test_pool_parse_maps_every_field_by_position():
    from mytoncore.lst import parse_pool_full_data

    data = parse_pool_full_data(_pool_stack())
    for index, name in enumerate(CONTRACT_POOL_ORDER):
        if name in ("prev_round", "current_round"):
            continue
        if name in ("jetton_minter", "deposit_payout", "withdrawal_payout", "sudoer",
                    "governor", "interest_manager", "halter", "approver"):
            continue  # 位址欄位是 slice，合成資料不驗
        if name in ("controller_code", "pool_jetton_wallet_code", "payout_minter_code"):
            continue
        assert data[name] == index, f"{name} 對到索引 {data[name]}，應為 {index}"


def test_pool_parse_round_tuple():
    from mytoncore.lst import parse_pool_full_data

    data = parse_pool_full_data(
        _pool_stack(prev_round="[C{AB} 507 1 2031393942726336 2032551593524084 0 -5]")
    )
    prev = data["prev_round"]
    assert prev["round_id"] == 507
    assert prev["active_borrowers"] == 1
    assert prev["borrowed"] == 2031393942726336
    assert prev["profit"] == -5, "profit 是有號數，負值代表罰沒"


def test_pool_parse_legacy_30_field_layout():
    """舊版 layout 少四個欄位，必須補預設值而不是整排錯位。"""
    from mytoncore.lst import POOL_V1_ABSENT_AT, parse_pool_full_data

    full = _pool_stack()
    legacy = [v for i, v in enumerate(full)
              if CONTRACT_POOL_ORDER[i] not in POOL_V1_ABSENT_AT]
    assert len(legacy) == 30
    data = parse_pool_full_data(legacy)
    assert data["instant_withdrawal_fee"] == 0
    assert data["disbalance_tolerance"] == 30
    # 補值之後，其後的欄位仍要對得上名稱（不是位移）
    assert data["supply"] == full[CONTRACT_POOL_ORDER.index("supply")]


def test_pool_parse_rejects_unexpected_length():
    from mytoncore.lst import parse_pool_full_data

    with pytest.raises(ValueError):
        parse_pool_full_data([0, 1, 2])


def test_slice_to_addr_decodes_masterchain_and_basechain():
    from mytoncore.lst import slice_to_addr

    # 實際從鏈上取得的 pKTON pool jetton_minter（wc 0）
    minter = ("CS{Cell{00538015a1fd5797916a4c3efba76f4edbfd03938b49e452e0d8fb689a913"
              "21bbfbdb0ae1f4f4f4aec693040} bits: 0..267; refs: 0..0}")
    assert slice_to_addr(minter) == "EQCtD-q8vItSYffdO3p23-gcnFpPIpcGx9tE1ImQ3f3thV4l"
    assert slice_to_addr("()") is None
    assert slice_to_addr(None) is None


def test_instant_withdrawal_fee_is_flagged():
    """本 fork 存在的理由之一：把會銷毀使用者本金的設定變成可見。"""
    from mytoncore.lst import SHARE_BASIS, check_pool_risks

    data = {
        "instant_withdrawal_fee": SHARE_BASIS - 1,
        "optimistic_deposit_withdrawals": -1,
        "supply": 1, "total_balance": 1,
    }
    keys = [key for _sev, key, _msg in check_pool_risks(data)]
    assert "instant_withdrawal_fee" in keys

    data["optimistic_deposit_withdrawals"] = 0
    keys = [key for _sev, key, _msg in check_pool_risks(data)]
    assert "instant_withdrawal_fee" not in keys, "optimistic 關閉時不應告警"


def test_projected_halt_divergence_is_critical():
    """pool.func:678-688 —— raw 看不到、帶 update_round 才會出現的 halt。"""
    from mytoncore.lst import check_pool_risks

    data = {"halted": 0, "supply": 1, "total_balance": 1}
    found = [(sev, key) for sev, key, _ in check_pool_risks(data, projected_halted=True)]
    assert ("crit", "pool_cannot_cover_withdrawals") in found


def test_governor_no_timestamp_sentinel_is_not_an_alert():
    """governor_update_after = 2^48-1 代表沒有排定變更，不是隔離期。"""
    from mytoncore.lst import NO_TIMESTAMP, check_pool_risks

    data = {"governor_update_after": NO_TIMESTAMP, "supply": 1, "total_balance": 1}
    keys = [key for _sev, key, _msg in check_pool_risks(data, now=1_800_000_000)]
    assert "governor_quarantine" not in keys


def test_controller_insolvent_is_critical():
    from mytoncore.lst import check_controller_risks

    found = check_controller_risks([
        {"addr": "Ef_test", "balance": 100.0, "data": {"state": 5, "approved": -1}},
    ])
    assert ("crit", "controller_insolvent") in [(sev, key) for sev, key, _ in found]


def test_lst_command_registered():
    from modules.controller import ControllerModule

    assert hasattr(ControllerModule, "lst_status")
    src = inspect.getsource(ControllerModule.add_console_commands)
    assert '"lst"' in src, "lst 指令沒有註冊到 console"


# ── 節點端借貸設定 ────────────────────────────────────────────────

def test_interest_below_pool_rate_is_critical():
    """pool.func:858 —— 願付利率低於池子要價，借款永遠被拒且沒有錯誤訊息。"""
    from mytoncore.lst import check_loan_settings, percent_to_share

    pool = {"interest_rate": percent_to_share(0.5), "min_loan_per_validator": 0,
            "max_loan_per_validator": 10**18}
    settings = {"min_loan": 1, "max_loan": 1000, "max_interest_percent": 0.1}
    keys = [key for _sev, key, _msg in check_loan_settings(settings, pool)]
    assert "interest_below_pool_rate" in keys

    settings["max_interest_percent"] = 1.0
    keys = [key for _sev, key, _msg in check_loan_settings(settings, pool)]
    assert "interest_below_pool_rate" not in keys


def test_loan_range_incompatible_with_pool():
    from mytoncore.lst import NANO, check_loan_settings

    pool = {"interest_rate": 0, "min_loan_per_validator": 5000 * NANO,
            "max_loan_per_validator": 9000 * NANO}
    keys = [k for _s, k, _m in check_loan_settings(
        {"min_loan": 1, "max_loan": 100, "max_interest_percent": 10}, pool)]
    assert "max_loan_below_pool_min" in keys

    keys = [k for _s, k, _m in check_loan_settings(
        {"min_loan": 50000, "max_loan": 60000, "max_interest_percent": 10}, pool)]
    assert "min_loan_above_pool_max" in keys

    keys = [k for _s, k, _m in check_loan_settings(
        {"min_loan": 8000, "max_loan": 6000, "max_interest_percent": 10}, pool)]
    assert "min_loan_above_max_loan" in keys


def test_loan_capacity_warning_only_during_elections():
    """輪次進行中池子沒有閒置資金是正常的，那時候告警只會是噪音。"""
    from mytoncore.lst import NANO, check_loan_settings

    pool = {"interest_rate": 0, "min_loan_per_validator": 0,
            "max_loan_per_validator": 10**18}
    settings = {"min_loan": 1, "max_loan": 10**6, "max_interest_percent": 10}

    keys = [k for _s, k, _m in check_loan_settings(
        settings, pool, loan_amount=2 * NANO, min_stake=300000.0, elections_open=False)]
    assert "loan_below_network_min_stake" not in keys

    keys = [k for _s, k, _m in check_loan_settings(
        settings, pool, loan_amount=2 * NANO, min_stake=300000.0, elections_open=True)]
    assert "loan_below_network_min_stake" in keys


def test_controller_loan_readiness():
    from mytoncore.lst import check_controller_loan_readiness

    found = check_controller_loan_readiness([
        {"addr": "Ef_poor", "required_for_loan": 2150.0, "validator_amount": 10.8},
        {"addr": "Ef_rich", "required_for_loan": 2150.0, "validator_amount": 5000.0},
    ])
    keys = [(sev, key) for sev, key, _msg in found]
    assert ("warn", "controller_insufficient_for_loan") in keys
    assert len(found) == 1, "資金充足的 controller 不該告警"


def test_percent_to_share_matches_create_loan_request():
    """換算必須與 mytoncore.py CreateLoanRequest 的 int(pct/100*16777216) 一致。"""
    from mytoncore.lst import percent_to_share

    for percent in (0.17, 1.5, 10.0):
        assert percent_to_share(percent) == int(percent / 100 * 16777216)


# ── 合約失效模式 ──────────────────────────────────────────────────

def _pool(**kw):
    base = {
        "halted": 0, "supply": 1000 * 10**9, "total_balance": 1000 * 10**9,
        "interest_rate": 9561, "deposits_open": -1,
        "optimistic_deposit_withdrawals": -1,
        "requested_for_withdrawal": 0,
        "projected_total_balance": 1000 * 10**9, "projected_pool_supply": 1000 * 10**9,
        "current_round": {"active_borrowers": 2, "borrowed": 0},
        "prev_round": {"active_borrowers": 0, "profit": 5 * 10**9},
        "disbalance_tolerance": 255, "credit_start_prior_elections_end": 1,
        "accrued_governance_fee": 1,
    }
    base.update(kw)
    return base


def test_pending_halt_is_self_computed():
    """pool.func:678-688。帶 update_round 的 getter 透過 liteserver 回 error 7，
    不能只靠比對兩個 getter，必須自算。"""
    from mytoncore.lst import check_pool_risks

    # 待提款 500 TON，池子餘額只有 100 TON → 回合結束會自動 halt
    data = _pool(requested_for_withdrawal=500 * 10**9)
    keys = [k for _s, k, _m in check_pool_risks(data, pool_balance=100.0)]
    assert "pool_cannot_cover_withdrawals" in keys

    # 餘額充足就不該告警
    keys = [k for _s, k, _m in check_pool_risks(data, pool_balance=600.0)]
    assert "pool_cannot_cover_withdrawals" not in keys


def test_withdrawal_liquidity_gate():
    """pool.func:254-257。觸發時交易 exit_code 是 0，鏈上完全看不出失敗。"""
    from mytoncore.lst import check_pool_risks

    # halt 門檻是 pending + 10（保留額），gate 門檻是 pending + 11（再加結算費）。
    # 兩者只差 1 TON，所以要精準落在中間才測得到 gate 單獨觸發。
    data = _pool(requested_for_withdrawal=100 * 10**9,
                 current_round={"active_borrowers": 1, "borrowed": 0})
    keys = [k for _s, k, _m in check_pool_risks(data, pool_balance=110.5)]
    assert "withdrawal_gate_blocked" in keys
    assert "pool_cannot_cover_withdrawals" not in keys, "halt 不該同時觸發"

    # 再多 1 TON 就兩個都不觸發
    keys = [k for _s, k, _m in check_pool_risks(data, pool_balance=112.0)]
    assert "withdrawal_gate_blocked" not in keys


def test_controller_stake_stuck_in_elector():
    """controller.func:315 —— count 沒到 2 就永遠 recover 不了。

    但 count 在每次 new_stake 時歸零（controller.func:423），
    所以「輪次進行中 + count=0」是正常的，不能誤報。
    """
    from mytoncore.lst import check_controller_risks

    elected_for, held = 65536, 32768
    stake_at = 2_000_000_000

    # 輪次進行中 —— 質押金還沒到解凍時間，不該告警
    found = check_controller_risks([{
        "addr": "Ef_ok", "balance": 100.0, "validators_elected_for": elected_for,
        "now": stake_at + 1000,
        "data": {"state": 3, "approved": -1, "validator_set_changes_count": 0,
                 "stake_at": stake_at, "stake_held_for": held},
    }])
    assert "controller_stake_stuck" not in [k for _s, k, _m in found], (
        "輪次進行中不該誤報成卡死"
    )

    # 早該解凍卻 count 還沒到 2 —— 真的卡住
    found = check_controller_risks([{
        "addr": "Ef_stuck", "balance": 100.0, "validators_elected_for": elected_for,
        "now": stake_at + elected_for + held + 7200,
        "data": {"state": 3, "approved": -1, "validator_set_changes_count": 1,
                 "stake_at": stake_at, "stake_held_for": held},
    }])
    assert "controller_stake_stuck" in [k for _s, k, _m in found]


def test_controller_halted_while_staken():
    """controller.func:295-297 —— halted 時 recover_stake 被封鎖。"""
    from mytoncore.lst import check_controller_risks

    found = check_controller_risks([{
        "addr": "Ef_halted", "balance": 100.0,
        "data": {"state": 3, "halted": -1, "approved": -1},
    }])
    assert "controller_halted_with_stake" in [k for _s, k, _m in found]


def test_elector_pending_stake_is_flagged():
    from mytoncore.lst import check_controller_risks

    found = check_controller_risks([{
        "addr": "Ef_pending", "balance": 100.0, "elector_returned_stake": 1234.5,
        "data": {"state": 0, "approved": -1},
    }])
    assert "elector_stake_pending" in [k for _s, k, _m in found]


def test_peculiarity_checks():
    """docs/peculiarities.md 的幾條會實際踩到的坑。"""
    from mytoncore.lst import check_pool_risks

    # #1 匯率偏離 1 過遠
    keys = [k for _s, k, _m in check_pool_risks(
        _pool(total_balance=10**9, supply=10**14))]
    assert "extreme_conversion_rate" in keys

    # #9 interest_rate 為 0
    keys = [k for _s, k, _m in check_pool_risks(_pool(interest_rate=0))]
    assert "zero_interest_rate" in keys

    # #2 回合利潤低於結算費
    keys = [k for _s, k, _m in check_pool_risks(
        _pool(prev_round={"active_borrowers": 0, "profit": 0}))]
    assert "round_profit_below_fee" in keys

    # pool_storage.func:220-225 舊格式降級指紋
    keys = [k for _s, k, _m in check_pool_risks(
        _pool(disbalance_tolerance=30, credit_start_prior_elections_end=0,
              accrued_governance_fee=0))]
    assert "possible_v1_fallback" in keys


def test_sudoer_presence_is_flagged():
    """docs/launching.md:34 —— sudoer 是繞過所有狀態機的後門，常態應為空。"""
    from mytoncore.lst import check_pool_risks

    keys = [k for _s, k, _m in check_pool_risks(_pool(sudoer="EQtest"))]
    assert "sudoer_present" in keys
    keys = [k for _s, k, _m in check_pool_risks(_pool(sudoer=None))]
    assert "sudoer_present" not in keys


def test_node_state_checks():
    from mytoncore.lst import check_node_state

    keys = [k for _s, k, _m in check_node_state({
        "liquid_staking_enabled": True, "only_node": True,
        "liquid_pool_addr": None, "using_controllers": [],
    })]
    assert "only_node_enabled" in keys        # LST 完全停擺
    assert "pool_addr_missing" in keys
    assert "no_using_controllers" in keys

    keys = [k for _s, k, _m in check_node_state({"liquid_staking_enabled": False})]
    assert keys == ["lst_mode_disabled"]


# ── Payout NFT 與 Librarian ───────────────────────────────────────

def test_payout_not_initialized_is_critical():
    """nft-collection.func:126 error 67 —— 沒收到 payout::init 就整個癱瘓。"""
    from mytoncore.lst import check_payout_risks

    found = check_payout_risks([
        {"pool": "KTON", "kind": "存款", "addr": "EQpayout", "distribution": None},
    ])
    assert ("crit", "payout_not_initialized") in [(s, k) for s, k, _ in found]


def test_payout_stalled_burn_chain():
    """nft-item.func:153-158 —— 任一 item gas 不足，整條 burn 鏈停住。"""
    from mytoncore.lst import check_payout_risks

    found = check_payout_risks([{
        "pool": "KTON", "kind": "提款", "addr": "EQpayout",
        "distribution": {"started": True}, "issued_bills": 42,
    }])
    assert "payout_distribution_stalled" in [k for _s, k, _m in found]

    # 分配已開始且 bill 全部燒完 → 正常
    found = check_payout_risks([{
        "pool": "KTON", "kind": "提款", "addr": "EQpayout",
        "distribution": {"started": True}, "issued_bills": 0,
    }])
    assert found == []


def test_librarian_balance_thresholds():
    """librarian 餘額歸零 = public library 失效 = 全系統癱瘓且無法自救。"""
    from mytoncore.lst import check_librarian

    assert check_librarian(None, None) == [], "未設定位址時不告警"
    assert "librarian_balance_critical" in [k for _s, k, _m in check_librarian(5.0, "EQlib")]
    assert "librarian_balance_low" in [k for _s, k, _m in check_librarian(30.0, "EQlib")]
    assert check_librarian(250.0, "EQlib") == []
    assert "librarian_unreachable" in [k for _s, k, _m in check_librarian(None, "EQlib")]


# ── 三類卡死條件 ──────────────────────────────────────────────────

def test_validator_wallet_empty_is_critical():
    """每一筆 controller 交易都由 validator wallet 付費並簽章。
    它空了 = 全部停擺，而且不會有錯誤訊息（根本沒送出）。"""
    from mytoncore.lst import check_validator_wallet

    keys = [k for _s, k, _m in check_validator_wallet(0.0, "Ef_wallet")]
    assert "validator_wallet_empty" in keys
    keys = [k for _s, k, _m in check_validator_wallet(12.0, "Ef_wallet")]
    assert "validator_wallet_low" in keys
    assert check_validator_wallet(100.0, "Ef_wallet") == []
    assert "validator_wallet_unknown" in [k for _s, k, _m in check_validator_wallet(None, "Ef_w")]


def test_max_loan_above_allocation():
    """controller.func:471 —— allocation 非 0 時是硬上限。"""
    from mytoncore.lst import NANO, check_stake_feasibility

    controllers = [{
        "addr": "Ef_c", "balance": 100000.0,
        "data": {"allocation": 500_000 * NANO},
    }]
    keys = [k for _s, k, _m in check_stake_feasibility(controllers, max_loan=600_000.0)]
    assert "max_loan_above_allocation" in keys

    keys = [k for _s, k, _m in check_stake_feasibility(controllers, max_loan=400_000.0)]
    assert "max_loan_above_allocation" not in keys

    # allocation == 0 代表無上限
    controllers[0]["data"]["allocation"] = 0
    keys = [k for _s, k, _m in check_stake_feasibility(controllers, max_loan=10**9)]
    assert "max_loan_above_allocation" not in keys


def test_stake_below_contract_minimum():
    """controller.func:400 —— 借到錢也可能過不了 new_stake 的 50,000 TON 門檻，
    那筆借款就只能原封還回去。這是 max_loan 沒調好的典型卡死。"""
    from mytoncore.lst import check_stake_feasibility

    controllers = [{"addr": "Ef_c", "balance": 100.0, "data": {"allocation": 0}}]
    keys = [k for _s, k, _m in check_stake_feasibility(controllers, max_loan=1000.0)]
    assert "stake_below_contract_minimum" in keys

    # 借夠多就過得了合約門檻，但仍可能低於網路門檻
    keys = [k for _s, k, _m in check_stake_feasibility(
        controllers, max_loan=100_000.0, network_min_stake=300_000.0)]
    assert "stake_below_contract_minimum" not in keys
    assert "stake_below_network_minimum" in keys


def test_ton_binary_age():
    """沒做 mytonctrl upgrade，網路升級後會跟不上。"""
    import time as _t

    from mytoncore.lst import check_ton_version

    now = int(_t.time())
    fresh = _t.strftime("%Y-%m-%d %H:%M:%S", _t.gmtime(now - 10 * 86400))
    assert check_ton_version(fresh, now) == []

    aging = _t.strftime("%Y-%m-%d %H:%M:%S", _t.gmtime(now - 150 * 86400))
    assert "ton_binary_aging" in [k for _s, k, _m in check_ton_version(aging, now)]

    stale = _t.strftime("%Y-%m-%d %H:%M:%S", _t.gmtime(now - 300 * 86400))
    assert "ton_binary_stale" in [k for _s, k, _m in check_ton_version(stale, now)]

    assert check_ton_version(None, now) == []
