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


# ---------------------------------------------------------------------------
# Step 2: FROM / JOIN table extraction
# ---------------------------------------------------------------------------

def _table_keys(inventory):
    return [table.name for table in inventory.tables]


def test_from_single_table():
    result = collect("SELECT * FROM t1;")
    assert _table_keys(result[0]) == [("t1",)]
    assert result[0].tables[0].mode == "read"
    assert result[0].tables[0].alias is None


def test_join_chain_and_on_columns_are_not_tables():
    sql_text = (
        "SELECT a.x FROM schema.tbl_a AS a "
        "JOIN tbl_b b ON a.id = b.id "
        "LEFT JOIN d.tbl_c c ON c.id = b.id;"
    )
    result = collect(sql_text)[0]
    assert _table_keys(result) == [
        ("schema", "tbl_a"), ("tbl_b",), ("d", "tbl_c")]
    assert [table.alias for table in result.tables] == ["a", "b", "c"]
    assert all(table.mode == "read" for table in result.tables)


def test_comma_separated_identifier_list():
    result = collect("SELECT * FROM t1, t2, schema3.t3;")[0]
    assert _table_keys(result) == [("t1",), ("t2",), ("schema3", "t3")]


def test_quoted_and_qualified_name_normalization():
    sql_text = (
        'SELECT * FROM "Mixed"."Tbl" AS m '
        "JOIN `back_t` b ON 1=1 "
        "JOIN [br_t] c ON 1=1;"
    )
    result = collect(sql_text)[0]
    assert _table_keys(result) == [
        ("Mixed", "Tbl"), ("back_t",), ("br_t",)]
    assert [table.alias for table in result.tables] == ["m", "b", "c"]


def test_derived_table_subquery_is_walked_not_named():
    result = collect("SELECT * FROM (SELECT id FROM inner_t) sub, outer_t;")[0]
    assert _table_keys(result) == [("inner_t",), ("outer_t",)]
    assert all("sub" not in name for name in _table_keys(result))


def test_deeply_nested_subqueries():
    result = collect(
        "select * from (select * from (select * from deep_t) x) y;")[0]
    assert _table_keys(result) == [("deep_t",)]


def test_insert_target_and_select_sources_are_separate_modes():
    sql_text = (
        "INSERT INTO target_t (a, b) SELECT x, y FROM src_t "
        "WHERE x IN (SELECT x FROM src2);"
    )
    result = collect(sql_text)[0]
    by_mode = {}
    for table in result.tables:
        by_mode.setdefault(table.mode, []).append(table.name)
    assert by_mode["write"] == [("target_t",)]
    assert by_mode["read"] == [("src_t",), ("src2",)]


def test_insert_without_column_list():
    result = collect("INSERT INTO t1 SELECT * FROM t2;")[0]
    by_mode = {}
    for table in result.tables:
        by_mode.setdefault(table.mode, []).append(table.name)
    assert by_mode == {"write": [("t1",)], "read": [("t2",)]}


def test_update_target_from_and_set_subquery():
    sql_text = (
        "UPDATE t1 SET a = (SELECT max(a) FROM t2) "
        "FROM t3 WHERE t1.id = t3.id;"
    )
    result = collect(sql_text)[0]
    by_mode = {}
    for table in result.tables:
        by_mode.setdefault(table.mode, []).append(table.name)
    assert by_mode == {"write": [("t1",)], "read": [("t2",), ("t3",)]}


def test_delete_from_and_using_with_join():
    sql_text = (
        "DELETE FROM t1 USING t2 JOIN t3 ON t2.id = t3.id "
        "WHERE t1.id = t2.id;"
    )
    result = collect(sql_text)[0]
    by_mode = {}
    for table in result.tables:
        by_mode.setdefault(table.mode, []).append(table.name)
    assert by_mode == {"write": [("t1",)], "read": [("t2",), ("t3",)]}


def test_values_insert_only_has_target():
    result = collect("INSERT INTO t1 VALUES (1, 2), (3, 4);")[0]
    assert [(t.name, t.mode) for t in result.tables] == [
        (("t1",), "write")]


def test_functions_are_not_tables():
    result = collect("SELECT count(*) FROM t1 WHERE x = my_func(1);")[0]
    assert _table_keys(result) == [("t1",)]


def test_merge_target_and_source():
    sql_text = (
        "MERGE INTO tgt USING src ON tgt.id = src.id "
        "WHEN MATCHED THEN UPDATE SET a = 1;"
    )
    result = collect(sql_text)[0]
    by_mode = {}
    for table in result.tables:
        by_mode.setdefault(table.mode, []).append(table.name)
    assert by_mode == {"write": [("tgt",)], "read": [("src",)]}


def test_union_collects_both_branches():
    result = collect("SELECT id FROM t1 UNION ALL SELECT id FROM t2;")[0]
    assert _table_keys(result) == [("t1",), ("t2",)]


def test_select_list_scalar_subquery_is_covered():
    sql_text = "SELECT (SELECT max(id) FROM t2) AS m, name FROM t1;"
    result = collect(sql_text)[0]
    assert _table_keys(result) == [("t2",), ("t1",)]


def test_parenthesised_join_group_collects_all_tables():
    result = collect(
        "SELECT * FROM a JOIN (b JOIN c ON b.id = c.id) "
        "ON a.id = b.id;")[0]
    assert _table_keys(result) == [("a",), ("b",), ("c",)]


def test_using_column_list_is_not_a_table():
    result = collect("SELECT * FROM a JOIN b USING (id, other_id);")[0]
    assert _table_keys(result) == [("a",), ("b",)]
    assert all("id" not in name for name in _table_keys(result))


def test_table_function_is_not_a_table():
    result = collect(
        "SELECT * FROM unnest(ARRAY[1, 2]) AS u(id) "
        "JOIN real_t r ON 1=1;")[0]
    assert _table_keys(result) == [("real_t",)]
    assert all("unnest" not in name for name in _table_keys(result))


def test_table_function_alias_column_list_not_inventoried():
    result = collect("SELECT * FROM generate_series(1, 5) AS g;")[0]
    assert result.tables == []
