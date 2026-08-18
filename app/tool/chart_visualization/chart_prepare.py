from app.tool.chart_visualization.python_execute import NormalPythonExecute


class VisualizationPrepare(NormalPythonExecute):
    """图表生成准备工具"""

    name: str = "visualization_preparation"
    description: str = "使用 Python 代码生成 data_visualization 工具的元数据。输出：1) JSON 信息。2) 清洗后的 CSV 数据文件（可选）。"
    parameters: dict = {
        "type": "object",
        "properties": {
            "code_type": {
                "description": "代码类型，visualization：csv -> 图表；insight：选择洞察到图表中",
                "type": "string",
                "default": "visualization",
                "enum": ["visualization", "insight"],
            },
            "code": {
                "type": "string",
                "description": """data_visualization 准备的 Python 代码。
## 可视化类型
1. 数据加载逻辑
2. 生成 CSV 数据和图表描述
2.1 CSV 数据（你想可视化的数据，从原始数据中清洗/转换，保存为 .csv）
2.2 CSV 数据的图表描述（图表标题或描述应简洁明了。例如：'产品销售分布'、'月度收入趋势'。）
3. 将信息保存为 JSON 文件。（格式：{"csvFilePath": string, "chartTitle": string}[]）
## 洞察类型
1. 从 data_visualization 结果中选择你想添加到图表中的洞察。
2. 将信息保存为 JSON 文件。（格式：{"chartPath": string, "insights_id": number[]}[]）
# 注意
1. 你可以根据不同的可视化需求生成一个或多个 CSV 数据。
2. 使每个图表数据简单、清晰且不同。
3. JSON 文件以 utf-8 编码保存并打印路径：print(json_path)
""",
            },
        },
        "required": ["code", "code_type"],
    }
