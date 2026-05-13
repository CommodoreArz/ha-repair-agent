"""Supervisor — the always-on process that watches the Home Assistant event stream
and fires the LangGraph repair workflow whenever an automation failure is detected.
"""

import asyncio
import logging
import time

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel

from ha_client import watch_automation_failures
from graph import build_graph
from state import RepairState
from config import DEBUG_LOGGING

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if DEBUG_LOGGING else logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True, markup=True)],
)
logger  = logging.getLogger(__name__)
console = Console()

# ── Dedup: don't fire multiple repair jobs for the same automation in quick succession
COOLDOWN_SECONDS = 60
_in_flight: dict[str, float] = {}


async def handle_failure(graph, failure_event: dict) -> None:
    """
    Process a single automation failure through the repair workflow.

    Enforces a cooldown period to prevent duplicate repair jobs for the same automation,
    displays the failure details, runs the full repair graph, and reports the outcome.

    Args:
        graph: Compiled LangGraph repair workflow.
        failure_event: Dict with automation_id, error, and related event data.
    """
    automation_id = failure_event["automation_id"]
    now = time.monotonic()

    # Cooldown guard
    if automation_id in _in_flight:
        elapsed = now - _in_flight[automation_id]
        if elapsed < COOLDOWN_SECONDS:
            logger.info(
                "Skipping duplicate failure for %s (cooldown: %.0fs remaining)",
                automation_id, COOLDOWN_SECONDS - elapsed
            )
            return
    _in_flight[automation_id] = now

    console.print(Panel(
        f"[bold red]⚡ Automation failure detected[/bold red]\n"
        f"Entity: [cyan]{automation_id}[/cyan]\n"
        f"Error:  {failure_event['error'][:200]}",
        title="YAML Repair Agent",
    ))

    initial_state: RepairState = {
        "automation_id":    automation_id,
        "error":            failure_event["error"],
        "yaml_content":     "",
        "error_logs":       [],
        "known_entity_ids": [],
        "root_cause":       "",
        "stale_entities":   [],
        "repaired_yaml":    "",
        "repair_notes":     "",
        "validation_ok":    False,
        "validation_error": "",
        "repair_attempts":  0,
        "status":           "",
        "summary":          "",
    }

    try:
        final_state = await graph.ainvoke(initial_state)

        status = final_state.get("status", "unknown")
        summary = final_state.get("summary", "")

        color = "green" if status == "deployed" else "yellow"
        console.print(Panel(summary, title=f"[{color}]Result: {status.upper()}[/{color}]"))

    except Exception:
        logger.exception("Repair graph crashed for %s", automation_id)
    finally:
        # Release cooldown after job finishes
        _in_flight.pop(automation_id, None)


async def supervisor() -> None:
    """
    Main supervisor loop that watches Home Assistant for automation failures.

    Builds the repair graph, listens to the HA WebSocket event stream indefinitely,
    and spawns handle_failure tasks for each detected failure. Automatically reconnects
    if the WebSocket drops.
    """

    graph = build_graph()

    # Re-connect indefinitely if the WebSocket drops
    while True:
        try:
            async for failure_event in watch_automation_failures():
                # Fire repair graph as a background task (non-blocking)
                asyncio.create_task(handle_failure(graph, failure_event))

        except Exception:
            logger.exception("Supervisor lost connection — retrying in 10s")
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(supervisor())
