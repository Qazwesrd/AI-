"use client";

import { useEffect, useMemo, useState } from "react";

type ToolCategory = "text" | "image" | "storyboard" | "video";
type NovelOption = { id: number; title: string; genre?: string; chapter_count?: number };
type ChapterOption = { id: number; novel_id: number; chapter_number: number; title: string; word_count: number };
type Tool = { id: string; name: string; category: ToolCategory; url: string; note: string; custom?: boolean };
type ResultFile = { id: number; filename: string; size: number; downloadUrl: string; classification?: string; classificationStatus?: string; classificationReason?: string; visualAssetId?: number | null };
type Task = {
  id: string;
  novelId?: number;
  chapterId?: number;
  title: string;
  category: ToolCategory;
  toolId: string;
  prompt: string;
  status: "prepared" | "opened" | "completed";
  resultText: string;
  resultUrl: string;
  createdAt: string;
};

const STORAGE_TOOLS = "ai-comic-studio:web-tools:v1";
const STORAGE_TASKS = "ai-comic-studio:web-tool-tasks:v1";

const presets: Tool[] = [
  { id: "chatgpt", name: "ChatGPT", category: "text", url: "https://chatgpt.com/", note: "剧本改编、对白润色、剧情分析" },
  { id: "deepseek", name: "DeepSeek", category: "text", url: "https://chat.deepseek.com/", note: "小说理解、长文本分析、剧本初稿" },
  { id: "gemini", name: "Google Gemini", category: "text", url: "https://gemini.google.com/", note: "文本分析与多模态辅助" },
  { id: "chatgpt-storyboard", name: "ChatGPT（分镜规划）", category: "storyboard", url: "https://chatgpt.com/", note: "镜头拆分、景别、机位、动作和时长规划" },
  { id: "deepseek-storyboard", name: "DeepSeek（分镜规划）", category: "storyboard", url: "https://chat.deepseek.com/", note: "剧本结构化与分镜表生成" },
  { id: "doubao", name: "豆包", category: "image", url: "https://www.doubao.com/", note: "人物、场景、道具图片生成；具体能力以账号页面为准" },
  { id: "jimeng", name: "即梦AI", category: "image", url: "https://jimeng.jianying.com/", note: "图片与创意视觉素材" },
  { id: "midjourney", name: "Midjourney", category: "image", url: "https://www.midjourney.com/", note: "风格化概念图与视觉探索" },
  { id: "kling", name: "可灵AI", category: "video", url: "https://klingai.com/", note: "图生视频、文生视频" },
  { id: "runway", name: "Runway", category: "video", url: "https://runwayml.com/", note: "视频生成与视觉处理" },
];

const categoryLabels: Record<ToolCategory, string> = {
  text: "剧本 / 文本",
  image: "图片 / 视觉素材",
  storyboard: "分镜 / 镜头",
  video: "视频生成",
};

const taskExamples: Record<ToolCategory, string> = {
  text: "请根据以下小说内容改编为适合漫剧的剧本，保留主线剧情、人物性格和关键事件，输出场景、人物、动作、对白及旁白。",
  image: "请根据以下设定生成可用于 AI 漫剧的视觉素材。保持人物身份、服装、发型、年龄感和画风一致；如为人物，请优先输出正面、侧面、背面三视图。",
  storyboard: "请把以下剧本拆解为可执行的分镜表。每个镜头给出镜头编号、景别、机位、画面内容、人物动作、对白/旁白、镜头运动和建议时长。",
  video: "请根据以下分镜描述生成视频片段。保持人物外观、服装、场景和镜头方向连续，画面比例与项目设置一致。",
};

