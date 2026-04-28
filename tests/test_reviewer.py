"""
Tests for ArcFuse Reviewer Agent.
"""

from codefuse.reviewer import ReviewerAgent, format_pr_body, ReviewReport, ReviewComment


def test_reviewer_approves_clean_diff():
    diff = """--- a/file.py
+++ b/file.py
@@ -1,3 +1,3 @@
 def old_func():
-    print("hello")
+    logger.info("hello")
"""
    reviewer = ReviewerAgent()
    report = reviewer.review(diff)
    assert report.approved
    assert report.score >= 75


def test_reviewer_rejects_hardcoded_credentials():
    diff = """--- a/config.py
+++ b/config.py
@@ -1 +1 @@
-api_key = os.environ["API_KEY"]
+api_key = "sk-1234567890abcdef"
"""
    reviewer = ReviewerAgent()
    report = reviewer.review(diff)
    assert not report.approved, "Should flag hardcoded credentials"
    critical_comments = [c for c in report.comments if c.severity == "critical"]
    assert len(critical_comments) > 0


def test_reviewer_warns_about_long_lines():
    diff = """--- a/file.py
+++ b/file.py
@@ -1 +1 @@
-x = 1
+result = some_really_long_function_call(with_many_parameters, and_even_more, that_goes_way_beyond_one_hundred_characters_limit)
"""
    reviewer = ReviewerAgent()
    report = reviewer.review(diff)
    warnings = [c for c in report.comments if c.severity == "warning"]
    assert any("too long" in c.message.lower() for c in warnings)


def test_reviewer_empty_diff():
    reviewer = ReviewerAgent()
    report = reviewer.review("")
    assert report.approved
    assert report.score == 100


def test_reviewer_detects_removed_error_handling():
    diff = """--- a/file.py
+++ b/file.py
@@ -1,4 +1,2 @@
 def process():
-    try:
-        do_something()
-    except Exception as e:
-        logger.error(str(e))
+    do_something()
"""
    reviewer = ReviewerAgent()
    report = reviewer.review(diff)
    criticals = [c for c in report.comments if c.severity == "critical"]
    assert len(criticals) > 0


def test_format_pr_body():
    report = ReviewReport(
        approved=True,
        score=88,
        comments=[ReviewComment(
            file="test.py", line=1, severity="warning",
            message="Test warning",
            suggestion="Fix it."
        )],
        summary="Score: 88/100 | APPROVED",
    )
    body = format_pr_body(report, "Fixed 1 issue")
    assert "✅ Approved" in body
    assert "WARNING" in body
    assert "Fix it" in body
