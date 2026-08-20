"""KTON / pKTON liquid staking 池的狀態解析與風險判讀。

本 fork 相對於官方 mytonctrl 的自訂功能集中在這裡，刻意獨立成新檔案，
好讓與上游同步時的衝突面降到最低（官方不會動到不存在於上游的檔案）。

資料來源是 KTON-IO/liquid-staking-contract：
  - contracts/pool.func:772-813   compose_pool_full_data_internal()
  - contracts/pool.func:814/818   get_pool_full_data() / get_pool_full_data_raw()
  - contracts/controller.func:620 get_validator_controller_data()
欄位順序已與 wrappers/Pool.ts:710,831 及 wrappers/Controller.ts:344 交叉驗證。
"""

from __future__ import annotations

import base64
import re
from typing import Any

# get_pool_full_data_raw 的回傳順序（34 項）。
# 舊版 layout 只回 30 項，缺 instant_withdrawal_fee / accrued_governance_fee /
# disbalance_tolerance / credit_start_prior_elections_end，
# 對應 pool_storage.func load_data() 的 try/catch 與 Pool.ts:712,833 的版本偵測。
POOL_FIELDS_V2 = [
    "state",
    "halted",
    "total_balance",
    "interest_rate",
    "optimistic_deposit_withdrawals",
    "deposits_open",
    "instant_withdrawal_fee",
    "saved_validator_set_hash",
    "prev_round",
    "current_round",
    "min_loan_per_validator",
    "max_loan_per_validator",
    "governance_fee_share",
    "accrued_governance_fee",
    "disbalance_tolerance",
    "credit_start_prior_elections_end",
    "jetton_minter",
    "supply",
    "deposit_payout",
    "requested_for_deposit",
    "withdrawal_payout",
    "requested_for_withdrawal",
    "sudoer",
    "sudoer_set_at",
    "governor",
    "governor_update_after",
    "interest_manager",
    "halter",
    "approver",
    "controller_code",
    "pool_jetton_wallet_code",
    "payout_minter_code",
    "projected_total_balance",
    "projected_pool_supply",
]

# 舊版（30 項）缺少的四個欄位與其預設值，比照 Pool.ts:712-720
POOL_V1_MISSING = {
    "instant_withdrawal_fee": 0,
    "accrued_governance_fee": 0,
    "disbalance_tolerance": 30,
    "credit_start_prior_elections_end": 0,
}
POOL_V1_ABSENT_AT = [
    "instant_withdrawal_fee",
    "accrued_governance_fee",
    "disbalance_tolerance",
    "credit_start_prior_elections_end",
]

# round tuple：pool_storage.func:3-14
ROUND_FIELDS = [
    "borrowers_dict",
    "round_id",
    "active_borrowers",
    "borrowed",
    "expected",
    "returned",
    "profit",
]

SHARE_BASIS = 1 << 24            # pool.func：uint24 定點基準
NANO = 10**9
SUDO_QUARANTINE = 2 * 86400      # Conf.sudoQuarantine
GOVERNOR_QUARANTINE = 86400      # pool.func:91
SERVICE_NOTIFICATION_AMOUNT = 2 * 10**7   # 0.02 TON，pool.func:614-621
# uint48 全 1 = 「沒有排定的變更」，不是一個真的時間戳
NO_TIMESTAMP = (1 << 48) - 1

# controller.func:83-89
CONTROLLER_STATES = {
    0: "REST",
    1: "SENT_BORROWING_REQUEST",
    2: "SENT_STAKE_REQUEST",
    3: "FUNDS_STAKEN",
    4: "SENT_RECOVER_REQUEST",
    5: "INSOLVENT",
}
# MIN_TONS_FOR_STORAGE(2) + MAX_OVERDUE_FINE(40) + ELECTOR_OPERATION_VALUE(1.03)
ENSURABLE_BALANCE_FOR_STAKING = 43.03

_CELL_RE = re.compile(r"Cell\{([0-9a-fA-F]+)\}")


