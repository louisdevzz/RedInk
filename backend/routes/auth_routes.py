"""
Google Antigravity（Cloud Code Assist）OAuth 登录相关 API

- POST /auth/antigravity/login           启动浏览器登录（多账号：再登录一次即加入池）
- GET  /auth/antigravity/status          查询登录流程状态 + 凭据状态 + 账号池
- GET  /auth/antigravity/accounts        列出已登录的 Google 账号（不含 token）
- POST /auth/antigravity/account/logout  退出单个账号
- POST /auth/antigravity/account/unblock 解除单个账号的配额冷却
- POST /auth/antigravity/logout          退出全部账号（删除本地凭据）
- GET  /auth/antigravity/models          拉取该账号可用的文本/图片模型
- GET  /auth/antigravity/quota           读取当前账号的配额窗口
"""
import logging

from flask import Blueprint, jsonify, request

from .utils import api_error_response, validation_error

logger = logging.getLogger(__name__)


def create_auth_blueprint():
    auth_bp = Blueprint('auth', __name__)

    @auth_bp.route('/auth/antigravity/login', methods=['POST'])
    def start_login():
        """
        启动 Google 登录流程。

        返回：
        - success / state / auth_url / redirect_uri / message
        """
        try:
            from backend.auth.antigravity_oauth import get_login_manager

            status = get_login_manager().start()
            return jsonify({
                'success': True,
                'state': status.get('state'),
                'auth_url': status.get('auth_url'),
                'redirect_uri': status.get('redirect_uri'),
                'message': status.get('message') or '请在浏览器中完成 Google 登录',
            })
        except Exception as e:
            return api_error_response(e, context={'endpoint': '/api/auth/antigravity/login'})

    @auth_bp.route('/auth/antigravity/status', methods=['GET'])
    def login_status():
        """
        查询登录流程与凭据状态。

        返回：
        - flow: waiting / provisioning / connected / failed / cancelled / idle
        - credential: 已保存凭据的状态（email、project_id、过期时间、账号池视图）
        """
        try:
            from backend.auth.antigravity_credentials import connection_summary
            from backend.auth.antigravity_oauth import get_login_manager

            flow = get_login_manager().status()
            credential = connection_summary()
            return jsonify({
                'success': True,
                'flow': flow.get('state'),
                'message': flow.get('message'),
                'auth_url': flow.get('auth_url'),
                'error': flow.get('error'),
                'credential': credential,
                'connected': bool(credential.get('connected')),
            })
        except Exception as e:
            return api_error_response(e, context={'endpoint': '/api/auth/antigravity/status'})

    @auth_bp.route('/auth/antigravity/cancel', methods=['POST'])
    def cancel_login():
        try:
            from backend.auth.antigravity_oauth import get_login_manager

            get_login_manager().cancel()
            return jsonify({'success': True, 'message': '登录已取消'})
        except Exception as e:
            return api_error_response(e, context={'endpoint': '/api/auth/antigravity/cancel'})

    @auth_bp.route('/auth/antigravity/accounts', methods=['GET'])
    def list_accounts():
        """列出账号池里的全部 Google 账号（不含 token）。"""
        try:
            from backend.auth.antigravity_credentials import account_list

            return jsonify({'success': True, 'accounts': account_list()})
        except Exception as e:
            return api_error_response(e, context={'endpoint': '/api/auth/antigravity/accounts'})

    @auth_bp.route('/auth/antigravity/account/logout', methods=['POST'])
    def logout_account():
        """退出单个 Google 账号（其余账号继续服务）。"""
        try:
            from backend.auth.antigravity_credentials import clear_account

            payload = request.get_json(silent=True) or {}
            account_id = (payload.get('account_id') or '').strip()
            if not account_id:
                return api_error_response(validation_error(
                    "Thiếu tham số account_id", "Vui lòng chỉ định tài khoản cần đăng xuất."
                ))

            removed = clear_account(account_id)
            if not removed:
                return api_error_response(validation_error(
                    f"Không tìm thấy tài khoản: {account_id}", "Tài khoản có thể đã bị xóa."
                ))

            logger.info(f"已退出单个 Google 账号（Antigravity）: {account_id}")
            return jsonify({'success': True, 'message': 'Đã đăng xuất tài khoản này'})
        except Exception as e:
            return api_error_response(e, context={'endpoint': '/api/auth/antigravity/account/logout'})

    @auth_bp.route('/auth/antigravity/account/unblock', methods=['POST'])
    def unblock_account():
        """解除某账号的配额冷却（用户确认已恢复后手动切回）。"""
        try:
            from backend.auth.antigravity_credentials import unblock_account as _unblock

            payload = request.get_json(silent=True) or {}
            account_id = (payload.get('account_id') or '').strip()
            if not account_id:
                return api_error_response(validation_error(
                    "Thiếu tham số account_id", "Vui lòng chỉ định tài khoản cần khôi phục."
                ))

            if not _unblock(account_id):
                return api_error_response(validation_error(
                    f"Không tìm thấy tài khoản: {account_id}", "Tài khoản có thể đã bị xóa."
                ))

            return jsonify({'success': True, 'message': 'Đã khôi phục tài khoản'})
        except Exception as e:
            return api_error_response(e, context={'endpoint': '/api/auth/antigravity/account/unblock'})

    @auth_bp.route('/auth/antigravity/logout', methods=['POST'])
    def logout():
        try:
            from backend.auth.antigravity_credentials import clear_credentials

            clear_credentials()
            logger.info("已退出 Google 账号登录（Antigravity）")
            return jsonify({'success': True, 'message': '已退出登录'})
        except Exception as e:
            return api_error_response(e, context={'endpoint': '/api/auth/antigravity/logout'})

    @auth_bp.route('/auth/antigravity/models', methods=['GET'])
    def available_models():
        """列出该 Google 账号实际可用的文本 / 图片模型（fetchAvailableModels）。"""
        try:
            from backend.auth.antigravity_credentials import connection_summary
            from backend.utils.antigravity_client import AntigravityClient

            if not connection_summary().get('connected'):
                return api_error_response(
                    validation_error("尚未登录 Google 账号", "请先在系统设置页面完成 Google 登录。")
                )

            roster = AntigravityClient().fetch_available_models()
            return jsonify({
                'success': True,
                'text_models': roster.get('text_models', []),
                'image_models': roster.get('image_model_ids', []),
            })
        except Exception as e:
            return api_error_response(e, context={'endpoint': '/api/auth/antigravity/models'})

    @auth_bp.route('/auth/antigravity/quota', methods=['GET'])
    def quota():
        """读取账号配额窗口（groups[].buckets[]）。"""
        try:
            from backend.utils.antigravity_client import AntigravityClient

            return jsonify({'success': True, 'quota': AntigravityClient().quota_summary()})
        except Exception as e:
            return api_error_response(e, context={'endpoint': '/api/auth/antigravity/quota'})

    return auth_bp


__all__ = ['create_auth_blueprint']
