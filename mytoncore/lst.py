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
POOL_MIN_TONS_FOR_STORAGE = 10 * NANO     # pool.func:76
FINALIZE_ROUND_FEE = NANO                 # 1 TON，pool.func:85
MAX_LOAN_DICT_DEPTH = 12                  # pool.func:73
CONTROLLER_GRACE_PERIOD = 600             # controller.func:33
# pool_storage.func:220-225 —— 舊格式解析成功時會填入的預設值組合。
# 三者同時等於這些值，代表儲存可能被當成 V1 格式解析（V2 參數被靜默重設）。
V1_FALLBACK_FINGERPRINT = {
    "disbalance_tolerance": 30,
    "credit_start_prior_elections_end": 0,
    "accrued_governance_fee": 0,
}
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


def _pending_withdrawal_ton(data: dict[str, Any]) -> int | None:
    """把 requested_for_withdrawal（以 jetton 計價）換算成 TON。

    合約用投影匯率換算（pool.func:676, :690）。
    """
    requested = data.get("requested_for_withdrawal")
    projected_balance = data.get("projected_total_balance")
    projected_supply = data.get("projected_pool_supply")
    if not isinstance(requested, int):
        return None
    if requested == 0:
        return 0
    if not isinstance(projected_balance, int) or not isinstance(projected_supply, int):
        return None
    if projected_supply == 0:
        return None
    return requested * projected_balance // projected_supply


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
    pool_balance: float | None = None,
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

    # pool.func:678-688 自算版本。
    # 帶 update_round 的 get_pool_full_data 透過 liteserver 會回 error 7，
    # 所以不能只靠比對兩個 getter —— 這裡直接複製合約的判斷式。
    pending = _pending_withdrawal_ton(data)
    if pool_balance is not None and pending is not None:
        balance_nano = int(pool_balance * NANO)
        if balance_nano < pending + POOL_MIN_TONS_FOR_STORAGE:
            out.append((
                "crit", "pool_cannot_cover_withdrawals",
                f"池子餘額 {balance_nano / NANO:,.2f} TON 不足以覆蓋待處理提款 "
                f"{pending / NANO:,.2f} TON + 10 TON 保留額 —— "
                "回合結束時會自動 halt（pool.func:678-688）",
            ))
        else:
            # pool.func:254-257 的流動性守門。觸發時交易 exit_code 是 0，
            # 使用者只看到 KTON 被退回，鏈上完全看不出失敗。
            # 門檻比上面的 halt 高 1 TON（FINALIZE_ROUND_FEE），
            # 所以它是「還沒到 halt、但提款已經開始被擋」的更早期訊號。
            available = balance_nano - POOL_MIN_TONS_FOR_STORAGE
            borrowed = (data.get("current_round") or {}).get("borrowed") or 0
            funds_at_round_end = max(available + borrowed - FINALIZE_ROUND_FEE, 0)
            if pending > 0 and funds_at_round_end < pending:
                out.append((
                    "crit", "withdrawal_gate_blocked",
                    f"回合結束時可用資金 {funds_at_round_end / NANO:,.2f} TON 低於"
                    f"待提款需求 {pending / NANO:,.2f} TON —— "
                    "新的提款請求會被靜默退回（交易顯示成功但 KTON 原封退還）",
                ))

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
    if not data.get("optimistic_deposit_withdrawals"):
        out.append((
            "info", "optimistic_disabled",
            "樂觀（即時）存提已關閉 —— 使用者必須等回合結束才能拿到資產",
        ))

    # docs/peculiarities.md #1 —— 匯率偏離 1 太遠時，換算的四捨五入誤差
    # 會被全體持有者瓜分
    rate = conversion_rate(data)
    if rate is not None and (rate < 0.01 or rate > 100):
        out.append((
            "warn", "extreme_conversion_rate",
            f"匯率 {rate:.6f} 偏離 1 過遠 —— 換算的捨入誤差會造成存提損失",
        ))

    # docs/peculiarities.md #9 + changelog.md:40-41
    if data.get("interest_rate") == 0:
        out.append((
            "warn", "zero_interest_rate",
            "interest_rate 為 0 —— 官方明確不建議只靠 revenue share",
        ))

    # docs/peculiarities.md #8 —— 舊儲存沒有 treasury 時會 fallback 成 interest_manager
    treasury_like = data.get("interest_manager")
    if treasury_like and treasury_like == data.get("governor"):
        out.append((
            "info", "treasury_shared_with_governor",
            "interest_manager 與 governor 是同一個位址 —— 確認 treasury 是否已獨立設定",
        ))

    # docs/launching.md:34 建議 sudoer 常態為空；非空即是可繞過所有狀態機的後門
    if data.get("sudoer"):
        out.append((
            "warn", "sudoer_present",
            f"sudoer 已設定（{str(data['sudoer'])[:12]}…）—— "
            "它能以池子名義發送任意訊息，官方建議常態保持為空",
        ))

    # pool_storage.func:211-227 —— 儲存被當成舊格式解析的指紋
    if all(data.get(k) == v for k, v in V1_FALLBACK_FINGERPRINT.items()):
        out.append((
            "info", "possible_v1_fallback",
            "disbalance_tolerance=30 / credit_start=0 / accrued_fee=0 同時成立 —— "
            "可能是儲存被當成舊格式解析（V2 參數遭重設），請確認是否為真實設定",
        ))

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

    # docs/peculiarities.md #2 —— 回合利潤低於 FINALIZE_ROUND_FEE 通常代表
    # 該回合根本沒有運作中的 validator，損失由持有者社會化
    if isinstance(prev.get("profit"), int) and 0 <= prev["profit"] < FINALIZE_ROUND_FEE:
        out.append((
            "warn", "round_profit_below_fee",
            f"上一輪利潤 {prev['profit'] / NANO:.4f} TON 低於 1 TON 的結算費 —— "
            "通常代表該回合沒有運作中的 validator",
        ))

    # pool.func:565 —— borrowers dict 太深會讓新借款全部失敗
    current_borrowers = (data.get("current_round") or {}).get("active_borrowers") or 0
    if current_borrowers >= MAX_LOAN_DICT_DEPTH:
        out.append((
            "warn", "borrowers_dict_deep",
            f"本輪借款人 {current_borrowers} 個，接近 dict 深度上限 "
            f"{MAX_LOAN_DICT_DEPTH} —— 超過後新借款會全部失敗",
        ))

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
        # controller.func:315,341 —— count 沒到 2 就永遠 recover 不了。
        # 若 controller 中途 halted 或 INSOLVENT，count 會凍結，
        # 質押金就永久卡在 elector。
        count = _to_int(data.get("validator_set_changes_count"))
        change_time = _to_int(data.get("validator_set_change_time")) or 0
        now = item.get("now") or 0
        if (
            state == 3
            and isinstance(count, int)
            and count < 2
            and now
            and change_time
            and now - change_time > CONTROLLER_GRACE_PERIOD
        ):
            out.append((
                "crit", "controller_stake_stuck",
                f"controller {short} 在 FUNDS_STAKEN 但 validator_set_changes_count="
                f"{count}（需要 ≥2）—— 質押金無法從 elector 取回，"
                "請確認 update_validator_hash 有在執行",
            ))

        # controller.func:295-297 —— halted 時 recover_stake 被封鎖。
        # 若同時處於 FUNDS_STAKEN，資金就卡在 elector 且只能靠 sudoer 救。
        if data.get("halted") and state == 3:
            out.append((
                "crit", "controller_halted_with_stake",
                f"controller {short} 已 halted 且處於 FUNDS_STAKEN —— "
                "recover_stake 被封鎖，質押金卡在 elector，需 governor 執行 unhalt",
            ))

        # controller.func:212-217 —— INSOLVENT 的救援門檻
        if state == 5:
            borrowed = _to_int(data.get("borrowed_amount")) or 0
            need = 2 + 10 + borrowed / NANO + 0.25
            out.append((
                "info", "controller_insolvent_recovery",
                f"controller {short} 若要脫離 INSOLVENT，餘額需補到超過 "
                f"{need:,.2f} TON（top_up 任何人都能做）",
            ))

        # elector 裡還有錢沒領回來
        returned = item.get("elector_returned_stake")
        if isinstance(returned, (int, float)) and returned > 0:
            out.append((
                "warn", "elector_stake_pending",
                f"controller {short} 在 elector 有 {returned:,.2f} TON 可回收但尚未領回",
            ))

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


