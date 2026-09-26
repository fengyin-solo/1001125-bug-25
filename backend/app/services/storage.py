"""堆存计费业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

import re
from typing import Any

from app.store import store

MODULE = "storage"
REQUIRED_FIELDS = ["计费单号", "关联箱号", "计费周期"]
STATUS_ORDER = ["待核算", "已核算", "已对账", "已开票"]
ACTION_RULES = {"生成账单": "已核算", "确认对账": "已对账", "开具发票": "已开票"}
NEGATIVE_ACTIONS: list[str] = []

# 免费堆存期：堆存天数超过它才产生堆存费，超期记录要单独挑出来说明
FREE_STORAGE_DAYS = 7
# 出账前必须补齐的计费要素
BILLING_CHECK_FIELDS = ["计费周期", "计费标准", "堆存天数"]
# 待核算阶段允许补录/修改的字段（计费单号是唯一标识，不允许改）
EDITABLE_FIELDS = ["关联箱号", "计费周期", "堆存天数", "计费标准", "客户名称"]
# 计费周期只认「2026-09」或「2026-09-01~2026-09-30」两种写法
PERIOD_PATTERN = re.compile(
    r"^\d{4}-(0[1-9]|1[0-2])(-\d{2})?([~～]\d{4}-(0[1-9]|1[0-2])(-\d{2})?)?$"
)


def _to_number(value: Any) -> float | None:
    """把堆存天数、计费标准这类字段转成数字；转不了就返回 None。"""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _fmt_number(value: float) -> str:
    """金额、天数展示时去掉多余的 .0。"""
    return f"{value:g}"


def _overdue_reason(row: dict[str, Any]) -> str | None:
    """超过免费堆存期的记录给出原因说明；未超期或天数缺失返回 None。"""
    days = _to_number(row.get("堆存天数"))
    if days is None or days <= FREE_STORAGE_DAYS:
        return None
    extra = days - FREE_STORAGE_DAYS
    return f"堆存{_fmt_number(days)}天，超出免费堆存期{FREE_STORAGE_DAYS}天，超期{_fmt_number(extra)}天"


def _annotate(row: dict[str, Any]) -> dict[str, Any]:
    """列表与详情统一附上超期说明，不落库，按当前数据实时算。"""
    return {**row, "超期原因": _overdue_reason(row)}


def _validate_billing_inputs(row: dict[str, Any]) -> list[str]:
    """出账前的计费要素校验：返回问题清单，空清单表示可以出账。"""
    problems: list[str] = []
    period = str(row.get("计费周期") or "").strip()
    if not period:
        problems.append("计费周期为空")
    elif not PERIOD_PATTERN.match(period):
        problems.append(f"计费周期「{period}」格式不正确（应为 2026-09 或 2026-09-01~2026-09-30）")
    rate = _to_number(row.get("计费标准"))
    if rate is None:
        problems.append("计费标准为空或不是有效的日费率")
    elif rate < 0:
        problems.append("计费标准不能为负数")
    days = _to_number(row.get("堆存天数"))
    if days is None:
        problems.append("堆存天数缺失或不是有效数字")
    elif days < 0:
        problems.append("堆存天数不能为负数")
    return problems


class StorageService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        overdue: bool = False,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("计费单号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        annotated = [_annotate(row) for row in rows]
        if overdue:
            annotated = [row for row in annotated if row["超期原因"]]
        total = len(annotated)
        start = max(page - 1, 0) * size
        return annotated[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        if row is None:
            return None
        return _annotate(row)

    def _find_by_bill_no(self, bill_no: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if str(row.get("计费单号", "")).strip() == bill_no:
                return row
        return None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """登记计费单：同一张计费单（计费单号相同）只留一条，重复登记返回原记录。"""
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        bill_no = str(values["计费单号"]).strip()
        existing = self._find_by_bill_no(bill_no)
        if existing is not None:
            return _annotate(existing), f"计费单 {bill_no} 已存在，同一张计费单只保留一条，未重复登记"
        problems = self._validate_optional_inputs(values)
        if problems:
            return None, "；".join(problems)
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry["计费单号"] = bill_no
        for field in EDITABLE_FIELDS:
            entry[field] = values.get(field)
        entry["应收金额"] = None
        entry["status"] = STATUS_ORDER[0]
        entry["计费状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return _annotate(entry), "计费单已登记"

    def _validate_optional_inputs(self, values: dict[str, Any]) -> list[str]:
        """登记/补录时，填了的内容必须有效；没填的留到出账时再拦。"""
        problems: list[str] = []
        period = str(values.get("计费周期") or "").strip()
        if period and not PERIOD_PATTERN.match(period):
            problems.append(f"计费周期「{period}」格式不正确（应为 2026-09 或 2026-09-01~2026-09-30）")
        for field in ("堆存天数", "计费标准"):
            raw = values.get(field)
            if raw is None or str(raw).strip() == "":
                continue
            number = _to_number(raw)
            if number is None:
                problems.append(f"{field}不是有效数字")
            elif number < 0:
                problems.append(f"{field}不能为负数")
        return problems

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """补录/修改计费内容：仅待核算状态可改，已出账的单子不能残留修改入口。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"计费单 {entry_id} 不存在或已归档"
        if entry.get("status") != STATUS_ORDER[0]:
            return None, f"计费单当前状态为{entry.get('status')}，仅待核算状态可以修改计费内容"
        problems = self._validate_optional_inputs(values)
        if problems:
            return None, "；".join(problems)
        for field in EDITABLE_FIELDS:
            if field in values:
                entry[field] = values[field]
        return _annotate(entry), "计费单内容已更新"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"计费单 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于堆存计费可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        current = str(entry.get("status") or STATUS_ORDER[0])
        # 重复执行同一动作：结果与上次一致，直接返回现有记录，不产生第二条账单
        if current == target:
            return _annotate(entry), f"计费单已{action}，无需重复操作"
        if current == STATUS_ORDER[-1]:
            return None, f"计费单{current}，流程已办结，不能再执行任何操作"
        expected = STATUS_ORDER[STATUS_ORDER.index(target) - 1]
        if current != expected:
            return None, f"计费单当前状态为{current}，不能执行{action}，请先完成前置环节"
        if action == "生成账单":
            problems = _validate_billing_inputs(entry)
            if problems:
                # 校验不过就只读不改：已填内容原样保留，补全后可重试
                return None, "无法生成账单：" + "；".join(problems) + "，请补全后重试"
            days = _to_number(entry.get("堆存天数")) or 0.0
            rate = _to_number(entry.get("计费标准")) or 0.0
            billable_days = max(days - FREE_STORAGE_DAYS, 0)
            entry["应收金额"] = round(billable_days * rate, 2)
        entry["status"] = target
        entry["计费状态"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        message = f"计费单已{action}"
        if action == "生成账单":
            message += f"，应收金额 {_fmt_number(float(entry['应收金额']))} 元"
            reason = _overdue_reason(entry)
            if reason:
                message += f"（{reason}）"
        return _annotate(entry), message
