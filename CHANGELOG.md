# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-03-07

### Added

- Initial release of the FinOps optimization agent.
- Multi-cloud cost collection for AWS (Cost Explorer + boto3), Azure (Cost Management), and GCP (Billing API + BigQuery).
- Cost anomaly detection using z-score analysis with LLM-powered explanations.
- Instance rightsizing analyzer with EC2 instance family specs and CPU utilization thresholds.
- Reserved instance and savings plan advisor with configurable commitment terms.
- Waste finder for idle instances, unattached volumes, and unused Elastic IPs.
- Resource scheduler for start/stop automation of non-production instances.
- Resource cleanup executor with dry-run mode and snapshot-before-delete safety.
- Dashboard data generator with JSON export for custom visualization.
- Slack and email reporters for daily cost summaries.
- Example scripts for daily reporting, waste finding, and rightsizing analysis.
