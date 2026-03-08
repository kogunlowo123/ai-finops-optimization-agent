"""Email cost report sender."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from src.config import EmailConfig
from src.reporters.dashboard import DashboardData

logger = logging.getLogger(__name__)


class EmailReporter:
    """Send formatted cost reports via email."""

    def __init__(self, config: Optional[EmailConfig] = None) -> None:
        self.config = config or EmailConfig()

    def send_daily_report(
        self,
        data: DashboardData,
        recipients: list[str] | None = None,
    ) -> bool:
        """Send a daily cost report email.

        Args:
            data: Dashboard data for the report.
            recipients: Override recipient list.

        Returns:
            True if the email was sent successfully.
        """
        if not self.config.smtp_host:
            logger.warning("SMTP host not configured")
            return False

        to_addresses = recipients or self.config.to_addresses
        if not to_addresses:
            logger.warning("No email recipients configured")
            return False

        subject = f"Cloud Cost Report - {data.generated_date} | ${data.total_cost:,.2f}"
        html_body = self._build_html_report(data)
        text_body = self._build_text_report(data)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.config.from_address
        msg["To"] = ", ".join(to_addresses)

        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        try:
            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port) as server:
                server.starttls()
                if self.config.username:
                    server.login(self.config.username, self.config.password)
                server.send_message(msg)

            logger.info("Cost report email sent to %d recipients", len(to_addresses))
            return True
        except smtplib.SMTPException as e:
            logger.error("Failed to send email report: %s", e)
            return False

    def _build_html_report(self, data: DashboardData) -> str:
        """Build an HTML email body for the cost report."""
        service_rows = ""
        for s in data.top_services[:10]:
            service_rows += (
                f"<tr>"
                f"<td style='padding:8px;border-bottom:1px solid #ddd'>{s['service']}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #ddd;text-align:right'>"
                f"${s['cost']:,.2f}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #ddd;text-align:right'>"
                f"{s['percent']}%</td>"
                f"</tr>"
            )

        savings_section = ""
        if data.savings_opportunities:
            monthly = data.savings_opportunities.get("total_monthly_savings", 0)
            if monthly > 0:
                savings_section = (
                    f"<h3>Savings Opportunities</h3>"
                    f"<p>Estimated monthly savings available: "
                    f"<strong>${monthly:,.2f}</strong></p>"
                )

        return f"""
        <html>
        <body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto">
            <h2>Cloud Cost Report</h2>
            <p>Period: {data.period_start} to {data.period_end}</p>
            <div style="background:#f0f4f8;padding:20px;border-radius:8px;margin:16px 0">
                <h1 style="margin:0;color:#1a1a2e">${data.total_cost:,.2f} {data.currency}</h1>
                <p style="margin:4px 0;color:#666">Total cost | Trend: {data.cost_trend}</p>
            </div>
            <h3>Top Services</h3>
            <table style="width:100%;border-collapse:collapse">
                <tr style="background:#1a1a2e;color:white">
                    <th style="padding:8px;text-align:left">Service</th>
                    <th style="padding:8px;text-align:right">Cost</th>
                    <th style="padding:8px;text-align:right">%</th>
                </tr>
                {service_rows}
            </table>
            {savings_section}
            <hr>
            <p style="color:#999;font-size:12px">
                Generated on {data.generated_date} by FinOps Optimization Agent
            </p>
        </body>
        </html>
        """

    def _build_text_report(self, data: DashboardData) -> str:
        """Build a plain text email body for the cost report."""
        lines = [
            f"Cloud Cost Report - {data.generated_date}",
            f"{'=' * 50}",
            f"Period: {data.period_start} to {data.period_end}",
            f"Total Cost: ${data.total_cost:,.2f} {data.currency}",
            f"Trend: {data.cost_trend}",
            "",
            "Top Services:",
            "-" * 40,
        ]

        for s in data.top_services[:10]:
            lines.append(f"  {s['service']}: ${s['cost']:,.2f} ({s['percent']}%)")

        if data.savings_opportunities:
            monthly = data.savings_opportunities.get("total_monthly_savings", 0)
            if monthly > 0:
                lines.extend([
                    "",
                    f"Savings Opportunities: ${monthly:,.2f}/month",
                ])

        return "\n".join(lines)
