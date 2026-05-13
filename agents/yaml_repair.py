"""Rewrites broken Home Assistant automation YAML based on root cause diagnosis."""

import logging

import yaml
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from config import LLM_BASE_URL, LLM_MODEL
from state import RepairState

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert Home Assistant automation engineer.
You will be given:
  1. A broken automation YAML
  2. A root cause diagnosis
  3. A list of valid entity IDs currently in Home Assistant
  4. (On retries) the error from the previous repair attempt

Your task: output a corrected, complete Home Assistant automation YAML.

Rules:
  - Output ONLY valid YAML. No markdown fences, no commentary outside the YAML.
  - Never invent entity IDs — only use IDs from the provided valid entity list.
  - Preserve the original intent of the automation.
  - Add a comment block at the top documenting what you changed and why.
  - The YAML must be a single automation object (not a list).
"""


class YAMLRepairAgent:
    """Rewrites broken Home Assistant automation YAML based on root cause diagnosis."""

    def __init__(self):
        """Initialize the agent with an LM Studio LLM client."""
        self._llm = ChatOpenAI(
            base_url=LLM_BASE_URL,
            model=LLM_MODEL,
            temperature=0.1,
            api_key="not-needed",
        )

    async def run(self, state: RepairState) -> RepairState:
        """
        Generate a corrected automation YAML.

        Uses the root cause diagnosis, known entity IDs, and optional previous
        validation errors to produce a valid replacement YAML.

        Args:
            state: Dict containing yaml_content, root_cause, known_entity_ids,
                   and optionally validation_error and repair_attempts.

        Returns:
            Updated state dict with repaired_yaml and repair_notes fields.
        """
        prompt = _build_prompt(state)

        response = await self._llm.ainvoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ])

        raw = response.content.strip()

        # Strip accidental markdown fences if the model adds them
        repaired_yaml, notes = _extract_yaml_and_notes(raw)

        logger.info("YAMLRepairAgent produced repaired YAML (%d chars)", len(repaired_yaml))

        return {
            **state,
            "repaired_yaml": repaired_yaml,
            "repair_notes":  notes,
        }


def _build_prompt(state: dict) -> str:
    """
    Construct the prompt for the YAML repair LLM.

    Includes the broken YAML, root cause diagnosis, valid entity IDs, and any
    previous validation errors to enable self-correction on retries.

    Args:
        state: Dict containing yaml_content, root_cause, known_entity_ids,
               stale_entities, and optional validation_error.

    Returns:
        A formatted prompt string for the LLM.
    """
    known_ids = state.get("known_entity_ids", [])
    # Only include entity IDs relevant to the automation's domain context
    # to keep the prompt manageable
    relevant_ids = _filter_relevant_entities(state["yaml_content"], known_ids)

    previous_error = ""
    if state.get("validation_error") and state.get("repair_attempts", 0) > 0:
        previous_error = f"""
=== PREVIOUS REPAIR ATTEMPT FAILED WITH ===
{state['validation_error']}
Please fix this additional error in your new attempt.
"""

    return f"""=== BROKEN AUTOMATION YAML ===
{state['yaml_content']}

=== ROOT CAUSE DIAGNOSIS ===
{state['root_cause']}

=== STALE ENTITIES TO REPLACE ===
{', '.join(state.get('stale_entities', [])) or 'None'}

=== VALID ENTITY IDs (use only these) ===
{chr(10).join(relevant_ids)}
{previous_error}
Output the corrected automation YAML now:"""


def _filter_relevant_entities(yaml_content: str, all_ids: list[str]) -> list[str]:
    """
    Filter entity IDs to only those relevant to the automation's domain context.

    Reduces prompt size by including only entities whose domains appear in the YAML.
    Capped at 150 to keep context manageable.

    Args:
        yaml_content: The automation YAML to analyze for domain references.
        all_ids: Full list of available entity IDs from Home Assistant.

    Returns:
        Filtered list of relevant entity IDs (up to 150).
    """
    import re
    domains_in_yaml = set(re.findall(r'\b([a-z_]+)\.[a-z0-9_]+\b', yaml_content))
    relevant = [e for e in all_ids if e.split(".")[0] in domains_in_yaml]
    return relevant[:150]


def _extract_yaml_and_notes(raw: str) -> tuple[str, str]:
    """
    Extract valid YAML and repair notes from LLM output.

    Removes markdown fences (```yaml), extracts leading comments as human-readable
    notes, and validates that the result is parseable YAML.

    Args:
        raw: Raw text output from the LLM.

    Returns:
        Tuple of (cleaned_yaml, notes_string).
    """
    import re

    # Strip markdown fences
    cleaned = re.sub(r"^```(?:yaml)?\s*", "", raw, flags=re.MULTILINE)
    cleaned = re.sub(r"^```\s*$", "", cleaned, flags=re.MULTILINE).strip()

    # Extract leading comment block as human-readable notes
    comment_lines = []
    for line in cleaned.splitlines():
        if line.startswith("#"):
            comment_lines.append(line.lstrip("# ").strip())
        else:
            break
    notes = " | ".join(comment_lines) if comment_lines else "No notes provided."

    # Validate it's parseable YAML before returning
    try:
        yaml.safe_load(cleaned)
    except yaml.YAMLError as e:
        logger.warning("YAMLRepairAgent produced unparseable YAML: %s", e)
        # Return as-is; the ValidatorAgent will catch it

    return cleaned, notes
