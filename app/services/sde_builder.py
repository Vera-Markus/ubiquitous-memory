"""
Builds data/eve.db from CCP's official SDE (the JSON Lines zip from
developers.eveonline.com/static-data), in the shape the app reads: Fuzzwork's table
and column names (docs/archive/SDE_MIGRATION_PLAN.md, step 9.1).

What goes into the database is declared in sde_tables.py: a Table copies fields
from one JSONL file, a DerivedTable computes its rows (station names). This module
is the engine. It reads each file in the zip once, line by line (mapMoons.jsonl
alone is over 200 MB), writes rows in batches, runs the sanity checks, and only
then moves the finished database to its destination.

A record without a field the tables need raises SdeFormatError naming the file,
line and field. Fields nobody reads are ignored, so CCP adding fields never
breaks a build.
"""
import json
import os
import sqlite3
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple, Union

# progress(stage, done, total): the same callback shape as the database download
ProgressCallback = Callable[[str, int, Optional[int]], None]

INTEGER, REAL, TEXT = "INTEGER", "REAL", "TEXT"
BATCH = 20_000
REPORT_EVERY = 1024 * 1024      # bytes read between progress reports


class SdeFormatError(Exception):
    """The SDE doesn't have something the tables need (a file, or a field in a record)."""

    def __init__(self, file: str, line: Optional[int], problem: str):
        self.file, self.line, self.problem = file, line, problem
        where = f"{file}, line {line}" if line else file
        super().__init__(f"{where}: {problem}")


class SdeCheckError(Exception):
    """The finished database failed a sanity check, so it wasn't installed."""

    def __init__(self, problems: List[str]):
        self.problems = problems
        super().__init__("The built database failed its checks: " + "; ".join(problems))


class SdeRecord(dict):
    """One JSONL record. A missing field raises SdeFormatError with the file and line."""

    def __init__(self, data: dict, file: str, line: int):
        super().__init__(data)
        self.file, self.line = file, line

    def __missing__(self, key):
        raise SdeFormatError(self.file, self.line, f"missing field '{key}'")


# --- reading fields -----------------------------------------------------------------------

def value_at(record: SdeRecord, path: str, source: Optional[dict] = None, optional: bool = False) -> Any:
    """The value at a dotted path ("position.x") in source (default: the record), or None if optional."""
    value: Any = record if source is None else source
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            if optional:
                return None
            raise SdeFormatError(record.file, record.line, f"missing field '{path}'")
        value = value[part]
    return value


def english(record: SdeRecord, path: str, source: Optional[dict] = None, optional: bool = False) -> Optional[str]:
    """The English text of a localized field ({"de": ..., "en": ...})."""
    value = value_at(record, path, source, optional)
    if value is None:
        return None
    if not isinstance(value, dict) or "en" not in value:
        raise SdeFormatError(record.file, record.line, f"field '{path}' has no English text")
    return value["en"]


@dataclass(frozen=True)
class Column:
    sql_type: str
    read: Callable[[SdeRecord, Optional[dict]], Any]     # (record, list item or None) -> value


def key() -> Column:
    """The record's _key (its ID)."""
    return Column(INTEGER, lambda record, item: record["_key"])


def field(path: str, sql_type: str = INTEGER, optional: bool = False) -> Column:
    """A field of the record, or of the list item when the table explodes a list."""
    return Column(sql_type, lambda record, item: value_at(record, path, item, optional))


def en(path: str, optional: bool = False) -> Column:
    """The English text of a localized field."""
    return Column(TEXT, lambda record, item: english(record, path, item, optional))


def flag(path: str) -> Column:
    """A true/false field as 1/0; missing counts as 0."""
    return Column(INTEGER, lambda record, item: int(bool(value_at(record, path, item, optional=True))))


def const(value: Any, sql_type: str) -> Column:
    """The same value in every row (a column the app expects but CCP doesn't have)."""
    return Column(sql_type, lambda record, item: value)


# --- table definitions ------------------------------------------------------------------

@dataclass(frozen=True)
class Table:
    """Rows copied from one JSONL file: one per record, or one per item of a list in it (explode)."""
    name: str
    source: str
    columns: Dict[str, Column]
    primary_key: Tuple[str, ...]
    explode: Optional[str] = None
    where: Optional[Callable[[SdeRecord], bool]] = None
    indexes: Tuple[Tuple[str, ...], ...] = ()

    @property
    def sources(self) -> Tuple[str, ...]:
        return (self.source,)

    @property
    def column_types(self) -> Dict[str, str]:
        return {name: column.sql_type for name, column in self.columns.items()}

    def rows(self, record: SdeRecord) -> Iterator[tuple]:
        if self.where is not None and not self.where(record):
            return
        if self.explode is None:
            yield tuple(column.read(record, None) for column in self.columns.values())
            return
        for item in record.get(self.explode) or ():
            yield tuple(column.read(record, item) for column in self.columns.values())


Reader = Callable[[str], Iterator[SdeRecord]]


