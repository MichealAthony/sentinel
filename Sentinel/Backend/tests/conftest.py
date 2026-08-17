"""
Shared fixtures for the Sentinel backend test suite.
"""
import numpy as np
import pytest


@pytest.fixture
def make_embedding():
    """Factory that returns normalised float32 embeddings of a given dim."""
    def _make(seed=42, dim=512):
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(dim).astype("float32")
        n = np.linalg.norm(v)
        return v / n if n > 0 else v
    return _make


def norm(v):
    n = np.linalg.norm(v)
    return v / n if n > 0 else v
