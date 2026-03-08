"""Resource cleanup executor for removing waste."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import boto3

from src.analyzers.waste_finder import WastedResource
from src.config import AWSConfig

logger = logging.getLogger(__name__)


@dataclass
class CleanupResult:
    """Result of a cleanup action."""

    resource_id: str
    resource_type: str
    action: str
    success: bool
    message: str
    snapshot_id: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ResourceCleanup:
    """Execute cleanup actions for identified wasted resources.

    Supports deleting unattached volumes (with optional snapshot backup),
    releasing unused Elastic IPs, and terminating stopped instances.
    All actions are logged for audit purposes.
    """

    def __init__(
        self,
        aws_config: Optional[AWSConfig] = None,
        dry_run: bool = True,
        snapshot_before_delete: bool = True,
    ) -> None:
        self.dry_run = dry_run
        self.snapshot_before_delete = snapshot_before_delete
        config = aws_config or AWSConfig()

        session_kwargs: dict[str, Any] = {}
        if config.profile_name:
            session_kwargs["profile_name"] = config.profile_name
        if config.access_key_id:
            session_kwargs["aws_access_key_id"] = config.access_key_id
            session_kwargs["aws_secret_access_key"] = config.secret_access_key

        session = boto3.Session(**session_kwargs)
        self.ec2_client = session.client("ec2", region_name=config.region)
        self._audit_log: list[CleanupResult] = []

    def cleanup(self, resources: list[WastedResource]) -> list[CleanupResult]:
        """Execute cleanup for a list of wasted resources.

        Args:
            resources: List of WastedResource objects to clean up.

        Returns:
            List of CleanupResult objects.
        """
        results: list[CleanupResult] = []

        eligible = [r for r in resources if r.auto_cleanup_eligible]
        logger.info(
            "Cleanup: %d/%d resources eligible (dry_run=%s)",
            len(eligible), len(resources), self.dry_run,
        )

        for resource in eligible:
            if resource.waste_type == "unattached_volume":
                result = self._cleanup_volume(resource)
            elif resource.waste_type == "unused_ip":
                result = self._cleanup_eip(resource)
            else:
                result = CleanupResult(
                    resource_id=resource.resource_id,
                    resource_type=resource.resource_type,
                    action="skip",
                    success=True,
                    message=f"Waste type '{resource.waste_type}' not auto-cleanable",
                )

            results.append(result)
            self._audit_log.append(result)

        return results

    def _cleanup_volume(self, resource: WastedResource) -> CleanupResult:
        """Delete an unattached EBS volume, optionally creating a snapshot first."""
        snapshot_id = None

        if self.snapshot_before_delete:
            try:
                if not self.dry_run:
                    snap = self.ec2_client.create_snapshot(
                        VolumeId=resource.resource_id,
                        Description=f"Backup before cleanup of {resource.resource_id}",
                        TagSpecifications=[{
                            "ResourceType": "snapshot",
                            "Tags": [
                                {"Key": "CreatedBy", "Value": "finops-agent"},
                                {"Key": "OriginalVolume", "Value": resource.resource_id},
                            ],
                        }],
                    )
                    snapshot_id = snap["SnapshotId"]
                    logger.info("Created snapshot %s for volume %s", snapshot_id, resource.resource_id)
                else:
                    snapshot_id = "snap-dryrun"
                    logger.info("[DRY RUN] Would create snapshot for volume %s", resource.resource_id)
            except Exception as e:
                return CleanupResult(
                    resource_id=resource.resource_id,
                    resource_type=resource.resource_type,
                    action="snapshot_failed",
                    success=False,
                    message=f"Snapshot creation failed: {e}",
                )

        try:
            if not self.dry_run:
                self.ec2_client.delete_volume(VolumeId=resource.resource_id)
                logger.info("Deleted volume %s", resource.resource_id)
            else:
                logger.info("[DRY RUN] Would delete volume %s", resource.resource_id)

            return CleanupResult(
                resource_id=resource.resource_id,
                resource_type=resource.resource_type,
                action="delete_volume" if not self.dry_run else "dry_run_delete_volume",
                success=True,
                message=f"Volume {'deleted' if not self.dry_run else 'would be deleted'}",
                snapshot_id=snapshot_id,
            )
        except Exception as e:
            return CleanupResult(
                resource_id=resource.resource_id,
                resource_type=resource.resource_type,
                action="delete_failed",
                success=False,
                message=f"Volume deletion failed: {e}",
                snapshot_id=snapshot_id,
            )

    def _cleanup_eip(self, resource: WastedResource) -> CleanupResult:
        """Release an unused Elastic IP address."""
        try:
            if not self.dry_run:
                self.ec2_client.release_address(AllocationId=resource.resource_id)
                logger.info("Released Elastic IP %s", resource.resource_id)
            else:
                logger.info("[DRY RUN] Would release Elastic IP %s", resource.resource_id)

            return CleanupResult(
                resource_id=resource.resource_id,
                resource_type=resource.resource_type,
                action="release_eip" if not self.dry_run else "dry_run_release_eip",
                success=True,
                message=f"EIP {'released' if not self.dry_run else 'would be released'}",
            )
        except Exception as e:
            return CleanupResult(
                resource_id=resource.resource_id,
                resource_type=resource.resource_type,
                action="release_failed",
                success=False,
                message=f"EIP release failed: {e}",
            )

    def get_audit_log(self) -> list[CleanupResult]:
        """Return the audit log of all cleanup actions taken."""
        return list(self._audit_log)
