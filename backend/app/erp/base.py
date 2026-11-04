"""ERP adapter contract.

An ERP adapter is just another *source* of `Invoice` objects. The rest of the
app (validation, export, UI) never knows which ERP is behind it, so moving
from SAP ECC to S/4HANA later means writing one new adapter.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from ..model import Invoice


@runtime_checkable
class ErpAdapter(Protocol):
    name: str

    def list_new_invoices(self, since: datetime | None = None) -> list[str]:
        """IDs of invoices that are ready for conversion (newer than `since`)."""
        ...

    def get_invoice(self, invoice_id: str) -> Invoice:
        """Full invoice incl. buyer master data, ready for validation/export."""
        ...
