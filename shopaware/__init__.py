"""ShopAware application modules."""
from pathlib import Path

__version__ = (Path(__file__).resolve().parent.parent / 'VERSION').read_text(encoding='utf-8').strip()
