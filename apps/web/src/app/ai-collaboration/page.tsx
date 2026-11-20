"use client";

import { useEffect, useMemo, useState } from "react";

declare global {
  interface Window {
    aiBrowserDesktop?: {
      openTask: (taskId: string, url: string) => Promise<{ opened: boolean; taskId: string }>;
      fillPrompt: (taskId: string, prompt: string) => Promise<{ filled: boolean; length: number; submitted: false }>;
    };
  }
}

type Novel = { id: number; title: string; genre?: string };
type Chapter = { id: number; novel_id: number; chapter_number: number; title: string; word_count: number };
type Handoff = {
  novel_id: number; chapter_id: number; chapter_number: number; content_chars: number;
  truncated: boolean; instruction: string; output_schema: unknown; context: unknown;
  third_party_transfer_started: boolean; notice: string;
};
type Proposal = {
  id: number; chapter_number: number; asset_name: string; asset_category: string;
  change_type: string; event: string; appearance_after: string; clothing_after: string;
  age_after?: string; hairstyle_after?: string; injury_after?: string;
  equipment_after?: string; condition_after?: string;
  holder: string; evidence: string; confidence: number; status: string; review_note?: string;
};
type ResultAttachment = {
  id: number; novel_id: number; chapter_number?: number | null; original_filename: string;
  media_type: string; mime_type: string; file_size: number; sha256: string;
  description: string; status: string; review_note?: string; media_file_id?: number | null;
  created_at?: string;
};
type WebAIResultFile = {
  id: number; taskId: string; filename: string; contentType: string; size: number;
  downloadUrl: string; createdAt: string; classification: string; classificationStatus: string;
  classificationReason: string; visualAssetId?: number | null; taskTitle: string;
  taskCategory: string; toolId: string; novelId?: number | null; chapterId?: number | null;
};
const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

const AI_WEB_PROVIDERS = {
  chatgpt: { label: "ChatGPT", url: "https://chatgpt.com/" },
  deepseek: { label: "DeepSeek", url: "https://chat.deepseek.com/" },
  doubao: { label: "豆包", url: "https://www.doubao.com/chat/" },
  grok: { label: "Grok", url: "https://grok.com/" },
} as const;
type AIWebProvider = keyof typeof AI_WEB_PROVIDERS;
type HandoffTaskType = "剧情分析" | "剧本改编" | "分镜设计" | "角色设定" | "场景设定" | "道具设定" | "对白整理";
type VisualStyle = "国漫" | "仿真人" | "3D漫" | "2D漫";
const VISUAL_STYLE_GUIDES: Record<VisualStyle, string> = {
  国漫: "中国动画电影美术语言，东方审美，符合题材时代的服饰与建筑，精细线稿与层次丰富的色彩，电影级构图；避免日式动漫脸和现代元素错置。",
  仿真人: "写实真人影视质感，真实人体比例、自然皮肤纹理、可信的服装材质与光影，电影摄影构图；角色外貌稳定，不要塑料皮肤或夸张卡通比例。",
  "3D漫": "高品质3D动画电影风格，立体角色模型、精细材质、自然布料与头发模拟、电影级全局光照和景深；所有镜头保持同一角色模型设定。",
  "2D漫": "高品质二维动画风格，清晰稳定的轮廓线、赛璐璐或细腻手绘上色、分层背景与动画电影构图；人物比例、线条和色彩体系保持统一。",
};
function visualPromptRequirements(style: VisualStyle, task: HandoffTaskType): string {
  const imageTasks = task === "分镜设计" || task === "角色设定" || task === "场景设定" || task === "道具设定";
  return [
    "【用户指定的统一视觉风格】" + style + "。" + VISUAL_STYLE_GUIDES[style],
    "【视觉连续性硬性要求】同一角色跨镜头保持脸型、五官、年龄感、发型、体型和服装一致；同一场景保持建筑结构、时间、天气、主色调和空间布局一致；道具外观与持有者一致。若剧情明确发生变化，按章节先后呈现变化，不得提前或遗漏。",
    imageTasks ? "【必须生成可直接用于图片模型的提示词】为每个主要角色分别输出角色定妆图提示词（正面、侧面、全身、表情与服装细节）；为每个重要场景输出环境概念图提示词；为每件关键道具、武器和服装输出独立设定图提示词；分镜中的每个镜头输出独立画面提示词。提示词必须包含主体、外观、动作、场景、构图、景别、光线、色彩、风格、画幅及负面提示词，并明确引用本次统一画风。" : "【图片提示词联动】剧本和剧情分析中标记需要制作角色设定图、场景图、道具图及关键剧情插图的位置，并给出可供后续图片提示词生成使用的具体视觉描述。",
    "【输出要求】内容必须依据本章原文，不得擅自新增角色设定或剧情事实；缺失信息标记为待确认；图片提示词不得只写‘好看’‘电影感’，必须具体可执行。",
  ].join("\n\n");
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(API + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.message || `请求失败（${response.status}）`);
  return data as T;
}

