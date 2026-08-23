"""Env-driven settings — the only place in the codebase that reads env vars.

Everything else (worker, API, activities, registry) imports get_settings().
No hardcoded ARNs or endpoints anywhere else (CLAUDE.md §2).
"""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _blank_env_vars_are_unset(cls, data: object) -> object:
        """A blank `KEY=` line in .env loads as "", not unset — treat it as unset."""
        if isinstance(data, dict):
            return {k: (None if v == "" else v) for k, v in data.items()}
        return data

    app_env: str = "local"  # local | cloud — laptop vs deployed, not local-vs-real-AWS

    # Temporal Cloud — never a local server (CLAUDE.md §2)
    temporal_address: str
    temporal_namespace: str
    task_queue: str
    temporal_cloud_api_key: str
    # Worker Deployment Version build id — bump to publish a new agent version (proof 2)
    build_id: str = "v1"

    # AWS
    aws_region: str
    aws_profile: str | None = None  # unset on Lambda/App Runner — falls back to the IAM role

    # Bedrock
    bedrock_claude_model_id: str
    bedrock_nova_model_id: str
    bedrock_guardrail_id: str | None = None
    bedrock_guardrail_version: str | None = None
    bedrock_api_key: str | None = None

    # AgentCore — filled in as E7.1/E7.2/E7.3 provision each service
    agentcore_gateway_url: str | None = None
    agentcore_memory_id: str | None = None
    agentcore_runtime_endpoint: str | None = None

    # DynamoDB (real AWS in every environment, see §5)
    dynamodb_table_name: str

    # S3 (External Storage claim-check, Preview) — filled in when E7.2 provisions the bucket
    s3_bucket_name: str | None = None

    # Mockoon — same App Runner URL as the API in cloud, standalone locally
    mockoon_base_url: str

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
