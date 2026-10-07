"""CLI: Figma node -> PDF with real, extractable text, with built-in verification."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .extract import ExtractError, extract
from .figma_client import FigmaAPIError, get_node
from .fonts import FontRegistry
from .pdf_builder import build_pdf
from .tracking import find_risky_runs, format_ten_percent_hint, format_warnings
from .verify import verify


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--token",
        default=os.environ.get("FIGMA_TOKEN"),
        help="Figma personal access token (or set FIGMA_TOKEN env var)",
    )
    p.add_argument("--file-key", required=True, help="Figma file key (the part of the file URL after /design/)")
    p.add_argument("--node-id", required=True, help="Figma node id to export, e.g. 12:34")
    p.add_argument("-o", "--output", default="output/cv.pdf", help="Output PDF path")
    p.add_argument(
        "--cache-node-json",
        default=None,
        help="Optional path to cache/reuse the raw Figma node JSON, to avoid "
        "re-hitting the API on repeated runs while iterating on the renderer",
    )
    p.add_argument(
        "--tighten-word-gaps",
        action="store_true",
        help="Narrow the gap between words in letter-spaced text so layout-based extractors "
        "(e.g. poppler's pdftotext) keep each tracked line together",
    )
    p.add_argument("--no-verify", action="store_true", help="Skip the post-render text-extraction check")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    node = None
    if args.cache_node_json and os.path.exists(args.cache_node_json):
        with open(args.cache_node_json) as f:
            node = json.load(f)

    if node is None:
        if not args.token:
            print(
                "error: no Figma token. Pass --token or set FIGMA_TOKEN.",
                file=sys.stderr,
            )
            return 1
        try:
            node = get_node(args.file_key, args.node_id, args.token)
        except FigmaAPIError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if args.cache_node_json:
            os.makedirs(os.path.dirname(args.cache_node_json) or ".", exist_ok=True)
            with open(args.cache_node_json, "w") as f:
                json.dump(node, f)

    warnings: list[str] = []
    try:
        doc = extract(node, warnings)
    except ExtractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    fonts = FontRegistry()
    tracked_lines = build_pdf(doc, args.output, fonts, warnings, tighten_word_gaps=args.tighten_word_gaps)

    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    risks = find_risky_runs(doc, fonts, args.tighten_word_gaps)
    for line in format_warnings(risks, args.tighten_word_gaps):
        print(line, file=sys.stderr)

    print(
        f"wrote {args.output} ({len(doc.text_runs)} text runs, {len(doc.rects)} rects, "
        f"{len(doc.vectors)} vector icons)"
    )

    if fonts.unsupported_families:
        print(
            f"warning: no embedded font source for: {', '.join(sorted(fonts.unsupported_families))} "
            "-- fell back to a core PDF font (Helvetica). Text stays extractable, but visual "
            "fidelity for these runs is off.",
            file=sys.stderr,
        )

    if not args.no_verify:
        results = verify(args.output, doc, tracked_lines)
        all_ok = True
        for r in results:
            status = "OK" if r.ok else "ISSUES"
            print(f"[{r.engine}] {status}: missing={len(r.missing_words)} extra={len(r.extra_words)} fragmented_runs={r.fragmented_word_count} split_tracked_lines={len(r.split_lines)}")
            if not r.ok:
                all_ok = False
                if r.missing_words:
                    print(f"  missing (first 20): {r.missing_words[:20]}")
                if r.fragmented_word_count:
                    print(f"  {r.fragmented_word_count} run(s) of text look letter-split in extraction")
                if r.split_lines:
                    print(f"  {len(r.split_lines)} tracked line(s) were split across lines by this extractor, e.g. {r.split_lines[0]!r} -- see the tracking warning above for fixes")
        if not all_ok:
            split = {line for r in results for line in r.split_lines}
            for line in format_ten_percent_hint(risks, split):
                print(line, file=sys.stderr)
            print("verification found issues -- see above", file=sys.stderr)
            return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
