# Tests for sqlparse.inventory: table/column reference collection.

import sqlparse
from sqlparse.inventory import (
    ColumnRef,
    StatementInventory,
    TableRef,
    collect,
)


def test_result_structures_and_entry_point():
    inv = StatementInventory(type="SELECT")
    assert inv.type == "SELECT"
    assert inv.tables == []
    assert inv.columns == []
    ref = TableRef(name=("schema", "tbl"), alias="t", mode="read", count=1)
    assert ref.key == ("schema", "tbl")
    col = ColumnRef(name="id", qualifier="t")
    assert col.name == "id" and col.qualifier == "t"
    assert isinstance(collect("SELECT 1"), list)


# ---------------------------------------------------------------------------
# Step 1: statement traversal and type extraction
# ---------------------------------------------------------------------------

def test_collect_returns_one_entry_per_statement_like_split():
    sql_text = (
        "SELECT * FROM t1; "
        "INSERT INTO t2 VALUES (1); "
        "UPDATE t3 SET a = 1; "
        "DELETE FROM t4; "
    )
    result = collect(sql_text)
    assert len(result) == len(sqlparse.split(sql_text))
    assert [item.type for item in result] == [
        "SELECT", "INSERT", "UPDATE", "DELETE"]
    assert all(isinstance(item, StatementInventory) for item in result)


def test_collect_type_matches_statement_get_type():
    sql_text = (
        "WITH c AS (SELECT 1) SELECT * FROM c; "
        "CREATE TABLE t (a int); "
        "DROP TABLE t; "
        "MERGE INTO t USING s ON 1=1 "
        "WHEN MATCHED THEN UPDATE SET a=1; "
        "TRUNCATE TABLE t; "
    )
    statements = sqlparse.parse(sql_text)
    result = collect(sql_text)
    assert [item.type for item in result] == [
        stmt.get_type() for stmt in statements]


def test_collect_empty_and_whitespace_statements():
    result = collect(";;;   ;")
    assert len(result) == len(sqlparse.split(";;;   ;"))
    assert all(item.type == "UNKNOWN" for item in result)
    assert all(item.tables == [] and item.columns == [] for item in result)