# ── 節點端狀態（鏈上查不到，只存在於 mytoncore.db）──────────────────
# 這些 key 決定「這台節點為什麼參選 / 為什麼不參選」，但既有的
# status / controllers_list / settings_status 都不顯示它們：
# 四份 controller 清單與 controllerPendingWithdraws 都不在 SETTINGS 裡。

NODE_STATE_KEYS = [
    "using_controllers",
    "old_controllers",
    "user_controllers",
    "stop_controllers_list",
]


def controller_stake_readiness(
    addr: str,
    controller_data: dict[str, Any] | None,
    *,
    stop_list: list[str],
    validators_elected_for: int | None,
    now: int,
) -> tuple[bool, str]:
    """複製 IsControllerReadyToStake 的判準並回傳「為什麼」。

    上游那個函式（mytoncore.py IsControllerReadyToStake）只回 bool，
    理由靠一行 raw print 噴進 systemd journal 而不是 log 檔 ——
    那是判斷「controller 為何未參選」的唯一線索卻查不到。這裡把它變成可讀的。
    """
    if addr in stop_list:
        return False, "在 stop_controllers_list 中（人為停用）"
    if not controller_data:
        return False, "讀不到 controller 資料"
    stake_at = _to_int(controller_data.get("stake_at")) or 0
    stake_held_for = _to_int(controller_data.get("stake_held_for")) or 0
    if validators_elected_for is None:
        return False, "讀不到 config15，無法判斷冷卻期"
    ready_at = stake_at + validators_elected_for + stake_held_for
    if ready_at >= now:
        return False, f"冷卻期未過，還需 {ready_at - now} 秒"
    return True, "可參選"


