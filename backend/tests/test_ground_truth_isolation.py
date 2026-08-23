"""
tests/test_ground_truth_isolation.py
=======================================
CHECKPOINT 3, Section 1: automated regression proof that ground truth is
never consumed by live pipeline code, per docs/ARCHITECTURE.md's "three-way
data separation":

    Ground truth (ground_truth_*, fraud_networks, expected_signals,
    expected_evidence) may only be read by evaluation code. It must never be
    imported, opened, or joined against by detection_layer.py, case
    bundling, network_layer.py, evidence_model.py, the investigation agents,
    run_pipeline.py, or any live API endpoint (main.py).

Two independent proofs, per the checkpoint instructions ("Search the
complete repository. Comments/documentation may mention ground truth.
Actual imports, reads, joins, or runtime dependencies are prohibited."):

  1. STATIC: an AST scan of every live module's source, excluding
     docstrings (which legitimately explain the separation in prose), for
     any string literal, import, attribute, or identifier referencing a
     ground-truth concept.
  2. DYNAMIC: run the real Detection -> Case Intake pipeline against a copy
     of mock_data/ with every ground_truth_*.csv file deleted, and confirm
     the output (alert/case counts, ids, content) is byte-identical to a
     run against the original directory. If any live code secretly depended
     on those files, this run would crash or silently change output;
     neither happens.

Both are required - the static scan alone can miss a truly obfuscated
read (e.g. a dynamically-built filename), and the dynamic proof alone can't
distinguish "never reads it" from "reads it but the file happens not to be
needed this run". Together they're a real regression guard against either
kind of future accidental coupling.
"""
import ast
import json
import os
import shutil
import tempfile

import pytest

from data_store import DataStore
from detection_layer import run_detection_pipeline, bundle_alerts_into_cases

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK_DATA_DIR = os.path.join(BACKEND_DIR, "mock_data")

# Every module that is part of the LIVE pipeline (per the checkpoint's own
# list in Section 1). generate_mock_data.py and eval_pipeline.py are
# deliberately EXCLUDED - they are the only code permitted to read ground
# truth (generator + evaluation code respectively), and are not scanned
# here. tests/ itself is also excluded (it's allowed to build synthetic
# ground-truth-shaped fixtures for other purposes without violating live
# isolation).
LIVE_MODULES = [
    "data_store.py",
    "detection_layer.py",
    "evidence_model.py",
    "network_layer.py",
    "run_pipeline.py",
    "main.py",
    os.path.join("agents", "contradiction_agent.py"),
    os.path.join("agents", "evidence_builder.py"),
    os.path.join("agents", "legitimate_hypothesis_agent.py"),
    os.path.join("agents", "scammer_hypothesis_agent.py"),
]

# Substrings that indicate a ground-truth concept per the checkpoint spec:
# ground_truth, fraud_networks, expected_signals, expected_evidence,
# ground_truth_alerts, ground_truth_cases (the last two are covered by the
# ground_truth prefix already, kept explicit for readability).
FORBIDDEN_SUBSTRINGS = (
    "ground_truth",
    "fraud_network",       # covers fraud_networks / fraud_network_id
    "expected_signal",
    "expected_evidence",
)


def _docstring_node_ids(tree):
    """Return the id() of every Constant/Str node that IS a docstring
    (the first statement of the module, or of any FunctionDef/AsyncFunctionDef
    /ClassDef body) - these are documentation, explicitly allowed to mention
    ground truth by the checkpoint spec, and must be excluded from the
    "actual code" scan below."""
    doc_ids = set()
    candidates = [tree] + [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    for node in candidates:
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr):
            val = first.value
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                doc_ids.add(id(val))
    return doc_ids


def _scan_file_for_ground_truth(path):
    """Return a list of (lineno, kind, token) violations found in `path`'s
    actual code (imports, non-docstring string literals, attribute access,
    identifiers) - never in comments (never part of the AST at all) or
    docstrings (excluded explicitly above)."""
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=path)
    docstring_ids = _docstring_node_ids(tree)

    violations = []
    for node in ast.walk(tree):
        # Imports: `import fraud_networks_loader` / `from x import ground_truth_y`
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.lower()
                if any(s in name for s in FORBIDDEN_SUBSTRINGS):
                    violations.append((node.lineno, "import", alias.name))
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").lower()
            if any(s in mod for s in FORBIDDEN_SUBSTRINGS):
                violations.append((node.lineno, "import_from", node.module))
            for alias in node.names:
                name = alias.name.lower()
                if any(s in name for s in FORBIDDEN_SUBSTRINGS):
                    violations.append((node.lineno, "import_from_name", alias.name))
        # String literals that are NOT docstrings: file paths, dict keys
        # (row["ground_truth_label"]), column names, etc.
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_ids:
                continue
            low = node.value.lower()
            if any(s in low for s in FORBIDDEN_SUBSTRINGS):
                violations.append((node.lineno, "string_literal", node.value))
        # Attribute access: obj.ground_truth_label
        elif isinstance(node, ast.Attribute):
            low = node.attr.lower()
            if any(s in low for s in FORBIDDEN_SUBSTRINGS):
                violations.append((node.lineno, "attribute", node.attr))
        # Bare identifiers: a variable literally named ground_truth_cases
        elif isinstance(node, ast.Name):
            low = node.id.lower()
            if any(s in low for s in FORBIDDEN_SUBSTRINGS):
                violations.append((node.lineno, "name", node.id))
    return violations