def slice_to_addr(value: Any) -> str | None:
    """把 lite-client 的 `CS{Cell{...} bits: ...}` slice 解成 friendly 位址。

    `()` 代表 addr_none，回 None。無法解析時也回 None（呼叫端自行判斷）。
    """
    if not isinstance(value, str) or not value.startswith("CS{"):
        return None
    match = _CELL_RE.search(value)
    if match is None:
        return None
    try:
        raw = bytes.fromhex(match.group(1))[2:]   # 去掉 2 bytes cell descriptor
    except ValueError:
        return None
    bits = "".join(f"{byte:08b}" for byte in raw)
    # addr_std$10 anycast:(Maybe Anycast) workchain_id:int8 address:bits256
    if len(bits) < 267 or bits[:2] != "10" or bits[2] != "0":
        return None
    workchain = int(bits[3:11], 2)
    if workchain > 127:
        workchain -= 256
    account = int(bits[11:267], 2).to_bytes(32, "big")
    payload = bytes([0x11, workchain & 0xFF]) + account
    crc = 0
    for byte in payload:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return base64.urlsafe_b64encode(payload + crc.to_bytes(2, "big")).decode()


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_round(value: Any) -> dict[str, Any]:
    """round tuple 由 parse_result_stack 回傳為字串，例如
    `[C{HEX} 507 1 2031393942726336 2032551593524084 0 0]`；
    空的 borrowers_dict 會是 `()`。
    """
    items: list[Any]
    if isinstance(value, list):
        items = list(value)  # pyright: ignore[reportUnknownArgumentType]
    elif isinstance(value, str):
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        items = text.split()
    else:
        return {name: None for name in ROUND_FIELDS}
    out: dict[str, Any] = {}
    for index, name in enumerate(ROUND_FIELDS):
        item = items[index] if index < len(items) else None
        out[name] = item if name == "borrowers_dict" else _to_int(item)
    return out


def parse_pool_full_data(stack: list[Any]) -> dict[str, Any]:
    """把 get_pool_full_data_raw 的 stack 映射成具名 dict。

    30 項 = 舊版 layout，補上四個缺席欄位的預設值。
    """
    values = list(stack)
    if len(values) == len(POOL_FIELDS_V2) - len(POOL_V1_ABSENT_AT):
        for name in POOL_V1_ABSENT_AT:
            values.insert(POOL_FIELDS_V2.index(name), POOL_V1_MISSING[name])
    if len(values) < len(POOL_FIELDS_V2):
        raise ValueError(
            f"get_pool_full_data_raw 回傳 {len(stack)} 項，"
            f"預期 {len(POOL_FIELDS_V2)} 或 {len(POOL_FIELDS_V2) - len(POOL_V1_ABSENT_AT)} 項"
        )

    data: dict[str, Any] = {}
    for name, value in zip(POOL_FIELDS_V2, values):
        if name in ("prev_round", "current_round"):
            data[name] = _parse_round(value)
        elif name in (
            "jetton_minter", "deposit_payout", "withdrawal_payout",
            "sudoer", "governor", "interest_manager", "halter", "approver",
        ):
            data[name] = slice_to_addr(value)
        elif name in ("controller_code", "pool_jetton_wallet_code", "payout_minter_code"):
            data[name] = value
        else:
            data[name] = _to_int(value)
    return data


def conversion_rate(data: dict[str, Any]) -> float | None:
    """TON per jetton。supply 為 0 時池子完全無法放貸（docs/peculiarities.md 3-4）。"""
    total = data.get("total_balance")
    supply = data.get("supply")
    if not isinstance(total, int) or not isinstance(supply, int) or supply == 0:
        return None
    return total / supply


def share_to_percent(value: Any) -> float | None:
    """uint24 定點 → 百分比。"""
    number = _to_int(value)
    if number is None:
        return None
    return number / SHARE_BASIS * 100


