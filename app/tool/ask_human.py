from app.tool import BaseTool


class AskHuman(BaseTool):
    """添加一个向人类寻求帮助的工具。"""

    name: str = "ask_human"
    description: str = "使用此工具向人类寻求帮助。"
    parameters: str = {
        "type": "object",
        "properties": {
            "inquire": {
                "type": "string",
                "description": "你想向人类提出的问题。",
            }
        },
        "required": ["inquire"],
    }

    async def execute(self, inquire: str) -> str:
        return input(f"""机器人：{inquire}\n\n你：""").strip()
