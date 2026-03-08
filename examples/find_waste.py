"""Example: Find and report wasted cloud resources."""

from src.collectors.aws_costs import AWSCostCollector
from src.analyzers.waste_finder import WasteFinder
from src.config import AWSConfig, FinOpsConfig


def main() -> None:
    config = FinOpsConfig.from_env()
    collector = AWSCostCollector(config.aws)
    waste_finder = WasteFinder(
        idle_cpu_threshold=config.idle_resource_cpu_threshold,
        idle_days=config.idle_resource_days,
    )

    print("Scanning for wasted AWS resources...")
    print("-" * 60)

    # Collect resource inventory
    instances = collector.list_ec2_instances(include_stopped=True)
    print(f"Found {len(instances)} EC2 instances")

    # Enrich with CPU utilization
    for inst in instances:
        if inst.state == "running":
            inst.cpu_utilization_avg = collector.get_instance_cpu_utilization(
                inst.resource_id,
                days=config.idle_resource_days,
            )

    unattached_volumes = collector.get_unattached_ebs_volumes()
    print(f"Found {len(unattached_volumes)} unattached EBS volumes")

    unused_ips = collector.get_unused_elastic_ips()
    print(f"Found {len(unused_ips)} unused Elastic IPs")

    # Find waste
    waste = waste_finder.find_waste(instances, unattached_volumes, unused_ips)
    summary = waste_finder.summarize(waste)

    print(f"\n{'=' * 60}")
    print(f"WASTE REPORT")
    print(f"{'=' * 60}")
    print(f"Total wasted resources: {summary['total_resources']}")
    print(f"Estimated monthly waste: ${summary['total_monthly_waste']:,.2f}")
    print(f"Estimated annual waste: ${summary['total_annual_waste']:,.2f}")
    print(f"Auto-cleanup eligible: {summary['auto_cleanup_eligible']}")

    print(f"\nBreakdown by type:")
    for waste_type, info in summary.get("by_type", {}).items():
        print(f"  {waste_type}: {info['count']} resources (${info['monthly_cost']:,.2f}/month)")

    print(f"\nDetailed findings:")
    for w in waste:
        print(f"\n  [{w.waste_type.upper()}] {w.resource_id}")
        print(f"    Type: {w.resource_type}")
        print(f"    Region: {w.region}")
        print(f"    Est. Cost: ${w.estimated_monthly_cost:,.2f}/month")
        print(f"    Reason: {w.reason}")
        print(f"    Recommendation: {w.recommendation}")
        print(f"    Auto-cleanup: {'Yes' if w.auto_cleanup_eligible else 'No'}")


if __name__ == "__main__":
    main()
