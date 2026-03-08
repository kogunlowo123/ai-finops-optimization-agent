"""Slack cost report sender."""

from __future__ import annotations

import logging
from typing import Any, Optional

import requests

from src.config import SlackConfig
from src.reporters.dashboard import DashboardData

logger = logging.getLogger(__name__)


class SlackReporter:
    """Send formatted cost reports to Slack via incoming webhooks."""

    def __init__(self, config: Optional[SlackConfig] = None) -> None:
        self.config = config or SlackConfig()
        self._session = requests.Session()

    def send_daily_report(self, data: DashboardData) -> bool:
        """Send a daily cost summary to Slack.

        Args:
            data: Dashboard data for the report.

        Returns:
            True if the message was sent successfully.
        """
        if not self.config.webhook_url:
            logger.warning("Slack webhook URL not configured")
            return False

        trend_emoji = {
            "increasing": ":chart_with_upwards_trend:",
            "decreasing": ":chart_with_downwards_trend:",
            "stable": ":left_right_arrow:",
        }
        trend = trend_emoji.get(data.cost_trend, ":bar_chart:")

        top_services_text = "\n".join(
            f"  {i+1}. {s['service']}: ${s['cost']:,.2f} ({s['percent']}%)"
            for i, s in enumerate(data.top_services[:5])
        )

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Daily Cloud Cost Report - {data.generated_date}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Total Cost:*\n${data.total_cost:,.2f} {data.currency}"},
                    {"type": "mrkdwn", "text": f"*Trend:* {trend}\n{data.cost_trend.capitalize()}"},
                    {"type": "mrkdwn", "text": f"*Period:*\n{data.period_start} to {data.period_end}"},
                ],
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Top Services by Cost:*\n{top_services_text}"},
            },
        ]

        if data.savings_opportunities:
            savings_total = data.savings_opportunities.get("total_monthly_savings", 0)
            if savings_total > 0:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":moneybag: *Savings Opportunities:* ${savings_total:,.2f}/month potential",
                    },
                })

        if data.anomalies:
            anomaly_count = len(data.anomalies)
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":warning: *{anomaly_count} cost anomalies detected.* Review recommended.",
                },
            })

        payload: dict[str, Any] = {"blocks": blocks}
        if self.config.channel:
            payload["channel"] = self.config.channel

        try:
            resp = self._session.post(
                self.config.webhook_url,
                json=payload,
                timeout=10,
            )
            success = resp.status_code == 200
            if not success:
                logger.error("Slack send failed: %s %s", resp.status_code, resp.text)
            return success
        except requests.RequestException as e:
            logger.error("Slack request failed: %s", e)
            return False

    def send_alert(self, message: str) -> bool:
        """Send a simple alert message to Slack.

        Args:
            message: Alert text.

        Returns:
            True if sent successfully.
        """
        if not self.config.webhook_url:
            return False

        try:
            resp = self._session.post(
                self.config.webhook_url,
                json={"text": message, "channel": self.config.channel},
                timeout=10,
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False
