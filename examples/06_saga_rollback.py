"""Example 06 —— Saga 补偿事务 + 幂等键。

演示：
    1. Saga 3 步（记账 / 发通知 / 更新工单）
    2. 故意在第 3 步失败 → 前 2 步按逆序补偿
    3. resume：再跑一次，成功的步骤不重放（幂等）
"""

from __future__ import annotations

from rich.console import Console

from fin_audit_agent.graph.saga import Saga, Step, idempotency_key

console = Console()

_LEDGER: list[dict] = []
_MESSAGES: list[dict] = []
_TICKETS: list[dict] = []


# --- 三个假工作 + 对应的 compensate ---
def do_ledger(p): _LEDGER.append({"amount": p["amount"]}); console.print(f"  + ledger: {_LEDGER[-1]}")
def comp_ledger(p):
    if _LEDGER: _LEDGER.pop()
    console.print("  - ledger rolled back")

def do_notify(p): _MESSAGES.append({"to": p["to"]}); console.print(f"  + notify: {_MESSAGES[-1]}")
def comp_notify(p):
    if _MESSAGES: _MESSAGES.pop()
    console.print("  - notify recalled")

def do_ticket_fail(p): raise RuntimeError("mock 工单系统挂了")
def do_ticket_ok(p):   _TICKETS.append({"id": p["id"]}); console.print(f"  + ticket: {_TICKETS[-1]}")
def comp_ticket(p):    pass


def make_saga_failing(graph_id="g1"):
    s = Saga(graph_id=graph_id)
    s.add(Step("record_ledger", do_ledger, comp_ledger, {"amount": 12000}))
    s.add(Step("notify_feishu", do_notify, comp_notify, {"to": "finance-team"}))
    s.add(Step("update_ticket", do_ticket_fail, comp_ticket, {"id": "T-99"}))
    return s


def make_saga_succeeding(graph_id="g1"):
    s = Saga(graph_id=graph_id)
    s.add(Step("record_ledger", do_ledger, comp_ledger, {"amount": 12000}))
    s.add(Step("notify_feishu", do_notify, comp_notify, {"to": "finance-team"}))
    s.add(Step("update_ticket", do_ticket_ok, comp_ticket, {"id": "T-99"}))
    return s


def main():
    console.rule("[bold]第一次跑：第 3 步故意失败 → 补偿[/bold]")
    r1 = make_saga_failing("g1").run()
    console.print(f"ok={r1.ok}, completed={r1.completed}, compensated={r1.compensated}, failed={r1.failed_step}")
    assert not r1.ok and r1.failed_step == "update_ticket"

    console.rule("[bold]第二次跑：如果传入 existing_log（幂等 resume）[/bold]")
    # 模拟第一次其实成功了前 2 步后才崩，且 saga_log 已持久化
    existing_log = [
        {"step": "record_ledger", "status": "done",
         "idempotency_key": idempotency_key("g1", "record_ledger")},
        {"step": "notify_feishu", "status": "done",
         "idempotency_key": idempotency_key("g1", "notify_feishu")},
    ]

    r2 = make_saga_succeeding("g1").run(existing_log=existing_log)
    console.print(f"ok={r2.ok}, completed={r2.completed}")
    # resume 时前 2 步不会真正执行（虽然这里计数器看不太出来，但 LEDGER/MESSAGES 的新增次数会减少）
    console.print(f"LEDGER 条数 = {len(_LEDGER)} (应含第一次的 1 次 + 第二次无新增)")


if __name__ == "__main__":
    main()
