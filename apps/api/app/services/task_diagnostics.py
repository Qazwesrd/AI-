import re


def classify_task_error(error: str | None):
    """Best-effort local classification only; never treats text matching as proof of billing state."""
    value = (error or "").lower()
    rules = [
        (
            "quota_or_billing",
            ("insufficient_quota", "insufficient balance", "quota exceeded", "out of credits", "余额不足", "额度不足", "欠费", "充值"),
            "检查服务商账户额度、账单和计费状态；不要自动重试或充值。",
        ),
        (
            "authentication",
            ("401", "403", "unauthorized", "invalid api key", "authentication", "permission denied", "鉴权失败", "密钥无效", "权限不足"),
            "检查 API Key、账户权限和服务商授权；不要把密钥粘贴到任务备注中。",
        ),
        (
            "rate_limit",
            ("429", "rate limit", "too many requests", "请求过于频繁", "速率限制"),
            "检查服务商速率限制；确认上一次请求状态后，再由用户手动重试。",
        ),
        (
            "timeout",
            ("timeout", "timed out", "deadline exceeded", "超时"),
            "先检查服务商任务记录。若属于提交阶段且没有远程任务编号，必须核实是否已接单后再提交。",
        ),
        (
            "network",
            ("connection error", "connection reset", "connection refused", "dns", "ssl", "network is unreachable", "网络连接", "连接中断"),
            "检查网络、DNS 和服务商可用性；提交结果不确定时不要直接重复提交。",
        ),
        (
            "invalid_request",
            ("400", "422", "invalid parameter", "validation error", "bad request", "参数无效", "请求格式"),
            "检查模型名称、请求参数和素材格式；修正参数后再手动执行。",
        ),
    ]
    for category, needles, guidance in rules:
        if any(needle in value for needle in needles):
            return {"category": category, "guidance": guidance, "confidence": "heuristic"}
    if value:
        return {
            "category": "unknown",
            "guidance": "无法可靠判断失败原因。查看完整错误和服务商控制台；不要自动重试或重复付费提交。",
            "confidence": "low",
        }
    return {"category": None, "guidance": "", "confidence": "none"}
