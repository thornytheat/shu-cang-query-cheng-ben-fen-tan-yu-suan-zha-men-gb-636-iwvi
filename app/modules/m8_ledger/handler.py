"""M8 月度预算账本入口：预算设定、跨批次预占、结算、撤销、状态与清单。

- PUT  /api/ledger/budget        设定部门本月预算（可按 month 指定其他月份）
- POST /api/ledger/reserve       按预估费用预占额度（并发安全，可带幂等键）
- POST /api/ledger/settle        执行回执结算，全额记账（幂等，超额记欠额）
- POST /api/ledger/cancel        撤销未执行的预占
- GET  /api/ledger/status        本月已用 / 预占 / 可用 / 欠额
- GET  /api/ledger/reservations  预占清单（对账与中断恢复入口）
- GET  /api/ledger/settlements   实账清单
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app import errors

from . import service

router = APIRouter(tags=["ledger"])


async def _body(request: Request) -> dict:
    body = await request.json()
    if not isinstance(body, dict) or not body:
        raise errors.BadRequest("请求体必须是非空 JSON 对象", code="E_BAD_REQUEST")
    return body


@router.put("/api/ledger/budget")
async def put_budget(request: Request) -> dict:
    app = request.app
    return service.set_budget(app.state.store, app.state.settings,
                              await _body(request))


@router.post("/api/ledger/reserve")
async def post_reserve(request: Request) -> dict:
    app = request.app
    return service.reserve(app.state.store, app.state.settings,
                           await _body(request))


@router.post("/api/ledger/settle")
async def post_settle(request: Request) -> dict:
    app = request.app
    return service.settle(app.state.store, app.state.settings,
                          await _body(request))


@router.post("/api/ledger/cancel")
async def post_cancel(request: Request) -> dict:
    app = request.app
    return service.cancel(app.state.store, app.state.settings,
                          await _body(request))


@router.get("/api/ledger/status")
def get_status(request: Request, dept: str | None = None,
               month: str | None = None) -> dict:
    app = request.app
    return service.status(app.state.store, app.state.settings, dept, month)


@router.get("/api/ledger/reservations")
def get_reservations(request: Request, dept: str | None = None,
                     month: str | None = None,
                     status: str | None = None) -> dict:
    app = request.app
    return service.reservations(app.state.store, app.state.settings,
                                dept, month, status)


@router.get("/api/ledger/settlements")
def get_settlements(request: Request, dept: str | None = None,
                    month: str | None = None) -> dict:
    app = request.app
    return service.settlements(app.state.store, app.state.settings, dept, month)
