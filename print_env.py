"""
Run this script inside the active conda environment to print everything needed
to recreate it: Python version, conda env name, and versions of all packages
directly used by step1_model_convergence.py and step2_outlook_rwa.py.

Usage:
    conda activate <env_name>
    python print_env.py
"""
import sys
import os

REQUIRED = [
    "numpy",
    "pandas",
    "polars",
    "toml",
    "openpyxl",
    "pyarrow",
]

print("=" * 50)
print(f"Python : {sys.version}")
print(f"Conda env: {os.environ.get('CONDA_DEFAULT_ENV', '(not in conda)')}")
print("=" * 50)

for pkg in REQUIRED:
    try:
        mod = __import__(pkg)
        version = getattr(mod, "__version__", "unknown")
        print(f"  {pkg:<12} {version}")
    except ImportError:
        print(f"  {pkg:<12} NOT INSTALLED")

print("=" * 50)
print("\nTo recreate, run:")
print("  conda create -n <env_name> python=<version>")
print("  conda activate <env_name>")
print("  pip install " + " ".join(f"{p}=={__import__(p).__version__}" for p in REQUIRED if __import__(p, globals(), locals(), [], 0) is not None))
