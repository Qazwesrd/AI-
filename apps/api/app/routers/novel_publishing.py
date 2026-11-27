"""AI-assisted novel distribution preparation for domestic and overseas author platforms.

This module prepares/export manuscripts and tracks the author's manual submission.
It deliberately does not log into publisher websites or claim that a package was published.
"""
from __future__ import annotations

import hashlib
import html
import io
import json
import re
import zipfile
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.novel import Novel, Chapter, NovelDistributionPackage, NovelSubmissionRecord, NovelTranslation
from app.routers.local_ai_runtime import _installed_models, _ollama_request, MODEL_NAME_PATTERN

router = APIRouter(prefix="/api/novel-publishing", tags=["AI小说创作与国内外发行"])

PLATFORMS = [
    {"id":"fanqie","name":"番茄小说","region":"国内","language":["zh-CN"],"portal":"https://fanqienovel.com/","mode":"official_manual","api_publish":False,"revenue_sync":False,"payout":"平台作者端结算与提现；软件不代操作"},
    {"id":"qimao","name":"七猫中文网","region":"国内","language":["zh-CN"],"portal":"https://zuozhe.qimao.com/front/register-login/login?query=hasNavBar%3Dtrue&redirect=%2Ffront%2Findex","mode":"official_manual","api_publish":False,"revenue_sync":False,"payout":"平台作者端结算与提现；软件不代操作"},
    {"id":"jjwxc","name":"晋江文学城","region":"国内","language":["zh-CN"],"portal":"https://www.jjwxc.net/","mode":"official_manual","api_publish":False,"revenue_sync":False,"payout":"作者账务与提现以晋江作者后台为准"},
    {"id":"qidian","name":"起点中文网","region":"国内","language":["zh-CN"],"portal":"https://www.qidian.com/","mode":"official_manual","api_publish":False,"revenue_sync":False,"payout":"作者后台结算与提现以平台规则为准"},
    {"id":"kdp","name":"Amazon Kindle Direct Publishing (KDP)","region":"海外","language":["en","zh-CN","ja","es","fr","de","it","pt"],"portal":"https://kdp.amazon.com/","mode":"official_manual","api_publish":False,"revenue_sync":False,"formats":["EPUB","DOCX（按 KDP 当前要求检查）"],"cover_formats":["JPG","TIFF"],"workflow":"在 KDP Bookshelf 创建图书，填写书名、作者、简介、版权、分类、价格与地区；上传 EPUB 正文和封面，使用 Kindle Previewer/在线预览检查后由作者确认发布。","payout":"KDP 官方账户管理税务与收款并查看版税报告；本软件不发起付款"},
    {"id":"google_play_books","name":"Google Play Books Partner Center","region":"海外","language":["en","zh-CN","ja","es","fr","de","it","pt"],"portal":"https://play.google.com/books/publish/","mode":"official_manual","api_publish":False,"revenue_sync":False,"formats":["EPUB","PDF"],"cover_formats":["JPG","PNG","TIFF","PDF"],"workflow":"在 Partner Center 添加图书并填写书目、作者、ISBN/GGKEY、题材、价格和销售地区；上传 EPUB/PDF 正文与封面，检查预览及价格后由作者确认发布。","payout":"Google Play Books Partner Center 的 Payment Center 管理付款资料；收益以月度 Earnings Report 对账，本软件不发起付款。"},
    {"id":"royalroad","name":"Royal Road","region":"海外","language":["en"],"portal":"https://www.royalroad.com/author-dashboard/submissions","mode":"official_manual","api_publish":False,"revenue_sync":False,"payout":"平台规则及作者收款项目以官方账户为准"},
    {"id":"inkitt","name":"Inkitt","region":"海外","language":["en","de","fr"],"portal":"https://www.inkitt.com/writers","mode":"official_manual","api_publish":False,"revenue_sync":False,"payout":"按平台作者计划/合同结算；不保证每部作品获得付费资格"},
]


