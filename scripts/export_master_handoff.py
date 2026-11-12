"""Build an offline, revision-labelled ForgeOS master handoff PDF.

Uses canonical repository Markdown; does not manufacture content or inflate pages.
Requires reportlab and pypdf, installed only in the export workflow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.pagesizes import letter
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph,
    Preformatted, Spacer,
)

# Mission first, then current takeover and technical reference, finally the
# cumulative engineering record. No duplicated source documents.
SOURCES = (
    ("Mission and product vision", "docs/PRODUCT_VISION.md"),
    ("Requirements and original constraints", "docs/REQUIREMENTS.md"),
    ("Current engineering takeover dossier", "docs/ENGINEERING_TAKEOVER.md"),
    ("Architecture and trust boundaries", "docs/ARCHITECTURE.md"),
    ("Current roadmap", "docs/ROADMAP.md"),
    ("Handoff quality standard", "docs/HANDOFF_STANDARD.md"),
    ("User and recovery manual", "docs/USER_GUIDE.md"),
    ("Windows build and acceptance", "docs/WINDOWS_BUILD.md"),
    ("Cumulative engineering history", "docs/HANDOFF.md"),
)
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
INLINE_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def git_sha(root: Path) -> str:
    p = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                       text=True, capture_output=True, check=True, timeout=10)
    return p.stdout.strip()


def paragraph_text(line: str) -> str:
    line = INLINE_LINK.sub(lambda m: m.group(1) + " (" + m.group(2) + ")", line)
    line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
    line = re.sub(r"`([^`]+)`", r"\1", line)
    return escape(line).replace("&lt;", "&lt;")


class HandoffDoc(BaseDocTemplate):
    def __init__(self, path: str, revision: str):
        super().__init__(path, pagesize=letter, leftMargin=55, rightMargin=55,
                         topMargin=60, bottomMargin=56, title="ForgeOS Master Engineering Handoff",
                         author="ForgeOS project")
        self.revision = revision
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height,
                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates(PageTemplate(id="normal", frames=[frame], onPage=self.decorate))

    def decorate(self, canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#546477"))
        canvas.drawString(55, 35, "ForgeOS | source " + self.revision[:12])
        canvas.drawRightString(letter[0] - 55, 35, "Page " + str(doc.page))
        canvas.restoreState()


def wrap_code_line(line: str, width: int = 92) -> list[str]:
    """Wrap long code lines without discarding any characters."""
    expanded = line.expandtabs(2)
    if not expanded:
        return [""]
    return [expanded[i:i + width] for i in range(0, len(expanded), width)]


def build(root: Path, destination: Path, minimum: int) -> dict:
    revision = git_sha(root)
    styles = getSampleStyleSheet()
    body = ParagraphStyle("ForgeBody", parent=styles["BodyText"], fontName="Helvetica",
                          fontSize=9.1, leading=13.5, spaceAfter=5, alignment=TA_LEFT)
    heading = [ParagraphStyle("ForgeH" + str(n), parent=styles["Heading2"],
               fontName="Helvetica-Bold", fontSize=max(10, 16 - n),
               leading=max(14, 20 - n), spaceBefore=12, spaceAfter=7,
               keepWithNext=True, textColor=colors.HexColor("#183654"))
               for n in range(1, 7)]
    section = ParagraphStyle("ForgeSection", parent=styles["Heading1"],
                             fontSize=17, leading=21, spaceAfter=12,
                             textColor=colors.HexColor("#183654"))
    code = ParagraphStyle("ForgeCode", parent=body, fontName="Courier",
                          fontSize=7.2, leading=10)
    story = [
        Paragraph("ForgeOS — Master Engineering Handoff", section),
        Paragraph("Mission, current takeover, architecture, decisions, roadmap, user guidance and cumulative engineering evidence", body),
        Paragraph("Source revision: " + escape(revision), body),
        Paragraph("Generated: " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), body),
        Paragraph("This document is a dated repository snapshot, not live GitHub or CI verification. Claims in historical entries must be reconciled with newer commits and test evidence.", body),
        Spacer(1, 16),
        Paragraph("Contents", heading[0]),
    ]
    for idx, (title, path) in enumerate(SOURCES, 1):
        story.append(Paragraph(f"{idx}. {escape(title)} — {escape(path)}", body))
    story.append(PageBreak())
    missing = []
    substantive_words = 0
    for idx, (title, relpath) in enumerate(SOURCES, 1):
        path = root / relpath
        if not path.is_file():
            missing.append(relpath)
            continue
        if idx > 1:
            story.append(PageBreak())
        story.append(Paragraph(f"{idx}. {escape(title)}", section))
        story.append(Paragraph(f"Canonical source: {escape(relpath)}", body))
        fenced = False
        code_lines = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            if FENCE.match(raw):
                if fenced and code_lines:
                    # Use bounded preformatted lines; long source examples are
                    # preserved but wrapped to avoid spilling beyond margins.
                    for chunk_start in range(0, len(code_lines), 12):
                        chunk = code_lines[chunk_start:chunk_start + 12]
                        safe = [line.expandtabs(2)[:110] for line in chunk]
                        story.append(Preformatted("\n".join(safe), code, maxLineLength=100, splitChars=" /"))
                    code_lines = []
                fenced = not fenced
                continue
            if fenced:
                code_lines.append(raw)
                continue
            stripped = raw.strip()
            if not stripped:
                continue
            substantive_words += len(stripped.split())
            match = HEADING.match(stripped)
            if match:
                level = len(match.group(1))
                story.append(Paragraph(paragraph_text(match.group(2)), heading[level - 1]))
            elif stripped.startswith("|"):
                # Keep Markdown table cells readable without a fragile wide
                # table renderer; source remains available for exact columns.
                cells = [v.strip() for v in stripped.strip("|").split("|")]
                if all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells):
                    continue
                story.append(Paragraph("  |  ".join(paragraph_text(v) for v in cells), body))
            else:
                story.append(Paragraph(paragraph_text(stripped), body))
    if missing:
        raise ValueError("Required source documents missing: " + ", ".join(missing))
    destination.parent.mkdir(parents=True, exist_ok=True)
    doc = HandoffDoc(str(destination), revision)
    doc.build(story)
    reader = PdfReader(str(destination))
    pages = len(reader.pages)
    # Page count alone is not a quality guarantee. Also require substantive
    # source material, all chapters, and no suspiciously empty body pages.
    body_pages = max(0, pages - 2)
    if body_pages < minimum:
        raise ValueError(f"Only {body_pages} body pages; need {minimum}")
    if substantive_words < 10000:
        raise ValueError(f"Only {substantive_words} source words; expected >=10000")
    empty = []
    for i, page in enumerate(reader.pages[2:], start=3):
        text = page.extract_text() or ""
        # A section break can leave a short last page; flag rather than fake
        # a pass if the page has almost no meaningful content.
        if len(text.strip()) < 60:
            empty.append(i)
    if empty:
        raise ValueError(f"Nearly empty body pages: {empty}")
    return {
        "status": "PASS",
        "source_sha": revision,
        "pdf_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "pages_total": pages,
        "body_pages": body_pages,
        "source_words": substantive_words,
        "chapters": len(SOURCES),
        "path": str(destination),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-body-pages", type=int, default=35)
    args = parser.parse_args()
    result = build(args.root.resolve(), args.output.resolve(), args.min_body_pages)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
