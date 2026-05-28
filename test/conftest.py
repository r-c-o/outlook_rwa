"""Pytest configuration and shared fixtures for Outlook RWA tests."""
import pytest
import pandas as pd
from pathlib import Path


@pytest.fixture
def sample_dataframe():
    """Fixture providing a sample DataFrame for testing."""
    return pd.DataFrame({
        'entity_id': [1, 2, 3],
        'rwa_value': [100.0, 200.0, 300.0],
        'quarter': ['Q1', 'Q1', 'Q2'],
    })


@pytest.fixture
def sample_risk_weights():
    """Fixture providing sample risk weight mappings."""
    return {
        'HIGH_RISK': 0.5,
        'MEDIUM_RISK': 0.3,
        'LOW_RISK': 0.1,
    }


@pytest.fixture
def output_dir(tmp_path):
    """Fixture providing a temporary output directory."""
    return tmp_path / 'output'


@pytest.fixture(autouse=True)
def reset_output_dir(output_dir):
    """Automatically create output directory for each test."""
    output_dir.mkdir(parents=True, exist_ok=True)
    yield output_dir
