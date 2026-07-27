"""BOM CSV parser with flexible header matching and reference-group expansion."""
from __future__ import annotations

import csv
import io
import re

from pydantic import BaseModel, Field

_HEADER_ALIASES = {
    "reference": {"reference", "references", "designator", "designators", "refdes", "ref"},
    "value": {"value", "val", "comp_value"},
    "footprint": {"footprint", "package", "fp"},
    "mpn": {"mpn", "part number", "part_number", "partnumber", "manufacturer part number",
            "manufacturer part", "mfr part", "mfr part no", "mfg part number"},
    "manufacturer": {"manufacturer", "mfr", "mfg", "brand"},
    "description": {"description", "desc", "comment"},
    "quantity": {"quantity", "qty", "count"},
}

_RANGE_RE = re.compile(r"^([A-Za-z]+)(\d+)\s*[-–]\s*(?:[A-Za-z]+)?(\d+)$")


class BomRow(BaseModel):
    reference: str
    value: str = ""
    footprint: str = ""
    mpn: str = ""
    manufacturer: str = ""
    description: str = ""
    fields: dict[str, str] = Field(default_factory=dict)


def _expand_references(cell: str) -> list[str]:
    """"R1, R2" -> [R1, R2]; "R1-R3" -> [R1, R2, R3]."""
    refs: list[str] = []
    for part in re.split(r"[,;\s]+", cell.strip()):
        if not part:
            continue
        m = _RANGE_RE.match(part)
        if m:
            prefix, start, end = m.group(1), int(m.group(2)), int(m.group(3))
            if start <= end and end - start <= 500:
                refs.extend(f"{prefix}{i}" for i in range(start, end + 1))
                continue
        refs.append(part)
    return refs


def parse_bom_csv(text: str) -> list[BomRow]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        return []

    header = [cell.strip().lower() for cell in rows[0]]
    col_map: dict[int, str] = {}
    for idx, name in enumerate(header):
        for canonical, aliases in _HEADER_ALIASES.items():
            if name in aliases:
                col_map[idx] = canonical
                break
    if "reference" not in col_map.values():
        return []  # unusable BOM; caller records a parser warning

    out: list[BomRow] = []
    for row in rows[1:]:
        record: dict[str, str] = {}
        extras: dict[str, str] = {}
        for idx, cell in enumerate(row):
            cell = cell.strip()
            canonical = col_map.get(idx)
            if canonical:
                record[canonical] = cell
            elif idx < len(header) and header[idx] and cell:
                extras[header[idx]] = cell
        for ref in _expand_references(record.get("reference", "")):
            out.append(
                BomRow(
                    reference=ref,
                    value=record.get("value", ""),
                    footprint=record.get("footprint", ""),
                    mpn=record.get("mpn", ""),
                    manufacturer=record.get("manufacturer", ""),
                    description=record.get("description", ""),
                    fields=extras,
                )
            )
    return out
