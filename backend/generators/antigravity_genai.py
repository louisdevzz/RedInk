"""
Antigravity（Google 账号登录）图片生成器

复用 Cloud Code Assist 的图片 SKU（gemini-3-pro-image / gemini-3.1-flash-image），
账号可用 which 由 fetchAvailableModels 的 imageGenerationModelIds 决定。
无需 API Key：只要系统设置里已登录 Google 账号。
"""
import logging
from typing import Any, Dict, List, Optional

from .base import ImageGeneratorBase
from ..auth.antigravity_constants import DEFAULT_IMAGE_MODEL
from ..auth.antigravity_credentials import connection_summary
from ..utils.antigravity_client import AntigravityClient

logger = logging.getLogger(__name__)



class AntigravityImageGenerator(ImageGeneratorBase):
    """Google Antigravity 图片生成器（OAuth，无 API Key）"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        logger.debug("初始化 AntigravityImageGenerator...")

        if not self.validate_config():
            summary = connection_summary()
            if not summary.get('connected'):
                raise ValueError(
                    "尚未登录 Google 账号（Antigravity）。\n"
                    "解决方案：打开系统设置页面，在「Google 账号登录」中完成登录，无需 API Key"
                )
            raise ValueError(
                "Google 登录凭据不可用。\n"
                "解决方案：在系统设置页面重新登录 Google 账号"
            )

        self.client = AntigravityClient(base_url=config.get('base_url'))
        self.default_model = config.get('model') or DEFAULT_IMAGE_MODEL
        logger.info(f"AntigravityImageGenerator 初始化完成 (model={self.default_model})")

    def validate_config(self) -> bool:
        """OAuth 服务商：校验登录状态而不是 API Key。"""
        return bool(connection_summary().get('connected'))

    def get_supported_aspect_ratios(self) -> list:
        return ['1:1', '3:4', '4:3', '16:9', '9:16']

    def generate_image(
        self,
        prompt: str,
        aspect_ratio: str = '3:4',
        temperature: float = 1.0,
        model: Optional[str] = None,
        reference_image: Optional[bytes] = None,
        reference_images: Optional[List[bytes]] = None,
        **kwargs,
    ) -> bytes:
        """
        生成图片，返回第一张 PNG 的二进制内容。

        Args:
            prompt: 提示词
            aspect_ratio: 宽高比
            temperature: 兼容参数（该接口不接受，忽略）
            model: 模型名，'auto'/空 时按账号自动选择
            reference_image: 单张参考图
            reference_images: 多张参考图
        """
        references: List[bytes] = []
        if reference_images:
            references.extend([img for img in reference_images if img])
        if reference_image:
            references.append(reference_image)

        images = self.client.generate_image(
            prompt=prompt,
            model=model or self.default_model,
            aspect_ratio=aspect_ratio,
            reference_images=references or None,
        )
        return images[0]
