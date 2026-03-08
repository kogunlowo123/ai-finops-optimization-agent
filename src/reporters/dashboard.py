"""Cost dashboard data generator."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from src.collectors.aws_costs import CostRecord

logger = logging.getLogger(__name__)


@dataclass
class DashboardData:
    """Structured data for a cost dashboard."""

    generated_date: str
    total_cost: float
    cost_by_service: dict[str, float]
    cost_by_day: dict[str, float]
    top_services: list[dict[str, Any]]
    cost_trend: str
    period_start: str
    period_end: str
    currency: str = "USD"
    forecast: dict[str, Any] = field(default_factory=dict)
    anomalies: list[dict[str, Any]] = field(default_factory=list)
    savings_opportunities: dict[str, Any] = field(default_factory=dict)


class DashboardGenerator:
    """Generate structured dashboard data from cost records and analysis results.

    Produces JSON-serializable data suitable for rendering in any
    dashboarding tool (Grafana, Metabase, custom web UI, etc.).
    """

    def __init__(self, currency: str = "USD") -> None:
        self.currency = currency

    def generate(
        self,
        cost_records: list[CostRecord],
        forecast: dict[str, Any] | None = None,
        anomalies: list[dict[str, Any]] | None = None,
        savings: dict[str, Any] | None = None,
    ) -> DashboardData:
        """Generate dashboard data from cost records and analysis.

        Args:
            cost_records: Daily cost records.
            forecast: Cost forecast data.
            anomalies: Detected anomalies.
            savings: Savings opportunity summary.

        Returns:
            DashboardData ready for rendering.
        """
        cost_by_service: dict[str, float] = defaultdict(float)
        cost_by_day: dict[str, float] = defaultdict(float)

        for record in cost_records:
            cost_by_service[record.service] += record.amount
            cost_by_day[record.date] += record.amount

        total_cost = sum(cost_by_service.values())

        top_services = sorted(
            [
                {"service": svc, "cost": round(cost, 2), "percent": round((cost / total_cost) * 100, 1) if total_cost > 0 else 0}
                for svc, cost in cost_by_service.items()
            ],
            key=lambda x: x["cost"],
            reverse=True,
        )[:10]

        sorted_days = sorted(cost_by_day.keys())
        trend = self._calculate_trend(cost_by_day, sorted_days)

        return DashboardData(
            generated_date=date.today().isoformat(),
            total_cost=round(total_cost, 2),
            cost_by_service=dict(cost_by_service),
            cost_by_day=dict(cost_by_day),
            top_services=top_services,
            cost_trend=trend,
            period_start=sorted_days[0] if sorted_days else "",
            period_end=sorted_days[-1] if sorted_days else "",
            currency=self.currency,
            forecast=forecast or {},
            anomalies=anomalies or [],
            savings_opportunities=savings or {},
        )

    def to_json(self, data: DashboardData) -> str:
        """Serialize dashboard data to JSON.

        Args:
            data: DashboardData to serialize.

        Returns:
            JSON string.
        """
        return json.dumps({
            "generated_date": data.generated_date,
            "total_cost": data.total_cost,
            "currency": data.currency,
            "period": {"start": data.period_start, "end": data.period_end},
            "cost_trend": data.cost_trend,
            "top_services": data.top_services,
            "cost_by_day": data.cost_by_day,
            "forecast": data.forecast,
            "anomalies": data.anomalies,
            "savings_opportunities": data.savings_opportunities,
        }, indent=2)

    def _calculate_trend(
        self,
        cost_by_day: dict[str, float],
        sorted_days: list[str],
    ) -> str:
        """Calculate cost trend direction."""
        if len(sorted_days) < 7:
            return "insufficient_data"

        midpoint = len(sorted_days) // 2
        first_half = [cost_by_day[d] for d in sorted_days[:midpoint]]
        second_half = [cost_by_day[d] for d in sorted_days[midpoint:]]

        avg_first = sum(first_half) / len(first_half) if first_half else 0
        avg_second = sum(second_half) / len(second_half) if second_half else 0

        if avg_first == 0:
            return "flat"

        change_pct = ((avg_second - avg_first) / avg_first) * 100

        if change_pct > 10:
            return "increasing"
        elif change_pct < -10:
            return "decreasing"
        return "stable"
