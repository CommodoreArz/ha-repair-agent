"""Analyzes why a Home Assistant automation failed using an LLM."""

import logging
import re

import yaml
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from config import LLM_BASE_URL, LLM_MODEL
from ha_client import get_available_domains
from state import RepairState

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert Home Assistant automation debugger.
You will be given:
  1. The YAML source of a failing automation
  2. Recent error log lines from Home Assistant
  3. A list of stale entity references (present in YAML but not in HA)

Your job is to identify the ROOT CAUSE of the failure in one concise paragraph.
Be specific: name exact fields, entity IDs, or service calls that are broken.
Do not suggest fixes yet — only diagnose.
"""


class RootCauseAgent:
    """Analyzes why a Home Assistant automation failed using an LLM."""

    def __init__(self):
        """Initialize the agent with an LM Studio LLM client."""
        self._llm = ChatOpenAI(
            base_url=LLM_BASE_URL,
            model=LLM_MODEL,
            temperature=0,
            api_key="not-needed",
        )

    async def run(self, state: RepairState) -> RepairState:
        """
        Diagnose the root cause of an automation failure.

        Args:
            state: Dict containing yaml_content, known_entity_ids, automation_id, and error_logs.

        Returns:
            Updated state dict with root_cause and stale_entities fields.
        """
        stale = await _find_stale_entities(
            state["yaml_content"],
            state["known_entity_ids"]
        )

        prompt = _build_prompt(
            automation_id=state["automation_id"],
            yaml_content=state["yaml_content"],
            error_logs=state["error_logs"],
            stale_entities=stale,
        )

        response = await self._llm.ainvoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ])

        root_cause = response.content.strip()
        logger.info("RootCauseAgent diagnosis:\n%s", root_cause)

        return {
            **state,
            "root_cause":     root_cause,
            "stale_entities": stale,
        }


async def _find_stale_entities(yaml_content: str, known_ids: list[str]) -> list[str]:
    """
    Identify entity references in automation YAML that don't exist in Home Assistant.

    Parses the YAML, extracts all entity_id-like patterns (domain.object_id), and
    returns those not found in the live entity list.

    Args:
        yaml_content: Raw YAML string of the automation.
        known_ids: List of valid entity_id strings from Home Assistant.

    Returns:
        List of stale entity_ids (those in YAML but not in known_ids).
    """
    ha_domains = await get_available_domains()
    known_set = set(known_ids)
    try:
        raw = yaml.safe_load(yaml_content)
        text = yaml.dump(raw)
    except yaml.YAMLError:
        text = yaml_content   # fall back to raw string search

    # entity_ids follow the pattern: domain.object_id
    candidates = set(re.findall(r'\b[a-z_]+\.[a-z0-9_]+\b', text))

    # Only flag candidates that look like real domains
    entity_refs = {c for c in candidates if c.split(".")[0] in ha_domains}
    stale = sorted(entity_refs - known_set)

    if stale:
        logger.warning("Stale entity references found: %s", stale)
    return stale


def _build_prompt(
    automation_id: str,
    yaml_content: str,
    error_logs: list[str],
    stale_entities: list[str],
) -> str:
    """
    Construct the prompt for the root cause analysis LLM.

    Args:
        automation_id: The Home Assistant automation entity_id.
        yaml_content: The raw YAML of the failing automation.
        error_logs: Recent error log entries mentioning this automation.
        stale_entities: Entity references present in YAML but not in Home Assistant.

    Returns:
        A formatted prompt string for the LLM.
    """
    logs_text = "\n".join(error_logs[-20:]) or "No recent logs found."
    stale_text = ", ".join(stale_entities) if stale_entities else "None detected."

    return f"""Automation ID: {automation_id}

=== YAML SOURCE ===
{yaml_content}

=== RECENT ERROR LOGS ===
{logs_text}

=== STALE ENTITY REFERENCES ===
{stale_text}

Diagnose the root cause of this automation failure."""
