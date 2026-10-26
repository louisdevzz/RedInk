"""Image API 图片生成器"""
import logging
import base64
from typing import Dict, Any, Optional, List
from .base import ImageGeneratorBase
from .image_api_client import ImageApiClient
from .image_provider_policy import ImageProviderPolicy
from ..utils.image_compressor import compress_image

logger = logging.getLogger(__name__)


class ImageApiGenerator(ImageGeneratorBase):
    """Image API 生成器"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        logger.debug("初始化 ImageApiGenerator...")
        self.policy = ImageProviderPolicy.from_config(
            config,
            default_model='default-model',
            default_endpoint='/v1/images/generations',
        )
        self.client = ImageApiClient(self.policy)
        self.base_url = self.policy.base_url
        self.model = self.policy.model
        self.default_aspect_ratio = config.get('default_aspect_ratio', '3:4')
        self.image_size = config.get('image_size', '4K')
        self.endpoint_type = self.policy.endpoint_type

        logger.info(f"ImageApiGenerator 初始化完成: base_url={self.base_url}, model={self.model}, endpoint={self.endpoint_type}")

    def validate_config(self) -> bool:
        """验证配置是否有效"""
        if not self.api_key:
            logger.error("Image API Key 未配置")
            raise ValueError(
                "Image API Key 未配置。\n"
                "【Giải pháp】Trong trang Cài đặt hệ thống, chỉnh sửa nhà cung cấp này và điền API Key."
            )
        return True

    def get_supported_sizes(self) -> List[str]:
        """获取支持的图片尺寸"""
        return ["1K", "2K", "4K"]

    def get_supported_aspect_ratios(self) -> List[str]:
        """获取支持的宽高比"""
        return ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"]

    def generate_image(
        self,
        prompt: str,
        aspect_ratio: str = None,
        temperature: float = 1.0,
        model: str = None,
        reference_image: Optional[bytes] = None,
        reference_images: Optional[List[bytes]] = None,
        **kwargs
    ) -> bytes:
        """
        生成图片

        Args:
            prompt: 图片描述
            aspect_ratio: 宽高比
            temperature: 创意度（未使用，保留接口兼容）
            model: 模型名称
            reference_image: 单张参考图片数据（向后兼容）
            reference_images: 多张参考图片数据列表

        Returns:
            生成的图片二进制数据
        """
        self.validate_config()

        if aspect_ratio is None:
            aspect_ratio = self.default_aspect_ratio

        if model is None:
            model = self.model

        logger.info(f"Image API 生成图片: model={model}, aspect_ratio={aspect_ratio}, endpoint={self.endpoint_type}")

        # 根据端点类型选择不同的生成方式
        if 'chat' in self.endpoint_type or 'completions' in self.endpoint_type:
            return self._generate_via_chat_api(prompt, aspect_ratio, model, reference_image, reference_images)
        else:
            return self._generate_via_images_api(prompt, aspect_ratio, model, reference_image, reference_images)

    def _generate_via_images_api(
        self,
        prompt: str,
        aspect_ratio: str,
        model: str,
        reference_image: Optional[bytes] = None,
        reference_images: Optional[List[bytes]] = None
    ) -> bytes:
        """通过 /v1/images/generations 端点生成图片"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "image_size": self.image_size
        }

        # 收集所有参考图片
        all_reference_images = []
        if reference_images and len(reference_images) > 0:
            all_reference_images.extend(reference_images)
        if reference_image and reference_image not in all_reference_images:
            all_reference_images.append(reference_image)

        # 如果有参考图片，添加到 image 数组
        if all_reference_images:
            logger.debug(f"  添加 {len(all_reference_images)} 张参考图片")
            image_uris = []
            for idx, img_data in enumerate(all_reference_images):
                compressed_img = compress_image(img_data, max_size_kb=200)
                logger.debug(f"  参考图 {idx}: {len(img_data)} -> {len(compressed_img)} bytes")
                base64_image = base64.b64encode(compressed_img).decode('utf-8')
                data_uri = f"data:image/png;base64,{base64_image}"
                image_uris.append(data_uri)

            payload["image"] = image_uris

            ref_count = len(all_reference_images)
            enhanced_prompt = f"""参考提供的 {ref_count} 张图片的风格（色彩、光影、构图、氛围），生成一张新图片。

新图片内容：{prompt}

要求：
1. 保持相似的色调和氛围
2. 使用相似的光影处理
3. 保持一致的画面质感
4. 如果参考图中有人物或产品，可以适当融入"""
            payload["prompt"] = enhanced_prompt

        return self.client.generate_via_images(payload)

    def _generate_via_chat_api(
        self,
        prompt: str,
        aspect_ratio: str,
        model: str,
        reference_image: Optional[bytes] = None,
        reference_images: Optional[List[bytes]] = None
    ) -> bytes:
        """通过 /v1/chat/completions 端点生成图片（如即梦 API）"""
        # 构建用户消息内容
        user_content: Any = prompt

        # 收集所有参考图片
        all_reference_images = []
        if reference_images and len(reference_images) > 0:
            all_reference_images.extend(reference_images)
        if reference_image and reference_image not in all_reference_images:
            all_reference_images.append(reference_image)

        # 如果有参考图片，构建多模态消息
        if all_reference_images:
            logger.debug(f"  添加 {len(all_reference_images)} 张参考图片到 chat 消息")
            content_parts = [{"type": "text", "text": prompt}]

            for idx, img_data in enumerate(all_reference_images):
                compressed_img = compress_image(img_data, max_size_kb=200)
                logger.debug(f"  参考图 {idx}: {len(img_data)} -> {len(compressed_img)} bytes")
                base64_image = base64.b64encode(compressed_img).decode('utf-8')
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{base64_image}"}
                })

            user_content = content_parts

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": user_content}],
            "max_tokens": 4096,
            "temperature": 1.0
        }

        return self.client.generate_via_chat(payload)
