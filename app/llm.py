import math
from typing import Dict, List, Optional, Union

import tiktoken
from openai import (
    APIError,
    AsyncAzureOpenAI,
    AsyncOpenAI,
    AuthenticationError,
    OpenAIError,
    RateLimitError,
)
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.bedrock import BedrockClient
from app.config import LLMSettings, config
from app.exceptions import TokenLimitExceeded
from app.logger import logger  # 假设应用中已设置好日志记录器
from app.schema import (
    ROLE_VALUES,
    TOOL_CHOICE_TYPE,
    TOOL_CHOICE_VALUES,
    Message,
    ToolChoice,
)

REASONING_MODELS = ["o1", "o3-mini"]
MULTIMODAL_MODELS = [
    "gpt-4-vision-preview",
    "gpt-4o",
    "gpt-4o-mini",
    "claude-3-opus-20240229",
    "claude-3-sonnet-20240229",
    "claude-3-haiku-20240307",
]


class TokenCounter:
    # Token 常量
    BASE_MESSAGE_TOKENS = 4
    FORMAT_TOKENS = 2
    LOW_DETAIL_IMAGE_TOKENS = 85
    HIGH_DETAIL_TILE_TOKENS = 170

    # 图片处理常量
    MAX_SIZE = 2048
    HIGH_DETAIL_TARGET_SHORT_SIDE = 768
    TILE_SIZE = 512

    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    def count_text(self, text: str) -> int:
        """计算文本字符串的 token 数"""
        return 0 if not text else len(self.tokenizer.encode(text))

    def count_image(self, image_item: dict) -> int:
        """
        根据详细级别和尺寸计算图片的 token 数

        "low" 详细级别：固定 85 个 token
        "high" 详细级别：
        1. 缩放以适应 2048x2048 方框
        2. 将最短边缩放至 768px
        3. 计算 512px 瓦片数（每个 170 个 token）
        4. 加上 85 个 token
        """
        detail = image_item.get("detail", "medium")

        # 对于低详细级别，始终返回固定 token 数
        if detail == "low":
            return self.LOW_DETAIL_IMAGE_TOKENS

        # 对于中等详细级别（OpenAI 默认），使用高详细级别计算
        # OpenAI 未指定中等详细级别的单独计算方式

        # 对于高详细级别，如果提供了尺寸则根据尺寸计算
        if detail == "high" or detail == "medium":
            # 如果 image_item 中提供了尺寸
            if "dimensions" in image_item:
                width, height = image_item["dimensions"]
                return self._calculate_high_detail_tokens(width, height)

        return (
            self._calculate_high_detail_tokens(1024, 1024) if detail == "high" else 1024
        )

    def _calculate_high_detail_tokens(self, width: int, height: int) -> int:
        """根据尺寸计算高详细级别图片的 token 数"""
        # 步骤1：缩放以适应 MAX_SIZE x MAX_SIZE 方框
        if width > self.MAX_SIZE or height > self.MAX_SIZE:
            scale = self.MAX_SIZE / max(width, height)
            width = int(width * scale)
            height = int(height * scale)

        # 步骤2：缩放使最短边为 HIGH_DETAIL_TARGET_SHORT_SIDE
        scale = self.HIGH_DETAIL_TARGET_SHORT_SIDE / min(width, height)
        scaled_width = int(width * scale)
        scaled_height = int(height * scale)

        # 步骤3：计算 512px 瓦片数量
        tiles_x = math.ceil(scaled_width / self.TILE_SIZE)
        tiles_y = math.ceil(scaled_height / self.TILE_SIZE)
        total_tiles = tiles_x * tiles_y

        # 步骤4：计算最终 token 数
        return (
            total_tiles * self.HIGH_DETAIL_TILE_TOKENS
        ) + self.LOW_DETAIL_IMAGE_TOKENS

    def count_content(self, content: Union[str, List[Union[str, dict]]]) -> int:
        """计算消息内容的 token 数"""
        if not content:
            return 0

        if isinstance(content, str):
            return self.count_text(content)

        token_count = 0
        for item in content:
            if isinstance(item, str):
                token_count += self.count_text(item)
            elif isinstance(item, dict):
                if "text" in item:
                    token_count += self.count_text(item["text"])
                elif "image_url" in item:
                    token_count += self.count_image(item)
        return token_count

    def count_tool_calls(self, tool_calls: List[dict]) -> int:
        """计算工具调用的 token 数"""
        token_count = 0
        for tool_call in tool_calls:
            if "function" in tool_call:
                function = tool_call["function"]
                token_count += self.count_text(function.get("name", ""))
                token_count += self.count_text(function.get("arguments", ""))
        return token_count

    def count_message_tokens(self, messages: List[dict]) -> int:
        """计算消息列表中的总 token 数"""
        total_tokens = self.FORMAT_TOKENS  # 基础格式 token

        for message in messages:
            tokens = self.BASE_MESSAGE_TOKENS  # 每条消息的基础 token

            # 添加角色 token
            tokens += self.count_text(message.get("role", ""))

            # 添加内容 token
            if "content" in message:
                tokens += self.count_content(message["content"])

            # 添加工具调用 token
            if "tool_calls" in message:
                tokens += self.count_tool_calls(message["tool_calls"])

            # 添加 name 和 tool_call_id token
            tokens += self.count_text(message.get("name", ""))
            tokens += self.count_text(message.get("tool_call_id", ""))

            total_tokens += tokens

        return total_tokens


