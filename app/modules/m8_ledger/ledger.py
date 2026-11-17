"""M8 月度预算账本：预占 / 结算 / 撤销的纯规则，不落库。

账本单位是 (部门, 月份)，不做跨月结转，每月独立成账：
- 预占按预估费用守预算：已用 + 预占中 + 本次预估 ≤ 预算 才放行；
- 结算按实际费用全额记账，允许超过预算，超额部分即欠额，不截断；
- 欠额 > 0 时可用额度恒为 0，任何正预估的新预占都会被挡住；
- 撤销只释放未执行的预占；已撤销预占的晚到回执仍全额记账。
"""
from __future__ import annotations

import re
import uuid

from app import errors

HELD = "HELD"            # 预占中（未执行）
SETTLED = "SETTLED"      # 已结算（已执行）
CANCELLED = "CANCELLED"  # 已撤销（未执行）
RESERVATION_STATUSES = (HELD, SETTLED, CANCELLED)

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MAX_CENTS = 2**63 - 1  # SQLite INTEGER 上限


def check_month(month: str) -> str:
    text = str(month or "").strip()
    if not MONTH_RE.match(text):
        raise errors.BadRequest(f"月份格式应为 YYYY-MM：{month!r}",
                                code="Q_LEDGER_BAD_MONTH",
                                detail={"month": month})
    return text


def new_reservation_id() -> str:
    return "RS-" + uuid.uuid4().hex[:16]


def summarize(*, dept: str, month: str, budget_cents, used_cents,
              held_cents) -> dict:
    """一本月度账的四个读数：已用 / 预占 / 可用 / 欠额（整数分）。

    可用 = max(0, 预算 − 已用 − 预占中)；欠额 = max(0, 已用 − 预算)。
    欠额只认已记账的实际费用，预占中的预估不算欠债。
    """
    used, held = int(used_cents), int(held_cents)
    budget = None if budget_cents is None else int(budget_cents)
    base = budget if budget is not None else 0
    return {
        "dept": dept,
        "month": month,
        "budget_cents": budget,
        "used_cents": used,
        "held_cents": held,
        "available_cents": max(0, base - used - held),
        "deficit_cents": max(0, used - base),
    }


def ensure_reservable(*, dept: str, month: str, budget_cents: int,
                      used_cents: int, held_cents: int,
                      estimate_cents: int) -> None:
    """按预估费用守预算：可用 = 预算 − 已用 − 预占中，不足即拒（409）。

    欠额存在时可用恒为 0，任何正预估都会被拒，即「欠额阻止新查询」。
    """
    available = int(budget_cents) - int(used_cents) - int(held_cents)
    if int(estimate_cents) > available:
        raise errors.Conflict(
            f"{dept} {month} 月度预算不足：预估 {int(estimate_cents)} 分，"
            f"可用 {max(0, available)} 分",
            code="Q_LEDGER_OVER_BUDGET",
            detail={"dept": dept, "month": month,
                    "estimate_cents": int(estimate_cents),
                    "available_cents": max(0, available),
                    "deficit_cents": max(0, int(used_cents) - int(budget_cents))})
