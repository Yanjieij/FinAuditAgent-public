"""验证 Saga 补偿事务 + 幂等键。"""

from __future__ import annotations

from fin_audit_agent.graph.saga import Saga, Step, idempotency_key


def test_saga_all_ok():
    done = []
    s = Saga("gid-ok")
    s.add(Step("a", do=lambda p: done.append("a"), compensate=lambda p: None))
    s.add(Step("b", do=lambda p: done.append("b"), compensate=lambda p: None))
    r = s.run()
    assert r.ok
    assert done == ["a", "b"]


def test_saga_compensates_in_reverse():
    done, comp = [], []

    def do_a(p): done.append("a")
    def do_b(p): done.append("b")
    def do_c(p): raise RuntimeError("boom")

    s = Saga("gid-fail")
    s.add(Step("a", do_a, lambda p: comp.append("a")))
    s.add(Step("b", do_b, lambda p: comp.append("b")))
    s.add(Step("c", do_c, lambda p: comp.append("c")))
    r = s.run()
    assert not r.ok
    assert r.failed_step == "c"
    # 补偿必须逆序
    assert comp == ["b", "a"]


def test_saga_idempotent_resume():
    hits = []
    s = Saga("gid-resume")
    s.add(Step("a", do=lambda p: hits.append("a"), compensate=lambda p: None))
    s.add(Step("b", do=lambda p: hits.append("b"), compensate=lambda p: None))

    existing_log = [
        {"step": "a", "status": "done",
         "idempotency_key": idempotency_key("gid-resume", "a")},
    ]
    r = s.run(existing_log=existing_log)
    assert r.ok
    # 只应该新执行了 b，a 被跳过
    assert hits == ["b"]


def test_idempotency_key_stable():
    k1 = idempotency_key("g", "step")
    k2 = idempotency_key("g", "step")
    assert k1 == k2
    assert idempotency_key("g", "other") != k1
