from __future__ import annotations

import html
import re
import unicodedata
from html.parser import HTMLParser
from io import BytesIO

import markdown
from weasyprint import HTML

from .repository import Artifact


class RemoteResourceBlocked(ValueError):
    """PDF export attempted to resolve a resource outside the generated document."""


def safe_export_filename(title: str, *, extension: str) -> str:
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", normalized).strip("._-").lower()
    stem = re.sub(r"-{2,}", "-", stem)[:100] or "highland-artifact"
    return f"{stem}.{extension.lstrip('.')}"


def export_markdown(artifact: Artifact) -> str:
    body = artifact.content.rstrip()
    sources = [
        "## Sources",
        "",
        *[
            (
                f"- [{citation.id}] {citation.title or citation.source_id} — "
                f"{citation.source_url}"
                + (
                    f" (updated {citation.updated_at.isoformat()})"
                    if citation.updated_at is not None
                    else ""
                )
            )
            for citation in artifact.citations
        ],
        "",
        (
            f"_Generated: {artifact.updated_at.isoformat()} · Highland artifact "
            f"{artifact.id} revision {artifact.revision}_"
        ),
    ]
    return f"{body}\n\n" + "\n".join(sources).rstrip() + "\n"


def export_pdf(artifact: Artifact) -> bytes:
    canonical = export_markdown(artifact)
    rendered = markdown.markdown(
        canonical,
        extensions=["tables", "fenced_code"],
        output_format="html",
    )
    sanitized = _sanitize_html(rendered)
    document = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
@page {{ size: A4; margin: 20mm; }}
body {{ color: #17211e; font: 10.5pt/1.5 "DejaVu Sans", sans-serif; }}
h1, h2, h3, h4 {{ color: #173f35; page-break-after: avoid; }}
h1 {{ font-size: 24pt; }} h2 {{ font-size: 17pt; margin-top: 20pt; }}
table {{ width: 100%; border-collapse: collapse; margin: 12pt 0; }}
th, td {{ padding: 6pt; border: 0.6pt solid #668078; text-align: left; }}
th {{ background: #e4f2ed; }}
code, pre {{ font-family: "DejaVu Sans Mono", monospace; }}
pre {{ padding: 8pt; background: #f2f5f4; white-space: pre-wrap; }}
</style>
</head>
<body>{sanitized}</body>
</html>"""
    output = BytesIO()
    HTML(string=document, url_fetcher=_deny_resource_fetch).write_pdf(output)
    return output.getvalue()


def _deny_resource_fetch(url: str, timeout: int = 10, ssl_context: object = None) -> dict:
    del timeout, ssl_context
    raise RemoteResourceBlocked(f"External resource loading is disabled: {url}")


class _SafeHTML(HTMLParser):
    allowed = frozenset({
        "p",
        "br",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "strong",
        "em",
        "blockquote",
        "ul",
        "ol",
        "li",
        "code",
        "pre",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "hr",
    })
    void = frozenset({"br", "hr"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in self.allowed:
            self.output.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.allowed and tag not in self.void:
            self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.output.append(html.escape(data))


def _sanitize_html(value: str) -> str:
    parser = _SafeHTML()
    parser.feed(value)
    parser.close()
    return "".join(parser.output)
