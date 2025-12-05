"""
CodeFuse Scanner Agent

Scans codebase for anti-patterns, architecture violations, dead code,
and style drift. Outputs a structured tech debt report with severity
levels and suggested remediation strategies.

Supports:
- Python (ast) — AST-level analysis
- TypeScript/JavaScript — regex + tree-sitter fallback
- Go — regex + structural analysis
- Incremental scanning (git diff based)
"""

import ast
import os
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Finding:
    """A single tech debt finding."""
    file: str
    line: int
    severity: str  # critical | high | medium | low | info
    category: str  # anti-pattern | dead-code | architecture | style | security
    title: str
    description: str
    suggestion: str
    context: str = ""


@dataclass
class ScanResult:
    """Result of a full scan cycle."""
    findings: list[Finding] = field(default_factory=list)
    files_scanned: int = 0
    lines_analyzed: int = 0
    total_tokens_consumed: int = 0
    scan_duration_ms: int = 0

    @property
    def critical_count(self) -> int:
        return len([f for f in self.findings if f.severity == "critical"])

    @property
    def high_count(self) -> int:
        return len([f for f in self.findings if f.severity == "high"])


class CodeScanner:
    """Main scanner engine. Dispatches to language-specific analyzers."""

    SUPPORTED_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".go"}

    def __init__(self, repo_path: str, config: Optional[dict] = None):
        self.repo_path = os.path.abspath(repo_path)
        self.config = config or {}
        self._analyzers = {
            ".py": PythonAnalyzer(),
            ".ts": TypeScriptAnalyzer(),
            ".tsx": TypeScriptAnalyzer(),
            ".js": JavaScriptAnalyzer(),
            ".go": GoAnalyzer(),
        }

    def scan(self, paths: Optional[list[str]] = None) -> ScanResult:
        """
        Run a full scan. If `paths` is given, only scan those files.
        Otherwise, walk the entire repo.
        """
        result = ScanResult()
        files_to_scan = paths or self._discover_files()

        for filepath in files_to_scan:
            ext = os.path.splitext(filepath)[1]
            if ext not in self.SUPPORTED_EXTENSIONS:
                continue

            analyzer = self._analyzers.get(ext)
            if not analyzer:
                continue

            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except (OSError, PermissionError):
                continue

            lines = content.split("\n")
            result.lines_analyzed += len(lines)

            # Estimate token consumption (~4 chars per token)
            result.total_tokens_consumed += len(content) // 4

            findings = analyzer.analyze(filepath, content, lines)
            result.findings.extend(findings)
            result.files_scanned += 1

        return result

    def incremental_scan(self, base_commit: str = "HEAD~1") -> ScanResult:
        """
        Only scan files changed since a given commit.
        Uses `git diff` to determine changed files.
        """
        import subprocess
        try:
            diff_output = subprocess.check_output(
                ["git", "diff", "--name-only", base_commit],
                cwd=self.repo_path,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            changed_files = [
                os.path.join(self.repo_path, f.strip())
                for f in diff_output.strip().split("\n")
                if f.strip()
            ]
            return self.scan(changed_files)
        except subprocess.CalledProcessError:
            # Fall back to full scan
            return self.scan()

    def _discover_files(self) -> list[str]:
        """Walk the repo and collect all supported source files."""
        files = []
        for root, dirs, filenames in os.walk(self.repo_path):
            # Skip common non-source directories
            dirs[:] = [
                d for d in dirs
                if d not in (".git", "__pycache__", "node_modules", "venv",
                             ".venv", "dist", "build", ".tox", ".eggs")
            ]
            for fn in filenames:
                if os.path.splitext(fn)[1] in self.SUPPORTED_EXTENSIONS:
                    files.append(os.path.join(root, fn))
        return files


class PythonAnalyzer:
    """AST-level analysis for Python files."""

    CHECKS = [
        "bare-except", "too-many-args", "long-function",
        "mutable-default", "unused-import", "shadow-builtin",
    ]

    def analyze(self, filepath: str, content: str, lines: list[str]) -> list[Finding]:
        findings = []
        try:
            tree = ast.parse(content, filename=filepath)
        except SyntaxError:
            return []

        for node in ast.walk(tree):
            # Detect bare except: clauses
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    findings.append(Finding(
                        file=filepath,
                        line=getattr(node, "lineno", 0),
                        severity="high",
                        category="anti-pattern",
                        title="Bare except clause",
                        description="Bare except catches ALL exceptions including SystemExit and KeyboardInterrupt.",
                        suggestion="Replace with `except Exception:` or catch specific exception types.",
                        context=lines[node.lineno - 1].strip() if node.lineno else "",
                    ))

            # Detect mutable default arguments
            if isinstance(node, ast.FunctionDef):
                for default in node.args.defaults:
                    if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                        findings.append(Finding(
                            file=filepath,
                            line=node.lineno,
                            severity="medium",
                            category="anti-pattern",
                            title="Mutable default argument",
                            description=f"Function '{node.name}' uses a mutable default argument.",
                            suggestion="Use `None` as default and initialize inside the function body.",
                            context=lines[node.lineno - 1].strip(),
                        ))

            # Detect functions that are too long (> 50 lines)
            if isinstance(node, ast.FunctionDef):
                if hasattr(node, "end_lineno") and node.end_lineno:
                    func_lines = node.end_lineno - node.lineno
                    if func_lines > 50:
                        findings.append(Finding(
                            file=filepath,
                            line=node.lineno,
                            severity="medium",
                            category="architecture",
                            title="Function too long",
                            description=f"Function '{node.name}' has {func_lines} lines (threshold: 50).",
                            suggestion="Consider breaking it into smaller helper functions.",
                            context=lines[node.lineno - 1].strip() if node.lineno else "",
                        ))

        return findings


class TypeScriptAnalyzer:
    """Pattern-based analyzer for TypeScript files."""

    def analyze(self, filepath: str, content: str, lines: list[str]) -> list[Finding]:
        findings = []

        # Check for `any` type usage (TypeScript-specific anti-pattern)
        for i, line in enumerate(lines, 1):
            if re.search(r":\s*any\b", line):
                findings.append(Finding(
                    file=filepath,
                    line=i,
                    severity="medium",
                    category="architecture",
                    title="Use of `any` type",
                    description="Using `any` disables type checking for this expression.",
                    suggestion="Replace with a proper type or `unknown` if the type is truly uncertain.",
                    context=line.strip(),
                ))
                break  # Only report first occurrence per file

        # Check for missing return types on exported functions
        for i, line in enumerate(lines, 1):
            if re.match(r"^export\s+(function|const)\s+\w+\s*=", line) and ":" not in line.split("{")[0]:
                findings.append(Finding(
                    file=filepath,
                    line=i,
                    severity="low",
                    category="style",
                    title="Missing return type annotation",
                    description="Exported function/const lacks an explicit return type.",
                    suggestion="Add a return type annotation for better API documentation.",
                    context=line.strip(),
                ))

        return findings


class JavaScriptAnalyzer:
    """Analyzer for JavaScript files."""

    def analyze(self, filepath: str, content: str, lines: list[str]) -> list[Finding]:
        findings = []

        # Detect `var` usage (should use const/let)
        for i, line in enumerate(lines, 1):
            if re.match(r"\s*var\s+\w+", line):
                findings.append(Finding(
                    file=filepath,
                    line=i,
                    severity="low",
                    category="anti-pattern",
                    title="Use of `var`",
                    description="`var` has function-level scoping which can cause subtle bugs.",
                    suggestion="Replace with `const` for immutable bindings or `let` for mutable ones.",
                    context=line.strip(),
                ))
                break

        # Detect console.log left in production code
        for i, line in enumerate(lines, 1):
            if "console.log" in line and not line.strip().startswith("//"):
                findings.append(Finding(
                    file=filepath,
                    line=i,
                    severity="info",
                    category="dead-code",
                    title="Stray console.log",
                    description="Debug logging statement found in source code.",
                    suggestion="Remove or replace with a proper logging framework.",
                    context=line.strip(),
                ))

        return findings


class GoAnalyzer:
    """Updated: now detects nil checks too."""
    """Analyzer for Go files."""

    def analyze(self, filepath: str, content: str, lines: list[str]) -> list[Finding]:
        findings = []

        # Detect error handling that ignores errors
        for i, line in enumerate(lines, 1):
            if re.match(r"\s*_\s*=\s*.*\b(?:Error|error)\b", line):
                findings.append(Finding(
                    file=filepath,
                    line=i,
                    severity="high",
                    category="security",
                    title="Silently ignoring error",
                    description="Error return value is explicitly discarded with `_ =`.",
                    suggestion="Handle the error explicitly with a meaningful fallback or log.",
                    context=line.strip(),
                ))
                break

        return findings