def check_node_state(state: dict[str, Any]) -> list[tuple[str, str, str]]:
    """節點端設定與清單的健康檢查。

    state 需含：liquid_staking_enabled / only_node / liquid_pool_addr /
    using_controllers / old_controllers / user_controllers /
    stop_controllers_list / pending_withdraws / participate_before_end /
    backup_age_sec
    """
    out: list[tuple[str, str, str]] = []

    if not state.get("liquid_staking_enabled"):
        out.append(("info", "lst_mode_disabled", "liquid-staking 模式未啟用，本機不參與 LST"))
        return out

    # background_runner.py:243-246 —— onlyNode 為真時選舉迴圈根本不註冊，
    # LST 會完全停擺而且不會有任何錯誤訊息
    if state.get("only_node"):
        out.append((
            "crit", "only_node_enabled",
            "onlyNode 設定為真 —— 背景選舉迴圈不會啟動，LST 完全停擺且無任何錯誤訊息",
        ))

    if not state.get("liquid_pool_addr"):
        out.append((
            "crit", "pool_addr_missing",
            "liquid-staking 已啟用但 liquid_pool_addr 未設定 —— 每輪選舉都會失敗",
        ))

    using = state.get("using_controllers") or []
    if not using:
        out.append((
            "crit", "no_using_controllers",
            "using_controllers 是空的 —— 尚未執行 create_controllers，無法參選",
        ))

    pending = state.get("pending_withdraws") or {}
    if pending:
        out.append((
            "warn", "pending_withdraws",
            f"有 {len(pending)} 筆提款排隊中，等 controller 回到 REST 才會送出："
            + ", ".join(f"{a[:8]}…" for a in pending),
        ))

    # mytoncore.py 的 periods key 全 repo 沒有寫入處，舊版讀到就 KeyError，
    # 會讓 ElectionEntry 永久失敗。本 fork 已修，但生產機若還跑舊版就有風險。
    if state.get("participate_before_end") is not None:
        out.append((
            "warn", "participate_before_end_set",
            "participateBeforeEnd 已設定 —— 未修正的 mytonctrl 會在此觸發 "
            "KeyError('periods') 導致 ElectionEntry 永久失敗，請確認節點已更新",
        ))

    # background_runner.py:217-232 —— db 讀取失敗會從 backup 還原，
    # 而 do_stop_controller / do_add_controller 只 save() 不建 backup
    backup_age = state.get("backup_age_sec")
    if isinstance(backup_age, (int, float)) and backup_age > 6 * 3600:
        out.append((
            "info", "stale_db_backup",
            f"mytoncore.db 備份已 {backup_age / 3600:.1f} 小時未更新 —— "
            "若觸發自動還原，controller 清單可能回滾到那個時間點",
        ))

    return out


# ── Payout NFT collection ────────────────────────────────────────
# 每個 round 會部署一對 payout collection（存款側與提款側），位址含
# random_seed 因此無法離線推算，只能從 pool 的 deposit_payout /
# withdrawal_payout 欄位動態取得（address_calculations.func:74-77）。
#
# 分配是一條 burn 鏈：從最新的 NFT 開始，每個 item 用 0.01 TON 通知下一個
# （nft-item.func:153-158）。任一 item 的餘額不足就整條鏈停住，
# 其後的持有者永遠拿不到錢，而且鏈上沒有任何錯誤。

