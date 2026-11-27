"""AI-assisted novel distribution preparation for domestic and overseas author platforms.

This module prepares/export manuscripts and tracks the author's manual submission.
It deliberately does not log into publisher websites or claim that a package was published.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.novel import Novel, Chapter, NovelDistributionPackage, NovelSubmissionRecord

router = APIRouter(prefix="/api/novel-publishing", tags=["AI小说创作与国内外发行"])

PLATFORMS = [
    {"id":"fanqie","name":"番茄小说","region":"国内","language":["zh-CN"],"portal":"https://fanqienovel.com/","mode":"official_manual","api_publish":False,"revenue_sync":False,"payout":"平台作者端结算与提现；软件不代操作"},
    {"id":"qimao","name":"七猫中文网","region":"国内","language":["zh-CN"],"portal":"https://zuozhe.qimao.com/front/register-login/login?query=hasNavBar%3Dtrue&redirect=%2Ffront%2Findex","mode":"official_manual","api_publish":False,"revenue_sync":False,"payout":"平台作者端结算与提现；软件不代操作"},
    {"id":"jjwxc","name":"晋江文学城","region":"国内","language":["zh-CN"],"portal":"https://www.jjwxc.net/","mode":"official_manual","api_publish":False,"revenue_sync":False,"payout":"作者账务与提现以晋江作者后台为准"},
    {"id":"qidian","name":"起点中文网","region":"国内","language":["zh-CN"],"portal":"https://www.qidian.com/","mode":"official_manual","api_publish":False,"revenue_sync":False,"payout":"作者后台结算与提现以平台规则为准"},
    {"id":"kdp","name":"Amazon Kindle Direct Publishing","region":"海外","language":["en","zh-CN","ja","es","fr","de","it","pt"],"portal":"https://kdp.amazon.com/","mode":"official_manual","api_publish":False,"revenue_sync":False,"payout":"KDP 官方账户管理税务与收款；本软件不发起付款"},
    {"id":"royalroad","name":"Royal Road","region":"海外","language":["en"],"portal":"https://www.royalroad.com/author-dashboard/submissions","mode":"official_manual","api_publish":False,"revenue_sync":False,"payout":"平台规则及作者收款项目以官方账户为准"},
    {"id":"inkitt","name":"Inkitt","region":"海外","language":["en","de","fr"],"portal":"https://www.inkitt.com/writers","mode":"official_manual","api_publish":False,"revenue_sync":False,"payout":"按平台作者计划/合同结算；不保证每部作品获得付费资格"},
]


class DistributionPackageInput(BaseModel):
    platform_id: str = Field(min_length=1, max_length=60)
    pen_name: str = Field(default="", max_length=120)
    language: str = Field(default="zh-CN", max_length=40)
    synopsis: str = Field(default="", max_length=10000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    rights_confirmed: bool
    ai_assistance_disclosed: bool


class SubmissionInput(BaseModel):
    status: str = "prepared"
    external_work_url: str = Field(default="", max_length=2000)
    external_work_id: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=5000)


class SubmissionUpdate(BaseModel):
    status: str
    external_work_url: str = Field(default="", max_length=2000)
    external_work_id: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=5000)


class AIAssistPromptInput(BaseModel):
    task: str = Field(min_length=1, max_length=40)
    chapter_id: Optional[int] = None
    target_language: str = Field(default="zh-CN", max_length=40)
    platform_id: str = Field(default="", max_length=60)
    extra_requirements: str = Field(default="", max_length=5000)


SUBMISSION_STATUSES = {"prepared","submitted","reviewing","published","rejected","paused","withdrawn"}


def package_dict(row):
    return {
        "id":row.id,"novel_id":row.novel_id,"platform_id":row.platform_id,
        "platform_name":row.platform_name,"pen_name":row.pen_name,"language":row.language,
        "synopsis":row.synopsis,"tags":row.tags or [],"content_hash":row.content_hash,
        "rights_confirmed":bool(row.rights_confirmed),
        "ai_assistance_disclosed":bool(row.ai_assistance_disclosed),
        "status":row.status,"created_at":row.created_at.isoformat() if row.created_at else None,
        "updated_at":row.updated_at.isoformat() if row.updated_at else None,
        "word_count":len(re.sub(r"\\s+","",row.manuscript_markdown)),
    }


def submission_dict(row):
    return {
        "id":row.id,"package_id":row.package_id,"status":row.status,
        "external_work_url":row.external_work_url,"external_work_id":row.external_work_id,
        "submitted_at":row.submitted_at.isoformat() if row.submitted_at else None,
        "last_checked_at":row.last_checked_at.isoformat() if row.last_checked_at else None,
        "note":row.note,"created_at":row.created_at.isoformat() if row.created_at else None,
    }


def build_manuscript(novel, chapters, synopsis, pen_name, language, tags):
    lines = [f"# {novel.title}", ""]
    if pen_name:
        lines.extend([f"**作者笔名：** {pen_name}", ""])
    lines.extend([f"**类型：** {novel.genre or '未分类'}", f"**语言：** {language}", ""])
    if synopsis:
        lines.extend(["## 作品简介", "", synopsis.strip(), ""])
    if tags:
        lines.extend(["**标签：** " + "、".join(tags), ""])
    lines.extend(["---", "", "## 正文", ""])
    for chapter in chapters:
        lines.extend([f"## {chapter.title or ('第'+str(chapter.chapter_number)+'章')}", "", (chapter.content or "").strip(), ""])
    return "\n".join(lines).strip() + "\n"


@router.get("/platforms")
def list_novel_distribution_platforms():
    return {
        "items":PLATFORMS,
        "notice":"平台目录仅提供官方作者入口和本地稿件准备能力；当前未确认这些平台向本软件开放个人作者作品发布 API 或收益 API。实际发布、审核、合同、结算和提现均在各平台官方账户完成。",
    }


@router.get("/novels")
def list_distribution_novels(db: Session = Depends(get_db)):
    rows = db.query(Novel).order_by(Novel.updated_at.desc()).all()
    return {"items":[{"id":row.id,"title":row.title,"genre":row.genre,"chapter_count":len(row.chapters),"word_count":sum(ch.word_count for ch in row.chapters)} for row in rows]}


@router.post("/novels/{novel_id}/packages")
def create_distribution_package(novel_id: int, data: DistributionPackageInput, db: Session = Depends(get_db)):
    novel = db.query(Novel).filter(Novel.id == novel_id).first()
    if not novel:
        raise HTTPException(404,"小说不存在")
    platform = next((item for item in PLATFORMS if item["id"] == data.platform_id),None)
    if not platform:
        raise HTTPException(400,"不支持的发行平台")
    if not data.rights_confirmed:
        raise HTTPException(400,"请先确认你拥有作品著作权或已取得完整授权")
    if not data.ai_assistance_disclosed:
        raise HTTPException(400,"请确认已按目标平台规则处理 AI 辅助创作披露要求")
    chapters = db.query(Chapter).filter(Chapter.novel_id == novel_id).order_by(Chapter.chapter_number).all()
    if not chapters and not novel.source_text.strip():
        raise HTTPException(400,"小说没有正文，请先创建或导入章节")
    if not chapters:
        chapters = [type("SourceChapter",(),{"title":"正文","chapter_number":1,"content":novel.source_text,"word_count":len(novel.source_text)})()]
    manuscript = build_manuscript(novel,chapters,data.synopsis or novel.description,data.pen_name,data.language,[tag.strip() for tag in data.tags if tag.strip()][:30])
    digest = hashlib.sha256(manuscript.encode("utf-8")).hexdigest()
    row = NovelDistributionPackage(
        novel_id=novel_id,platform_id=platform["id"],platform_name=platform["name"],
        pen_name=data.pen_name.strip(),language=data.language,synopsis=data.synopsis or novel.description,
        tags=[tag.strip() for tag in data.tags if tag.strip()][:30],manuscript_markdown=manuscript,
        content_hash=digest,rights_confirmed=True,ai_assistance_disclosed=True,status="prepared",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success":True,"item":package_dict(row),"message":"已生成平台适配稿件包。它尚未上传或发布；请下载后通过平台官方作者入口提交并记录审核状态。"}


@router.get("/packages")
def list_distribution_packages(novel_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(NovelDistributionPackage)
    if novel_id is not None:
        query = query.filter(NovelDistributionPackage.novel_id == novel_id)
    rows = query.order_by(NovelDistributionPackage.created_at.desc()).limit(500).all()
    return {"items":[package_dict(row) for row in rows]}


@router.get("/packages/{package_id}/export")
def export_distribution_package(package_id: int, format: str = Query(default="md", pattern="^(md|txt|json)$"), db: Session = Depends(get_db)):
    row = db.query(NovelDistributionPackage).filter(NovelDistributionPackage.id == package_id).first()
    if not row:
        raise HTTPException(404,"发行稿件包不存在")
    filename = f"novel-{row.novel_id}-{row.platform_id}-{row.id}.{format}"
    if format == "json":
        body = json.dumps({
            "package":package_dict(row),
            "manuscript_markdown":row.manuscript_markdown,
        },ensure_ascii=False,indent=2)
        media = "application/json; charset=utf-8"
    elif format == "txt":
        body = re.sub(r"^#{1,6}\\s*","",row.manuscript_markdown,flags=re.MULTILINE)
        body = body.replace("---","-"*40)
        media = "text/plain; charset=utf-8"
    else:
        body = row.manuscript_markdown
        media = "text/markdown; charset=utf-8"
    return Response(content=body,media_type=media,headers={"Content-Disposition":f'attachment; filename="{filename}"'})


@router.post("/packages/{package_id}/submissions")
def create_submission_record(package_id: int, data: SubmissionInput, db: Session = Depends(get_db)):
    package = db.query(NovelDistributionPackage).filter(NovelDistributionPackage.id == package_id).first()
    if not package:
        raise HTTPException(404,"发行稿件包不存在")
    if data.status not in SUBMISSION_STATUSES:
        raise HTTPException(400,"无效的投稿状态")
    now = datetime.utcnow()
    submitted_at = now if data.status in {"submitted","reviewing","published"} else None
    row = NovelSubmissionRecord(
        package_id=package_id,status=data.status,external_work_url=data.external_work_url.strip(),
        external_work_id=data.external_work_id.strip(),submitted_at=submitted_at,
        last_checked_at=now,note=data.note.strip(),
    )
    db.add(row)
    package.status = "submitted" if data.status in {"submitted","reviewing","published"} else "prepared"
    db.commit()
    db.refresh(row)
    return {"success":True,"item":submission_dict(row),"message":"投稿记录已保存；状态由你根据平台官方页面实际结果更新。"}


@router.get("/submissions")
def list_submission_records(package_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(NovelSubmissionRecord)
    if package_id is not None:
        query = query.filter(NovelSubmissionRecord.package_id == package_id)
    rows = query.order_by(NovelSubmissionRecord.created_at.desc()).limit(500).all()
    return {"items":[submission_dict(row) for row in rows]}




@router.post("/novels/{novel_id}/ai-assist-prompt")
def build_ai_assist_prompt(novel_id: int, data: AIAssistPromptInput, db: Session = Depends(get_db)):
    novel = db.query(Novel).filter(Novel.id == novel_id).first()
    if not novel:
        raise HTTPException(404,"小说不存在")
    platform = next((item for item in PLATFORMS if item["id"] == data.platform_id),None) if data.platform_id else None
    chapter = None
    if data.chapter_id is not None:
        chapter = db.query(Chapter).filter(Chapter.id == data.chapter_id, Chapter.novel_id == novel_id).first()
        if not chapter:
            raise HTTPException(404,"章节不存在或不属于该小说")
    tasks = {
        "concept": "请帮助我构思原创小说核心创意、题材定位、读者画像、独特卖点和冲突引擎。先提出3个差异明显的方案，再比较并推荐一个，不得套用现有作品的人物、世界观或具体桥段。",
        "outline": "请为这部原创小说设计长篇大纲：核心主题、世界规则、主线目标、主要反派、人物弧光、阶段目标、卷纲、每卷高潮和结尾钩子。保证因果闭环、伏笔可回收、人物动机可信。",
        "characters": "请设计人物档案和关系网：姓名、身份、目标、恐惧、缺陷、能力边界、外貌、服装、说话习惯、人物弧光、人物关系变化。角色之间必须有差异，不得用空洞标签替代行为动机。",
        "chapter": "请续写下一章。严格承接现有章节事实、人物状态、时间线、伏笔和世界规则；先列出本章目标与冲突，再输出完整章节正文。禁止擅自复活人物、改变设定或提前揭示未到时机的秘密。",
        "polish": "请对提供的章节进行专业网文编辑：检查节奏、开篇吸引力、场景调度、人物动机、对白、逻辑漏洞、重复表达和章节结尾钩子。先给问题清单，再给保留原意的修订稿，不得擅自改变关键剧情。",
        "translate": "请将小说内容翻译为目标语言。保留人物姓名、专有名词、世界观术语、称谓关系和章节编号；先建立术语表，再逐段翻译。译文要符合目标语言读者的自然表达，不得删节、改写剧情或擅自增加内容。",
        "synopsis": "请为目标平台撰写作品标题备选、短简介、长简介、标签、关键词和前三章推荐语。突出独特卖点和核心冲突，避免剧透结局，不得虚构正文没有的设定。",
        "localize": "请对作品进行目标市场本地化建议：读者偏好、文化语境、称谓、计量单位、节奏、标题、简介和标签。只提出可解释的适配建议，不得改变人物身份、核心主题或原作因果关系。",
    }
    if data.task not in tasks:
        raise HTTPException(400,"不支持的 AI 创作任务")
    context = [
        f"小说标题：{novel.title}",
        f"题材：{novel.genre}",
        f"现有简介：{novel.description or '尚未填写'}",
        f"目标语言：{data.target_language}",
        f"目标平台：{platform['name'] if platform else '通用创作'}",
    ]
    if chapter:
        context.extend([
            f"当前章节：第{chapter.chapter_number}章 {chapter.title}",
            "当前章节正文：",
            (chapter.content or "")[:24000],
        ])
    prompt = (
        "你是专业长篇小说策划、编辑与跨语言出版顾问。请严格以用户提供的作品资料为依据，"
        "保持人物、时间线、设定和版权边界一致。对不确定的事实先提问，不要编造既有正文。\n\n"
        + "\n".join(context)
        + "\n\n任务要求：\n" + tasks[data.task]
        + ("\n\n额外要求：\n" + data.extra_requirements.strip() if data.extra_requirements.strip() else "")
        + "\n\n输出时区分正文事实、编辑建议和新增创作内容。"
    )
    return {
        "success":True,"task":data.task,"task_name":data.task,
        "prompt":prompt,"platform":platform["name"] if platform else "",
        "target_language":data.target_language,
        "execution_mode":"copy_to_user_selected_free_or_local_AI",
        "automatic_paid_api_call":False,
        "message":"已生成 AI 创作任务提示词。当前不会自动调用付费 API；可复制到你选择的本地或免费 AI 工具，审阅结果后再保存进小说项目。",
    }



@router.patch("/submissions/{submission_id}")
def update_submission_record(submission_id: int, data: SubmissionUpdate, db: Session = Depends(get_db)):
    row = db.query(NovelSubmissionRecord).filter(NovelSubmissionRecord.id == submission_id).first()
    if not row:
        raise HTTPException(404,"投稿记录不存在")
    if data.status not in SUBMISSION_STATUSES:
        raise HTTPException(400,"无效的投稿状态")
    target_url = data.external_work_url.strip()
    if data.status == "published" and not target_url:
        raise HTTPException(400,"标记为已发布必须填写平台实际作品链接")
    if target_url and not (target_url.startswith("https://") or target_url.startswith("http://")):
        raise HTTPException(400,"作品链接必须是有效 HTTP/HTTPS 地址")
    row.status = data.status
    row.external_work_url = target_url
    row.external_work_id = data.external_work_id.strip()
    row.note = data.note.strip()
    row.last_checked_at = datetime.utcnow()
    if data.status in {"submitted","reviewing","published"} and row.submitted_at is None:
        row.submitted_at = datetime.utcnow()
    package = db.query(NovelDistributionPackage).filter(NovelDistributionPackage.id == row.package_id).first()
    if package:
        package.status = "published" if data.status == "published" else "submitted" if data.status in {"submitted","reviewing"} else "prepared"
    db.commit()
    db.refresh(row)
    return {"success":True,"item":submission_dict(row)}
