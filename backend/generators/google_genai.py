"""Google GenAI 图片生成器"""
import logging
import base64
from typing import Dict, Any, Optional
from google import genai
from google.genai import types
from .base import ImageGeneratorBase
from ..utils.image_compressor import compress_image

logger = logging.getLogger(__name__)


def parse_genai_error(error: Exception) -> str:
    """
    解析 Google GenAI API 错误，返回用户友好的错误信息

    识别的错误类型：
    - 401 UNAUTHENTICATED: API Key 无效或认证失败
    - 403 PERMISSION_DENIED: 权限不足
    - 404 NOT_FOUND: 模型不存在
    - 429 RESOURCE_EXHAUSTED: 配额用尽或速率限制
    - 400 INVALID_ARGUMENT: 参数错误
    - 500 INTERNAL: 服务器内部错误
    - 503 UNAVAILABLE: 服务不可用
    - 安全过滤相关错误
    - 网络连接错误
    """
    error_str = str(error).lower()
    error_original = str(error)

    # 401 认证错误
    if "401" in error_str or "unauthenticated" in error_str:
        if "api key" in error_str and "not supported" in error_str:
            return (
                "❌ Xác thực API Key thất bại: Vertex AI không hỗ trợ API Key\n\n"
                "【Nguyên nhân】\n"
                "Bạn có thể đã dùng nhầm chế độ Vertex AI, chế độ này yêu cầu xác thực OAuth2 thay vì API Key.\n\n"
                "【Giải pháp】\n"
                "1. Nếu bạn sử dụng API Key của Google AI Studio:\n"
                "   - Đảm bảo trong Cài đặt không cấu hình base_url (để trống)\n"
                "   - Địa chỉ lấy API Key: https://aistudio.google.com/app/apikey\n\n"
                "2. Nếu bạn sử dụng API Key của Google Cloud:\n"
                "   - Đảm bảo API Key đã kích hoạt Generative Language API\n"
                "   - Kiểm tra quyền API trong Google Cloud Console\n\n"
                "3. Nếu bạn thực sự cần dùng Vertex AI:\n"
                "   - Vertex AI yêu cầu xác thực Service Account, không hỗ trợ API Key\n"
                "   - Vui lòng tham khảo tài liệu để cấu hình Application Default Credentials"
            )
        else:
            return (
                "❌ Xác thực API Key thất bại\n\n"
                "【Nguyên nhân có thể】\n"
                "1. API Key không hợp lệ hoặc đã hết hạn\n"
                "2. Định dạng API Key sai (có thể chứa khoảng trắng khi copy)\n"
                "3. API Key bị vô hiệu hóa hoặc xóa\n\n"
                "【Giải pháp】\n"
                "1. Kiểm tra API Key đã được copy chính xác chưa (không chứa khoảng trắng thừa)\n"
                "2. Đến Google AI Studio tạo lại API Key:\n"
                "   https://aistudio.google.com/app/apikey\n"
                "3. Đảm bảo dự án tương ứng với API Key đã bật các API liên quan"
            )

    # 403 权限错误
    if "403" in error_str or "permission_denied" in error_str or "forbidden" in error_str:
        if "billing" in error_str or "quota" in error_str:
            return (
                "❌ Quyền hạn bị từ chối: Chưa bật thanh toán hoặc hạn mức 配额 không đủ\n\n"
                "【Giải pháp】\n"
                "1. Kiểm tra dự án Google Cloud đã kích hoạt thanh toán (billing) chưa\n"
                "2. Kiểm tra hạn mức 配额 của API đã dùng hết chưa\n"
                "3. Nếu là tài khoản dùng thử miễn phí, có thể bị giới hạn sử dụng"
            )
        elif "region" in error_str or "location" in error_str:
            return (
                "❌ Quyền hạn bị từ chối: Giới hạn khu vực địa lý\n\n"
                "【Giải pháp】\n"
                "1. Một số API có thể không khả dụng tại khu vực của bạn\n"
                "2. Thử dùng proxy hoặc cấu hình base_url trỏ tới khu vực khả dụng"
            )
        else:
            return (
                "❌ Quyền hạn bị từ chối\n\n"
                "【Nguyên nhân có thể】\n"
                "1. API Key không có quyền truy cập model này\n"
                "2. Model có thể yêu cầu quyền đặc biệt hoặc whitelist\n"
                "3. Hạn mức 配额 hoặc giới hạn của dự án\n\n"
                "【Giải pháp】\n"
                "1. Kiểm tra quyền hạn API trong Google Cloud Console\n"
                "2. Xác nhận model đã được mở cho tài khoản của bạn chưa\n"
                "3. Thử đổi sang model khác (ví dụ: gemini-2.0-flash-exp)"
            )

    # 404 资源不存在
    if "404" in error_str or "not_found" in error_str or "not found" in error_str:
        if "model" in error_str:
            return (
                "❌ Model không tồn tại 不存在\n\n"
                "【Nguyên nhân có thể】\n"
                "1. Tên model bị viết sai\n"
                "2. Model này đã ngừng cung cấp hoặc đổi tên\n"
                "3. Model này chưa được mở tại khu vực của bạn\n\n"
                "【Giải pháp】\n"
                "1. Kiểm tra tên model đã chính xác chưa\n"
                "2. Các model tạo ảnh khuyên dùng:\n"
                "   - imagen-3.0-generate-002 (khuyên dùng)\n"
                "   - gemini-2.0-flash-exp-image-generation\n"
                "3. Xem tài liệu chính thức để lấy danh sách model khả dụng mới nhất"
            )
        else:
            return (
                "❌ Tài nguyên được yêu cầu không tồn tại 不存在\n\n"
                f"【原始错误】{error_original[:200]}\n\n"
                "【Giải pháp】Kiểm tra cấu hình endpoint API và tham số"
            )

    # 429 速率限制/配额用尽
    if "429" in error_str or "resource_exhausted" in error_str or "quota" in error_str:
        if "per minute" in error_str or "rpm" in error_str:
            return (
                "⏳ Tần suất yêu cầu vượt giới hạn 限流 (giới hạn RPM)\n\n"
                "【Mô tả】\n"
                "Tần suất gửi yêu cầu của bạn đã vượt quá giới hạn mỗi phút.\n\n"
                "【Giải pháp】\n"
                "1. Chờ giây lát rồi thử lại\n"
                "2. Tắt chế độ đồng thời cao trong Cài đặt\n"
                "3. Giảm số lượng hình ảnh tạo đồng thời"
            )
        elif "per day" in error_str or "daily" in error_str:
            return (
                "⏳ Hạn mức 配额 hàng ngày đã dùng hết\n\n"
                "【Mô tả】\n"
                "Số lượt gọi API hôm nay của bạn đã chạm mức tối đa.\n\n"
                "【Giải pháp】\n"
                "1. Chờ reset hạn mức 配额 ngày mai (thường vào UTC 0:00)\n"
                "2. Nâng cấp lên gói trả phí để có thêm hạn mức 配额\n"
                "3. Sử dụng API Key khác"
            )
        else:
            return (
                "⏳ Hạn mức 配额 hoặc giới hạn tần suất 限流 của API\n\n"
                "【Nguyên nhân có thể】\n"
                "1. Tần suất gửi yêu cầu quá cao\n"
                "2. Hạn mức 配额 miễn phí đã dùng hết\n"
                "3. Hạn mức 配额 tài khoản đã chạm ngưỡng tối đa\n\n"
                "【Giải pháp】\n"
                "1. Thử lại sau (thường chờ 1-2 phút)\n"
                "2. Kiểm tra tình hình sử dụng hạn mức 配额 trong Google Cloud Console\n"
                "3. Cân nhắc nâng cấp gói hoặc đăng ký thêm hạn mức 配额"
            )

    # 400 参数错误
    if "400" in error_str or "invalid_argument" in error_str or "invalid" in error_str:
        if "image" in error_str and ("size" in error_str or "large" in error_str):
            return (
                "❌ Lỗi tham số hình ảnh: Kích thước ảnh quá lớn\n\n"
                "【Giải pháp】\n"
                "Ảnh tham khảo sẽ được tự động nén, nhưng nếu vẫn báo lỗi, vui lòng thử tải lên ảnh nhỏ hơn"
            )
        elif "prompt" in error_str or "content" in error_str:
            return (
                "❌ Lỗi tham số prompt\n\n"
                "【Nguyên nhân có thể】\n"
                "1. Prompt quá dài\n"
                "2. Prompt chứa ký tự không được hỗ trợ\n"
                "3. Prompt vi phạm bộ lọc nội dung\n\n"
                "【Giải pháp】\n"
                "1. Thử rút ngắn prompt\n"
                "2. Loại bỏ ký tự đặc biệt hoặc nội dung nhạy cảm\n"
                "3. Sử dụng mô tả khách quan, trung tính hơn"
            )
        else:
            return (
                f"❌ Lỗi tham số yêu cầu\n\n"
                f"【原始错误】{error_original[:300]}\n\n"
                "【Giải pháp】Kiểm tra tham số yêu cầu đã chính xác chưa"
            )

    # 安全过滤
    if "safety" in error_str or "blocked" in error_str or "filter" in error_str:
        return (
            "🛡️ Nội dung bị bộ lọc an toàn chặn\n\n"
            "【Mô tả】\n"
            "Prompt hoặc nội dung được tạo của bạn đã kích hoạt cơ chế lọc an toàn của Google.\n\n"
            "【Giải pháp】\n"
            "1. Chỉnh sửa prompt, sử dụng mô tả trung tính hơn\n"
            "2. Tránh các chủ đề nhạy cảm\n"
            "3. Thử diễn đạt lại nội dung theo cách khác"
        )

    # 图片生成特定错误
    if "could not generate" in error_str or "unable to generate" in error_str:
        return (
            "❌ Model không thể tạo hình ảnh\n\n"
            "【Nguyên nhân có thể】\n"
            "1. Model này không hỗ trợ tính năng tạo ảnh\n"
            "2. Prompt quá phức tạp hoặc mơ hồ\n"
            "3. Model tạm thời không khả dụng\n\n"
            "【Giải pháp】\n"
            "1. Xác nhận sử dụng model hỗ trợ tạo ảnh:\n"
            "   - imagen-3.0-generate-002\n"
            "   - gemini-2.0-flash-exp-image-generation\n"
            "2. Đơn giản hóa mô tả trong prompt\n"
            "3. Thử lại sau"
        )

    # 500 服务器错误
    if "500" in error_str or "internal" in error_str:
        return (
            "⚠️ Lỗi nội bộ máy chủ Google API\n\n"
            "【Mô tả】\n"
            "Đây là sự cố tạm thời phía máy chủ Google, không liên quan đến cấu hình của bạn.\n\n"
            "【Giải pháp】\n"
            "1. Chờ vài phút rồi thử lại\n"
            "2. Nếu vẫn tiếp diễn, bạn có thể kiểm tra Google Cloud Status"
        )

    # 503 服务不可用
    if "503" in error_str or "unavailable" in error_str:
        return (
            "⚠️ Dịch vụ Google API tạm thời không khả dụng\n\n"
            "【Mô tả】\n"
            "Dịch vụ đang bảo trì hoặc tạm thời quá tải.\n\n"
            "【Giải pháp】\n"
            "1. Chờ vài phút rồi thử lại\n"
            "2. Kiểm tra Google Cloud Status để biết trạng thái dịch vụ"
        )

    # 网络错误
    if "timeout" in error_str or "timed out" in error_str:
        return (
            "⏱️ Yêu cầu 超时\n\n"
            "【Nguyên nhân có thể】\n"
            "1. Kết nối 网络 không ổn định\n"
            "2. Dịch vụ API phản hồi chậm\n"
            "3. Thời gian tạo hình ảnh quá dài\n\n"
            "【Giải pháp】\n"
            "1. Kiểm tra kết nối 网络\n"
            "2. Thử gửi lại yêu cầu\n"
            "3. Nếu sử dụng proxy, kiểm tra xem proxy có hoạt động bình thường không"
        )

    if "connection" in error_str or "network" in error_str or "refused" in error_str:
        return (
            "🌐 Lỗi kết nối 网络 (连接失败)\n\n"
            "【Nguyên nhân có thể】\n"
            "1. Mất kết nối 网络\n"
            "2. Không thể truy cập Google API (có thể bị tường lửa chặn)\n"
            "3. Sự cố cấu hình proxy\n\n"
            "【Giải pháp】\n"
            "1. Kiểm tra kết nối 网络 có bình thường không\n"
            "2. Cấu hình proxy nếu cần thiết\n"
            "3. Trong Cài đặt, cấu hình base_url trỏ tới địa chỉ proxy khả dụng"
        )

    if "ssl" in error_str or "certificate" in error_str:
        return (
            "🔒 Lỗi chứng chỉ SSL/TLS\n\n"
            "【Nguyên nhân có thể】\n"
            "1. Thời gian hệ thống không chính xác\n"
            "2. Proxy hoặc tường lửa can thiệp kết nối HTTPS\n"
            "3. Chứng chỉ đã hết hạn hoặc không hợp lệ\n\n"
            "【Giải pháp】\n"
            "1. Kiểm tra thời gian hệ thống đã chuẩn xác chưa\n"
            "2. Kiểm tra thiết lập proxy hoặc tường lửa"
        )

    # 默认错误
    return (
        f"❌ Gọi API thất bại\n\n"
        f"【原始错误】\n{error_original[:500]}\n\n"
        "【Giải pháp】\n"
        "1. Kiểm tra API Key đã được cấu hình chính xác chưa\n"
        "2. Kiểm tra kết nối 网络 có bình thường không\n"
        "3. Thử đổi model khác hoặc đơn giản hóa prompt\n"
        "4. Xem nhật ký backend để biết thêm chi tiết"
    )


