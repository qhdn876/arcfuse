"""
CodeFuse Orchestrator

Coordinates the multi-agent pipeline:
  Scanner → Refactor → Reviewer

Manages Agent lifecycle, retry logic, token budget tracking,
and report generation.
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from .scanner import CodeScanner, ScanResult
from .refactor import RefactorEngine, RefactorPlan, Verifier, RollbackManager
from .reviewer import ReviewerAgent, ReviewReport, format_pr_body


@dataclass
class PipelineResult:
    """Complete pipeline execution result."""
    scan_result: ScanResult = field(default_factory=ScanResult)
    refactor_results: list = field(default_factory=list)
    review_reports: list[ReviewReport] = field(default_factory=list)
    tokens_consumed: int = 0
    duration_seconds: float = 0.0
    success: bool = True
    summary: str = ""


@dataclass
class PipelineConfig:
    """Pipeline configuration."""
    repo_path: str
    max_concurrent_agents: int = 5
    max_retries: int = 2
    auto_rollback: bool = True
    run_tests: bool = True
    run_lint: bool = True
    create_pr: bool = True
    incremental: bool = True
    min_score_to_merge: int = 75
    token_budget: Optional[int] = None  # Hard limit on tokens for this run


class Orchestrator:
    """
    Top-level orchestrator for the CodeFuse pipeline.
    Manages the full lifecycle of a codebase scan → refactor → review run.
    """

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.scanner = CodeScanner(config.repo_path)
        self.refactor_engine = RefactorEngine(config.repo_path)
        self.verifier = Verifier()
        self.rollback_manager = RollbackManager(self.refactor_engine)
        self.reviewer = ReviewerAgent()
        self._token_counter = 0

    def run_pipeline(self) -> PipelineResult:
        """
        Execute the full pipeline:
        1. Scan codebase
        2. Generate refactoring plans from findings
        3. Apply refactors with verification
        4. Review diffs
        5. Generate report
        """
        start_time = time.time()
        result = PipelineResult()

        # ── Phase 1: Scan ──
        print("[CodeFuse] Phase 1/4: Scanning codebase...")
        if self.config.incremental:
            result.scan_result = self.scanner.incremental_scan()
        else:
            result.scan_result = self.scanner.scan()

        self._token_counter += result.scan_result.total_tokens_consumed
        print(f"  → Scanned {result.scan_result.files_scanned} files, "
              f"{len(result.scan_result.findings)} findings")

        # Check token budget
        if self.config.token_budget and self._token_counter > self.config.token_budget:
            result.success = False
            result.summary = f"Token budget exceeded ({self._token_counter} > {self.config.token_budget})"
            return result

        if not result.scan_result.findings:
            result.summary = "No issues found. Codebase is clean."
            return result

        # ── Phase 2: Plan Refactoring ──
        print("[CodeFuse] Phase 2/4: Generating refactoring plans...")
        plans = self._findings_to_plans(result.scan_result)
        print(f"  → Generated {len(plans)} refactoring plans")

        # ── Phase 3: Apply Refactors ──
        print("[CodeFuse] Phase 3/4: Applying refactorings...")
        for plan in plans:
            ref_result = self._apply_single_refactor(plan)
            result.refactor_results.append(ref_result)

            if ref_result.success and ref_result.diff:
                # Phase 3b: Review each successful refactoring
                review = self.reviewer.review(ref_result.diff)
                result.review_reports.append(review)
                print(f"  → {plan.strategy} on {os.path.basename(plan.file)}: "
                      f"{'✅' if review.approved else '❌'} score={review.score}")
            else:
                result.review_reports.append(ReviewReport(
                    approved=False, score=0,
                    summary=f"Refactoring failed: {ref_result.error}"
                ))

        # ── Phase 4: Final Report ──
        print("[CodeFuse] Phase 4/4: Generating summary...")
        result.tokens_consumed = self._token_counter
        result.duration_seconds = time.time() - start_time

        approved_count = sum(1 for r in result.review_reports if r.approved)
        total_refactors = len(result.refactor_results)

        result.summary = (
            f"CodeFuse Pipeline Complete\n"
            f"  Duration: {result.duration_seconds:.1f}s\n"
            f"  Files scanned: {result.scan_result.files_scanned}\n"
            f"  Findings: {len(result.scan_result.findings)}\n"
            f"  Refactors attempted: {total_refactors}\n"
            f"  Approved: {approved_count}\n"
            f"  Tokens consumed: {result.tokens_consumed:,}\n"
            f"  Status: {'✅ All clear' if result.success else '⚠️ Issues found'}"
        )

        return result

    def _findings_to_plans(self, scan_result: ScanResult) -> list[RefactorPlan]:
        """Convert scanner findings to executable refactoring plans."""
        plans = []

        for finding in scan_result.findings:
            if finding.severity in ("info", "low"):
                continue  # Skip trivial items

            if finding.category == "anti-pattern" and "except" in finding.title.lower():
                # Generate a plan that wraps bare except
                original_snippet = finding.context
                target_snippet = original_snippet.replace(
                    "except:", "except Exception:"
                )
                plan = RefactorPlan(
                    finding_id=f"codefuse-{finding.category}-{finding.line}",
                    file=finding.file,
                    line_start=finding.line,
                    line_end=finding.line,
                    strategy="inline-fix",
                    original_snippet=original_snippet,
                    target_snippet=target_snippet,
                    explanation=f"Fixed: {finding.title}",
                )
                plans.append(plan)

        return plans

    def _apply_single_refactor(self, plan: RefactorPlan) -> object:
        """Apply a single refactoring with verification and rollback."""
        from dataclasses import dataclass

        @dataclass
        class _Result:
            plan: RefactorPlan
            success: bool
            diff: str = ""
            error: Optional[str] = None

        for attempt in range(self.config.max_retries + 1):
            try:
                diff = self.refactor_engine.apply(plan)

                # Verify
                if self.config.run_tests:
                    tests_ok, test_output = self.verifier.run_tests(self.config.repo_path)
                    if not tests_ok:
                        if self.config.auto_rollback:
                            fallback = self.rollback_manager.on_failure(
                                _Result(plan=plan, success=False, diff=diff, error=test_output)
                            )
                            if fallback:
                                return _Result(plan=plan, success=True, diff=fallback.diff)
                        raise RuntimeError(f"Tests failed: {test_output[:200]}")

                return _Result(plan=plan, success=True, diff=diff)

            except Exception as e:
                if attempt < self.config.max_retries:
                    # Rollback and retry
                    self.refactor_engine.rollback(plan.file)
                    continue
                return _Result(plan=plan, success=False, error=str(e))


# ── CLI Entry Point ──

def main():
    """Command-line entry point for running a pipeline."""
    import argparse

    parser = argparse.ArgumentParser(description="CodeFuse — Automatic Codebase Refactoring")
    parser.add_argument("repo", help="Path to git repository")
    parser.add_argument("--max-agents", type=int, default=5, help="Max concurrent agents")
    parser.add_argument("--no-tests", action="store_true", help="Skip test verification")
    parser.add_argument("--no-lint", action="store_true", help="Skip lint verification")
    parser.add_argument("--full-scan", action="store_true", help="Full scan (not incremental)")
    parser.add_argument("--token-budget", type=int, default=None, help="Hard token limit")
    parser.add_argument("--min-score", type=int, default=75, help="Min review score to merge")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    args = parser.parse_args()

    config = PipelineConfig(
        repo_path=os.path.abspath(args.repo),
        max_concurrent_agents=args.max_agents,
        run_tests=not args.no_tests,
        run_lint=not args.no_lint,
        incremental=not args.full_scan,
        token_budget=args.token_budget,
        min_score_to_merge=args.min_score,
    )

    orchestrator = Orchestrator(config)
    result = orchestrator.run_pipeline()

    if args.json:
        print(json.dumps({
            "success": result.success,
            "files_scanned": result.scan_result.files_scanned,
            "findings": len(result.scan_result.findings),
            "refactors_attempted": len(result.refactor_results),
            "refactors_approved": sum(1 for r in result.review_reports if r.approved),
            "tokens_consumed": result.tokens_consumed,
            "duration_seconds": result.duration_seconds,
            "summary": result.summary,
        }, indent=2))
    else:
        print(result.summary)


if __name__ == "__main__":
    main()
