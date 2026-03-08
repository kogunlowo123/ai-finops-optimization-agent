"""Start/stop scheduler for non-production resources."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Any, Optional

import boto3

from src.config import AWSConfig

logger = logging.getLogger(__name__)


@dataclass
class ScheduleRule:
    """A start/stop schedule rule for resources."""

    name: str
    tag_key: str
    tag_value: str
    start_time: time
    stop_time: time
    timezone_name: str = "UTC"
    days_of_week: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])
    enabled: bool = True


@dataclass
class ScheduleAction:
    """Result of a scheduling action."""

    resource_id: str
    action: str
    success: bool
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ResourceScheduler:
    """Start and stop cloud resources on a schedule to reduce costs.

    Identifies resources tagged with schedule rules and starts/stops them
    according to the configured business hours.
    """

    def __init__(self, aws_config: Optional[AWSConfig] = None) -> None:
        self.aws_config = aws_config or AWSConfig()
        session_kwargs: dict[str, Any] = {}
        if self.aws_config.profile_name:
            session_kwargs["profile_name"] = self.aws_config.profile_name
        if self.aws_config.access_key_id:
            session_kwargs["aws_access_key_id"] = self.aws_config.access_key_id
            session_kwargs["aws_secret_access_key"] = self.aws_config.secret_access_key

        session = boto3.Session(**session_kwargs)
        self.ec2_client = session.client("ec2", region_name=self.aws_config.region)

    def apply_schedule(self, rule: ScheduleRule) -> list[ScheduleAction]:
        """Apply a schedule rule: stop or start instances based on current time.

        Args:
            rule: The schedule rule to apply.

        Returns:
            List of actions taken.
        """
        if not rule.enabled:
            return []

        now = datetime.now(timezone.utc)
        current_day = now.weekday()

        if current_day not in rule.days_of_week:
            logger.info("Schedule '%s' not active on day %d", rule.name, current_day)
            return []

        current_time = now.time()
        should_be_running = rule.start_time <= current_time < rule.stop_time

        instances = self._find_tagged_instances(rule.tag_key, rule.tag_value)
        actions: list[ScheduleAction] = []

        for instance_id, state in instances:
            if should_be_running and state == "stopped":
                action = self._start_instance(instance_id)
                actions.append(action)
            elif not should_be_running and state == "running":
                action = self._stop_instance(instance_id)
                actions.append(action)

        logger.info(
            "Schedule '%s': %d actions taken (%s)",
            rule.name, len(actions),
            "running hours" if should_be_running else "off hours",
        )
        return actions

    def _find_tagged_instances(
        self, tag_key: str, tag_value: str
    ) -> list[tuple[str, str]]:
        """Find EC2 instances matching a specific tag."""
        response = self.ec2_client.describe_instances(
            Filters=[
                {"Name": f"tag:{tag_key}", "Values": [tag_value]},
                {"Name": "instance-state-name", "Values": ["running", "stopped"]},
            ]
        )

        instances = []
        for reservation in response.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                instance_id = instance["InstanceId"]
                state = instance["State"]["Name"]
                instances.append((instance_id, state))

        return instances

    def _start_instance(self, instance_id: str) -> ScheduleAction:
        """Start an EC2 instance."""
        try:
            self.ec2_client.start_instances(InstanceIds=[instance_id])
            logger.info("Started instance %s", instance_id)
            return ScheduleAction(
                resource_id=instance_id,
                action="start",
                success=True,
                message=f"Instance {instance_id} started successfully",
            )
        except Exception as e:
            logger.error("Failed to start instance %s: %s", instance_id, e)
            return ScheduleAction(
                resource_id=instance_id,
                action="start",
                success=False,
                message=f"Failed to start: {e}",
            )

    def _stop_instance(self, instance_id: str) -> ScheduleAction:
        """Stop an EC2 instance."""
        try:
            self.ec2_client.stop_instances(InstanceIds=[instance_id])
            logger.info("Stopped instance %s", instance_id)
            return ScheduleAction(
                resource_id=instance_id,
                action="stop",
                success=True,
                message=f"Instance {instance_id} stopped successfully",
            )
        except Exception as e:
            logger.error("Failed to stop instance %s: %s", instance_id, e)
            return ScheduleAction(
                resource_id=instance_id,
                action="stop",
                success=False,
                message=f"Failed to stop: {e}",
            )
