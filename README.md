# 🏠 HA YAML Repair Agent

A fully autonomous multi-agent system that monitors Home Assistant for broken
automations, diagnoses the root cause using a local LLM, rewrites the YAML,
validates it, and redeploys — all without human intervention.

## Architecture

```
Supervisor (main.py)
└── watches HA WebSocket for automation failures
    └── fires LangGraph repair workflow per failure:

        DiagnosticsAgent   → fetches YAML + logs + known entity IDs
        RootCauseAgent     → LLM reasons about why it broke + diffs stale entities
        YAMLRepairAgent    → LLM rewrites the automation YAML
        ValidatorAgent     → local structural checks + HA dry-run
             │
             ├── valid   → deploy (write + reload HA) → ✅
             ├── invalid → retry YAMLRepairAgent (up to MAX_REPAIR_ATTEMPTS)
             └── max retries → escalate (log for human review) → ⚠️
```

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Start LM Studio with API enabled
- Open LM Studio
- Load your model
- Start the local server (default: `http://localhost:8000`)
- Ensure the API is running on the OpenAI-compatible endpoint

### 3. Configure environment
```bash
cp .env.example .env
# Edit .env with your HA URL, token, and LM Studio settings
```

### 4. Run
```bash
python main.py
```

## Environment Variables

| Variable            | Default                        | Description                            |
|---------------------|--------------------------------|----------------------------------------|
| `HA_URL`            | `http://homeassistant.local:8123` | Home Assistant base URL             |
| `HA_TOKEN`          | *(required)*                   | Long-lived HA access token             |
| `LLM_BASE_URL`      | `http://192.168.0.155:1234/v1` | LM Studio OpenAI-compatible API        |
| `LLM_MODEL`         | `local-model`                  | Model name (local-model for LM Studio) |
| `MAX_REPAIR_ATTEMPTS` | `3`                          | Max self-correction loops per failure  |
| `AUTOMATIONS_DIR`   | `/config/automations`          | Path to HA automation YAML files       |

## Getting a Home Assistant Token

1. Open HA → Profile → Security → Long-Lived Access Tokens
2. Create a token and paste it into your `.env`

## Extending

- **Add a Notification Agent**: ping you on Slack/Telegram when a repair is deployed or escalated
- **Add Memory**: persist repair history to SQLite so the agent learns from past fixes
- **Add the Energy Optimizer**: layer Option A from the project ideas on top of this supervisor
