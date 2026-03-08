"""Azure Cost Management collector using azure-mgmt-costmanagement."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

from azure.identity import ClientSecretCredential
from azure.mgmt.costmanagement import CostManagementClient
from azure.mgmt.costmanagement.models import (
    ExportType,
    QueryAggregation,
    QueryColumnType,
    QueryDataset,
    QueryDefinition,
    QueryFilter,
    QueryGrouping,
    QueryTimePeriod,
    TimeframeType,
)
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.monitor import MonitorManagementClient

from src.collectors.aws_costs import CostRecord, ResourceInfo
from src.config import AzureConfig

logger = logging.getLogger(__name__)


class AzureCostCollector:
    """Collect cost data and resource inventory from Azure.

    Uses Azure Cost Management API for cost queries and Azure Compute/Monitor
    APIs for resource utilization metrics.

    Reference:
        https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/
    """

    def __init__(self, config: Optional[AzureConfig] = None) -> None:
        self.config = config or AzureConfig()
        self.credential = ClientSecretCredential(
            tenant_id=self.config.tenant_id,
            client_id=self.config.client_id,
            client_secret=self.config.client_secret,
        )
        self.cost_client = CostManagementClient(
            credential=self.credential,
            subscription_id=self.config.subscription_id,
        )
        self.compute_client = ComputeManagementClient(
            credential=self.credential,
            subscription_id=self.config.subscription_id,
        )
        self.monitor_client = MonitorManagementClient(
            credential=self.credential,
            subscription_id=self.config.subscription_id,
        )

    def get_cost_and_usage(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        group_by: Optional[list[str]] = None,
    ) -> list[CostRecord]:
        """Query Azure Cost Management for cost data.

        Args:
            start_date: Start of the time range. Defaults to 30 days ago.
            end_date: End of the time range. Defaults to today.
            group_by: Dimensions to group by (e.g., ['ServiceName', 'ResourceGroup']).

        Returns:
            List of CostRecord objects.
        """
        if start_date is None:
            start_date = date.today() - timedelta(days=30)
        if end_date is None:
            end_date = date.today()

        groupings = []
        if group_by:
            groupings = [
                QueryGrouping(type=QueryColumnType.DIMENSION, name=dim)
                for dim in group_by
            ]

        query_def = QueryDefinition(
            type=ExportType.ACTUAL_COST,
            timeframe=TimeframeType.CUSTOM,
            time_period=QueryTimePeriod(
                from_property=start_date,
                to=end_date,
            ),
            dataset=QueryDataset(
                granularity="Daily",
                aggregation={
                    "totalCost": QueryAggregation(
                        name="Cost",
                        function="Sum",
                    ),
                },
                grouping=groupings if groupings else None,
            ),
        )

        scope = f"/subscriptions/{self.config.subscription_id}"
        result = self.cost_client.query.usage(scope=scope, parameters=query_def)

        records: list[CostRecord] = []
        columns = [col.name for col in result.columns] if result.columns else []

        for row in result.rows or []:
            row_dict = dict(zip(columns, row))
            records.append(CostRecord(
                date=str(row_dict.get("UsageDate", "")),
                service=str(row_dict.get("ServiceName", "Unknown")),
                amount=float(row_dict.get("Cost", 0)),
                currency=str(row_dict.get("Currency", "USD")),
            ))

        return records

    def list_virtual_machines(self) -> list[ResourceInfo]:
        """List all Azure VMs across all resource groups.

        Returns:
            List of ResourceInfo objects for VMs.
        """
        resources: list[ResourceInfo] = []

        for vm in self.compute_client.virtual_machines.list_all():
            location = vm.location or ""
            vm_size = vm.hardware_profile.vm_size if vm.hardware_profile else ""
            tags = vm.tags or {}

            power_state = "unknown"
            if vm.instance_view and vm.instance_view.statuses:
                for status in vm.instance_view.statuses:
                    if status.code and status.code.startswith("PowerState/"):
                        power_state = status.code.split("/")[1]

            resources.append(ResourceInfo(
                resource_id=vm.id or "",
                resource_type="microsoft.compute/virtualmachines",
                region=location,
                instance_type=vm_size,
                state=power_state,
                tags=tags,
            ))

        return resources

    def get_vm_cpu_utilization(
        self,
        resource_id: str,
        days: int = 7,
    ) -> float:
        """Get average CPU utilization for an Azure VM.

        Args:
            resource_id: Full Azure resource ID of the VM.
            days: Number of days to average over.

        Returns:
            Average CPU utilization percentage.
        """
        from datetime import datetime, timezone

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=days)
        timespan = f"{start_time.isoformat()}/{end_time.isoformat()}"

        metrics = self.monitor_client.metrics.list(
            resource_uri=resource_id,
            timespan=timespan,
            interval="PT1H",
            metricnames="Percentage CPU",
            aggregation="Average",
        )

        total = 0.0
        count = 0
        for metric in metrics.value:
            for ts in metric.timeseries:
                for dp in ts.data:
                    if dp.average is not None:
                        total += dp.average
                        count += 1

        return total / count if count > 0 else 0.0

    def get_unattached_disks(self) -> list[ResourceInfo]:
        """Find Azure managed disks not attached to any VM.

        Returns:
            List of ResourceInfo for unattached disks.
        """
        resources: list[ResourceInfo] = []

        for disk in self.compute_client.disks.list():
            if disk.disk_state == "Unattached":
                resources.append(ResourceInfo(
                    resource_id=disk.id or "",
                    resource_type="microsoft.compute/disks",
                    region=disk.location or "",
                    state="unattached",
                    tags=disk.tags or {},
                ))

        return resources
