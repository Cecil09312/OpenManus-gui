# tool/planning.py
from typing import Dict, List, Literal, Optional

from app.exceptions import ToolError
from app.tool.base import BaseTool, ToolResult


_PLANNING_TOOL_DESCRIPTION = """
一个规划工具，允许智能体创建和管理计划以解决复杂任务。
该工具提供创建计划、更新计划步骤和跟踪进度的功能。
"""


class PlanningTool(BaseTool):
    """
    一个规划工具，允许智能体创建和管理计划以解决复杂任务。
    该工具提供创建计划、更新计划步骤和跟踪进度的功能。
    """

    name: str = "planning"
    description: str = _PLANNING_TOOL_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "command": {
                "description": "要执行的命令。可用命令：create、update、list、get、set_active、mark_step、delete。",
                "enum": [
                    "create",
                    "update",
                    "list",
                    "get",
                    "set_active",
                    "mark_step",
                    "delete",
                ],
                "type": "string",
            },
            "plan_id": {
                "description": "计划的唯一标识符。create、update、set_active 和 delete 命令必需。get 和 mark_step 可选（未指定时使用活动计划）。",
                "type": "string",
            },
            "title": {
                "description": "计划的标题。create 命令必需，update 命令可选。",
                "type": "string",
            },
            "steps": {
                "description": "计划步骤列表。create 命令必需，update 命令可选。",
                "type": "array",
                "items": {"type": "string"},
            },
            "step_index": {
                "description": "要更新的步骤索引（从0开始）。mark_step 命令必需。",
                "type": "integer",
            },
            "step_status": {
                "description": "要为步骤设置的状态。与 mark_step 命令一起使用。",
                "enum": ["not_started", "in_progress", "completed", "blocked"],
                "type": "string",
            },
            "step_notes": {
                "description": "步骤的附加备注。mark_step 命令可选。",
                "type": "string",
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    plans: dict = {}  # 按 plan_id 存储计划的字典
    _current_plan_id: Optional[str] = None  # 跟踪当前活动计划

    async def execute(
        self,
        *,
        command: Literal[
            "create", "update", "list", "get", "set_active", "mark_step", "delete"
        ],
        plan_id: Optional[str] = None,
        title: Optional[str] = None,
        steps: Optional[List[str]] = None,
        step_index: Optional[int] = None,
        step_status: Optional[
            Literal["not_started", "in_progress", "completed", "blocked"]
        ] = None,
        step_notes: Optional[str] = None,
        **kwargs,
    ):
        """
        使用给定的命令和参数执行规划工具。

        参数：
        - command: 要执行的操作
        - plan_id: 计划的唯一标识符
        - title: 计划的标题（用于 create 命令）
        - steps: 计划的步骤列表（用于 create 命令）
        - step_index: 要更新的步骤索引（用于 mark_step 命令）
        - step_status: 要为步骤设置的状态（用于 mark_step 命令）
        - step_notes: 步骤的附加备注（用于 mark_step 命令）
        """

        if command == "create":
            return self._create_plan(plan_id, title, steps)
        elif command == "update":
            return self._update_plan(plan_id, title, steps)
        elif command == "list":
            return self._list_plans()
        elif command == "get":
            return self._get_plan(plan_id)
        elif command == "set_active":
            return self._set_active_plan(plan_id)
        elif command == "mark_step":
            return self._mark_step(plan_id, step_index, step_status, step_notes)
        elif command == "delete":
            return self._delete_plan(plan_id)
        else:
            raise ToolError(
                f"未识别的命令：{command}。允许的命令为：create, update, list, get, set_active, mark_step, delete"
            )

    def _create_plan(
        self, plan_id: Optional[str], title: Optional[str], steps: Optional[List[str]]
    ) -> ToolResult:
        """使用给定的 ID、标题和步骤创建新计划。"""
        if not plan_id:
            raise ToolError("参数 `plan_id` 是 create 命令的必需参数")

        if plan_id in self.plans:
            raise ToolError(
                f"ID 为 '{plan_id}' 的计划已存在。请使用 'update' 来修改现有计划。"
            )

        if not title:
            raise ToolError("参数 `title` 是 create 命令的必需参数")

        if (
            not steps
            or not isinstance(steps, list)
            or not all(isinstance(step, str) for step in steps)
        ):
            raise ToolError(
                "参数 `steps` 必须是非空字符串列表，用于 create 命令"
            )

        # 创建新计划并初始化步骤状态
        plan = {
            "plan_id": plan_id,
            "title": title,
            "steps": steps,
            "step_statuses": ["not_started"] * len(steps),
            "step_notes": [""] * len(steps),
        }

        self.plans[plan_id] = plan
        self._current_plan_id = plan_id  # 设为活动计划

        return ToolResult(
            output=f"计划创建成功，ID：{plan_id}\n\n{self._format_plan(plan)}"
        )

    def _update_plan(
        self, plan_id: Optional[str], title: Optional[str], steps: Optional[List[str]]
    ) -> ToolResult:
        """使用新标题或步骤更新现有计划。"""
        if not plan_id:
            raise ToolError("参数 `plan_id` 是 update 命令的必需参数")

        if plan_id not in self.plans:
            raise ToolError(f"未找到 ID 为 {plan_id} 的计划")

        plan = self.plans[plan_id]

        if title:
            plan["title"] = title

        if steps:
            if not isinstance(steps, list) or not all(
                isinstance(step, str) for step in steps
            ):
                raise ToolError(
                    "参数 `steps` 必须是字符串列表，用于 update 命令"
                )

            # 保留未更改步骤的现有状态
            old_steps = plan["steps"]
            old_statuses = plan["step_statuses"]
            old_notes = plan["step_notes"]

            # 创建新的步骤状态和备注
            new_statuses = []
            new_notes = []

            for i, step in enumerate(steps):
                # 如果步骤在旧步骤的相同位置存在，则保留状态和备注
                if i < len(old_steps) and step == old_steps[i]:
                    new_statuses.append(old_statuses[i])
                    new_notes.append(old_notes[i])
                else:
                    new_statuses.append("not_started")
                    new_notes.append("")

            plan["steps"] = steps
            plan["step_statuses"] = new_statuses
            plan["step_notes"] = new_notes

        return ToolResult(
            output=f"计划更新成功：{plan_id}\n\n{self._format_plan(plan)}"
        )

    def _list_plans(self) -> ToolResult:
        """列出所有可用的计划。"""
        if not self.plans:
            return ToolResult(
                output="没有可用的计划。请使用 'create' 命令创建一个计划。"
            )

        output = "可用计划：\n"
        for plan_id, plan in self.plans.items():
            current_marker = "（活动）" if plan_id == self._current_plan_id else ""
            completed = sum(
                1 for status in plan["step_statuses"] if status == "completed"
            )
            total = len(plan["steps"])
            progress = f"{completed}/{total} 个步骤已完成"
            output += f"• {plan_id}{current_marker}: {plan['title']} - {progress}\n"

        return ToolResult(output=output)

    def _get_plan(self, plan_id: Optional[str]) -> ToolResult:
        """获取特定计划的详细信息。"""
        if not plan_id:
            # 如果未提供 plan_id，则使用当前活动计划
            if not self._current_plan_id:
                raise ToolError(
                    "没有活动计划。请指定 plan_id 或设置一个活动计划。"
                )
            plan_id = self._current_plan_id

        if plan_id not in self.plans:
            raise ToolError(f"未找到 ID 为 {plan_id} 的计划")

        plan = self.plans[plan_id]
        return ToolResult(output=self._format_plan(plan))

    def _set_active_plan(self, plan_id: Optional[str]) -> ToolResult:
        """将计划设置为活动计划。"""
        if not plan_id:
            raise ToolError("参数 `plan_id` 是 set_active 命令的必需参数")

        if plan_id not in self.plans:
            raise ToolError(f"未找到 ID 为 {plan_id} 的计划")

        self._current_plan_id = plan_id
        return ToolResult(
            output=f"计划 '{plan_id}' 现在是活动计划。\n\n{self._format_plan(self.plans[plan_id])}"
        )

    def _mark_step(
        self,
        plan_id: Optional[str],
        step_index: Optional[int],
        step_status: Optional[str],
        step_notes: Optional[str],
    ) -> ToolResult:
        """为步骤标记特定状态和可选备注。"""
        if not plan_id:
            # 如果未提供 plan_id，则使用当前活动计划
            if not self._current_plan_id:
                raise ToolError(
                    "没有活动计划。请指定 plan_id 或设置一个活动计划。"
                )
            plan_id = self._current_plan_id

        if plan_id not in self.plans:
            raise ToolError(f"未找到 ID 为 {plan_id} 的计划")

        if step_index is None:
            raise ToolError("参数 `step_index` 是 mark_step 命令的必需参数")

        plan = self.plans[plan_id]

        if step_index < 0 or step_index >= len(plan["steps"]):
            raise ToolError(
                f"无效的 step_index：{step_index}。有效索引范围从 0 到 {len(plan['steps'])-1}。"
            )

        if step_status and step_status not in [
            "not_started",
            "in_progress",
            "completed",
            "blocked",
        ]:
            raise ToolError(
                f"无效的 step_status：{step_status}。有效状态为：not_started, in_progress, completed, blocked"
            )

        if step_status:
            plan["step_statuses"][step_index] = step_status

        if step_notes:
            plan["step_notes"][step_index] = step_notes

        return ToolResult(
            output=f"步骤 {step_index} 已在计划 '{plan_id}' 中更新。\n\n{self._format_plan(plan)}"
        )

    def _delete_plan(self, plan_id: Optional[str]) -> ToolResult:
        """删除计划。"""
        if not plan_id:
            raise ToolError("参数 `plan_id` 是 delete 命令的必需参数")

        if plan_id not in self.plans:
            raise ToolError(f"未找到 ID 为 {plan_id} 的计划")

        del self.plans[plan_id]

        # 如果删除的计划是活动计划，则清除活动计划
        if self._current_plan_id == plan_id:
            self._current_plan_id = None

        return ToolResult(output=f"计划 '{plan_id}' 已被删除。")

    def _format_plan(self, plan: Dict) -> str:
        """格式化计划以供显示。"""
        output = f"计划：{plan['title']} (ID: {plan['plan_id']})\n"
        output += "=" * len(output) + "\n\n"

        # 计算进度统计
        total_steps = len(plan["steps"])
        completed = sum(1 for status in plan["step_statuses"] if status == "completed")
        in_progress = sum(
            1 for status in plan["step_statuses"] if status == "in_progress"
        )
        blocked = sum(1 for status in plan["step_statuses"] if status == "blocked")
        not_started = sum(
            1 for status in plan["step_statuses"] if status == "not_started"
        )

        output += f"进度：{completed}/{total_steps} 个步骤已完成 "
        if total_steps > 0:
            percentage = (completed / total_steps) * 100
            output += f"({percentage:.1f}%)\n"
        else:
            output += "(0%)\n"

        output += f"状态：{completed} 已完成，{in_progress} 进行中，{blocked} 已阻塞，{not_started} 未开始\n\n"
        output += "步骤：\n"

        # 添加每个步骤及其状态和备注
        for i, (step, status, notes) in enumerate(
            zip(plan["steps"], plan["step_statuses"], plan["step_notes"])
        ):
            status_symbol = {
                "not_started": "[ ]",
                "in_progress": "[→]",
                "completed": "[✓]",
                "blocked": "[!]",
            }.get(status, "[ ]")

            output += f"{i}. {status_symbol} {step}\n"
            if notes:
                output += f"   备注：{notes}\n"

        return output
