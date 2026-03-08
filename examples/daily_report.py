"""Example: Generate and send a daily FinOps cost report."""

from src.config import CloudProvider, FinOpsConfig
from src.finops_agent import FinOpsAgent


def main() -> None:
    config = FinOpsConfig.from_env()
    config.enabled_providers = [CloudProvider.AWS]

    agent = FinOpsAgent(config)

    print("Running daily FinOps analysis...")
    report = agent.run_analysis()

    agent.print_summary(report)

    # Send to Slack if configured
    if config.slack.webhook_url:
        success = agent.send_slack_report(report)
        print(f"\nSlack report sent: {success}")

    # Export dashboard JSON
    dashboard_json = agent.dashboard_gen.to_json(report.dashboard)
    with open("daily_report.json", "w") as f:
        f.write(dashboard_json)
    print("\nDashboard data exported to daily_report.json")


if __name__ == "__main__":
    main()
