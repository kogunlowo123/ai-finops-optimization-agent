"""AWS Cost Explorer and resource inventory collector using boto3."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional

import boto3
from botocore.config import Config as BotoConfig

from src.config import AWSConfig

logger = logging.getLogger(__name__)


@dataclass
class CostRecord:
    """A single cost data point."""

    date: str
    service: str
    amount: float
    currency: str = "USD"
    account_id: str = ""
    region: str = ""
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class ResourceInfo:
    """Information about a cloud resource for rightsizing analysis."""

    resource_id: str
    resource_type: str
    region: str
    instance_type: str = ""
    state: str = ""
    cpu_utilization_avg: float = 0.0
    memory_utilization_avg: float = 0.0
    monthly_cost: float = 0.0
    tags: dict[str, str] = field(default_factory=dict)
    launch_time: Optional[datetime] = None


class AWSCostCollector:
    """Collect cost data and resource inventory from AWS.

    Uses AWS Cost Explorer API for cost/usage data and EC2/CloudWatch APIs
    for resource utilization metrics.

    Reference:
        https://docs.aws.amazon.com/cost-management/latest/userguide/ce-api.html
    """

    def __init__(self, config: Optional[AWSConfig] = None) -> None:
        self.config = config or AWSConfig()
        boto_config = BotoConfig(region_name=self.config.region)

        session_kwargs: dict[str, Any] = {}
        if self.config.profile_name:
            session_kwargs["profile_name"] = self.config.profile_name
        if self.config.access_key_id:
            session_kwargs["aws_access_key_id"] = self.config.access_key_id
            session_kwargs["aws_secret_access_key"] = self.config.secret_access_key

        session = boto3.Session(**session_kwargs)

        if self.config.role_arn:
            sts = session.client("sts", config=boto_config)
            creds = sts.assume_role(
                RoleArn=self.config.role_arn,
                RoleSessionName="finops-agent",
            )["Credentials"]
            session = boto3.Session(
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
            )

        self.ce_client = session.client("ce", config=boto_config)
        self.ec2_client = session.client("ec2", config=boto_config)
        self.cw_client = session.client("cloudwatch", config=boto_config)

    def get_cost_and_usage(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        granularity: str = "DAILY",
        group_by: Optional[list[str]] = None,
    ) -> list[CostRecord]:
        """Retrieve cost and usage data from AWS Cost Explorer.

        Args:
            start_date: Start of the time range. Defaults to 30 days ago.
            end_date: End of the time range. Defaults to today.
            granularity: DAILY, MONTHLY, or HOURLY.
            group_by: Fields to group by (e.g., ['SERVICE', 'REGION']).

        Returns:
            List of CostRecord objects.
        """
        if start_date is None:
            start_date = date.today() - timedelta(days=30)
        if end_date is None:
            end_date = date.today()

        request_params: dict[str, Any] = {
            "TimePeriod": {
                "Start": start_date.isoformat(),
                "End": end_date.isoformat(),
            },
            "Granularity": granularity,
            "Metrics": ["UnblendedCost", "UsageQuantity"],
        }

        if group_by:
            request_params["GroupBy"] = [
                {"Type": "DIMENSION", "Key": key} for key in group_by
            ]

        records: list[CostRecord] = []
        next_token: Optional[str] = None

        while True:
            if next_token:
                request_params["NextPageToken"] = next_token

            response = self.ce_client.get_cost_and_usage(**request_params)

            for result in response.get("ResultsByTime", []):
                period_start = result["TimePeriod"]["Start"]

                if "Groups" in result:
                    for group in result["Groups"]:
                        keys = group.get("Keys", [])
                        metrics = group.get("Metrics", {})
                        amount = float(metrics.get("UnblendedCost", {}).get("Amount", 0))
                        currency = metrics.get("UnblendedCost", {}).get("Unit", "USD")

                        records.append(CostRecord(
                            date=period_start,
                            service=keys[0] if keys else "Unknown",
                            amount=amount,
                            currency=currency,
                            region=keys[1] if len(keys) > 1 else "",
                        ))
                else:
                    metrics = result.get("Total", {})
                    amount = float(metrics.get("UnblendedCost", {}).get("Amount", 0))
                    records.append(CostRecord(
                        date=period_start,
                        service="Total",
                        amount=amount,
                    ))

            next_token = response.get("NextPageToken")
            if not next_token:
                break

        return records

    def get_cost_forecast(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        granularity: str = "MONTHLY",
    ) -> dict[str, Any]:
        """Get cost forecast from AWS Cost Explorer.

        Args:
            start_date: Forecast start date. Defaults to today.
            end_date: Forecast end date. Defaults to end of current month.
            granularity: DAILY or MONTHLY.

        Returns:
            Dictionary with forecast data including total and per-period amounts.
        """
        if start_date is None:
            start_date = date.today()
        if end_date is None:
            if start_date.month == 12:
                end_date = date(start_date.year + 1, 1, 1)
            else:
                end_date = date(start_date.year, start_date.month + 1, 1)

        response = self.ce_client.get_cost_forecast(
            TimePeriod={
                "Start": start_date.isoformat(),
                "End": end_date.isoformat(),
            },
            Metric="UNBLENDED_COST",
            Granularity=granularity,
        )

        return {
            "total_forecast": float(response.get("Total", {}).get("Amount", 0)),
            "currency": response.get("Total", {}).get("Unit", "USD"),
            "periods": [
                {
                    "start": p["TimePeriod"]["Start"],
                    "end": p["TimePeriod"]["End"],
                    "amount": float(p["MeanValue"]),
                }
                for p in response.get("ForecastResultsByTime", [])
            ],
        }

    def list_ec2_instances(self, include_stopped: bool = True) -> list[ResourceInfo]:
        """List all EC2 instances with their details.

        Args:
            include_stopped: Whether to include stopped instances.

        Returns:
            List of ResourceInfo objects.
        """
        filters = []
        if not include_stopped:
            filters.append({"Name": "instance-state-name", "Values": ["running"]})

        paginator = self.ec2_client.get_paginator("describe_instances")
        page_kwargs: dict[str, Any] = {}
        if filters:
            page_kwargs["Filters"] = filters

        resources: list[ResourceInfo] = []
        for page in paginator.paginate(**page_kwargs):
            for reservation in page.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    tags = {
                        t["Key"]: t["Value"]
                        for t in instance.get("Tags", [])
                    }
                    launch_time = instance.get("LaunchTime")

                    resources.append(ResourceInfo(
                        resource_id=instance["InstanceId"],
                        resource_type="ec2:instance",
                        region=self.config.region,
                        instance_type=instance.get("InstanceType", ""),
                        state=instance.get("State", {}).get("Name", ""),
                        tags=tags,
                        launch_time=launch_time,
                    ))

        return resources

    def get_instance_cpu_utilization(
        self,
        instance_id: str,
        days: int = 7,
    ) -> float:
        """Get average CPU utilization for an EC2 instance over a time period.

        Args:
            instance_id: EC2 instance ID.
            days: Number of days to average over.

        Returns:
            Average CPU utilization percentage.
        """
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=days)

        response = self.cw_client.get_metric_statistics(
            Namespace="AWS/EC2",
            MetricName="CPUUtilization",
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=start_time,
            EndTime=end_time,
            Period=3600,
            Statistics=["Average"],
        )

        datapoints = response.get("Datapoints", [])
        if not datapoints:
            return 0.0

        return sum(dp["Average"] for dp in datapoints) / len(datapoints)

    def get_unattached_ebs_volumes(self) -> list[ResourceInfo]:
        """Find EBS volumes that are not attached to any instance.

        Returns:
            List of ResourceInfo for unattached volumes.
        """
        paginator = self.ec2_client.get_paginator("describe_volumes")
        resources: list[ResourceInfo] = []

        for page in paginator.paginate(
            Filters=[{"Name": "status", "Values": ["available"]}]
        ):
            for vol in page.get("Volumes", []):
                tags = {t["Key"]: t["Value"] for t in vol.get("Tags", [])}
                resources.append(ResourceInfo(
                    resource_id=vol["VolumeId"],
                    resource_type="ec2:volume",
                    region=self.config.region,
                    state="available",
                    tags=tags,
                ))

        return resources

    def get_unused_elastic_ips(self) -> list[ResourceInfo]:
        """Find Elastic IPs not associated with any instance.

        Returns:
            List of ResourceInfo for unused Elastic IPs.
        """
        response = self.ec2_client.describe_addresses()
        resources: list[ResourceInfo] = []

        for addr in response.get("Addresses", []):
            if not addr.get("InstanceId") and not addr.get("NetworkInterfaceId"):
                tags = {t["Key"]: t["Value"] for t in addr.get("Tags", [])}
                resources.append(ResourceInfo(
                    resource_id=addr.get("AllocationId", addr.get("PublicIp", "")),
                    resource_type="ec2:eip",
                    region=self.config.region,
                    state="unassociated",
                    tags=tags,
                ))

        return resources