class GoogleGenAIGenerator(ImageGeneratorBase):
    """Google GenAI 图片生成器"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        logger.debug("初始化 GoogleGenAIGenerator...")

        if not self.api_key:
            logger.error("Google GenAI API Key 未配置")
            raise ValueError(
                "Google GenAI API Key 未配置。\n"
                "【Giải pháp】Trong trang Cài đặt hệ thống, chỉnh sửa nhà cung cấp này và điền API Key.\n"
                "Lấy API Key: https://aistudio.google.com/app/apikey"
            )

        # 初始化客户端
        logger.debug("初始化 Google GenAI 客户端...")
        client_kwargs = {
            "api_key": self.api_key,
        }

        # 默认使用 Gemini API (vertexai=False)，因为大多数用户使用 Google AI Studio 的 API Key
        # Vertex AI 需要 OAuth2 认证，不支持 API Key
        self.is_vertexai = False

        # 如果有 base_url，则配置 http_options
        if self.config.get('base_url'):
            logger.debug(f"  使用自定义 base_url: {self.config['base_url']}")
            client_kwargs["http_options"] = {
                "base_url": self.config['base_url'],
                "api_version": "v1beta"
            }

        client_kwargs["vertexai"] = False

        self.client = genai.Client(**client_kwargs)

        # 默认安全设置
        self.safety_settings = [
            types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF"),
        ]
        logger.info("GoogleGenAIGenerator 初始化完成")

    def validate_config(self) -> bool:
        """验证配置"""
        return bool(self.api_key)

    def generate_image(
        self,
        prompt: str,
        aspect_ratio: str = "3:4",
        temperature: float = 1.0,
        model: str = "gemini-3-pro-image-preview",
        reference_image: Optional[bytes] = None,
        **kwargs
    ) -> bytes:
        """
        生成图片

        Args:
            prompt: 提示词
            aspect_ratio: 宽高比 (如 "3:4", "1:1", "16:9")
            temperature: 温度
            model: 模型名称
            reference_image: 参考图片二进制数据（用于保持风格一致）
            **kwargs: 其他参数

        Returns:
            图片二进制数据
        """
        logger.info(f"Google GenAI 生成图片: model={model}, aspect_ratio={aspect_ratio}")
        logger.debug(f"  prompt 长度: {len(prompt)} 字符, 有参考图: {reference_image is not None}")

        # 构建 parts 列表
        parts = []

        # 如果有参考图，先添加参考图和说明
        if reference_image:
            logger.debug(f"  添加参考图片 ({len(reference_image)} bytes)")
            # 压缩参考图到 200KB 以内
            compressed_ref = compress_image(reference_image, max_size_kb=200)
            logger.debug(f"  参考图压缩后: {len(compressed_ref)} bytes")
            # 添加参考图
            parts.append(types.Part(
                inline_data=types.Blob(
                    mime_type="image/png",
                    data=compressed_ref
                )
            ))
            # 添加带参考说明的提示词
            enhanced_prompt = f"""请参考上面这张图片的视觉风格（包括配色、排版风格、字体风格、装饰元素风格），生成一张风格一致的新图片。

