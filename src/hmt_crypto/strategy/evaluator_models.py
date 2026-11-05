"""Immutable results emitted by the portable Strategy-v2 evaluator."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuleDiagnostic:
    """One evaluated strategy rule."""

    rule: str
    passed: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        """Return a detached JSON-compatible representation."""

        return {"rule": self.rule, "pass": self.passed, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class GroupDiagnostic:
    """One AND-connected accelerator condition group."""

    group_id: str
    passed: bool
    failed_rule: str | None
    rule_passes: tuple[bool, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a detached JSON-compatible representation."""

        return {
            "group_id": self.group_id,
            "pass": self.passed,
            "failed_rule": self.failed_rule,
            "rule_passes": list(self.rule_passes),
        }


@dataclass(frozen=True, slots=True)
class AcceleratorDiagnostic:
    """One priority-ordered accelerator evaluation."""

    accelerator_id: str
    status: str
    condition_mode: str
    groups: tuple[GroupDiagnostic, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a detached JSON-compatible representation."""

        return {
            "accelerator_id": self.accelerator_id,
            "status": self.status,
            "condition_mode": self.condition_mode,
            "groups": [group.to_dict() for group in self.groups],
        }


@dataclass(frozen=True, slots=True)
class FinalAction:
    """Broker-neutral action units and ATR multipliers."""

    lots: int
    atr_multiplier: int | float
    sl_atr_multiplier: int | float
    target_atr_multiplier: int | float

    def to_dict(self) -> dict[str, object]:
        """Return a detached JSON-compatible representation."""

        return {
            "lots": self.lots,
            "atr_multiplier": self.atr_multiplier,
            "sl_atr_multiplier": self.sl_atr_multiplier,
            "target_atr_multiplier": self.target_atr_multiplier,
        }


@dataclass(frozen=True, slots=True)
class StrategyDiagnostics:
    """Stable diagnostics for explainability and golden parity."""

    engine_rules: tuple[RuleDiagnostic, ...]
    failed_rule: str | None
    accelerator_condition_mode: str | None
    accelerator_group_passes: tuple[tuple[str, bool], ...]
    accelerator_evaluations: tuple[AcceleratorDiagnostic, ...]
    final_action: FinalAction | None
    trace_statuses: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, object]:
        """Return a detached JSON-compatible representation."""

        return {
            "engine_rules": [rule.to_dict() for rule in self.engine_rules],
            "failed_rule": self.failed_rule,
            "accelerator_condition_mode": self.accelerator_condition_mode,
            "accelerator_group_passes": dict(self.accelerator_group_passes),
            "accelerator_evaluations": [
                accelerator.to_dict() for accelerator in self.accelerator_evaluations
            ],
            "final_action": None if self.final_action is None else self.final_action.to_dict(),
            "trace_statuses": dict(self.trace_statuses),
        }


@dataclass(frozen=True, slots=True)
class StrategyEvaluation:
    """Pure, non-authorizing Strategy-v2 decision."""

    passed: bool
    status: str
    reason: str
    direction: str
    matched_engine_id: str | None
    matched_accelerator_id: str | None
    lots: int
    atr_multiplier: int | float | None
    sl_atr_multiplier: int | float | None
    target_atr_multiplier: int | float
    atr_multiplier_source: str | None
    sl_multiplier_source: str | None
    target_multiplier_source: str | None
    stock_target_override_symbol: str | None
    diagnostics: StrategyDiagnostics

    def to_dict(self) -> dict[str, object]:
        """Return a detached JSON-compatible representation."""

        return {
            "passed": self.passed,
            "status": self.status,
            "reason": self.reason,
            "direction": self.direction,
            "matched_engine_id": self.matched_engine_id,
            "matched_accelerator_id": self.matched_accelerator_id,
            "lots": self.lots,
            "atr_multiplier": self.atr_multiplier,
            "sl_atr_multiplier": self.sl_atr_multiplier,
            "target_atr_multiplier": self.target_atr_multiplier,
            "atr_multiplier_source": self.atr_multiplier_source,
            "sl_multiplier_source": self.sl_multiplier_source,
            "target_multiplier_source": self.target_multiplier_source,
            "stock_target_override_symbol": self.stock_target_override_symbol,
            "diagnostics": self.diagnostics.to_dict(),
        }


__all__ = [
    "AcceleratorDiagnostic",
    "FinalAction",
    "GroupDiagnostic",
    "RuleDiagnostic",
    "StrategyDiagnostics",
    "StrategyEvaluation",
]
