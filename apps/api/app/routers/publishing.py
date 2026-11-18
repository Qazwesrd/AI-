import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.publishing import PublishAccount, PublishJob
from app.services.media_service import safe_storage_file

router = APIRouter(prefix="/api/publishing", tags=["多平台视频发布"])

PLATFORMS = {
    "douyin": "抖音",
    "kuaishou": "快手",
    "wechat_channels": "微信视频号",
    "xiaohongshu": "小红书",
    "bilibili": "哔哩哔哩",
    "weibo": "微博",
    "xigua": "西瓜视频",
    "toutiao": "今日头条",
    "baijiahao": "百家号",
    "zhihu": "知乎",
    "tencent_video": "腾讯视频",
    "iqiyi_youku": "爱奇艺/优酷",
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "x": "X",
    "vimeo": "Vimeo",
    "dailymotion": "Dailymotion",
    "linkedin": "LinkedIn",
    "pinterest": "Pinterest",
    "reddit": "Reddit",
}

OVERSEAS_PLATFORMS = {
    "youtube", "tiktok", "instagram", "facebook", "x",
    "vimeo", "dailymotion", "linkedin", "pinterest", "reddit",
}

CREATOR_URLS = {
    "douyin": "https://creator.douyin.com/",
    "kuaishou": "https://cp.kuaishou.com/",
    "wechat_channels": "https://channels.weixin.qq.com/",
    "xiaohongshu": "https://creator.xiaohongshu.com/",
    "bilibili": "https://member.bilibili.com/platform/upload/video/frame",
    "weibo": "https://weibo.com/",
    "xigua": "https://studio.ixigua.com/",
    "toutiao": "https://mp.toutiao.com/",
    "baijiahao": "https://baijiahao.baidu.com/",
    "zhihu": "https://www.zhihu.com/creator",
    "tencent_video": "https://v.qq.com/",
    "iqiyi_youku": "https://mp.iqiyi.com/",
    "youtube": "https://studio.youtube.com/",
    "tiktok": "https://www.tiktok.com/creator-center/",
    "instagram": "https://www.instagram.com/",
    "facebook": "https://business.facebook.com/",
    "x": "https://x.com/compose/post",
    "vimeo": "https://vimeo.com/upload",
    "dailymotion": "https://www.dailymotion.com/partner",
    "linkedin": "https://www.linkedin.com/",
    "pinterest": "https://www.pinterest.com/",
    "reddit": "https://www.reddit.com/submit",
}

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
JOB_STATUSES = {"pending", "scheduled", "ready", "published", "failed"}


class AccountInput(BaseModel):
    platform: str
    account_name: str = Field(min_length=1, max_length=200)


class PublishInput(BaseModel):
    platform: str
    video_path: str
    title: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=10000)
    tags: list[str] = Field(default_factory=list)
    cover_path: Optional[str] = None
    account_id: Optional[int] = None
    scheduled_at: Optional[datetime] = None