@pytest.mark.parametrize("relpath", LIVE_MODULES)
def test_live_module_never_references_ground_truth_in_code(relpath):
    """STATIC proof (TEST 9 / ARCHITECTURE.md test #17): no live module's
    actual code - as opposed to its documentation - imports, reads, or
    otherwise references a ground-truth concept."""
    path = os.path.join(BACKEND_DIR, relpath)
    assert os.path.isfile(path), f"expected live module not found: {path}"
    violations = _scan_file_for_ground_truth(path)
    assert violations == [], (
        f"{relpath} contains {len(violations)} ground-truth reference(s) in "
        f"actual code (not docstrings/comments): {violations}"
    )


def test_ground_truth_files_exist_and_are_named_distinctly():
    """Sanity check on the OTHER half of the separation: the ground-truth
    files must actually exist under mock_data/ (so the isolation proof below
    isn't vacuously true because the files are simply missing) and must all
    carry the `ground_truth_` prefix, which is what lets a human (and this
    test) tell live data and evaluation-only data apart at a glance."""
    assert os.path.isdir(MOCK_DATA_DIR), "mock_data/ must be generated before running tests"
    entries = os.listdir(MOCK_DATA_DIR)
    gt_files = [e for e in entries if "ground_truth" in e]
    assert gt_files, "expected at least one ground_truth_*.csv under mock_data/"
    for f in gt_files:
        assert f.startswith("ground_truth_"), f"{f} contains 'ground_truth' but doesn't use the required prefix"


def test_live_pipeline_runs_identically_with_ground_truth_files_removed():
    """DYNAMIC proof: copy mock_data/ to a temp dir, delete every
    ground_truth_*.csv, and confirm the live Detection -> Case Intake chain
    produces byte-identical output to a run against the untouched directory.
    If detection_layer.py or bundle_alerts_into_cases() secretly depended on
    any ground_truth_*.csv (or crashed without it), this test would fail;
    it does neither, because live code never opens those files at all."""
    assert os.path.isdir(MOCK_DATA_DIR), "mock_data/ must be generated before running tests"

    # Baseline: real directory, untouched.
    store_full = DataStore(MOCK_DATA_DIR)
    alerts_full = run_detection_pipeline(store_full)
    cases_full = bundle_alerts_into_cases(alerts_full)

    # Stripped copy: every ground_truth_*.csv physically removed.
    with tempfile.TemporaryDirectory() as tmp:
        stripped_dir = os.path.join(tmp, "mock_data_no_gt")
        shutil.copytree(MOCK_DATA_DIR, stripped_dir)
        removed_any = False
        for fname in os.listdir(stripped_dir):
            if "ground_truth" in fname:
                os.remove(os.path.join(stripped_dir, fname))
                removed_any = True
        assert removed_any, "expected at least one ground_truth_*.csv to remove for this test to be meaningful"

        store_stripped = DataStore(stripped_dir)
        alerts_stripped = run_detection_pipeline(store_stripped)
        cases_stripped = bundle_alerts_into_cases(alerts_stripped)

    # Byte-identical (modulo dict key order, hence sort_keys) output proves
    # the missing files changed nothing about live behavior.
    assert json.dumps(alerts_full, sort_keys=True, default=str) == \
        json.dumps(alerts_stripped, sort_keys=True, default=str)
    assert json.dumps(cases_full, sort_keys=True, default=str) == \
        json.dumps(cases_stripped, sort_keys=True, default=str)


def test_no_case_or_alert_field_carries_a_ground_truth_prefix():
    """Belt-and-suspenders check on the live OUTPUT shape itself (not just
    the code that produces it): no key on a real, freshly-generated alert or
    case dict is named with the ground_truth_ prefix, confirming the live
    schema and the evaluation-only schema never accidentally converge."""
    store = DataStore(MOCK_DATA_DIR)
    alerts = run_detection_pipeline(store)
    cases = bundle_alerts_into_cases(alerts)
    assert alerts, "expected at least one live alert on the checked-in mock_data/ fixture"
    assert cases, "expected at least one live case on the checked-in mock_data/ fixture"
    for a in alerts:
        assert not any("ground_truth" in k for k in a.keys()), a.keys()
    for c in cases:
        assert not any("ground_truth" in k for k in c.keys()), c.keys()