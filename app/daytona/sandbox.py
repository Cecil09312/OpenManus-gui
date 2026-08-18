import time

from daytona import (
    CreateSandboxFromImageParams,
    Daytona,
    DaytonaConfig,
    Resources,
    Sandbox,
    SandboxState,
    SessionExecuteRequest,
)

from app.config import config
from app.utils.logger import logger

# load_dotenv()
daytona_settings = config.daytona
logger.info("正在初始化 Daytona 沙箱配置")
daytona_config = DaytonaConfig(
    api_key=daytona_settings.daytona_api_key,
    server_url=daytona_settings.daytona_server_url,
    target=daytona_settings.daytona_target,
)

if daytona_config.api_key:
    logger.info("Daytona API 密钥配置成功")
else:
    logger.warning("环境变量中未找到 Daytona API 密钥")

if daytona_config.server_url:
    logger.info(f"Daytona 服务器 URL 设置为：{daytona_config.server_url}")
else:
    logger.warning("环境变量中未找到 Daytona 服务器 URL")

if daytona_config.target:
    logger.info(f"Daytona 目标设置为：{daytona_config.target}")
else:
    logger.warning("环境变量中未找到 Daytona 目标")

daytona = Daytona(daytona_config)
logger.info("Daytona 客户端已初始化")


async def get_or_start_sandbox(sandbox_id: str):
    """根据 ID 获取沙箱，检查其状态，并在需要时启动它。"""

    logger.info(f"正在获取或启动 ID 为 {sandbox_id} 的沙箱")

    try:
        sandbox = daytona.get(sandbox_id)

        # 检查沙箱是否需要启动
        if (
            sandbox.state == SandboxState.ARCHIVED
            or sandbox.state == SandboxState.STOPPED
        ):
            logger.info(f"沙箱处于 {sandbox.state} 状态。正在启动...")
            try:
                daytona.start(sandbox)
                # 等待沙箱初始化
                # sleep(5)
                # 启动后刷新沙箱状态
                sandbox = daytona.get(sandbox_id)

                # 重启时在会话中启动 supervisord
                start_supervisord_session(sandbox)
            except Exception as e:
                logger.error(f"启动沙箱时出错：{e}")
                raise e

        logger.info(f"沙箱 {sandbox_id} 已就绪")
        return sandbox

    except Exception as e:
        logger.error(f"获取或启动沙箱时出错：{str(e)}")
        raise e


def start_supervisord_session(sandbox: Sandbox):
    """在会话中启动 supervisord。"""
    session_id = "supervisord-session"
    try:
        logger.info(f"正在为 supervisord 创建会话 {session_id}")
        sandbox.process.create_session(session_id)

        # 执行 supervisord 命令
        sandbox.process.execute_session_command(
            session_id,
            SessionExecuteRequest(
                command="exec /usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf",
                var_async=True,
            ),
        )
        time.sleep(25)  # Wait a bit to ensure supervisord starts properly
        logger.info(f"Supervisord 已在会话 {session_id} 中启动")
    except Exception as e:
        logger.error(f"启动 supervisord 会话时出错：{str(e)}")
        raise e


def create_sandbox(password: str, project_id: str = None):
    """创建一个配置好所有必需服务并运行的新沙箱。"""

    logger.info("正在创建新的 Daytona 沙箱环境")
    logger.info("正在使用 browser-use 镜像和环境变量配置沙箱")

    labels = None
    if project_id:
        logger.info(f"使用 sandbox_id 作为标签：{project_id}")
        labels = {"id": project_id}

    params = CreateSandboxFromImageParams(
        image=daytona_settings.sandbox_image_name,
        public=True,
        labels=labels,
        env_vars={
            "CHROME_PERSISTENT_SESSION": "true",
            "RESOLUTION": "1024x768x24",
            "RESOLUTION_WIDTH": "1024",
            "RESOLUTION_HEIGHT": "768",
            "VNC_PASSWORD": password,
            "ANONYMIZED_TELEMETRY": "false",
            "CHROME_PATH": "",
            "CHROME_USER_DATA": "",
            "CHROME_DEBUGGING_PORT": "9222",
            "CHROME_DEBUGGING_HOST": "localhost",
            "CHROME_CDP": "",
        },
        resources=Resources(
            cpu=2,
            memory=4,
            disk=5,
        ),
        auto_stop_interval=15,
        auto_archive_interval=24 * 60,
    )

    # 创建沙箱
    sandbox = daytona.create(params)
    logger.info(f"已创建沙箱，ID：{sandbox.id}")

    # 为新沙箱在会话中启动 supervisord
    start_supervisord_session(sandbox)

    logger.info(f"沙箱环境已成功初始化")
    return sandbox


async def delete_sandbox(sandbox_id: str):
    """根据 ID 删除沙箱。"""
    logger.info(f"正在删除 ID 为 {sandbox_id} 的沙箱")

    try:
        # 获取沙箱
        sandbox = daytona.get(sandbox_id)

        # 删除沙箱
        daytona.delete(sandbox)

        logger.info(f"已成功删除沙箱 {sandbox_id}")
        return True
    except Exception as e:
        logger.error(f"删除沙箱 {sandbox_id} 时出错：{str(e)}")
        raise e