function buildProductionPrompt(input: {
  category: ToolCategory;
  title: string;
  source: string;
  mode: string;
  toolName?: string;
  instruction?: string;
}) {
  const { category, title, source, mode, toolName = "", instruction = "" } = input;
  const style = mode === "concise"
    ? "表达精炼、结构清楚、避免空话；但不得省略制作所需信息。"
    : mode === "cinematic"
      ? "采用电影级视听语言，强化情绪弧线、镜头调度、构图层次、光影质感和节奏。"
      : "采用专业影视动画制作标准，描述具体、可执行、前后一致，方便后续制作与归档。";
  const common = [
    "你是资深 AI 漫剧导演、编剧与视听制作专家。请严格依据输入资料完成任务。",
    "【任务】\n" + title.trim(),
    instruction ? "【本次具体要求】\n" + instruction : "",
    "【原始资料】\n" + (source.trim() || "当前没有提供原文或设定。请先列出需要补充的信息，不要虚构关键设定。"),
    "【创作与一致性规则】\n1. 尊重原著主线、人物性格、人物关系和事件因果，不擅自改写关键事实。\n2. 区分原文事实、合理推断和新增创意；推断或新增内容必须标注。\n3. 人物姓名、年龄感、脸型、发型、服装、配色、身份标识保持统一。\n4. 场景的空间结构、时间、天气、光源和道具位置保持连续。\n5. 不确定的信息列入“待确认”，不得自行补成事实。\n6. 输出使用中文，专有名词和必要的生成参数可保留英文。",
    "【质量要求】\n" + style,
  ];
  let specialist = "";
  if (category === "text") {
    specialist = [
      "【文本/剧本专用要求】",
      "先识别本次任务属于剧情分析、人物分析、剧本改编还是对白润色，并按任务目标输出。",
      "剧情分析需包含事件顺序、时间线、地点、人物动机、冲突、转折、伏笔及因果关系。",
      "剧本改编需按场次输出：场次编号、内外景、时间、地点、出场人物、动作、对白、旁白、情绪和转场。",
      "保留原著核心信息与关键情节；对白自然、有角色差异；避免流水账、重复解释和无意义扩写。",
      "结尾附：新增/删减内容说明、原著一致性检查、待确认事项。",
    ].join("\n");
  } else if (category === "image") {
    const midjourney = /midjourney/i.test(toolName);
    specialist = [
      "【图片生成专用要求】",
      "先判断素材类型：人物、人物三视图、场景、道具、建筑、服装或关键物品；每个主体分别输出独立提示词。",
      "人物提示词必须包含身份气质、年龄感、面部特征、发型、服装层次、材质、配色、姿态、表情、光线和背景；三视图必须是同一角色的正面/侧面/背面，比例和服装细节严格一致。",
      "场景提示词必须包含空间布局、前中后景、建筑结构、时间天气、光源方向、氛围和可用于构图的视觉焦点。",
      "道具提示词必须包含轮廓、尺寸感、材质、结构、磨损/纹样、颜色和独立展示背景。",
      "画风默认沿用项目已设定风格；没有明确风格时，先给出风格建议，不要混用写实、2D和3D风格。",
      "输出格式：素材名称｜素材类型｜正向提示词｜负面提示词｜画幅建议｜一致性注意事项。",
      "避免文字、水印、Logo、畸形肢体、重复人物、错误手指、结构破损和无关元素。",
      midjourney ? "针对 Midjourney：提示词采用主体+细节+环境+光线+构图+风格的短语结构；参数单独放在末尾，默认建议 --ar 16:9，参数仅作建议，用户可按网页版本调整。" : "提示词使用清晰自然语言，避免堆砌互相冲突的形容词；不假定网站支持特定参数。",
    ].join("\n");
  } else if (category === "storyboard") {
    specialist = [
      "【分镜专用要求】",
      "先按场景和动作节拍拆分镜头，保证动作连续、空间关系清楚、情绪递进自然。",
      "使用表格输出：镜头号｜场景/时间｜景别｜机位/角度｜构图｜画面内容｜人物动作表情｜对白/旁白｜镜头运动｜建议时长｜关联人物/场景/道具｜图片生成提示词。",
      "每个镜头只表达一个主要视觉动作；明确人物在画面中的位置、视线方向、动作起止状态和道具位置。",
      "相邻镜头保持轴线、视线、光线方向和人物服装一致；重要动作提供全景/中景/特写的合理衔接。",
      "分镜图片提示词必须可直接用于图像生成；镜头时长需合理，总时长与场景节奏匹配。",
      "最后附镜头连续性检查和需要补充的素材清单。",
    ].join("\n");
  } else {
    specialist = [
      "【视频生成专用要求】",
      "将每个镜头写成可独立生成的视频提示词，描述主体、动作、场景、时间、光线、构图、景别、机位、镜头运动和情绪。",
      "明确动作起始状态、连续运动过程和结束状态；避免一个片段包含过多不相关动作。",
      "人物外观、服装、发型、道具、场景布局必须与参考图和前后镜头一致；不要擅自增加角色或改变空间。",
      "建议每段 3–8 秒，按工具实际能力调整；说明是否需要首帧/尾帧参考图、图生视频或文生视频。",
      "输出：镜头编号｜时长建议｜正向视频提示词｜负面提示词｜参考素材｜与前后镜头的衔接说明。",
      "负面约束包含：人物变脸、服装跳变、肢体扭曲、物体瞬移、画面闪烁、背景结构变化、无意义镜头抖动和字幕水印。",
      /kling|可灵/i.test(toolName) ? "针对可灵：优先写清主体动作、镜头运动、首尾帧变化与运动幅度，避免含糊的多动作指令。" :
      /runway/i.test(toolName) ? "针对 Runway：优先使用清晰的主体动作与摄像机运动描述，区分场景运动和镜头运动。" :
      "不要假定网站支持特定控制参数；将参数建议与自然语言提示词分开。",
    ].join("\n");
  }
  return [...common, specialist, "【最终交付】\n先给出可直接复制到所选 AI 网站的完整提示词；再给出简短执行注意事项。不要声称已经生成文件。"].join("\n\n");
}

