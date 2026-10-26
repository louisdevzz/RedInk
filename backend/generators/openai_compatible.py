"""OpenAI 兼容接口图片生成器"""
import logging
from typing import Dict, Any
from .base import ImageGeneratorBase
from .image_api_client import ImageApiClient
from .image_provider_policy import ImageProviderPolicy

logger = logging.getLogger(__name__)


class OpenAICompatibleGenerator(ImageGeneratorBase):
    """OpenAI 兼容接口图片生成器"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        logger.debug("初始化 OpenAICompatibleGenerator...")

        if not self.api_key:
            logger.error("OpenAI 兼容 API Key 未配置")
            raise ValueError(
                "OpenAI 兼容 API Key 未配置。\n"
                "【Giải pháp】Trong trang Cài đặt hệ thống, chỉnh sửa nhà cung cấp này và điền API Key."
            )

        if not self.base_url:
            logger.error("OpenAI 兼容 API Base URL 未配置")
            raise ValueError(
                "OpenAI 兼容 API Base URL 未配置。\n"
                "【Giải pháp】Trong trang Cài đặt hệ thống, chỉnh sửa nhà cung cấp này và điền Base URL."
            )

        self.policy = ImageProviderPolicy.from_config(
            config,
            default_model='gpt-image-2',
            default_endpoint='/v1/images/generations',
        )
        self.client = ImageApiClient(self.policy)
        self.base_url = self.policy.base_url
        self.default_model = self.policy.model
        self.endpoint_type = self.policy.endpoint_type

        logger.info(f"OpenAICompatibleGenerator 初始化完成: base_url={self.base_url}, model={self.default_model}, endpoint={self.endpoint_type}")

    def validate_config(self) -> bool:
        """验证配置"""
        return bool(self.api_key and self.base_url)

    def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        model: str = None,
        quality: str = "standard",
        **kwargs
    ) -> bytes:
        """
        生成图片

        Args:
            prompt: 提示词
            size: 图片尺寸 (如 "1024x1024", "2048x2048", "4096x4096")
            model: 模型名称
            quality: 质量 ("standard" 或 "hd")
            **kwargs: 其他参数

        Returns:
            图片二进制数据
        """
        if model is None:
            model = self.default_model

        logger.info(f"OpenAI 兼容 API 生成图片: model={model}, size={size}, endpoint={self.endpoint_type}")

        # 根据端点路径决定使用哪种 API 方式
        if 'chat' in self.endpoint_type or 'completions' in self.endpoint_type:
            return self._generate_via_chat_api(prompt, size, model)
        else:
            return self._generate_via_images_api(prompt, size, model, quality)

    def _generate_via_images_api(
        self,
        prompt: str,
        size: str,
        model: str,
        quality: str
    ) -> bytes:
        """通过 images API 端点生成"""
        payload = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": size
        }

        return self.client.generate_via_images(payload)

    def _generate_via_chat_api(
        self,
        prompt: str,
        size: str,
        model: str
    ) -> bytes:
        """
        通过 chat/completions 端点生成图片

        支持多种返回格式：
        1. Markdown 图片链接: ![xxx](url) - 即梦、部分中转站使用
        2. Base64 data URL: data:image/xxx;base64,xxx
        3. 纯图片 URL
        """
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "max_tokens": 4096,
            "temperature": 1.0
        }

        return self.client.generate_via_chat(payload)

    def get_supported_sizes(self) -> list:
        """获取支持的图片尺寸"""
        # 默认OpenAI支持的尺寸
        return self.config.get('supported_sizes', [
            "1024x1024",
            "1792x1024",
            "1024x1792",
            "2048x2048",
            "4096x4096"
        ])
