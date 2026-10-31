"""Load SEC `companyfacts` JSON into `xbrl_facts`.

Each company's rows are replaced in one transaction, so a reload is idempotent and a failed
one leaves the previous load in place. Only the taxonomies the concept map can use are kept.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import psycopg

TAXONOMIES = ("us-gaap", "dei", "srt")
COLUMNS = (
    "cik, accession, taxonomy, concept, unit, value, period_start, period_end,"
    " fy, fp, form, filed, frame"
)


@dataclass(frozen=True)
class LoadStats:
    cik: int
    rows: int
    duplicates: int


def fact_rows(cik: int, companyfacts: dict[str, Any]) -> Iterator[tuple[Any, ...]]:
    """Rows in COLUMNS order. A fact repeated under the same key (it happens when SEC assigns a
    frame to one copy only) is yielded once."""
    seen: set[tuple[Any, ...]] = set()
    for taxonomy, concepts in companyfacts.get("facts", {}).items():
        if taxonomy not in TAXONOMIES:
            continue
        for concept, body in concepts.items():
            for unit, facts in body.get("units", {}).items():
                for f in facts:
                    key = (f["accn"], taxonomy, concept, unit, f.get("start"), f["end"])
                    if key in seen:
                        continue
                    seen.add(key)
                    yield (
                        cik, f["accn"], taxonomy, concept, unit, f["val"], f.get("start"),
                        f["end"], f.get("fy"), f.get("fp"), f.get("form"), f.get("filed"),
                        f.get("frame"),
                    )  # fmt: skip


def load_company(
    conn: psycopg.Connection[Any], cik: int, companyfacts: dict[str, Any]
) -> LoadStats:
    total = sum(
        len(facts)
        for taxonomy, concepts in companyfacts.get("facts", {}).items()
        if taxonomy in TAXONOMIES
        for body in concepts.values()
        for facts in body.get("units", {}).values()
    )
    n = 0
    with conn.transaction():
        conn.execute("DELETE FROM xbrl_facts WHERE cik = %s", (cik,))
        with conn.cursor().copy(f"COPY xbrl_facts ({COLUMNS}) FROM STDIN") as copy:
            for row in fact_rows(cik, companyfacts):
                copy.write_row(row)
                n += 1
        if n == 0:  # an error or truncated body: roll back the delete, keep the last load
            raise ValueError(f"CIK {cik}: companyfacts has no usable facts; keeping existing rows")
    return LoadStats(cik, n, total - n)
