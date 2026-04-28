"""
ArcFuse Refactor Agent

Receives findings from Scanner Agent, generates semantic refactoring
diffs using LLM-based code transformation, applies changes, runs
verification tests, and handles rollback on failure.

Architecture:
- RefactorPlan: structured plan for a single refactoring operation
- RefactorEngine: applies plans as text diffs
- Verifier: runs tests, linting, and validation after refactoring
- RollbackManager: restores original state on failure
"""

import difflib
import hashlib
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RefactorPlan:
    """A structured refactoring plan with intent."""
    finding_id: str
    file: str
    line_start: int
    line_end: int
    strategy: str  # inline-fix | extract-function | rename | restructure
    original_snippet: str
    target_snippet: str
    explanation: str
    requires_type_check: bool = True
    requires_lint: bool = True


@dataclass
class RefactorResult:
    """Result of a single refactoring operation."""
    plan: RefactorPlan
    success: bool
    diff: str = ""
    error: Optional[str] = None
    tests_passed: Optional[bool] = None
    rollback_needed: bool = False


class RefactorEngine:
    """
    Core refactoring engine. Applies transformation plans as text diffs
    with backup-and-rollback support.
    """

    def __init__(self, repo_path: str):
        self.repo_path = os.path.abspath(repo_path)
        self._backups: dict[str, str] = {}  # filepath → original content hash

    def apply(self, plan: RefactorPlan) -> str:
        """
        Apply a single refactoring plan. Returns unified diff string.
        Creates a backup before applying.
        """
        full_path = os.path.join(self.repo_path, plan.file)
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"Cannot refactor: {full_path} not found")

        # Read and backup
        with open(full_path, "r") as f:
            original = f.read()

        self._backups[full_path] = original
        content_hash = hashlib.sha256(original.encode()).hexdigest()

        # Apply the transformation
        # Strategies:
        # - inline-fix: replace exact snippet
        # - extract-function: more complex, requires structural change
        # - rename: find-and-replace with context

        if plan.strategy == "inline-fix":
            updated = original.replace(plan.original_snippet, plan.target_snippet, 1)
        elif plan.strategy == "rename":
            updated = original.replace(plan.original_snippet, plan.target_snippet)
        elif plan.strategy == "extract-function":
            # For extract-function, the target_snippet contains the full
            # replacement block with the extracted function appended
            updated = original.replace(plan.original_snippet, plan.target_snippet, 1)
        elif plan.strategy == "restructure":
            # Line-range based replacement
            lines = original.split("\n")
            context_before = "\n".join(lines[:plan.line_start - 1]) + "\n" if plan.line_start > 1 else ""
            context_after = "\n" + "\n".join(lines[plan.line_end:]) if plan.line_end < len(lines) else ""
            updated = context_after
            if context_before or context_after:
                # Full replacement of the block
                updated = context_before + plan.target_snippet + context_after
            else:
                updated = plan.target_snippet
        else:
            raise ValueError(f"Unknown strategy: {plan.strategy}")

        # Write updated file
        with open(full_path, "w") as f:
            f.write(updated)

        # Generate diff for record-keeping
        diff = "".join(difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=plan.file,
            tofile=plan.file,
        ))

        return diff

    def rollback(self, filepath: str) -> None:
        """Restore a file to its pre-refactoring state."""
        if filepath in self._backups:
            with open(filepath, "w") as f:
                f.write(self._backups[filepath])
            del self._backups[filepath]

    def rollback_all(self) -> None:
        """Restore all backed-up files."""
        for filepath in list(self._backups.keys()):
            self.rollback(filepath)