class LLM:
    _instances: Dict[str, "LLM"] = {}

    def __new__(
        cls, config_name: str = "default", llm_config: Optional[LLMSettings] = None
    ):
        if config_name not in cls._instances:
            instance = super().__new__(cls)
            instance.__init__(config_name, llm_config)
            cls._instances[config_name] = instance
        return cls._instances[config_name]

    def __init__(
        self, config_name: str = "default", llm_config: Optional[LLMSettings] = None
    ):
        if not hasattr(self, "client"):  # Only initialize if not already initialized
            llm_config = llm_config or config.llm
            llm_config = llm_config.get(config_name, llm_config["default"])
            self.model = llm_config.model
            self.max_tokens = llm_config.max_tokens
            self.temperature = llm_config.temperature
            self.api_type = llm_config.api_type
            self.api_key = llm_config.api_key
            self.api_version = llm_config.api_version
            self.base_url = llm_config.base_url

            # 添加 token 计数相关属性
            self.total_input_tokens = 0
            self.total_completion_tokens = 0
            self.max_input_tokens = (
                llm_config.max_input_tokens
                if hasattr(llm_config, "max_input_tokens")
                else None
            )

            # 初始化分词器
            try:
                self.tokenizer = tiktoken.encoding_for_model(self.model)
            except KeyError:
                # 如果模型不在 tiktoken 的预设中，使用 cl100k_base 作为默认值
                self.tokenizer = tiktoken.get_encoding("cl100k_base")

            if self.api_type == "azure":
                self.client = AsyncAzureOpenAI(
                    base_url=self.base_url,
                    api_key=self.api_key,
                    api_version=self.api_version,
                )
            elif self.api_type == "aws":
                self.client = BedrockClient()
            else:
                self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

            self.token_counter = TokenCounter(self.tokenizer)

    def count_tokens(self, text: str) -> int:
        """计算文本中的 token 数"""
        if not text:
            return 0
        return len(self.tokenizer.encode(text))

    def count_message_tokens(self, messages: List[dict]) -> int:
        return self.token_counter.count_message_tokens(messages)

    def update_token_count(self, input_tokens: int, completion_tokens: int = 0) -> None:
        """更新 token 计数"""
        # 仅在设置了 max_input_tokens 时跟踪 token
        self.total_input_tokens += input_tokens
        self.total_completion_tokens += completion_tokens
        logger.info(
            f"Token 使用：输入={input_tokens}，完成={completion_tokens}，"
            f"累计输入={self.total_input_tokens}，累计完成={self.total_completion_tokens}，"
            f"总计={input_tokens + completion_tokens}，累计总计={self.total_input_tokens + self.total_completion_tokens}"
        )

    def check_token_limit(self, input_tokens: int) -> bool:
        """检查是否超过 token 限制"""
        if self.max_input_tokens is not None:
            return (self.total_input_tokens + input_tokens) <= self.max_input_tokens
        # 如果未设置 max_input_tokens，始终返回 True
        return True

    def get_limit_error_message(self, input_tokens: int) -> str:
        """生成 token 限制超出的错误消息"""
        if (
            self.max_input_tokens is not None
            and (self.total_input_tokens + input_tokens) > self.max_input_tokens
        ):
            return f"请求可能超过输入 token 限制（当前：{self.total_input_tokens}，需要：{input_tokens}，最大：{self.max_input_tokens}）"

        return "Token 限制已超出"

    @staticmethod
    def format_messages(
        messages: List[Union[dict, Message]], supports_images: bool = False
    ) -> List[dict]:
        """
        将消息格式化为 LLM 可用的 OpenAI 消息格式。

        参数：
            messages: 消息列表，可以是字典或 Message 对象
            supports_images: 标志，指示目标模型是否支持图片输入

        返回：
            List[dict]: OpenAI 格式的消息列表

        异常：
            ValueError: 如果消息无效或缺少必需字段
            TypeError: 如果提供了不支持的消息类型

        示例：
            >>> msgs = [
            ...     Message.system_message("You are a helpful assistant"),
            ...     {"role": "user", "content": "Hello"},
            ...     Message.user_message("How are you?")
            ... ]
            >>> formatted = LLM.format_messages(msgs)
        """
        formatted_messages = []

        for message in messages:
            # 将 Message 对象转换为字典
            if isinstance(message, Message):
                message = message.to_dict()

            if isinstance(message, dict):
                # 如果消息是字典，确保它包含必需字段
                if "role" not in message:
                    raise ValueError("消息字典必须包含 'role' 字段")

                # 如果存在 base64 图片且模型支持图片，则处理
                if supports_images and message.get("base64_image"):
                    # 初始化或将内容转换为适当的格式
                    if not message.get("content"):
                        message["content"] = []
                    elif isinstance(message["content"], str):
                        message["content"] = [
                            {"type": "text", "text": message["content"]}
                        ]
                    elif isinstance(message["content"], list):
                        # 将字符串项转换为正确的文本对象
                        message["content"] = [
                            (
                                {"type": "text", "text": item}
                                if isinstance(item, str)
                                else item
                            )
                            for item in message["content"]
                        ]

                    # 将图片添加到内容中
                    message["content"].append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{message['base64_image']}"
                            },
                        }
                    )

                    # 移除 base64_image 字段
                    del message["base64_image"]
                # 如果模型不支持图片但消息包含 base64_image，则优雅处理
                elif not supports_images and message.get("base64_image"):
                    # 仅移除 base64_image 字段并保留文本内容
                    del message["base64_image"]

                if "content" in message or "tool_calls" in message:
                    formatted_messages.append(message)
                # 否则：不包含该消息
            else:
                raise TypeError(f"不支持的消息类型：{type(message)}")

        # 验证所有消息包含必需字段
        for msg in formatted_messages:
            if msg["role"] not in ROLE_VALUES:
                raise ValueError(f"无效的角色：{msg['role']}")

        return formatted_messages

    @retry(
        wait=wait_random_exponential(min=1, max=60),
        stop=stop_after_attempt(6),
        retry=retry_if_exception_type(
            (OpenAIError, Exception, ValueError)
        ),  # 不重试 TokenLimitExceeded
    )
    async def ask(
        self,
        messages: List[Union[dict, Message]],
        system_msgs: Optional[List[Union[dict, Message]]] = None,
        stream: bool = True,
        temperature: Optional[float] = None,
    ) -> str:
        """
        向 LLM 发送提示并获取响应。

        参数：
            messages: 对话消息列表
            system_msgs: 可选的前置系统消息
            stream (bool): 是否流式传输响应
            temperature (float): 响应的采样温度

        返回：
            str: 生成的响应

        异常：
            TokenLimitExceeded: 如果超过 token 限制
            ValueError: 如果消息无效或响应为空
            OpenAIError: 如果 API 调用在重试后失败
            Exception: 其他意外错误
        """
        try:
            # 检查模型是否支持图片
            supports_images = self.model in MULTIMODAL_MODELS

            # 格式化系统消息和用户消息，并检查图片支持
            if system_msgs:
                system_msgs = self.format_messages(system_msgs, supports_images)
                messages = system_msgs + self.format_messages(messages, supports_images)
            else:
                messages = self.format_messages(messages, supports_images)

            # 计算输入 token 数
            input_tokens = self.count_message_tokens(messages)

            # 检查是否超过 token 限制
            if not self.check_token_limit(input_tokens):
                error_message = self.get_limit_error_message(input_tokens)
                # 抛出不会重试的特殊异常
                raise TokenLimitExceeded(error_message)

            params = {
                "model": self.model,
                "messages": messages,
            }

            if self.model in REASONING_MODELS:
                params["max_completion_tokens"] = self.max_tokens
            else:
                params["max_tokens"] = self.max_tokens
                params["temperature"] = (
                    temperature if temperature is not None else self.temperature
                )

            if not stream:
                # 非流式请求
                response = await self.client.chat.completions.create(
                    **params, stream=False
                )

                if not response.choices or not response.choices[0].message.content:
                    raise ValueError("LLM 返回空或无效的响应")

                # 更新 token 计数
                self.update_token_count(
                    response.usage.prompt_tokens, response.usage.completion_tokens
                )

                return response.choices[0].message.content

            # 流式请求，对于流式传输，在发送请求前更新估计的 token 计数
            self.update_token_count(input_tokens)

            response = await self.client.chat.completions.create(**params, stream=True)

            collected_messages = []
            completion_text = ""
            async for chunk in response:
                chunk_message = chunk.choices[0].delta.content or ""
                collected_messages.append(chunk_message)
                completion_text += chunk_message
                print(chunk_message, end="", flush=True)

            print()  # 流式输出后换行
            full_response = "".join(collected_messages).strip()
            if not full_response:
                raise ValueError("流式 LLM 返回空响应")

            # 估计流式响应的完成 token 数
            completion_tokens = self.count_tokens(completion_text)
            logger.info(f"流式响应的估计完成 token 数：{completion_tokens}")
            self.total_completion_tokens += completion_tokens

            return full_response

        except TokenLimitExceeded:
            # 重新抛出 token 限制错误，不记录日志
            raise
        except ValueError:
            logger.exception(f"验证错误")
            raise
        except OpenAIError as oe:
            logger.exception(f"OpenAI API 错误")
            if isinstance(oe, AuthenticationError):
                logger.error("认证失败。请检查 API 密钥。")
            elif isinstance(oe, RateLimitError):
                logger.error("已超过速率限制。建议增加重试次数。")
            elif isinstance(oe, APIError):
                logger.error(f"API 错误：{oe}")
            raise
        except Exception:
            logger.exception(f"ask 中的意外错误")
            raise

    @retry(
        wait=wait_random_exponential(min=1, max=60),
        stop=stop_after_attempt(6),
        retry=retry_if_exception_type(
            (OpenAIError, Exception, ValueError)
        ),  # 不重试 TokenLimitExceeded
    )
    async def ask_with_images(
        self,
        messages: List[Union[dict, Message]],
        images: List[Union[str, dict]],
        system_msgs: Optional[List[Union[dict, Message]]] = None,
        stream: bool = False,
        temperature: Optional[float] = None,
    ) -> str:
        """
        向 LLM 发送带图片的提示并获取响应。

        参数：
            messages: 对话消息列表
            images: 图片 URL 或图片数据字典列表
            system_msgs: 可选的前置系统消息
            stream (bool): 是否流式传输响应
            temperature (float): 响应的采样温度

        返回：
            str: 生成的响应

        异常：
            TokenLimitExceeded: 如果超过 token 限制
            ValueError: 如果消息无效或响应为空
            OpenAIError: 如果 API 调用在重试后失败
            Exception: 其他意外错误
        """
        try:
            # 对于 ask_with_images，我们始终将 supports_images 设为 True，因为
            # 此方法仅应在支持图片的模型上调用
            if self.model not in MULTIMODAL_MODELS:
                raise ValueError(
                    f"模型 {self.model} 不支持图片。请使用 {MULTIMODAL_MODELS} 中的模型"
                )

            # 格式化消息并支持图片
            formatted_messages = self.format_messages(messages, supports_images=True)

            # 确保最后一条消息来自用户以附加图片
            if not formatted_messages or formatted_messages[-1]["role"] != "user":
                raise ValueError("最后一条消息必须来自用户才能附加图片")

            # 处理最后一条用户消息以包含图片
            last_message = formatted_messages[-1]

            # 如需要，将内容转换为多模态格式
            content = last_message["content"]
            multimodal_content = (
                [{"type": "text", "text": content}]
                if isinstance(content, str)
                else content if isinstance(content, list) else []
            )

            # 将图片添加到内容中
            for image in images:
                if isinstance(image, str):
                    multimodal_content.append(
                        {"type": "image_url", "image_url": {"url": image}}
                    )
                elif isinstance(image, dict) and "url" in image:
                    multimodal_content.append({"type": "image_url", "image_url": image})
                elif isinstance(image, dict) and "image_url" in image:
                    multimodal_content.append(image)
                else:
                    raise ValueError(f"不支持的图片格式：{image}")

            # 用多模态内容更新消息
            last_message["content"] = multimodal_content

            # 如果提供了系统消息则添加
            if system_msgs:
                all_messages = (
                    self.format_messages(system_msgs, supports_images=True)
                    + formatted_messages
                )
            else:
                all_messages = formatted_messages

            # 计算 token 并检查限制
            input_tokens = self.count_message_tokens(all_messages)
            if not self.check_token_limit(input_tokens):
                raise TokenLimitExceeded(self.get_limit_error_message(input_tokens))

            # 设置 API 参数
            params = {
                "model": self.model,
                "messages": all_messages,
                "stream": stream,
            }

            # 添加模型特定参数
            if self.model in REASONING_MODELS:
                params["max_completion_tokens"] = self.max_tokens
            else:
                params["max_tokens"] = self.max_tokens
                params["temperature"] = (
                    temperature if temperature is not None else self.temperature
                )

            # 处理非流式请求
            if not stream:
                response = await self.client.chat.completions.create(**params)

                if not response.choices or not response.choices[0].message.content:
                    raise ValueError("LLM 返回空或无效的响应")

                self.update_token_count(response.usage.prompt_tokens)
                return response.choices[0].message.content

            # 处理流式请求
            self.update_token_count(input_tokens)
            response = await self.client.chat.completions.create(**params)

            collected_messages = []
            async for chunk in response:
                chunk_message = chunk.choices[0].delta.content or ""
                collected_messages.append(chunk_message)
                print(chunk_message, end="", flush=True)

            print()  # 流式输出后换行
            full_response = "".join(collected_messages).strip()

            if not full_response:
                raise ValueError("流式 LLM 返回空响应")

            return full_response

        except TokenLimitExceeded:
            raise
        except ValueError as ve:
            logger.error(f"ask_with_images 中的验证错误：{ve}")
            raise
        except OpenAIError as oe:
            logger.error(f"OpenAI API 错误：{oe}")
            if isinstance(oe, AuthenticationError):
                logger.error("认证失败。请检查 API 密钥。")
            elif isinstance(oe, RateLimitError):
                logger.error("已超过速率限制。建议增加重试次数。")
            elif isinstance(oe, APIError):
                logger.error(f"API 错误：{oe}")
            raise
        except Exception as e:
            logger.error(f"ask_with_images 中的意外错误：{e}")
            raise

    @retry(
        wait=wait_random_exponential(min=1, max=60),
        stop=stop_after_attempt(6),
        retry=retry_if_exception_type(
            (OpenAIError, Exception, ValueError)
        ),  # 不重试 TokenLimitExceeded
    )
    async def ask_tool(
        self,
        messages: List[Union[dict, Message]],
        system_msgs: Optional[List[Union[dict, Message]]] = None,
        timeout: int = 300,
        tools: Optional[List[dict]] = None,
        tool_choice: TOOL_CHOICE_TYPE = ToolChoice.AUTO,  # type: ignore
        temperature: Optional[float] = None,
        **kwargs,
    ) -> ChatCompletionMessage | None:
        """
        使用函数/工具向 LLM 提问并返回响应。

        参数：
            messages: 对话消息列表
            system_msgs: 可选的前置系统消息
            timeout: 请求超时时间（秒）
            tools: 要使用的工具列表
            tool_choice: 工具选择策略
            temperature: 响应的采样温度
            **kwargs: 额外的补全参数

        返回：
            ChatCompletionMessage: 模型的响应

        异常：
            TokenLimitExceeded: 如果超过 token 限制
            ValueError: 如果工具、tool_choice 或消息无效
            OpenAIError: 如果 API 调用在重试后失败
            Exception: 其他意外错误
        """
        try:
            # 验证 tool_choice
            if tool_choice not in TOOL_CHOICE_VALUES:
                raise ValueError(f"无效的 tool_choice：{tool_choice}")

            # 检查模型是否支持图片
            supports_images = self.model in MULTIMODAL_MODELS

            # 格式化消息
            if system_msgs:
                system_msgs = self.format_messages(system_msgs, supports_images)
                messages = system_msgs + self.format_messages(messages, supports_images)
            else:
                messages = self.format_messages(messages, supports_images)

            # 计算输入 token 数
            input_tokens = self.count_message_tokens(messages)

            # 如果有工具，计算工具描述的 token 数
            tools_tokens = 0
            if tools:
                for tool in tools:
                    tools_tokens += self.count_tokens(str(tool))

            input_tokens += tools_tokens

            # 检查是否超过 token 限制
            if not self.check_token_limit(input_tokens):
                error_message = self.get_limit_error_message(input_tokens)
                # 抛出不会重试的特殊异常
                raise TokenLimitExceeded(error_message)

            # 如果提供了工具则验证
            if tools:
                for tool in tools:
                    if not isinstance(tool, dict) or "type" not in tool:
                        raise ValueError("每个工具必须是包含 'type' 字段的字典")

            # 设置补全请求
            params = {
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
                "timeout": timeout,
                **kwargs,
            }

            if self.model in REASONING_MODELS:
                params["max_completion_tokens"] = self.max_tokens
            else:
                params["max_tokens"] = self.max_tokens
                params["temperature"] = (
                    temperature if temperature is not None else self.temperature
                )

            params["stream"] = False  # 工具请求始终使用非流式模式
            response: ChatCompletion = await self.client.chat.completions.create(
                **params
            )

            # 检查响应是否有效
            if not response.choices or not response.choices[0].message:
                print(response)
                # raise ValueError("LLM 返回无效或空的响应")
                return None

            # 更新 token 计数
            self.update_token_count(
                response.usage.prompt_tokens, response.usage.completion_tokens
            )

            return response.choices[0].message

        except TokenLimitExceeded:
            # 重新抛出 token 限制错误，不记录日志
            raise
        except ValueError as ve:
            logger.error(f"ask_tool 中的验证错误：{ve}")
            raise
        except OpenAIError as oe:
            logger.error(f"OpenAI API 错误：{oe}")
            if isinstance(oe, AuthenticationError):
                logger.error("认证失败。请检查 API 密钥。")
            elif isinstance(oe, RateLimitError):
                logger.error("已超过速率限制。建议增加重试次数。")
            elif isinstance(oe, APIError):
                logger.error(f"API 错误：{oe}")
            raise
        except Exception as e:
            logger.error(f"ask_tool 中的意外错误：{e}")
            raise
