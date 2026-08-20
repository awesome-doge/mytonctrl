"""`lst` 指令的呈現層。

與 mytoncore/lst.py 一樣刻意獨立成新檔案，讓與上游同步時的衝突面最小化。
排版風格沿用 modules/general.py print_status 的既有慣例：
color_print 做區塊標題、print_table 做表格、bcolors 做欄位著色。
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from mypylib.mypylib import bcolors, color_print, print_table

from mytoncore.lst import (
    CONTROLLER_STATES,
    NANO,
    check_controller_risks,
    check_pool_risks,
    conversion_rate,
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
        entry["projected_halted"] = ton.GetPoolProjectedHalted(addr)
        loan_amount = None
        if entry["is_local"]:
            # calculate_loan_amount 需要 pool 端的 update_round，只對本機池子試算
            loan_amount = local.try_function(ton.calculate_loan_amount, args=[0, 10**6, (1 << 24) - 1])
        entry["loan_amount"] = loan_amount
        entry["risks"] = check_pool_risks(
            data,
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
    for addr in using:
        item: dict[str, Any] = {"addr": addr}
        account = local.try_function(ton.GetAccount, args=[addr])
        item["balance"] = getattr(account, "balance", None)
        item["status"] = getattr(account, "status", None)
        item["data"] = local.try_function(ton.GetControllerData, args=[addr])
        controllers.append(item)

    return {
        "timestamp": now,
        "local_pool": local_pool,
        "elections_open": elections_open,
        "pools": pools,
        "controllers": controllers,
        "controller_risks": check_controller_risks(controllers),
    }


def render(report: dict[str, Any]) -> None:
    pools: list[dict[str, Any]] = report["pools"]

    color_print("{cyan}===[ LST 池子總覽 ]==={endc}")
    table: list[list[Any]] = [[
        "Pool", "Address", "TVL (TON)", "Supply", "Rate", "Interest", "Gov fee", "Deposits", "Halted",
    ]]
    for pool in pools:
        label = pool["name"] + (" ←本機" if pool["is_local"] else "")
        if "error" in pool:
            table.append([label, _short(pool["address"]), "讀取失敗", "", "", "", "", "", ""])
            continue
        data = pool["data"]
        rate = pool.get("rate")
        table.append([
            label,
            _short(pool["address"]),
            _ton(data.get("total_balance"), 0),
            _ton(data.get("supply"), 0),
            f"{rate:.6f}" if rate else "n/a",
            f"{share_to_percent(data.get('interest_rate')) or 0:.4f}%",
            f"{share_to_percent(data.get('governance_fee_share')) or 0:.4f}%",
            _bool(data.get("deposits_open"), "open", "closed"),
            _bool(not data.get("halted"), "no", "YES"),
        ])
    print_table(table)

    print()
    color_print("{cyan}===[ 借貸輪次 ]==={endc}")
    rounds: list[list[Any]] = [[
        "Pool", "Round", "Borrowers", "Borrowed", "Expected", "Returned", "Profit", "Deployed",
    ]]
    for pool in pools:
        if "error" in pool:
            continue
        data = pool["data"]
        total = data.get("total_balance") or 0
        for which, label in (("current_round", "本輪"), ("prev_round", "上輪")):
            rnd = data.get(which) or {}
            profit = rnd.get("profit")
            profit_text = _ton(profit) if isinstance(profit, int) else "n/a"
            borrowed = rnd.get("borrowed") or 0
            rounds.append([
                f"{pool['name']} {label}",
                rnd.get("round_id"),
                rnd.get("active_borrowers"),
                _ton(rnd.get("borrowed"), 0),
                _ton(rnd.get("expected"), 0),
                _ton(rnd.get("returned"), 0),
                profit_text,
                f"{borrowed / total * 100:.1f}%" if total else "n/a",
            ])
    print_table(rounds)

    controllers: list[dict[str, Any]] = report["controllers"]
    if controllers:
        print()
        color_print("{cyan}===[ 本機 Controller ]==={endc}")
        ctable: list[list[Any]] = [[
            "Address", "Status", "Balance", "State", "Approved", "Borrowed", "Interest", "Allocation",
        ]]
        for item in controllers:
            data = item.get("data") or {}
            state = data.get("state")
            state_text = CONTROLLER_STATES.get(state, str(state)) if state is not None else "n/a"
            ctable.append([
                _short(item["addr"], 12),
                item.get("status") or "n/a",
                f"{item['balance']:.2f}" if isinstance(item.get("balance"), (int, float)) else "n/a",
                state_text,
                _bool(data.get("approved")),
                _ton(data.get("borrowed_amount")),
                f"{share_to_percent(data.get('interest')) or 0:.4f}%",
                _ton(data.get("allocation"), 0),
            ])
        print_table(ctable)

    print()
    color_print("{cyan}===[ 風險檢查 ]==={endc}")
    findings: list[tuple[str, str, str, str]] = []
    for pool in pools:
        if "error" in pool:
            findings.append(("crit", pool["name"], "pool_unreachable", f"讀不到池子狀態：{pool['error']}"))
            continue
        for severity, key, message in pool["risks"]:
            findings.append((severity, pool["name"], key, message))
    for severity, key, message in report["controller_risks"]:
        findings.append((severity, "controller", key, message))

    if not findings:
        color_print("  {green}沒有偵測到問題{endc}")
        return

    order = {"crit": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda item: order.get(item[0], 3))
    for severity, scope, _key, message in findings:
        tag = severity.upper().ljust(4)
        colorize = SEVERITY_COLOR.get(severity, bcolors.blue_text)
        print(f"  [{colorize(tag)}] {scope}: {message}")

    counts = {level: sum(1 for f in findings if f[0] == level) for level in ("crit", "warn", "info")}
    print()
    summary = f"  嚴重 {counts['crit']} / 警告 {counts['warn']} / 提示 {counts['info']}"
    print(bcolors.red_text(summary) if counts["crit"] else bcolors.yellow_text(summary))


def lst_status(ton: "MyTonCore", local: "MyPyClass", args: list[str]) -> None:
    report = collect(ton, local)
    if "--json" in args:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        return
    render(report)
