"""GCP Billing and Compute Engine cost collector."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Optional

from google.cloud import billing_v1, compute_v1, monitoring_v2
from google.protobuf import timestamp_pb2

from src.collectors.aws_costs import CostRecord, ResourceInfo
from src.config import GCPConfig

logger = logging.getLogger(__name__)


class GCPCostCollector:
    """Collect cost data and resource inventory from Google Cloud Platform.

    Uses Cloud Billing API for cost data and Compute Engine API for
    resource inventory and utilization metrics.

    Reference:
        https://cloud.google.com/billing/docs/reference/rest
    """

    def __init__(self, config: Optional[GCPConfig] = None) -> None:
        self.config = config or GCPConfig()
        self.billing_client = billing_v1.CloudBillingClient()
        self.compute_client = compute_v1.InstancesClient()
        self.disks_client = compute_v1.DisksClient()
        self.monitoring_client = monitoring_v2.MetricServiceClient()

    def get_cost_and_usage(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        group_by_service: bool = True,
    ) -> list[CostRecord]:
        """Query GCP billing data using BigQuery billing export.

        This method queries the BigQuery billing export table, which must
        be configured in the GCP billing account settings.

        Args:
            start_date: Start of the time range. Defaults to 30 days ago.
            end_date: End of the time range. Defaults to today.
            group_by_service: Whether to group costs by service.

        Returns:
            List of CostRecord objects.
        """
        if start_date is None:
            start_date = date.today() - timedelta(days=30)
        if end_date is None:
            end_date = date.today()

        from google.cloud import bigquery

        bq_client = bigquery.Client(project=self.config.project_id)

        group_clause = ""
        select_service = "'Total' AS service"
        if group_by_service:
            select_service = "service.description AS service"
            group_clause = "GROUP BY usage_date, service"

        query = f"""
            SELECT
                DATE(usage_start_time) AS usage_date,
                {select_service},
                SUM(cost) AS total_cost,
                currency
            FROM `{self.config.project_id}.billing_export.gcp_billing_export_v1_{self.config.billing_account_id.replace('-', '_')}`
            WHERE DATE(usage_start_time) BETWEEN @start_date AND @end_date
            {group_clause}
            ORDER BY usage_date
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start_date", "DATE", start_date.isoformat()),
                bigquery.ScalarQueryParameter("end_date", "DATE", end_date.isoformat()),
            ]
        )

        results = bq_client.query(query, job_config=job_config)
        records: list[CostRecord] = []

        for row in results:
            records.append(CostRecord(
                date=str(row.get("usage_date", "")),
                service=row.get("service", "Unknown"),
                amount=float(row.get("total_cost", 0)),
                currency=row.get("currency", "USD"),
            ))

        return records

    def list_compute_instances(
        self,
        zones: Optional[list[str]] = None,
    ) -> list[ResourceInfo]:
        """List Compute Engine instances.

        Args:
            zones: Specific zones to query. Queries all zones if None.

        Returns:
            List of ResourceInfo objects.
        """
        resources: list[ResourceInfo] = []

        if zones:
            for zone in zones:
                request = compute_v1.ListInstancesRequest(
                    project=self.config.project_id,
                    zone=zone,
                )
                for instance in self.compute_client.list(request=request):
                    resources.append(self._instance_to_resource(instance, zone))
        else:
            request = compute_v1.AggregatedListInstancesRequest(
                project=self.config.project_id,
            )
            for zone, instances_scoped_list in self.compute_client.aggregated_list(request=request):
                if instances_scoped_list.instances:
                    zone_name = zone.split("/")[-1] if "/" in zone else zone
                    for instance in instances_scoped_list.instances:
                        resources.append(self._instance_to_resource(instance, zone_name))

        return resources

    def get_instance_cpu_utilization(
        self,
        instance_name: str,
        zone: str,
        days: int = 7,
    ) -> float:
        """Get average CPU utilization for a Compute Engine instance.

        Args:
            instance_name: Name of the instance.
            zone: Zone where the instance is located.
            days: Number of days to average over.

        Returns:
            Average CPU utilization percentage.
        """
        from datetime import datetime, timezone
        from google.cloud.monitoring_v3 import (
            Aggregation,
            ListTimeSeriesRequest,
            TimeInterval,
        )

        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)

        start_ts = timestamp_pb2.Timestamp()
        start_ts.FromDatetime(start)
        end_ts = timestamp_pb2.Timestamp()
        end_ts.FromDatetime(now)

        interval = TimeInterval(start_time=start_ts, end_time=end_ts)
        aggregation = Aggregation(
            alignment_period={"seconds": 3600},
            per_series_aligner=Aggregation.Aligner.ALIGN_MEAN,
        )

        request = ListTimeSeriesRequest(
            name=f"projects/{self.config.project_id}",
            filter=(
                f'metric.type = "compute.googleapis.com/instance/cpu/utilization" '
                f'AND resource.labels.instance_id = "{instance_name}" '
                f'AND resource.labels.zone = "{zone}"'
            ),
            interval=interval,
            aggregation=aggregation,
            view=ListTimeSeriesRequest.TimeSeriesView.FULL,
        )

        total = 0.0
        count = 0
        for ts in self.monitoring_client.list_time_series(request=request):
            for point in ts.points:
                total += point.value.double_value * 100
                count += 1

        return total / count if count > 0 else 0.0

    def get_unattached_disks(self) -> list[ResourceInfo]:
        """Find persistent disks not attached to any instance.

        Returns:
            List of ResourceInfo for unattached disks.
        """
        resources: list[ResourceInfo] = []

        request = compute_v1.AggregatedListDisksRequest(
            project=self.config.project_id,
        )

        for zone, disks_scoped_list in self.disks_client.aggregated_list(request=request):
            if disks_scoped_list.disks:
                zone_name = zone.split("/")[-1] if "/" in zone else zone
                for disk in disks_scoped_list.disks:
                    if not disk.users:
                        labels = dict(disk.labels) if disk.labels else {}
                        resources.append(ResourceInfo(
                            resource_id=disk.self_link or disk.name,
                            resource_type="compute.googleapis.com/Disk",
                            region=zone_name,
                            state="unattached",
                            tags=labels,
                        ))

        return resources

    def _instance_to_resource(self, instance: Any, zone: str) -> ResourceInfo:
        """Convert a GCP compute instance to ResourceInfo."""
        machine_type = ""
        if instance.machine_type:
            machine_type = instance.machine_type.split("/")[-1]

        labels = dict(instance.labels) if instance.labels else {}

        return ResourceInfo(
            resource_id=instance.self_link or instance.name,
            resource_type="compute.googleapis.com/Instance",
            region=zone,
            instance_type=machine_type,
            state=instance.status or "UNKNOWN",
            tags=labels,
        )