class DistributionPackageInput(BaseModel):
    platform_id: str = Field(min_length=1, max_length=60)
    pen_name: str = Field(default="", max_length=120)
    localized_title: str = Field(default="", max_length=300)
    language: str = Field(default="zh-CN", max_length=40)
    synopsis: str = Field(default="", max_length=10000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    rights_confirmed: bool
    ai_assistance_disclosed: bool
    translation_id: Optional[int] = None


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
        "id":row.id,"novel_id":row.novel_id,"translation_id":row.translation_id,"platform_id":row.platform_id,
        "platform_name":row.platform_name,"pen_name":row.pen_name,"language":row.language,
        "synopsis":row.synopsis,"tags":row.tags or [],"content_hash":row.content_hash,
        "rights_confirmed":bool(row.rights_confirmed),
        "ai_assistance_disclosed":bool(row.ai_assistance_disclosed),
        "status":row.status,"created_at":row.created_at.isoformat() if row.created_at else None,
        "updated_at":row.updated_at.isoformat() if row.updated_at else None,
        "word_count":len(re.sub(r"\s+","",row.manuscript_markdown)),
    }


def submission_dict(row):
    return {
        "id":row.id,"package_id":row.package_id,"status":row.status,
        "external_work_url":row.external_work_url,"external_work_id":row.external_work_id,
        "submitted_at":row.submitted_at.isoformat() if row.submitted_at else None,
        "last_checked_at":row.last_checked_at.isoformat() if row.last_checked_at else None,
        "note":row.note,"created_at":row.created_at.isoformat() if row.created_at else None,
    }


def build_manuscript(novel, chapters, synopsis, pen_name, language, tags, localized_title=""):
    publication_title = localized_title.strip() or novel.title
    lines = [f"# {publication_title}", ""]
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
    if data.language not in platform["language"]:
        raise HTTPException(400,"所选平台当前发行目录未列出该语言；请先选择平台支持的语言或改用其他平台。")
    if not data.rights_confirmed:
        raise HTTPException(400,"请先确认你拥有作品著作权或已取得完整授权")
    if not data.ai_assistance_disclosed:
        raise HTTPException(400,"请确认已按目标平台规则处理 AI 辅助创作披露要求")
    chapters = db.query(Chapter).filter(Chapter.novel_id == novel_id).order_by(Chapter.chapter_number).all()
    if data.translation_id is not None:
        translation = db.query(NovelTranslation).filter(
            NovelTranslation.id == data.translation_id,
            NovelTranslation.novel_id == novel_id,
        ).first()
        if not translation:
            raise HTTPException(404,"所选译本不存在或不属于当前小说")
        if translation.status != "completed":
            raise HTTPException(409,"所选译本尚未全部翻译完成")
        if translation.target_language != data.language:
            raise HTTPException(400,"发行语言必须与所选译本语言一致")
        if _novel_source_signature(novel,chapters) != translation.source_signature:
            raise HTTPException(409,"原文在译本生成后发生变化；请先重新翻译，避免发布过期译文")
        chapters = [
            type("TranslatedChapter",(),{
                "title":chapter["title"],"chapter_number":chapter["chapter_number"],
                "content":"\n\n".join(chunk["translation"] for chunk in chapter["chunks"]),
                "word_count":len(re.sub(r"\s+","", "\n".join(chunk["translation"] for chunk in chapter["chunks"]))),
            })()
            for chapter in (translation.chapters_data or [])
        ]
    elif not chapters and not novel.source_text.strip():
        raise HTTPException(400,"小说没有正文，请先创建或导入章节")
    if not chapters and data.translation_id is None:
        chapters = [type("SourceChapter",(),{"title":"正文","chapter_number":1,"content":novel.source_text,"word_count":len(novel.source_text)})()]
    manuscript = build_manuscript(novel,chapters,data.synopsis or novel.description,data.pen_name,data.language,[tag.strip() for tag in data.tags if tag.strip()][:30],data.localized_title)
    digest = hashlib.sha256(manuscript.encode("utf-8")).hexdigest()
    row = NovelDistributionPackage(
        novel_id=novel_id,translation_id=data.translation_id,platform_id=platform["id"],platform_name=platform["name"],
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
def _epub_document(markdown_text: str, title: str, language: str) -> bytes:
    """Build a lightweight EPUB 3 file locally; no external conversion service."""
    def inline_markup(value: str) -> str:
        escaped = html.escape(value, quote=False)
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
        escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)
        escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
        return escaped

    rendered = []
    paragraph = []
    def flush_paragraph():
        if paragraph:
            rendered.append("<p>" + " ".join(inline_markup(part) for part in paragraph) + "</p>")
            paragraph.clear()

    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        if not line:
            flush_paragraph()
        elif line == "---":
            flush_paragraph()
            rendered.append("<hr/>")
        elif line.startswith("#"):
            flush_paragraph()
            level = min(6, len(line) - len(line.lstrip("#")))
            heading = line[level:].strip()
            rendered.append(f"<h{level}>{inline_markup(heading)}</h{level}>")
        else:
            paragraph.append(line)
    flush_paragraph()

    safe_title = html.escape(title, quote=True)
    safe_language = html.escape(language or "en", quote=True)
    identifier = "urn:sha256:" + hashlib.sha256(markdown_text.encode("utf-8")).hexdigest()
    xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
        f'xml:lang="{safe_language}" lang="{safe_language}"><head><title>{safe_title}</title>'
        '<meta charset="utf-8"/><style>body{font-family:serif;line-height:1.65;margin:5%;}'
        'h1,h2,h3{line-height:1.3;margin-top:1.6em;}p{text-indent:2em;margin:.7em 0;}'
        'hr{margin:2em 0;border:0;border-top:1px solid #888;}</style></head>'
        '<body><section epub:type="frontmatter"><h1>' + safe_title + '</h1></section>'
        '<section epub:type="bodymatter">' + "".join(rendered) + '</section></body></html>'
    )
    nav = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
        '<head><title>Contents</title></head><body><nav epub:type="toc" id="toc">'
        '<h1>Contents</h1><ol><li><a href="chapter.xhtml">' + safe_title +
        '</a></li></ol></nav></body></html>'
    )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="book-id">{identifier}</dc:identifier><dc:title>{safe_title}</dc:title>'
        f'<dc:language>{safe_language}</dc:language><meta property="dcterms:modified">{datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}</meta>'
        '</metadata><manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        '</manifest><spine><itemref idref="chapter"/></spine></package>'
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml",
            '<?xml version="1.0" encoding="utf-8"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            '</rootfiles></container>', compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/chapter.xhtml", xhtml, compress_type=zipfile.ZIP_DEFLATED)
    return output.getvalue()


