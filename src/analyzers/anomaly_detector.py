"""Cost anomaly detection using statistical methods and LLM analysis."""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.collectors.aws_costs import CostRecord
from src.config import LLMConfig

logger = logging.getLogger(__name__)


@dataclass
class CostAnomaly:
    """A detected cost anomaly."""

    date: str
    service: str
    actual_amount: float
    expected_amount: float
    deviation_percent: float
    severity: str
    explanation: str
    currency: str = "USD"


class AnomalyDetector:
    """Detect cost anomalies using statistical analysis and LLM-powered explanation.

    Uses z-score based detection to identify days where spending deviates
    significantly from the historical average, then uses an LLM to
    provide human-readable explanations and potential causes.
    """

    def __init__(
        self,
        threshold_percent: float = 20.0,
        llm_config: Optional[LLMConfig] = None,
    ) -> None:
        self.threshold_percent = threshold_percent
        llm_conf = llm_config or LLMConfig()
        self.llm = ChatOpenAI(
            model=llm_conf.model,
            temperature=llm_conf.temperature,
            max_tokens=llm_conf.max_tokens,
            api_key=llm_conf.api_key,
        )

    def detect(self, cost_records: list[CostRecord]) -> list[CostAnomaly]:
        """Detect cost anomalies in the provided cost data.

        Args:
            cost_records: List of daily cost records.

        Returns:
            List of CostAnomaly objects for days with significant deviations.
        """
        service_groups: dict[str, list[CostRecord]] = {}
        for record in cost_records:
            service_groups.setdefault(record.service, []).append(record)

        anomalies: list[CostAnomaly] = []

        for service, records in service_groups.items():
            if len(records) < 7:
                continue

            amounts = [r.amount for r in records]
            mean = statistics.mean(amounts)
            if mean == 0:
                continue

            stdev = statistics.stdev(amounts) if len(amounts) > 1 else 0

            for record in records:
                if mean > 0:
                    deviation = ((record.amount - mean) / mean) * 100
                else:
                    deviation = 0.0

                if abs(deviation) >= self.threshold_percent:
                    z_score = (record.amount - mean) / stdev if stdev > 0 else 0
                    severity = self._classify_severity(abs(deviation), abs(z_score))

                    anomalies.append(CostAnomaly(
                        date=record.date,
                        service=service,
                        actual_amount=record.amount,
                        expected_amount=round(mean, 2),
                        deviation_percent=round(deviation, 1),
                        severity=severity,
                        explanation="",
                        currency=record.currency,
                    ))

        anomalies.sort(key=lambda a: abs(a.deviation_percent), reverse=True)
        return anomalies

    def explain_anomalies(self, anomalies: list[CostAnomaly]) -> list[CostAnomaly]:
        """Use an LLM to generate explanations for detected anomalies.

        Args:
            anomalies: List of anomalies to explain.

        Returns:
            The same anomalies with explanation fields populated.
        """
        if not anomalies:
            return anomalies

        anomaly_descriptions = []
        for a in anomalies[:10]:
            direction = "increase" if a.deviation_percent > 0 else "decrease"
            anomaly_descriptions.append(
                f"- {a.service} on {a.date}: ${a.actual_amount:.2f} "
                f"(expected ~${a.expected_amount:.2f}, {abs(a.deviation_percent):.1f}% {direction})"
            )

        prompt = (
            "You are a FinOps analyst. Analyze the following cloud cost anomalies "
            "and provide brief, actionable explanations for each.\n\n"
            "For each anomaly, suggest:\n"
            "1. Most likely cause\n"
            "2. Whether it requires investigation\n"
            "3. Recommended action\n\n"
            "Anomalies:\n" + "\n".join(anomaly_descriptions)
        )

        messages = [
            SystemMessage(content="You are an expert cloud FinOps analyst."),
            HumanMessage(content=prompt),
        ]

        response = self.llm.invoke(messages)
        explanation = response.content if isinstance(response.content, str) else str(response.content)

        for anomaly in anomalies[:10]:
            anomaly.explanation = explanation

        return anomalies

    def _classify_severity(self, deviation_pct: float, z_score: float) -> str:
        """Classify anomaly severity based on deviation and z-score."""
        if deviation_pct > 100 or z_score > 3:
            return "critical"
        elif deviation_pct > 50 or z_score > 2:
            return "high"
        elif deviation_pct > 20:
            return "medium"
        return "low"
