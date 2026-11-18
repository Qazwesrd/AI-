import uuid
from datetime import datetime
from typing import Optional

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


class AccountInput(BaseModel):
    platform: str
    account_name: str = ""
    authorization_type: str = "manual"


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
    }


@router.get("/platforms")
def platforms():
    return {
        "items": [
            {
                "id": key,
                "name": name,
                "mode": "manual",
                "automatic_publish_enabled": False,
                "creator_url": CREATOR_URLS[key],
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

    return {
        "items": [
            {
                "id": row.id,
                "platform": row.platform,
                "account_name": row.account_name,
                "authorization_type": row.authorization_type,
                "enabled": row.enabled,
                "authorized": bool(row.access_token_encrypted),
            }
            for row in rows
        ]
    }


@router.post("/accounts")
def add_account(data: AccountInput, db: Session = Depends(get_db)):
    if data.platform not in PLATFORMS:
        raise HTTPException(400, "不支持的平台")

    row = PublishAccount(
        platform=data.platform,
        account_name=data.account_name.strip(),
        authorization_type="manual",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return {"success": True, "id": row.id}


@router.post("/jobs/batch")
def create_jobs(data: list[PublishInput], db: Session = Depends(get_db)):
    if not data:
        raise HTTPException(400, "请选择发布平台")

    if len(data) > 22:
        raise HTTPException(400, "最多选择22个平台")

    created = []

    for item in data:
        if item.platform not in PLATFORMS:
            raise HTTPException(400, f"不支持的平台：{item.platform}")

        if not item.video_path:
            raise HTTPException(400, "视频路径不能为空")

        try:
            video = safe_storage_file(item.video_path)
        except FileNotFoundError as exc:
            raise HTTPException(404, "视频文件不存在") from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(400, "视频必须位于应用素材存储目录内") from exc

        cover = None
        if item.cover_path:
            try:
                cover = safe_storage_file(item.cover_path)
            except FileNotFoundError as exc:
                raise HTTPException(404, "封面文件不存在") from exc
            except (OSError, ValueError) as exc:
                raise HTTPException(400, "封面必须位于应用素材存储目录内") from exc
            if cover.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                raise HTTPException(400, "封面仅支持 PNG、JPG、JPEG、WEBP 格式")

        if item.account_id:
            account = db.query(PublishAccount).filter(
                PublishAccount.id == item.account_id,
                PublishAccount.platform == item.platform,
                PublishAccount.enabled.is_(True),
            ).first()

            if not account:
                raise HTTPException(400, "发布账号不存在或已停用")

        job = PublishJob(
            platform=item.platform,
            account_id=item.account_id,
            video_path=str(video),
            title=item.title,
            description=item.description,
            tags=item.tags,
            cover_path=str(cover) if cover else "",
            scheduled_at=item.scheduled_at,
            status="scheduled" if item.scheduled_at else "pending",
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
def jobs(status: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(PublishJob)

    if status:
        query = query.filter(PublishJob.status == status)

    rows = query.order_by(PublishJob.created_at.desc()).limit(500).all()
    return {"items": [job_dict(row) for row in rows], "total": len(rows)}


@router.post("/jobs/{job_id}/prepare")
def prepare_job(job_id: int, db: Session = Depends(get_db)):
    row = db.query(PublishJob).filter(PublishJob.id == job_id).first()

    if not row:
        raise HTTPException(404, "发布任务不存在")

    if row.status == "published":
        raise HTTPException(409, "任务已经发布")

    row.status = "ready"
    row.error = ""
    db.commit()

    return {
        "success": True,
        "message": "请前往官方创作者页面完成发布",
        "creator_url": CREATOR_URLS[row.platform],
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

    row.status = "published"
    row.publish_url = publish_url
    row.error = ""
    db.commit()

    return {"success": True, "job": job_dict(row)}


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, db: Session = Depends(get_db)):
    row = db.query(PublishJob).filter(PublishJob.id == job_id).first()

    if not row:
        raise HTTPException(404, "任务不存在")

    if row.status == "published":
        raise HTTPException(409, "已发布任务不能重试")

    row.retry_count += 1
    row.status = "pending"
    row.error = ""
    db.commit()

    return {"success": True, "job": job_dict(row)}


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
