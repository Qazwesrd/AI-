import re
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database import get_db
from app.document_import import extract_document_text
from app.models.novel import Novel, Chapter, AnalysisItem
from app.models.story import StoryProject

router = APIRouter(prefix="/api", tags=["小说与章节"])

class NovelCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    genre: str = "未分类"

class NovelUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    genre: str | None = None
    status: str | None = None

class ChapterUpdate(BaseModel):
    title: str | None = None
    content: str | None = None

class AnalysisUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    data: dict | None = None

def novel_dict(item):
    return {
        "id": item.id,
        "title": item.title,
        "description": item.description,
        "genre": item.genre,
        "status": item.status,
        "chapter_count": len(item.chapters),
        "word_count": sum(c.word_count for c in item.chapters),
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }

def chapter_dict(item):
    return {
        "id": item.id,
        "novel_id": item.novel_id,
        "chapter_number": item.chapter_number,
        "title": item.title,
        "content": item.content,
        "word_count": item.word_count,
    }

def analysis_dict(item):
    return {
        "id": item.id,
        "novel_id": item.novel_id,
        "category": item.category,
        "name": item.name,
        "description": item.description,
        "data": item.data or {},
        "confirmed": item.confirmed,
    }

def split_chapters(text):
    pattern = re.compile(
        r"(?m)^\s*(第[零〇一二三四五六七八九十百千万两\d]+[章节回卷集].*)$"
        r"|^\s*((?:Chapter|CHAPTER)\s+\d+.*)$"
    )
    matches = list(pattern.finditer(text))
    if not matches:
        content = text.strip()
        return [("正文", content)] if content else []

    result = []
    if text[:matches[0].start()].strip():
        result.append(("序章", text[:matches[0].start()].strip()))

    for index, match in enumerate(matches):
        title = (match.group(1) or match.group(2) or "").strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        result.append((title or f"第{index + 1}章", content))

    return result

@router.get("/health")
def api_health():
    return {"status": "ok", "service": "novel-api"}

@router.get("/novels")
def list_novels(db: Session = Depends(get_db)):
    items = db.query(Novel).order_by(Novel.updated_at.desc()).all()
    return {"items": [novel_dict(x) for x in items], "total": len(items)}

