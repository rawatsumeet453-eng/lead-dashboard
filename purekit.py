"""
purekit — pandas-free helpers shared by the desktop agent AND the website (runs inside Pyodide).

Only the Python standard library + openpyxl (for .xlsx). Keeps behaviour identical to the earlier pandas engine:
  * header naming: blank header -> "Unnamed: <i>", repeated header -> "<name>.1", "<name>.2" (same as pandas)
  * every cell is text ("" = empty); Excel dates become naive datetimes (treated as IST)
  * dates: timestamps with an offset (+0000 / +05:30 / Z) are converted to IST; ISO without offset = IST;
    slash dates auto-detect DD/MM vs MM/DD per column (Meta exports are MM/DD/YYYY)
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
from pathlib import Path

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


# --------------------------------------------------------------------------------------
# reading files
# --------------------------------------------------------------------------------------
def _mangle(headers):
    out, seen = [], {}
    for i, h in enumerate(headers):
        h = "" if h is None else str(h)
        if h.strip() == "" and h == "":
            h = f"Unnamed: {i}"
        base = h
        if base in seen:
            seen[base] += 1
            h = f"{base}.{seen[base]}"
            while h in seen:
                seen[base] += 1
                h = f"{base}.{seen[base]}"
        seen.setdefault(h, 0)
        out.append(h)
    return out


def _cell_text(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, dt.datetime):
        return v  # keep datetime objects (handled by parse_dates)
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day)
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return repr(v)
    return str(v)


class Table:
    """A sheet tab: .name, .columns (list), .rows (list of dict col -> str/datetime)."""
    __slots__ = ("name", "columns", "rows")

    def __init__(self, name, columns, rows):
        self.name, self.columns, self.rows = name, columns, rows

    def col(self, c):
        return [r.get(c, "") for r in self.rows] if c else []

    def __len__(self):
        return len(self.rows)


def _table_from_matrix(name, matrix):
    # first non-empty row = header
    matrix = [list(r) for r in matrix]
    while matrix and not any(_cell_text(x) not in ("",) for x in matrix[0]):
        matrix.pop(0)
    if not matrix:
        return None
    width = max(len(r) for r in matrix)
    head = [(_cell_text(x) if not isinstance(_cell_text(x), dt.datetime) else str(x)) for x in matrix[0]] + [""] * (width - len(matrix[0]))
    # pandas drops trailing completely-unnamed AND empty columns? No — it keeps them as Unnamed. Keep all.
    cols = _mangle(head)
    rows = []
    for r in matrix[1:]:
        vals = [_cell_text(x) for x in r] + [""] * (width - len(r))
        if not any((v if isinstance(v, str) else "x") .strip() for v in vals):
            continue
        rows.append({c: v for c, v in zip(cols, vals)})
    return Table(name, cols, rows)


def read_tables(path: Path | str, data: bytes | None = None) -> list[Table]:
    """Read CSV / TSV / XLSX into Tables. `data` lets the website pass uploaded bytes."""
    path = Path(path)
    raw = data if data is not None else path.read_bytes()
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        import openpyxl  # pure python, works in Pyodide
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        out = []
        for ws in wb.worksheets:
            t = _table_from_matrix(ws.title, ws.iter_rows(values_only=True))
            if t is not None and t.rows:
                out.append(t)
        wb.close()
        return out
    if suffix == ".xls":
        raise ValueError(f"{path.name}: old .xls format — please save / download as .xlsx")
    text = None
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            if enc == "utf-16" and "\x00" in text:
                continue
            break
        except UnicodeError:
            continue
    if text is None:
        raise ValueError(f"Could not read {path.name}")
    sample = text[:20000]
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except csv.Error:
        delim = "\t" if sample.count("\t") > sample.count(",") else ","
    matrix = list(csv.reader(io.StringIO(text), delimiter=delim))
    t = _table_from_matrix(path.stem, matrix)
    if t is None or len(t.columns) < 2:
        raise ValueError(f"Could not read {path.name}")
    return [t]


# --------------------------------------------------------------------------------------
# values
# --------------------------------------------------------------------------------------
def to_num(v) -> float:
    if v is None or isinstance(v, dt.datetime):
        return 0.0
    s = re.sub(r"[^0-9.\-]", "", str(v))
    try:
        return float(s) if s not in ("", "-", ".", "-.") else 0.0
    except ValueError:
        return 0.0


_TZ_RX = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)\s*(Z|[+-]\d{2}:?\d{2})$")
_ISO_RX = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ](\d{1,2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?)?$")
_SLASH_RX = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})(?:[ ,T]+(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?\s*([ap]m)?)?$", re.I)


def _mk(y, m, d, hh=0, mm=0, ss=0):
    try:
        y = int(y)
        if y < 100:
            y += 2000
        return dt.datetime(y, int(m), int(d), int(hh or 0), int(mm or 0), int(ss or 0))
    except (ValueError, TypeError):
        return None


def parse_date_one(v, prefer="dayfirst"):
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v.replace(tzinfo=None) if v.tzinfo is None else v.astimezone(IST).replace(tzinfo=None)
    s = str(v).strip()
    if not s:
        return None
    m = _TZ_RX.match(s)
    if m:
        off = m.group(3)
        off = "+00:00" if off == "Z" else (off if ":" in off else off[:3] + ":" + off[3:])
        try:
            d = dt.datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}{off}")
            return d.astimezone(IST).replace(tzinfo=None)
        except ValueError:
            return None
    m = _ISO_RX.match(s)
    if m:
        return _mk(m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), m.group(6))
    m = _SLASH_RX.match(s)
    if m:
        a, b, y, hh, mi, ss, ap = m.groups()
        hh = int(hh or 0)
        if ap:
            hh = hh % 12 + (12 if ap.lower() == "pm" else 0)
        d1, m1 = (a, b) if prefer == "dayfirst" else (b, a)
        r = _mk(y, m1, d1, hh, mi, ss)
        if r is None:  # e.g. 10/29/2026 read day-first -> try the other way, like pandas "mixed"
            r = _mk(y, d1, m1, hh, mi, ss)
        return r
    return None


def parse_dates(values, prefer="auto"):
    """Column-level parse (auto-detects DD/MM vs MM/DD for slash dates)."""
    if prefer == "auto":
        a_big = b_big = False
        for v in values:
            if isinstance(v, str):
                m = re.match(r"^\s*(\d{1,2})[/\-.](\d{1,2})[/\-.]\d{2,4}", v)
                if m:
                    a_big |= int(m.group(1)) > 12
                    b_big |= int(m.group(2)) > 12
        prefer = "monthfirst" if (b_big and not a_big) else "dayfirst"
    return [parse_date_one(v, prefer) for v in values]


def day(d):
    return d.replace(hour=0, minute=0, second=0, microsecond=0) if d else None