def job_dict(job):
    return {
        "id": job.id,
        "platform": job.platform,
        "account_id": job.account_id,
        "title": job.title,
        "description": job.description,
        "tags": job.tags or [],
        "video_path": job.video_path,
        "cover_path": job.cover_path,
        "scheduled_at": job.scheduled_at.isoformat() if job.scheduled_at else None,
        "status": job.status,
        "publish_url": job.publish_url,
        "error": job.error,
        "retry_count": job.retry_count,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def account_dict(row):
    return {
        "id": row.id,
        "platform": row.platform,
        "account_name": row.account_name,
        "authorization_type": row.authorization_type,
        "enabled": bool(row.enabled),
        "authorized": bool(row.access_token_encrypted),
    }


def validate_media_path(raw_path: str, kind: str):
    try:
        path = safe_storage_file(raw_path)
    except FileNotFoundError as exc:
        raise HTTPException(404, "素材文件不存在") from exc
    except (OSError, ValueError) as exc:
        label = "视频" if kind == "video" else "封面"
        raise HTTPException(400, f"{label}必须位于应用素材存储目录内") from exc

    allowed = VIDEO_EXTENSIONS if kind == "video" else IMAGE_EXTENSIONS
    if path.suffix.lower() not in allowed:
        detail = (
            "视频仅支持 MP4、MOV、MKV、WEBM、AVI 格式"
            if kind == "video"
            else "封面仅支持 PNG、JPG、JPEG、WEBP 格式"
        )
        raise HTTPException(400, detail)
    return path


@router.get("/platforms")
def platforms():
    return {
        "items": [
            {
                "id": key,
                "name": name,
                "region": "海外平台" if key in OVERSEAS_PLATFORMS else "国内平台",
                "mode": "manual",
                "automatic_publish_enabled": False,
                "creator_url": CREATOR_URLS[key],
                "authorization_status": "not_connected",
            }
            for key, name in PLATFORMS.items()
        ],
        "total": len(PLATFORMS),
    }


@router.get("/accounts")
def accounts(db: Session = Depends(get_db)):
    rows = db.query(PublishAccount).order_by(
        PublishAccount.created_at.desc()
    ).all()
    return {"items": [account_dict(row) for row in rows]}


@router.post("/accounts")
def add_account(data: AccountInput, db: Session = Depends(get_db)):
    if data.platform not in PLATFORMS:
        raise HTTPException(400, "不支持的平台")

    name = data.account_name.strip()
    if not name:
        raise HTTPException(400, "账号名称不能为空")

    row = PublishAccount(
        platform=data.platform,
        account_name=name,
        authorization_type="manual",
        enabled=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "item": account_dict(row)}


@router.delete("/accounts/{account_id}")
def disable_account(account_id: int, db: Session = Depends(get_db)):
    row = db.query(PublishAccount).filter(
        PublishAccount.id == account_id
    ).first()
    if not row:
        raise HTTPException(404, "账号不存在")
    row.enabled = False
    db.commit()
    return {"success": True, "message": "账号已停用"}


@router.post("/jobs/batch")
def create_jobs(data: list[PublishInput], db: Session = Depends(get_db)):
    if not data:
        raise HTTPException(400, "请选择发布平台")
    if len(data) > len(PLATFORMS):
        raise HTTPException(400, "最多选择22个平台")

    seen = set()
    created = []

    for item in data:
        if item.platform not in PLATFORMS:
            raise HTTPException(400, f"不支持的平台：{item.platform}")
        if item.platform in seen:
            raise HTTPException(400, f"重复选择平台：{PLATFORMS[item.platform]}")
        seen.add(item.platform)

        video = validate_media_path(item.video_path, "video")
        cover = validate_media_path(item.cover_path, "cover") if item.cover_path else None

        account = None
        if item.account_id is not None:
            account = db.query(PublishAccount).filter(
                PublishAccount.id == item.account_id,
                PublishAccount.platform == item.platform,
                PublishAccount.enabled.is_(True),
            ).first()
            if not account:
                raise HTTPException(400, f"{PLATFORMS[item.platform]}账号不存在或已停用")

        scheduled_at = item.scheduled_at
        if scheduled_at and scheduled_at.tzinfo is not None:
            scheduled_at = scheduled_at.astimezone(timezone.utc).replace(tzinfo=None)
        if scheduled_at and scheduled_at <= datetime.utcnow():
            raise HTTPException(400, "计划发布时间必须晚于当前时间")

        job = PublishJob(
            platform=item.platform,
            account_id=account.id if account else None,
            video_path=str(video),
            title=item.title.strip(),
            description=item.description.strip(),
            tags=[tag.strip() for tag in item.tags if tag.strip()][:50],
            cover_path=str(cover) if cover else "",
            scheduled_at=scheduled_at,
            status="scheduled" if scheduled_at else "pending",
            idempotency_key=uuid.uuid4().hex,
        )
        db.add(job)
        db.flush()
        created.append(job)

    db.commit()
    return {
        "success": True,
        "created_count": len(created),
        "items": [job_dict(row) for row in created],
    }


@router.get("/jobs")
def jobs(
    status: Optional[str] = None,
    platform: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = db.query(PublishJob)
    if status:
        if status not in JOB_STATUSES:
            raise HTTPException(400, "无效的任务状态")
        query = query.filter(PublishJob.status == status)
    if platform:
        if platform not in PLATFORMS:
            raise HTTPException(400, "不支持的平台")
        query = query.filter(PublishJob.platform == platform)

    rows = query.order_by(PublishJob.created_at.desc()).limit(500).all()
    return {"items": [job_dict(row) for row in rows], "total": len(rows)}


@router.get("/stats")
def publishing_stats(db: Session = Depends(get_db)):
    rows = db.query(PublishJob).all()
    result = {status: 0 for status in JOB_STATUSES}
    for row in rows:
        if row.status in result:
            result[row.status] += 1
    result["total"] = len(rows)
    result["accounts"] = db.query(PublishAccount).filter(
        PublishAccount.enabled.is_(True)
    ).count()
    return result


@router.post("/jobs/{job_id}/prepare")
def prepare_job(job_id: int, db: Session = Depends(get_db)):
    row = db.query(PublishJob).filter(PublishJob.id == job_id).first()
    if not row:
        raise HTTPException(404, "发布任务不存在")
    if row.status == "published":
        raise HTTPException(409, "任务已经发布")
    if row.status == "scheduled":
        raise HTTPException(409, "定时任务不会自动发布，请到计划时间后手动准备")
    row.status = "ready"
    row.error = ""
    row.updated_at = datetime.utcnow()
    db.commit()
    return {
        "success": True,
        "message": "素材已准备，请在平台页面手动完成实际发布",
        "creator_url": CREATOR_URLS[row.platform],
        "automatic_publish": False,
        "job": job_dict(row),
    }


@router.post("/jobs/{job_id}/complete")
def complete_job(
    job_id: int,
    publish_url: str = "",
    db: Session = Depends(get_db),
):
    row = db.query(PublishJob).filter(PublishJob.id == job_id).first()
    if not row:
        raise HTTPException(404, "任务不存在")
    if row.status == "published":
        raise HTTPException(409, "任务已经完成")

    target = publish_url.strip()
    parsed = urlparse(target)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        raise HTTPException(400, "请填写有效的已发布视频链接")

    row.status = "published"
    row.publish_url = target
    row.error = ""
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"success": True, "job": job_dict(row)}


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, db: Session = Depends(get_db)):
    row = db.query(PublishJob).filter(PublishJob.id == job_id).first()
    if not row:
        raise HTTPException(404, "发布任务不存在")
    if row.status == "published":
        raise HTTPException(409, "已发布任务不能重试")

    row.retry_count += 1
    row.status = "pending"
    row.error = ""
    row.scheduled_at = None
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"success": True, "job": job_dict(row)}
