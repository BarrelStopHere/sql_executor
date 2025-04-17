from collections.abc import Generator
from typing import Any
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from utils.alchemy_db_client import execute_sql
import json
from datetime import datetime, date
from decimal import Decimal
import csv
from io import StringIO

class SqlExecutor(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        # 获取传入的 SQL 语句
        sql = tool_parameters.get("sql")
        if not sql:
            raise ValueError("SQL 语句不能为空")

        # 改进后的风险检测
        if self._contains_risk_commands(sql):
            raise ValueError("SQL 语句存在风险")

        # 获取数据库连接参数
        host = tool_parameters.get("host")
        port = tool_parameters.get("port")
        database = tool_parameters.get("db_name")
        username = tool_parameters.get("username")
        password = tool_parameters.get("password")

        if not all([host, port, database, username, password]):
            raise ValueError("数据库连接参数不能为空")

        try:
            # 执行 SQL 语句（查询或非查询）
            result = execute_sql(
                host, int(port), database,
                username, password, sql, ""
            )
            # 处理空结果
            if isinstance(result, list) and not result:  # 空查询结果
                yield self.create_text_message("No data found")
            elif isinstance(result, dict) and "rowcount" in result and result["rowcount"] == 0:  # 无影响行数
                yield self.create_text_message("No data affected")

            result_format = tool_parameters.get("result_format", "json")

            if result_format == 'json':
                yield self.create_json_message({
                    "status": "success",
                    "result": result
                }
                )
            elif result_format == 'csv':
                yield from self._handle_csv(result)
            elif result_format == 'html':
                yield from self._handle_html(result)
            else:
                if result is not None:
                    message_text = json.dumps(
                        result,
                        ensure_ascii=False,
                        default=self._custom_serializer  # 关键修改点
                    )
                else:
                    message_text = "No data found"
                yield self.create_text_message(message_text)
        except Exception as e:
            raise ValueError(f"数据库操作失败：{str(e)}")

    def _handle_html(self, data: list[dict[str, Any]] | dict[str, Any] | None) -> Generator[ToolInvokeMessage, None, None]:
        """生成 HTML 表格消息"""
        html_table = self._to_html_table(data)
        yield self.create_blob_message(html_table.encode('utf-8'), meta={'mime_type': 'text/html', 'filename': 'result.html'})

    def _handle_csv(self, data: list[dict[str, Any]] | dict[str, Any] | None) -> Generator[ToolInvokeMessage]:
        """生成 CSV 文件消息"""
        output = StringIO()
        # 写入 BOM（仅前3个字节）
        output.write('\ufeff')  # 添加 BOM
        writer = csv.writer(output)
        # 写入表头
        writer.writerow(data[0].keys())

        # 写入数据行（处理日期序列化）
        for row in data:
            processed_row = [
                self._custom_serializer(val) if isinstance(val, (date, datetime)) else val
                for val in row.values()
            ]
            writer.writerow(processed_row)

        # 注意：使用 utf-8-sig 编码会自动包含 BOM，推荐使用这种方式
        yield self.create_blob_message(
            output.getvalue().encode('utf-8-sig'),  # 关键修改点 ✅
            meta={
                'mime_type': 'text/csv',
                'filename': 'result.csv',
                'encoding': 'utf-8-sig'  # 显式声明编码
            }
        )

    def _to_html_table(self, data: list[dict]) -> str:
        """生成标准 HTML 表格"""
        html = ["<table border='1'>"]
        html.append("<tr>" + "".join(f"<th>{col}</th>" for col in data[0].keys()) + "</tr>")

        for row in data:
            html.append(
                "<tr>" +
                "".join(f"<td>{self._custom_serializer(val)}</td>" for val in row.values()) +
                "</tr>"
            )

        html.append("</table>")
        return "".join(html)

    def _contains_risk_commands(self, sql: str) -> bool:
        import re
        risk_keywords = {"DROP", "DELETE", "TRUNCATE", "ALTER", "UPDATE", "INSERT"}
        # 移除注释
        sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
        sql = re.sub(r'--.*', '', sql)
        # 分割语句
        statements = re.split(r';\s*', sql)
        for stmt in statements:
            stmt = stmt.strip()
            if not stmt:
                continue
            # 匹配第一个单词（不区分大小写）
            match = re.match(r'\s*([^\s]+)', stmt, re.IGNORECASE)
            if match:
                first_word = match.group(1).upper()
                if first_word in risk_keywords:
                    return True
        return False

    def _custom_serializer(self, obj: Any) -> Any:
        """处理数据库常见不可序列化类型"""
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()  # 转换为 ISO8601 字符串
        elif isinstance(obj, Decimal):
            return float(obj)  # Decimal转浮点数
        # 添加其他需要处理的类型（如 bytes）
        raise TypeError(f"Unserializable type {type(obj)}")
