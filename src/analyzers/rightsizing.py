"""Instance rightsizing recommendation engine."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from src.collectors.aws_costs import ResourceInfo

logger = logging.getLogger(__name__)


# Simplified EC2 instance family pricing and specs (relative)
EC2_INSTANCE_SPECS: dict[str, dict[str, Any]] = {
    "t3.micro": {"vcpu": 2, "memory_gb": 1, "hourly_cost": 0.0104},
    "t3.small": {"vcpu": 2, "memory_gb": 2, "hourly_cost": 0.0208},
    "t3.medium": {"vcpu": 2, "memory_gb": 4, "hourly_cost": 0.0416},
    "t3.large": {"vcpu": 2, "memory_gb": 8, "hourly_cost": 0.0832},
    "t3.xlarge": {"vcpu": 4, "memory_gb": 16, "hourly_cost": 0.1664},
    "t3.2xlarge": {"vcpu": 8, "memory_gb": 32, "hourly_cost": 0.3328},
    "m5.large": {"vcpu": 2, "memory_gb": 8, "hourly_cost": 0.096},
    "m5.xlarge": {"vcpu": 4, "memory_gb": 16, "hourly_cost": 0.192},
    "m5.2xlarge": {"vcpu": 8, "memory_gb": 32, "hourly_cost": 0.384},
    "m5.4xlarge": {"vcpu": 16, "memory_gb": 64, "hourly_cost": 0.768},
    "c5.large": {"vcpu": 2, "memory_gb": 4, "hourly_cost": 0.085},
    "c5.xlarge": {"vcpu": 4, "memory_gb": 8, "hourly_cost": 0.17},
    "c5.2xlarge": {"vcpu": 8, "memory_gb": 16, "hourly_cost": 0.34},
    "r5.large": {"vcpu": 2, "memory_gb": 16, "hourly_cost": 0.126},
    "r5.xlarge": {"vcpu": 4, "memory_gb": 32, "hourly_cost": 0.252},
    "r5.2xlarge": {"vcpu": 8, "memory_gb": 64, "hourly_cost": 0.504},
}

HOURS_PER_MONTH = 730


@dataclass
class RightsizingRecommendation:
    """A single rightsizing recommendation."""

    resource_id: str
    current_type: str
    recommended_type: str
    current_monthly_cost: float
    recommended_monthly_cost: float
    monthly_savings: float
    savings_percent: float
    cpu_utilization: float
    reason: str
    confidence: str


class RightsizingAnalyzer:
    """Analyze resource utilization and recommend rightsizing changes.

    Compares current instance sizes against actual CPU utilization to
    identify instances that can be downsized or upsized.
    """

    def __init__(
        self,
        headroom_percent: float = 20.0,
        cpu_threshold_low: float = 10.0,
        cpu_threshold_high: float = 80.0,
    ) -> None:
        self.headroom_percent = headroom_percent
        self.cpu_threshold_low = cpu_threshold_low
        self.cpu_threshold_high = cpu_threshold_high

    def analyze(self, resources: list[ResourceInfo]) -> list[RightsizingRecommendation]:
        """Analyze resources and generate rightsizing recommendations.

        Args:
            resources: List of resources with utilization data populated.

        Returns:
            List of recommendations sorted by potential savings.
        """
        recommendations: list[RightsizingRecommendation] = []

        for resource in resources:
            if resource.resource_type not in ("ec2:instance", "microsoft.compute/virtualmachines"):
                continue
            if resource.state not in ("running", "Running", "VM running"):
                continue

            rec = self._evaluate_instance(resource)
            if rec:
                recommendations.append(rec)

        recommendations.sort(key=lambda r: r.monthly_savings, reverse=True)
        return recommendations

    def _evaluate_instance(self, resource: ResourceInfo) -> Optional[RightsizingRecommendation]:
        """Evaluate a single instance for rightsizing."""
        current_specs = EC2_INSTANCE_SPECS.get(resource.instance_type)
        if not current_specs:
            return None

        current_monthly = current_specs["hourly_cost"] * HOURS_PER_MONTH
        cpu_util = resource.cpu_utilization_avg

        if cpu_util < self.cpu_threshold_low:
            target_type = self._find_smaller_instance(
                resource.instance_type, cpu_util
            )
            if target_type and target_type != resource.instance_type:
                target_specs = EC2_INSTANCE_SPECS[target_type]
                target_monthly = target_specs["hourly_cost"] * HOURS_PER_MONTH
                savings = current_monthly - target_monthly

                return RightsizingRecommendation(
                    resource_id=resource.resource_id,
                    current_type=resource.instance_type,
                    recommended_type=target_type,
                    current_monthly_cost=round(current_monthly, 2),
                    recommended_monthly_cost=round(target_monthly, 2),
                    monthly_savings=round(savings, 2),
                    savings_percent=round((savings / current_monthly) * 100, 1),
                    cpu_utilization=cpu_util,
                    reason=f"CPU utilization is {cpu_util:.1f}% (below {self.cpu_threshold_low}% threshold)",
                    confidence="high" if cpu_util < 5 else "medium",
                )

        elif cpu_util > self.cpu_threshold_high:
            target_type = self._find_larger_instance(resource.instance_type)
            if target_type and target_type != resource.instance_type:
                target_specs = EC2_INSTANCE_SPECS[target_type]
                target_monthly = target_specs["hourly_cost"] * HOURS_PER_MONTH
                cost_increase = target_monthly - current_monthly

                return RightsizingRecommendation(
                    resource_id=resource.resource_id,
                    current_type=resource.instance_type,
                    recommended_type=target_type,
                    current_monthly_cost=round(current_monthly, 2),
                    recommended_monthly_cost=round(target_monthly, 2),
                    monthly_savings=round(-cost_increase, 2),
                    savings_percent=round((-cost_increase / current_monthly) * 100, 1),
                    cpu_utilization=cpu_util,
                    reason=f"CPU utilization is {cpu_util:.1f}% (above {self.cpu_threshold_high}% threshold). Upsize to prevent performance issues.",
                    confidence="medium",
                )

        return None

    def _find_smaller_instance(self, current_type: str, cpu_util: float) -> Optional[str]:
        """Find the next smaller instance in the same family."""
        family = current_type.rsplit(".", 1)[0] if "." in current_type else ""
        same_family = {
            k: v for k, v in EC2_INSTANCE_SPECS.items()
            if k.startswith(family + ".")
        }

        current_specs = EC2_INSTANCE_SPECS.get(current_type)
        if not current_specs:
            return None

        sorted_types = sorted(
            same_family.items(),
            key=lambda x: x[1]["hourly_cost"],
        )

        for inst_type, specs in sorted_types:
            if specs["hourly_cost"] < current_specs["hourly_cost"]:
                needed_cpu_headroom = cpu_util * (1 + self.headroom_percent / 100)
                available_capacity = (specs["vcpu"] / current_specs["vcpu"]) * 100
                if available_capacity >= needed_cpu_headroom:
                    return inst_type

        if sorted_types and sorted_types[0][0] != current_type:
            return sorted_types[0][0]

        return None

    def _find_larger_instance(self, current_type: str) -> Optional[str]:
        """Find the next larger instance in the same family."""
        family = current_type.rsplit(".", 1)[0] if "." in current_type else ""
        same_family = {
            k: v for k, v in EC2_INSTANCE_SPECS.items()
            if k.startswith(family + ".")
        }

        current_specs = EC2_INSTANCE_SPECS.get(current_type)
        if not current_specs:
            return None

        sorted_types = sorted(
            same_family.items(),
            key=lambda x: x[1]["hourly_cost"],
        )

        for inst_type, specs in sorted_types:
            if specs["hourly_cost"] > current_specs["hourly_cost"]:
                return inst_type

        return None

    def summarize(self, recommendations: list[RightsizingRecommendation]) -> dict[str, Any]:
        """Summarize rightsizing recommendations."""
        if not recommendations:
            return {"total_recommendations": 0, "total_monthly_savings": 0.0}

        downsize = [r for r in recommendations if r.monthly_savings > 0]
        upsize = [r for r in recommendations if r.monthly_savings < 0]

        return {
            "total_recommendations": len(recommendations),
            "downsize_count": len(downsize),
            "upsize_count": len(upsize),
            "total_monthly_savings": round(sum(r.monthly_savings for r in downsize), 2),
            "total_annual_savings": round(sum(r.monthly_savings for r in downsize) * 12, 2),
            "top_savings": [
                {
                    "resource_id": r.resource_id,
                    "current": r.current_type,
                    "recommended": r.recommended_type,
                    "monthly_savings": r.monthly_savings,
                }
                for r in downsize[:5]
            ],
        }
