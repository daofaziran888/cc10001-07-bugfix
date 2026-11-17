"""质量审计 时间线服务：生成作业时间序列和示例数据。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.store import store

MODULE = "quality_check"
REQUIRED_FIELDS = ["记录编号", "关联批次", "检查项"]
LIST_FIELDS = ["记录编号", "关联批次", "检查项", "阈值", "实际值", "判定结果", "审计时间", "处理建议"]
STATUS_ORDER = ["待审计", "已通过", "需修复", "已忽略"]
ACTION_RULES = {"执行审计": "已通过", "确认通过": "需修复", "提交修复": "已忽略"}
NEGATIVE_ACTIONS = []

# 审计结论 -> 审计记录状态：通过终态、失败终态与待复核终态分别落位
VERDICT_STATUS = {"通过": "已通过", "不通过": "需修复", "待复核": "待审计"}


class QualityCheckService:
    def get_timeline(self) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        today = date.today()
        items: list[dict] = []
        for i, row in enumerate(rows):
            start = (today - timedelta(days=7 - i * 2)).isoformat()
            end = (today + timedelta(days=3 - i)).isoformat()
            items.append({
                "id": int(row.get("id", i + 1)),
                "name": str(row.get(list(row.keys())[1] if len(row) > 1 else "name", "")),
                "start": start,
                "end": end,
                "status": str(row.get("status", STATUS_ORDER[i % len(STATUS_ORDER)])),
                "logs": f"[{start}] 作业启动\n[{start}] 正在处理...\n[{end}] 任务{'完成' if row.get('status') == STATUS_ORDER[-1] else '进行中'}",
            })
        return items

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [f for f in REQUIRED_FIELDS if not str(values.get(f) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(r.get("id", 0)) for r in rows), default=0) + 1}
        entry.update({f: values.get(f) for f in LIST_FIELDS if values.get(f) is not None})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> dict[str, Any] | None:
        """更新既有审计记录的字段；重复审计同一批次时复用，避免多出审计记录。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        for field, value in values.items():
            if value is not None:
                entry[field] = value
        return entry

    def apply_verdict(self, entry: dict[str, Any], verdict: str) -> dict[str, Any]:
        """把审计结论落到记录状态：通过、失败与待复核三种终态。"""
        entry["status"] = VERDICT_STATUS.get(verdict, STATUS_ORDER[0])
        entry["pending"] = verdict != "通过"
        entry["abnormal"] = verdict == "不通过"
        return entry

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"审计记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于质量审计可执行范围"
        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"审计记录已{action}"
