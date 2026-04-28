"""
ArcFuse Reviewer Agent

Reviews refactoring diffs for correctness, style compliance, and
architectural consistency. Produces a ReviewScore and may request
changes or approve automatically.
"""

import re
from dataclasses import dataclass, field


@dataclass
class ReviewComment:
    """A single review comment on a diff hunk."""
    file: str
    line: int
    message: str
    severity: str = "warning"
    suggestion: str = ""


@dataclass
class ReviewReport:
    """Complete review of a refactoring operation."""
    approved: bool
    score: int  # 0-100
    comments: list[ReviewComment] = field(default_factory=list)
    summary: str = ""


class ReviewerAgent:
    """
    Reviews refactoring diffs and scores them on:
    - Correctness (does the change preserve behavior?)
    - Style (does it follow project conventions?)
    - Safety (are there edge cases not handled?)
    - Completeness (are all related locations updated?)
    """

    WEIGHTS = {
        "correctness": 0.40,
        "style": 0.20,
        "safety": 0.25,
        "completeness": 0.15,
    }

    def review(self, diff: str, file_context: dict | None = None) -> ReviewReport:
        """
        Review a unified diff. Returns structured report with score.
        
        Args:
            diff: Unified diff string (output of `git diff` or difflib)
            file_context: Optional metadata about the file being changed
        """
        comments: list[ReviewComment] = []
        scores = {k: 1.0 for k in self.WEIGHTS}

        if not diff.strip():
            return ReviewReport(approved=True, score=100, summary="No changes to review.")

        # Parse diff hunks
        hunks = self._parse_diff(diff)

        for hunk in hunks:
            # Check each changed line
            for line in hunk["lines"]:
                if line.startswith("+"):
                    # Added lines — check for potential issues
                    comment = self._check_added_line(line, hunk["file"])
                    if comment:
                        comments.append(comment)

                elif line.startswith("-"):
                    # Removed lines — check for dangerous removals
                    comment = self._check_removed_line(line, hunk["file"])
                    if comment:
                        comments.append(comment)

        # Calculate final score from comments
        if comments:
            severity_map = {"critical": -0.5, "warning": -0.12, "suggestion": -0.04}
            for c in comments:
                penalty = severity_map.get(c.severity, -0.04)
                # Apply penalty to the most relevant dimension
                if c.severity == "critical":
                    scores["correctness"] = max(0, scores["correctness"] + penalty)
                    scores["safety"] = max(0, scores["safety"] + penalty)
                elif c.severity == "warning":
                    scores["safety"] = max(0, scores["safety"] + penalty)
                else:
                    scores["style"] = max(0, scores["style"] + penalty)

        final_score = sum(
            scores[k] * w for k, w in self.WEIGHTS.items()
        )
        final_score = max(0, min(100, int(final_score * 100)))

        approved = final_score >= 75
        severity_counts = {}
        for c in comments:
            severity_counts[c.severity] = severity_counts.get(c.severity, 0) + 1

        summary_parts = [f"Score: {final_score}/100"]
        if approved:
            summary_parts.append("APPROVED")
        else:
            summary_parts.append("CHANGES REQUESTED")
        if comments:
            summary_parts.append(f"Comments: {len(comments)}")
            for sev, count in sorted(severity_counts.items()):
                summary_parts.append(f"  {sev}: {count}")

        return ReviewReport(
            approved=approved,
            score=final_score,
            comments=comments,
            summary=" | ".join(summary_parts),
        )

    def _parse_diff(self, diff: str) -> list[dict]:
        """Parse a unified diff into structured hunks."""
        hunks = []
        current_hunk = None

        for line in diff.split("\n"):
            if line.startswith("--- "):
                # Start of a file section
                if current_hunk:
                    hunks.append(current_hunk)
                current_hunk = {"file": line[6:].strip(), "lines": []}
            elif line.startswith("+++ "):
                continue
            elif line.startswith("@@"):
                if current_hunk:
                    current_hunk["lines"].append(line)
            elif current_hunk is not None:
                current_hunk["lines"].append(line)

        if current_hunk:
            hunks.append(current_hunk)

        return hunks

    def _check_added_line(self, line: str, file: str) -> ReviewComment | None:
        """Check an added line for potential issues."""
        content = line[1:]  # Strip the leading +

        # Check for hardcoded credentials/tokens
        if re.search(r'(?:token|password|secret|api_key|apikey)\s*[=:]\s*["\'].+["\']', content, re.I):
            return ReviewComment(
                file=file,
                line=0,
                severity="critical",
                message="Potentially hardcoded credential detected in added line.",
                suggestion="Move to environment variables or a secrets manager.",
            )

        # Check for TODO/FIXME being introduced
        if re.search(r'\bTODO\b', content, re.I):
            return ReviewComment(
                file=file,
                line=0,
                severity="suggestion",
                message="New TODO comment introduced.",
                suggestion="Consider tracking this in your issue tracker instead.",
            )

        # Check for overly long lines (> 100 chars)
        if len(content) > 100:
            return ReviewComment(
                file=file,
                line=0,
                severity="warning",
                message=f"Line too long ({len(content)} chars, limit: 100).",
                suggestion="Break the line into multiple shorter lines.",
            )

        # Check for bare print() statements being added
        if re.match(r'\s*print\(', content) and not content.strip().startswith("#"):
            return ReviewComment(
                file=file,
                line=0,
                severity="warning",
                message="Added print() statement detected.",
                suggestion="Replace with proper logging if this is for production.",
            )

        return None

    def _check_removed_line(self, line: str, file: str) -> ReviewComment | None:
        """Check a removed line for dangerous removals."""
        content = line[1:]  # Strip the leading -

        # Flag removal of error handling
        if re.search(r'try\s*:|except\s+\w+\s*:|raise\s+\w+', content):
            return ReviewComment(
                file=file,
                line=0,
                severity="critical",
                message="Removal of exception handling detected.",
                suggestion="Ensure the error is still handled appropriately.",
            )

        # Flag removal of type annotations
        if re.search(r'->\s*\w+', content):
            return ReviewComment(
                file=file,
                line=0,
                severity="warning",
                message="Return type annotation being removed.",
                suggestion="Type annotations improve code documentation and tooling support.",
            )

        return None


def format_pr_body(report: ReviewReport, refactoring_summary: str) -> str:
    """Generate a PR description from the review report."""
    status = "✅ Approved" if report.approved else "❌ Changes Requested"
    
    body = f"""## CodeFuse Auto-Review

**Status:** {status}
**Score:** {report.score}/100

### Refactoring Summary
{refactoring_summary}

### Review Comments
"""
    if report.comments:
        for c in report.comments:
            body += f"\n- **[{c.severity.upper()}]** ({c.file}): {c.message}"
            if c.suggestion:
                body += f"\n  → {c.suggestion}"
    else:
        body += "\nNo issues found. Clean refactoring."

    return body