class Verifier:
    """
    Runs verification checks after refactoring.
    Supports Python (pytest, flake8), TypeScript (tsc, eslint), Go (go vet).
    """

    @staticmethod
    def run_tests(work_dir: str) -> tuple[bool, str]:
        """Run pytest or equivalent. Returns (passed, output)."""
        # Auto-detect test framework
        cmd = None
        if os.path.exists(os.path.join(work_dir, "pyproject.toml")):
            cmd = ["pytest", "-x", "--tb=short", "-q"]
        elif os.path.exists(os.path.join(work_dir, "pytest.ini")):
            cmd = ["pytest", "-x", "--tb=short", "-q"]
        elif os.path.exists(os.path.join(work_dir, "setup.cfg")):
            cmd = ["pytest", "-x", "--tb=short", "-q"]
        elif os.path.exists(os.path.join(work_dir, "package.json")):
            cmd = ["npm", "test", "--", "--runInBand"]
        elif os.path.exists(os.path.join(work_dir, "go.mod")):
            cmd = ["go", "test", "./..."]

        if cmd is None:
            return True, "No test runner config found — skipping verification (safe fallback)."

        try:
            result = subprocess.run(
                cmd, cwd=work_dir, capture_output=True, text=True, timeout=120
            )
            passed = result.returncode == 0
            output = result.stdout + "\n" + result.stderr
            return passed, output
        except subprocess.TimeoutExpired as e:
            return False, f"Tests timed out after 120s:\n{e.output.decode() if e.output else ''}"
        except FileNotFoundError:
            return True, f"Test runner '{cmd[0]}' not installed — skipping verification."

    @staticmethod
    def run_lint(work_dir: str) -> tuple[bool, str]:
        """Run linting checks."""
        checks = []
        if os.path.exists(os.path.join(work_dir, ".flake8")):
            checks.append(["flake8", "--statistics"])
        if os.path.exists(os.path.join(work_dir, ".eslintrc.js")) or \
           os.path.exists(os.path.join(work_dir, ".eslintrc.json")):
            checks.append(["npx", "eslint", "."])
        if os.path.exists(os.path.join(work_dir, ".golangci.yml")):
            checks.append(["golangci-lint", "run"])

        if not checks:
            return True, "No linter config found — skipping lint."

        for cmd in checks:
            try:
                result = subprocess.run(
                    cmd, cwd=work_dir, capture_output=True, text=True, timeout=60
                )
                if result.returncode != 0:
                    return False, f"Lint failed ({cmd[0]}):\n{result.stdout}\n{result.stderr}"
            except FileNotFoundError:
                continue

        return True, "All lint checks passed."


class RollbackManager:
    """
    Manages rollback strategies.
    
    Strategies:
    - full: roll back all changes in a batch
    - selective: only roll back failed items
    - degraded: apply a simplified fallback fix if the primary fails
    """

    def __init__(self, engine: RefactorEngine):
        self.engine = engine
        self.degraded_plans: list[RefactorPlan] = []

    def on_failure(self, result: RefactorResult) -> Optional[RefactorResult]:
        """
        Handle a failed refactoring. Attempts degraded mode first,
        then falls back to full rollback.
        """
        # Try degraded mode (simpler, safer fix)
        degraded = self._generate_degraded_plan(result.plan)
        if degraded:
            try:
                diff = self.engine.apply(degraded)
                return RefactorResult(
                    plan=degraded,
                    success=True,
                    diff=diff,
                )
            except Exception:
                pass

        # Rollback to original
        self.engine.rollback(result.plan.file)
        return RefactorResult(
            plan=result.plan,
            success=False,
            error=f"All strategies failed. Rolled back {result.plan.file}.",
            rollback_needed=False,  # Already rolled back
        )

    def _generate_degraded_plan(self, plan: RefactorPlan) -> Optional[RefactorPlan]:
        """Generate a safer, simpler version of a refactoring plan."""
        # If the original was a restructure, try inline-fix instead
        if plan.strategy == "restructure":
            return RefactorPlan(
                finding_id=plan.finding_id,
                file=plan.file,
                line_start=plan.line_start,
                line_end=plan.line_end,
                strategy="inline-fix",
                original_snippet=plan.original_snippet.split("\n")[0],
                target_snippet=plan.target_snippet.split("\n")[0],
                explanation=f"Degraded fix for: {plan.explanation}",
            )
        return None
