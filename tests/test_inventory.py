# Tests for sqlparse.inventory: table/column reference collection.

import pytest

from sqlparse.inventory import (
    ColumnRef,
    StatementInventory,
    TableRef,
    collect,
)


def test_module_exposes_collect_and_result_types():
    # skeleton smoke test: the module imports and the result dataclasses
    # expose the documented attributes.
    inv = StatementInventory(type="SELECT")
    assert inv.type == "SELECT"
    assert inv.tables == []
    assert inv.columns == []
    ref = TableRef(name=("schema", "tbl"), alias="t", mode="read", count=1)
    assert ref.key == ("schema", "tbl")
    col = ColumnRef(name="id", qualifier="t")
    assert col.name == "id" and col.qualifier == "t"


@pytest.mark.xfail(reason="collect is implemented incrementally", strict=True,
                   raises=NotImplementedError)
def test_collect_skeleton():
    collect("SELECT 1;")
