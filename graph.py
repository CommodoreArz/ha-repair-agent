"""LangGraph state machine for the YAML Repair multi-agent system.

Flow:
  diagnose → root_cause → repair → validate ──(valid)──→ deploy → END
                                       ↑                    │
                                       └──(invalid, retry)──┘

                          escalate → END  (max retries exceeded)
"""

import logging
from pathlib import Path

from langgraph.graph import END, StateGraph

from agents.diagnostics import DiagnosticsAgent
from agents.root_cause import RootCauseAgent
from agents.yaml_repair import YAMLRepairAgent
from agents.validator import ValidatorAgent
from config import MAX_REPAIR_ATTEMPTS
from state import RepairState

logger = logging.getLogger(__name__)


# ── Agent instances ──────────────────────────────────────────────────────────

_diagnostics = DiagnosticsAgent()
_root_cause = RootCauseAgent()
_yaml_repair = YAMLRepairAgent()
_validator = ValidatorAgent()


# ── Node functions ───────────────────────────────────────────────────────────


async def diagnose(state: RepairState) -> RepairState:
    """Fetch automation YAML, logs, and known entity IDs from Home Assistant."""
    logger.info("[DIAGNOSE] Fetching YAML + logs for %s", state["automation_id"])
    return await _diagnostics.run(state)


async def root_cause(state: RepairState) -> RepairState:
    """Analyze why the automation failed using LLM reasoning."""
    logger.info("[ROOT CAUSE] Reasoning about failure for %s", state["automation_id"])
    return await _root_cause.run(state)


async def repair(state: RepairState) -> RepairState:
    """Generate corrected automation YAML using LLM, incrementing repair attempt counter."""
    attempt = state.get("repair_attempts", 0) + 1
    logger.info("[REPAIR] Attempt %d for %s", attempt, state["automation_id"])
    state = await _yaml_repair.run(state)
    state["repair_attempts"] = attempt
    return state


async def validate(state: RepairState) -> RepairState:
    """Validate repaired YAML via local checks and Home Assistant dry-run."""
    logger.info("[VALIDATE] Checking repaired YAML")
    return await _validator.run(state)


async def deploy(state: RepairState) -> RepairState:
    """Write repaired YAML to Home Assistant and reload automations."""
    from ha_client import write_automation_yaml, reload_automations

    logger.info("[DEPLOY] Writing repaired automation and reloading HA")
    await write_automation_yaml(state["automation_id"], state["repaired_yaml"])
    await reload_automations()

    state["status"] = "deployed"
    state["summary"] = (
        f"✅ Successfully repaired and deployed {state['automation_id']}.\n"
        f"Root cause: {state['root_cause']}\n"
        f"Repair notes: {state['repair_notes']}"
    )
    return state


async def escalate(state: RepairState) -> RepairState:
    """Mark automation as unable to auto-repair and flag for human review."""
    logger.warning(
        "[ESCALATE] Could not auto-repair %s after %d attempts",
        state["automation_id"],
        state.get("repair_attempts", 0),
    )
    state["status"] = "escalated"
    state["summary"] = (
        f"⚠️ Could not auto-repair {state['automation_id']} after "
        f"{state['repair_attempts']} attempts.\n"
        f"Last validation error: {state.get('validation_error', 'unknown')}\n"
        f"Human review required."
    )
    return state


# ── Routing ───────────────────────────────────────────────────────────────────


def route_after_validate(state: RepairState) -> str:
    """
    Determine next step after validation.

    Routes to deploy if valid, repair if invalid and retries remain, or escalate
    if max repair attempts exceeded.

    Args:
        state: Current repair state.

    Returns:
        Name of next node: "deploy", "repair", or "escalate".
    """
    if state["validation_ok"]:
        return "deploy"
    if state.get("repair_attempts", 0) >= MAX_REPAIR_ATTEMPTS:
        return "escalate"
    return "repair"


# ── Graph assembly ────────────────────────────────────────────────────────────


def build_graph() -> StateGraph:
    """
    Assemble the LangGraph state machine for the repair workflow.

    Creates nodes for each agent, defines edges, sets entry point, and adds
    conditional routing from the validate node.

    Returns:
        Compiled LangGraph graph ready for invocation.
    """
    g = StateGraph(RepairState)

    g.add_node("diagnose", diagnose)
    g.add_node("root_cause", root_cause)
    g.add_node("repair", repair)
    g.add_node("validate", validate)
    g.add_node("deploy", deploy)
    g.add_node("escalate", escalate)

    g.set_entry_point("diagnose")

    g.add_edge("diagnose", "root_cause")
    g.add_edge("root_cause", "repair")
    g.add_edge("repair", "validate")

    g.add_conditional_edges(
        "validate",
        route_after_validate,
        {"deploy": "deploy", "repair": "repair", "escalate": "escalate"},
    )

    g.add_edge("deploy", END)
    g.add_edge("escalate", END)

    return g.compile()


def visualize_graph(output_path: str = "repair_workflow.png") -> None:
    """
    Generate a PNG visualization of the repair workflow graph.

    Creates a visual representation of the LangGraph state machine and saves it
    as a PNG file.

    Args:
        output_path: Path where the PNG file will be saved (default: 'repair_workflow.png').
    """
    try:
        graph = build_graph()

        # Get the visualization as a PIL Image
        png_data = graph.get_graph().draw_mermaid_png()

        # Write to file
        path = Path(output_path)
        path.write_bytes(png_data)

        logger.info(f"Graph visualization saved to {output_path}")
        print(f"✅ Graph visualization saved to {output_path}")

    except ImportError as e:
        logger.error(f"Missing dependency for visualization: {e}")
        print(f"❌ Error: {e}")
        print("Install graphviz: pip install graphviz")
    except Exception as e:
        logger.error(f"Failed to generate graph visualization: {e}")
        print(f"❌ Error generating visualization: {e}")


if __name__ == "__main__":
    visualize_graph()
