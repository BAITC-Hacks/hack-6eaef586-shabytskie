"""Pure helpers for the human approval step."""
from __future__ import annotations

import pandas as pd


def review_signature(review_state: dict, run_id: str) -> str:
    values = (
        (str(sku), item["selected"], float(item["quantity"]), str(item["comment"]).strip())
        for (state_run_id, sku), item in review_state.items()
        if state_run_id == run_id
    )
    return repr(sorted(values))


def missing_override_comments(edited: pd.DataFrame) -> list[str]:
    """Return SKU values whose manually changed quantity has no explanation."""
    changed = edited[edited["final_quantity"].astype(float) != edited["recommended_order"].astype(float)]
    missing = changed[changed["comment"].fillna("").astype(str).str.strip().eq("")]
    return missing["sku"].astype(str).tolist()


def build_draft(edited: pd.DataFrame, metadata: dict) -> pd.DataFrame:
    draft = edited[edited.selected & (edited.final_quantity > 0)].copy()
    draft = draft.rename(
        columns={
            "recommended_order": "original_recommended_order",
            "final_quantity": "approved_quantity",
        }
    )
    draft["run_id"] = metadata.get("run_id")
    draft["engine_mode"] = metadata.get("engine_mode")
    draft["data_origin"] = metadata.get("data_origin")
    if "supplier" in draft:
        draft = draft.sort_values(["supplier", "sku"])
    return draft

