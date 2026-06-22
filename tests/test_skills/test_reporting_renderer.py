"""Tests for skills/reporting/renderer.py"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from skills.reporting.renderer import ReportRenderer

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "skills" / "reporting" / "templates"


@pytest.fixture
def renderer():
    return ReportRenderer(templates_dir=TEMPLATES_DIR)


class TestRenderHtmlBase:
    """test_render_html_base — verify NKP branding in output."""

    def test_render_html_base(self, renderer: ReportRenderer):
        html = renderer.render_html("base.html", {"report_date": "March 2026"})
        assert "NKP MEDICAL MARKETING" in html
        assert "NKP" in html
        assert "#174B6A" in html  # primary brand colour present in CSS


class TestRenderHtmlWithBlocks:
    """test_render_html_with_blocks — verify valid HTML structure."""

    def test_render_html_with_blocks(self, renderer: ReportRenderer):
        html = renderer.render_html("base.html", {"report_date": "March 2026"})
        assert html.strip().startswith("<!DOCTYPE html>")
        assert "<html" in html
        assert "</html>" in html
        assert "<body" in html
        assert "</body>" in html


class TestSaveHtml:
    """test_save_html — verify file saved to disk."""

    def test_save_html(self, renderer: ReportRenderer, tmp_path: Path):
        html = renderer.render_html("base.html", {"report_date": "March 2026"})
        saved = renderer.save(html, tmp_path, "report.html", pdf=False)
        assert "html" in saved
        assert saved["html"].exists()
        content = saved["html"].read_text(encoding="utf-8")
        assert "NKP" in content


class TestSaveCreatesDirectory:
    """test_save_creates_directory — verify mkdir is called for non-existent dirs."""

    def test_save_creates_directory(self, renderer: ReportRenderer, tmp_path: Path):
        new_dir = tmp_path / "nested" / "output"
        assert not new_dir.exists()
        html = renderer.render_html("base.html", {"report_date": "March 2026"})
        renderer.save(html, new_dir, "report.html", pdf=False)
        assert new_dir.exists()
        assert (new_dir / "report.html").exists()


class TestRenderPdfGraceful:
    """test_render_pdf_graceful — verify no crash when weasyprint is missing."""

    def test_render_pdf_graceful(self, renderer: ReportRenderer):
        html = renderer.render_html("base.html", {"report_date": "March 2026"})
        # Simulate weasyprint not installed by blocking the import
        with patch.dict(sys.modules, {"weasyprint": None}):
            result = renderer.render_pdf(html)
        # Should return None gracefully, not raise
        assert result is None
