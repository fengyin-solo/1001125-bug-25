"""堆存计费业务规则：状态流转、字段校验、免堆期核算与账单幂等都收在这里。

收款口径不在本模块：作业结算（settle）保持原有规则，这里只管堆存计费单。
"""
from __future__ import annotations

import re
from typing import Any

from app.store import store

MODULE = "storage"
REQUIRED_FIELDS = ["计费单号", "关联箱号", "计费周期"]
# 待核算 -> 已核算（生成账单）-> 已对账 -> 已开票，已开票为终态。
STATUS_ORDER = ["待核算", "已核算", "已对账", "已开票"]
BILL_ACTION = "生成账单"
RECONCILE_ACTION = "确认对账"
INVOICE_ACTION = "开具发票"
ACTION_RULES = {BILL_ACTION: "已核算", RECONCILE_ACTION: "已对账", INVOICE_ACTION: "已开票"}
# 每个动作允许的前置状态；已开票不在任何动作的前置里，终态不可再改。
ACTION_FROM = {BILL_ACTION: ["待核算"], RECONCILE_ACTION: ["已核算"], INVOICE_ACTION: ["已对账"]}
NEGATIVE_ACTIONS = []

FREE_DAYS = 7
BILL_NO_PREFIX = "BILL"

_PERIOD_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_number(value: Any) -> float | None:
    """从「5 元/天」「12.50」这类文本里取数值；空值或没有数字时返回 None。"""
    text = _as_text(value)
    if not text:
        return None
    match = _NUMBER_RE.search(text.replace(",", ""))
    if match is None:
        return None
    return float(match.group())


def _parse_period(value: Any) -> tuple[str | None, str | None]:
    """解析「2026-09-01 至 2026-09-10」这类计费周期，按出现顺序取起止日期。"""
    text = _as_text(value)
    if not text:
        return None, None
    dates = _PERIOD_RE.findall(text)
    if not dates:
        return None, None
    return dates[0], (dates[1] if len(dates) > 1 else dates[0])


def _period_days(value: Any) -> int | None:
    """计费周期按自然日差加一天（含首尾）计堆存天数，解析不出来返回 None。"""
    start, end = _parse_period(value)
    if start is None or end is None:
        return None
    from datetime import date

    start_day = date.fromisoformat(start)
    end_day = date.fromisoformat(end)
    days = (end_day - start_day).days + 1
    return days if days >= 1 else None


def _parse_days(value: Any) -> int | None:
    number = _parse_number(value)
    if number is None:
        return None
    days = int(number)
    return days if days > 0 else None


def _to_view(entry: dict[str, Any]) -> dict[str, Any]:
    """列表/明细出参：在不动业务字段的前提下补上免堆期提示与超期标记。"""
    view = dict(entry)
    notes = inspect_entry(entry)
    if notes:
        view["堆存提示"] = "；".join(notes)
    else:
        view["堆存提示"] = None
    # 超免堆期标记按堆存天数实时核算，顺带把历史记录的标记补齐。
    days = _parse_days(entry.get("堆存天数"))
    overdue = days is not None and days > FREE_DAYS
    entry["overdue"] = overdue
    view["overdue"] = overdue
    return view


def inspect_entry(entry: dict[str, Any]) -> list[str]:
    """给出一条计费单的边界说明：超免堆期挑出来并说明原因。"""
    notes: list[str] = []
    period = _as_text(entry.get("计费周期"))
    days = _parse_days(entry.get("堆存天数"))

    if not period:
        notes.append("计费周期为空，无法核算堆存天数")
    if _parse_period(period)[0] is None:
        if period:
            notes.append("计费周期格式无法识别，应为起止日期（如 2026-09-01 至 2026-09-10）")
    if days is None:
        notes.append("堆存天数缺失或不是正整数")

    if days is not None and period:
        period_days = _period_days(period)
        if period_days is not None and period_days != days:
            notes.append(f"堆存天数 {days} 天与计费周期不符，按周期应为 {period_days} 天")

    if days is not None and days > FREE_DAYS:
        notes.append(f"堆存 {days} 天，超过免费堆存期 {FREE_DAYS} 天，超期 {days - FREE_DAYS} 天需计费")
    elif days is not None and days > 0 and days <= FREE_DAYS:
        notes.append(f"堆存 {days} 天，处于 {FREE_DAYS} 天免费堆存期内")

    rate = _parse_number(entry.get("计费标准"))
    if rate is None:
        notes.append("计费标准为空或无法识别单价")

    return notes


