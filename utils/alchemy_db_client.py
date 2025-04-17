from typing import Any
from sqlalchemy import create_engine, inspect, text # type: ignore
from sqlalchemy.exc import SQLAlchemyError # type: ignore
from urllib.parse import quote_plus # 用于对URL进行编码
    
def get_db_schema(
        db_type: str,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        table_names: str | None = None
) -> dict[str, Any] | None:
    result: dict[str, Any] = {}
    # 构建连接URL
    driver = {
        'mysql': 'pymysql',
        'oracle': 'cx_oracle',
        'sqlserver': 'pymssql',
        'postgresql': 'psycopg2'
    }.get(db_type.lower(), '')

    encoded_username = quote_plus(username)
    encoded_password = quote_plus(password)

    engine = create_engine(f'{db_type.lower()}+{driver}://{encoded_username}:{encoded_password}@{host}:{port}/{database}')
    inspector = inspect(engine)

    # 获取字段注释的SQL语句
    column_comment_sql = {
        'mysql': f"SELECT COLUMN_COMMENT FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = '{database}' AND TABLE_NAME = :table_name AND COLUMN_NAME = :column_name",
        'oracle': "SELECT COMMENTS FROM ALL_COL_COMMENTS WHERE TABLE_NAME = :table_name AND COLUMN_NAME = :column_name",
        'sqlserver': "SELECT CAST(ep.value AS NVARCHAR(MAX)) FROM sys.columns c LEFT JOIN sys.extended_properties ep ON ep.major_id = c.object_id AND ep.minor_id = c.column_id WHERE OBJECT_NAME(c.object_id) = :table_name AND c.name = :column_name",
        'postgresql': """
            SELECT pg_catalog.col_description(c.oid, cols.ordinal_position::int)
            FROM pg_catalog.pg_class c
            JOIN information_schema.columns cols
            ON c.relname = cols.table_name
            WHERE c.relname = :table_name AND cols.column_name = :column_name
        """
    }.get(db_type.lower(), "")

    try:
        # 获取所有表名
        all_tables = inspector.get_table_names()

        # 如果指定了table_names，则过滤表名
        target_tables = all_tables

        if table_names:
            target_tables = [table.strip() for table in table_names.split(',')]
            # 过滤出实际存在的表
            target_tables = [table for table in target_tables if table in all_tables]
        print(f"Retrieving table metadata for {len(target_tables)} tables...")
        for table_name in target_tables:
            # 获取表注释
            table_comment = ""
            try:
                table_comment = inspector.get_table_comment(table_name).get("text") or ""
            except SQLAlchemyError as e:
                raise ValueError(f"Failed to retrieve table comments: {str(e)}")

            table_info = {
                'comment': table_comment,
                'columns': []
            }

            for column in inspector.get_columns(table_name):
                # 获取字段注释
                column_comment = ""
                try:
                    with engine.connect() as conn:
                        stmt = text(column_comment_sql)
                        column_comment = conn.execute(stmt, {
                            'table_name': table_name,
                            'column_name': column['name']
                        }).scalar() or ""
                except SQLAlchemyError as e:
                    print(f"Warning: failed to get comment for {table_name}.{column['name']} - {e}")
                    column_comment = ""

                table_info['columns'].append({
                    'name': column['name'],
                    'comment': column_comment,
                    'type': str(column['type'])
                })

            result[table_name] = table_info
        return result
    except SQLAlchemyError as e:
        raise ValueError(f"Failed to retrieve database table metadata: {str(e)}")
    finally:
        engine.dispose()

def format_schema_dsl(schema: dict[str, Any], with_type: bool = True, with_comment: bool = False) -> str:
    type_aliases = {
        'INTEGER': 'i', 'INT': 'i', 'BIGINT': 'i', 'SMALLINT': 'i', 'TINYINT': 'i',
        'VARCHAR': 's', 'TEXT': 's', 'CHAR': 's',
        'DATETIME': 'dt', 'TIMESTAMP': 'dt', 'DATE': 'dt',
        'DECIMAL': 'f', 'NUMERIC': 'f', 'FLOAT': 'f', 'DOUBLE': 'f',
        'BOOLEAN': 'b', 'BOOL': 'b',
        'JSON': 'j'
    }
    lines = []
    for table_name, table_data in schema.items():
        column_parts = []

        for col in table_data['columns']:
            parts = [col['name']]
            if with_type:
                raw_type = col['type'].split('(')[0].upper()
                col_type = type_aliases.get(raw_type, raw_type.lower())
                parts.append(col_type)
            if with_comment and col.get('comment'):
                parts.append(f"# {col['comment']}")
            column_parts.append(":".join(parts))

        # 构建表注释
        if with_comment and table_data.get('comment'):
            lines.append(f"# {table_data['comment']}")
        lines.append(f"T:{table_name}({', '.join(column_parts)})")

    return "\n".join(lines)

def execute_sql(
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        sql: str,
        params: dict[str, Any] | None = None
) -> list[dict[str, Any]] | dict[str, Any] | None:
    encoded_username = quote_plus(username)
    encoded_password = quote_plus(password)
    
    # 创建数据库引擎
    engine = create_engine(f'mysql+pymysql://{encoded_username}:{encoded_password}@{host}:{port}/{database}')
    
    try:
        # 使用 begin() 确保事务自动提交
        with engine.begin() as conn:
            stmt = text(sql)
            result_proxy = conn.execute(stmt, params or {})
            # 如果返回行数据，则为查询语句
            if result_proxy.returns_rows:
                rows = result_proxy.fetchall()
                keys = result_proxy.keys()
                return [dict(zip(keys, row)) for row in rows]
            else:
                # 非查询语句返回受影响的行数
                return {"rowcount": result_proxy.rowcount}
    except SQLAlchemyError as e:
        raise ValueError(f"Database error: {str(e)}")
    finally:
        engine.dispose()