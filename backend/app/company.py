"""Own-company master data (seller), loaded from YAML.

Lookup order: $ERECHNUNG_COMPANY_FILE, config/company.yaml, config/company.example.yaml.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .model import Party, PaymentInfo

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class Company(BaseModel):
    seller: Party = Field(default_factory=Party)
    payment: PaymentInfo = Field(default_factory=PaymentInfo)


def company_file() -> Path:
    env = os.environ.get("ERECHNUNG_COMPANY_FILE")
    if env:
        return Path(env)
    real = CONFIG_DIR / "company.yaml"
    return real if real.exists() else CONFIG_DIR / "company.example.yaml"


@lru_cache(maxsize=1)
def load_company() -> Company:
    with company_file().open(encoding="utf-8") as fh:
        return Company.model_validate(yaml.safe_load(fh) or {})
