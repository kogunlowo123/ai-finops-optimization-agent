"""Reservation and savings plan purchase executor."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import boto3

from src.analyzers.reserved_advisor import ReservationRecommendation
from src.config import AWSConfig

logger = logging.getLogger(__name__)


@dataclass
class PurchaseResult:
    """Result of a reservation purchase."""

    recommendation: ReservationRecommendation
    success: bool
    reservation_id: Optional[str] = None
    message: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ReservationPurchaser:
    """Execute reserved instance and savings plan purchases on AWS.

    Wraps the AWS EC2 Reserved Instances and Savings Plans purchase APIs.
    Requires explicit approval before executing purchases.
    """

    def __init__(
        self,
        aws_config: Optional[AWSConfig] = None,
        dry_run: bool = True,
        require_approval: bool = True,
    ) -> None:
        self.dry_run = dry_run
        self.require_approval = require_approval
        config = aws_config or AWSConfig()

        session_kwargs: dict[str, Any] = {}
        if config.profile_name:
            session_kwargs["profile_name"] = config.profile_name
        if config.access_key_id:
            session_kwargs["aws_access_key_id"] = config.access_key_id
            session_kwargs["aws_secret_access_key"] = config.secret_access_key

        session = boto3.Session(**session_kwargs)
        self.ec2_client = session.client("ec2", region_name=config.region)
        self._purchase_log: list[PurchaseResult] = []

    def preview_purchase(
        self,
        recommendation: ReservationRecommendation,
    ) -> dict[str, Any]:
        """Generate a preview of a reservation purchase without executing.

        Args:
            recommendation: The reservation recommendation to preview.

        Returns:
            Dictionary with purchase details and cost projections.
        """
        term_months = 36 if "3 year" in recommendation.commitment_term else 12

        return {
            "service": recommendation.service,
            "instance_type": recommendation.instance_type,
            "region": recommendation.region,
            "term": recommendation.commitment_term,
            "payment_option": recommendation.payment_option,
            "current_monthly_cost": recommendation.current_monthly_cost,
            "reserved_monthly_cost": recommendation.reserved_monthly_cost,
            "monthly_savings": recommendation.monthly_savings,
            "total_savings_over_term": round(recommendation.monthly_savings * term_months, 2),
            "break_even_months": recommendation.break_even_months,
            "total_commitment": round(recommendation.reserved_monthly_cost * term_months, 2),
            "confidence": recommendation.confidence,
        }

    def purchase(
        self,
        recommendation: ReservationRecommendation,
        approved: bool = False,
    ) -> PurchaseResult:
        """Execute a reservation purchase.

        Args:
            recommendation: The recommendation to act on.
            approved: Whether explicit approval has been given.

        Returns:
            PurchaseResult with outcome details.
        """
        if self.require_approval and not approved:
            result = PurchaseResult(
                recommendation=recommendation,
                success=False,
                message="Purchase requires explicit approval. Call with approved=True.",
            )
            self._purchase_log.append(result)
            return result

        if self.dry_run:
            result = PurchaseResult(
                recommendation=recommendation,
                success=True,
                reservation_id="ri-dryrun-preview",
                message=(
                    f"[DRY RUN] Would purchase {recommendation.commitment_term} "
                    f"reservation for {recommendation.service}. "
                    f"Estimated savings: ${recommendation.annual_savings:.2f}/year."
                ),
            )
            self._purchase_log.append(result)
            return result

        try:
            offering_type_map = {
                "1yr no upfront": "No Upfront",
                "1yr partial upfront": "Partial Upfront",
                "1yr all upfront": "All Upfront",
                "3yr no upfront": "No Upfront",
                "3yr partial upfront": "Partial Upfront",
                "3yr all upfront": "All Upfront",
            }

            duration_map = {
                "1 year": 31536000,
                "3 years": 94608000,
            }

            offering_type = offering_type_map.get(
                recommendation.payment_option, "No Upfront"
            )
            duration = duration_map.get(recommendation.commitment_term, 31536000)

            offerings = self.ec2_client.describe_reserved_instances_offerings(
                InstanceType=recommendation.instance_type,
                OfferingType=offering_type,
                ProductDescription="Linux/UNIX",
                MaxDuration=duration,
                MinDuration=duration,
                MaxResults=1,
            )

            offering_list = offerings.get("ReservedInstancesOfferings", [])
            if not offering_list:
                result = PurchaseResult(
                    recommendation=recommendation,
                    success=False,
                    message="No matching reserved instance offering found.",
                )
                self._purchase_log.append(result)
                return result

            offering_id = offering_list[0]["ReservedInstancesOfferingId"]

            purchase_response = self.ec2_client.purchase_reserved_instances_offering(
                ReservedInstancesOfferingId=offering_id,
                InstanceCount=1,
            )

            ri_id = purchase_response.get("ReservedInstancesId", "")
            result = PurchaseResult(
                recommendation=recommendation,
                success=True,
                reservation_id=ri_id,
                message=f"Successfully purchased reservation {ri_id}",
            )
            self._purchase_log.append(result)
            return result

        except Exception as e:
            logger.error("Reservation purchase failed: %s", e)
            result = PurchaseResult(
                recommendation=recommendation,
                success=False,
                message=f"Purchase failed: {e}",
            )
            self._purchase_log.append(result)
            return result

    def get_purchase_log(self) -> list[PurchaseResult]:
        """Return the log of all purchase attempts."""
        return list(self._purchase_log)
