"""Shared fixtures for job-applier tests."""
import sys
from pathlib import Path

# Ensure scripts/ is always on the path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
