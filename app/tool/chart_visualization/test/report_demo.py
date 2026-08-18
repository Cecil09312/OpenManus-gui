import asyncio

from app.agent.data_analysis import DataAnalysis


# from app.agent.manus import Manus


async def main():
    agent = DataAnalysis()
    # agent = Manus()
    await agent.run(
        """需求：
1. 分析以下数据并生成图形化的数据报告（HTML 格式）。最终产品应该是一个数据报告。
数据：
月份 | A 团队 | B 团队 | C 团队
一月 | 1200 小时 | 1350 小时 | 1100 小时
二月 | 1250 小时 | 1400 小时 | 1150 小时
三月 | 1180 小时 | 1300 小时 | 1300 小时
四月 | 1220 小时 | 1280 小时 | 1400 小时
五月 | 1230 小时 | 1320 小时 | 1450 小时
六月 | 1200 小时 | 1250 小时 | 1500 小时  """
    )


if __name__ == "__main__":
    asyncio.run(main())