@dataclass(frozen=True)
class DerivedTable:
    """Rows computed from several files (and the tables already written), e.g. station names."""
    name: str
    sources: Tuple[str, ...]
    column_types: Dict[str, str]
    primary_key: Tuple[str, ...]
    build: Callable[[Reader, sqlite3.Connection], Iterable[tuple]]
    indexes: Tuple[Tuple[str, ...], ...] = ()


AnyTable = Union[Table, DerivedTable]
Check = Callable[[sqlite3.Connection], List[str]]      # returns the problems it found


# --- the engine -------------------------------------------------------------------------

class _Progress:
    """Counts the bytes read across every file the build uses."""

    def __init__(self, total: int, report: Optional[ProgressCallback]):
        self.total, self.report, self.done, self._last = total, report, 0, 0

    def start(self) -> None:
        if self.report:
            self.report("build", 0, self.total)

    def add(self, count: int) -> None:
        self.done += count
        if self.report and self.done - self._last >= REPORT_EVERY:
            self._last = self.done
            self.report("build", self.done, self.total)

    def finish(self) -> None:
        if self.report:
            self.report("build", self.total, self.total)


def _reader(archive: zipfile.ZipFile, progress: _Progress) -> Reader:
    def read(name: str) -> Iterator[SdeRecord]:
        try:
            member = archive.open(name)
        except KeyError:
            raise SdeFormatError(name, None, "file not found in the SDE archive") from None
        with member:
            for number, line in enumerate(member, 1):
                progress.add(len(line))
                if line.strip():
                    yield SdeRecord(json.loads(line), name, number)
    return read


def _create(db: sqlite3.Connection, table: AnyTable) -> str:
    columns = ", ".join(f"{name} {sql_type}" for name, sql_type in table.column_types.items())
    db.execute(f"CREATE TABLE {table.name} ({columns}, PRIMARY KEY ({', '.join(table.primary_key)}))")
    return f"INSERT INTO {table.name} VALUES ({', '.join('?' * len(table.column_types))})"


def _write(db: sqlite3.Connection, sql: str, rows: Iterable[tuple]) -> None:
    batch: List[tuple] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= BATCH:
            db.executemany(sql, batch)
            batch.clear()
    if batch:
        db.executemany(sql, batch)


def _fill(db: sqlite3.Connection, read: Reader, tables: Sequence[AnyTable]) -> None:
    inserts = {table.name: _create(db, table) for table in tables}

    # Plain tables first, grouped by file so each file is read once.
    by_source: Dict[str, List[Table]] = {}
    for table in tables:
        if isinstance(table, Table):
            by_source.setdefault(table.source, []).append(table)
    for source, group in by_source.items():
        batches: Dict[str, List[tuple]] = {table.name: [] for table in group}
        for record in read(source):
            for table in group:
                batch = batches[table.name]
                batch.extend(table.rows(record))
                if len(batch) >= BATCH:
                    db.executemany(inserts[table.name], batch)
                    batch.clear()
        for name, batch in batches.items():
            if batch:
                db.executemany(inserts[name], batch)

    # Then the derived ones, which may read the plain tables (station names need system names).
    for table in tables:
        if isinstance(table, DerivedTable):
            _write(db, inserts[table.name], table.build(read, db))

    for table in tables:
        for columns in table.indexes:
            db.execute(f"CREATE INDEX ix_{table.name}_{'_'.join(columns)} ON {table.name} ({', '.join(columns)})")


def build_sde(zip_path: Union[str, Path], out_path: Union[str, Path], progress: Optional[ProgressCallback] = None,
              tables: Optional[Sequence[AnyTable]] = None, checks: Optional[Sequence[Check]] = None) -> Dict[str, int]:
    """
    Builds the database at out_path from an SDE zip. Returns the row count of each
    table. tables and checks default to sde_tables.TABLES and sde_tables.CHECKS.
    Raises SdeFormatError or SdeCheckError; out_path is then left as it was.
    """
    if tables is None or checks is None:
        from app.services import sde_tables
        tables = sde_tables.TABLES if tables is None else tables
        checks = sde_tables.CHECKS if checks is None else checks

    out_path = Path(out_path)
    building = out_path.with_name(out_path.name + ".building")
    building.unlink(missing_ok=True)
    db = sqlite3.connect(building)
    try:
        db.execute("PRAGMA journal_mode = OFF")       # a scratch file: nothing to recover if the build fails
        db.execute("PRAGMA synchronous = OFF")
        with zipfile.ZipFile(zip_path) as archive:
            sources = list(dict.fromkeys(source for table in tables for source in table.sources))
            sizes = {info.filename: info.file_size for info in archive.infolist()}
            counter = _Progress(sum(sizes.get(source, 0) for source in sources), progress)
            counter.start()
            _fill(db, _reader(archive, counter), tables)
        db.commit()

        problems = [problem for check in checks for problem in check(db)]
        if problems:
            raise SdeCheckError(problems)
        counts = {table.name: db.execute(f"SELECT COUNT(*) FROM {table.name}").fetchone()[0] for table in tables}
        db.close()
        counter.finish()
        os.replace(building, out_path)
        return counts
    except BaseException:
        db.close()
        building.unlink(missing_ok=True)
        raise