def check_pool_risks(
    data: dict[str, Any],
    *,
    projected_halted: bool | None = None,
    loan_amount: int | None = None,
    elections_open: bool = False,
    now: int = 0,
) -> list[tuple[str, str, str]]:
    """回傳 [(severity, key, message)]，severity ∈ {crit, warn, info}。

    每一條都對應合約裡一個具體的失效路徑，註解標出來源行號。
    """
    out: list[tuple[str, str, str]] = []

    # pool.func:678-688 —— update_round 覆蓋不了待提款時會就地 halt。
    # 這個 halt 只存在於帶 update_round 的 getter，raw 版看不到，
    # 所以兩者不一致 = 尚未落盤的「提款覆蓋不足」預警。
    if projected_halted and not data.get("halted"):
        out.append((
            "crit", "pool_cannot_cover_withdrawals",
            "池子帳面正常，但套用 update_round 後會 halt —— 代表現有餘額覆蓋不了待處理提款",
        ))

    if data.get("halted"):
        out.append(("crit", "pool_halted", "池子已 halted，所有存提與借貸停止"))

    # pool.func:222-230 —— instant 路徑收取 fee，使用者實收 amount/2^24
    fee = data.get("instant_withdrawal_fee") or 0
    if fee and data.get("optimistic_deposit_withdrawals"):
        percent = share_to_percent(fee) or 0.0
        out.append((
            "crit", "instant_withdrawal_fee",
            f"instant 提款費 {percent:.6f}% 且 optimistic 模式開啟 —— "
            "走 instant 路徑的使用者會損失本金",
        ))

    if not data.get("deposits_open"):
        out.append(("warn", "deposits_closed", "存款已關閉"))

    # docs/peculiarities.md 3-4
    total = data.get("total_balance") or 0
    supply = data.get("supply") or 0
    if total == 0 and supply > 0:
        out.append(("crit", "zero_total_balance", "total_balance 為 0 但 supply 不為 0 —— 存款會失敗"))
    if total > 0 and supply == 0:
        out.append(("crit", "zero_supply", "supply 為 0 —— 池子完全無法放貸"))

    # pool.func:143-151 —— 前一輪沒還完就無法輪替
    prev = data.get("prev_round") or {}
    if (prev.get("active_borrowers") or 0) > 0 and loan_amount == -1:
        out.append((
            "crit", "round_stuck",
            f"上一輪仍有 {prev.get('active_borrowers')} 個借款人未結清，且池子無法放貸 —— 輪次卡住",
        ))
    elif loan_amount == -1 and elections_open:
        out.append(("crit", "cannot_lend", "選舉開放中但池子無法放貸（calculate_loan_amount 回 -1）"))

    if isinstance(prev.get("profit"), int) and prev["profit"] < 0:
        out.append((
            "warn", "negative_profit",
            f"上一輪損益為負：{prev['profit'] / NANO:.2f} TON —— 可能發生罰沒",
        ))

    # 隔離期內被更換的 sudoer 才是攻擊訊號；長期存在的自家 sudoer 不告警
    sudoer_set_at = data.get("sudoer_set_at") or 0
    if data.get("sudoer") and now and 0 < sudoer_set_at and now - sudoer_set_at < SUDO_QUARANTINE:
        out.append((
            "crit", "sudoer_quarantine",
            f"sudoer 於 {SUDO_QUARANTINE // 3600} 小時內被設定，仍在隔離期 —— 請確認是否為預期操作",
        ))

    governor_update_after = data.get("governor_update_after") or 0
    if now and 0 < governor_update_after < NO_TIMESTAMP and now < governor_update_after:
        out.append(("warn", "governor_quarantine", "governor 正在隔離期內，即將變更"))

    # governance_fee_share 接近 100% 代表本輪利潤幾乎全數歸營運方。
    # 這在營運方即為唯一存款人的私有池可能是刻意設定，但不該是隱形的。
    gov_fee = share_to_percent(data.get("governance_fee_share"))
    if gov_fee is not None and gov_fee >= 50:
        out.append((
            "warn", "high_governance_fee",
            f"governance_fee_share = {gov_fee:.4f}% —— 本輪利潤幾乎全數歸營運方，"
            "請確認是否為刻意設定",
        ))

    # pool.func:614-621 —— 低於門檻就永遠掃不出去
    accrued = data.get("accrued_governance_fee") or 0
    if 0 < accrued < SERVICE_NOTIFICATION_AMOUNT:
        out.append((
            "info", "governance_fee_dust",
            f"accrued_governance_fee {accrued / NANO:.4f} TON 低於 0.02 TON 門檻，不會被撥付",
        ))

    current = data.get("current_round") or {}
    if (current.get("active_borrowers") or 0) == 1:
        out.append(("info", "single_borrower", "本輪只有 1 個借款人 —— 驗證者分散度為 1"))

    return out


def check_controller_risks(
    controllers: list[dict[str, Any]],
) -> list[tuple[str, str, str]]:
    """controllers 每項需含 addr / balance(TON) / data(GetControllerData 結果)。"""
    out: list[tuple[str, str, str]] = []
    for item in controllers:
        addr = str(item.get("addr", ""))
        short = f"{addr[:8]}…" if addr else "?"
        data = item.get("data") or {}
        state = _to_int(data.get("state"))

        if state == 5:
            out.append((
                "crit", "controller_insolvent",
                f"controller {short} 進入 INSOLVENT —— 無力償還池子的借款",
            ))
        if data.get("halted"):
            out.append(("crit", "controller_halted", f"controller {short} 已 halted"))
        if data.get("approved") in (0, None):
            out.append((
                "warn", "controller_not_approved",
                f"controller {short} 未被池子認可，無法借款質押",
            ))

        balance = item.get("balance")
        if isinstance(balance, (int, float)) and balance < ENSURABLE_BALANCE_FOR_STAKING:
            out.append((
                "warn", "controller_low_balance",
                f"controller {short} 餘額 {balance:.2f} TON 低於質押所需的 "
                f"{ENSURABLE_BALANCE_FOR_STAKING} TON",
            ))
    return out


# ── 節點端借貸設定 ────────────────────────────────────────────────
# 這一段是「只有 mytonctrl 才知道」的部分：池子的 get method 看不到
# 這台節點打算借多少、願意付多少利息。設定與池子條件不匹配時，
# 借款會被靜默拒絕（calculate_loan_amount 回 -1，沒有任何錯誤訊息）。

