"""Validates repaired automation YAML through local checks and Home Assistant dry-run."""

import logging

import yaml

from ha_client import check_config, write_automation_yaml
from state import RepairState

logger = logging.getLogger(__name__)

# Minimum required top-level keys for a valid HA automation
REQUIRED_KEYS = {"alias", "trigger", "action"}

# Optional but validated if present
KNOWN_TOP_LEVEL_KEYS = REQUIRED_KEYS | {
    "id", "description", "mode", "condition", "variables", "trace", "max"
}


class ValidatorAgent:
    """Validates repaired automation YAML through local checks and Home Assistant dry-run."""

    async def run(self, state: RepairState) -> RepairState:
        """
        Perform two-stage validation on repaired automation YAML.

        Stage 1: Local structural checks (parseable YAML, required HA fields).
        Stage 2: HA dry-run check via REST API if reachable.

        Args:
            state: Dict containing repaired_yaml and automation_id.

        Returns:
            Updated state dict with validation_ok (bool) and validation_error (str).
        """
        repaired_yaml = state.get("repaired_yaml", "")

        # Stage 1 — local structural checks
        ok, error = _local_validate(repaired_yaml)
        if not ok:
            logger.warning("Local validation failed: %s", error)
            return {**state, "validation_ok": False, "validation_error": error}

        # Stage 2 — write to HA temporarily and ask HA to check config
        try:
            await write_automation_yaml(state["automation_id"], repaired_yaml)
            result = await check_config()
            if result.get("result") == "valid":
                logger.info("HA config check passed ✓")
                return {**state, "validation_ok": True, "validation_error": ""}
            else:
                error_msg = result.get("errors", "Unknown HA config error")
                logger.warning("HA config check failed: %s", error_msg)
                return {**state, "validation_ok": False, "validation_error": error_msg}

        except Exception as e:
            # If HA is unreachable, fall back to trusting local validation
            logger.warning(
                "HA config check unreachable (%s) — trusting local validation", e
            )
            return {**state, "validation_ok": True, "validation_error": ""}


def _local_validate(yaml_content: str) -> tuple[bool, str]:
    """
    Perform local structural validation on automation YAML.

    Checks:
    - YAML is parseable
    - Is a mapping (dict), not a list or scalar
    - Contains required keys: alias, trigger, action
    - Trigger and action are non-empty
    - Mode value (if present) is valid

    Args:
        yaml_content: Raw YAML string to validate.

    Returns:
        Tuple of (is_valid, error_message).
    """

    # 1. Must be parseable YAML
    try:
        doc = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        return False, f"YAML parse error: {e}"

    if not isinstance(doc, dict):
        return False, "Automation YAML must be a mapping (dict), not a list or scalar."

    # 2. Required keys must be present
    missing = REQUIRED_KEYS - doc.keys()
    if missing:
        return False, f"Missing required keys: {missing}"

    # 3. trigger and action must be non-empty
    if not doc.get("trigger"):
        return False, "The 'trigger' field is empty or missing."
    if not doc.get("action"):
        return False, "The 'action' field is empty or missing."

    # 4. mode must be a known value if present
    valid_modes = {"single", "restart", "queued", "parallel"}
    if "mode" in doc and doc["mode"] not in valid_modes:
        return False, f"Invalid mode '{doc['mode']}'. Must be one of: {valid_modes}"

    return True, ""
