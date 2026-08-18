import argparse
import asyncio

from app.agent.manus import Manus
from app.logger import logger


async def main():
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="运行 Manus 智能体并接受提示词")
    parser.add_argument(
        "--prompt", type=str, required=False, help="智能体的输入提示词"
    )
    args = parser.parse_args()

    # 创建并初始化 Manus 智能体
    agent = await Manus.create()
    try:
        # 如果提供了命令行提示词则使用，否则请求输入
        prompt = args.prompt if args.prompt else input("请输入你的提示词：")
        if not prompt.strip():
            logger.warning("提供了空的提示词。")
            return

        logger.warning("正在处理你的请求...")
        await agent.run(prompt)
        logger.info("请求处理完成。")
    except KeyboardInterrupt:
        logger.warning("操作已中断。")
    finally:
        # 确保在退出前清理智能体资源
        await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
