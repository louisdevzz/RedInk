"""Text API 客户端封装"""
import time
import random
import base64
import requests
from functools import wraps
from typing import List, Optional, Union
from .image_compressor import compress_image


def retry_on_429(max_retries=3, base_delay=2):
    """429 错误自动重试装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    error_str = str(e)
                    if "429" in error_str or "rate" in error_str.lower():
                        if attempt < max_retries - 1:
                            wait_time = (base_delay ** attempt) + random.uniform(0, 1)
                            print(f"[重试] 遇到限流，{wait_time:.1f}秒后重试 (尝试 {attempt + 2}/{max_retries})")
                            time.sleep(wait_time)
                            continue
                    raise
            raise Exception(
                f"Text API sau khi thử lại {max_retries} lần vẫn thất bại.\n"
                "Nguyên nhân có thể:\n"
                "1. API liên tục bị giới hạn tần suất hoặc hạn mức không đủ\n"
                "2. 连接 kết nối 网络 không ổn định kéo dài\n"
                "3. Dịch vụ API tạm thời không khả dụng\n"
                "Khuyến nghị: Thử lại sau, hoặc liên hệ nhà cung cấp dịch vụ API"
            )
        return wrapper
    return decorator


class TextChatClient:
    """Text API 客户端封装类"""

    def __init__(self, api_key: str = None, base_url: str = None, endpoint_type: str = None):
        self.api_key = api_key
        if not self.api_key:
            raise ValueError(
                "Text 未配置 API Key.\n"
                "Giải pháp: Chỉnh sửa nhà cung cấp tạo văn bản trong trang Cài đặt hệ thống, điền API Key"
            )

        self.base_url = (base_url or "https://api.openai.com").rstrip('/')
        if self.base_url.endswith('/v1'):
            self.base_url = self.base_url[:-3]

        # 支持自定义端点路径
        endpoint = endpoint_type or '/v1/chat/completions'
        # 确保端点以 / 开头
        if not endpoint.startswith('/'):
            endpoint = '/' + endpoint
        self.chat_endpoint = f"{self.base_url}{endpoint}"

    def _encode_image_to_base64(self, image_data: bytes) -> str:
        """将图片数据编码为 base64"""
        return base64.b64encode(image_data).decode('utf-8')

    def _build_content_with_images(
        self,
        text: str,
        images: List[Union[bytes, str]] = None
    ) -> Union[str, List[dict]]:
        """
        构建包含图片的 content

        Args:
            text: 文本内容
            images: 图片列表，可以是 bytes（图片数据）或 str（URL）

        Returns:
            如果没有图片，返回纯文本；有图片则返回多模态内容列表
        """
        if not images:
            return text

        content = [{"type": "text", "text": text}]

        for img in images:
            if isinstance(img, bytes):
                # 压缩图片到 200KB 以内
                compressed_img = compress_image(img, max_size_kb=200)
                # 图片数据，转为 base64 data URL
                base64_data = self._encode_image_to_base64(compressed_img)
                image_url = f"data:image/png;base64,{base64_data}"
            else:
                # 已经是 URL
                image_url = img

            content.append({
                "type": "image_url",
                "image_url": {"url": image_url}
            })

        return content

    @retry_on_429(max_retries=3, base_delay=2)
    def generate_text(
        self,
        prompt: str,
        model: str = "gemini-3-pro-preview",
        temperature: float = 1.0,
        max_output_tokens: int = 8000,
        images: List[Union[bytes, str]] = None,
        system_prompt: str = None,
        **kwargs
    ) -> str:
        """
        生成文本（支持图片输入）

        Args:
            prompt: 提示词
            model: 模型名称
            temperature: 温度
            max_output_tokens: 最大输出 token
            images: 图片列表（可选）
            system_prompt: 系统提示词（可选）

        Returns:
            生成的文本
        """
        messages = []

        # 添加系统提示词
        if system_prompt:
            messages.append({
                "role": "system",
                "content": system_prompt
            })

        # 构建用户消息内容
        content = self._build_content_with_images(prompt, images)
        messages.append({
            "role": "user",
            "content": content
        })

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_output_tokens,
            "stream": False
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        response = requests.post(
            self.chat_endpoint,
            json=payload,
            headers=headers,
            timeout=300  # 5分钟超时
        )

        if response.status_code != 200:
            error_detail = response.text[:500]
            status_code = response.status_code

            # 根据状态码给出更详细的错误信息
            if status_code == 401:
                raise Exception(
                    "❌ Xác thực API Key thất bại (未配置 API Key / 失效)\n\n"
                    "【Nguyên nhân có thể】\n"
                    "1. API Key không hợp lệ hoặc đã hết hạn\n"
                    "2. Định dạng API Key bị sai (khi sao chép có thể dính khoảng trắng)\n"
                    "3. API Key bị vô hiệu hóa hoặc xóa\n\n"
                    "【Giải pháp】\n"
                    "1. Kiểm tra lại API Key trong trang Cài đặt hệ thống xem đã chính xác chưa\n"
                    "2. Lấy lại API Key mới\n"
                    f"\n【Địa chỉ yêu cầu】{self.chat_endpoint}"
                )
            elif status_code == 403:
                raise Exception(
                    "❌ Quyền truy cập bị từ chối\n\n"
                    "【Nguyên nhân có thể】\n"
                    "1. API Key không có quyền truy cập model này\n"
                    "2. Hạn mức tài khoản đã dùng hết\n"
                    "3. Giới hạn khu vực địa lý\n\n"
                    "【Giải pháp】\n"
                    "1. Kiểm tra cấu hình quyền hạn API\n"
                    "2. Thử sử dụng model khác\n"
                    f"\n【Lỗi gốc】{error_detail[:200]}"
                )
            elif status_code == 404:
                raise Exception(
                    "❌ Model không tồn tại hoặc endpoint API bị sai\n\n"
                    "【Nguyên nhân có thể】\n"
                    f"1. Model '{model}' không tồn tại hoặc đã ngừng hỗ trợ\n"
                    "2. Cấu hình Base URL bị sai\n\n"
                    "【Giải pháp】\n"
                    "1. Kiểm tra tên model đã đúng chưa\n"
                    "2. Kiểm tra cấu hình Base URL\n"
                    f"\n【Địa chỉ yêu cầu】{self.chat_endpoint}"
                )
            elif status_code == 429:
                raise Exception(
                    "⏳ Hạn mức hoặc giới hạn tần suất API (超时 / 限流)\n\n"
                    "【Mô tả】\n"
                    "Tần suất gửi yêu cầu quá cao hoặc hạn mức đã dùng hết.\n\n"
                    "【Giải pháp】\n"
                    "1. Thử lại sau (chờ 1-2 phút)\n"
                    "2. Kiểm tra tình hình sử dụng hạn mức API\n"
                    "3. Xem xét nâng cấp gói để có thêm hạn mức"
                )
            elif status_code >= 500:
                raise Exception(
                    f"⚠️ Lỗi máy chủ API ({status_code})\n\n"
                    "【Mô tả】\n"
                    "Đây là sự cố tạm thời phía máy chủ, không liên quan đến cấu hình của bạn.\n\n"
                    "【Giải pháp】\n"
                    "1. Chờ vài phút rồi thử lại\n"
                    "2. Nếu vẫn tiếp diễn, hãy kiểm tra trang trạng thái của nhà cung cấp"
                )
            else:
                raise Exception(
                    f"❌ Yêu cầu API thất bại (mã trạng thái: {status_code})\n\n"
                    f"【Lỗi gốc】\n{error_detail}\n\n"
                    f"【Địa chỉ yêu cầu】{self.chat_endpoint}\n"
                    f"【Model】{model}\n\n"
                    "【Giải pháp chung】\n"
                    "1. Kiểm tra API Key đã đúng chưa\n"
                    "2. Kiểm tra cấu hình Base URL\n"
                    "3. Kiểm tra tên model đã đúng chưa"
                )

        result = response.json()

        # 提取生成的文本
        if "choices" in result and len(result["choices"]) > 0:
            return result["choices"][0]["message"]["content"]
        else:
            raise Exception(
                f"Định dạng phản hồi Text API bất thường: không tìm thấy văn bản được tạo.\n"
                f"Dữ liệu phản hồi: {str(result)[:500]}\n"
                "Nguyên nhân có thể:\n"
                "1. Định dạng phản hồi của API không khớp với tiêu chuẩn OpenAI\n"
                "2. Yêu cầu bị từ chối hoặc bị bộ lọc an toàn chặn\n"
                "3. Model trả về nội dung rỗng\n"
                "Khuyến nghị: Kiểm tra tài liệu API để xác nhận định dạng phản hồi"
            )


def get_text_chat_client(provider_config: dict):
    """
    获取 Text Chat 客户端实例（根据 type 返回对应客户端）

    Args:
        provider_config: 服务商配置字典
            - type: 'google_gemini' / 'openai_compatible' / 'google_antigravity'
            - api_key: API密钥（OAuth 服务商可省略）
            - base_url: API基础URL（可选）
            - endpoint_type: 自定义端点路径（可选）

    Returns:
        AntigravityTextClient、GenAIClient 或 TextChatClient
    """
    provider_type = provider_config.get('type', 'openai_compatible')
    api_key = provider_config.get('api_key')
    base_url = provider_config.get('base_url')
    endpoint_type = provider_config.get('endpoint_type')

    # Antigravity：Google 账号登录，不使用 API Key
    if provider_type == 'google_antigravity' or provider_config.get('auth') == 'antigravity_oauth':
        from .antigravity_text_client import get_antigravity_text_client
        return get_antigravity_text_client(provider_config)

    if provider_type == 'google_gemini':
        from .genai_client import GenAIClient
        return GenAIClient(api_key=api_key, base_url=base_url)
    else:
        return TextChatClient(api_key=api_key, base_url=base_url, endpoint_type=endpoint_type)
