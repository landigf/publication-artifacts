"""Offline structural checks for the source-linked competitor review."""
from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path

from harness.validate_results import ROOT


VENDOR_HEADER = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
STATUS = re.compile(r"\bverify:\s*([A-Za-z_-]+)\b")
SOURCE_LINE = re.compile(r"^-\s+\*\*Sources?:\*\*\s*(.+)$", re.MULTILINE)
URL = re.compile(r"https?://[^\s)>]+")
FINAL_STATUSES = {"CONFIRMED", "PARTIAL", "UNSUPPORTED"}


class VentureValidationError(ValueError):
    """Raised when the competitor map is structurally incomplete."""


def validate_competitors(path: Path, *, expected_vendors: int = 36) -> dict:
    text = path.read_text(encoding="utf-8")
    matches = list(VENDOR_HEADER.finditer(text))
    if len(matches) != expected_vendors:
        raise VentureValidationError(
            f"expected {expected_vendors} vendor headers, found {len(matches)}"
        )

    errors: list[str] = []
    statuses: Counter[str] = Counter()
    source_count = 0
    names: list[str] = []
    for index, match in enumerate(matches):
        header = match.group(1).strip()
        block_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.end():block_end]
        name = header.split("  `", 1)[0].strip()
        names.append(name)

        status_match = STATUS.search(header)
        if not status_match:
            errors.append(f"{name}: missing verify status in vendor header")
        else:
            status = status_match.group(1).upper()
            statuses[status] += 1
            if status not in FINAL_STATUSES:
                errors.append(
                    f"{name}: non-final verify status {status!r}; "
                    f"expected one of {sorted(FINAL_STATUSES)}"
                )

        source_match = SOURCE_LINE.search(block)
        if not source_match:
            errors.append(f"{name}: missing Sources line")
        else:
            urls = URL.findall(source_match.group(1))
            source_count += len(urls)
            if not urls:
                errors.append(f"{name}: Sources line contains no HTTP(S) URL")

    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        errors.append(f"duplicate vendor headers: {duplicates}")
    if statuses.get("UNCHECKED", 0):
        errors.append(f"unchecked vendor entries: {statuses['UNCHECKED']}")
    if errors:
        raise VentureValidationError("; ".join(errors))
    return {
        "vendors": len(matches),
        "statuses": dict(sorted(statuses.items())),
        "source_urls": source_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate competitor-map vendor/status/source structure",
    )
    parser.add_argument(
        "--competitors",
        default=str(ROOT / "venture" / "competitors.md"),
    )
    parser.add_argument("--expected-vendors", type=int, default=36)
    args = parser.parse_args()
    try:
        summary = validate_competitors(
            Path(args.competitors), expected_vendors=args.expected_vendors,
        )
    except (OSError, VentureValidationError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    statuses = ",".join(
        f"{key.lower()}={value}" for key, value in summary["statuses"].items()
    )
    print(
        f"PASS: vendors={summary['vendors']} source_urls={summary['source_urls']} "
        f"statuses={statuses} unchecked=0"
    )


if __name__ == "__main__":
    main()
