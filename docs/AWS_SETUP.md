# AWS setup — manual steps

Every AWS action that creates or mutates a resource is done by hand, not run by Claude directly (CLAUDE.md §2). Each step below was written for a human to run, in order, and is appended here as we reach it so a fork of this repo can be stood up from README.md alone. Append-only, like `docs/DECISIONS.md` — if a step changes, add a new dated section rather than editing an old one.

Steps are written as **AWS Console click-paths first**, with the equivalent CLI command noted as a fallback — console is the preferred path for this project.

---

## 2026-08-21 — AWS SSO access

Prerequisite for every other step in this file.

1. Add an SSO session and profile to `~/.aws/config`:
   ```ini
   [sso-session temporal-sso-jit-session]
   sso_start_url           = https://temporal.awsapps.com/start
   sso_region              = us-west-2
   sso_registration_scopes = sso:account:access

   [profile SolutionsArchitecture/AWSAdministratorAccess]
   sso_session    = temporal-sso-jit-session
   sso_account_id = <your-account-id>
   sso_role_name  = AWSAdministratorAccess
   region         = us-west-2
   sso_start_url  = https://temporal.awsapps.com/start
   ```
2. Log in: `aws sso login --profile "SolutionsArchitecture/AWSAdministratorAccess"`
3. Verify: `aws sts get-caller-identity --profile "SolutionsArchitecture/AWSAdministratorAccess"` — should print your account ID and assumed-role ARN.
4. Set `AWS_PROFILE` (and `AWS_REGION`) in `.env` to match.

---

## 2026-08-21 — DynamoDB table (E0.2)

Single-table design — see `docs/DECISIONS.md` for the key schema and rationale.

**Console:**
1. DynamoDB console → **Tables** → **Create table**.
2. Table name: `agent-control-plane` (must match `DYNAMODB_TABLE_NAME` in `.env`).
3. Partition key: `pk` (String). Sort key: `sk` (String).
4. Table settings: **Customize settings** → Capacity mode: **On-demand**.
5. Create the table, then open it → **Indexes** tab → **Create index**:
   - Partition key: `gsi1_pk` (String)
   - Sort key: `gsi1_sk` (String)
   - Index name: `gsi1`
   - Capacity mode: on-demand (inherits from table)
6. **Additional settings** tab → **Time to Live (TTL)** → enable, attribute name: `ttl`.
7. Confirm region matches `AWS_REGION` in `.env` (currently `us-east-1`).

**CLI fallback:**
```bash
aws dynamodb create-table \
  --table-name agent-control-plane \
  --attribute-definitions \
      AttributeName=pk,AttributeType=S \
      AttributeName=sk,AttributeType=S \
      AttributeName=gsi1_pk,AttributeType=S \
      AttributeName=gsi1_sk,AttributeType=S \
  --key-schema AttributeName=pk,KeyType=HASH AttributeName=sk,KeyType=RANGE \
  --global-secondary-indexes '[{
      "IndexName": "gsi1",
      "KeySchema": [
          {"AttributeName": "gsi1_pk", "KeyType": "HASH"},
          {"AttributeName": "gsi1_sk", "KeyType": "RANGE"}
      ],
      "Projection": {"ProjectionType": "ALL"}
  }]' \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1

aws dynamodb update-time-to-live \
  --table-name agent-control-plane \
  --time-to-live-specification "Enabled=true, AttributeName=ttl" \
  --region us-east-1
```

**Verify:** `aws dynamodb describe-table --table-name agent-control-plane --region us-east-1 --query 'Table.TableStatus'` → should print `"ACTIVE"`.

---

## 2026-08-21 — Bedrock model access (E1.1)

**Console:**
1. Bedrock console → **Model access** (left nav, under Configure and learn) → **Modify model access**.
2. Enable **Anthropic Claude Sonnet 5** and **Amazon Nova Pro**. Submit; access is usually immediate.
3. Confirm the region matches `AWS_REGION` in `.env` (currently `us-east-1`).

**Important — use inference profile IDs, not base model IDs.** Claude Sonnet 5 does not support on-demand invocation with its base model id; it needs a cross-region inference profile. Discover the real ids rather than assuming them:

```bash
aws bedrock list-foundation-models --region us-east-1 \
  --query "modelSummaries[?contains(modelId,'claude-sonnet')].modelId" --output text
aws bedrock list-inference-profiles --region us-east-1 \
  --query "inferenceProfileSummaries[].inferenceProfileId" --output text
```

In this account that yields `us.anthropic.claude-sonnet-5` and `us.amazon.nova-pro-v1:0`, which is what `.env` uses. The `us.` prefix keeps data in US regions; `global.` routes to any commercial region — a data-residency choice.

**Verify:**
```bash
aws bedrock-runtime converse --region us-east-1 \
  --model-id us.anthropic.claude-sonnet-5 \
  --messages '[{"role":"user","content":[{"text":"Reply with exactly: OK"}]}]' \
  --inference-config '{"maxTokens":16}' \
  --query 'output.message.content[0].text'
```
Should print `"OK"`.

---

## 2026-08-21 — Temporal Cloud setup (not AWS, but required)

1. **Worker Deployment current version.** A versioned worker receives no tasks until this is set — workflows silently sit at `WorkflowTaskScheduled`:
   ```bash
   temporal worker deployment set-current-version \
     --deployment-name agent-control-plane --build-id v1
   ```
   (Add `--address`, `--namespace`, `--api-key`, `--tls` when not using a saved CLI profile.)

2. **Enable Fairness** — required for proof 1, and a **paid** add-on. Temporal Cloud UI → your namespace → **Namespace Overview** → enable Fairness. Public Preview.

3. **Request single-partition task queue** (recommended for demo legibility): task queues are internally partitioned and tasks distribute randomly across partitions, which blurs fair-dispatch proportions. Open a Temporal Support request to pin the queue to a single partition. **Lead-time item — file early.**

---

## 2026-08-21 — Mockoon (local, not AWS)

The Mockoon **desktop app** is installed on this machine but not the CLI. Either works:

- CLI, no install needed: `make mockoon` (runs `npx @mockoon/cli@9.8.0 start --data mocks/payment-service.json --port 3001`)
- Desktop app: open `mocks/payment-service.json` and start the environment on port 3001.

`MOCKOON_BASE_URL` in `.env` must match (`http://localhost:3001` locally). Endpoints:

- `POST /payments` — records a payment, returns the created record. Deliberately does **not** dedupe: it counts every call that reaches it, so the proof-3 counter reflects what actually hit the provider.
- `GET /payments` — all recorded payments; its length is the counter.

Data buckets are in-memory per server run, so restarting Mockoon resets the count.

---

<!-- Next manual steps land here as we build E7.1 (Guardrails, AgentCore Gateway/Memory/Identity), E7.2 (AgentCore Code Interpreter, S3 bucket), E7.3 (AgentCore Runtime), and E9 (IAM roles, Lambda, App Runner, Amplify). -->
