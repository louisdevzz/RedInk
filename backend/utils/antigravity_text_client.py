"""
Antigravity 文本客户端适配层

对外提供与 TextChatClient / GenAIClient 相同的 generate_text 签名，
因此 OutlineService / ContentService 无需改动调用方式。
"""
import logging
import threading

from ..utils.antigravity_client import AntigravityClient

logger = logging.getLogger(__name__)

_clients = {}
_clients_lock = threading.Lock()


class AntigravityTextClient:
    """通过 Google 账号登录（Antigravity OAuth）调用文本生成，不需要 API Key。"""

    def __init__(self, provider_config: dict = None):
        config = provider_config or {}
        self.base_url = config.get('base_url')
        self.client = AntigravityClient(base_url=self.base_url)
        logger.info(
            "使用 Antigravity (Google 账号登录) 文本客户端"
            + (f": {config.get('email')}" if config.get('email') else "")
        )

    def generate_text(
        self,
        prompt: str,
        model: str = 'gemini-3.1-pro-low',
        temperature: float = 1.0,
        max_output_tokens: int = 8000,
        images=None,
        system_prompt: str = None,
        **kwargs,
    ) -> str:
        return self.client.generate_text(
            prompt=prompt,
            model=model,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            images=images,
            system_prompt=system_prompt,
        )


def get_antigravity_text_client(provider_config: dict = None) -> AntigravityTextClient:
    """按 base_url 复用客户端实例（登录/刷新时会被重置）。"""
    base_url = (provider_config or {}).get('base_url')
    with _clients_lock:
        client = _clients.get(base_url)
        if client is None:
            client = AntigravityTextClient(provider_config or {})
            _clients[base_url] = client
        return client


def reset_antigravity_text_clients():
    """凭据变化（登录/退出/刷新失败）后丢弃缓存的客户端。"""
    with _clients_lock:
        _clients.clear()
