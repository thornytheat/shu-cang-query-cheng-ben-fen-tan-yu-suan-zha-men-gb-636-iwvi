"""M8 编排：解析请求（金额 / 月份 / 标识），调用 store 的原子账本操作。

金额入口支持整数分（*_cents）或元（*_yuan，走计费模块的归一化），
内部一律整数分；月份缺省取评估日所在月，账本不做跨月结转。
"""
from __future__ import annotations

import datetime as dt

from app import errors
from app.modules.m2_billing import coerce

from . import ledger

MAX_TEXT = 200


def current_month(settings) -> str:
    """本月 = 评估日所在月；每月独立成账，不做跨月结转。"""
    return settings.eval_date.strftime("%Y-%m")


def resolve_month(settings, raw) -> str:
    if raw is None or str(raw).strip() == "":
        return current_month(settings)
    return ledger.check_month(str(raw))


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _text(body: dict, key: str, *, required: bool = True) -> str | None:
    value = coerce.clean_text(body.get(key))
    if not value:
        if required:
            raise errors.BadRequest(f"缺少字段：{key}", code="E_BAD_REQUEST",
                                    detail={"field": key})
        return None
    if len(value) > MAX_TEXT:
        raise errors.BadRequest(f"{key} 过长（>{MAX_TEXT} 字符）",
                                code="E_BAD_REQUEST", detail={"field": key})
    return value


def _cents(body: dict, *, cents_key: str, yuan_key: str, field: str,
           minimum: int) -> int:
    raw_cents, raw_yuan = body.get(cents_key), body.get(yuan_key)
    if raw_cents is None and raw_yuan is None:
        raise errors.BadRequest(f"缺少金额字段：{cents_key} 或 {yuan_key}",
                                code="E_BAD_REQUEST", detail={"field": cents_key})
    if raw_cents is not None:
        if isinstance(raw_cents, bool):
            raise errors.BadRequest(f"{cents_key} 必须是整数分",
                                    code="E_BAD_REQUEST",
                                    detail={"field": cents_key})
        try:
            cents = int(str(raw_cents).strip())
        except (TypeError, ValueError):
            raise errors.BadRequest(f"{cents_key} 必须是整数分",
                                    code="E_BAD_REQUEST",
                                    detail={"field": cents_key, "raw": raw_cents})
    else:
        try:
            parsed = coerce.parse_decimal(raw_yuan, field)
        except coerce.CoerceError as exc:
            raise errors.BadRequest(exc.message, code=exc.code,
                                    detail={"field": field, "raw": exc.raw})
        cents = coerce.to_cents(parsed)
    if cents < minimum:
        raise errors.BadRequest(f"{field} 不能小于 {minimum} 分",
                                code="E_BAD_REQUEST",
                                detail={"field": field, "cents": cents})
    if cents > ledger.MAX_CENTS:
        raise errors.BadRequest(f"{field} 超出可表示范围",
                                code="E_BAD_REQUEST",
                                detail={"field": field})
    return cents


def set_budget(store, settings, body: dict) -> dict:
    dept = _text(body, "dept")
    month = resolve_month(settings, body.get("month"))
    budget = _cents(body, cents_key="budget_cents", yuan_key="budget_yuan",
                    field="budget", minimum=1)
    status = store.ledger_set_budget(dept=dept, month=month,
                                     budget_cents=budget, now=_now())
    return {"dept": dept, "month": month, "budget_cents": budget,
            "status": status}


def reserve(store, settings, body: dict) -> dict:
    dept = _text(body, "dept")
    batch_id = _text(body, "batch_id")
    month = resolve_month(settings, body.get("month"))
    estimate = _cents(body, cents_key="estimate_cents",
                      yuan_key="estimate_yuan", field="estimate", minimum=1)
    reservation_id = _text(body, "reservation_id", required=False)
    return store.ledger_reserve(dept=dept, month=month, batch_id=batch_id,
                                estimate_cents=estimate,
                                reservation_id=reservation_id, now=_now())


def settle(store, settings, body: dict) -> dict:
    receipt_id = _text(body, "receipt_id")
    reservation_id = _text(body, "reservation_id")
    actual = _cents(body, cents_key="actual_cents", yuan_key="actual_yuan",
                    field="actual", minimum=0)
    return store.ledger_settle(receipt_id=receipt_id,
                               reservation_id=reservation_id,
                               actual_cents=actual, now=_now())


def cancel(store, settings, body: dict) -> dict:
    reservation_id = _text(body, "reservation_id")
    return store.ledger_cancel(reservation_id=reservation_id, now=_now())


def status(store, settings, dept_raw, month_raw) -> dict:
    dept = coerce.clean_text(dept_raw)
    if not dept:
        raise errors.BadRequest("缺少查询参数：dept", code="E_BAD_REQUEST",
                                detail={"field": "dept"})
    return store.ledger_status(dept, resolve_month(settings, month_raw))


def reservations(store, settings, dept_raw, month_raw, status_raw) -> dict:
    state = coerce.clean_text(status_raw).upper() or None
    if state is not None and state not in ledger.RESERVATION_STATUSES:
        raise errors.BadRequest(f"未知预占状态：{state}", code="E_BAD_REQUEST",
                                detail={"status": state})
    month = ledger.check_month(month_raw) if coerce.clean_text(month_raw) else None
    rows = store.ledger_list_reservations(
        dept=coerce.clean_text(dept_raw) or None, month=month, status=state)
    return {"reservations": rows}


def settlements(store, settings, dept_raw, month_raw) -> dict:
    month = ledger.check_month(month_raw) if coerce.clean_text(month_raw) else None
    rows = store.ledger_list_settlements(
        dept=coerce.clean_text(dept_raw) or None, month=month)
    return {"settlements": rows}
