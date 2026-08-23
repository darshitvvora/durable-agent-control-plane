# Durable Agent Control Plane 
The durable operating system for agentic AI - Temporal, Strands, Bedrock, AgentCore, Serverless, S3

## Setup

- Copy `sample.env` to `.env` and fill in the values.
- AWS resources (DynamoDB, IAM roles, Lambda, S3, Bedrock/AgentCore access, etc.) are provisioned by hand, not by a script — follow [docs/AWS_SETUP.md](docs/AWS_SETUP.md) step by step.
- See `CLAUDE.md` for the architecture, non-negotiables, and stack.