class StorageService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        overdue_only: bool = False,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("计费单号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]

        views = [_to_view(row) for row in rows]
        # 超免堆期标记以堆存天数重新核算为准，顺带把历史记录的标记补齐。
        if overdue_only:
            views = [view for view in views if view["overdue"]]
        total = len(views)
        start = max(page - 1, 0) * size
        return views[start:start + size], total

    def stats(self) -> dict[str, float | int]:
        """统计口径：待核算单数、超免堆期单数、本月应收（已生成账单的应收金额合计）。"""
        rows = store.rows(MODULE)
        pending = sum(1 for row in rows if row.get("status") == STATUS_ORDER[0])
        overdue = 0
        receivable = 0.0
        for row in rows:
            days = _parse_days(row.get("堆存天数"))
            if days is not None and days > FREE_DAYS:
                overdue += 1
            amount = _parse_number(row.get("应收金额"))
            if row.get("账单号") and amount is not None:
                receivable += amount
        return {"待核算计费单": pending, "超免堆期计费单": overdue, "账单应收合计": round(receivable, 2)}

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return _to_view(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not _as_text(values.get(field))]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        # 登记时允许补录的核算字段原样保留；出账前才做硬校验。
        for field in ("堆存天数", "计费标准", "应收金额", "客户名称"):
            if field in values:
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["overdue"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str, bool]:
        """执行状态动作，返回 (记录, 说明, 是否幂等命中)。

        校验不通过、状态不允许时记录原样保留，调用方可让用户改完直接重试。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"计费单 {entry_id} 不存在或已归档", False
        action = (action or "").strip()
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于堆存计费可执行范围", False

        current = str(entry.get("status") or "")
        if current == STATUS_ORDER[-1]:
            # 终态：单据原样返回，前端可继续展示且不再出现可修改入口。
            return _to_view(entry), "计费单已开票，终态单据不能再执行任何修改动作", False
        # 幂等放在状态校验之前：同一张计费单无论重复点几次，都只返回已生成的那一条账单。
        if action == BILL_ACTION and entry.get("账单号"):
            return _to_view(entry), f"账单已存在（{entry['账单号']}），无需重复生成", True
        allowed = ACTION_FROM[action]
        if current not in allowed:
            return _to_view(entry), (
                f"当前状态为「{current}」，不能{action}；"
                f"仅{'、'.join(allowed)}状态可执行该动作"
            ), False

        if action == BILL_ACTION:
            return self._generate_bill(entry)
        if action == RECONCILE_ACTION:
            entry["status"] = "已对账"
            entry["pending"] = True
            return _to_view(entry), "计费单已确认对账", True
        entry["status"] = "已开票"
        entry["pending"] = False
        return _to_view(entry), "计费单已开具发票", True

    # -- 生成账单：边界校验 + 幂等 ------------------------------------------

    def _generate_bill(self, entry: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
        # 1. 计费周期、计费标准为空（或无法识别）时直接挡住，不出账。
        period = _as_text(entry.get("计费周期"))
        if not period:
            return _to_view(entry), "账单未生成：计费周期为空，请补全计费周期后重试", False
        period_days = _period_days(period)
        if period_days is None:
            return (
                _to_view(entry),
                "账单未生成：计费周期格式无法识别，应为起止日期（如 2026-09-01 至 2026-09-10）",
                False,
            )

        rate = _parse_number(entry.get("计费标准"))
        if rate is None:
            return _to_view(entry), "账单未生成：计费标准为空或无法识别单价，请补全后重试", False
        if rate < 0:
            return _to_view(entry), "账单未生成：计费标准单价不能为负数", False

        # 2. 堆存天数必须和计费周期对得上；空着或填错时按周期纠正并明确告知。
        days = _parse_days(entry.get("堆存天数"))
        if days is None:
            return _to_view(entry), f"账单未生成：堆存天数缺失，按计费周期应为 {period_days} 天，请核对后重试", False
        if days != period_days:
            return (
                _to_view(entry),
                f"账单未生成：堆存天数 {days} 天与计费周期不符，按周期应为 {period_days} 天，请核对后重试",
                False,
            )

        # 3. 幂等：同一张计费单重复生成只留一条账单，直接返回已生成的账单。
        existing_no = entry.get("账单号")
        if existing_no:
            return _to_view(entry), f"账单已存在（{existing_no}），无需重复生成", True

        # 4. 金额按超免堆期口径核算；已有手填金额且对不上时拦住，避免两边不一致。
        overdue_days = max(days - FREE_DAYS, 0)
        amount = round(overdue_days * rate, 2)
        current_amount = _parse_number(entry.get("应收金额"))
        if current_amount is not None and abs(current_amount - amount) > 0.01:
            return (
                _to_view(entry),
                f"账单未生成：已填应收金额 {current_amount:.2f} 与核算金额 {amount:.2f} 不一致"
                f"（超免堆期 {overdue_days} 天 × {rate:g}），请核对计费标准或应收金额",
                False,
            )

        rows = store.rows(MODULE)
        bill_no = f"{BILL_NO_PREFIX}-{len(rows) + 1:04d}-{int(entry['id']):04d}"
        entry["账单号"] = bill_no
        entry["应收金额"] = amount
        entry["堆存天数"] = days
        entry["status"] = "已核算"
        entry["pending"] = True
        entry["overdue"] = overdue_days > 0
        entry["abnormal"] = overdue_days > 0
        if overdue_days > 0:
            message = (
                f"账单 {bill_no} 已生成：堆存 {days} 天，超过免费堆存期 {FREE_DAYS} 天，"
                f"超期 {overdue_days} 天 × {rate:g}，应收 {amount:.2f}"
            )
        else:
            message = f"账单 {bill_no} 已生成：堆存 {days} 天在免费堆存期内，应收 {amount:.2f}"
        return _to_view(entry), message, True
