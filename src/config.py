"""Configuration management for the FinOps optimization agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class CloudProvider(str, Enum):
    """Supported cloud providers."""

    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"


class LLMProvider(str, Enum):
    """Supported LLM providers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


@dataclass
class LLMConfig:
    """LLM provider configuration."""

    provider: LLMProvider = LLMProvider.OPENAI
    model: str = "gpt-4o"
    temperature: float = 0.0
    max_tokens: int = 4096
    api_key: Optional[str] = None

    def __post_init__(self) -> None:
        if self.api_key is None:
            env_map = {
                LLMProvider.OPENAI: "OPENAI_API_KEY",
                LLMProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
            }
            self.api_key = os.environ.get(env_map.get(self.provider, ""), "")


@dataclass
class AWSConfig:
    """AWS cost management configuration."""

    profile_name: Optional[str] = None
    region: str = "us-east-1"
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    role_arn: Optional[str] = None

    def __post_init__(self) -> None:
        if self.access_key_id is None:
            self.access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
            self.secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY")


@dataclass
class AzureConfig:
    """Azure cost management configuration."""

    subscription_id: str = ""
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""

    def __post_init__(self) -> None:
        if not self.subscription_id:
            self.subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
            self.tenant_id = os.environ.get("AZURE_TENANT_ID", "")
            self.client_id = os.environ.get("AZURE_CLIENT_ID", "")
            self.client_secret = os.environ.get("AZURE_CLIENT_SECRET", "")


@dataclass
class GCPConfig:
    """GCP billing configuration."""

    project_id: str = ""
    billing_account_id: str = ""
    credentials_path: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.project_id:
            self.project_id = os.environ.get("GCP_PROJECT_ID", "")
            self.billing_account_id = os.environ.get("GCP_BILLING_ACCOUNT_ID", "")
            self.credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")


@dataclass
class SlackConfig:
    """Slack reporting configuration."""

    webhook_url: Optional[str] = None
    channel: str = "#finops"

    def __post_init__(self) -> None:
        if self.webhook_url is None:
            self.webhook_url = os.environ.get("SLACK_WEBHOOK_URL")


@dataclass
class EmailConfig:
    """Email reporting configuration."""

    smtp_host: str = ""
    smtp_port: int = 587
    username: str = ""
    password: str = ""
    from_address: str = ""
    to_addresses: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.smtp_host:
            self.smtp_host = os.environ.get("SMTP_HOST", "")
            self.username = os.environ.get("SMTP_USERNAME", "")
            self.password = os.environ.get("SMTP_PASSWORD", "")
            self.from_address = os.environ.get("EMAIL_FROM", "")


@dataclass
class FinOpsConfig:
    """Top-level FinOps agent configuration."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    aws: AWSConfig = field(default_factory=AWSConfig)
    azure: AzureConfig = field(default_factory=AzureConfig)
    gcp: GCPConfig = field(default_factory=GCPConfig)
    slack: SlackConfig = field(default_factory=SlackConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    enabled_providers: list[CloudProvider] = field(
        default_factory=lambda: [CloudProvider.AWS]
    )
    cost_anomaly_threshold_percent: float = 20.0
    idle_resource_cpu_threshold: float = 5.0
    idle_resource_days: int = 7
    rightsizing_headroom_percent: float = 20.0
    report_currency: str = "USD"

    @classmethod
    def from_env(cls) -> "FinOpsConfig":
        """Create configuration from environment variables."""
        providers_str = os.environ.get("FINOPS_PROVIDERS", "aws")
        providers = [CloudProvider(p.strip()) for p in providers_str.split(",")]

        return cls(
            llm=LLMConfig(),
            aws=AWSConfig(),
            azure=AzureConfig(),
            gcp=GCPConfig(),
            slack=SlackConfig(),
            email=EmailConfig(),
            enabled_providers=providers,
        )