# mytoncore.py CreateLoanRequest 用的預設值（與 modules/__init__.py 的
# Setting 預設值不一致，這是上游既有的缺陷，此處以實際生效的為準）
LOAN_SETTING_DEFAULTS = {
    "min_loan": 41000,
    "max_loan": 43000,
    "max_interest_percent": 1.5,
}


def percent_to_share(percent: float) -> int:
    """百分比 → uint24 定點，與 CreateLoanRequest 的換算一致。"""
    return int(percent / 100 * SHARE_BASIS)


def check_loan_settings(
    settings: dict[str, Any],
    pool: dict[str, Any],
    *,
    loan_amount: int | None = None,
    min_stake: float | None = None,
    elections_open: bool = False,
) -> list[tuple[str, str, str]]:
    """比對節點端借貸設定與池子/網路的條件。

    可貸額度相關的判準只在選舉開放時才有意義 —— 輪次進行中池子沒有
    閒置資金是正常狀態，那時候告警只會變成永久噪音。
    """
    out: list[tuple[str, str, str]] = []

    min_loan = settings.get("min_loan")
    max_loan = settings.get("max_loan")
    max_interest_percent = settings.get("max_interest_percent")

    pool_min = pool.get("min_loan_per_validator")
    pool_max = pool.get("max_loan_per_validator")
    pool_rate = pool.get("interest_rate")

    # pool.func:858 —— 願付利率低於池子要價，借款永遠被拒且沒有錯誤訊息
    if isinstance(max_interest_percent, (int, float)) and isinstance(pool_rate, int):
        if percent_to_share(float(max_interest_percent)) < pool_rate:
            pool_percent = share_to_percent(pool_rate) or 0.0
            out.append((
                "crit", "interest_below_pool_rate",
                f"max_interest_percent {max_interest_percent}% 低於池子要價 "
                f"{pool_percent:.4f}% —— 借款會被靜默拒絕",
            ))

    # pool.func:878-884 —— clamp 之後區間為空就借不到
    if isinstance(max_loan, (int, float)) and isinstance(pool_min, int):
        if max_loan * NANO < pool_min:
            out.append((
                "crit", "max_loan_below_pool_min",
                f"max_loan {max_loan:,} TON 低於池子的最低借款 "
                f"{pool_min / NANO:,.0f} TON —— 借款會被拒絕",
            ))
    if isinstance(min_loan, (int, float)) and isinstance(pool_max, int):
        if min_loan * NANO > pool_max:
            out.append((
                "crit", "min_loan_above_pool_max",
                f"min_loan {min_loan:,} TON 高於池子的最高借款 "
                f"{pool_max / NANO:,.0f} TON —— 借款會被拒絕",
            ))
    if (
        isinstance(min_loan, (int, float))
        and isinstance(max_loan, (int, float))
        and min_loan > max_loan
    ):
        out.append((
            "crit", "min_loan_above_max_loan",
            f"min_loan {min_loan:,} 大於 max_loan {max_loan:,} —— 設定本身矛盾",
        ))

    # 借到的錢要能達到網路最低質押，否則選舉會被 elector 拒絕。
    # 只在選舉開放時檢查：輪次中池子資金已部署出去，可貸額度本來就低。
    if elections_open and loan_amount is not None and loan_amount > 0 and min_stake:
        if loan_amount / NANO < min_stake:
            out.append((
                "warn", "loan_below_network_min_stake",
                f"本輪可貸 {loan_amount / NANO:,.0f} TON 低於網路最低質押 "
                f"{min_stake:,.0f} TON —— 即使借到也無法參選",
            ))

    return out


def check_controller_loan_readiness(
    controllers: list[dict[str, Any]],
) -> list[tuple[str, str, str]]:
    """controller 自有資金是否足以支撐借款。

    每項需含 addr / balance(TON) / required_for_loan(TON) / validator_amount(TON)。
    required_for_loan 來自 controller.func:667 required_balance_for_loan。
    """
    out: list[tuple[str, str, str]] = []
    for item in controllers:
        required = item.get("required_for_loan")
        available = item.get("validator_amount")
        if not isinstance(required, (int, float)) or not isinstance(available, (int, float)):
            continue
        addr = str(item.get("addr", ""))
        short = f"{addr[:8]}…" if addr else "?"
        if available < required:
            out.append((
                "warn", "controller_insufficient_for_loan",
                f"controller {short} 自有資金 {available:,.2f} TON 不足以支撐借款，"
                f"需要 {required:,.2f} TON（差 {required - available:,.2f}）",
            ))
    return out
