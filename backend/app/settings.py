"""Admin-editable settings, stored in the `settings` table.

- "company": own master data (seller + payment). Falls back to company.yaml
  (or the example file) until an admin saves it in the UI.
- "idoc":    SAP ECC IDoc inbox connector (on/off, output format, interval).
             Falls back to the IDOC_* environment variables.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .company import Company, load_company
from .db import get_setting


class IdocSettings(BaseModel):
    enabled: bool = True
    profile: Literal["zugferd", "xrechnung"] = "zugferd"
    interval_seconds: int = Field(10, ge=5, le=3600)


def get_company(session: Session) -> Company:
    stored = get_setting(session, "company")
    return Company.model_validate(stored) if stored else load_company()


def idoc_defaults_from_env() -> IdocSettings:
    return IdocSettings(
        profile="xrechnung" if os.environ.get("IDOC_PROFILE") == "xrechnung" else "zugferd",
        interval_seconds=max(5, int(float(os.environ.get("IDOC_INTERVAL", "10")))),
    )


def get_idoc_settings(session: Session) -> IdocSettings:
    stored = get_setting(session, "idoc")
    return IdocSettings.model_validate(stored) if stored else idoc_defaults_from_env()