@router.get("/packages/{package_id}/export")
def export_distribution_package(package_id: int, format: str = Query(default="md", pattern="^(md|txt|json|epub)$"), db: Session = Depends(get_db)):
    row = db.query(NovelDistributionPackage).filter(NovelDistributionPackage.id == package_id).first()
    if not row:
        raise HTTPException(404,"发行稿件包不存在")
    filename = f"novel-{row.novel_id}-{row.platform_id}-{row.id}.{format}"
    if format == "epub":
        body = _epub_document(row.manuscript_markdown, row.platform_name or "Novel", row.language)
        media = "application/epub+zip"
    elif format == "json":
        body = json.dumps({
            "package":package_dict(row),
            "manuscript_markdown":row.manuscript_markdown,
        },ensure_ascii=False,indent=2)
        media = "application/json; charset=utf-8"
    elif format == "txt":
        body = re.sub(r"^#{1,6}\s*","",row.manuscript_markdown,flags=re.MULTILINE)
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
    if data.status == "published" and not data.external_work_url.strip():
        raise HTTPException(400,"标记为已发布必须填写平台实际作品链接")
    if data.external_work_url.strip() and not (data.external_work_url.startswith("https://") or data.external_work_url.startswith("http://")):
        raise HTTPException(400,"作品链接必须是有效 HTTP/HTTPS 地址")
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
    if data.platform_id and not platform:
        raise HTTPException(400,"不支持的目标发行平台")
    if platform and data.target_language not in platform["language"]:
        raise HTTPException(400,"目标平台当前目录未列出该语言，请更换目标语言。")
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


# Local-only chapter translation. Each translation is stored as a separate edition;
# source chapters are never overwritten and no paid/cloud API is called.
TRANSLATION_LANGUAGES = {
    "en":"英语", "ja":"日语", "ko":"韩语", "es":"西班牙语",
    "fr":"法语", "de":"德语", "it":"意大利语", "pt":"葡萄牙语",
    "ru":"俄语", "ar":"阿拉伯语", "th":"泰语", "vi":"越南语",
    "id":"印尼语", "tr":"土耳其语", "zh-CN":"简体中文",
    "zh-TW":"繁体中文",
}


