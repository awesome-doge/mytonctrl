"""`lst` 指令的呈現層。

與 mytoncore/lst.py 一樣刻意獨立成新檔案，讓與上游同步時的衝突面最小化。
排版風格沿用 modules/general.py print_status 的既有慣例：
color_print 做區塊標題、print_table 做表格、bcolors 做欄位著色。
"""

from __future__ import annotations

import json
import os
import time
import re
import unicodedata
from typing import TYPE_CHECKING, Any

from mypylib.mypylib import bcolors

from mytoncore.lst import (
    CONTROLLER_STATES,
    MIN_STAKE_TO_SEND,
    NODE_STATE_KEYS,
    LOAN_SETTING_DEFAULTS,
    NANO,
    check_controller_loan_readiness,
    check_controller_risks,
    check_loan_settings,
    check_librarian,
    check_node_state,
    check_stake_feasibility,
    check_ton_version,
    check_validator_wallet,
    check_payout_risks,
    check_pool_risks,
    controller_stake_readiness,
    conversion_rate,
    percent_to_share,
    share_to_percent,
)

if TYPE_CHECKING:
    from mytoncore.mytoncore import MyTonCore
    from mypylib.mypylib import MyPyClass

# 本營運方部署的兩個池子。GetLiquidPoolAddr() 取得的本機池子若不在其中會自動補上。
KNOWN_POOLS = {
    "EQA9HwEZD_tONfVz6lJS0PVKR5viEiEGyj9AuQewGQVnXPg0": "KTON",
    "EQDsW2P6nuP1zopKoNiCYj2xhqDan0cBuULQ8MH4o7dBt_7a": "pKTON",
}

SEVERITY_COLOR = {
    "crit": bcolors.red_text,
    "warn": bcolors.yellow_text,
    "info": bcolors.blue_text,
}


# ── 寬度正確的排版 ────────────────────────────────────────────────
# mypylib 的 print_table 用 len() 算欄寬，但 CJK 字元實際佔兩欄，
# 只要表格裡有中文就一定歪掉。這裡自己算顯示寬度。

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _dw(text: Any) -> int:
    """字串的顯示寬度（CJK 全形字算 2 欄，忽略 ANSI 色碼）。"""
    plain = _ANSI.sub("", str(text))
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in plain)


def _pad(text: Any, width: int) -> str:
    return str(text) + " " * max(0, width - _dw(text))


def _table(rows: list[list[Any]], indent: str = "  ") -> None:
    """寬度正確的表格。第一列是表頭。"""
    if not rows:
        return
    widths = [max(_dw(row[i]) for row in rows) for i in range(len(rows[0]))]
    for index, row in enumerate(rows):
        cells = [_pad(cell, widths[i]) for i, cell in enumerate(row)]
        line = indent + "  ".join(cells).rstrip()
        print(bcolors.bold_text(bcolors.blue_text(line)) if index == 0 else line)


def _kv(pairs: list[tuple[str, Any]], indent: str = "  ", columns: int = 2) -> None:
    """key-value 區塊，每列放 columns 組，欄位對齊。"""
    if not pairs:
        return
    key_width = max(_dw(k) for k, _ in pairs)
    val_width = max(_dw(v) for _, v in pairs)
    for start in range(0, len(pairs), columns):
        chunk = pairs[start:start + columns]
        parts = [f"{_pad(k, key_width)}  {_pad(v, val_width)}" for k, v in chunk]
        print(indent + "    ".join(parts).rstrip())


def _rule(title: str) -> None:
    print()
    print(bcolors.bold_text(bcolors.blue_text(f"▍{title}")))


def _ton(value: Any, digits: int = 2) -> str:
    if not isinstance(value, int):
        return "n/a"
    return f"{value / NANO:,.{digits}f}"


