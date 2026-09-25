"""database/schema.sql (боевая схема MariaDB) должна совпадать с ORM-моделями."""
from __future__ import annotations

import re
from pathlib import Path

from app.models import Base

SCHEMA = Path(__file__).resolve().parents[1] / "backend" / "database" / "schema.sql"


def parse_schema() -> dict[str, dict]:
    sql = SCHEMA.read_text(encoding="utf-8")
    sql = re.sub(r"--[^\n]*", "", sql)
    tables: dict[str, dict] = {}
    for name, body in re.findall(r"CREATE TABLE (\w+) \((.*?)\n\) ENGINE=InnoDB;", sql, flags=re.S):
        cols, nullable, keys = [], {}, []
        for line in body.split("\n"):
            line = line.strip().rstrip(",")
            if not line:
                continue
            first = line.split()[0]
            if first in ("PRIMARY", "UNIQUE", "KEY", "CONSTRAINT"):
                keys.append(line)
                continue
            cols.append(first)
            nullable[first] = "NOT NULL" not in line
        tables[name] = {"columns": cols, "nullable": nullable, "keys": keys}
    return tables


def test_every_orm_table_exists_with_same_columns():
    schema = parse_schema()
    assert set(schema) == set(Base.metadata.tables), set(schema) ^ set(Base.metadata.tables)
    for name, table in Base.metadata.tables.items():
        assert schema[name]["columns"] == [c.name for c in table.columns], name


def test_nullability_matches():
    schema = parse_schema()
    for name, table in Base.metadata.tables.items():
        for col in table.columns:
            if col.primary_key:
                continue
            assert schema[name]["nullable"][col.name] == col.nullable, f"{name}.{col.name}"


def test_unique_constraints_are_declared():
    schema = parse_schema()
    for name, table in Base.metadata.tables.items():
        sql_keys = " ".join(schema[name]["keys"])
        for col in table.columns:
            if col.unique:
                assert f"({col.name})" in sql_keys and "UNIQUE" in sql_keys, f"{name}.{col.name}"
        for constraint in table.constraints:
            if constraint.__class__.__name__ == "UniqueConstraint" and constraint.name:
                assert constraint.name in sql_keys, f"{name}: {constraint.name}"
