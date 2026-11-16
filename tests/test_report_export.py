from pathlib import Path

from loop_autopsy.report_export import export_html, export_text


def test_export_text_creates_utf8_file(tmp_path):
    path = export_text("Loop autopsy — stuck\nx = 1", tmp_path / "report.txt")

    assert path.exists()
    assert path.read_text(encoding="utf-8") == "Loop autopsy — stuck\nx = 1"


def test_export_html_wraps_existing_markup(tmp_path):
    path = export_html("<div>Loop autopsy</div>", tmp_path / "report.html")

    html = path.read_text(encoding="utf-8")

    assert "<!doctype html>" in html
    assert "<title>Loop Autopsy Report</title>" in html
    assert "<div>Loop autopsy</div>" in html


def test_export_html_escapes_title(tmp_path):
    path = export_html("<p>ok</p>", tmp_path / "report.html", '<demo & "test">')

    html = path.read_text(encoding="utf-8")

    assert "&lt;demo &amp; &quot;test&quot;&gt;" in html