function makeId() {
  return `task-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function safeRead<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export default function AIToolsPage() {
  const [tools, setTools] = useState<Tool[]>(presets);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [category, setCategory] = useState<ToolCategory>("text");
  const [promptMode, setPromptMode] = useState("professional");
  const [toolId, setToolId] = useState("chatgpt");
  const [title, setTitle] = useState("小说章节改编为漫剧剧本");
  const [sourceText, setSourceText] = useState("");
  const [customName, setCustomName] = useState("");
  const [customUrl, setCustomUrl] = useState("");
  const [customCategory, setCustomCategory] = useState<ToolCategory>("image");
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [resultText, setResultText] = useState("");
  const [resultUrl, setResultUrl] = useState("");
  const [notice, setNotice] = useState("");
  const [apiReady, setApiReady] = useState(false);
  const [taskFiles, setTaskFiles] = useState<Record<string, ResultFile[]>>({});
  const [novels, setNovels] = useState<NovelOption[]>([]);
  const [selectedNovelId, setSelectedNovelId] = useState("");
  const [chapters, setChapters] = useState<ChapterOption[]>([]);
  const [selectedChapterId, setSelectedChapterId] = useState("");
  const [selectedChapterIds, setSelectedChapterIds] = useState<string[]>([]);
  const [loadingChapter, setLoadingChapter] = useState(false);
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

  useEffect(() => {
    const savedTools = safeRead<Tool[]>(STORAGE_TOOLS, presets);
    const savedTasks = safeRead<Task[]>(STORAGE_TASKS, []);
    setTools(savedTools);
    setTasks(savedTasks);
    if (savedTools.length && !savedTools.some((tool) => tool.id === "chatgpt")) {
      setToolId(savedTools[0].id);
    }
    void Promise.all([
      fetch(`${apiBase}/api/web-ai/tools`).then((response) => response.ok ? response.json() : null).catch(() => null),
      fetch(`${apiBase}/api/web-ai/tasks`).then((response) => response.ok ? response.json() : null).catch(() => null),
    ]).then(([toolData, taskData]) => {
      if (Array.isArray(toolData?.items) && toolData.items.length) {
        setTools((current) => {
          const merged = new Map(current.map((item) => [item.id, item]));
          for (const item of toolData.items) merged.set(item.id, { id: item.id, name: item.name, category: item.category, url: item.url, note: item.note || "", custom: Boolean(item.custom) });
          return [...merged.values()];
        });
      }
      if (Array.isArray(taskData?.items) && taskData.items.length) {
        setTasks(taskData.items);
        for (const item of taskData.items) {
          void fetch(apiBase + "/api/web-ai/tasks/" + encodeURIComponent(item.id) + "/files").then((response) => response.ok ? response.json() : null).then((data) => {
            if (Array.isArray(data?.items)) setTaskFiles((current) => ({ ...current, [item.id]: data.items }));
          }).catch(() => undefined);
        }
      }
      setApiReady(true);
    });
  }, []);


  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBase}/api/novels`)
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("无法读取小说列表")))
      .then((data) => {
        if (cancelled) return;
        const items = Array.isArray(data?.items) ? data.items as NovelOption[] : [];
        setNovels(items);
        if (items.length) setSelectedNovelId(String(items[0].id));
      })
      .catch(() => {
        if (!cancelled) setNotice("暂时无法读取本地小说。请确认已导入小说且本地 API 正常运行。");
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!selectedNovelId) {
      setChapters([]);
      setSelectedChapterId("");
      return;
    }
    let cancelled = false;
    setChapters([]);
    setSelectedChapterId("");
    fetch(`${apiBase}/api/novels/${selectedNovelId}/chapters`)
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("读取章节目录失败")))
      .then((data) => {
        if (cancelled) return;
        const items = Array.isArray(data?.items) ? data.items as ChapterOption[] : [];
        setChapters(items);
        if (items.length) setSelectedChapterId(String(items[0].id));
        setSelectedChapterIds([]);
      })
      .catch(() => { if (!cancelled) setNotice("读取章节目录失败，请检查本地 API。"); });
    return () => { cancelled = true; };
  }, [selectedNovelId]);

  async function loadSelectedChapter(append = false) {
    if (!selectedChapterId) {
      setNotice("请先选择一个章节。");
      return;
    }
    setLoadingChapter(true);
    try {
      const response = await fetch(`${apiBase}/api/chapters/${selectedChapterId}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "读取章节内容失败");
      const chapter = chapters.find((item) => item.id === Number(selectedChapterId));
      const heading = `【${chapter?.chapter_number ?? ""} ${data.title || chapter?.title || "章节"}】\n`;
      const content = heading + (data.content || "");
      setSourceText((current) => append && current.trim() ? current.trimEnd() + "\n\n" + content : content);
      setTitle((current) => current.trim() ? current : `${novels.find((item) => String(item.id) === selectedNovelId)?.title || "小说"} - ${data.title || "章节"}`);
      setNotice(`已载入章节：${data.title || chapter?.title || ""}。现在可以生成任务提示词。`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "读取章节内容失败");
    } finally {
      setLoadingChapter(false);
    }
  }


  async function createBatchChapterTasks() {
    const chosen = chapters.filter((chapter) => selectedChapterIds.includes(String(chapter.id)));
    if (!chosen.length) { setNotice("请先勾选需要加入队列的章节。"); return; }
    if (chosen.length > 5) { setNotice("单批最多选择 5 个章节，请分批加入。"); return; }
    const plannedCount = chosen.length * 4;
    if (!window.confirm(`将读取所选 ${chosen.length} 个章节，为每章准备深度分析、剧本改编、视觉素材提示词、分镜规划 4 项任务，共 ${plannedCount} 项。内容只会保存在本地任务中，不会自动发送到 AI 网页。继续吗？`)) return;
    setLoadingChapter(true);
    setNotice("正在读取章节并准备任务队列，请稍候…");
    try {
      const queued: Task[] = [];
      const stages: { category: ToolCategory; label: string; instruction: string }[] = [
        { category: "text", label: "剧情与人物深度分析", instruction: "深度分析本章，提取剧情事件、人物出场与行为动机、关系变化、时间线、地点、伏笔、关键道具和情绪转折。结构化输出，不得编造原文没有的事实。" },
        { category: "text", label: "漫剧剧本改编", instruction: "将本章改编为适合漫剧的剧本，保留原著主线、人物性格和关键事件。按场次输出场景、时间、人物、动作、对白、旁白和转场，避免无意义扩写。" },
        { category: "image", label: "人物场景道具视觉提示词", instruction: "提取主要人物、场景和道具，分别生成详细图片提示词。人物强调身份、年龄感、脸型、发型、服装、配色和三视图；场景强调空间结构、光线、气氛；道具强调形状、材质和识别特征。保持画风统一。" },
        { category: "storyboard", label: "漫剧分镜规划", instruction: "规划连续分镜，输出镜头编号、场景、景别、机位、构图、画面内容、人物动作表情、对白/旁白、镜头运动、建议时长及人物/场景/道具关联。保持连续性。" },
      ];
      for (const chapter of chosen) {
        const response = await fetch(`${apiBase}/api/chapters/${chapter.id}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `读取章节失败：${chapter.title}`);
        if (!data.content?.trim()) throw new Error(`章节正文为空：${chapter.title}`);
        const novelName = novels.find((item) => item.id === chapter.novel_id)?.title || "小说";
        const source = `【小说】${novelName}\n【章节】第 ${chapter.chapter_number} 章：${data.title || chapter.title}\n\n${data.content}`;
        for (const stage of stages) {
          const target = tools.find((item) => item.category === stage.category);
          if (!target) throw new Error(`没有配置可用的${categoryLabels[stage.category]}工具`);
          queued.push({
            id: makeId(), title: `${novelName} · 第${chapter.chapter_number}章 · ${stage.label}`,
            category: stage.category, toolId: target.id,
            prompt: buildProductionPrompt({ category: stage.category, title: `${novelName} · 第${chapter.chapter_number}章 · ${stage.label}`, source, mode: promptMode, toolName: target.name, instruction: stage.instruction }),
            status: "prepared", resultText: "", resultUrl: "", novelId: chapter.novel_id, chapterId: chapter.id, createdAt: new Date().toISOString(),
          });
        }
      }
      setTasks((current) => [...queued, ...current]);
      setNotice(`任务队列已准备完成：${chosen.length} 个章节，共 ${queued.length} 项。逐项复制并打开对应 AI 网页，确认后再发送。`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "批量准备任务失败");
    } finally {
      setLoadingChapter(false);
    }
  }

  useEffect(() => {
    const bridge = (window as Window & {
      aiBrowserDesktop?: {
        onDownloaded?: (callback: (payload: ResultFile & { taskId: string }) => void) => () => void;
        onDownloadFailed?: (callback: (payload: { taskId: string; filename: string; reason?: string }) => void) => () => void;
        onDownloadRejected?: (callback: (payload: { taskId: string; filename: string; reason: string }) => void) => () => void;
      };
    }).aiBrowserDesktop;
    if (!bridge) return;
    const removeDownloaded = bridge.onDownloaded?.((file) => {
      setTaskFiles((current) => ({ ...current, [file.taskId]: [file, ...(current[file.taskId] || [])] }));
      setNotice("已自动收集并归档网页下载文件：" + file.filename);
    });
    const removeFailed = bridge.onDownloadFailed?.((payload) => {
      setNotice("网页文件已下载，但自动登记失败：" + payload.filename + "。可在任务中手动导入。");
    });
    const removeRejected = bridge.onDownloadRejected?.((payload) => {
      setNotice(payload.reason || "该文件类型暂不支持自动归档。");
    });
    return () => {
      removeDownloaded?.();
      removeFailed?.();
      removeRejected?.();
    };
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_TOOLS, JSON.stringify(tools));
    } catch {
      setNotice("浏览器本地存储不可用，工具配置可能无法保留。");
    }
  }, [tools]);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_TASKS, JSON.stringify(tasks));
    } catch {
      setNotice("浏览器本地存储空间不足，任务记录可能无法保留。");
    }
  }, [tasks]);

  useEffect(() => {
    if (!apiReady) return;
    for (const tool of tools) {
      void fetch(`${apiBase}/api/web-ai/tools`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: tool.id, name: tool.name, category: tool.category, url: tool.url, note: tool.note, custom: Boolean(tool.custom), enabled: true }),
      }).catch(() => setNotice("本地 API 暂不可用，工具配置暂时仅保存在本机浏览器。"));
    }
  }, [tools, apiReady]);

  useEffect(() => {
    if (!apiReady) return;
    for (const task of tasks) {
      const payload = { id: task.id, title: task.title, category: task.category, toolId: task.toolId, status: task.status, prompt: task.prompt, sourceText: "", resultText: task.resultText, resultUrl: task.resultUrl, novelId: task.novelId ?? null, chapterId: task.chapterId ?? null };
      void fetch(`${apiBase}/api/web-ai/tasks`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      }).then(async (response) => {
        if (response.status === 409) await fetch(`${apiBase}/api/web-ai/tasks/${encodeURIComponent(task.id)}`, {
          method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
        });
      }).catch(() => setNotice("本地 API 暂不可用，任务暂时仅保存在本机浏览器。"));
    }
  }, [tasks, apiReady]);

  const categoryTools = useMemo(
    () => tools.filter((tool) => tool.category === category),
    [tools, category],
  );
  const selectedTool = tools.find((tool) => tool.id === toolId && tool.category === category)
    ?? categoryTools[0];

  useEffect(() => {
    if (selectedTool && selectedTool.id !== toolId) setToolId(selectedTool.id);
  }, [selectedTool, toolId]);

  function buildPrompt() {
    return buildProductionPrompt({
      category,
      title,
      source: sourceText,
      mode: promptMode,
      toolName: selectedTool?.name || "",
      instruction: taskExamples[category],
    });
  }

  async function copyText(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setNotice("任务提示词已复制。");
    } catch {
      setNotice("无法自动复制，请手动选中提示词复制。");
    }
  }

  function createTask() {
    if (!title.trim()) {
      setNotice("请先填写任务名称。");
      return;
    }
    if (!selectedTool) {
      setNotice("当前任务类型没有可用工具，请先添加一个工具。");
      return;
    }
    const task: Task = {
      id: makeId(),
      title: title.trim(),
      category,
      toolId: selectedTool.id,
      prompt: buildPrompt(),
      status: "prepared",
      resultText: "",
      resultUrl: "",
      novelId: selectedNovelId ? Number(selectedNovelId) : undefined,
      chapterId: selectedChapterId ? Number(selectedChapterId) : undefined,
      createdAt: new Date().toISOString(),
    };
    setTasks((current) => [task, ...current]);
    setActiveTaskId(task.id);
    setResultText("");
    setResultUrl("");
    setNotice("任务已准备。点击“复制并打开网页”后，才会跳转到外部 AI 网站。");
  }

  async function openTask(task: Task) {
    const target = tools.find((tool) => tool.id === task.toolId);
    if (!target) {
      setNotice("找不到该任务对应的 AI 工具，请检查工具配置。");
      return;
    }
    try {
      const parsed = new URL(target.url);
      if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
        throw new Error("只允许 HTTP 或 HTTPS 网站地址");
      }
      const embedded = (window as Window & { aiBrowserDesktop?: {
        openTask?: (taskId: string, url: string) => Promise<{ opened: boolean }>;
        fillPrompt?: (taskId: string, prompt: string) => Promise<{ filled: boolean; length: number; submitted: boolean }>;
        captureTextResult?: (taskId: string) => Promise<{ captured: boolean; text: string; length: number; saved: boolean }>;
      } }).aiBrowserDesktop;
      const desktop = (window as Window & { desktopAPI?: { openExternal?: (url: string) => Promise<boolean> } }).desktopAPI;
      await copyText(task.prompt);
      if (embedded?.openTask) {
        const opened = await embedded.openTask(task.id, parsed.toString());
        if (!opened?.opened) throw new Error("内嵌 AI 网页未能打开");
        if (embedded.fillPrompt) {
          try {
            const filled = await embedded.fillPrompt(task.id, task.prompt);
            if (!filled?.filled || filled.submitted) throw new Error("自动填入状态未通过校验");
            setNotice(`已在软件内打开 ${target.name}，并自动填入完整提示词（${filled.length} 字）。请先检查网页内容，再由你手动点击发送；软件不会自动提交或触发付费。`);
          } catch {
            setNotice(`已在软件内打开 ${target.name}，提示词已复制到剪贴板，但该网页暂未能自动定位输入框。请手动粘贴后检查并发送。`);
          }
        } else {
          setNotice(`已在软件内打开 ${target.name}，提示词已复制到剪贴板。请手动粘贴、检查并发送。`);
        }
      } else {
        const opening = desktop?.openExternal
          ? desktop.openExternal(parsed.toString())
          : Promise.resolve(window.open(parsed.toString(), "_blank", "noopener,noreferrer"));
        await opening;
        setNotice(`已打开 ${target.name}。提示词已复制；当前运行环境不支持内嵌浏览器自动收集，请手动导入结果。`);
      }
      setTasks((current) => current.map((item) => item.id === task.id ? { ...item, status: "opened" } : item));
    } catch {
      setNotice("打开网页失败，请检查工具网址或默认浏览器设置。");
    }
  }

  function addCustomTool() {
    if (!customName.trim() || !customUrl.trim()) {
      setNotice("请填写工具名称和网址。");
      return;
    }
    try {
      const parsed = new URL(customUrl.trim());
      if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
        setNotice("只允许 HTTP 或 HTTPS 网站地址。");
        return;
      }
      const tool: Tool = {
        id: `custom-${Date.now()}`,
        name: customName.trim(),
        category: customCategory,
        url: parsed.toString(),
        note: "用户自定义工具",
        custom: true,
      };
      setTools((current) => [...current, tool]);
      setCategory(customCategory);
      setToolId(tool.id);
      setCustomName("");
      setCustomUrl("");
      setNotice("自定义 AI 工具已添加到本机配置。");
    } catch {
      setNotice("网址格式不正确，请输入完整的网址，例如 https://example.com。");
    }
  }

  async function captureWebResult(task: Task) {
    const embedded = (window as Window & {
      aiBrowserDesktop?: {
        captureTextResult?: (taskId: string) => Promise<{ captured: boolean; text: string; length: number; saved: boolean }>;
      };
    }).aiBrowserDesktop;
    if (!embedded?.captureTextResult) {
      setNotice("当前环境不支持自动读取网页回答，请手动复制到结果框。");
      return;
    }
    try {
      const captured = await embedded.captureTextResult(task.id);
      if (!captured?.captured || !captured.text?.trim() || captured.saved) {
        throw new Error("没有读取到可保存的网页回答。");
      }
      setResultText(captured.text);
      setResultUrl("");
      setActiveTaskId(task.id);
      setNotice(`已读取网页最新回答（${captured.length} 字），尚未保存。请检查内容后点击“保存结果”。`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "自动读取失败，请手动复制网页回答。");
    }
  }

  function saveResult(task: Task) {
    let normalizedResultUrl = "";
    if (resultUrl.trim()) {
      try {
        const parsed = new URL(resultUrl.trim());
        if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
          setNotice("结果链接只允许 HTTP 或 HTTPS 地址。");
          return;
        }
        normalizedResultUrl = parsed.toString();
      } catch {
        setNotice("结果链接格式不正确。");
        return;
      }
    }
    setTasks((current) => current.map((item) => item.id === task.id
      ? { ...item, status: "completed", resultText, resultUrl: normalizedResultUrl }
      : item));
    setActiveTaskId(null);
    setNotice("结果已保存到本机任务记录。图片和视频文件请先从 AI 网站下载，再到素材库导入。");
  }


  async function uploadResultFile(task: Task, file?: File) {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      const response = await fetch(apiBase + "/api/web-ai/tasks/" + encodeURIComponent(task.id) + "/files", { method: "POST", body: form });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "文件上传失败");
      setTaskFiles((current) => ({ ...current, [task.id]: [data, ...(current[task.id] || [])] }));
      setNotice("已将 " + file.name + " 保存到本机任务结果库。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "文件上传失败，请确认本地 API 正常运行。");
    }
  }


  async function confirmResultFile(task: Task, file: ResultFile, classification: string) {
    const needsName = ["character", "scene", "prop", "weapon", "costume", "building", "special_item"].includes(classification);
    const defaultName = file.filename.replace(/\\.[^.]+$/, "");
    const assetName = needsName ? window.prompt("请输入素材名称（确认后将登记到人物/场景/道具素材库）", defaultName) : "";
    if (needsName && !assetName?.trim()) return;
    let novelId = task.novelId;
    if (needsName && !novelId && novels.length) {
      const choices = novels.map((item) => `${item.id}: ${item.title}`).join("\\n");
      const selected = window.prompt("请选择素材所属小说，输入小说编号；不绑定小说请留空：\\n" + choices, "");
      if (selected === null) return;
      novelId = selected.trim() ? Number(selected.trim()) : undefined;
      if (selected.trim() && (!Number.isInteger(novelId) || !novels.some((item) => item.id === novelId))) {
        setNotice("小说编号无效，请重新确认归档。");
        return;
      }
    }
    try {
      const response = await fetch(apiBase + "/api/web-ai/files/" + file.id + "/classification", {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ classification, assetName: assetName || "", description: "由任务“" + task.title + "”导入", novelId: novelId ?? null }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "确认归档失败");
      setTaskFiles((current) => ({ ...current, [task.id]: (current[task.id] || []).map((item) => item.id === file.id ? data : item) }));
      setNotice(needsName ? "已确认并登记到素材库：" + assetName : "已确认文件分类：" + file.filename);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "确认归档失败");
    }
  }
  function removeTask(taskId: string) {
    setTasks((current) => current.filter((task) => task.id !== taskId));
    if (activeTaskId === taskId) setActiveTaskId(null);
  }

  const activeTask = tasks.find((task) => task.id === activeTaskId);

  return (
    <main className="cosmic min-h-screen px-4 py-6 text-white md:px-8">
      <div className="mx-auto max-w-7xl space-y-6">
        <header className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-xs tracking-[0.3em] text-purple-300">AI WEB TOOL ORCHESTRATION</p>
            <h1 className="mt-2 text-3xl font-black">AI 网页工具协同中心</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-purple-200">
              在本软件中准备任务，按需打开你已登录的 AI 网页，完成生成后把文字结果登记到任务，并将下载的图片、视频导入素材库。
            </p>
          </div>
          <a href="/" className="rounded-xl border border-purple-300/30 px-4 py-2 text-sm text-purple-100 hover:bg-purple-950/50">返回首页</a>
        </header>

        <section className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
          <div className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-5 shadow-xl shadow-purple-950/20">
            <div className="mb-4 flex items-center justify-between gap-3">
              <h2 className="text-lg font-bold">① 准备 AI 任务</h2>
              <span className="rounded-full border border-emerald-300/20 bg-emerald-950/30 px-3 py-1 text-xs text-emerald-200">手动确认后才打开网页</span>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-sm text-purple-200">任务类型
                <select value={category} onChange={(event) => setCategory(event.target.value as ToolCategory)} className="mt-2 w-full rounded-xl border border-white/10 bg-slate-900 px-3 py-3 text-white">
                  {Object.entries(categoryLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
              <label className="text-sm text-purple-200">使用的 AI 工具
                <select value={selectedTool?.id ?? ""} onChange={(event) => setToolId(event.target.value)} className="mt-2 w-full rounded-xl border border-white/10 bg-slate-900 px-3 py-3 text-white">
                  {categoryTools.map((tool) => <option key={tool.id} value={tool.id}>{tool.name}</option>)}
                </select>
              </label>
            </div>
            {selectedTool && <p className="mt-2 text-xs text-slate-400">{selectedTool.note} · {selectedTool.url}</p>}
            <label className="mt-4 block text-sm text-purple-200">提示词方案（自动匹配任务并用于生成）
              <select value={promptMode} onChange={(event) => setPromptMode(event.target.value)} className="mt-2 w-full rounded-xl border border-white/10 bg-slate-900 px-3 py-3 text-white">
                <option value="professional">专业详细提示词（推荐）</option>
                <option value="cinematic">电影级叙事 / 视觉提示词</option>
                <option value="concise">简洁提示词</option>
              </select>
            </label>
            <label className="mt-4 block text-sm text-purple-200">任务名称
              <input value={title} onChange={(event) => setTitle(event.target.value)} className="mt-2 w-full rounded-xl border border-white/10 bg-slate-900 px-3 py-3 text-white outline-none focus:border-purple-300/60" placeholder="例如：第一章改编为第1集剧本" />
            </label>
            <div className="mt-4 rounded-xl border border-cyan-300/20 bg-cyan-950/20 p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h3 className="text-sm font-bold text-cyan-100">从本地小说直接选取章节</h3>
                  <p className="mt-1 text-xs text-cyan-200/70">自动读取已导入的小说目录；正文只在你选择载入时读取，不会自动发送给 AI。</p>
                </div>
                <button type="button" onClick={() => {
                  fetch(`${apiBase}/api/novels`).then((response) => response.ok ? response.json() : Promise.reject(new Error("刷新失败"))).then((data) => {
                    const items = Array.isArray(data?.items) ? data.items as NovelOption[] : [];
                    setNovels(items);
                    setSelectedNovelId(items.length ? String(items[0].id) : "");
                    setNotice(items.length ? "小说列表已刷新。" : "本地还没有导入的小说。");
                  }).catch(() => setNotice("刷新小说列表失败，请检查本地 API。"));
                }} className="rounded-lg border border-cyan-200/30 px-3 py-1.5 text-xs text-cyan-100">刷新小说</button>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="text-xs text-cyan-100">小说项目
                  <select value={selectedNovelId} onChange={(event) => setSelectedNovelId(event.target.value)} className="mt-1.5 w-full rounded-lg border border-white/10 bg-slate-900 px-3 py-2.5 text-sm text-white">
                    <option value="">请选择小说</option>
                    {novels.map((novel) => <option key={novel.id} value={String(novel.id)}>{novel.title}</option>)}
                  </select>
                </label>
                <label className="text-xs text-cyan-100">章节
                  <select value={selectedChapterId} onChange={(event) => setSelectedChapterId(event.target.value)} disabled={!chapters.length} className="mt-1.5 w-full rounded-lg border border-white/10 bg-slate-900 px-3 py-2.5 text-sm text-white disabled:opacity-50">
                    <option value="">请选择章节</option>
                    {chapters.map((chapter) => <option key={chapter.id} value={String(chapter.id)}>{chapter.chapter_number}. {chapter.title}（{chapter.word_count}字）</option>)}
                  </select>
                </label>
              </div>
              {chapters.length > 0 && (
                <div className="mt-3 rounded-lg border border-white/10 bg-black/20 p-3">
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <span className="text-xs font-semibold text-cyan-100">批量选择章节（每批最多 5 章）</span>
                    <button type="button" onClick={() => setSelectedChapterIds(chapters.slice(0, 5).map((item) => String(item.id)))} className="text-xs text-cyan-200 underline">选择前 5 章</button>
                  </div>
                  <div className="max-h-40 space-y-1 overflow-y-auto">
                    {chapters.map((chapter) => (
                      <label key={chapter.id} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs text-slate-300 hover:bg-white/5">
                        <input type="checkbox" checked={selectedChapterIds.includes(String(chapter.id))} onChange={(event) => setSelectedChapterIds((current) => event.target.checked ? [...current, String(chapter.id)] : current.filter((id) => id !== String(chapter.id)))} />
                        <span>第 {chapter.chapter_number} 章 · {chapter.title}（{chapter.word_count}字）</span>
                      </label>
                    ))}
                  </div>
                  <button type="button" onClick={() => void createBatchChapterTasks()} disabled={loadingChapter || selectedChapterIds.length === 0 || selectedChapterIds.length > 5} className="mt-3 rounded-lg bg-gradient-to-r from-cyan-700 to-blue-700 px-4 py-2 text-xs font-semibold text-white disabled:opacity-40">
                    {loadingChapter ? "正在准备队列…" : `为所选 ${selectedChapterIds.length} 章生成全流程任务`}
                  </button>
                  {selectedChapterIds.length > 5 && <p className="mt-1 text-xs text-amber-300">每批最多 5 章，请取消部分选择。</p>}
                </div>
              )}
              <div className="mt-3 flex flex-wrap gap-2">
                <button type="button" onClick={() => void loadSelectedChapter(false)} disabled={!selectedChapterId || loadingChapter} className="rounded-lg bg-cyan-700 px-4 py-2 text-xs font-semibold text-white disabled:opacity-40">{loadingChapter ? "正在读取…" : "载入所选章节（替换）"}</button>
                <button type="button" onClick={() => void loadSelectedChapter(true)} disabled={!selectedChapterId || loadingChapter} className="rounded-lg border border-cyan-200/30 px-4 py-2 text-xs text-cyan-100 disabled:opacity-40">追加所选章节</button>
                <span className="self-center text-xs text-slate-400">{novels.length ? `已找到 ${novels.length} 部小说 · ${chapters.length} 个章节` : "尚未读取到小说项目"}</span>
              </div>
            </div>
            <label className="mt-4 block text-sm text-purple-200">小说原文 / 人物设定 / 分镜要求
              <textarea value={sourceText} onChange={(event) => setSourceText(event.target.value)} rows={8} className="mt-2 w-full resize-y rounded-xl border border-white/10 bg-slate-900 px-3 py-3 text-sm leading-6 text-white outline-none focus:border-purple-300/60" placeholder="粘贴需要交给 AI 网页处理的内容。建议按章节或镜头分批提交。" />
            </label>
            <div className="mt-4 flex flex-wrap gap-3">
              <button onClick={createTask} className="rounded-xl bg-gradient-to-r from-violet-600 to-fuchsia-600 px-5 py-3 text-sm font-semibold shadow-lg shadow-purple-950/40 hover:brightness-110">直接创建任务（自动准备提示词）</button>
              <button onClick={() => void copyText(buildPrompt())} className="rounded-xl border border-purple-300/30 px-5 py-3 text-sm text-purple-100 hover:bg-purple-950/50">只复制提示词</button>
            </div>
          </div>

          <div className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-5">
            <h2 className="text-lg font-bold">② 工具管理</h2>
            <p className="mt-2 text-xs leading-5 text-slate-400">内置常用入口，也可以添加你自己的 AI 网站。工具列表只保存在当前电脑的本地浏览器配置中。</p>
            <div className="mt-4 space-y-2">
              {tools.map((tool) => (
                <div key={tool.id} className="flex items-center justify-between gap-3 rounded-xl border border-white/10 bg-black/20 px-3 py-2.5">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{tool.name} <span className="ml-2 text-xs text-purple-300">{categoryLabels[tool.category]}</span></p>
                    <p className="truncate text-xs text-slate-500">{tool.url}</p>
                  </div>
                  {tool.custom ? <button onClick={() => setTools((current) => current.filter((item) => item.id !== tool.id))} className="shrink-0 text-xs text-red-300 hover:text-red-200">删除</button> : <span className="shrink-0 text-xs text-slate-500">内置</span>}
                </div>
              ))}
            </div>
            <div className="mt-5 border-t border-white/10 pt-4">
              <h3 className="text-sm font-semibold">添加自定义工具</h3>
              <div className="mt-3 grid gap-2">
                <input value={customName} onChange={(event) => setCustomName(event.target.value)} placeholder="工具名称" className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2.5 text-sm outline-none focus:border-purple-300/60" />
                <input value={customUrl} onChange={(event) => setCustomUrl(event.target.value)} placeholder="https://AI网站地址" className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2.5 text-sm outline-none focus:border-purple-300/60" />
                <select value={customCategory} onChange={(event) => setCustomCategory(event.target.value as ToolCategory)} className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2.5 text-sm">
                  {Object.entries(categoryLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
                <button onClick={addCustomTool} className="rounded-lg border border-purple-300/30 px-4 py-2.5 text-sm text-purple-100 hover:bg-purple-950/50">＋ 添加工具</button>
              </div>
            </div>
          </div>
        </section>

        <section className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-bold">③ 任务与结果归档</h2>
            <a href="/assets" className="rounded-lg border border-cyan-300/30 px-4 py-2 text-xs text-cyan-200 hover:bg-cyan-950/30">打开素材库导入文件 ↗</a>
          </div>
          {notice && <p className="mt-3 rounded-xl border border-purple-300/15 bg-purple-950/30 px-3 py-2 text-xs text-purple-100">{notice}</p>}
          <div className="mt-4 space-y-3">
            {tasks.map((task) => {
              const tool = tools.find((item) => item.id === task.toolId);
              return (
                <article key={task.id} className="rounded-xl border border-white/10 bg-black/20 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h3 className="font-semibold">{task.title}</h3>
                      <p className="mt-1 text-xs text-slate-400">{categoryLabels[task.category]} · {tool?.name ?? "工具已删除"} · {new Date(task.createdAt).toLocaleString()}</p>
                      <p className="mt-2 text-xs text-slate-400">状态：{task.status === "prepared" ? "已准备" : task.status === "opened" ? "已打开网页 / 等待生成" : "结果已登记"}</p>
                      <details className="mt-3 rounded-lg border border-purple-300/15 bg-purple-950/20 p-3">
                        <summary className="cursor-pointer text-xs font-semibold text-purple-200">查看本任务实际使用的提示词（可复制）</summary>
                        <textarea readOnly value={task.prompt} rows={7} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-xs leading-5 text-slate-200" />
                        <button type="button" onClick={() => void copyText(task.prompt)} className="mt-2 rounded-lg border border-purple-300/30 px-3 py-1.5 text-xs text-purple-100">复制本任务提示词</button>
                      </details>
                      {task.resultUrl && <a href={task.resultUrl} target="_blank" rel="noreferrer" className="mt-2 block break-all text-xs text-cyan-300 underline">结果链接：{task.resultUrl}</a>}
                      {task.resultText && <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-xs text-slate-300">{task.resultText}</p>}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button onClick={() => void openTask(task)} className="rounded-lg bg-violet-700 px-3 py-2 text-xs font-medium hover:bg-violet-600">复制并打开网页</button>
                      <button onClick={() => { setActiveTaskId(task.id); setResultText(task.resultText); setResultUrl(task.resultUrl); }} className="rounded-lg border border-emerald-300/30 px-3 py-2 text-xs text-emerald-200 hover:bg-emerald-950/30">登记生成结果</button>
                      <button onClick={() => removeTask(task.id)} className="rounded-lg border border-red-300/20 px-3 py-2 text-xs text-red-200 hover:bg-red-950/30">删除</button>
                      <label className="cursor-pointer rounded-lg border border-cyan-300/30 px-3 py-2 text-xs text-cyan-200 hover:bg-cyan-950/30">
                        导入生成文件
                        <input type="file" className="hidden" accept="image/*,video/*,audio/*,.txt,.md,.json,.srt,.vtt,.pdf,.docx,.csv" onChange={(event) => { void uploadResultFile(task, event.target.files?.[0]); event.currentTarget.value = ""; }} />
                      </label>
                    </div>
                  </div>
                  {(taskFiles[task.id] || []).length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {(taskFiles[task.id] || []).map((file) => (
                        <div key={file.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-cyan-300/20 px-3 py-2">
                          <a href={apiBase + file.downloadUrl} className="text-xs text-cyan-200 hover:underline" download>{file.filename} · {(file.size / 1024 / 1024).toFixed(1)} MB</a>
                          <span className="text-[11px] text-slate-400">{file.classification || "待识别"} · {file.classificationStatus === "confirmed" ? "已确认" : "待确认"}</span>
                          {file.classificationStatus !== "confirmed" && <div className="flex flex-wrap gap-1">
                            {(["character", "scene", "prop", "weapon", "costume", "building", "special_item", "storyboard", "script", "video", "audio", "document", "image", "other"] as const).map((kind) => {
                              const labels: Record<string, string> = { character: "人物", scene: "场景", prop: "道具", weapon: "武器", costume: "服装", building: "建筑", special_item: "特殊物品", storyboard: "分镜", script: "剧本", video: "视频", audio: "音频", document: "文档", image: "图片", other: "其他" };
                              return <button key={kind} onClick={() => void confirmResultFile(task, file, kind)} className="rounded border border-purple-300/20 px-2 py-1 text-[10px] text-purple-100 hover:bg-purple-900/40">确认为{labels[kind]}</button>;
                            })}
                          </div>}
                          {file.visualAssetId && <span className="text-[10px] text-emerald-300">素材库 ID：{file.visualAssetId}</span>}
                        </div>
                      ))}
                    </div>
                  )}
                  {activeTaskId === task.id && (
                    <div className="mt-4 grid gap-3 border-t border-white/10 pt-4">
                      <label className="text-xs text-purple-200">生成结果文字 / 剧本 / 分镜内容
                        <textarea value={resultText} onChange={(event) => setResultText(event.target.value)} rows={5} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-sm text-white" placeholder="将网页生成的文字复制到这里。" />
                      </label>
                      <label className="text-xs text-purple-200">结果网页链接（可选）
                        <input value={resultUrl} onChange={(event) => setResultUrl(event.target.value)} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-sm text-white" placeholder="https://..." />
                      </label>
                      <div className="flex flex-wrap gap-2">
                        <button onClick={() => void captureWebResult(task)} className="rounded-lg border border-cyan-300/30 px-4 py-2 text-xs text-cyan-100">从网页读取最新回答</button>
                        <button onClick={() => saveResult(task)} className="rounded-lg bg-emerald-700 px-4 py-2 text-xs font-semibold hover:bg-emerald-600">检查后保存结果</button>
                        <button onClick={() => setActiveTaskId(null)} className="rounded-lg border border-white/15 px-4 py-2 text-xs">取消</button>
                      </div>
                    </div>
                  )}
                </article>
              );
            })}
            {tasks.length === 0 && <div className="rounded-xl border border-dashed border-white/15 p-8 text-center text-sm text-slate-400">还没有任务。先准备一个剧本、图片、分镜或视频生成任务。</div>}
          </div>
        </section>

        <section className="grid gap-3 md:grid-cols-3">
          {[
            ["网页由你控制", "支持站点在软件内嵌浏览器打开；不会读取网站密码、Cookie 或验证码。"],
            ["不自动触发付费", "打开网站不等于提交生成任务；复制提示词、提交生成和付费均由你确认。"],
            ["结果可追溯", "支持站点可读取当前最新回答供你检查后保存；下载文件按任务归档到本机素材库。"],
          ].map(([heading, description]) => <div key={heading} className="rounded-xl border border-white/10 bg-slate-950/50 p-4"><h3 className="text-sm font-semibold text-purple-100">{heading}</h3><p className="mt-2 text-xs leading-5 text-slate-400">{description}</p></div>)}
        </section>
      </div>
    </main>
  );
}
