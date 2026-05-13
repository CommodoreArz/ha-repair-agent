import os
from dotenv import load_dotenv

load_dotenv()

# ── Home Assistant ────────────────────────────────────────────────────────────
HA_URL   = os.getenv("HA_URL",   "http://homeassistant.local:8123")
HA_TOKEN = os.getenv("HA_TOKEN", "")                # Long-lived access token

# ── Local LLM (LM Studio) ────────────────────────────────────────────────────
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://192.168.0.155:1234/v1")
LLM_MODEL    = os.getenv("LLM_MODEL",    "local-model")

# ── Agent behaviour ───────────────────────────────────────────────────────────
MAX_REPAIR_ATTEMPTS = int(os.getenv("MAX_REPAIR_ATTEMPTS", "3"))
USE_DYNAMIC_DOMAINS = os.getenv("USE_DYNAMIC_DOMAINS", "false").lower() == "true"
DEBUG_LOGGING       = os.getenv("DEBUG_LOGGING",       "false").lower() == "true"

# ── Automation config storage ─────────────────────────────────────────────────
# Where HA automation YAML files live (adjust to your HA install path)
AUTOMATIONS_DIR = os.getenv(
    "AUTOMATIONS_DIR",
    "/config/automations"
)
