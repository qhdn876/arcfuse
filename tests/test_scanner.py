"""
Tests for ArcFuse Scanner Agent.
"""

import tempfile
from pathlib import Path

from codefuse.scanner import CodeScanner, PythonAnalyzer


def test_scanner_discovers_python_files():
    with tempfile.TemporaryDirectory() as td:
        # Create some test files
        Path(td, "hello.py").write_text("x = 1\n")
        Path(td, "main.ts").write_text("const x: number = 1;\n")
        Path(td, "ignored.js").write_text("var x = 1;\n")
        Path(td, "README.md").write_text("# readme\n")

        scanner = CodeScanner(td)
        files = scanner._discover_files()
        paths = [f.replace(td, "") for f in files]
        assert any("hello.py" in p for p in paths)
        assert any("main.ts" in p for p in paths)
        assert any("ignored.js" in p for p in paths)
        assert not any("README.md" in p for p in paths)


def test_python_analyzer_detects_bare_except():
    code = """
try:
    do_something()
except:
    pass
"""
    analyzer = PythonAnalyzer()
    findings = analyzer.analyze("test.py", code, code.split("\n"))
    bare_excepts = [f for f in findings if "bare except" in f.title.lower()]
    assert len(bare_excepts) == 1


def test_python_analyzer_detects_mutable_defaults():
    code = """
def bad_default(items=[]):
    return items
"""
    analyzer = PythonAnalyzer()
    findings = analyzer.analyze("test.py", code, code.split("\n"))
    mutable = [f for f in findings if "mutable default" in f.title.lower()]
    assert len(mutable) == 1


def test_python_analyzer_long_function():
    code = "def long_func():\n" + "    pass\n" * 60
    analyzer = PythonAnalyzer()
    findings = analyzer.analyze("test.py", code, code.split("\n"))
    long_funcs = [f for f in findings if "too long" in f.title.lower()]
    assert len(long_funcs) == 1


def test_scanner_skips_unsupported_extensions():
    with tempfile.TemporaryDirectory() as td:
        Path(td, "binary.bin").write_bytes(b"\x00\x01\x02")
        Path(td, "data.json").write_text('{"key": "value"}')

        scanner = CodeScanner(td)
        result = scanner.scan()
        assert result.files_scanned == 0


def test_scanner_incremental_returns_result():
    """Incremental scan should not crash when there's no git diff."""
    with tempfile.TemporaryDirectory() as td:
        Path(td, "test.py").write_text("x = 1\n")

        import os
        orig_dir = os.getcwd()
        os.chdir(td)

        try:
            import subprocess
            subprocess.run(["git", "init"], capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], capture_output=True)
            subprocess.run(["git", "config", "user.name", "test"], capture_output=True)
            subprocess.run(["git", "add", "."], capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], capture_output=True)

            scanner = CodeScanner(td)
            result = scanner.incremental_scan()
            assert result is not None
        finally:
            os.chdir(orig_dir)
