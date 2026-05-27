"""
Run this script inside the active conda environment to print everything needed
to recreate it: Python version, conda env name, and versions of all packages
directly used by the outlook_rwa pipeline.

Usage:
    conda activate <env_name>
    python scripts/print_env.py
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

# toml alternatives: fall back to tomllib (stdlib, Python 3.11+) or tomli
TOML_ALTERNATIVES = ["toml", "tomllib", "tomli"]


def _get_pkg_version(pkg: str) -> str | None:
    """Try to import a package and return its version, or None if not available."""
    try:
        mod = __import__(pkg)
        return getattr(mod, "__version__", "unknown")
    except ImportError:
        return None


print("=" * 50)
print(f"Python : {sys.version}")
print(f"Conda env: {os.environ.get('CONDA_DEFAULT_ENV', '(not in conda)')}")
print("=" * 50)

for pkg in REQUIRED:
    if pkg == "toml":
        # Try toml first, then tomllib (stdlib), then tomli
        found = False
        for alt in TOML_ALTERNATIVES:
            ver = _get_pkg_version(alt)
            if ver is not None:
                label = f"toml ({alt})" if alt != "toml" else "toml"
                print(f"  {label:<12} {ver}")
                found = True
                break
        if not found:
            print(f"  {'toml':<12} NOT INSTALLED (tried: {', '.join(TOML_ALTERNATIVES)})")
    else:
        ver = _get_pkg_version(pkg)
        if ver is not None:
            print(f"  {pkg:<12} {ver}")
        else:
            print(f"  {pkg:<12} NOT INSTALLED")

print("=" * 50)
print("\nTo recreate, run:")
print("  conda create -n <env_name> python=<version>")
print("  conda activate <env_name>")

_installed = []
for _p in REQUIRED:
    if _p == "toml":
        # Report whichever toml alternative is actually installed
        for _alt in TOML_ALTERNATIVES:
            _ver = _get_pkg_version(_alt)
            if _ver is not None and _ver != "unknown":
                _installed.append(f"{_alt}=={_ver}")
                break
    else:
        _ver = _get_pkg_version(_p)
        if _ver is not None and _ver != "unknown":
            _installed.append(f"{_p}=={_ver}")

print("  pip install " + " ".join(_installed))
