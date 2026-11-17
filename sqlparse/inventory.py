#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

"""Collect table and column references from SQL statements.

The module exposes a single public entry point, :func:`collect`, which
takes a SQL string and returns one :class:`StatementInventory` per
parsed statement, in statement order and therefore matching
:func:`sqlparse.split`.

Table names are normalized as follows:

* qualified names are split into separate segments instead of being
  joined into one string (``schema.table`` becomes ``('schema', 'table')``);
* unquoted segments are lower-cased;
* backtick, double-quote and square-bracket quoting is stripped while the
  quoted content itself is kept unchanged;
* an alias is recorded separately and never mixed into the table name.

Names defined by a ``WITH`` clause are common table expressions, not
physical tables.  They never appear in the table inventory; references
to them resolve to the physical tables used by their definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlparse import sql

__all__ = ["ColumnRef", "StatementInventory", "TableRef", "collect"]


@dataclass
class ColumnRef:
    """A column reference inside a statement.

    Attributes:
        name: normalized column name.
        qualifier: qualifier prefix of a qualified column (e.g. the
            alias ``t`` of ``t.col``), or ``None`` for unqualified
            columns.
    """

    name: str
    qualifier: str | None = None


@dataclass
class TableRef:
    """A physical table reference inside a statement.

    Attributes:
        name: normalized name segments, outermost qualifier first.
        alias: alias used for this table, or ``None``.
        mode: ``'read'`` for query sources, ``'write'`` for targets.
        count: number of occurrences in the statement.
    """

    name: tuple[str, ...]
    alias: str | None = None
    mode: str = "read"
    count: int = 0

    @property
    def key(self) -> tuple[str, ...]:
        """Merge key: the normalized table name."""
        return self.name


@dataclass
class StatementInventory:
    """Reference inventory of a single statement.

    Attributes:
        type: statement type as returned by
            :meth:`sqlparse.sql.Statement.get_type`.
        tables: table references, ordered by first occurrence.
        columns: column references, ordered by first occurrence.
    """

    type: str
    tables: list[TableRef] = field(default_factory=list)
    columns: list[ColumnRef] = field(default_factory=list)


class _Collector:
    """Collects the references of one statement."""

    def __init__(self, statement: sql.Statement):
        self.statement = statement
        self.tables: list[TableRef] = []
        self.columns: list[ColumnRef] = []

    def collect(self) -> StatementInventory:
        return StatementInventory(
            type=self.statement.get_type(),
            tables=self.tables,
            columns=self.columns,
        )


def collect(sql_text: str) -> list[StatementInventory]:
    """Collect table and column references from *sql_text*.

    :param sql_text: A string containing one or more SQL statements.
    :returns: one :class:`StatementInventory` per statement, in the
        same order as :func:`sqlparse.split`.
    """
    # Imported lazily to avoid a circular import: the package
    # initializer imports this module.
    from sqlparse import parse

    return [_Collector(statement).collect() for statement in parse(sql_text)]
