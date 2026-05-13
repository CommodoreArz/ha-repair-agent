"""Collects initial diagnostic data for a failing automation."""

import logging

from ha_client import get_automation_yaml, get_error_logs, get_all_entity_ids
from state import RepairState

logger = logging.getLogger(__name__)


class DiagnosticsAgent:
    """Collects initial diagnostic data for a failing automation."""

    async def run(self, state: RepairState) -> RepairState:
        """
        Gather automation YAML, recent error logs, and known entity IDs.

        Args:
            state: Dict containing automation_id.

        Returns:
            Updated state dict with yaml_content, error_logs, and known_entity_ids.
        """
        automation_id = state["automation_id"]

        yaml_content, error_logs, known_entity_ids = await _gather(automation_id)

        return {
            **state,
            "yaml_content":     yaml_content,
            "error_logs":       error_logs,
            "known_entity_ids": known_entity_ids,
        }


async def _gather(automation_id: str) -> tuple[str, list[str], list[str]]:
    """
    Concurrently fetch diagnostic data from Home Assistant.

    Args:
        automation_id: The Home Assistant automation entity_id.

    Returns:
        Tuple of (yaml_content, error_logs, known_entity_ids).
    """
    import asyncio
    yaml_content, error_logs, known_entity_ids = await asyncio.gather(
        get_automation_yaml(automation_id),
        get_error_logs(automation_id),
        get_all_entity_ids(),
    )
    logger.debug(
        "DiagnosticsAgent: fetched %d log lines, %d known entities",
        len(error_logs), len(known_entity_ids)
    )
    return yaml_content, error_logs, known_entity_ids
