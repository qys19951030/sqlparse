#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

"""Collect table and column references from SQL statements.

The module exposes a single public entry point, :func:`collect`, which
takes a SQL string and returns one :class:`StatementInventory` per
parsed statement (in statement order, matching :func:`sqlparse.split`).

Table name normalization rules (see :class:`TableRef`):

* qualified names are split into segments instead of being joined into
  a single string (``schema.table`` -> ``['schema', 'table']``);
* every segment is lower-cased;
* backtick, double-quote and square-bracket quoting is stripped, the
  quoted content itself is kept unchanged;
* an alias is recorded separately and never mixed into the table name.

Names introduced by ``WITH`` clauses are common table expressions, not
physical tables: they never appear in the table inventory.  References
to CTE names inside the statement (including nested CTEs that reference
earlier ones) are resolved against the enclosing CTE scope and only the
underlying physical tables are counted.
"""

from dataclasses import dataclass, field

from sqlparse import sql
from sqlparse import tokens as T

__all__ = ["collect", "StatementInventory", "TableRef", "ColumnRef"]


@dataclass
class ColumnRef:
    """A column reference inside a statement.

    Attributes:
        name: normalized column name (lower-cased, quoting stripped;
            for qualified columns such as ``t.col`` only the column
            part after the last dot is kept).
        qualifier: qualifier prefix of a qualified column (e.g. the
            table alias ``t`` of ``t.col``), normalized the same way,
            or ``None`` for unqualified columns.
    """

    name: str
    qualifier: str | None = None


@dataclass
class TableRef:
    """A physical table reference inside a statement.

    Attributes:
        name: normalized name segments of the table, from the outermost
            qualifier to the table name (e.g. ``['schema', 'tbl']`` for
            ``schema.tbl``).
        alias: alias under which the table is referenced in this
            occurrence, normalized like a name segment, or ``None``.
        mode: ``'read'`` for references in query clauses (FROM/JOIN/
            USING/...) or ``'write'`` for statement targets
            (INSERT INTO / UPDATE / DELETE FROM / MERGE INTO / ...).
        count: number of occurrences of the table in the statement.
    """

    name: tuple[str, ...]
    alias: str | None = None
    mode: str = "read"
    count: int = 0

    @property
    def key(self) -> tuple[str, ...]:
        """Merge key: normalized table name without alias or mode."""
        return self.name


@dataclass
class StatementInventory:
    """Reference inventory of a single SQL statement.

    Attributes:
        type: statement type as returned by
            :meth:`sqlparse.sql.Statement.get_type`.
        tables: table references, ordered by first occurrence.
        columns: column references, ordered by first occurrence and
            de-duplicated.
    """

    type: str
    tables: list[TableRef] = field(default_factory=list)
    columns: list[ColumnRef] = field(default_factory=list)


def collect(sql_text: str) -> list[StatementInventory]:
    """Collect table and column references from *sql_text*.

    :param sql_text: A string containing one or more SQL statements.
    :returns: A list of :class:`StatementInventory` instances, one per
        statement, in the same order as :func:`sqlparse.split`.
    """
    raise NotImplementedError
