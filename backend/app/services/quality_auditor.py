"""质量审计引擎：消费采集仓储数据，按真实行数生成审计记录。

数据流：result_store -> quality_check

判定依据：批次的原始行数、有效行数、存储路径（存储文件实际行数）与校验摘要长度。
- 声明有效行数与存储文件实际行数冲突时，按存储文件判定失败并给出修复建议；
- 行数缺失或存储文件不可读时，保留声明值但把批次置为待复核并阻止通过；
- 两种终态都不会把冲突批次标成通过。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from app.store import store
from app.services.quality_check import QualityCheckService

RESULT_MODULE = "result_store"
AUDIT_TABLE = "audit_records"

# 判定阈值：有效率下限与校验摘要最小长度
MIN_VALID_RATE = 0.8
MIN_CHECKSUM_LENGTH = 8
THRESHOLD_TEXT = (
    f"有效率>={int(MIN_VALID_RATE * 100)}%, "
    f"摘要长度>={MIN_CHECKSUM_LENGTH}, "
    "有效行数与存储文件一致"
)

# 判定结论 -> 修正后的批次状态（失败或待复核终态，冲突批次不得标为通过）
BATCH_STATUS_BY_VERDICT = {
    "通过": "校验通过",
    "不通过": "校验失败",
    "待复核": "待复核",
}

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class QualityAuditor:
    def __init__(self) -> None:
        self._qc_svc = QualityCheckService()

    # ---------- 行数依据 ----------

    @staticmethod
    def _parse_row_count(value: Any) -> int | None:
        """把批次里的行数字段解析成整数；占位文本或空值返回 None。"""
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _resolve_storage_path(storage_path: str) -> str:
        if os.path.isabs(storage_path):
            return storage_path
        return os.path.join(_BACKEND_DIR, storage_path)

    def _storage_row_count(self, storage_path: str) -> int | None:
        """读取存储文件的实际行数；文件缺失或不可解析时返回 None。"""
        if not storage_path:
            return None
        path = self._resolve_storage_path(storage_path)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read().strip()
        except OSError:
            return None
        if not text:
            return 0
        if path.endswith(".json"):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                return None
            if isinstance(payload, list):
                return len(payload)
            if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
                return len(payload["rows"])
            return 1
        return sum(1 for line in text.splitlines() if line.strip())

    def _build_basis(self, batch: dict[str, Any]) -> dict[str, Any]:
        """从批次真实字段构建行数依据，并读取存储文件实际行数。"""
        raw = self._parse_row_count(batch.get("原始行数"))
        valid = self._parse_row_count(batch.get("有效行数"))
        checksum = str(batch.get("校验摘要") or "")
        storage_path = str(batch.get("存储路径") or "").strip()
        storage_rows = self._storage_row_count(storage_path)
        valid_rate = (valid / raw) if raw is not None and raw > 0 and valid is not None else None
        return {
            "raw_rows": raw,
            "valid_rows": valid,
            "valid_rate": valid_rate,
            "storage_path": storage_path,
            "storage_rows": storage_rows,
            "checksum": checksum,
            "checksum_length": len(checksum),
        }

    # ---------- 判定 ----------

    def _judge(self, basis: dict[str, Any]) -> tuple[str, list[str], list[str]]:
        """按行数依据判定结论，返回 (判定结果, 问题列表, 修复建议列表)。"""
        issues: list[str] = []
        suggestions: list[str] = []
        raw = basis["raw_rows"]
        valid = basis["valid_rows"]
        storage_rows = basis["storage_rows"]

        # 行数口径核对：声明有效行数 vs 存储文件实际行数
        row_conflict = valid is not None and storage_rows is not None and storage_rows != valid
        unverifiable = storage_rows is None

        if raw is None:
            issues.append("原始行数缺失或非数值")
        elif raw <= 0:
            issues.append("原始行数为零")
        if valid is None:
            issues.append("有效行数缺失或非数值")
        if raw is not None and valid is not None and raw > 0:
            if valid > raw:
                issues.append("有效行数超过原始行数")
            rate = basis["valid_rate"]
            if rate is not None and rate < MIN_VALID_RATE:
                issues.append(f"有效率{rate:.1%}低于阈值{int(MIN_VALID_RATE * 100)}%")
        if basis["checksum_length"] < MIN_CHECKSUM_LENGTH:
            issues.append("校验摘要过短")

        if row_conflict:
            # 做法一：以存储文件为准判定失败，并生成修复建议
            issues.append(f"声明有效行数{valid}与存储文件实际行数{storage_rows}不一致")
            suggestions.append(
                f"按存储文件实际行数{storage_rows}修正有效行数，并重新生成校验摘要后重新入库"
            )
        if unverifiable:
            issues.append("存储文件缺失或不可读，无法核对行数")
            suggestions.append("补齐存储文件或修正存储路径后重新审计")

        if raw is None or valid is None or unverifiable:
            # 做法二：行数依据不足时保留声明值，批次置为待复核并阻止通过
            verdict = "待复核"
        elif issues:
            verdict = "不通过"
        else:
            verdict = "通过"
        return verdict, issues, suggestions

    @staticmethod
    def _actual_text(basis: dict[str, Any]) -> str:
        rate = basis["valid_rate"]
        rate_text = f"{rate:.1%}" if rate is not None else "无法计算"
        raw_text = str(basis["raw_rows"]) if basis["raw_rows"] is not None else "未知"
        valid_text = str(basis["valid_rows"]) if basis["valid_rows"] is not None else "未知"
        storage = basis["storage_rows"]
        storage_text = f"{storage}行" if storage is not None else "不可读"
        return (
            f"有效{valid_text}/原始{raw_text}(有效率{rate_text}), "
            f"存储文件{storage_text}, 摘要{basis['checksum_length']}字符"
        )

    # ---------- 审计记录（同一批次只保留一条当前有效结论） ----------

    def _next_audit_id(self) -> int:
        rows = store.joint_rows(AUDIT_TABLE)
        return max((int(r.get("id", 0)) for r in rows), default=0) + 1

    @staticmethod
    def _find_record(batch_id: int) -> dict[str, Any] | None:
        for record in store.joint_rows(AUDIT_TABLE):
            if int(record.get("batch_id", 0)) == batch_id:
                return record
        return None

    def _upsert_qc_entry(
        self,
        record: dict[str, Any] | None,
        values: dict[str, Any],
        verdict: str,
    ) -> tuple[dict[str, Any] | None, str]:
        """写入或更新质量审计条目；重复审计同一批次不会多出审计记录。"""
        entry: dict[str, Any] | None = None
        if record is not None and record.get("audit_entry_id") is not None:
            candidate = store.find("quality_check", int(record["audit_entry_id"]))
            # 仅当条目确属本批次（记录编号一致）才复用；重启后条目 id 可能撞上其他批次，
            # 直接更新会把别的批次结论串改
            if candidate is not None and str(candidate.get("记录编号")) == values["记录编号"]:
                entry = self._qc_svc.update_entry(int(record["audit_entry_id"]), values)
        if entry is None:
            entry, missing = self._qc_svc.create_entry(values)
            if entry is None:
                return None, f"审计记录写入失败：缺少 {missing}"
        self._qc_svc.apply_verdict(entry, verdict)
        return entry, ""

    def audit_batch(self, batch_id: int) -> dict[str, Any]:
        """对指定采集仓储批次按真实行数执行质量审计，生成或更新审计记录。"""
        batch = store.find(RESULT_MODULE, batch_id)
        if batch is None:
            return {"ok": False, "error": f"结果批次 {batch_id} 不存在"}

        basis = self._build_basis(batch)
        verdict, issues, suggestions = self._judge(basis)
        audited_at = time.strftime("%Y-%m-%d %H:%M:%S")
        actual = self._actual_text(basis)

        record = self._find_record(batch_id)
        audit_no = (
            str(record.get("audit_no"))
            if record is not None and record.get("audit_no")
            else f"AUDIT-{batch_id}-{int(time.time())}"
        )
        qc_values = {
            "记录编号": audit_no,
            "关联批次": str(batch.get("批次编号", "")),
            "检查项": "完整性与有效率",
            "阈值": THRESHOLD_TEXT,
            "实际值": actual,
            "判定结果": verdict,
            "审计时间": audited_at,
            "处理建议": "; ".join(suggestions or issues) if (suggestions or issues) else "无需处理",
        }
        audit_entry, error = self._upsert_qc_entry(record, qc_values, verdict)
        if audit_entry is None:
            return {"ok": False, "error": error}

        # 修正批次状态：判定为通过前必须保证行数与存储文件一致
        batch_status = BATCH_STATUS_BY_VERDICT[verdict]
        batch["status"] = batch_status
        batch["pending"] = verdict == "待复核"
        batch["abnormal"] = verdict == "不通过"

        # 跨模块台账：保留行数依据，同一批次更新而不是新增
        record_fields = {
            "batch_id": batch_id,
            "audit_entry_id": audit_entry["id"],
            "audit_no": audit_no,
            "batch_no": str(batch.get("批次编号", "")),
            "verdict": verdict,
            "issues": issues,
            "suggestions": suggestions,
            "threshold": THRESHOLD_TEXT,
            "actual": actual,
            "raw_rows": basis["raw_rows"],
            "valid_rows": basis["valid_rows"],
            "valid_rate": round(basis["valid_rate"], 4) if basis["valid_rate"] is not None else None,
            "storage_path": basis["storage_path"],
            "storage_rows": basis["storage_rows"],
            "checksum": basis["checksum"],
            "checksum_length": basis["checksum_length"],
            "batch_status": batch_status,
            "audited_at": audited_at,
        }
        if record is None:
            record = {"id": self._next_audit_id(), **record_fields}
            store.joint_append(AUDIT_TABLE, record)
        else:
            record.update(record_fields)
            store.joint_save(AUDIT_TABLE)

        return {
            "ok": True,
            "audit": record,
            "audit_entry": audit_entry,
            "batch": batch,
        }

    def audit_all(self) -> dict[str, Any]:
        """对采集仓储中所有待校验与待复核批次执行审计；重复执行不会多出审计记录。"""
        batches = [
            b for b in store.rows(RESULT_MODULE)
            if b.get("status") in ("接收中", "已落盘", "待复核")
        ]
        results: list[dict[str, Any]] = []
        for batch in batches:
            res = self.audit_batch(int(batch["id"]))
            results.append(res)
        passed = sum(1 for r in results if r.get("ok") and r.get("audit", {}).get("verdict") == "通过")
        failed = sum(1 for r in results if r.get("ok") and r.get("audit", {}).get("verdict") == "不通过")
        review = sum(1 for r in results if r.get("ok") and r.get("audit", {}).get("verdict") == "待复核")
        return {
            "ok": True,
            "audited": len(results),
            "passed": passed,
            "failed": failed,
            "review": review,
            "results": results,
        }

    def list_records(self) -> list[dict[str, Any]]:
        return list(store.joint_rows(AUDIT_TABLE))

    def get_record(self, record_id: int) -> dict[str, Any] | None:
        return store.joint_find(AUDIT_TABLE, record_id)


quality_auditor = QualityAuditor()
