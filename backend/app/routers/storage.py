"""堆存计费接口：维护计费单，覆盖生成账单、确认对账、开具发票等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.storage import StorageService

router = APIRouter(prefix="/api/storage", tags=["堆存计费"])

service = StorageService()

LIST_FIELDS = ["计费单号", "关联箱号", "计费周期", "堆存天数", "计费标准", "应收金额", "客户名称", "计费状态"]
STATUSES = ["待核算", "已核算", "已对账", "已开票"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按计费单号检索"),
    status: str | None = Query(default=None, description="待核算、已核算、已对账、已开票"),
    overdue: bool = Query(default=False, description="只看超过免费堆存期的记录"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按计费单号与状态过滤堆存计费列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, overdue=overdue, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出堆存计费清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "storage", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条计费单明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"计费单 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条计费单；缺字段或重复单号时说明原因，同一张计费单只留一条。"""
    entry, message = service.create_entry(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """补录/修改待核算计费单的计费内容；已出账的单子不允许再改。"""
    entry, message = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条计费单执行生成账单、确认对账、开具发票；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
