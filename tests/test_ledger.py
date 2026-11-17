"""M8 月度预算账本：跨批次预占、全额结算、撤销、幂等回执、崩溃恢复、月度隔离。

金额断言一律整数分；时钟走 settings.eval_date（2026-11-15），月份默认 2026-11。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app import errors
from app.modules.m8_ledger import ledger, service

MONTH = "2026-11"
NOW = "2026-11-15T08:00:00+00:00"
DEPT = "数据平台"


def set_budget(store, dept=DEPT, cents=100_000, month=MONTH):
    return store.ledger_set_budget(dept=dept, month=month, budget_cents=cents,
                                   now=NOW)


def reserve(store, dept=DEPT, batch="B1", cents=1000, month=MONTH, rid=None):
    return store.ledger_reserve(dept=dept, month=month, batch_id=batch,
                                estimate_cents=cents, reservation_id=rid, now=NOW)


def settle(store, receipt, rid, cents):
    return store.ledger_settle(receipt_id=receipt, reservation_id=rid,
                               actual_cents=cents, now=NOW)


# ---------- 状态读数与预占守门 ----------

def test_status_reports_used_held_available_deficit(store):
    set_budget(store, cents=10_000)
    rid = reserve(store, cents=4000)["reservation"]["reservation_id"]
    settle(store, "RC-1", rid, 3500)
    reserve(store, batch="B2", cents=2000)
    status = store.ledger_status(DEPT, MONTH)
    assert status == {"dept": DEPT, "month": MONTH, "budget_cents": 10_000,
                      "used_cents": 3500, "held_cents": 2000,
                      "available_cents": 4500, "deficit_cents": 0}


def test_reserve_gates_by_estimate(store):
    set_budget(store, cents=5000)
    reserve(store, cents=5000)  # 恰好等于预算也放行（区间 (0, budget]）
    with pytest.raises(errors.Conflict) as excinfo:
        reserve(store, batch="B2", cents=1)
    assert excinfo.value.code == "Q_LEDGER_OVER_BUDGET"
    assert excinfo.value.detail["available_cents"] == 0


def test_reserve_without_budget_is_rejected(store):
    with pytest.raises(errors.NotFound) as excinfo:
        reserve(store, cents=100)
    assert excinfo.value.code == "Q_LEDGER_NO_BUDGET"


def test_cross_batch_reservations_share_one_monthly_budget(store):
    """多个账单批次各用一份部门月度预算：跨批次合计不能超。"""
    set_budget(store, cents=10_000)
    reserve(store, batch="CS-20261115-0001-0", cents=4000)
    reserve(store, batch="CS-20261115-0002-3", cents=4000)
    with pytest.raises(errors.Conflict):
        reserve(store, batch="CS-20261115-0003-8", cents=3000)
    status = store.ledger_status(DEPT, MONTH)
    assert status["held_cents"] == 8000
    assert status["available_cents"] == 2000


def test_trap29_concurrent_reserves_never_overcommit(store):
    """TRAP-29：并发获准不能重复占用额度，成功者合计恰好等于预算。"""
    set_budget(store, cents=1000)

    def attempt(i):
        try:
            reserve(store, batch=f"B{i}", cents=100)
            return True
        except errors.Conflict:
            return False

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(attempt, range(16)))
    assert results.count(True) == 10
    assert results.count(False) == 6
    status = store.ledger_status(DEPT, MONTH)
    assert status["held_cents"] == 1000
    assert status["available_cents"] == 0


def test_reserve_replay_with_same_id_is_idempotent(store):
    set_budget(store, cents=5000)
    first = reserve(store, cents=2000, rid="RS-CLIENT-1")
    replay = reserve(store, cents=2000, rid="RS-CLIENT-1")
    assert replay["duplicate"] is True
    assert replay["reservation"]["reservation_id"] == "RS-CLIENT-1"
    assert first["reservation"]["created_at"] == replay["reservation"]["created_at"]
    assert store.ledger_status(DEPT, MONTH)["held_cents"] == 2000  # 只占一次
    with pytest.raises(errors.Conflict) as excinfo:
        reserve(store, cents=3000, rid="RS-CLIENT-1")  # 同号不同参
    assert excinfo.value.code == "Q_LEDGER_RESERVE_CONFLICT"


# ---------- 结算：全额记账、欠额、幂等 ----------

def test_settle_releases_hold_and_books_actual(store):
    set_budget(store, cents=5000)
    rid = reserve(store, cents=2000)["reservation"]["reservation_id"]
    result = settle(store, "RC-1", rid, 1500)
    assert result["duplicate"] is False
    assert result["late"] is False
    status = result["status"]
    assert status["used_cents"] == 1500
    assert status["held_cents"] == 0
    assert status["available_cents"] == 3500


def test_settle_over_budget_books_full_actual_and_deficit_blocks_new(store):
    """回执超额也全额记账（不截断），超额成欠额并阻止新查询。"""
    set_budget(store, cents=5000)
    rid = reserve(store, cents=4000)["reservation"]["reservation_id"]
    result = settle(store, "RC-9", rid, 6000)
    status = result["status"]
    assert status["used_cents"] == 6000      # 全额 6000，不截断到 5000
    assert status["held_cents"] == 0
    assert status["deficit_cents"] == 1000   # 超额部分形成欠额
    assert status["available_cents"] == 0
    with pytest.raises(errors.Conflict) as excinfo:
        reserve(store, batch="B2", cents=1)  # 欠额下任何新查询都被拒
    assert excinfo.value.code == "Q_LEDGER_OVER_BUDGET"
    assert excinfo.value.detail["deficit_cents"] == 1000


def test_trap30_replayed_receipt_is_not_charged_twice(store):
    """TRAP-30：同一执行回执重试不重复扣费。"""
    set_budget(store, cents=5000)
    rid = reserve(store, cents=2000)["reservation"]["reservation_id"]
    first = settle(store, "RC-1", rid, 2000)
    replay = settle(store, "RC-1", rid, 2000)
    assert first["duplicate"] is False
    assert replay["duplicate"] is True
    assert replay["settlement"] == first["settlement"]
    assert store.ledger_status(DEPT, MONTH)["used_cents"] == 2000


def test_receipt_reused_with_different_payload_conflicts(store):
    set_budget(store, cents=5000)
    rid = reserve(store, cents=2000)["reservation"]["reservation_id"]
    rid2 = reserve(store, batch="B2", cents=1000)["reservation"]["reservation_id"]
    settle(store, "RC-1", rid, 2000)
    with pytest.raises(errors.Conflict) as excinfo:
        settle(store, "RC-1", rid, 9999)  # 同回执不同金额
    assert excinfo.value.code == "Q_LEDGER_RECEIPT_CONFLICT"
    with pytest.raises(errors.Conflict):
        settle(store, "RC-1", rid2, 2000)  # 同回执不同预占
    assert store.ledger_status(DEPT, MONTH)["used_cents"] == 2000


def test_same_reservation_cannot_settle_twice(store):
    set_budget(store, cents=5000)
    rid = reserve(store, cents=2000)["reservation"]["reservation_id"]
    settle(store, "RC-1", rid, 2000)
    with pytest.raises(errors.Conflict) as excinfo:
        settle(store, "RC-2", rid, 2000)  # 另一张回执重复结算同一预占
    assert excinfo.value.code == "Q_LEDGER_SETTLED"


def test_settle_unknown_reservation_is_rejected(store):
    set_budget(store, cents=5000)
    with pytest.raises(errors.NotFound) as excinfo:
        settle(store, "RC-X", "RS-NOPE", 100)
    assert excinfo.value.code == "Q_LEDGER_NO_RESERVATION"


# ---------- 撤销与晚到回执 ----------

def test_cancel_unexecuted_releases_hold_and_is_idempotent(store):
    set_budget(store, cents=5000)
    rid = reserve(store, cents=3000)["reservation"]["reservation_id"]
    result = store.ledger_cancel(reservation_id=rid, now=NOW)
    assert result["reservation"]["status"] == "CANCELLED"
    assert result["status"]["held_cents"] == 0
    assert result["status"]["available_cents"] == 5000
    again = store.ledger_cancel(reservation_id=rid, now=NOW)  # 重复撤销幂等
    assert again["reservation"]["status"] == "CANCELLED"
    assert store.ledger_status(DEPT, MONTH)["held_cents"] == 0


def test_settled_reservation_cannot_be_cancelled(store):
    set_budget(store, cents=5000)
    rid = reserve(store, cents=2000)["reservation"]["reservation_id"]
    settle(store, "RC-1", rid, 2000)
    with pytest.raises(errors.Conflict) as excinfo:
        store.ledger_cancel(reservation_id=rid, now=NOW)
    assert excinfo.value.code == "Q_LEDGER_SETTLED"


def test_trap32_late_receipt_after_cancel_is_still_charged(store):
    """TRAP-32：撤销后晚到的已执行回执仍全额计费，哪怕形成欠额。"""
    set_budget(store, cents=5000)
    rid = reserve(store, cents=4000)["reservation"]["reservation_id"]
    store.ledger_cancel(reservation_id=rid, now=NOW)
    assert store.ledger_status(DEPT, MONTH)["held_cents"] == 0
    result = settle(store, "RC-LATE", rid, 6000)
    assert result["late"] is True
    status = result["status"]
    assert status["used_cents"] == 6000      # 不丢账、不截断
    assert status["deficit_cents"] == 1000
    reservation = store.ledger_get_reservation(rid)
    assert reservation["status"] == "CANCELLED"  # 撤销事实不被回执改写


# ---------- 中断恢复：同成同败 ----------

def test_trap31_crash_mid_settle_rolls_back_and_replay_recovers(store,
                                                                monkeypatch):
    """TRAP-31：结算中途断电，释放预占与记实账同成同败；恢复后重试只记一次。"""
    set_budget(store, cents=5000)
    rid = reserve(store, cents=2000)["reservation"]["reservation_id"]
    original = store._ledger_insert_settlement

    def boom(conn, settlement):
        raise RuntimeError("模拟结算中途断电")

    monkeypatch.setattr(store, "_ledger_insert_settlement", boom)
    with pytest.raises(RuntimeError):
        settle(store, "RC-1", rid, 2000)
    mid = store.ledger_status(DEPT, MONTH)
    assert mid["used_cents"] == 0      # 实账没落
    assert mid["held_cents"] == 2000   # 预占也没被释放
    monkeypatch.setattr(store, "_ledger_insert_settlement", original)
    recovered = settle(store, "RC-1", rid, 2000)  # 中断后恢复结算
    assert recovered["duplicate"] is False
    replay = settle(store, "RC-1", rid, 2000)
    assert replay["duplicate"] is True
    final = store.ledger_status(DEPT, MONTH)
    assert final["used_cents"] == 2000
    assert final["held_cents"] == 0


# ---------- 不做跨月结转 ----------

def test_no_cross_month_carryover(store):
    set_budget(store, cents=5000, month="2026-11")
    rid = reserve(store, cents=3000, month="2026-11")["reservation"]["reservation_id"]
    settle(store, "RC-1", rid, 2500)
    rid2 = reserve(store, batch="B2", cents=1000,
                   month="2026-11")["reservation"]["reservation_id"]
    december = store.ledger_status(DEPT, "2026-12")
    assert december == {"dept": DEPT, "month": "2026-12", "budget_cents": None,
                        "used_cents": 0, "held_cents": 0, "available_cents": 0,
                        "deficit_cents": 0}
    # 11 月的已用/预占/欠额都不滚入 12 月；12 月预算要单独设
    with pytest.raises(errors.NotFound):
        reserve(store, cents=100, month="2026-12")
    set_budget(store, cents=8000, month="2026-12")
    ok = reserve(store, cents=7000, month="2026-12")
    assert ok["status"]["available_cents"] == 1000
    # 11 月留下的未执行预占，晚到回执记回 11 月，不动 12 月的账
    late = settle(store, "RC-2", rid2, 1000)
    assert late["status"]["month"] == "2026-11"
    assert late["status"]["used_cents"] == 3500
    assert store.ledger_status(DEPT, "2026-12")["used_cents"] == 0


# ---------- 纯规则与编排 ----------

def test_summarize_without_budget():
    status = ledger.summarize(dept=DEPT, month=MONTH, budget_cents=None,
                              used_cents=0, held_cents=0)
    assert status["budget_cents"] is None
    assert status["available_cents"] == 0
    assert status["deficit_cents"] == 0


def test_current_month_comes_from_eval_date(settings):
    assert service.current_month(settings) == "2026-11"


def test_check_month_rejects_bad_format():
    for bad in ("2026-1", "2026-13", "202611", ""):
        with pytest.raises(errors.BadRequest):
            ledger.check_month(bad)
    assert ledger.check_month("2026-12") == "2026-12"


# ---------- HTTP 接口 ----------

def test_api_budget_reserve_settle_status_flow(client):
    resp = client.put("/api/ledger/budget",
                      json={"dept": "风控", "budget_yuan": "1,000.00"})
    assert resp.status_code == 200
    assert resp.json()["budget_cents"] == 100_000

    resp = client.post("/api/ledger/reserve",
                       json={"dept": "风控", "batch_id": "B1",
                             "estimate_yuan": "600"})
    assert resp.status_code == 200
    rid = resp.json()["reservation"]["reservation_id"]
    assert rid.startswith("RS-")

    status = client.get("/api/ledger/status", params={"dept": "风控"}).json()
    assert status["month"] == "2026-11"
    assert status["held_cents"] == 60_000
    assert status["available_cents"] == 40_000

    resp = client.post("/api/ledger/settle",
                       json={"receipt_id": "RC-1", "reservation_id": rid,
                             "actual_cents": 120_000})
    assert resp.status_code == 200
    assert resp.json()["status"]["used_cents"] == 120_000  # 全额记账
    assert resp.json()["status"]["deficit_cents"] == 20_000

    replay = client.post("/api/ledger/settle",
                         json={"receipt_id": "RC-1", "reservation_id": rid,
                               "actual_cents": 120_000})
    assert replay.json()["duplicate"] is True
    assert replay.json()["status"]["used_cents"] == 120_000  # 没重复扣

    denied = client.post("/api/ledger/reserve",
                         json={"dept": "风控", "batch_id": "B2",
                               "estimate_cents": 1})
    assert denied.status_code == 409
    assert denied.json()["error"] == "Q_LEDGER_OVER_BUDGET"
    assert denied.json()["detail"]["deficit_cents"] == 20_000


def test_api_cancel_and_late_receipt(client):
    client.put("/api/ledger/budget", json={"dept": "营销", "budget_cents": 50_000})
    rid = client.post("/api/ledger/reserve",
                      json={"dept": "营销", "batch_id": "B1",
                            "estimate_cents": 40_000}).json()[
                                "reservation"]["reservation_id"]
    resp = client.post("/api/ledger/cancel", json={"reservation_id": rid})
    assert resp.status_code == 200
    assert resp.json()["status"]["held_cents"] == 0
    resp = client.post("/api/ledger/settle",
                       json={"receipt_id": "RC-LATE", "reservation_id": rid,
                             "actual_yuan": "600"})
    assert resp.status_code == 200
    assert resp.json()["late"] is True
    assert resp.json()["status"]["deficit_cents"] == 10_000


def test_api_lists_for_reconciliation(client):
    client.put("/api/ledger/budget", json={"dept": "平台组", "budget_cents": 9000})
    rid = client.post("/api/ledger/reserve",
                      json={"dept": "平台组", "batch_id": "B1",
                            "estimate_cents": 3000}).json()[
                                "reservation"]["reservation_id"]
    client.post("/api/ledger/settle",
                json={"receipt_id": "RC-1", "reservation_id": rid,
                      "actual_cents": 2500})
    client.post("/api/ledger/reserve",
                json={"dept": "平台组", "batch_id": "B2", "estimate_cents": 1000})
    held = client.get("/api/ledger/reservations",
                      params={"dept": "平台组", "status": "HELD"}).json()
    assert [row["estimate_cents"] for row in held["reservations"]] == [1000]
    settled = client.get("/api/ledger/settlements",
                         params={"dept": "平台组"}).json()
    assert [row["actual_cents"] for row in settled["settlements"]] == [2500]


def test_api_validation_errors(client):
    assert client.post("/api/ledger/reserve", json={}).status_code == 400
    resp = client.put("/api/ledger/budget",
                      json={"dept": "风控", "budget_cents": 1000,
                            "month": "2026-13"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "Q_LEDGER_BAD_MONTH"
    resp = client.put("/api/ledger/budget",
                      json={"dept": "风控", "budget_cents": 0})
    assert resp.status_code == 400
    resp = client.get("/api/ledger/status")
    assert resp.status_code == 400
    resp = client.post("/api/ledger/settle",
                       json={"receipt_id": "RC-X", "reservation_id": "RS-X",
                             "actual_cents": 100})
    assert resp.status_code == 404
    assert resp.json()["error"] == "Q_LEDGER_NO_RESERVATION"
    resp = client.get("/api/ledger/reservations",
                      params={"dept": "风控", "status": "BOGUS"})
    assert resp.status_code == 400
