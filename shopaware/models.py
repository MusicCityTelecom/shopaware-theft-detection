"""Resolve bare checkpoint names into the appliance's persistent model directory."""
import os
from pathlib import Path


def model_path(value: str) -> str:
    directory = os.getenv('SHOPAWARE_MODEL_DIR')
    path = Path(value)
    if directory and path.name == value and path.suffix == '.pt':
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        return str(root / value)
    return value
