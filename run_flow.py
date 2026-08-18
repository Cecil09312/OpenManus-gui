import asyncio
import time

from app.agent.data_analysis import DataAnalysis
from app.agent.manus import Manus
from app.config import config
from app.flow.flow_factory import FlowFactory, FlowType
from app.logger import logger


async def run_flow():
    agents = {
        "manus": Manus(),
    }
    if config.run_flow_config.use_data_analysis_agent:
        agents["data_analysis"] = DataAnalysis()
    try:
        prompt = input("请输入你的提示词：")

        if prompt.strip().isspace() or not prompt:
            logger.warning("提供了空的提示词。")
            return

        flow = FlowFactory.create_flow(
            flow_type=FlowType.PLANNING,
            agents=agents,
        )
        logger.warning("正在处理你的请求...")

        try:
            start_time = time.time()
            result = await asyncio.wait_for(
                flow.execute(prompt),
                timeout=3600,  # 60 分钟超时限制
            )
            elapsed_time = time.time() - start_time
            logger.info(f"请求在 {elapsed_time:.2f} 秒内处理完成")
            logger.info(result)
        except asyncio.TimeoutError:
            logger.error("请求处理在1小时后超时")
            logger.info(
                "操作因超时而终止。请尝试更简单的请求。"
            )

    except KeyboardInterrupt:
        logger.info("操作已被用户取消。")
    except Exception as e:
        logger.error(f"错误：{str(e)}")


if __name__ == "__main__":
    asyncio.run(run_flow())