新图片的内容要求：
{prompt}

重要：
1. 必须保持与参考图相同的视觉风格和设计语言
2. 配色方案要与参考图协调一致
3. 排版和装饰元素的风格要统一
4. 但内容要按照新的要求来生成"""
            parts.append(types.Part(text=enhanced_prompt))
        else:
            # 没有参考图，直接使用原始提示词
            parts.append(types.Part(text=prompt))

        contents = [
            types.Content(
                role="user",
                parts=parts
            )
        ]

        image_config_kwargs = {
            "aspect_ratio": aspect_ratio,
        }

        # 只有在 Vertex AI 模式下才支持 output_mime_type
        if self.is_vertexai:
            image_config_kwargs["output_mime_type"] = "image/png"

        generate_content_config = types.GenerateContentConfig(
            temperature=temperature,
            top_p=0.95,
            max_output_tokens=32768,
            response_modalities=["TEXT", "IMAGE"],
            safety_settings=self.safety_settings,
            image_config=types.ImageConfig(**image_config_kwargs),
        )

        image_data = None
        logger.debug(f"  开始调用 API: model={model}")
        for chunk in self.client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=generate_content_config,
        ):
            if chunk.candidates and chunk.candidates[0].content and chunk.candidates[0].content.parts:
                for part in chunk.candidates[0].content.parts:
                    # 检查是否有图片数据
                    if hasattr(part, 'inline_data') and part.inline_data:
                        image_data = part.inline_data.data
                        logger.debug(f"  收到图片数据: {len(image_data)} bytes")
                        break

        if not image_data:
            logger.error("API 返回为空，未生成图片")
            raise ValueError(
                "❌ Tạo hình ảnh thất bại: API trả về rỗng (API 返回为空)\n\n"
                "【Nguyên nhân có thể】\n"
                "1. Prompt vi phạm bộ lọc chính sách an toàn (thường gặp nhất)\n"
                "2. Model không hỗ trợ yêu cầu tạo hình ảnh hiện tại\n"
                "3. Mất mát dữ liệu trong quá trình truyền tải 网络\n\n"
                "【Giải pháp】\n"
                "1. Chỉnh sửa prompt, tránh nội dung nhạy cảm:\n"
                "   - Tránh nội dung bạo lực, phản cảm, đồi trụy\n"
                "   - Tránh nhắc đến nhân vật đời thực (ngôi sao, chính trị gia...)\n"
                "   - Sử dụng mô tả khách quan, tích cực hơn\n"
                "2. Thử rút gọn và đơn giản hóa prompt\n"
                "3. Kiểm tra kết nối 网络 rồi thử lại"
            )

        logger.info(f"✅ Google GenAI 图片生成成功: {len(image_data)} bytes")
        return image_data

    def get_supported_aspect_ratios(self) -> list:
        """获取支持的宽高比"""
        return ["1:1", "3:4", "4:3", "16:9", "9:16"]
