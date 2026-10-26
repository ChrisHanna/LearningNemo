"""Compare captured live columns and parameters to the ordered migration DDL."""

import json
from pathlib import Path
import re
import runpy


def split_sql(value):
    chunks, start, depth, quoted = [], 0, 0, False
    for index, character in enumerate(value):
        if character == "'":
            quoted = not quoted
        if not quoted:
            depth += (character == '(') - (character == ')')
            if character == ',' and depth == 0:
                chunks.append(value[start:index])
                start = index+1
    return chunks+[value[start:]]


def column_spec(declaration):
    match = re.match(r'\s*(\w+)\s+(bigint|int|smallint|tinyint|bit|char|nchar|varchar|nvarchar|varbinary|binary|datetime2|datetime|date|decimal|numeric|uniqueidentifier|money|sysname|rowversion|timestamp)\b(?:\s*\(([^)]+)\))?(.*)', declaration, re.I|re.S)
    if not match:
        return None
    name, kind, size, remainder = match.groups()
    result = {'type_name':kind.lower(), 'is_nullable':not bool(re.search(r'\bNOT\s+NULL\b|\bPRIMARY\s+KEY\b', remainder,re.I)),
              'is_identity':bool(re.search(r'\bIDENTITY\b',remainder,re.I))}
    if kind.lower() in ('rowversion','timestamp'):
        result.update(type_name='timestamp',max_length=8)
    if kind.lower() in ('char','nchar','varchar','nvarchar','varbinary','binary'):
        result['max_length'] = -1 if size and size.lower()=='max' else int(size or 1)*(2 if kind.lower().startswith('n') else 1)
    if kind.lower() == 'datetime2':
        result['scale'] = int(size or 7)
    if kind.lower() in ('decimal','numeric'):
        values = [int(value) for value in (size or '18,0').split(',')]
        result['precision'],result['scale'] = values[0],values[1] if len(values)>1 else 0
    return name,result


def expected_columns(paths):
    tables = {}
    for path in paths:
        source = re.sub(r'--[^\n]*','',path.read_text())
        for match in re.finditer(r'\bCREATE\s+TABLE\s+(\w+\.\w+)\s*\(', source,re.I):
            depth, end, quoted = 1,match.end(),False
            while depth:
                character = source[end]
                if character == "'":
                    quoted = not quoted
                if not quoted:
                    depth += (character == '(') - (character == ')')
                end += 1
            columns = tables.setdefault(match[1],{})
            for declaration in split_sql(source[match.end():end-1]):
                spec = column_spec(declaration)
                if spec:
                    columns[spec[0]] = spec[1]
        for match in re.finditer(r'\bALTER\s+TABLE\s+(\w+\.\w+)\s+(ADD|ALTER\s+COLUMN)\s+([^;]+);',source,re.I):
            if match[1] not in tables:
                continue
            for declaration in split_sql(match[3]):
                spec = column_spec(declaration)
                if spec:
                    if match[2].upper() != 'ADD':
                        spec[1].pop('is_identity',None)
                    tables[match[1]].setdefault(spec[0],{}).update(spec[1])
    return tables


def summarize(report):
    root = Path(__file__).resolve().parents[1]
    paths = sorted((root/'infra/next-phase/sql').glob('*.sql')) + sorted((root/'infra/next-phase/review-service').glob('*.sql'))
    tables = expected_columns(paths)
    live = {(row['table_name'],row['column_name']):row for row in report['columns']}
    drift = []
    for table, columns in tables.items():
        for name, expected in columns.items():
            actual = live.get((table,name))
            differences = {key:{'expected':value,'live':actual.get(key) if actual else None} for key,value in expected.items() if actual is None or actual.get(key)!=value}
            if differences:
                drift.append({'table':table,'column':name,'differences':differences})
        for actual_table, column in live:
            if actual_table==table and column not in columns:
                drift.append({'table':table,'unexpected_column':column})
    expected = runpy.run_path(str(root/'scripts/run-sql-audit.py'))['expected_schema']()
    parameter_drift = []
    compared = 0
    for name, source in expected['modules'].items():
        if not re.match(r'CREATE\s+(?:OR\s+ALTER\s+)?PROC',source,re.I):
            continue
        header = re.split(r'\bWITH\s+EXECUTE\b|\bAS\s*\n', source, maxsplit=1, flags=re.I)[0]
        params = re.findall(r'(@\w+)\s+(\w+)(?:\s*\(([^)]+)\))?', header)
        wanted = [(param,kind.lower(),(size or '').lower()) for param,kind,size in params]
        rows = [row for row in report['parameters'] if row['procedure_name']==name]
        actual = []
        for row in rows:
            kind = row['type_name']
            size = ''
            if kind in ('char','nchar','varchar','nvarchar','varbinary','binary'):
                size = 'max' if row['max_length']==-1 else str(row['max_length']//(2 if kind.startswith('n') else 1))
            elif kind=='datetime2':
                size=str(row['scale'])
            actual.append((row['parameter_name'],kind,size))
        compared += len(wanted)
        if wanted != actual:
            parameter_drift.append({'procedure':name,'expected':wanted,'live':actual})
    return {'expected_tables_compared':len(tables),'expected_columns_compared':sum(map(len,tables.values())),
            'column_drift':drift,'expected_parameters_compared':compared,'parameter_drift':parameter_drift,
            'module_definitions_compared':sum(item['compared'] for item in report['modules']),
            'module_drift':[item['name'] for item in report['modules'] if item.get('matches') is False]}


if __name__=='__main__':
    state = Path.home()/'.local/state/learningnemo'
    result = summarize(json.loads((state/'sql-audit.result.json').read_text()))
    path = state/'sql-audit.comparison.json'
    path.write_text(json.dumps(result,indent=2))
    path.chmod(0o600)
    print(json.dumps(result,indent=2))