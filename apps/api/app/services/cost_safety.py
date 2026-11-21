from fastapi import HTTPException

# Hard safety policy: cloud AI API requests are disabled in the zero-cost build.
# Manual browser-based AI handoff and local media processing remain available.
STRICT_ZERO_COST_MODE = True


def require_cloud_ai_api_disabled() -> None:
    """Reject every cloud AI API submission while strict zero-cost mode is enabled."""
    if STRICT_ZERO_COST_MODE:
        raise HTTPException(
            status_code=403,
            detail=(
                "严格零费用模式已启用：云端 AI API 请求已被系统拦截，"
                "不会提交任务或产生 API 调用费用。请使用本地处理功能，"
                "或通过 AI 网页协作中心手动操作；第三方网页是否收费需自行核实。"
            ),
        )
