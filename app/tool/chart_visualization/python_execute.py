from app.config import config
from app.tool.python_execute import PythonExecute


class NormalPythonExecute(PythonExecute):
    """执行 Python 代码的工具，具有超时和安全限制。"""

    name: str = "python_execute"
    description: str = """执行 Python 代码进行深度数据分析 / 数据报告（任务结论）/ 其他无直接可视化的普通任务。"""
    parameters: dict = {
        "type": "object",
        "properties": {
            "code_type": {
                "description": "代码类型，数据处理 / 数据报告 / 其他",
                "type": "string",
                "default": "process",
                "enum": ["process", "report", "others"],
            },
            "code": {
                "type": "string",
                "description": """要执行的 Python 代码。
# 注意
1. 代码应生成一份综合性的文本报告，包含数据集概览、列详情、基本统计信息、派生指标、时间序列比较、异常值和关键洞察。
2. 所有输出使用 print() 以便分析结果（包括'数据集概览'或'预处理结果'等部分）清晰可见并保存
3. 将所有报告 / 处理后的文件 / 每个分析结果保存在工作区目录：{directory}
4. 数据报告需要内容丰富，包含你的整体分析过程和相应的数据可视化。
5. 你可以逐步调用此工具进行数据分析，从摘要到深度分析，并保存数据报告""".format(
                    directory=config.workspace_root
                ),
            },
        },
        "required": ["code"],
    }

    async def execute(self, code: str, code_type: str | None = None, timeout=5):
        return await super().execute(code, timeout)
