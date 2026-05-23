# %% [markdown]
# # Step 2: Outlook RWA
#
# Reads Step 1 outputs, applies PUG/PMF mapping, generates upload templates
# (CG and CBNA), and writes the final control file.
#
# **How to use:** Update the PARAMETERS cell below, then run all cells top-to-bottom.

# %%
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import toml
import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from dateutil.relativedelta import relativedelta
import polars as pl
from constants import *
import time

from functions import *
pd.set_option("display.max_columns", 500)

# %%
start_time = time.time()

# ==============================================================================
# PARAMETERS — Update these before each run
# ==============================================================================

config_path = Path(
    r"C:\Users\rl09895\project_home\outlook-rwa-release\outlook-rwa-app\src\main\tools\config.toml"
)
config        = toml.load(config_path)
global_params = config["global_params"]

QO                    = global_params["QO"]
period                = global_params["period"]
input_dir             = Path(global_params["input_dir"])
output_dir            = Path(global_params["output_dir"])
model_convergence_dir = Path(global_params["model_convergence_dir"])
schema_csv            = Path(global_params["schema_registry_path"])

# Step 2 input filenames from config
step2_inputs = {
    spec["logical_name"]: input_dir / spec["filename"]
    for spec in config["inputs"]["step2"]
}

# Step 1 outputs (produced by step1_model_convergence.py)
step1_outputs = config["outputs"]["step1"]
cg_outlook_path   = model_convergence_dir / step1_outputs["cg_outlook"]
cbna_outlook_path = model_convergence_dir / step1_outputs["cbna_outlook"]

check_input_files_exist(
    [cg_outlook_path, cbna_outlook_path] + list(step2_inputs.values())
)

# %%
# Load Step 1 outputs
cg_outlook   = pd.read_excel(cg_outlook_path)
cbna_outlook = pd.read_excel(cbna_outlook_path)

# Load Step 2 mapping files
pug_mapping      = pd.read_excel(step2_inputs["pug_mapping"])
pmit_rwa_mapping = pd.read_excel(step2_inputs["pmit_rwa_mapping"])
cg_addon         = pd.read_excel(step2_inputs["cg_addon"])
cbna_addon       = pd.read_excel(step2_inputs["cbna_addon"])

print(f"CG outlook rows:   {len(cg_outlook):,}")
print(f"CBNA outlook rows: {len(cbna_outlook):,}")
print(f"⏱ Loaded inputs in {time.time() - start_time:.2f}s")

# ==============================================================================
# Step 2 business logic (apply PUG/PMF mapping, generate templates, etc.)
# ==============================================================================
# %%

# ... (Step 2 transformation logic applied here) ...

# ==============================================================================
# Write outputs
# ==============================================================================
# %%
output_dir.mkdir(parents=True, exist_ok=True)

out_specs = config["outputs"]["step2"]
cg_outlook.to_excel(output_dir / out_specs["cg_upload_full"],   index=False)
cbna_outlook.to_excel(output_dir / out_specs["cbna_upload_full"], index=False)

print(f"✅ Outputs written to {output_dir}")
print(f"⏱ Total elapsed: {time.time() - start_time:.2f} seconds")
