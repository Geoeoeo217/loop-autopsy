from loop_autopsy.report_accessibility import (
    accessibility_issues,
    is_accessible_report,
)


def test_accessible_html_passes_basic_checks():
    html = """<!doctype html>
<html lang="en">
<head><title>Loop Autopsy</title></head>
<body><p>Report</p></body>
</html>"""

    assert is_accessible_report(html)
    assert accessibility_issues(html) == []


def test_missing_language_is_reported():
    html = "<html><head><title>Report</title></head><body></body></html>"

    issues = accessibility_issues(html)

    assert any("language" in issue for issue in issues)


def test_image_without_alt_is_reported():
    html = """<html lang="en">
    <head><title>Report</title></head>
    <body><img src="plot.png"></body>
    </html>"""

    issues = accessibility_issues(html)

    assert any("alt attribute" in issue for issue in issues)


def test_title_is_required():
    html = '<html lang="en"><body><p>Report</p></body></html>'

    issues = accessibility_issues(html)

    assert any("<title>" in issue for issue in issues)
