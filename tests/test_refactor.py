"""
Tests for CodeFuse Refactor Agent.
"""

import tempfile
from pathlib import Path

from codefuse.refactor import RefactorEngine, RefactorPlan, Verifier


SAMPLE_PYTHON_CODE = """
def process(items):
    try:
        for item in items:
            print(item)
    except:
        print("error")
"""


def test_refactor_engine_applies_inline_fix():
    with tempfile.TemporaryDirectory() as td:
        test_file = Path(td, "sample.py")
        test_file.write_text(SAMPLE_PYTHON_CODE)

        plan = RefactorPlan(
            finding_id="test-001",
            file="sample.py",
            line_start=5,
            line_end=5,
            strategy="inline-fix",
            original_snippet="    except:",
            target_snippet="    except Exception:",
            explanation="Fix bare except",
        )

        engine = RefactorEngine(td)
        diff = engine.apply(plan)
        assert "except Exception:" in diff
        assert diff.startswith("--- ")  # Unified diff format


def test_refactor_engine_rollback():
    with tempfile.TemporaryDirectory() as td:
        test_file = Path(td, "sample.py")
        test_file.write_text(SAMPLE_PYTHON_CODE)
        original = test_file.read_text()

        plan = RefactorPlan(
            finding_id="test-002",
            file="sample.py",
            line_start=5,
            line_end=5,
            strategy="inline-fix",
            original_snippet="    except:",
            target_snippet="    except Exception:",
            explanation="Fix bare except",
        )

        engine = RefactorEngine(td)
        engine.apply(plan)
        engine.rollback(str(test_file))

        restored = test_file.read_text()
        assert restored == original


def test_refactor_engine_supports_multiple_strategies():
    with tempfile.TemporaryDirectory() as td:
        test_file = Path(td, "old_name.py")
        test_file.write_text("def old_func():\n    pass\n")

        # Test rename strategy
        plan = RefactorPlan(
            finding_id="test-003",
            file="old_name.py",
            line_start=1,
            line_end=1,
            strategy="rename",
            original_snippet="old_func",
            target_snippet="new_func",
            explanation="Rename function",
        )

        engine = RefactorEngine(td)
        diff = engine.apply(plan)
        assert "new_func" in diff


def test_verifier_detects_no_test_runner():
    with tempfile.TemporaryDirectory() as td:
        passed, output = Verifier.run_tests(td)
        assert passed  # No test runner = no failure
        assert "No test runner" in output