export default function AICollaborationPage() {
  const [novels, setNovels] = useState<Novel[]>([]);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [novelId, setNovelId] = useState("");
  const [chapterNumber, setChapterNumber] = useState("");
  const [selectedChapterNumbers, setSelectedChapterNumbers] = useState<string[]>([]);
  const [batchProgress, setBatchProgress] = useState("");
  const [handoff, setHandoff] = useState<Handoff | null>(null);
  const [resultJson, setResultJson] = useState("");
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [attachments, setAttachments] = useState<ResultAttachment[]>([]);
  const [webAiFiles, setWebAiFiles] = useState<WebAIResultFile[]>([]);
  const [webAiClassifications, setWebAiClassifications] = useState<Record<number, string>>({});
  const [webAiAssetNames, setWebAiAssetNames] = useState<Record<number, string>>({});
  const [selectedWebAiFileIds, setSelectedWebAiFileIds] = useState<number[]>([]);
  const [compareIds, setCompareIds] = useState<number[]>([]);
  const [comparison, setComparison] = useState<{ left: string; right: string } | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [attachmentDescription, setAttachmentDescription] = useState("");
  const [attachmentChapter, setAttachmentChapter] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [filter, setFilter] = useState("pending_review");
  const [aiProvider, setAiProvider] = useState<AIWebProvider>("chatgpt");
  const [providerRoutes, setProviderRoutes] = useState<Record<HandoffTaskType, AIWebProvider>>({ "剧情分析": "chatgpt", "剧本改编": "deepseek", "分镜设计": "doubao", "角色设定": "chatgpt", "场景设定": "doubao", "道具设定": "doubao", "对白整理": "deepseek" });
  const [autoOpenAi, setAutoOpenAi] = useState(true);
  const [visualStyle, setVisualStyle] = useState<VisualStyle>("国漫");
  const [taskType, setTaskType] = useState<HandoffTaskType>("剧情分析");
  const selectedAi = AI_WEB_PROVIDERS[aiProvider];

  const selectedNovel = useMemo(() => novels.find((item) => String(item.id) === novelId), [novels, novelId]);
  const selectedChapter = useMemo(() => chapters.find((item) => String(item.chapter_number) === chapterNumber), [chapters, chapterNumber]);

  useEffect(() => {
    request<{ items: Novel[] }>("/api/novels")
      .then((data) => {
        setNovels(data.items || []);
        if (data.items?.length) setNovelId(String(data.items[0].id));
      })
      .catch((error) => setMessage(error.message));
  }, []);

  useEffect(() => {
    if (!novelId) { setChapters([]); setChapterNumber(""); setProposals([]); setHandoff(null); return; }
    request<{ items: Chapter[] }>(`/api/novels/${novelId}/chapters`)
      .then((data) => { setChapters(data.items || []); setChapterNumber(data.items?.length ? String(data.items[0].chapter_number) : ""); setSelectedChapterNumbers(data.items?.length ? [String(data.items[0].chapter_number)] : []); })
      .catch((error) => setMessage(error.message));
    void loadProposals(novelId, filter);
    void loadAttachments(novelId);
    void loadWebAiFiles(novelId);
  }, [novelId]);

  async function loadProposals(id = novelId, status = filter) {
    if (!id) return;
    try {
      const data = await request<{ items: Proposal[] }>(`/api/ai-collaboration/novels/${id}/proposals?status=${encodeURIComponent(status)}`);
      setProposals(data.items || []);
    } catch (error) { setMessage(error instanceof Error ? error.message : "读取待审核提议失败"); }
  }

  async function loadAttachments(id = novelId) {
    if (!id) return;
    try {
      const data = await request<{ items: ResultAttachment[] }>(`/api/ai-collaboration/results/attachments?novel_id=${id}&status=all`);
      setAttachments(data.items || []);
    } catch (error) { setMessage(error instanceof Error ? error.message : "读取 AI 结果文件失败"); }
  }

  async function loadWebAiFiles(id = novelId) {
    if (!id) { setWebAiFiles([]); return; }
    try {
      const data = await request<{ items: WebAIResultFile[] }>(`/api/web-ai/files?novel_id=${id}&limit=200`);
      setWebAiFiles(data.items || []);
      setWebAiClassifications((current) => {
        const next = { ...current };
        for (const item of data.items || []) if (!next[item.id]) next[item.id] = item.classification || "other";
        return next;
      });
      setWebAiAssetNames((current) => {
        const next = { ...current };
        for (const item of data.items || []) if (!next[item.id]) next[item.id] = item.filename.replace(/\\.[^.]+$/, "");
        return next;
      });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "读取内嵌 AI 浏览器下载结果失败");
    }
  }

  async function confirmWebAiFile(item: WebAIResultFile) {
    const classification = webAiClassifications[item.id] || item.classification || "other";
    const needsAsset = ["character", "scene", "prop", "weapon", "costume", "building", "special_item"].includes(classification);
    const assetName = (webAiAssetNames[item.id] || "").trim();
    if (needsAsset && !assetName) { setMessage("请先填写素材名称，再确认归档。"); return; }
    if (!window.confirm("确认将“" + item.filename + "”按“" + classification + "”归档？确认后会写入本地素材库，并按任务章节尝试关联分集和分镜。")) return;
    setBusy(true);
    try {
      await request(`/api/web-ai/files/${item.id}/classification`, {
        method: "PATCH",
        body: JSON.stringify({
          classification,
          assetName,
          description: "来自 " + item.taskTitle + "（" + item.toolId + "）",
          novelId: item.novelId || Number(novelId),
          chapterId: item.chapterId || null,
        }),
      });
      setMessage("文件已确认归档；素材关联已更新。");
      await loadWebAiFiles();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "文件归档失败");
    } finally {
      setBusy(false);
    }
  }

  async function confirmSelectedWebAiFiles() {
    const selected = webAiFiles.filter((item) => selectedWebAiFileIds.includes(item.id) && item.classificationStatus !== "confirmed");
    if (!selected.length) { setMessage("请先选择待归档文件。"); return; }
    const invalid = selected.find((item) => {
      const category = webAiClassifications[item.id] || item.classification || "other";
      return ["character", "scene", "prop", "weapon", "costume", "building", "special_item"].includes(category)
        && !(webAiAssetNames[item.id] || "").trim();
    });
    if (invalid) { setMessage("请先为所有人物、场景、道具等视觉素材填写名称：" + invalid.filename); return; }
    if (!window.confirm("确认批量归档选中的 " + selected.length + " 个文件？系统会逐个按当前分类写入本地素材库。")) return;
    setBusy(true);
    let succeeded = 0;
    const failures: string[] = [];
    try {
      for (const item of selected) {
        try {
          const classification = webAiClassifications[item.id] || item.classification || "other";
          await request(`/api/web-ai/files/${item.id}/classification`, {
            method: "PATCH",
            body: JSON.stringify({
              classification,
              assetName: (webAiAssetNames[item.id] || "").trim(),
              description: "来自 " + item.taskTitle + "（" + item.toolId + "）",
              novelId: item.novelId || Number(novelId),
              chapterId: item.chapterId || null,
            }),
          });
          succeeded += 1;
        } catch (error) {
          failures.push(item.filename + "：" + (error instanceof Error ? error.message : "归档失败"));
        }
      }
      setSelectedWebAiFileIds([]);
      setMessage("批量归档完成：成功 " + succeeded + " 个，失败 " + failures.length + " 个。" + (failures.length ? " " + failures.slice(0, 3).join("；") : ""));
      await loadWebAiFiles();
    } finally {
      setBusy(false);
    }
  }

  async function uploadAttachment() {
    if (!novelId || !selectedFile) { setMessage("请先选择小说和 AI 返回的文件。"); return; }
    if (!window.confirm("确认把这个文件导入本地待审核区？文件不会发送到第三方，审核通过后才加入正式媒体素材库。")) return;
    setBusy(true);
    try {
      const form = new FormData();
      form.append("novel_id", novelId);
      if (attachmentChapter) form.append("chapter_number", attachmentChapter);
      form.append("confirm_import", "true");
      form.append("description", attachmentDescription);
      form.append("source_task_key", `web-ai-${novelId}-${attachmentChapter || "general"}`);
      form.append("file", selectedFile);
      const response = await fetch(API + "/api/ai-collaboration/results/attachments", { method: "POST", body: form });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "文件导入失败");
      setMessage(data.message || "文件已进入待审核区。");
      setSelectedFile(null);
      setAttachmentDescription("");
      await loadAttachments();
    } catch (error) { setMessage(error instanceof Error ? error.message : "文件导入失败"); }
    finally { setBusy(false); }
  }

  async function reviewAttachment(id: number, action: "approve" | "reject") {
    const note = window.prompt(action === "approve" ? "确认将该文件加入正式媒体素材库？可填写备注：" : "拒绝原因（可留空）：", "") ?? null;
    if (note === null) return;
    setBusy(true);
    try {
      const data = await request<{ message: string }>(`/api/ai-collaboration/results/attachments/${id}/review`, {
        method: "POST", body: JSON.stringify({ action, note }),
      });
      setMessage(data.message);
      await loadAttachments();
    } catch (error) { setMessage(error instanceof Error ? error.message : "文件审核失败"); }
    finally { setBusy(false); }
  }

  function openSelectedAiWebsite(existingTab?: Window | null) {
    if (existingTab && !existingTab.closed) {
      existingTab.location.assign(selectedAi.url);
      return;
    }
    window.open(selectedAi.url, "_blank", "noopener,noreferrer");
  }

  const FULL_WORKFLOW_TASKS: HandoffTaskType[] = ["剧情分析", "剧本改编", "分镜设计", "角色设定", "场景设定", "道具设定", "对白整理"];

  async function prepareFullWorkflow() {
    if (!novelId || selectedChapterNumbers.length === 0) { setMessage("请至少选择一个章节。"); return; }
    if (!window.aiBrowserDesktop?.openTask || !window.aiBrowserDesktop?.fillPrompt) { setMessage("全流程自动填入需要在 AI 漫剧大师电脑版内运行。"); return; }
    if (selectedChapterNumbers.length > 5) { setMessage("为避免一次打开过多网页，请每次最多选择 5 个章节。"); return; }
    const taskCount = selectedChapterNumbers.length * FULL_WORKFLOW_TASKS.length;
    const approved = window.confirm("将为 " + selectedChapterNumbers.length + " 个章节准备 " + taskCount + " 项任务（剧情、剧本、分镜、角色、场景、道具、对白），并在内嵌 AI 浏览器中逐项填入。每项仍需你检查后手动发送；不会自动提交或调用视频 API。继续吗？");
    if (!approved) return;
    setBusy(true); setBatchProgress("正在创建全流程任务…");
    let completed = 0;
    const failures: string[] = [];
    try {
      for (const chapterNo of selectedChapterNumbers) {
        for (const task of FULL_WORKFLOW_TASKS) {
          setBatchProgress("正在准备第 " + chapterNo + " 章 · " + task + "（" + (completed + failures.length + 1) + "/" + taskCount + "）");
          try {
            const data = await request<Handoff>("/api/ai-collaboration/novels/" + novelId + "/chapters/" + chapterNo + "/handoff-package", { method: "POST", body: JSON.stringify({ confirm_selected_chapter: true, include_confirmed_analysis: true, task_type: task }) });
            const taskId = "task-" + crypto.randomUUID();
            const providerKey = providerRoutes[task];
            const routedProvider = AI_WEB_PROVIDERS[providerKey];
            const prompt = [data.instruction, visualPromptRequirements(visualStyle, task), "【结构化输出规范】", JSON.stringify(data.output_schema, null, 2), "【软件交接资料】", JSON.stringify(data.context, null, 2), "【重要】请只返回符合结构规范的结果；剧情变化必须引用章节原文证据，不得把推断当事实。"].join("\n\n");
            const selectedChapterForTask = chapters.find((item) => String(item.chapter_number) === chapterNo);
            const taskCategory = task === "分镜设计" ? "storyboard" : (task === "角色设定" || task === "场景设定" || task === "道具设定") ? "image" : "text";
            await request("/api/web-ai/tasks", { method: "POST", body: JSON.stringify({
              id: taskId, title: "第" + chapterNo + "章 · " + task, category: taskCategory,
              toolId: providerKey, status: "prepared", prompt, sourceText: "第" + chapterNo + "章 · " + task,
              novelId: Number(novelId), chapterId: selectedChapterForTask?.id ?? null,
            }) });
            await window.aiBrowserDesktop.openTask(taskId, routedProvider.url);
            await window.aiBrowserDesktop.fillPrompt(taskId, prompt);
            completed += 1;
          } catch (error) { failures.push("第 " + chapterNo + " 章「" + task + "」：" + (error instanceof Error ? error.message : "失败")); }
        }
      }
      setMessage("全流程任务准备结束：成功填入 " + completed + "/" + taskCount + " 项。" + (failures.length ? "失败项：" + failures.join("；") : "所有任务均已填入对应 AI 网页。") + "请逐个检查网页并手动点击发送。");
    } finally { setBusy(false); setBatchProgress(""); }
  }

  async function prepareHandoff() {
    if (!novelId || !chapterNumber) { setMessage("请先选择小说和章节。"); return; }
    if (!window.confirm(`即将读取“${selectedNovel?.title || "所选小说"}”第 ${chapterNumber} 章，并将章节正文及已确认分析摘要展示在交接预览中。只会打开所选 AI 网站，不会自动发送章节内容。继续吗？`)) return;
    // Open a blank tab synchronously inside the click gesture so popup blockers
    // do not block the later navigation after the chapter request completes.
    const aiTab = autoOpenAi && !(window.aiBrowserDesktop?.openTask && window.aiBrowserDesktop?.fillPrompt) ? window.open("about:blank", "_blank") : null;
    if (aiTab) aiTab.opener = null;
    setBusy(true); setMessage("正在从本地数据库准备章节交接包……");
    try {
      const data = await request<Handoff>(`/api/ai-collaboration/novels/${novelId}/chapters/${chapterNumber}/handoff-package`, {
        method: "POST", body: JSON.stringify({ confirm_selected_chapter: true, include_confirmed_analysis: true, task_type: taskType }),
      });
      setHandoff(data);
      let openNotice = "自动打开已关闭，可稍后手动打开 AI 网页。";
      if (autoOpenAi && window.aiBrowserDesktop?.openTask && window.aiBrowserDesktop?.fillPrompt) {
        const taskId = "task-" + crypto.randomUUID();
        try {
          const prompt = [data.instruction, visualPromptRequirements(visualStyle, taskType), "【结构化输出规范】", JSON.stringify(data.output_schema, null, 2), "【软件交接资料】", JSON.stringify(data.context, null, 2), "【重要】请只返回符合结构规范的 JSON。剧情变化必须逐字引用章节证据，不得把推断当事实。"].join("\n\n");
          const taskCategory = taskType === "分镜设计" ? "storyboard" : (taskType === "角色设定" || taskType === "场景设定" || taskType === "道具设定") ? "image" : "text";
          await request("/api/web-ai/tasks", { method: "POST", body: JSON.stringify({
            id: taskId, title: "第" + chapterNumber + "章 · " + taskType, category: taskCategory,
            toolId: aiProvider, status: "prepared", prompt, sourceText: "第" + chapterNumber + "章 · " + taskType,
            novelId: Number(novelId), chapterId: selectedChapter?.id ?? null,
          }) });
          await window.aiBrowserDesktop.openTask(taskId, selectedAi.url);
          await window.aiBrowserDesktop.fillPrompt(taskId, prompt);
          openNotice = "已在软件内打开" + selectedAi.label + "并填入交接包。请检查后手动点击发送。";
        } catch (fillError) { openNotice = "网页自动填入未完成，请使用复制按钮手动粘贴：" + (fillError instanceof Error ? fillError.message : "未知错误"); }
      } else if (aiTab && !aiTab.closed) {
        aiTab.location.assign(selectedAi.url);
        openNotice = "已打开" + selectedAi.label + "网页，请使用复制按钮手动粘贴并提交。";
      } else if (autoOpenAi) { openNotice = "浏览器拦截了自动打开，请点击打开网页按钮。"; }
      setMessage((data.truncated ? "交接包已准备，但章节正文已按长度上限截断，请先确认。" : "交接包已准备。") + openNotice);
    } catch (error) {
      if (aiTab && !aiTab.closed) aiTab.close();
      setMessage(error instanceof Error ? error.message : "交接包准备失败");
    } finally { setBusy(false); }
  }

  const handoffText = handoff ? [
    handoff.instruction,
    visualPromptRequirements(visualStyle, taskType),
    "【结构化输出规范】",
    JSON.stringify(handoff.output_schema, null, 2),
    "【软件交接资料】",
    JSON.stringify(handoff.context, null, 2),
    "【重要】请只返回符合结构规范的 JSON。剧情变化必须逐字引用章节证据，不得把推断当事实。",
  ].join("\n\n") : "";

  async function copyHandoff() {
    if (!handoffText) return;
    try { await navigator.clipboard.writeText(handoffText); setMessage(`交接内容已复制。请切换到${selectedAi.label}网页手动粘贴并提交；不会自动上传或点击生成。`); }
    catch { setMessage("浏览器不允许自动复制，请选中预览内容手动复制。"); }
  }

  async function importResult() {
    if (!novelId || !chapterNumber || !resultJson.trim()) return;
    setBusy(true);
    try {
      let parsed: unknown = null;
      try { parsed = JSON.parse(resultJson); } catch { /* Markdown/plain text is also accepted and archived. */ }
      const proposalsToImport = Array.isArray(parsed) ? parsed : (
        parsed && typeof parsed === "object" && Array.isArray((parsed as { proposals?: unknown }).proposals)
          ? (parsed as { proposals: unknown[] }).proposals : null
      );
      if (proposalsToImport?.length) {
        const data = await request<{ created_count: number; duplicates_skipped: number; message: string }>(
          `/api/ai-collaboration/novels/${novelId}/proposals/import`,
          { method: "POST", body: JSON.stringify({
            novel_id: Number(novelId),
            source_text: handoff?.context ? (handoff.context as { chapter?: { content?: string } }).chapter?.content || "selected chapter" : "selected chapter",
            source_task_key: `web-ai-${novelId}-chapter-${chapterNumber}`,
            proposals: proposalsToImport,
          }) },
        );
        const archived = await request<{ message: string }>(
          "/api/ai-collaboration/results/text",
          { method: "POST", body: JSON.stringify({
            novel_id: Number(novelId), chapter_number: Number(chapterNumber),
            content: resultJson, source_task_key: `web-ai-${novelId}-chapter-${chapterNumber}`,
          }) },
        );
        setMessage(`已导入 ${data.created_count} 项待审核剧情变化；跳过重复项 ${data.duplicates_skipped} 项。完整 AI 返回内容也已归档：${archived.message}`);
        await Promise.all([loadProposals(), loadAttachments()]);
      } else {
        const data = await request<{ message: string; detected_type: string; duplicate?: boolean }>(
          "/api/ai-collaboration/results/text",
          { method: "POST", body: JSON.stringify({
            novel_id: Number(novelId), chapter_number: Number(chapterNumber),
            content: resultJson, source_task_key: `web-ai-${novelId}-chapter-${chapterNumber}`,
          }) },
        );
        setMessage(data.message);
        await loadAttachments();
      }
      setResultJson("");
    } catch (error) { setMessage(error instanceof Error ? error.message : "结果导入失败"); }
    finally { setBusy(false); }
  }

  async function compareSelectedResults() {
    if (compareIds.length !== 2) { setMessage("请选择两份 AI 文本结果进行对比。"); return; }
    setBusy(true);
    try {
      const [left, right] = await Promise.all(compareIds.map(async id => {
        const response = await fetch(`${API}/api/ai-collaboration/results/attachments/${id}/file`);
        if (!response.ok) throw new Error("读取对比文件失败");
        return response.text();
      }));
      setComparison({ left, right });
      setMessage("已加载两份 AI 结果，可并排检查差异；合并草稿需要你确认和编辑。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "结果对比失败"); }
    finally { setBusy(false); }
  }

  function createMergedDraft() {
    if (!comparison) return;
    setResultJson(`# AI 结果合并草稿\n\n## 结果 A\n\n${comparison.left}\n\n---\n\n## 结果 B\n\n${comparison.right}\n\n---\n\n请人工检查、去重并整合以上内容。\n`);
    setMessage("已将两份结果合并到上方结果输入框作为草稿。请检查、编辑后再保存；不会自动覆盖原结果。");
    setComparison(null);
  }

  async function reviewProposal(id: number, action: "approve" | "reject") {
    const note = window.prompt(action === "approve" ? "确认审核通过？可填写备注：" : "请填写拒绝原因（可留空）：", "") ?? null;
    if (note === null) return;
    setBusy(true);
    try {
      const data = await request<{ message?: string }>(`/api/ai-collaboration/proposals/${id}/review`, {
        method: "POST", body: JSON.stringify({ action, note }),
      });
      setMessage(data.message || (action === "approve" ? "已审核通过。" : "已拒绝该提议。"));
      await loadProposals();
    } catch (error) { setMessage(error instanceof Error ? error.message : "审核操作失败"); }
    finally { setBusy(false); }
  }

  return (
    <main className="cosmic min-h-screen px-4 py-8 text-white">
      <div className="mx-auto max-w-6xl space-y-6">
        <header>
          <p className="text-xs font-semibold tracking-[0.25em] text-cyan-300">DESKTOP · WEB AI COLLABORATION</p>
          <h1 className="mt-2 text-3xl font-bold">网页版 AI 协作执行中心</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">软件负责小说、人物、素材和数据库；网页 AI 负责分析与生成。所有交接都由你确认，结果经过证据校验和人工审核后才进入正式素材状态。</p>
        </header>

        {message && <div className="rounded-xl border border-cyan-300/20 bg-cyan-950/30 px-4 py-3 text-sm text-cyan-100">{message}</div>}

        <section className="rounded-2xl border border-purple-300/20 bg-slate-900/80 p-5">
          <h2 className="text-lg font-semibold">① 选择小说与章节，准备 AI 交接包</h2>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <label className="text-sm text-slate-300">小说
              <select value={novelId} onChange={(event) => { setNovelId(event.target.value); setHandoff(null); }} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-3 text-white">
                <option value="">选择小说</option>{novels.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
              </select>
            </label>
            <label className="text-sm text-slate-300">章节
              <select value={chapterNumber} onChange={(event) => { setChapterNumber(event.target.value); setHandoff(null); }} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-3 text-white">
                <option value="">选择章节</option>{chapters.map((item) => <option key={item.id} value={item.chapter_number}>第 {item.chapter_number} 章 · {item.title}（{item.word_count} 字）</option>)}
              </select>
            </label>
          </div>
          <div className="mt-4 rounded-xl border border-cyan-300/15 bg-slate-950/60 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div><h3 className="text-sm font-semibold text-cyan-100">全流程章节任务</h3><p className="mt-1 text-xs text-slate-400">选择多个章节，一次准备剧情分析、剧本改编、分镜设计、角色设定、场景设定、道具设定和对白整理任务。</p></div>
              <div className="flex gap-2 text-xs"><button type="button" onClick={() => setSelectedChapterNumbers(chapters.map((item) => String(item.chapter_number)))} className="rounded border border-white/15 px-2 py-1">全选</button><button type="button" onClick={() => setSelectedChapterNumbers([])} className="rounded border border-white/15 px-2 py-1">清空</button></div>
            </div>
            <div className="mt-3 grid max-h-44 gap-2 overflow-y-auto sm:grid-cols-2 lg:grid-cols-3">
              {chapters.map((chapter) => <label key={chapter.id} className="flex items-center gap-2 rounded-lg border border-white/10 px-3 py-2 text-xs text-slate-200"><input type="checkbox" checked={selectedChapterNumbers.includes(String(chapter.chapter_number))} onChange={(event) => setSelectedChapterNumbers((current) => event.target.checked ? [...current, String(chapter.chapter_number)] : current.filter((value) => value !== String(chapter.chapter_number)))} className="accent-cyan-500" />第 {chapter.chapter_number} 章 · {chapter.title}</label>)}
            </div>
            <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              <label className="text-xs text-slate-300">全流程图片风格
                <select value={visualStyle} onChange={(event) => setVisualStyle(event.target.value as VisualStyle)} className="mt-1 w-full rounded-lg border border-cyan-300/30 bg-slate-950 px-3 py-2 text-white">
                  <option value="国漫">国漫</option><option value="仿真人">仿真人</option><option value="3D漫">3D漫</option><option value="2D漫">2D漫</option>
                </select>
                <span className="mt-1 block text-[11px] text-slate-500">自动写入剧本、分镜、角色图、场景图、道具图相关提示词。</span>
              </label>
              {FULL_WORKFLOW_TASKS.map((task) => <label key={task} className="text-xs text-slate-300">{task}分配给
                <select value={providerRoutes[task]} onChange={(event) => setProviderRoutes((current) => ({ ...current, [task]: event.target.value as AIWebProvider }))} className="mt-1 w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-2 text-white">
                  {Object.entries(AI_WEB_PROVIDERS).map(([key, provider]) => <option key={key} value={key}>{provider.label}</option>)}
                </select>
              </label>)}
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <button type="button" onClick={() => void prepareFullWorkflow()} disabled={busy || selectedChapterNumbers.length === 0} className="rounded-lg bg-gradient-to-r from-cyan-700 to-blue-700 px-4 py-2 text-sm font-semibold disabled:opacity-40">为选中的章节生成全流程任务</button>
              <span className="text-xs text-slate-400">最多同时选择 5 章；每章 7 项任务，并按所选画风生成视觉提示词。网页支持时自动填入，但必须由你确认发送。</span>
            </div>
            {batchProgress && <p className="mt-2 text-xs text-cyan-200">{batchProgress}</p>}
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-3 md:items-end">
            <label className="text-sm text-slate-300">本次交接任务类型
              <select value={taskType} onChange={(event) => { setTaskType(event.target.value as HandoffTaskType); setHandoff(null); }} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-3 text-white">
                <option value="剧情分析">剧情分析</option><option value="剧本改编">剧本改编</option><option value="分镜设计">分镜设计</option><option value="角色设定">角色设定</option><option value="场景设定">场景设定</option><option value="道具设定">道具设定</option><option value="对白整理">对白整理</option>
              </select>
            </label>
            <label className="text-sm text-slate-300">读取章节后打开哪个 AI 网页？
              <select value={aiProvider} onChange={(event) => setAiProvider(event.target.value as AIWebProvider)} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-3 text-white">
                {Object.entries(AI_WEB_PROVIDERS).map(([key, provider]) => <option key={key} value={key}>{provider.label}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-2 rounded-lg border border-white/10 bg-slate-950 px-4 py-3 text-sm text-slate-200">
              <input type="checkbox" checked={autoOpenAi} onChange={(event) => setAutoOpenAi(event.target.checked)} className="h-4 w-4 accent-cyan-500" />
              读取完成后自动打开 AI 网页
            </label>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button onClick={() => void prepareHandoff()} disabled={busy || !novelId || !chapterNumber} className="rounded-lg bg-gradient-to-r from-violet-600 to-cyan-700 px-5 py-3 text-sm font-semibold disabled:opacity-40">{busy ? "处理中…" : "读取章节并打开 AI 网页"}</button>
            <span className="text-xs text-slate-400">只读取所选章节和已确认分析摘要；AI 网页在独立浏览器标签页打开，不会接管或关闭 AI 漫剧大师。</span>
          </div>
          {handoff && <div className="mt-5 space-y-3">
            <div className="flex flex-wrap gap-3 text-xs text-slate-300">
              <span>章节字数：{handoff.content_chars.toLocaleString()}</span>
              <span>正文截断：{handoff.truncated ? "是，请复核" : "否"}</span>
              <span>第三方发送：{handoff.third_party_transfer_started ? "已开始" : "未发送"}</span>
            </div>
            <div className="rounded-lg border border-amber-300/20 bg-amber-950/20 p-3 text-xs text-amber-100">{handoff.notice}</div>
            <textarea readOnly value={handoffText} rows={14} className="w-full rounded-xl border border-white/10 bg-slate-950 p-3 text-xs leading-5 text-slate-200" />
            <div className="flex flex-wrap gap-2">
              <button onClick={() => void copyHandoff()} className="rounded-lg border border-cyan-300/30 px-4 py-2 text-sm text-cyan-100">复制完整交接包</button>
              <button onClick={() => openSelectedAiWebsite()} className="rounded-lg border border-purple-300/30 px-4 py-2 text-sm text-purple-100">打开{selectedAi.label}网页</button>
            </div>
            <p className="text-xs leading-5 text-slate-400">软件与 AI 网站相互独立：关闭 AI 网页不会关闭、退出或中断 AI 漫剧大师。章节内容不会自动发送，需你手动复制、粘贴并提交。</p>
          </div>}
        </section>

        <section className="rounded-2xl border border-purple-300/20 bg-slate-900/80 p-5">
          <h2 className="text-lg font-semibold">② 接收 AI 返回的剧情分析与变化提议</h2>
          <p className="mt-2 text-xs leading-5 text-slate-400">请把网页 AI 返回的 JSON、Markdown 或普通文本粘贴到这里。系统会自动识别剧本、分镜、角色、场景、对白或综合分析；剧情变化提议仍会逐字核对章节原文。普通文本结果会保存为关联当前小说和章节的待审核文档。</p>
          <textarea value={resultJson} onChange={(event) => setResultJson(event.target.value)} rows={12} placeholder={'粘贴 AI 返回的 JSON、Markdown 或普通文本；含 proposals 数组的结果会进入剧情变化审核'} className="mt-3 w-full rounded-xl border border-white/10 bg-slate-950 p-3 font-mono text-xs leading-5 text-white" />
          <button onClick={() => void importResult()} disabled={busy || !resultJson.trim() || !novelId} className="mt-3 rounded-lg bg-emerald-700 px-5 py-3 text-sm font-semibold disabled:opacity-40">自动识别类型并保存 / 导入</button>
        </section>

        <section className="rounded-2xl border border-purple-300/20 bg-slate-900/80 p-5">
          <h2 className="text-lg font-semibold">③ 接收图片、视频、音频和文档文件</h2>
          <p className="mt-2 text-xs leading-5 text-slate-400">可导入网页版 AI 生成的图片、视频、音频、字幕、TXT、MD、JSON 和 PDF。文件先进入待审核区，审核通过后才加入正式媒体素材库。</p>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <label className="text-sm text-slate-300">关联章节（可选）
              <select value={attachmentChapter} onChange={event => setAttachmentChapter(event.target.value)} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-3 text-white">
                <option value="">不指定章节</option>
                {chapters.map(item => <option key={item.id} value={item.chapter_number}>第 {item.chapter_number} 章 · {item.title}</option>)}
              </select>
            </label>
            <label className="text-sm text-slate-300">选择 AI 返回文件
              <input type="file" accept="image/png,image/jpeg,image/webp,image/gif,video/mp4,video/quicktime,video/x-matroska,video/webm,audio/mpeg,audio/wav,audio/mp4,audio/aac,audio/ogg,.srt,.vtt,.txt,.md,.json,.pdf" onChange={event => setSelectedFile(event.target.files?.[0] || null)} className="mt-2 block w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-2 text-xs text-white" />
            </label>
          </div>
          <input value={attachmentDescription} onChange={event => setAttachmentDescription(event.target.value)} placeholder="文件说明（可选，例如：主角第三版正面设定图）" className="mt-3 w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-3 text-sm text-white" />
          <div className="mt-3 flex flex-wrap items-center gap-3">
            {selectedFile && <span className="text-xs text-cyan-200">{selectedFile.name} · {(selectedFile.size / 1024 / 1024).toFixed(1)} MB</span>}
            <button disabled={busy || !selectedFile || !novelId} onClick={() => void uploadAttachment()} className="rounded-lg bg-cyan-700 px-5 py-3 text-sm font-semibold disabled:opacity-40">确认导入待审核区</button>
            <button onClick={() => void loadAttachments()} className="rounded-lg border border-white/15 px-4 py-3 text-sm">刷新文件</button>
          </div>
          <div className="mt-5 space-y-2">
            {attachments.map(item => <article key={item.id} className="rounded-xl border border-white/10 bg-slate-950/70 p-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="break-all text-sm font-medium">{item.original_filename}</p>
                  <p className="mt-1 text-xs text-slate-400">{item.media_type} · {(item.file_size / 1024 / 1024).toFixed(1)} MB · {item.chapter_number ? `第 ${item.chapter_number} 章` : "未指定章节"}</p>
                  <p className="mt-1 break-all text-[10px] text-slate-500">SHA-256：{item.sha256}</p>
                  <a href={`${API}/api/ai-collaboration/results/attachments/${item.id}/file`} target="_blank" rel="noreferrer" className="mt-2 inline-block text-xs text-cyan-300 underline">预览 / 打开文件</a>
                  {item.description && <p className="mt-1 text-xs text-slate-300">{item.description}</p>}
                  {item.media_file_id && <p className="mt-1 text-xs text-emerald-300">媒体素材 ID：{item.media_file_id}</p>}
                  {item.media_type === "document" && /\.(json|md)$/i.test(item.original_filename) && <button onClick={() => setCompareIds(current => current.includes(item.id) ? current.filter(id => id !== item.id) : current.length < 2 ? [...current, item.id] : [current[1], item.id])} className={`mt-2 rounded-md border px-3 py-1.5 text-xs ${compareIds.includes(item.id) ? "border-cyan-300 bg-cyan-950 text-cyan-100" : "border-white/15 text-slate-300"}`}>{compareIds.includes(item.id) ? "已加入对比" : "加入结果对比"}</button>}
                </div>
                <span className={`rounded-full px-2 py-1 text-[10px] ${item.status === "approved" ? "bg-emerald-900/50 text-emerald-200" : item.status === "rejected" ? "bg-red-900/50 text-red-200" : "bg-amber-900/50 text-amber-200"}`}>{item.status === "approved" ? "已入库" : item.status === "rejected" ? "已拒绝" : "待审核"}</span>
              </div>
              {item.status === "pending_review" && <div className="mt-3 flex gap-2">
                <button disabled={busy} onClick={() => void reviewAttachment(item.id, "approve")} className="rounded-lg bg-emerald-700 px-3 py-2 text-xs disabled:opacity-40">审核通过并入库</button>
                <button disabled={busy} onClick={() => void reviewAttachment(item.id, "reject")} className="rounded-lg border border-red-300/30 px-3 py-2 text-xs text-red-200 disabled:opacity-40">拒绝</button>
              </div>}
            </article>)}
            {!attachments.length && <p className="rounded-xl border border-dashed border-white/15 p-5 text-center text-xs text-slate-400">还没有导入 AI 结果文件。</p>}
          </div>
          <div className="mt-4 rounded-xl border border-cyan-300/20 bg-slate-950/60 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div><h3 className="text-sm font-semibold text-cyan-100">多 AI 结果对比与合并</h3><p className="mt-1 text-xs text-slate-400">从上方已保存的 JSON / Markdown 文档中选择两份，查看并排内容，再生成可编辑的合并草稿。</p></div>
              <button disabled={busy || compareIds.length !== 2} onClick={() => void compareSelectedResults()} className="rounded-lg border border-cyan-300/30 px-4 py-2 text-xs text-cyan-100 disabled:opacity-40">对比所选结果（{compareIds.length}/2）</button>
            </div>
            {comparison && <div className="mt-4 grid gap-3 md:grid-cols-2">
              <label className="text-xs text-slate-300">AI 结果 A<textarea readOnly value={comparison.left} rows={12} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-950 p-3 font-mono text-xs text-white" /></label>
              <label className="text-xs text-slate-300">AI 结果 B<textarea readOnly value={comparison.right} rows={12} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-950 p-3 font-mono text-xs text-white" /></label>
              <button onClick={createMergedDraft} className="rounded-lg bg-cyan-700 px-4 py-2 text-xs font-semibold md:col-span-2">生成可编辑合并草稿</button>
            </div>}
          </div>
        </section>

        <section className="rounded-2xl border border-cyan-300/20 bg-slate-900/80 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h2 className="text-lg font-semibold">④ 内嵌 AI 网页下载结果 · 图片预览与素材归档</h2><p className="mt-1 text-xs leading-5 text-slate-400">桌面端 AI 网页下载的文件会自动出现在这里。图片可直接预览；分类由你确认后才会进入视觉素材库，并关联任务所属章节。</p></div>
            <div className="flex flex-wrap gap-2">
              <button onClick={() => setSelectedWebAiFileIds(webAiFiles.filter((item) => item.classificationStatus !== "confirmed").map((item) => item.id))} className="rounded-lg border border-white/15 px-3 py-2 text-xs">全选待归档</button>
              <button disabled={busy || selectedWebAiFileIds.length === 0} onClick={() => void confirmSelectedWebAiFiles()} className="rounded-lg bg-emerald-700 px-3 py-2 text-xs font-semibold disabled:opacity-40">批量归档（{selectedWebAiFileIds.length}）</button>
              <button onClick={() => void loadWebAiFiles()} className="rounded-lg border border-cyan-300/30 px-3 py-2 text-xs text-cyan-100">刷新下载结果</button>
            </div>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {webAiFiles.map((item) => {
              const classification = webAiClassifications[item.id] || item.classification || "other";
              const needsAsset = ["character", "scene", "prop", "weapon", "costume", "building", "special_item"].includes(classification);
              const isImage = (item.contentType || "").startsWith("image/");
              return <article key={item.id} className="overflow-hidden rounded-xl border border-white/10 bg-slate-950/70">
                {isImage ? <img src={API + "/api/web-ai/files/" + item.id + "/preview"} alt={item.filename} className="h-48 w-full bg-black/40 object-contain" loading="lazy" /> : <div className="flex h-28 items-center justify-center bg-slate-950 text-4xl">{item.contentType.startsWith("video/") ? "🎬" : item.contentType.startsWith("audio/") ? "🎵" : "📄"}</div>}
                <div className="space-y-2 p-3">
                  {item.classificationStatus !== "confirmed" && <label className="flex items-center gap-2 text-xs text-cyan-200"><input type="checkbox" checked={selectedWebAiFileIds.includes(item.id)} onChange={(event) => setSelectedWebAiFileIds((current) => event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id))} className="accent-cyan-500" />加入批量归档</label>}
                  <p className="break-all text-sm font-semibold">{item.filename}</p>
                  <p className="text-[11px] text-slate-400">{item.taskTitle} · {item.toolId} · {(item.size / 1024 / 1024).toFixed(1)} MB</p>
                  <p className="text-[11px] text-slate-500">{item.classificationReason}</p>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className={item.classificationStatus === "confirmed" ? "text-xs text-emerald-300" : "text-xs text-amber-300"}>{item.classificationStatus === "confirmed" ? "已归档" : "待确认分类"}</span>
                    <a href={API + item.downloadUrl} className="text-xs text-cyan-300 underline">下载原文件</a>
                  </div>
                  {item.classificationStatus !== "confirmed" && <>
                    <select value={classification} onChange={(event) => setWebAiClassifications((current) => ({ ...current, [item.id]: event.target.value }))} className="w-full rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-xs text-white">
                      <option value="character">人物图片</option><option value="scene">场景图片</option><option value="prop">道具图片</option><option value="weapon">武器图片</option><option value="costume">服装图片</option><option value="building">建筑图片</option><option value="special_item">特殊物品图片</option><option value="storyboard">分镜图</option><option value="script">剧本</option><option value="subtitle">字幕</option><option value="video">视频</option><option value="audio">音频</option><option value="document">文档</option><option value="image">普通图片</option><option value="other">其他</option>
                    </select>
                    {needsAsset && <input value={webAiAssetNames[item.id] || ""} onChange={(event) => setWebAiAssetNames((current) => ({ ...current, [item.id]: event.target.value }))} placeholder="素材名称，例如：主角·林风·正面设定图" className="w-full rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-xs text-white" />}
                    <button disabled={busy} onClick={() => void confirmWebAiFile(item)} className="w-full rounded-lg bg-emerald-700 px-3 py-2 text-xs font-semibold disabled:opacity-40">确认分类并归档</button>
                  </>}
                  {item.visualAssetId && <p className="text-xs text-emerald-300">视觉素材 ID：{item.visualAssetId}</p>}
                </div>
              </article>;
            })}
            {!webAiFiles.length && <p className="rounded-xl border border-dashed border-white/15 p-6 text-center text-xs text-slate-400 md:col-span-2 xl:col-span-3">还没有从内嵌 AI 网页下载的文件。完成网页生成并下载后，这里会自动显示结果。</p>}
          </div>
        </section>

        <section className="rounded-2xl border border-purple-300/20 bg-slate-900/80 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-semibold">⑤ 剧情变化审核与素材状态记录</h2>
            <div className="flex gap-2">
              <select value={filter} onChange={(event) => { setFilter(event.target.value); void loadProposals(novelId, event.target.value); }} className="rounded-lg border border-white/10 bg-slate-950 px-3 py-2 text-xs">
                <option value="pending_review">待审核</option><option value="approved">已通过</option><option value="rejected">已拒绝</option><option value="all">全部</option>
              </select>
              <button onClick={() => void loadProposals()} className="rounded-lg border border-white/15 px-3 py-2 text-xs">刷新</button>
            </div>
          </div>
          <div className="mt-4 space-y-3">
            {proposals.map((item) => <article key={item.id} className="rounded-xl border border-white/10 bg-slate-950/70 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div><h3 className="font-semibold">{item.asset_name} · {item.change_type}</h3><p className="mt-1 text-xs text-slate-400">第 {item.chapter_number} 章 · {item.asset_category} · 置信度 {(item.confidence * 100).toFixed(0)}% · {item.status}</p></div>
                {item.status === "pending_review" && <div className="flex gap-2"><button disabled={busy} onClick={() => void reviewProposal(item.id, "approve")} className="rounded-lg bg-emerald-700 px-3 py-2 text-xs">通过并更新素材历史</button><button disabled={busy} onClick={() => void reviewProposal(item.id, "reject")} className="rounded-lg border border-red-300/30 px-3 py-2 text-xs text-red-200">拒绝</button></div>}
              </div>
              <p className="mt-2 text-sm text-slate-200">{item.event}</p>
              {item.appearance_after && <p className="mt-1 text-xs text-slate-300">外貌变化：{item.appearance_after}</p>}
              {item.clothing_after && <p className="mt-1 text-xs text-slate-300">服装变化：{item.clothing_after}</p>}
              {item.age_after && <p className="mt-1 text-xs text-slate-300">年龄变化：{item.age_after}</p>}
              {item.hairstyle_after && <p className="mt-1 text-xs text-slate-300">发型变化：{item.hairstyle_after}</p>}
              {item.injury_after && <p className="mt-1 text-xs text-slate-300">伤势变化：{item.injury_after}</p>}
              {item.equipment_after && <p className="mt-1 text-xs text-slate-300">装备变化：{item.equipment_after}</p>}
              {item.condition_after && <p className="mt-1 text-xs text-slate-300">道具/场景状态：{item.condition_after}</p>}
              {item.holder && <p className="mt-1 text-xs text-slate-300">持有者：{item.holder}</p>}
              <blockquote className="mt-3 border-l-2 border-cyan-400/50 pl-3 text-xs leading-5 text-cyan-100">原文证据：{item.evidence}</blockquote>
              {item.review_note && <p className="mt-2 text-xs text-slate-500">审核备注：{item.review_note}</p>}
            </article>)}
            {!proposals.length && <p className="rounded-xl border border-dashed border-white/15 p-8 text-center text-sm text-slate-400">当前筛选条件下没有剧情变化提议。</p>}
          </div>
        </section>
        <p className="text-xs leading-5 text-slate-500">说明：本页面实现章节交接、JSON 结果接收、图片/视频/音频/文档文件导入与审核，以及剧情变化审核。第三方网页的登录、上传和提交仍由用户主动完成；视频发布功能保持独立。</p>
      </div>
    </main>
  );
}
