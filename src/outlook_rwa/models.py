"""Data models for the Outlook RWA pipeline.

`EntityBundle` collects everything that distinguishes one reportable entity
(CG vs. CBNA) plus the per-stage DataFrames the pipeline produces for it. It
replaces the ~20 paired ``cg_*`` / ``cbna_*`` variable blocks in pipeline.py
with a single object iterated in a ``for entity in entities:`` loop.

The bundle is a plain mutable dataclass: stage functions read the inputs they
need off the bundle and write their outputs back onto it. Adding a third entity
is then just appending one more ``EntityBundle`` to the list.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class EntityBundle:  # pylint: disable=too-many-instance-attributes
    """All inputs, identity, and per-stage outputs for one reportable entity.

    The attribute count is high by design: the bundle deliberately collects
    every per-stage DataFrame the pipeline produces for one entity so the
    orchestrator can loop instead of carrying paired cg_*/cbna_* variables.

    Attributes:
        name: Entity short name, "CG" or "CBNA".
        adv_rwa_col: Entity-specific advanced RWA column in convergence
            (ADV_CG_TOTAL_RWA_AMT or ADV_CBNA_TOTAL_RWA_AMT).
        entity_filter_col: Convergence flag column whose value 'Y' selects this
            entity's rows (REPORTABLE_ENTITY_IS_CG or REPORTABLE_ENTITY_IS_CBNA).
        raw_entity_code: Code written to the "Entity" column in stage 2
            ("BA" for CG, "BB" for CBNA).
        balance_sheet: Raw balance-sheet DataFrame for this entity.
        adjustments: Raw adjustments DataFrame for this entity.

    Stage-1 outputs (set as the pipeline runs):
        credit_risk: Credit-risk convergence rows for this entity.
        addon_markets: Markets credit-risk add-on rows / pivot.
        addon_non_waterfall: Non-credit-risk non-waterfall add-on rows / pivot.
        waterfall_lookups: List of per-key RWF pivot tables.
        outlook: Long-format outlook DataFrame with computed SA/AA/ERBA RWA.
        addon_all: Concatenated Markets + non-waterfall add-on rows.

    Stage-2 outputs:
        frm_output: Formatted FRM output prior to the upload pivot.
        upload_template: Final wide upload-template DataFrame.
        raw_data: Pre-legacy-breakout raw data DataFrame.
    """

    name: str
    adv_rwa_col: str
    entity_filter_col: str
    raw_entity_code: str
    balance_sheet: pd.DataFrame
    adjustments: pd.DataFrame

    # Stage-1 outputs
    credit_risk: pd.DataFrame | None = None
    addon_markets: pd.DataFrame | None = None
    addon_non_waterfall: pd.DataFrame | None = None
    waterfall_lookups: list = field(default_factory=list)
    outlook: pd.DataFrame | None = None
    addon_all: pd.DataFrame | None = None

    # Stage-2 outputs
    frm_output: pd.DataFrame | None = None
    upload_template: pd.DataFrame | None = None
    raw_data: pd.DataFrame | None = None
