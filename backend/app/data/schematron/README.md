# Official Schematron rules (vendored)

Compiled XSLT versions of the official business rules, run by `app/schematron.py`
(Saxon via `saxonche`). They are bundled because the worker has no internet.

| File | Rules | Version | Source | Licence |
|---|---|---|---|---|
| `EN16931-CII-validation.xslt` | EN 16931 (CEN/TC 434), CII syntax | 1.3.17 | <https://github.com/ConnectingEurope/eInvoicing-EN16931/releases/tag/validation-1.3.17> (`en16931-cii-1.3.17.zip`, `xslt/`) | EUPL 1.2 |
| `XRechnung-CII-validation.xsl` | XRechnung 3.0.2 (KoSIT), CII syntax | 2.6.0 | <https://github.com/itplr-kosit/xrechnung-schematron/releases/tag/v2.6.0> (`xrechnung-3.0.2-schematron-2.6.0.zip`, `schematron/cii/`) | Apache 2.0 |

ZUGFeRD / Factur-X (profile EN 16931) is checked against the EN 16931 rules,
XRechnung against EN 16931 **and** the XRechnung rules – the same combination
the KoSIT validator uses.

## Updating

1. Download the new release zips from the links above.
2. Replace the two files here (keep the file names), update the table.
3. `pytest tests/test_schematron.py` – and read the changelog: new rules may
   need a change in `app/export/cii.py`.