def _bool(value: Any, true_text: str = "yes", false_text: str = "no") -> str:
    """表格內一律回純文字 —— print_table 以字串長度算欄寬，
    含 ANSI 色碼的值會把整欄的對齊弄壞。異常狀態由風險區塊負責標紅。"""
    if value is None:
        return "n/a"
    return true_text if value else false_text


def _short(addr: Any, keep: int = 8) -> str:
    if not isinstance(addr, str) or len(addr) <= keep * 2:
        return str(addr)
    return f"{addr[:keep]}…{addr[-4:]}"


def collect(ton: "MyTonCore", local: "MyPyClass") -> dict[str, Any]:
    """把兩個池子與本機 controller 的狀態收集成一個 dict（--json 直接輸出這個）。"""
    try:
        local_pool = ton.GetLiquidPoolAddr()
    except Exception:
        local_pool = None

    addresses = dict(KNOWN_POOLS)
    if local_pool and local_pool not in addresses:
        addresses[local_pool] = "本機"

    # 節點端的借貸設定 —— 池子的 get method 看不到這些
    settings = {
        name: ton.local.db.get(name, default)
        for name, default in LOAN_SETTING_DEFAULTS.items()
    }

    db = ton.local.db
    node_state: dict[str, Any] = {
        "liquid_staking_enabled": bool(local.try_function(ton.using_liquid_staking)),
        "only_node": db.get("onlyNode"),
        "liquid_pool_addr": local_pool,
        "pending_withdraws": db.get("controllerPendingWithdraws") or {},
        "participate_before_end": db.get("participateBeforeEnd"),
        "stake": db.get("stake"),
    }
    for key in NODE_STATE_KEYS:
        node_state[key] = db.get(key) or []
    # background_runner.py:219-224 —— db 讀取失敗時會從 <db_path>.backup 還原。
    # 注意要用 ton.local（mytoncore 的 db）而不是 local（mytonctrl 自己的 db）
    node_state["backup_age_sec"] = None
    try:
        node_state["backup_age_sec"] = time.time() - os.path.getmtime(
            ton.local.db_path + ".backup"
        )
    except (OSError, AttributeError):
        pass

    now = int(time.time())
    elections_open = False
    try:
        elections_open = bool(ton.GetActiveElectionId(ton.GetFullElectorAddr()))
    except Exception as ex:
        local.add_log(f"lst: 讀取選舉狀態失敗 {ex}", "debug")

    pools: list[dict[str, Any]] = []
    for addr, name in addresses.items():
        entry: dict[str, Any] = {"name": name, "address": addr, "is_local": addr == local_pool}
        try:
            data = ton.GetPoolFullData(addr)
        except Exception as ex:
            entry["error"] = str(ex)
            pools.append(entry)
            continue
        entry["data"] = data
        entry["rate"] = conversion_rate(data)
        # 合約用「帳戶餘額」而非 total_balance 判斷 halt 與流動性守門
        pool_account = local.try_function(ton.GetAccount, args=[addr])
        entry["pool_balance"] = getattr(pool_account, "balance", None)
        entry["projected_halted"] = ton.GetPoolProjectedHalted(addr)
        loan_amount = None
        if entry["is_local"]:
            # 用這台節點「實際會送出的參數」試算，才知道現在借不借得到。
            # 用泛用參數試算只能知道池子有沒有錢，測不出設定不匹配。
            loan_amount = local.try_function(
                ton.calculate_loan_amount,
                args=[settings["min_loan"], settings["max_loan"],
                      percent_to_share(float(settings["max_interest_percent"]))],
            )
        entry["loan_amount"] = loan_amount
        entry["risks"] = check_pool_risks(
            data,
            pool_balance=entry["pool_balance"],
            projected_halted=entry["projected_halted"],
            loan_amount=loan_amount,
            elections_open=elections_open,
            now=now,
        )
        pools.append(entry)

    controllers: list[dict[str, Any]] = []
    try:
        using = ton.local.db.get("using_controllers") or []
    except Exception:
        using = []
    config15 = local.try_function(ton.get_config_15)
    validators_elected_for = getattr(config15, "validators_elected_for", None)
    stop_list = node_state.get("stop_controllers_list") or []

    elector_addr = local.try_function(ton.GetFullElectorAddr)
    for addr in using:
        item: dict[str, Any] = {"addr": addr, "now": now}
        if elector_addr:
            item["elector_returned_stake"] = local.try_function(
                ton.get_returned_stake, args=[elector_addr, addr]
            )
        account = local.try_function(ton.GetAccount, args=[addr])
        item["balance"] = getattr(account, "balance", None)
        item["status"] = getattr(account, "status", None)
        item["data"] = local.try_function(ton.GetControllerData, args=[addr])
        # controller.func:667 —— 借這筆錢需要 controller 自己有多少資金
        required = local.try_function(
            ton.GetControllerRequiredBalanceForLoan,
            args=[addr, settings["max_loan"],
                  percent_to_share(float(settings["max_interest_percent"]))],
        )
        if isinstance(required, tuple) and len(required) == 2:
            item["required_for_loan"] = required[0] / NANO
            item["validator_amount"] = required[1] / NANO
        ready, reason = controller_stake_readiness(
            addr, item.get("data"), stop_list=stop_list,
            validators_elected_for=validators_elected_for, now=now,
        )
        item["ready_to_stake"] = ready
        item["ready_reason"] = reason
        item["pending_withdraw"] = node_state["pending_withdraws"].get(addr)
        controllers.append(item)

    # payout collection —— 位址只能從池子的 deposit/withdrawal_payout 動態取得
    payouts: list[dict[str, Any]] = []
    for pool in pools:
        data = pool.get("data")
        if not data:
            continue
        current_round_id = (data.get("current_round") or {}).get("round_id")
        for field, kind in (("deposit_payout", "存款"), ("withdrawal_payout", "提款")):
            addr = data.get(field)
            if not addr:
                continue
            info = local.try_function(ton.GetPayoutCollectionData, args=[addr]) or {}
            info.update({
                "pool": pool["name"], "kind": kind, "addr": addr,
                "is_stale": False,
            })
            payouts.append(info)
            _ = current_round_id

    # validator wallet —— 每一筆 controller 交易都由它付費並簽章
    wallet_addr = None
    wallet_balance = None
    wallet = local.try_function(ton.GetValidatorWallet)
    if wallet is not None:
        wallet_addr = getattr(wallet, "addrB64", None)
        if wallet_addr:
            account = local.try_function(ton.GetAccount, args=[wallet_addr])
            wallet_balance = getattr(account, "balance", None)

    # TON 二進位檔的建置日期 —— 沒 upgrade 會在網路升級後跟不上
    ton_build_date = None
    try:
        import subprocess

        out = subprocess.run(
            [str(ton.get_paths().ton_bin) + "/validator-engine/validator-engine", "--version"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        marker = "Date: "
        if marker in out:
            ton_build_date = out.split(marker, 1)[1].split("]")[0].strip()
    except Exception as ex:
        local.add_log(f"lst: 讀不到 validator-engine 版本 {ex}", "debug")

    # librarian 位址不在任何 get method 裡，需手動指定
    librarian_addr = db.get("lst_librarian_addr")
    librarian_balance = None
    if librarian_addr:
        account = local.try_function(ton.GetAccount, args=[librarian_addr])
        librarian_balance = getattr(account, "balance", None)

    min_stake = None
    config17 = local.try_function(ton.get_config_17)
    if config17 is not None:
        min_stake = getattr(config17, "min_stake", None)

    local_entry = next((p for p in pools if p["is_local"] and "data" in p), None)
    loan_risks: list[tuple[str, str, str]] = []
    if local_entry is not None:
        loan_risks = check_loan_settings(
            settings,
            local_entry["data"],
            loan_amount=local_entry.get("loan_amount"),
            min_stake=min_stake,
            elections_open=elections_open,
        )

    return {
        "timestamp": now,
        "local_pool": local_pool,
        "elections_open": elections_open,
        "settings": settings,
        "node_state": node_state,
        "node_risks": check_node_state(node_state)
        + check_librarian(librarian_balance, librarian_addr)
        + check_validator_wallet(wallet_balance, wallet_addr)
        + check_ton_version(ton_build_date, now),
        "payouts": payouts,
        "payout_risks": check_payout_risks(payouts),
        "librarian": {"addr": librarian_addr, "balance": librarian_balance},
        "min_stake": min_stake,
        "pools": pools,
        "controllers": controllers,
        "controller_risks": check_controller_risks(controllers)
        + check_controller_loan_readiness(controllers)
        + check_stake_feasibility(
            controllers,
            max_loan=float(settings["max_loan"]) if settings.get("max_loan") else None,
            network_min_stake=min_stake,
        ),
        "validator_wallet": {"addr": wallet_addr, "balance": wallet_balance},
        "ton_build_date": ton_build_date,
        "loan_risks": loan_risks,
    }


def render(report: dict[str, Any]) -> None:
    pools: list[dict[str, Any]] = report["pools"]
    settings: dict[str, Any] = report.get("settings") or {}
    node_state: dict[str, Any] = report.get("node_state") or {}
    stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(report["timestamp"]))
    print(bcolors.bold_text(f"LST 狀態 — {stamp}"))

    # ── 每個池子一個區塊。兩個池子欄位又多，橫排表格會擠成一團 ──
    for pool in pools:
        mark = "  ← 本機" if pool["is_local"] else ""
        _rule(f"{pool['name']}  {pool['address']}{mark}")
        if "error" in pool:
            print(f"  {bcolors.red_text('讀取失敗')}：{pool['error']}")
            continue
        data = pool["data"]
        rate = pool.get("rate")
        balance = pool.get("pool_balance")
        _kv([
            ("TVL", f"{_ton(data.get('total_balance'), 0)} TON"),
            ("帳戶餘額", f"{balance:,.2f} TON" if isinstance(balance, (int, float)) else "n/a"),
            ("Supply", _ton(data.get("supply"), 0)),
            ("待提款", f"{_ton(data.get('requested_for_withdrawal'), 0)} KTON"),
            ("匯率", f"{rate:.6f}" if rate else "n/a"),
            ("利率／輪", f"{share_to_percent(data.get('interest_rate')) or 0:.4f}%"),
            ("治理費", f"{share_to_percent(data.get('governance_fee_share')) or 0:.4f}%"),
            ("instant 提款費", f"{share_to_percent(data.get('instant_withdrawal_fee')) or 0:.6f}%"),
            ("存款", _bool(data.get("deposits_open"), "開放", "關閉")),
            ("樂觀存提", _bool(data.get("optimistic_deposit_withdrawals"), "開啟", "關閉")),
            ("halted", _bool(not data.get("halted"), "否", "是")),
            ("借款區間", f"{_ton(data.get('min_loan_per_validator'), 0)} ~ "
                        f"{_ton(data.get('max_loan_per_validator'), 0)} TON"),
        ])
        rounds: list[list[Any]] = [["", "Round", "借款人", "借出", "預期", "已還", "損益", "部署率"]]
        total = data.get("total_balance") or 0
        for which, label in (("current_round", "本輪"), ("prev_round", "上輪")):
            rnd = data.get(which) or {}
            borrowed = rnd.get("borrowed") or 0
            rounds.append([
                label, rnd.get("round_id"), rnd.get("active_borrowers"),
                _ton(rnd.get("borrowed"), 0), _ton(rnd.get("expected"), 0),
                _ton(rnd.get("returned"), 0), _ton(rnd.get("profit")),
                f"{borrowed / total * 100:.1f}%" if total else "n/a",
            ])
        print()
        _table(rounds)

    # ── 本機 controller ──
    controllers: list[dict[str, Any]] = report["controllers"]
    if controllers:
        _rule("本機 Controller")
        ctable: list[list[Any]] = [[
            "Address", "餘額", "狀態", "認可", "借款", "可參選", "原因",
        ]]
        for item in controllers:
            data = item.get("data") or {}
            state = data.get("state")
            ctable.append([
                _short(item["addr"], 10),
                f"{item['balance']:,.2f}" if isinstance(item.get("balance"), (int, float)) else "n/a",
                CONTROLLER_STATES.get(state, str(state)) if state is not None else "n/a",
                _bool(data.get("approved")),
                _ton(data.get("borrowed_amount"), 0),
                "yes" if item.get("ready_to_stake") else "no",
                item.get("ready_reason", ""),
            ])
        _table(ctable)

        loans = [c for c in controllers if c.get("required_for_loan") is not None]
        if loans:
            print()
            ltable: list[list[Any]] = [["Address", "自有資金", "借款所需", "差額", "elector 待領"]]
            for item in loans:
                available = item["validator_amount"]
                required = item["required_for_loan"]
                pending = item.get("elector_returned_stake")
                ltable.append([
                    _short(item["addr"], 10),
                    f"{available:,.2f}", f"{required:,.2f}",
                    f"{available - required:+,.2f}",
                    f"{pending:,.2f}" if isinstance(pending, (int, float)) else "－",
                ])
            _table(ltable)

    # ── 借貸設定（節點端，池子看不到）──
    local_entry = next((p for p in pools if p["is_local"] and "data" in p), None)
    if settings and local_entry is not None:
        pool_data = local_entry["data"]
        _rule(f"借貸設定（節點端）vs {local_entry['name']} 池子限制")
        pool_rate = share_to_percent(pool_data.get("interest_rate")) or 0.0
        _table([
            ["項目", "節點設定", "池子限制"],
            ["最低借款", f"{settings['min_loan']:,} TON",
             f"{_ton(pool_data.get('min_loan_per_validator'), 0)} TON"],
            ["最高借款", f"{settings['max_loan']:,} TON",
             f"{_ton(pool_data.get('max_loan_per_validator'), 0)} TON"],
            ["可付利率上限", f"{settings['max_interest_percent']}%", f"{pool_rate:.4f}%（要價）"],
        ])
        loan_amount = local_entry.get("loan_amount")
        if loan_amount is None:
            loan_text = "試算失敗"
        elif loan_amount == -1:
            loan_text = bcolors.yellow_text("池子拒絕放貸（-1）")
        else:
            loan_text = f"{loan_amount / NANO:,.0f} TON"
        min_stake = report.get("min_stake")
        print()
        _kv([
            ("本輪可貸額度", loan_text),
            ("網路最低質押", f"{min_stake:,.0f} TON" if min_stake else "n/a"),
        ], columns=1)
        print("  ※ calculate_loan_amount 的時間判斷方向與 recv_internal 相反"
              "（pool.func:864 vs :396），credit_start 非 0 時不可盡信")

    # ── 節點端狀態 ──
    if node_state:
        _rule("節點端狀態（mytoncore.db，鏈上查不到）")
        backup_age = node_state.get("backup_age_sec")
        pending = node_state.get("pending_withdraws") or {}
        _kv([
            ("liquid-staking", _bool(node_state.get("liquid_staking_enabled"), "啟用", "停用")),
            ("onlyNode", _bool(not node_state.get("only_node"), "否", "是（LST 停擺）")),
            ("using_controllers", f"{len(node_state.get('using_controllers') or [])} 個"),
            ("old_controllers", f"{len(node_state.get('old_controllers') or [])} 個"),
            ("user_controllers", f"{len(node_state.get('user_controllers') or [])} 個"),
            ("stop_controllers", f"{len(node_state.get('stop_controllers_list') or [])} 個"),
            ("排隊中提款", f"{len(pending)} 筆" if pending else "無"),
            ("db backup 年齡", f"{backup_age / 3600:.1f} 小時"
             if isinstance(backup_age, (int, float)) else "n/a"),
        ])
        wallet = report.get("validator_wallet") or {}
        wallet_balance = wallet.get("balance")
        print()
        _kv([
            ("validator wallet", _short(wallet.get("addr"), 12)),
            ("錢包餘額", f"{wallet_balance:,.2f} TON"
             if isinstance(wallet_balance, (int, float)) else "n/a"),
            ("TON 建置日期", report.get("ton_build_date") or "n/a"),
            ("合約質押下限", f"{MIN_STAKE_TO_SEND:,.0f} TON"),
        ])

    payouts: list[dict[str, Any]] = report.get("payouts") or []
    librarian: dict[str, Any] = report.get("librarian") or {}
    # 一律顯示，讓「目前沒有進行中的分配」也是可見的結論
    _rule("Payout 分配與 Librarian")
    if payouts:
        ptable: list[list[Any]] = [["Pool", "類型", "Address", "分配", "未燒毀 bill", "額度"]]
        for item in payouts:
            dist = item.get("distribution") or {}
            ptable.append([
                item.get("pool", "?"), item.get("kind", "?"),
                _short(item.get("addr"), 10),
                "已開始" if dist.get("started") else ("未開始" if dist else "讀不到"),
                item.get("issued_bills") if item.get("issued_bills") is not None else "n/a",
                _ton(dist.get("volume"), 2) if dist.get("volume") is not None else "n/a",
            ])
        _table(ptable)
    else:
        print("  本輪沒有進行中的 payout collection")
    if librarian.get("addr"):
        balance = librarian.get("balance")
        print()
        _kv([
            ("librarian", _short(librarian["addr"], 12)),
            ("餘額", f"{balance:,.2f} TON" if isinstance(balance, (int, float)) else "n/a"),
        ], columns=1)
    else:
        print("  librarian 未設定（set lst_librarian_addr <位址> 後可監控其餘額）")

    # ── 風險 ──
    _rule("風險檢查")
    findings: list[tuple[str, str, str, str]] = []
    for pool in pools:
        if "error" in pool:
            findings.append(("crit", pool["name"], "pool_unreachable", f"讀不到池子狀態：{pool['error']}"))
            continue
        for severity, key, message in pool["risks"]:
            findings.append((severity, pool["name"], key, message))
    for severity, key, message in report.get("node_risks", []):
        findings.append((severity, "節點", key, message))
    for severity, key, message in report.get("payout_risks", []):
        findings.append((severity, "payout", key, message))
    for severity, key, message in report.get("loan_risks", []):
        findings.append((severity, "借貸設定", key, message))
    for severity, key, message in report["controller_risks"]:
        findings.append((severity, "controller", key, message))

    if not findings:
        print(f"  {bcolors.green_text('沒有偵測到問題')}")
        return

    order = {"crit": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda item: order.get(item[0], 3))
    scope_width = max(_dw(f[1]) for f in findings)
    for severity, scope, _key, message in findings:
        colorize = SEVERITY_COLOR.get(severity, bcolors.blue_text)
        tag = colorize(severity.upper().ljust(4))
        print(f"  [{tag}] {_pad(scope, scope_width)}  {message}")

    counts = {level: sum(1 for f in findings if f[0] == level) for level in ("crit", "warn", "info")}
    print()
    summary = f"  嚴重 {counts['crit']}　警告 {counts['warn']}　提示 {counts['info']}"
    print(bcolors.red_text(summary) if counts["crit"] else bcolors.yellow_text(summary))


def lst_status(ton: "MyTonCore", local: "MyPyClass", args: list[str]) -> None:
    report = collect(ton, local)
    if "--json" in args:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        return
    render(report)