@router.post("/novels")
def create_novel(payload: NovelCreate, db: Session = Depends(get_db)):
    item = Novel(
        title=payload.title.strip(),
        description=payload.description,
        genre=payload.genre,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return novel_dict(item)

@router.get("/novels/{novel_id}")
def get_novel(novel_id: int, db: Session = Depends(get_db)):
    item = db.query(Novel).filter(Novel.id == novel_id).first()
    if not item:
        raise HTTPException(404, "小说不存在")
    result = novel_dict(item)
    result["chapters"] = [chapter_dict(x) for x in item.chapters]
    result["source_text"] = item.source_text
    return result

@router.put("/novels/{novel_id}")
def update_novel(novel_id: int, payload: NovelUpdate, db: Session = Depends(get_db)):
    item = db.query(Novel).filter(Novel.id == novel_id).first()
    if not item:
        raise HTTPException(404, "小说不存在")
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return novel_dict(item)

@router.delete("/novels/{novel_id}")
def delete_novel(novel_id: int, db: Session = Depends(get_db)):
    item = db.query(Novel).filter(Novel.id == novel_id).first()
    if not item:
        raise HTTPException(404, "小说不存在")
    db.delete(item)
    db.commit()
    return {"success": True}

@router.post("/novels/{novel_id}/import-txt")
async def import_txt(
    novel_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    item = db.query(Novel).filter(Novel.id == novel_id).first()
    if not item:
        raise HTTPException(404, "小说不存在")

    filename = file.filename or "novel.txt"
    raw = await file.read()
    try:
        text = extract_document_text(filename, raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    parts = split_chapters(text)
    if not parts:
        raise HTTPException(400, "文件内容为空")

    item.source_text = text
    item.title = item.title or Path(filename).stem

    # Keep old screenplay content, but mark it for review after source replacement.
    affected_projects = db.query(StoryProject).filter(
        StoryProject.novel_id == novel_id
    ).all()
    for project in affected_projects:
        project.status = "source_changed"

    for old in list(item.chapters):
        db.delete(old)

    for number, (title, content) in enumerate(parts, start=1):
        db.add(Chapter(
            novel_id=item.id,
            chapter_number=number,
            title=title[:300],
            content=content,
            word_count=len(re.sub(r"\s+", "", content)),
        ))

    db.commit()
    db.refresh(item)
    return {
        "success": True,
        "filename": filename,
        "chapter_count": len(parts),
        "word_count": len(re.sub(r"\s+", "", text)),
        "outdated_story_projects": len(affected_projects),
        # Avoid returning every chapter body in a single response for large novels.
        "message": "小说正文已导入，章节列表请通过章节接口读取；已有剧本项目已标记为需要复核。",
    }

@router.get("/novels/{novel_id}/chapters")
def list_chapters(novel_id: int, db: Session = Depends(get_db)):
    if not db.query(Novel).filter(Novel.id == novel_id).first():
        raise HTTPException(404, "小说不存在")
    items = db.query(Chapter).filter(
        Chapter.novel_id == novel_id
    ).order_by(Chapter.chapter_number).all()
    # Return directory metadata only. Chapter bodies are fetched on demand so
    # very long novels do not transfer hundreds of full chapters at once.
    return {
        "items": [{
            "id": x.id,
            "novel_id": x.novel_id,
            "chapter_number": x.chapter_number,
            "title": x.title,
            "word_count": x.word_count,
        } for x in items],
        "total": len(items),
    }

@router.get("/chapters/{chapter_id}")
def get_chapter(chapter_id: int, db: Session = Depends(get_db)):
    item = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not item:
        raise HTTPException(404, "章节不存在")
    return chapter_dict(item)

@router.put("/chapters/{chapter_id}")
def update_chapter(
    chapter_id: int,
    payload: ChapterUpdate,
    db: Session = Depends(get_db),
):
    item = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not item:
        raise HTTPException(404, "章节不存在")
    values = payload.model_dump(exclude_unset=True)
    for key, value in values.items():
        if value is not None:
            setattr(item, key, value)
    item.word_count = len(re.sub(r"\s+", "", item.content))
    db.commit()
    db.refresh(item)
    return chapter_dict(item)

@router.post("/novels/{novel_id}/analyze")
def analyze_novel(novel_id: int, db: Session = Depends(get_db)):
    item = db.query(Novel).filter(Novel.id == novel_id).first()
    if not item:
        raise HTTPException(404, "小说不存在")
    if not item.chapters:
        raise HTTPException(400, "请先导入小说或创建章节")

    # 本地规则初步提取：这是基础识别，不冒充外部 AI 模型分析。
    full_text = "\n".join(c.content for c in item.chapters)
    candidates = []

    quoted_names = re.findall(r"[“「『]([^”」』]{2,12})[”」』]", full_text)
    for name in dict.fromkeys(quoted_names):
        candidates.append(("character", name, "从文本引号内容中提取的待核对名称"))

    scene_words = ("大厅", "宫殿", "山谷", "森林", "城池", "村庄", "洞穴", "学校", "医院")
    for word in scene_words:
        if word in full_text:
            candidates.append(("scene", word, "文本中出现的场景线索"))

    prop_words = ("宝剑", "长剑", "法宝", "玉佩", "戒指", "卷轴", "手机", "钥匙")
    for word in prop_words:
        if word in full_text:
            candidates.append(("prop", word, "文本中出现的道具线索"))

    event_words = ("突然", "于是", "最终", "随后", "大战", "相遇", "逃跑", "发现")
    for word in event_words:
        count = full_text.count(word)
        if count:
            candidates.append(("event", word, f"剧情事件线索，出现 {count} 次"))

    for old in db.query(AnalysisItem).filter(
        AnalysisItem.novel_id == novel_id,
        AnalysisItem.confirmed == False
    ).all():
        db.delete(old)

    existing = set()
    created = []
    for category, name, description in candidates:
        key = (category, name)
        if key in existing:
            continue
        existing.add(key)
        row = AnalysisItem(
            novel_id=novel_id,
            category=category,
            name=name,
            description=description,
            data={"source": "local_rule", "needs_review": True},
        )
        db.add(row)
        created.append(row)

    db.commit()
    return {
        "success": True,
        "mode": "local_rule_preliminary",
        "message": "基础文本线索提取完成。接入 AI 模型后可进行深度语义分析。",
        "items": [analysis_dict(x) for x in created],
        "total": len(created),
    }

@router.get("/novels/{novel_id}/analysis")
def list_analysis(novel_id: int, db: Session = Depends(get_db)):
    if not db.query(Novel).filter(Novel.id == novel_id).first():
        raise HTTPException(404, "小说不存在")
    items = db.query(AnalysisItem).filter(
        AnalysisItem.novel_id == novel_id
    ).order_by(AnalysisItem.category, AnalysisItem.id).all()
    return {"items": [analysis_dict(x) for x in items], "total": len(items)}

@router.put("/analysis/{item_id}")
def update_analysis(
    item_id: int,
    payload: AnalysisUpdate,
    db: Session = Depends(get_db),
):
    item = db.query(AnalysisItem).filter(AnalysisItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "解析条目不存在")
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return analysis_dict(item)

@router.delete("/analysis/{item_id}")
def delete_analysis(item_id: int, db: Session = Depends(get_db)):
    item = db.query(AnalysisItem).filter(AnalysisItem.id == item_id).first()
    if not item:
        raise HTTPException(404, "解析条目不存在")
    db.delete(item)
    db.commit()
    return {"success": True}

@router.post("/novels/{novel_id}/analysis/confirm")
def confirm_analysis(novel_id: int, db: Session = Depends(get_db)):
    items = db.query(AnalysisItem).filter(
        AnalysisItem.novel_id == novel_id
    ).all()
    for item in items:
        item.confirmed = True
    db.commit()
    return {"success": True, "confirmed_count": len(items)}

