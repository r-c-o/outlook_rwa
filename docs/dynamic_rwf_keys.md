# Dynamic RWF Waterfall Keys — Design Plan

## Request

Can you create a plan & design for the below files on how to:

### 1. Dynamic RWF Waterfall Keys (runtime parameters)

Want dynamic rwf waterfall keys that can be modified using runtime parameters.

References to the variables the rwf waterfall keys represent is below:

```python
cg_waterfall_rwf_lookup_1, cg_waterfall_rwf_lookup_2, cg_waterfall_rwf_lookup_3,
cg_waterfall_rwf_lookup_4, cg_waterfall_rwf_lookup_5 = create_key_pivots(
    credit_risk_convergence_cg, ADV_CG_TOTAL_RWA_AMT
)

cbna_waterfall_rwf_lookup_1, cbna_waterfall_rwf_lookup_2, cbna_waterfall_rwf_lookup_3,
cbna_waterfall_rwf_lookup_4, cbna_waterfall_rwf_lookup_5 = create_key_pivots(
    credit_risk_convergence_cbna, ADV_CBNA_TOTAL_RWA_AMT
)
```

### 2. Collapse Repeated "5-Key Logic" Into Loops in Step 1 File

Rather than repeating merge/lookup logic for key1 through key5 as five separate blocks,
collapse into a loop over the 5 key dataframes.

### 3. Simplify Key Construction

Simplify key construction by reducing boilerplate in `build_outlook_key_strings`.

---

## Files Affected

- `src/main/tools/step1_model_convergence.py`
- `src/main/tools/step2_outlook_rwa.py`

---

## Notes

If you need any additional information let me know.
Try not to assume anything related to the data, scripts, or transformations.
If assumptions must be made, make them explicit in your response.
