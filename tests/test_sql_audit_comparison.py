from pathlib import Path
import runpy


AUDIT = runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/summarize-sql-audit.py'))


def test_sql_column_types_and_aliases():
    spec = AUDIT['column_spec']
    assert spec('RowVersion rowversion NOT NULL')[1] == {
        'type_name':'timestamp','max_length':8,'is_nullable':False,'is_identity':False}
    assert spec('Payload nvarchar(max) NULL')[1]['max_length'] == -1
    assert spec('Name nvarchar(64) NOT NULL')[1]['max_length'] == 128
    assert spec('CreatedAt datetime2(3) NOT NULL')[1]['scale'] == 3
    assert spec('Amount decimal(18, 2) NOT NULL')[1]['precision'] == 18
    assert spec('CONSTRAINT PK_Test PRIMARY KEY (RunId)') is None


def test_sql_split_preserves_nested_commas_and_quoted_values():
    parts = AUDIT['split_sql']("Amount decimal(18,2), State varchar(16) CHECK (State IN ('a','b')), Id int")
    assert len(parts) == 3


def test_sql_columns_apply_later_add_and_alter(tmp_path):
    first = tmp_path/'001.sql'
    first.write_text('CREATE TABLE control.Test (Id int NOT NULL PRIMARY KEY, Value nvarchar(20) NULL);')
    second = tmp_path/'002.sql'
    second.write_text('ALTER TABLE control.Test ADD Deadline datetime2(3) NULL; ALTER TABLE control.Test ALTER COLUMN Deadline datetime2(3) NOT NULL;')
    tables = AUDIT['expected_columns']([first,second])
    assert set(tables['control.Test']) == {'Id','Value','Deadline'}
    assert tables['control.Test']['Deadline']['is_nullable'] is False