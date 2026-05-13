"""
ha_client.py
Thin async wrapper around the Home Assistant REST API and WebSocket event bus.
"""

import asyncio
import json
import logging
import re
import time
from typing import AsyncGenerator

import aiohttp
import yaml

from config import HA_URL, HA_TOKEN, AUTOMATIONS_DIR, USE_DYNAMIC_DOMAINS

logger = logging.getLogger(__name__)
HEADERS = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}

# ── Domain discovery ──────────────────────────────────────────────────────────

HA_DOMAINS_FALLBACK = {
    "light", "switch", "sensor", "binary_sensor", "climate", "media_player",
    "automation", "script", "scene", "input_boolean", "input_number",
    "input_select", "input_text", "cover", "fan", "lock", "alarm_control_panel",
    "camera", "device_tracker", "person", "zone", "sun", "weather",
}

_domains_cache: dict[str, float | set[str]] = {}  # format: {domains: set, expiry: float}


# ── REST helpers ──────────────────────────────────────────────────────────────

async def get_automation_yaml(automation_id: str) -> str:
    """
    Fetch the YAML source of an automation.
    HA stores automations in /config/automations.yaml (split files or single).
    We first try the REST config endpoint, then fall back to reading the file.
    """
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        url = f"{HA_URL}/api/config/automation/config/{automation_id}"
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                return yaml.dump(data, default_flow_style=False)
            else:
                logger.warning(
                    "REST config fetch failed (%s), falling back to file read", resp.status
                )

    # Fallback: read from mounted config directory
    path = f"{AUTOMATIONS_DIR}/{automation_id}.yaml"
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        raise RuntimeError(f"Cannot find YAML for automation: {automation_id}")


async def get_error_logs(automation_id: str, limit: int = 50) -> list[str]:
    """Pull recent logbook entries that mention this automation."""
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        url = f"{HA_URL}/api/logbook?entity_id={automation_id}&limit={limit}"
        async with session.get(url) as resp:
            if resp.status == 200:
                entries = await resp.json()
                return [
                    f"[{e.get('when','')}] {e.get('name','')}: {e.get('message','')}"
                    for e in entries
                ]
            return []


async def check_config() -> dict:
    """
    Ask HA to validate its current configuration.
    Returns {"result": "valid"} or {"result": "invalid", "errors": "..."}.
    """
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        url = f"{HA_URL}/api/config/core/check_config"
        async with session.post(url) as resp:
            return await resp.json()


async def reload_automations() -> bool:
    """Tell HA to reload all automation config from disk."""
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        url = f"{HA_URL}/api/services/automation/reload"
        async with session.post(url) as resp:
            return resp.status in (200, 201)


async def write_automation_yaml(automation_id: str, yaml_content: str) -> None:
    """Persist repaired YAML back via the HA REST config API."""
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        url = f"{HA_URL}/api/config/automation/config/{automation_id}"
        payload = yaml.safe_load(yaml_content)
        async with session.post(url, json=payload) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                raise RuntimeError(f"Failed to write automation config: {text}")


async def get_all_entity_ids() -> list[str]:
    """
    Fetch every known entity_id from HA.
    Used by the Root Cause Agent to detect stale entity references in YAML.
    """
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        url = f"{HA_URL}/api/states"
        async with session.get(url) as resp:
            states = await resp.json()
            return [s["entity_id"] for s in states]


async def get_available_domains() -> set[str]:
    """
    Get all available entity domains from Home Assistant.

    If USE_DYNAMIC_DOMAINS is enabled, fetches from /api/services.
    Falls back to hardcoded HA_DOMAINS_FALLBACK on API failure or if disabled.
    Results are cached for 1 hour to minimize API calls.
    """
    domains = HA_DOMAINS_FALLBACK
    source = "hardcoded fallback"

    # If dynamic fetching is disabled, return the hardcoded list
    if not USE_DYNAMIC_DOMAINS:
        logger.info("Dynamic domain fetching disabled; using hardcoded list")
        return domains
    
    # Check cache and return if still fresh
    now = time.time()
    if "domains" in _domains_cache and "expiry" in _domains_cache:
        if now < _domains_cache["expiry"]:
            return _domains_cache["domains"]
        
    # Fetch from HA API - Domains are the keys of the services dict returned by /api/services
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            url = f"{HA_URL}/api/services"
            async with session.get(url) as resp:
                if resp.status == 200:
                    services = await resp.json()
                    domains = set(services.keys())
                    source = "HA API"
    except Exception as e:
        logger.warning("Failed to fetch domains from HA API: %s; using fallback", e)
        source = "hardcoded fallback (API failed)"
        logger.info("Using domains from %s", source)
        return domains

    # Cache for 1 hour and update cache
    _domains_cache["domains"] = domains
    _domains_cache["expiry"] = now + 3600

    return domains


# ── WebSocket event stream ────────────────────────────────────────────────────

