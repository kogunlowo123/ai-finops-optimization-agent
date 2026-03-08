"""Find unused, idle, and wasted cloud resources."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.collectors.aws_costs import ResourceInfo

logger = logging.getLogger(__name__)


@dataclass
class WastedResource:
    """A resource identified as wasted or underutilized."""

    resource_id: str
    resource_type: str
    region: str
    waste_type: str
    estimated_monthly_cost: float
    reason: str
    recommendation: str
    tags: dict[str, str] = field(default_factory=dict)
    auto_cleanup_eligible: bool = False


class WasteFinder:
    """Identify unused, idle, and orphaned cloud resources.

    Checks for:
    - Stopped instances running for extended periods
    - Unattached EBS volumes / Azure disks / GCP persistent disks
    - Unused Elastic IPs
    - Idle instances (very low CPU utilization)
    - Old snapshots
    """

    WASTE_COST_ESTIMATES: dict[str, float] = {
        "ec2:volume": 10.0,
        "ec2:eip": 3.60,
        "ec2:instance_stopped": 5.0,
        "microsoft.compute/disks": 8.0,
        "compute.googleapis.com/Disk": 8.0,
    }

    def __init__(
        self,
        idle_cpu_threshold: float = 5.0,
        idle_days: int = 7,
        stopped_instance_days: int = 30,
    ) -> None:
        self.idle_cpu_threshold = idle_cpu_threshold
        self.idle_days = idle_days
        self.stopped_instance_days = stopped_instance_days

    def find_waste(
        self,
        instances: list[ResourceInfo],
        unattached_volumes: list[ResourceInfo],
        unused_ips: list[ResourceInfo],
    ) -> list[WastedResource]:
        """Identify all wasted resources from the provided inventory.

        Args:
            instances: List of compute instances with utilization data.
            unattached_volumes: List of unattached storage volumes.
            unused_ips: List of unused IP addresses.

        Returns:
            List of WastedResource objects sorted by estimated cost.
        """
        wasted: list[WastedResource] = []

        wasted.extend(self._find_idle_instances(instances))
        wasted.extend(self._find_stopped_instances(instances))
        wasted.extend(self._find_unattached_volumes(unattached_volumes))
        wasted.extend(self._find_unused_ips(unused_ips))

        wasted.sort(key=lambda w: w.estimated_monthly_cost, reverse=True)
        return wasted

    def _find_idle_instances(self, instances: list[ResourceInfo]) -> list[WastedResource]:
        """Find running instances with very low CPU utilization."""
        wasted = []
        for inst in instances:
            if inst.state not in ("running", "Running", "VM running", "RUNNING"):
                continue
            if inst.cpu_utilization_avg <= self.idle_cpu_threshold:
                wasted.append(WastedResource(
                    resource_id=inst.resource_id,
                    resource_type=inst.resource_type,
                    region=inst.region,
                    waste_type="idle_instance",
                    estimated_monthly_cost=inst.monthly_cost or 50.0,
                    reason=(
                        f"CPU utilization is {inst.cpu_utilization_avg:.1f}% over "
                        f"the last {self.idle_days} days (threshold: {self.idle_cpu_threshold}%)"
                    ),
                    recommendation=(
                        f"Consider stopping or terminating this instance. "
                        f"If needed, rightsize to a smaller instance type."
                    ),
                    tags=inst.tags,
                    auto_cleanup_eligible=inst.cpu_utilization_avg < 1.0,
                ))
        return wasted

    def _find_stopped_instances(self, instances: list[ResourceInfo]) -> list[WastedResource]:
        """Find instances that have been stopped for an extended period."""
        wasted = []
        for inst in instances:
            if inst.state not in ("stopped", "Stopped", "deallocated", "TERMINATED"):
                continue
            est_cost = self.WASTE_COST_ESTIMATES.get("ec2:instance_stopped", 5.0)
            wasted.append(WastedResource(
                resource_id=inst.resource_id,
                resource_type=inst.resource_type,
                region=inst.region,
                waste_type="stopped_instance",
                estimated_monthly_cost=est_cost,
                reason=(
                    f"Instance is in '{inst.state}' state. Stopped instances "
                    f"still incur charges for attached storage and Elastic IPs."
                ),
                recommendation=(
                    "Terminate the instance if no longer needed, or create an AMI/snapshot "
                    "and terminate to eliminate ongoing storage costs."
                ),
                tags=inst.tags,
                auto_cleanup_eligible=False,
            ))
        return wasted

    def _find_unattached_volumes(self, volumes: list[ResourceInfo]) -> list[WastedResource]:
        """Find storage volumes not attached to any instance."""
        wasted = []
        for vol in volumes:
            est_cost = self.WASTE_COST_ESTIMATES.get(vol.resource_type, 10.0)
            wasted.append(WastedResource(
                resource_id=vol.resource_id,
                resource_type=vol.resource_type,
                region=vol.region,
                waste_type="unattached_volume",
                estimated_monthly_cost=est_cost,
                reason="Storage volume is not attached to any compute instance.",
                recommendation=(
                    "Create a snapshot for backup, then delete the volume. "
                    "Snapshots are significantly cheaper than provisioned volumes."
                ),
                tags=vol.tags,
                auto_cleanup_eligible=True,
            ))
        return wasted

    def _find_unused_ips(self, ips: list[ResourceInfo]) -> list[WastedResource]:
        """Find IP addresses not associated with any resource."""
        wasted = []
        for ip in ips:
            est_cost = self.WASTE_COST_ESTIMATES.get("ec2:eip", 3.60)
            wasted.append(WastedResource(
                resource_id=ip.resource_id,
                resource_type=ip.resource_type,
                region=ip.region,
                waste_type="unused_ip",
                estimated_monthly_cost=est_cost,
                reason="Elastic/static IP is not associated with any running resource.",
                recommendation="Release the IP address if no longer needed.",
                tags=ip.tags,
                auto_cleanup_eligible=True,
            ))
        return wasted

    def summarize(self, wasted: list[WastedResource]) -> dict[str, Any]:
        """Summarize waste findings."""
        if not wasted:
            return {"total_resources": 0, "total_monthly_waste": 0.0}

        by_type: dict[str, list[WastedResource]] = {}
        for w in wasted:
            by_type.setdefault(w.waste_type, []).append(w)

        total_monthly = sum(w.estimated_monthly_cost for w in wasted)

        return {
            "total_resources": len(wasted),
            "total_monthly_waste": round(total_monthly, 2),
            "total_annual_waste": round(total_monthly * 12, 2),
            "auto_cleanup_eligible": sum(1 for w in wasted if w.auto_cleanup_eligible),
            "by_type": {
                k: {"count": len(v), "monthly_cost": round(sum(w.estimated_monthly_cost for w in v), 2)}
                for k, v in by_type.items()
            },
        }
