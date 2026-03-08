"""Main FinOps agent that orchestrates cost analysis and optimization."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

from src.analyzers.anomaly_detector import AnomalyDetector, CostAnomaly
from src.analyzers.reserved_advisor import ReservationRecommendation, ReservedInstanceAdvisor
from src.analyzers.rightsizing import RightsizingAnalyzer, RightsizingRecommendation
from src.analyzers.waste_finder import WastedResource, WasteFinder
from src.collectors.aws_costs import AWSCostCollector, CostRecord, ResourceInfo
from src.config import CloudProvider, FinOpsConfig
from src.reporters.dashboard import DashboardData, DashboardGenerator
from src.reporters.slack_reporter import SlackReporter

logger = logging.getLogger(__name__)


@dataclass
class FinOpsReport:
    """Complete FinOps analysis report."""

    generated_date: str
    total_cost: float
    cost_records: list[CostRecord]
    anomalies: list[CostAnomaly]
    rightsizing: list[RightsizingRecommendation]
    reservations: list[ReservationRecommendation]
    waste: list[WastedResource]
    dashboard: DashboardData
    total_monthly_savings: float = 0.0
    total_annual_savings: float = 0.0


class FinOpsAgent:
    """Orchestrates multi-cloud cost analysis and optimization.

    Pipeline:
        1. Collect cost data from enabled cloud providers
        2. Detect cost anomalies
        3. Analyze rightsizing opportunities
        4. Evaluate reserved instance recommendations
        5. Find wasted/unused resources
        6. Generate dashboard and reports

    Usage:
        config = FinOpsConfig.from_env()
        agent = FinOpsAgent(config)
        report = agent.run_analysis()
    """

    def __init__(self, config: Optional[FinOpsConfig] = None) -> None:
        self.config = config or FinOpsConfig.from_env()

        self.collectors: dict[str, Any] = {}
        if CloudProvider.AWS in self.config.enabled_providers:
            self.collectors["aws"] = AWSCostCollector(self.config.aws)

        if CloudProvider.AZURE in self.config.enabled_providers:
            from src.collectors.azure_costs import AzureCostCollector
            self.collectors["azure"] = AzureCostCollector(self.config.azure)

        if CloudProvider.GCP in self.config.enabled_providers:
            from src.collectors.gcp_costs import GCPCostCollector
            self.collectors["gcp"] = GCPCostCollector(self.config.gcp)

        self.anomaly_detector = AnomalyDetector(
            threshold_percent=self.config.cost_anomaly_threshold_percent,
            llm_config=self.config.llm,
        )
        self.rightsizing_analyzer = RightsizingAnalyzer(
            headroom_percent=self.config.rightsizing_headroom_percent,
            cpu_threshold_low=self.config.idle_resource_cpu_threshold,
        )
        self.reserved_advisor = ReservedInstanceAdvisor()
        self.waste_finder = WasteFinder(
            idle_cpu_threshold=self.config.idle_resource_cpu_threshold,
            idle_days=self.config.idle_resource_days,
        )
        self.dashboard_gen = DashboardGenerator(currency=self.config.report_currency)
        self.slack_reporter = SlackReporter(config=self.config.slack)

    def run_analysis(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> FinOpsReport:
        """Run the full FinOps analysis pipeline.

        Args:
            start_date: Analysis start date. Defaults to 30 days ago.
            end_date: Analysis end date. Defaults to today.

        Returns:
            FinOpsReport with all analysis results.
        """
        if start_date is None:
            start_date = date.today() - timedelta(days=30)
        if end_date is None:
            end_date = date.today()

        logger.info("Starting FinOps analysis for %s to %s", start_date, end_date)

        # Step 1: Collect cost data
        all_costs: list[CostRecord] = []
        all_instances: list[ResourceInfo] = []
        all_volumes: list[ResourceInfo] = []
        all_ips: list[ResourceInfo] = []

        for provider_name, collector in self.collectors.items():
            logger.info("Collecting data from %s", provider_name)
            try:
                costs = collector.get_cost_and_usage(
                    start_date=start_date,
                    end_date=end_date,
                    group_by=["SERVICE"] if provider_name == "aws" else ["ServiceName"],
                )
                all_costs.extend(costs)

                if provider_name == "aws":
                    instances = collector.list_ec2_instances()
                    for inst in instances:
                        if inst.state == "running":
                            inst.cpu_utilization_avg = collector.get_instance_cpu_utilization(
                                inst.resource_id, days=self.config.idle_resource_days,
                            )
                    all_instances.extend(instances)
                    all_volumes.extend(collector.get_unattached_ebs_volumes())
                    all_ips.extend(collector.get_unused_elastic_ips())

            except Exception:
                logger.exception("Failed to collect data from %s", provider_name)

        total_cost = sum(r.amount for r in all_costs)
        logger.info("Total cost collected: $%.2f from %d records", total_cost, len(all_costs))

        # Step 2: Detect anomalies
        anomalies = self.anomaly_detector.detect(all_costs)
        if anomalies:
            anomalies = self.anomaly_detector.explain_anomalies(anomalies)
        logger.info("Detected %d cost anomalies", len(anomalies))

        # Step 3: Rightsizing analysis
        rightsizing_recs = self.rightsizing_analyzer.analyze(all_instances)
        logger.info("Generated %d rightsizing recommendations", len(rightsizing_recs))

        # Step 4: Reserved instance recommendations
        reservation_recs = self.reserved_advisor.analyze(all_costs)
        logger.info("Generated %d reservation recommendations", len(reservation_recs))

        # Step 5: Find waste
        waste = self.waste_finder.find_waste(all_instances, all_volumes, all_ips)
        logger.info("Found %d wasted resources", len(waste))

        # Step 6: Calculate total savings
        rightsizing_savings = sum(
            r.monthly_savings for r in rightsizing_recs if r.monthly_savings > 0
        )
        reservation_savings = sum(r.monthly_savings for r in reservation_recs)
        waste_savings = sum(w.estimated_monthly_cost for w in waste)
        total_monthly_savings = rightsizing_savings + reservation_savings + waste_savings

        # Step 7: Generate dashboard
        savings_summary = {
            "total_monthly_savings": round(total_monthly_savings, 2),
            "total_annual_savings": round(total_monthly_savings * 12, 2),
            "rightsizing_monthly": round(rightsizing_savings, 2),
            "reservation_monthly": round(reservation_savings, 2),
            "waste_monthly": round(waste_savings, 2),
        }

        anomaly_dicts = [
            {
                "date": a.date,
                "service": a.service,
                "deviation": a.deviation_percent,
                "severity": a.severity,
            }
            for a in anomalies[:10]
        ]

        dashboard = self.dashboard_gen.generate(
            cost_records=all_costs,
            anomalies=anomaly_dicts,
            savings=savings_summary,
        )

        report = FinOpsReport(
            generated_date=date.today().isoformat(),
            total_cost=round(total_cost, 2),
            cost_records=all_costs,
            anomalies=anomalies,
            rightsizing=rightsizing_recs,
            reservations=reservation_recs,
            waste=waste,
            dashboard=dashboard,
            total_monthly_savings=round(total_monthly_savings, 2),
            total_annual_savings=round(total_monthly_savings * 12, 2),
        )

        logger.info(
            "Analysis complete. Total savings potential: $%.2f/month ($%.2f/year)",
            total_monthly_savings,
            total_monthly_savings * 12,
        )

        return report

    def send_slack_report(self, report: FinOpsReport) -> bool:
        """Send the FinOps report to Slack.

        Args:
            report: The analysis report to send.

        Returns:
            True if sent successfully.
        """
        return self.slack_reporter.send_daily_report(report.dashboard)

    def print_summary(self, report: FinOpsReport) -> None:
        """Print a summary of the FinOps report to stdout."""
        print(f"\nFinOps Analysis Report - {report.generated_date}")
        print("=" * 60)
        print(f"Total Cost: ${report.total_cost:,.2f}")
        print(f"Cost Trend: {report.dashboard.cost_trend}")
        print(f"\nSavings Opportunities:")
        print(f"  Rightsizing:   ${sum(r.monthly_savings for r in report.rightsizing if r.monthly_savings > 0):,.2f}/month")
        print(f"  Reservations:  ${sum(r.monthly_savings for r in report.reservations):,.2f}/month")
        print(f"  Waste removal: ${sum(w.estimated_monthly_cost for w in report.waste):,.2f}/month")
        print(f"  TOTAL:         ${report.total_monthly_savings:,.2f}/month (${report.total_annual_savings:,.2f}/year)")
        print(f"\nAnomalies: {len(report.anomalies)}")
        print(f"Rightsizing recommendations: {len(report.rightsizing)}")
        print(f"Wasted resources: {len(report.waste)}")