class TranslationCreateInput(BaseModel):
    target_language: str = Field(min_length=2, max_length=40)
    model_name: str = Field(min_length=1, max_length=160)
    glossary: dict = Field(default_factory=dict)

class TranslationChunkEditInput(BaseModel):
    translation: str = Field(max_length=100000)


def _novel_source_signature(novel, chapters):
    source = "\n".join(
        f"{chapter.chapter_number}:{chapter.title}\n{chapter.content}"
        for chapter in chapters
    ) if chapters else (novel.source_text or "")
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _translation_chunks(text, limit=4200):
    paragraphs = [part.strip() for part in (text or "").splitlines() if part.strip()]
    chunks, current = [], ""
    for paragraph in paragraphs:
        while len(paragraph) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        candidate = paragraph if not current else current + "\n\n" + paragraph
        if len(candidate) > limit and current:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [""]


def translation_dict(row):
    return {
        "id":row.id,"novel_id":row.novel_id,"source_signature":row.source_signature,
        "target_language":row.target_language,"target_language_name":TRANSLATION_LANGUAGES.get(row.target_language,row.target_language),
        "model_name":row.model_name,"status":row.status,"total_chunks":row.total_chunks,
        "completed_chunks":row.completed_chunks,"progress_percent":round(row.completed_chunks/max(1,row.total_chunks)*100,1),
        "glossary":row.glossary or {},"last_error":row.last_error or "",
        "created_at":row.created_at.isoformat() if row.created_at else None,
        "updated_at":row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/translation/languages")
def list_translation_languages():
    return {"items":[{"code":code,"name":name} for code,name in TRANSLATION_LANGUAGES.items()],
            "engine":"本机已安装 Ollama 模型","cloud_fallback":False,"paid_api":False}


@router.post("/novels/{novel_id}/translations")
def create_translation(novel_id: int, data: TranslationCreateInput, db: Session = Depends(get_db)):
    novel = db.query(Novel).filter(Novel.id == novel_id).first()
    if not novel:
        raise HTTPException(404,"小说不存在")
    if data.target_language not in TRANSLATION_LANGUAGES:
        raise HTTPException(400,"暂不支持该目标语言")
    model_name = data.model_name.strip()
    if not MODEL_NAME_PATTERN.fullmatch(model_name):
        raise HTTPException(400,"本地模型名称格式无效")
    try:
        installed = {item["name"] for item in _installed_models()}
    except HTTPException as exc:
        raise HTTPException(exc.status_code,exc.detail)
    if model_name not in installed:
        raise HTTPException(404,"所选模型未安装在本机；不会自动下载模型")
    chapters = db.query(Chapter).filter(Chapter.novel_id == novel_id).order_by(Chapter.chapter_number).all()
    source_rows = [(x.chapter_number,x.title,x.content or "") for x in chapters]
    if not source_rows and (novel.source_text or "").strip():
        source_rows = [(1,"正文",novel.source_text)]
    if not source_rows:
        raise HTTPException(400,"小说正文为空，请先上传 TXT、DOCX 或其他支持的文档")
    source_signature = _novel_source_signature(novel,chapters)
    translated_chapters = []
    total_chunks = 0
    for number,title,content in source_rows:
        chunks = _translation_chunks(content)
        translated_chapters.append({
            "chapter_number":number,"title":title,
            "chunks":[{"source":chunk,"translation":"","status":"pending"} for chunk in chunks],
        })
        total_chunks += len(chunks)
    glossary = {}
    for key,value in list(data.glossary.items())[:300]:
        if isinstance(key,str) and isinstance(value,str) and key.strip() and value.strip():
            glossary[key.strip()[:120]] = value.strip()[:160]
    row = NovelTranslation(
        novel_id=novel_id,source_signature=source_signature,target_language=data.target_language,
        model_name=model_name,status="pending",total_chunks=total_chunks,completed_chunks=0,
        chapters_data=translated_chapters,glossary=glossary,last_error="",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success":True,"item":translation_dict(row),
            "message":"已建立独立译本任务。原文不会修改；点击逐段翻译即可使用本机模型处理，不会调用付费 API 或云端模型。"}


@router.get("/translations")
def list_translations(novel_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(NovelTranslation)
    if novel_id is not None:
        query = query.filter(NovelTranslation.novel_id == novel_id)
    rows = query.order_by(NovelTranslation.created_at.desc()).limit(200).all()
    return {"items":[translation_dict(row) for row in rows]}


@router.get("/translations/{translation_id}")
def get_translation(translation_id: int, db: Session = Depends(get_db)):
    row = db.query(NovelTranslation).filter(NovelTranslation.id == translation_id).first()
    if not row:
        raise HTTPException(404,"译本任务不存在")
    result = translation_dict(row)
    result["chapters"] = [
        {"chapter_number":chapter["chapter_number"],"title":chapter["title"],
         "completed_chunks":sum(1 for chunk in chapter["chunks"] if chunk["status"]=="completed"),
         "total_chunks":len(chapter["chunks"]),
         "translated_text":"\n\n".join(chunk["translation"] for chunk in chapter["chunks"] if chunk["translation"]),
         "chunks":[{"index":index,"source":chunk["source"],"translation":chunk["translation"],"status":chunk["status"]} for index,chunk in enumerate(chapter["chunks"])],
         "status":"completed" if all(chunk["status"]=="completed" for chunk in chapter["chunks"]) else "pending"}
        for chapter in (row.chapters_data or [])
    ]
    return result


@router.post("/translations/{translation_id}/translate-next")
def translate_next_chunk(translation_id: int, db: Session = Depends(get_db)):
    row = db.query(NovelTranslation).filter(NovelTranslation.id == translation_id).first()
    if not row:
        raise HTTPException(404,"译本任务不存在")
    if row.status == "completed":
        return {"success":True,"item":translation_dict(row),"message":"该译本已经全部完成。"}
    novel = db.query(Novel).filter(Novel.id == row.novel_id).first()
    if not novel:
        raise HTTPException(404,"原小说不存在")
    source_chapters = db.query(Chapter).filter(Chapter.novel_id == row.novel_id).order_by(Chapter.chapter_number).all()
    if _novel_source_signature(novel,source_chapters) != row.source_signature:
        row.status = "source_changed"
        row.last_error = "原文在翻译期间发生变化；为避免错位，已暂停该译本。请基于最新原文新建译本。"
        db.commit()
        raise HTTPException(409,row.last_error)
    chapter_data = row.chapters_data or []
    target = None
    for chapter in chapter_data:
        for chunk_index,chunk in enumerate(chapter["chunks"]):
            if chunk["status"] != "completed":
                target = (chapter,chunk_index,chunk)
                break
        if target:
            break
    if not target:
        row.status = "completed"
        db.commit()
        return {"success":True,"item":translation_dict(row),"message":"译本全部完成。"}
    chapter,chunk_index,chunk = target
    language_name = TRANSLATION_LANGUAGES.get(row.target_language,row.target_language)
    glossary_lines = [f"{key} => {value}" for key,value in (row.glossary or {}).items()]
    system = (
        f"你是专业小说译者。将输入完整翻译为{language_name}（语言代码 {row.target_language}）。"
        "不得摘要、删节、续写或解释；保留段落、对话、情绪、人物关系、时态与叙事视角。"
        "专有名词按术语表固定翻译；没有列出的名字保持前后一致。只输出译文，不要输出说明或标题。"
    )
    prompt = "【术语表】\n" + ("\n".join(glossary_lines) if glossary_lines else "无") + "\n\n【原文】\n" + chunk["source"]
    row.status = "running"
    row.last_error = ""
    db.commit()
    try:
        result = _ollama_request(
            "/api/generate",
            {"model":row.model_name,"system":system,"prompt":prompt,"stream":False,
             "options":{"num_predict":4096,"temperature":0.2}},
            timeout=240.0,
        )
        translated = result.get("response") if isinstance(result,dict) else None
        if not isinstance(translated,str) or not translated.strip():
            raise HTTPException(502,"本机模型未返回有效译文")
        chunk["translation"] = translated.strip()
        chunk["status"] = "completed"
        row.chapters_data = chapter_data
        row.completed_chunks += 1
        row.status = "completed" if row.completed_chunks >= row.total_chunks else "pending"
        row.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
        return {"success":True,"item":translation_dict(row),
                "current_chapter":chapter["chapter_number"],"current_chunk":chunk_index+1,
                "translated_text":translated.strip(),
                "message":"本段翻译完成并已保存到独立译本；原文未修改。"}
    except HTTPException as exc:
        row.status = "error"
        row.last_error = str(exc.detail)[:2000]
        db.commit()
        raise
    except Exception as exc:
        row.status = "error"
        row.last_error = str(exc)[:2000]
        db.commit()
        raise HTTPException(502,"本地翻译失败："+row.last_error)


@router.patch("/translations/{translation_id}/chapters/{chapter_number}/chunks/{chunk_index}")
def edit_translation_chunk(
    translation_id: int,
    chapter_number: int,
    chunk_index: int,
    data: TranslationChunkEditInput,
    db: Session = Depends(get_db),
):
    row = db.query(NovelTranslation).filter(NovelTranslation.id == translation_id).first()
    if not row:
        raise HTTPException(404, "译本任务不存在")
    novel = db.query(Novel).filter(Novel.id == row.novel_id).first()
    if not novel:
        raise HTTPException(404, "原小说不存在")
    source_chapters = db.query(Chapter).filter(
        Chapter.novel_id == row.novel_id
    ).order_by(Chapter.chapter_number).all()
    if _novel_source_signature(novel, source_chapters) != row.source_signature:
        raise HTTPException(409, "原文已变化，不能继续修改旧译本；请基于最新原文新建译本。")
    chapter = next(
        (item for item in (row.chapters_data or []) if item.get("chapter_number") == chapter_number),
        None,
    )
    if chapter is None or chunk_index < 0 or chunk_index >= len(chapter.get("chunks", [])):
        raise HTTPException(404, "译文章节或分段不存在")
    chunk = chapter["chunks"][chunk_index]
    chunk["translation"] = data.translation.strip()
    chunk["status"] = "completed" if chunk["translation"] else "pending"
    row.chapters_data = row.chapters_data
    row.completed_chunks = sum(
        1 for item in (row.chapters_data or [])
        for part in item.get("chunks", [])
        if part.get("status") == "completed"
    )
    row.status = "completed" if row.completed_chunks >= row.total_chunks else "pending"
    row.last_error = ""
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return {
        "success": True,
        "item": translation_dict(row),
        "message": "译文校对已保存；原文保持不变。",
    }


@router.get("/translations/{translation_id}/export")
def export_translation(translation_id: int, format: str = Query(default="md",pattern="^(md|txt|json|epub)$"), db: Session = Depends(get_db)):
    row = db.query(NovelTranslation).filter(NovelTranslation.id == translation_id).first()
    if not row:
        raise HTTPException(404,"译本任务不存在")
    novel = db.query(Novel).filter(Novel.id == row.novel_id).first()
    lines = [f"# {novel.title if novel else 'Novel'}",f"\n**Language:** {TRANSLATION_LANGUAGES.get(row.target_language,row.target_language)}",""]
    for chapter in (row.chapters_data or []):
        lines.extend([f"## {chapter['title']}",""])
        lines.extend(["\n\n".join(chunk["translation"] for chunk in chapter["chunks"] if chunk["translation"]),""])
    manuscript = "\n".join(lines).strip()+"\n"
    if format == "epub":
        if row.status != "completed":
            raise HTTPException(409,"译本尚未全部翻译完成，不能导出正式 EPUB。")
        body = _epub_document(manuscript, novel.title if novel else "Novel", row.target_language)
        media = "application/epub+zip"
    elif format == "json":
        body = json.dumps({"translation":translation_dict(row),"manuscript":manuscript},ensure_ascii=False,indent=2)
        media = "application/json; charset=utf-8"
    elif format == "txt":
        body = re.sub(r"^#{1,6}\s*","",manuscript,flags=re.MULTILINE)
        media = "text/plain; charset=utf-8"
    else:
        body = manuscript
        media = "text/markdown; charset=utf-8"
    return Response(content=body,media_type=media,headers={"Content-Disposition":f'attachment; filename="novel-{row.novel_id}-{row.target_language}-translation.{format}"'})