async def watch_automation_failures() -> AsyncGenerator[dict, None]:
    """
    Subscribe to the HA WebSocket event bus and yield any event that looks
    like an automation failure (call_service errors, automation trigger errors).
    
    Yields dicts with at minimum: {"automation_id": str, "error": str}
    """
    ws_url = f"{HA_URL.replace('http', 'ws')}/api/websocket"

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url) as ws:
            # Authenticate
            auth_msg = await ws.receive_json()
            assert auth_msg["type"] == "auth_required"
            await ws.send_json({"type": "auth", "access_token": HA_TOKEN})

            auth_result = await ws.receive_json()
            if auth_result["type"] != "auth_ok":
                raise RuntimeError("HA WebSocket authentication failed")

            logger.info("WebSocket authenticated — subscribing to events")

            # Subscribe to all events; filter client-side for failures
            await ws.send_json({
                "id": 1,
                "type": "subscribe_events",
                "event_type": "system_log_event"
            })

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    logger.debug("WebSocket event received: %s", data)
                    failure = _parse_failure_event(data)
                    if failure:
                        yield failure

                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    logger.warning("WebSocket closed — reconnecting in 5s")
                    await asyncio.sleep(5)
                    break


def _parse_failure_event(event: dict) -> dict | None:
    """
    Extract automation failure details from a raw Home Assistant system_log_event.

    Filters for ERROR or WARNING level events mentioning automation-related keywords,
    then attempts to extract the automation entity_id from the message.

    Args:
        event: Raw event dict from the HA WebSocket event bus.

    Returns:
        Dict with automation_id, error message, level, source, and raw_event, or None if
        the event is not an automation failure.
    """
    try:
        event_data = event.get("event", {}).get("data", {})
        message    = " ".join(event_data.get("message", []))
        level      = event_data.get("level", "")
        source     = event_data.get("source", ("", 0))

        if level not in ("ERROR", "WARNING"):
            logger.debug("Ignoring non-error log event (level=%s)", level)
            return None

        # Regex patterns covering all HA runtime failure modes for automations/scripts.
        # [\s_]? matches spaces, underscores, and CamelCase boundaries across HA versions.
        failure_patterns = [
            # Automation execution (helpers/script.py, components/automation/__init__.py)
            r"automation",
            r"error[\s_]?while[\s_]?executing[\s_]?automation",
            r"while[\s_]?executing[\s_]?automation",

            # Script execution (helpers/script.py)
            r"error[\s_]?executing[\s_]?script",
            r"timeout[\s_]?reached,?\s*abort[\s_]?script",
            r"maximum[\s_]?number[\s_]?of[\s_]?runs[\s_]?exceeded",
            r"already[\s_]?running",

            # Template rendering (components/automation/__init__.py, helpers/template)
            r"error[\s_]?rendering[\s_]?(variables|trigger[\s_]?variables|template|data[\s_]?template)",
            r"error[\s_]?parsing[\s_]?value",
            r"UndefinedError",
            r"TemplateError",

            # Condition evaluation (helpers/script.py)
            r"error[\s_]?in[\s_]?'(condition|choose|[^']+)'[\s_]?evaluation",

            # Entity / state lookup (components/automation/reproduce_state.py)
            r"entity[\s_]?not[\s_]?found",
            r"unknown[\s_]?entity",
            r"unable[\s_]?to[\s_]?find[\s_]?entity",
            r"invalid[\s_]?state[\s_]?specified[\s_]?for",

            # Service call failures (core.py, helpers/script.py)
            r"service[\s_]?not[\s_]?found",
            r"error[\s_]?executing[\s_]?service",
            r"unauthorized[\s_]?service[\s_]?called",
        ]
        if not any(re.search(p, message, re.IGNORECASE) for p in failure_patterns):
            return None

        # Try to extract entity_id from the name field or message
        name          = event_data.get("name", "")
        automation_id = _extract_automation_id(message, name)

        return {
            "automation_id": automation_id,
            "error":         message,
            "level":         level,
            "source":        source,
            "raw_event":     event,
        }

    except Exception:
        return None


def _extract_automation_id(message: str, name: str = "") -> str:
    """
    Extract automation entity_id from a log message or logger name.

    Checks in priority order:
      1. The logger name field (e.g. "homeassistant.components.automation.climate_away")
      2. The message text for "automation.<id>" patterns

    Args:
        message: Log message string to search.
        name:    Logger name from event data (optional).

    Returns:
        Extracted automation entity_id, or "automation.unknown" if not found.
    """
    logger.debug("Extracting automation_id from name=%r message=%s", name, message)

    # 1. Check logger name: homeassistant.components.automation.<name>
    if name:
        name_match = re.search(r"homeassistant\.components\.(automation\.[a-z0-9_]+)", name)
        if name_match:
            automation_id = name_match.group(1).replace("homeassistant.components.", "")
            logger.debug("Detected automation_id from logger name (HA path): %s", automation_id)
            return automation_id
        # Also handle plain "automation.<name>" in the name field
        plain_match = re.search(r"automation\.[a-z0-9_]+", name)
        if plain_match:
            logger.debug("Detected automation_id from logger name (plain): %s", plain_match.group(0))
            return plain_match.group(0)

    # 2. Fall back to searching the message text
    msg_match = re.search(r"automation\.[a-z0-9_]+", message)
    automation_id = msg_match.group(0) if msg_match else "automation.unknown"
    logger.debug("Detected automation_id from message text: %s", automation_id)
    return automation_id
