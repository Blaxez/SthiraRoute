"""Structured explanation of an optimization run — never invents routes."""

from __future__ import annotations

import json


def explain_run_text(explain_json: str | None, metrics_json: str | None, error: str | None) -> str:
    details = json.loads(explain_json or "{}")
    metrics = json.loads(metrics_json or "{}")
    base = details.get("summary") or error or "No explanation available."

    lines = [base]
    if metrics.get("matrix_source"):
        lines.append(f"Distance matrix: {metrics['matrix_source']}.")
    if metrics.get("vehicles_used") is not None:
        lines.append(f"Vehicles used: {metrics['vehicles_used']}.")
    if metrics.get("total_distance_km") is not None:
        lines.append(f"Total distance: {metrics['total_distance_km']} km.")
    if metrics.get("stability_score") is not None:
        lines.append(
            f"Stability score: {metrics['stability_score']} "
            f"(reassigned {metrics.get('shipments_reassigned', 0)})."
        )
    if details.get("no_entry_applied"):
        lines.append(
            "No-entry zones applied to: " + ", ".join(details["no_entry_applied"][:8])
        )
    if details.get("vehicles_used"):
        lines.append("Vehicles: " + ", ".join(details["vehicles_used"]))
    return " ".join(lines)
