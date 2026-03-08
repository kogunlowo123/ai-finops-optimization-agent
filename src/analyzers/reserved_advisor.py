"""Reserved instance and savings plan advisor."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from src.collectors.aws_costs import CostRecord

logger = logging.getLogger(__name__)


@dataclass
class ReservationRecommendation:
    """A recommendation for purchasing reserved instances or savings plans."""

    service: str
    instance_type: str
    region: str
    current_monthly_cost: float
    reserved_monthly_cost: float
    monthly_savings: float
    annual_savings: float
    savings_percent: float
    commitment_term: str
    payment_option: str
    break_even_months: int
    confidence: str
    reasoning: str


class ReservedInstanceAdvisor:
    """Analyze usage patterns to recommend reserved instances and savings plans.

    Evaluates consistent usage patterns over time to identify workloads
    that would benefit from reserved capacity commitments.
    """

    RESERVED_DISCOUNT_RATES: dict[str, dict[str, float]] = {
        "1yr_no_upfront": {"discount": 0.30, "upfront": 0.0},
        "1yr_partial_upfront": {"discount": 0.35, "upfront": 0.4},
        "1yr_all_upfront": {"discount": 0.40, "upfront": 1.0},
        "3yr_no_upfront": {"discount": 0.45, "upfront": 0.0},
        "3yr_partial_upfront": {"discount": 0.50, "upfront": 0.4},
        "3yr_all_upfront": {"discount": 0.58, "upfront": 1.0},
    }

    def __init__(
        self,
        min_usage_days: int = 21,
        min_daily_cost: float = 1.0,
        preferred_term: str = "1yr_no_upfront",
    ) -> None:
        self.min_usage_days = min_usage_days
        self.min_daily_cost = min_daily_cost
        self.preferred_term = preferred_term

    def analyze(
        self,
        cost_records: list[CostRecord],
        lookback_days: int = 30,
    ) -> list[ReservationRecommendation]:
        """Analyze cost data and generate reservation recommendations.

        Args:
            cost_records: Daily cost records grouped by service.
            lookback_days: Number of days of data to analyze.

        Returns:
            List of reservation recommendations sorted by savings.
        """
        service_usage: dict[str, list[CostRecord]] = {}
        for record in cost_records:
            service_usage.setdefault(record.service, []).append(record)

        recommendations: list[ReservationRecommendation] = []

        for service, records in service_usage.items():
            rec = self._evaluate_service(service, records)
            if rec:
                recommendations.append(rec)

        recommendations.sort(key=lambda r: r.annual_savings, reverse=True)
        return recommendations

    def _evaluate_service(
        self,
        service: str,
        records: list[CostRecord],
    ) -> Optional[ReservationRecommendation]:
        """Evaluate a service's usage pattern for reservation suitability."""
        if len(records) < self.min_usage_days:
            return None

        daily_costs = [r.amount for r in records]
        avg_daily = sum(daily_costs) / len(daily_costs)
        min_daily = min(daily_costs)

        if avg_daily < self.min_daily_cost:
            return None

        consistency = min_daily / avg_daily if avg_daily > 0 else 0
        if consistency < 0.5:
            return None

        baseline_daily = min_daily
        baseline_monthly = baseline_daily * 30

        discount_info = self.RESERVED_DISCOUNT_RATES.get(
            self.preferred_term,
            self.RESERVED_DISCOUNT_RATES["1yr_no_upfront"],
        )
        discount_rate = discount_info["discount"]

        reserved_monthly = baseline_monthly * (1 - discount_rate)
        monthly_savings = baseline_monthly - reserved_monthly
        annual_savings = monthly_savings * 12

        if "3yr" in self.preferred_term:
            term_label = "3 years"
            break_even = int((baseline_monthly * discount_info["upfront"] * 36) / monthly_savings) if monthly_savings > 0 else 36
        else:
            term_label = "1 year"
            break_even = int((baseline_monthly * discount_info["upfront"] * 12) / monthly_savings) if monthly_savings > 0 else 12

        confidence = "high" if consistency > 0.8 else "medium" if consistency > 0.6 else "low"

        return ReservationRecommendation(
            service=service,
            instance_type="",
            region=records[0].region if records else "",
            current_monthly_cost=round(baseline_monthly, 2),
            reserved_monthly_cost=round(reserved_monthly, 2),
            monthly_savings=round(monthly_savings, 2),
            annual_savings=round(annual_savings, 2),
            savings_percent=round(discount_rate * 100, 1),
            commitment_term=term_label,
            payment_option=self.preferred_term.replace("_", " "),
            break_even_months=break_even,
            confidence=confidence,
            reasoning=(
                f"Service shows {consistency:.0%} usage consistency over "
                f"{len(records)} days with baseline spend of ${baseline_daily:.2f}/day. "
                f"A {term_label} commitment could save ~${annual_savings:.2f}/year."
            ),
        )

    def summarize(self, recommendations: list[ReservationRecommendation]) -> dict[str, Any]:
        """Summarize reservation recommendations."""
        if not recommendations:
            return {"total_recommendations": 0}

        return {
            "total_recommendations": len(recommendations),
            "total_monthly_savings": round(sum(r.monthly_savings for r in recommendations), 2),
            "total_annual_savings": round(sum(r.annual_savings for r in recommendations), 2),
            "high_confidence_count": sum(1 for r in recommendations if r.confidence == "high"),
            "services": [r.service for r in recommendations],
        }