PAYOUT_MIN_ITEM_TON = 0.1   # min_tons_for_storage(0.09) + burn_notification(0.01)


def check_payout_risks(payouts: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """payouts 每項需含 pool / kind / addr / distribution / issued_bills / round_id。

    distribution 為 None 代表 collection 收不到 payout::init
    （nft-collection.func:126 的 error 67），所有操作都會癱瘓。
    """
    out: list[tuple[str, str, str]] = []
    for item in payouts:
        pool = item.get("pool", "?")
        kind = item.get("kind", "payout")
        addr = str(item.get("addr", ""))
        short = f"{addr[:10]}…" if addr else "?"

        if item.get("distribution") is None:
            out.append((
                "crit", "payout_not_initialized",
                f"{pool} 的 {kind} collection {short} 讀不到 distribution —— "
                "可能未收到 payout::init（gas 不足），所有 mint 與分配都會失敗",
            ))
            continue

        started = item["distribution"].get("started")
        issued = _to_int(item.get("issued_bills"))
        if started and isinstance(issued, int) and issued > 0:
            out.append((
                "warn", "payout_distribution_stalled",
                f"{pool} 的 {kind} collection {short} 分配已開始但仍有 {issued} 張 bill 未燒毀 —— "
                "若數字停滯不動代表 burn 鏈斷裂，其後的持有者拿不到錢",
            ))

        if item.get("is_stale"):
            out.append((
                "warn", "payout_stale_round",
                f"{pool} 的 {kind} collection {short} 屬於較舊的 round —— 分配可能沒有完成",
            ))
    return out


# ── Librarian ────────────────────────────────────────────────────
# librarian 發布 public library；餘額歸零 → masterchain 帳戶被凍結 →
# library 失效 → 以 library ref 部署的 controller / pool 無法執行。
# 這是全系統唯一的單點失效，而且無法自救（librarian.func:115-127）。
# 位址不在任何 get method 裡，需要用 set lst_librarian_addr 手動指定。

LIBRARIAN_WARN_TON = 50.0
LIBRARIAN_CRIT_TON = 10.0


def check_librarian(balance: float | None, addr: str | None) -> list[tuple[str, str, str]]:
    if addr is None:
        return []
    if balance is None:
        return [(
            "warn", "librarian_unreachable",
            f"讀不到 librarian {addr[:10]}… 的餘額",
        )]
    if balance < LIBRARIAN_CRIT_TON:
        return [(
            "crit", "librarian_balance_critical",
            f"librarian 餘額僅 {balance:,.2f} TON —— 耗盡後 public library 失效，"
            "以 library 部署的 controller/pool 將無法執行且無法自救",
        )]
    if balance < LIBRARIAN_WARN_TON:
        return [(
            "warn", "librarian_balance_low",
            f"librarian 餘額 {balance:,.2f} TON 偏低（建議充值水位 250 TON）",
        )]
    return []


# ── 三類會讓 LST 卡死的條件 ──────────────────────────────────────

# controller.func:29 —— 送進 elector 的金額下限，與網路 config17 的
# min_stake 是兩回事，兩個都要過
MIN_STAKE_TO_SEND = 50_000.0
# controller.func:402 —— 質押後 controller 必須留下的金額
# （MAX_OVERDUE_FINE 40 + MIN_TONS_FOR_STORAGE 2）
OVERDUE_FINE_AND_STORAGE = 42.0

# validator wallet 為每一筆 controller 交易付費並簽章：
# 借款 1.01 / 參選 1.03 / 回收 1.04 / 還款 1.05 / 提款 1.06 / 更新 hash 1.07。
# 一輪一個 controller 約 6.3 TON（hash 最多更新 3 次），兩個約 12.6 TON。
VALIDATOR_WALLET_CRIT_TON = 5.0
VALIDATOR_WALLET_WARN_TON = 20.0

# TON 二進位檔太舊會在網路升級後無法跟上
TON_BUILD_WARN_DAYS = 120
TON_BUILD_CRIT_DAYS = 240


def check_validator_wallet(balance: float | None, addr: str | None) -> list[tuple[str, str, str]]:
    """validator wallet 沒錢 = 所有 controller 操作全部停擺。

    這是最容易被忽略的卡死原因：controller 本身有錢、池子也正常，
    但沒有任何交易送得出去，而且不會有錯誤訊息——因為根本沒送出。
    """
    if balance is None:
        return [("warn", "validator_wallet_unknown", "讀不到 validator wallet 餘額")]
    short = f"{addr[:10]}…" if addr else "?"
    if balance < VALIDATOR_WALLET_CRIT_TON:
        return [(
            "crit", "validator_wallet_empty",
            f"validator wallet {short} 只剩 {balance:,.2f} TON —— "
            "借款／參選／回收／提款的每一筆交易都由它付費，"
            "耗盡後所有 controller 操作全部停擺且不會有錯誤訊息",
        )]
    if balance < VALIDATOR_WALLET_WARN_TON:
        return [(
            "warn", "validator_wallet_low",
            f"validator wallet {short} 餘額 {balance:,.2f} TON 偏低 —— "
            "一輪兩個 controller 約需 12.6 TON",
        )]
    return []


def check_stake_feasibility(
    controllers: list[dict[str, Any]],
    *,
    max_loan: float | None = None,
    network_min_stake: float | None = None,
) -> list[tuple[str, str, str]]:
    """借款金額與質押門檻的相容性。

    這是「max_loan / min_loan 沒調好就卡死」的具體判準：借到錢之後
    仍然過不了 new_stake 的門檻，錢就只能原封還回去。
    """
    out: list[tuple[str, str, str]] = []
    for item in controllers:
        addr = str(item.get("addr", ""))
        short = f"{addr[:8]}…" if addr else "?"
        data = item.get("data") or {}
        balance = item.get("balance")
        if not isinstance(balance, (int, float)):
            continue

        # controller.func:471 —— allocation 為 0 表示無上限
        allocation = _to_int(data.get("allocation"))
        if allocation and max_loan and max_loan * NANO > allocation:
            out.append((
                "crit", "max_loan_above_allocation",
                f"max_loan {max_loan:,.0f} TON 超過 controller {short} 的 allocation "
                f"{allocation / NANO:,.0f} TON —— 借款會被拒絕（0xfa04）",
            ))

        # 借到 max_loan 之後可質押的金額
        if max_loan:
            projected_balance = balance + max_loan
            # mytoncore 的 LST 分支是 balance - 50
            stake = projected_balance - 50
            if stake < MIN_STAKE_TO_SEND:
                out.append((
                    "crit", "stake_below_contract_minimum",
                    f"借到 max_loan 後 controller {short} 只能質押 {stake:,.0f} TON，"
                    f"低於合約下限 {MIN_STAKE_TO_SEND:,.0f} TON —— "
                    "new_stake 會被拒絕（0xf902），借來的錢只能原封還回",
                ))
            elif network_min_stake and stake < network_min_stake:
                out.append((
                    "warn", "stake_below_network_minimum",
                    f"借到 max_loan 後 controller {short} 只能質押 {stake:,.0f} TON，"
                    f"低於網路最低質押 {network_min_stake:,.0f} TON —— 無法入選",
                ))
            # controller.func:402 —— 質押後必須留下 42 TON
            elif projected_balance - stake < OVERDUE_FINE_AND_STORAGE:
                out.append((
                    "crit", "insufficient_retained_balance",
                    f"controller {short} 質押後只留下 {projected_balance - stake:,.2f} TON，"
                    f"低於合約要求的 {OVERDUE_FINE_AND_STORAGE} TON —— new_stake 會被拒絕（0xf903）",
                ))
    return out


def check_ton_version(build_date: str | None, now: int = 0) -> list[tuple[str, str, str]]:
    """TON 二進位檔的建置日期。

    網路升級後舊版節點會跟不上；`mytonctrl upgrade` 沒做就會卡死。
    """
    if not build_date or not now:
        return []
    import datetime

    try:
        built = datetime.datetime.strptime(build_date[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return []
    days = (now - built.replace(tzinfo=datetime.timezone.utc).timestamp()) / 86400
    if days > TON_BUILD_CRIT_DAYS:
        return [(
            "crit", "ton_binary_stale",
            f"validator-engine 建置於 {days:.0f} 天前 —— "
            "網路升級後可能無法跟上，請執行 mytonctrl upgrade",
        )]
    if days > TON_BUILD_WARN_DAYS:
        return [(
            "warn", "ton_binary_aging",
            f"validator-engine 建置於 {days:.0f} 天前，建議近期執行 mytonctrl upgrade",
        )]
    return []
