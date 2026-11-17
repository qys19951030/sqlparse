# Tests for sqlparse.inventory: table/column reference collection.

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
