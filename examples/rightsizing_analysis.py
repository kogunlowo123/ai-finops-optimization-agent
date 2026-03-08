"""Example: Run rightsizing analysis and display recommendations."""

from src.collectors.aws_costs import AWSCostCollector
from src.analyzers.rightsizing import RightsizingAnalyzer
from src.config import FinOpsConfig


def main() -> None:
    config = FinOpsConfig.from_env()
    collector = AWSCostCollector(config.aws)
    analyzer = RightsizingAnalyzer(
        headroom_percent=config.rightsizing_headroom_percent,
        cpu_threshold_low=config.idle_resource_cpu_threshold,
    )

    print("Collecting EC2 instance data...")
    instances = collector.list_ec2_instances(include_stopped=False)
    print(f"Found {len(instances)} running instances")

    print("Collecting CPU utilization metrics (this may take a few minutes)...")
    for inst in instances:
        inst.cpu_utilization_avg = collector.get_instance_cpu_utilization(
            inst.resource_id,
            days=config.idle_resource_days,
        )
        print(f"  {inst.resource_id} ({inst.instance_type}): {inst.cpu_utilization_avg:.1f}% CPU")

    print(f"\nAnalyzing rightsizing opportunities...")
    recommendations = analyzer.analyze(instances)
    summary = analyzer.summarize(recommendations)

    print(f"\n{'=' * 60}")
    print(f"RIGHTSIZING REPORT")
    print(f"{'=' * 60}")
    print(f"Total recommendations: {summary['total_recommendations']}")
    print(f"Downsize candidates: {summary.get('downsize_count', 0)}")
    print(f"Upsize candidates: {summary.get('upsize_count', 0)}")
    print(f"Monthly savings: ${summary.get('total_monthly_savings', 0):,.2f}")
    print(f"Annual savings: ${summary.get('total_annual_savings', 0):,.2f}")

    if recommendations:
        print(f"\nDetailed Recommendations:")
        for rec in recommendations:
            direction = "DOWNSIZE" if rec.monthly_savings > 0 else "UPSIZE"
            print(f"\n  [{direction}] {rec.resource_id}")
            print(f"    Current: {rec.current_type} (${rec.current_monthly_cost:,.2f}/month)")
            print(f"    Recommended: {rec.recommended_type} (${rec.recommended_monthly_cost:,.2f}/month)")
            print(f"    CPU Utilization: {rec.cpu_utilization:.1f}%")
            print(f"    Monthly Savings: ${rec.monthly_savings:,.2f} ({rec.savings_percent:.1f}%)")
            print(f"    Confidence: {rec.confidence}")
            print(f"    Reason: {rec.reason}")


if __name__ == "__main__":
    main()
