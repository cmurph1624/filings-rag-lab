"""Download a filing's primary document (plus EX-99 exhibits for 8-Ks) and extract its text."""

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

from filings_rag.edgar.client import EdgarClient
from filings_rag.edgar.resolve import ManifestRow, periodic_filings
from filings_rag.extract.html import EXTRACTOR, html_to_text

ARCHIVES = "https://www.sec.gov/Archives/edgar/data"


@dataclass(frozen=True)
class FilingFile:
    seq: str
    type: str
    filename: str
    url: str


def filing_files(index_html: bytes) -> list[FilingFile]:
    """Rows of the 'Document Format Files' table on an EDGAR `-index.htm` page."""
    soup = BeautifulSoup(index_html, "lxml")
    table = soup.find("table", summary="Document Format Files")
    if table is None:
        raise ValueError("no 'Document Format Files' table on index page")
    files = []
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        link = tr.find("a")
        if len(tds) < 4 or link is None:
            continue
        href = str(link["href"]).removeprefix("/ix?doc=")
        files.append(
            FilingFile(
                seq=tds[0].get_text(strip=True),
                type=tds[3].get_text(strip=True),
                filename=href.rsplit("/", 1)[-1],
                url="https://www.sec.gov" + href,
            )
        )
    return files


def select_files(files: list[FilingFile], primary: str) -> list[FilingFile]:
    """The primary document first, then EX-13 / EX-99 exhibits, in filing order.

    EX-99.x carries 8-K press releases; EX-13 is the annual report some filers (CVS through
    2018) use for their financial statements instead of the 10-K body.
    """
    main = [f for f in files if f.filename == primary]
    if not main:
        raise ValueError(f"primary document {primary} not in filing index")
    exhibits = [
        f for f in files if f.type.upper().startswith(("EX-99", "EX-13")) and f.filename != primary
    ]
    return main + exhibits


def _download(client: EdgarClient, url: str, path: Path) -> bytes:
    if path.exists():
        return path.read_bytes()
    data = client.get_bytes(url, cache=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return data


def build_body(
    parts: list[tuple[FilingFile, bytes]],
) -> tuple[str, list[dict[str, Any]], list[int]]:
    """Concatenate extracted parts. Exhibits get a `[EX-99.1 name.htm]` header line."""
    body = ""
    meta: list[dict[str, Any]] = []
    pages: list[int] = []
    for i, (f, raw) in enumerate(parts):
        ex = html_to_text(raw)
        prefix = "" if i == 0 else f"\n\n[{f.type} {f.filename}]\n"
        start = len(body) + len(prefix)
        body += prefix + ex.text
        meta.append({"type": f.type, "filename": f.filename, "start": start, "end": len(body)})
        pages += [start + o for o in ex.page_offsets]
    return body, meta, pages


def fetch_document(
    conn: psycopg.Connection[Any],
    client: EdgarClient,
    row: ManifestRow,
    ticker: str | None,
    data_dir: Path,
) -> int:
    """Fetch, extract and upsert one manifest row. Returns the body length."""
    if row.source != "edgar" or not row.accession or row.cik is None:
        raise ValueError(f"{row.doc_name}: not an EDGAR row ({row.status}); PDF fallback not built")
    acc = row.accession
    base = f"{ARCHIVES}/{row.cik}/{acc.replace('-', '')}"
    raw_dir = Path("raw") / str(row.cik) / acc
    index = _download(client, f"{base}/{acc}-index.htm", data_dir / raw_dir / "index.htm")
    files = select_files(filing_files(index), row.primary_document)
    parts = [(f, _download(client, f.url, data_dir / raw_dir / f.filename)) for f in files]
    body, meta, pages = build_body(parts)
    if len(body) < 1000:
        raise ValueError(f"{row.doc_name}: extracted only {len(body)} chars")

    form = files[0].type
    fy, fp = None, None
    if form in ("10-K", "10-Q"):
        labels = periodic_filings(client.companyfacts(row.cik)).get(acc)
        if labels:
            _, fy, fp = labels
    conn.execute(
        """INSERT INTO documents
           (accession, doc_name, cik, ticker, company, form_type, fiscal_year, fiscal_period,
            period_end, filing_date, source, raw_dir, parts, page_offsets, body, extractor)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'edgar', %s, %s, %s, %s, %s)
           ON CONFLICT (accession) DO UPDATE SET doc_name = EXCLUDED.doc_name,
             ticker = EXCLUDED.ticker, company = EXCLUDED.company,
             form_type = EXCLUDED.form_type, fiscal_year = EXCLUDED.fiscal_year,
             fiscal_period = EXCLUDED.fiscal_period, period_end = EXCLUDED.period_end,
             filing_date = EXCLUDED.filing_date, raw_dir = EXCLUDED.raw_dir,
             parts = EXCLUDED.parts, page_offsets = EXCLUDED.page_offsets,
             body = EXCLUDED.body, extractor = EXCLUDED.extractor, fetched_at = now()""",
        (
            acc,
            row.doc_name,
            row.cik,
            ticker,
            row.company,
            form,
            fy,
            fp,
            row.report_date or None,
            row.filing_date,
            str(raw_dir),
            Jsonb(meta),
            pages,
            body,
            EXTRACTOR,
        ),
    )
    conn.commit()
    return len(body)


def read_manifest(path: Path) -> list[ManifestRow]:
    with path.open() as fh:
        rows = []
        for r in csv.DictReader(fh):
            r_any: dict[str, Any] = dict(r)
            r_any["cik"] = int(r["cik"]) if r["cik"] else None
            r_any["fiscal_year"] = int(r["fiscal_year"])
            rows.append(ManifestRow(**r_any))
        return rows
