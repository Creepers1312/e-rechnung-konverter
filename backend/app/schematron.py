"""Official business-rule validation of the generated CII XML (Schematron).

Runs the compiled XSLT rule sets in `app/data/schematron/` with Saxon
(`saxonche`, XSLT 3.0): EN 16931 for both profiles, plus the KoSIT XRechnung
rules for XRechnung. This is the same check the KoSIT validator performs.

The result is turned into `Issue`s: `fatal`/`error` asserts block the export,
`warning`/`information` are shown but do not block. If the check itself cannot
run, that is an error too – an unchecked invoice is never released.
"""

from __future__ import annotations

import logging
import multiprocessing
import re
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any

from lxml import etree

from .export.cii import Profile
from .validation import Issue

log = logging.getLogger(__name__)

RULES_DIR = Path(__file__).parent / "data" / "schematron"
RULE_SETS: dict[str, Path] = {
    "EN 16931": RULES_DIR / "EN16931-CII-validation.xslt",
    "XRechnung": RULES_DIR / "XRechnung-CII-validation.xsl",
}
PROFILE_RULE_SETS: dict[Profile, tuple[str, ...]] = {
    "zugferd": ("EN 16931",),
    "xrechnung": ("EN 16931", "XRechnung"),
}

SVRL = "http://purl.oclc.org/dsdl/svrl"
BLOCKING_FLAGS = {"fatal", "error"}
_ID_PREFIX = re.compile(r"^\s*\[[^\]]+\]\s*-?\s*")

# Saxon (a GraalVM native image) is not reliable off the main thread – it aborts the
# whole process ("wrong IsolateThread"), and FastAPI runs sync endpoints in a thread
# pool. So Saxon lives in one long-lived helper process and runs on its main thread.
# A Saxon crash then only kills the helper: the check fails (= not released) and the
# next call starts a fresh one. After start-up a check takes a few milliseconds.
TIMEOUT = 120  # seconds; the first call includes starting the helper and compiling
_pool: ProcessPoolExecutor | None = None
_pool_lock = threading.Lock()
_state: dict[str, Any] = {}  # in the helper process


def _helper() -> ProcessPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
        return _pool


def _discard(pool: ProcessPoolExecutor) -> None:
    global _pool
    with _pool_lock:
        if _pool is pool:
            _pool = None
    for process in list((getattr(pool, "_processes", None) or {}).values()):
        process.kill()  # a hung Saxon would otherwise stay around
    pool.shutdown(wait=False, cancel_futures=True)


def _transform(xml: bytes, name: str) -> bytes:
    """Runs in the helper process (main thread)."""
    if "processor" not in _state:
        from saxonche import PySaxonProcessor

        _state["processor"] = PySaxonProcessor(license=False)
    processor = _state["processor"]
    key = f"xslt:{name}"
    if key not in _state:  # compiled once per process
        _state[key] = processor.new_xslt30_processor().compile_stylesheet(stylesheet_file=str(RULE_SETS[name]))
    doc = processor.parse_xml(xml_text=xml.decode("utf-8"))
    return _state[key].transform_to_string(xdm_node=doc).encode("utf-8")


def run_rules(xml: bytes, name: str) -> bytes:
    """Apply one rule set, return the SVRL report."""
    pool = _helper()
    try:
        return pool.submit(_transform, xml, name).result(timeout=TIMEOUT)
    except (BrokenProcessPool, TimeoutError):
        _discard(pool)
        raise


def parse_svrl(svrl: bytes, rule_set: str) -> list[Issue]:
    issues: list[Issue] = []
    root = etree.fromstring(svrl)
    for el in root.iter(f"{{{SVRL}}}failed-assert", f"{{{SVRL}}}successful-report"):
        flag = (el.get("flag") or "error").lower()
        rule_id = el.get("id") or rule_set
        text = " ".join("".join(el.findtext(f"{{{SVRL}}}text") or "").split())
        issues.append(
            Issue(
                code=rule_id,
                field="",
                message=f"Offizielle Prüfung {rule_set}: {_ID_PREFIX.sub('', text) or rule_id}",
                severity="error" if flag in BLOCKING_FLAGS else "warning",
            )
        )
    return issues


def check(xml: bytes, profile: Profile) -> list[Issue]:
    """All Schematron findings for the CII XML of the given profile."""
    issues: list[Issue] = []
    for name in PROFILE_RULE_SETS[profile]:
        try:
            svrl = run_rules(xml, name)
        except Exception as exc:  # missing library/file, Saxon error: fail closed
            log.exception("schematron %s failed", name)
            issues.append(
                Issue(
                    code="SCHEMATRON",
                    field="",
                    message=f"Offizielle Prüfung {name} konnte nicht ausgeführt werden: {exc}",
                )
            )
            continue
        issues.extend(parse_svrl(svrl, name))
    return issues
