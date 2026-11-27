"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type Novel = { id: number; title?: string; name?: string };

type Asset = {
  id: number;
  category: string;
  name: string;
  description: string;
  appearance: string;
  clothing: string;
  personality: string;
  consistency_prompt: string;
  style: string;
  image_path: string;
  confirmed: boolean;
  data?: {
    model3d_task?: { task_id?: number; status?: string };
    model3d?: { path?: string; format?: string };
    generated_views?: Record<string, { path?: string }>;
    evolution_history?: Array<{ chapter_number: number; event: string; change_type: string; appearance_after?: string; clothing_after?: string; holder?: string }>;
    current_state?: { chapter_number?: number; appearance?: string; clothing?: string; holder?: string; change_type?: string };
  };
};

type Task = {
  id: number;
  task_type: string;
  prompt: string;
  status: string;
  error: string;
  result_path: string;
};

const categories = [
  { key: "characters", label: "人物角色" },
  { key: "scenes", label: "场景环境" },
  { key: "props", label: "普通道具" },
  { key: "weapons", label: "武器与刀具" },
  { key: "costumes", label: "服装与装备" },
  { key: "buildings", label: "建筑与地标" },
  { key: "special_items", label: "特殊物品" },
];

const styles = [
  "3D动漫电影风格",
  "真人电影风格",
  "2D动漫风格",
  "国风漫画风格",
  "水墨动画风格",
  "自定义参考图风格",
];

export default function VisualStudioPage() {
  const [category, setCategory] = useState("characters");
  const [assets, setAssets] = useState<Asset[]>([]);
  const [novels, setNovels] = useState<Novel[]>([]);
  const [sourceNovelId, setSourceNovelId] = useState("");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [appearance, setAppearance] = useState("");
  const [clothing, setClothing] = useState("");
  const [personality, setPersonality] = useState("");
  const [consistency, setConsistency] = useState("");
  const [style, setStyle] = useState(styles[0]);
  const [prompt, setPrompt] = useState("");
  const [generatedPrompt, setGeneratedPrompt] = useState("");
  const [promptMode, setPromptMode] = useState("professional");
  const [selectedAsset, setSelectedAsset] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function loadNovels() {
    try {
      const response = await fetch(`${API}/api/story/novels`);
      if (response.ok) setNovels(await response.json());
    } catch {}
  }

  async function importAnalysisAssets() {
    if (!sourceNovelId) { setMessage("请先选择要同步的小说"); return; }
    setBusy(true);
    try {
      const response = await fetch(`${API}/api/visual/assets/import-analysis/${sourceNovelId}`, { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "同步失败");
      setMessage(result.message + ` 新增 ${result.created} 项，更新 ${result.updated} 项，跳过 ${result.skipped} 项。`);
      await loadAssets();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "同步失败");
    } finally { setBusy(false); }
  }

  async function loadAssets() {
    try {
      const response = await fetch(`${API}/api/visual/assets?category=${category}`);
      if (response.ok) setAssets(await response.json());
    } catch {}
  }

  async function loadTasks() {
    try {
      const response = await fetch(`${API}/api/visual/tasks`);
      if (response.ok) setTasks(await response.json());
    } catch {}
  }

  useEffect(() => {
    loadAssets();
  }, [category]);

  useEffect(() => { loadNovels(); }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const requestedCategory = params.get("category");
    const requestedNovelId = params.get("novelId");
    if (requestedCategory && categories.some((item) => item.key === requestedCategory)) setCategory(requestedCategory);
    if (requestedNovelId) setSourceNovelId(requestedNovelId);
  }, []);

  useEffect(() => {
    loadTasks();
  }, []);

  async function saveAsset() {
    if (!name.trim()) {
      setMessage("请先填写素材名称");
      return;
    }
    setBusy(true);
    try {
      const response = await fetch(`${API}/api/visual/assets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          category, name, description, appearance, clothing,
          personality, consistency_prompt: consistency, style, data: {},
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "保存失败");
      setMessage("素材设定已保存");
      setName("");
      setDescription("");
      setAppearance("");
      setClothing("");
      setPersonality("");
      setConsistency("");
      await loadAssets();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  function applySavedVisualStyle(promptText: string) {
    try {
      const raw = localStorage.getItem("ai-comic-prompt-style-library-v1");
      if (!raw) return promptText;
      const library = JSON.parse(raw) as {
        activeId?: string;
        templates?: Array<Record<string, unknown>>;
      };
      const template = library.templates?.find((item) => item.id === library.activeId);
      if (!template) return promptText;
      const fields = [
        ["整体视觉风格", template.visual],
        ["人物表现", template.character],
        ["服装与材质", template.clothing],
        ["场景环境", template.scene],
        ["镜头与构图", template.camera],
        ["光线与氛围", template.lighting],
        ["色彩体系", template.color],
        ["画质要求", template.quality],
      ] as const;
      const guidance = fields
        .filter(([, value]) => typeof value === "string" && value.trim())
        .map(([label, value]) => label + "：" + String(value).trim())
        .join("。");
      const negative = typeof template.negative === "string" ? template.negative.trim() : "";
      const marker = "已应用本地视觉风格模板：" + String(template.name || "自定义风格");
      if (promptText.includes(marker)) return promptText;
      return promptText + "\\n\\n" + marker + "。\\n" + guidance
        + (negative ? "\\n风格负面提示词：" + negative : "");
    } catch {
      return promptText;
    }
  }

  function buildPrompt() {
    const selected = assets.find((item) => item.id === selectedAsset);
    const base = selected
      ? `${selected.name}。原文设定：${selected.description}。外观：${selected.appearance}。服装/装备：${selected.clothing}。性格/气质：${selected.personality}。必须保持的固定特征：${selected.consistency_prompt}。`
      : "";
    const viewGuide = category === "characters"
      ? "生成同一角色标准三视图设定稿：正面全身、严格90度侧面全身、背面全身，三个视角必须是同一张画布内同一人物、同一身高比例、同一服装、同一发型、同一装备；中性站姿，纯净背景，不添加文字水印，不得生成三个不同人物。"
      : category === "weapons"
      ? "生成武器/刀具专业三视图：正视图、侧视图、背视图，完整展示刃口、刀背、护手、握柄、刀鞘、连接结构和材质纹理；严格保持同一件武器的比例和结构，不得擅自添加不存在的零件。"
      : category === "props" || category === "special_items"
      ? "生成物品正面、侧面、背面三视图，完整展示轮廓、尺寸比例、材质、颜色、纹理、磨损和独特标记，三个视角必须是同一件物品。"
      : category === "costumes"
      ? "生成服装装备正面、侧面、背面三视图，清晰展示剪裁、层次、系带、护甲、配饰、材质和纹样；服装结构必须一致。"
      : category === "buildings"
      ? "生成建筑正立面、侧立面和整体轴测参考图，保持建筑结构、层数、屋顶、门窗、材质和比例一致。"
      : "生成电影级环境概念设定图，建立明确空间布局、地形、建筑位置、光照方向、天气、时间和可供人物行动的区域；保持可复用的场景地理结构。";
    const subject = prompt.trim() ? `用户补充要求：${prompt.trim()}。` : (name.trim() ? `素材名称：${name.trim()}。` : `请根据${category === "characters" ? "人物" : category === "scenes" ? "场景" : "道具"}类别创作一个符合项目设定的视觉资产。`);
    const continuity = category === "characters"
      ? "角色一致性锁定：固定脸型、五官间距、年龄感、肤色、发型、发色、身高体型、四肢比例、服装层次和标志性配饰；不同视角必须是同一个角色，不得换脸、换装或改变年龄。"
      : category === "scenes"
      ? "场景一致性锁定：固定地形轮廓、建筑相对位置、道路与入口、地标、空间尺度、主光方向和环境色；后续镜头不得无故改变场景布局。"
      : category === "costumes"
      ? "服装一致性锁定：固定剪裁、版型、衣长、层数、领口、袖口、扣件、纹样、配色和穿戴方式；不得在视角之间增删部件。"
      : category === "buildings"
      ? "建筑一致性锁定：固定层数、平面轮廓、屋顶形式、门窗数量与位置、立柱、檐口、材质和结构比例；不同视角必须对应同一建筑。"
      : category === "weapons"
      ? "武器一致性锁定：固定总长度、刃形、刃宽、刀背、护手、握柄、刀鞘、装饰纹路和连接结构；不得改变武器型号或凭空增加零件。"
      : category === "props" || category === "special_items"
      ? "物品一致性锁定：固定外轮廓、长宽高比例、开合结构、材质、颜色、纹理、刻痕和标志；正侧背视图必须是同一件物品。"
      : "视觉一致性锁定：固定主体轮廓、空间关系、材质、配色和标志性细节，确保后续镜头可复用。";
    const quality = promptMode === "concise"
      ? "画面主体明确，构图简洁，重点突出，避免无关元素。"
      : promptMode === "cinematic"
      ? "电影级视觉设计，强调戏剧性光影、镜头构图、色彩叙事和真实空间层次。"
      : "专业影视资产设定稿，细节完整，材质可信，结构准确，画面清晰，视觉统一。";
    const positive = `${base}${subject}。${viewGuide}${continuity}视觉风格：${style}。${quality}保持项目内视觉一致性，不添加原设定中不存在的关键特征。`;
    const negative = "负面约束：身份或外观漂移、视角间结构不一致、比例错误、额外肢体或零件、重复主体、错误服装、错误颜色、透视畸变、主体裁切、模糊、低清晰度、文字、字幕、水印、Logo、边框、无关装饰。";
    return applySavedVisualStyle(`正向提示词：${positive}\n\n${negative}`);
  }

  async function createTask() {
    const effectivePrompt = generatedPrompt.trim() || buildPrompt();
    if (!effectivePrompt.trim()) { setMessage("无法准备图片生成提示词，请补充素材描述。"); return; }
    try {
      const negativeMatch = effectivePrompt.match(/(?:负面提示词|风格负面提示词|负面约束)[：:]([\\s\\S]*)$/);
      localStorage.setItem("ai-comic-local-image-draft-v1", JSON.stringify({
        prompt: effectivePrompt,
        negative_prompt: negativeMatch?.[1]?.trim() || "低质量、模糊、畸形肢体、多余手指、文字、水印",
        style,
        asset_id: selectedAsset,
        created_at: new Date().toISOString(),
      }));
      setGeneratedPrompt(effectivePrompt);
      setMessage("提示词已交接到本地图片工作台，正在打开本机 ComfyUI 生图页面。");
      window.location.href = "/local-image-studio?from=visual-studio";
    } catch {
      setMessage("无法保存本地生图任务，请检查浏览器本地存储空间。");
    }
  }

  async function runTask(_id: number) {
    setMessage("严格零费用模式已拦截云端图片生成 API。请前往本地 ComfyUI 图片工作台生成；不会提交任何云端请求。");
  }


  async function manageTask(id: number, action: "pause" | "resume" | "retry" | "cancel") {
    setBusy(true);
    try {
      const response = await fetch(`${API}/api/visual/tasks/${id}/${action}`, { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "任务操作失败");
      setMessage(result.message || `任务 #${id} 已${({ pause: "暂停", resume: "恢复", retry: "重新排队", cancel: "取消" } as const)[action]}`);
      await loadTasks();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "任务操作失败");
      await loadTasks();
    } finally {
      setBusy(false);
    }
  }

  async function generateProfessionalPrompt(assetId: number) {
    setBusy(true);
    try {
      const response = await fetch(API + "/api/visual/assets/" + assetId + "/prompt");
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "提示词生成失败");
      setGeneratedPrompt(applySavedVisualStyle("正向提示词：" + result.positive_prompt + (prompt.trim() ? "\n\n用户补充要求：" + prompt.trim() : "") + "\n\n负面提示词：" + result.negative_prompt));
      setSelectedAsset(assetId);
      setMessage("已生成专业正向/负面提示词，可检查后再创建图片任务。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "提示词生成失败");
    } finally { setBusy(false); }
  }

  async function createViewBatch(assetId: number) {
    setBusy(true);
    try {
      const response = await fetch(API + "/api/visual/assets/" + assetId + "/views?provider=doubao", { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "创建多视角任务失败");
      setMessage(result.message);
      await loadTasks();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "创建多视角任务失败");
    } finally { setBusy(false); }
  }

  async function confirmAsset(asset: Asset) {
    if (!window.confirm("确认“" + asset.name + "”的设定已审核无误？确认后，AI 分镜和三维生成将优先复用这份素材。")) return;
    setBusy(true);
    try {
      const response = await fetch(API + "/api/visual/assets/" + asset.id + "/confirm", { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "确认素材失败");
      setMessage("素材“" + asset.name + "”已确认。");
      await loadAssets();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "确认素材失败");
    } finally { setBusy(false); }
  }

  async function generateEditable3D(_asset: Asset) {
    setMessage("云端 Tripo 3D API 已在严格零费用模式下禁用；本地 3D 生成引擎尚未集成，不会上传素材或产生费用。");
  }


  async function refreshEditable3D(asset: Asset) {
    const taskId = asset.data?.model3d_task?.task_id;
    if (!taskId) return setMessage("该素材尚无 3D 模型任务。");
    setBusy(true);
    try {
      const response = await fetch(API + "/api/visual/tasks/" + taskId + "/refresh-model3d", { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "查询 3D 模型失败");
      setMessage(result.status === "completed" ? "GLB 三维模型已下载到本地素材库。" : result.status === "failed" ? (result.error || "3D 任务失败") : "3D 模型仍在生成，当前进度：" + (result.progress ?? "未知") + "%");
      await loadAssets();
      await loadTasks();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "查询 3D 模型失败");
    } finally { setBusy(false); }
  }

  async function addEvolution(asset: Asset) {
    const chapter = window.prompt("变化发生的章节号：");
    if (!chapter || !/^[1-9]\d*$/.test(chapter)) return;
    const event = window.prompt("记录剧情事件/变化：");
    if (!event || !event.trim()) return;
    const changeType = window.prompt("变化类型（状态变化/损坏/升级/易主/外观变化）：", "状态变化") || "状态变化";
    const appearanceAfter = window.prompt("变化后的外观（可留空）：", "") || "";
    const clothingAfter = window.prompt("变化后的服装/装备（可留空）：", "") || "";
    const ageAfter = window.prompt("变化后的年龄阶段（可留空）：", "") || "";
    const hairstyleAfter = window.prompt("变化后的发型（可留空）：", "") || "";
    const injuryAfter = window.prompt("变化后的伤势/疤痕（可留空）：", "") || "";
    const equipmentAfter = window.prompt("变化后的装备/武器（可留空）：", "") || "";
    const conditionAfter = window.prompt("道具状态（损坏、修复、升级等，可留空）：", "") || "";
    const holder = window.prompt("变化后的持有人（可留空）：", "") || "";
    const evidence = window.prompt("原文依据或备注（可留空）：", "") || "";
    const confirmed = window.confirm("是否确认以上剧情变化真实发生，并将其用于后续分镜和生成提示词？选择“取消”会保存为待确认记录，不影响当前有效状态。");
    setBusy(true);
    try {
      const response = await fetch(API + "/api/visual/assets/" + asset.id + "/evolution", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chapter_number: Number(chapter), event, change_type: changeType,
          appearance_after: appearanceAfter, clothing_after: clothingAfter,
          age_after: ageAfter, hairstyle_after: hairstyleAfter, injury_after: injuryAfter,
          equipment_after: equipmentAfter, condition_after: conditionAfter,
          holder, evidence, confirmed, source: "manual",
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "记录变化失败");
      setMessage("已记录第 " + chapter + " 章变化，共 " + result.history_count + " 条。" + (confirmed ? "该变化已确认并会用于后续分镜。" : "该变化处于待确认状态，不会改变当前有效外观。"));
      await loadAssets();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "记录变化失败");
    } finally { setBusy(false); }
  }

  async function uploadReference(file?: File) {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    if (selectedAsset) form.append("asset_id", String(selectedAsset));
    try {
      const response = await fetch(`${API}/api/visual/references`, {
        method: "POST",
        body: form,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "上传失败");
      setMessage("参考图片已上传");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "上传失败");
    }
  }

  const panel = "rounded-2xl border border-purple-300/20 bg-slate-950/65 p-5 shadow-xl shadow-purple-950/20";
  const field = "w-full rounded-xl border border-purple-300/20 bg-slate-900/80 px-3 py-2.5 text-sm text-white outline-none focus:border-purple-400";
  const button = "rounded-xl border border-purple-300/30 bg-purple-600/80 px-4 py-2 text-sm font-medium text-white transition hover:bg-purple-500 disabled:opacity-50";

  return (
    <main className="min-h-screen space-y-6 p-6 text-white">
      <header>
        <a
          href="/"
          className="mb-5 inline-flex items-center gap-2 rounded-xl border border-purple-300/30 bg-purple-950/60 px-4 py-2.5 text-sm font-medium text-white transition hover:border-purple-300/70 hover:bg-purple-800/60"
        >← 返回主页</a>
        <div className="text-sm text-purple-300">AI COMIC STUDIO / VISUAL LAB</div>
        <h1 className="mt-2 text-3xl font-bold">AI视觉素材生产中心</h1>
        <p className="mt-3 rounded-xl border border-cyan-300/20 bg-cyan-950/20 p-3 text-xs text-cyan-100">严格零费用模式：云端图片与 Tripo 3D API 不会被调用。图片请使用本地 ComfyUI 工作台；本地 3D 模型生成仍待开发。</p>
        <p className="mt-2 text-sm text-slate-300">
          统一管理人物、场景、普通道具、武器刀具、服装、建筑和特殊物品，并为后续镜头生成提供统一视觉参考。
        </p>
      </header>

      {message && (
        <div className="rounded-xl border border-purple-300/20 bg-purple-950/50 p-3 text-sm text-purple-100">
          {message}
        </div>
      )}

      <section className="grid gap-3 md:grid-cols-3">
        {categories.map((item) => (
          <button
            key={item.key}
            onClick={() => { setCategory(item.key); setGeneratedPrompt(""); }}
            className={`rounded-xl border p-4 text-left transition ${
              category === item.key
                ? "border-purple-400 bg-purple-700/40"
                : "border-purple-300/20 bg-slate-950/60 hover:bg-purple-950/40"
            }`}
          >
            <div className="text-lg font-semibold">{item.label}</div>
            <div className="mt-1 text-xs text-slate-400">
              {item.key === "characters" ? "人物身份、外貌、服装、三视图与角色一致性" :
               item.key === "scenes" ? "地点布局、环境、时间、天气、氛围与场景参考图" :
               item.key === "weapons" ? "刀身、刃口、刀背、护手、握柄、刀鞘与武器结构" :
               item.key === "costumes" ? "服装剪裁、层次、护甲、配饰、材质与纹样" :
               item.key === "buildings" ? "建筑立面、屋顶、门窗、结构、材质与比例" :
               item.key === "special_items" ? "法器、神器、信物、关键线索与独特物品" :
               "普通道具外观、材质、颜色、尺寸与三视图"}
            </div>
          </button>
        ))}
      </section>

      <section className="grid gap-6 xl:grid-cols-2">
        <div className={panel}>
          <h2 className="mb-4 text-xl font-semibold">建立视觉素材设定</h2>
          <div className="space-y-3">
            <input className={field} placeholder="素材名称" value={name} onChange={(e) => setName(e.target.value)} />
            <textarea className={field} rows={2} placeholder="基础描述 / 故事背景" value={description} onChange={(e) => setDescription(e.target.value)} />
            <textarea className={field} rows={3} placeholder="外貌、外形、颜色、体型、材质等" value={appearance} onChange={(e) => setAppearance(e.target.value)} />
            <textarea className={field} rows={2} placeholder="服装、装备或表面细节（场景和道具可填写对应特征）" value={clothing} onChange={(e) => setClothing(e.target.value)} />
            <textarea className={field} rows={2} placeholder="性格、气质或视觉氛围" value={personality} onChange={(e) => setPersonality(e.target.value)} />
            <textarea className={field} rows={3} placeholder="固定特征：发型、脸型、颜色、标志物等，供后续保持一致" value={consistency} onChange={(e) => setConsistency(e.target.value)} />
            <select className={field} value={style} onChange={(e) => { setStyle(e.target.value); setGeneratedPrompt(""); }}>
              {styles.map((item) => <option key={item}>{item}</option>)}
            </select>
            <button className={button} disabled={busy} onClick={saveAsset}>保存素材设定</button>
          </div>
        </div>

        <div className={panel}>
          <h2 className="mb-4 text-xl font-semibold">AI图片生成</h2>
          <div className="space-y-3">
            <label className="block text-sm text-slate-300">关联素材</label>
            <select
              className={field}
              value={selectedAsset ?? ""}
              onChange={(e) => { setSelectedAsset(e.target.value ? Number(e.target.value) : null); setGeneratedPrompt(""); }}
            >
              <option value="">不关联素材</option>
              {assets.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
            <label className="block text-sm text-slate-300">提示词方案（自动生成并用于本次图片生成）
              <select className={field + " mt-2"} value={promptMode} onChange={(e) => { setPromptMode(e.target.value); setGeneratedPrompt(""); }}>
                <option value="professional">专业详细提示词（推荐）</option>
                <option value="cinematic">电影镜头风格提示词</option>
                <option value="concise">简洁提示词</option>
              </select>
            </label>
            <label className="block text-sm text-slate-300">用户补充要求（可选）
              <textarea
                className={field + " mt-2"}
                rows={3}
                placeholder="例如：角色表情更坚毅、场景增加薄雾、保留原著中的特殊纹样。留空时由软件根据素材设定自动生成。"
                value={prompt}
                onChange={(e) => { setPrompt(e.target.value); setGeneratedPrompt(""); }}
              />
            </label>
            <div className="rounded-xl border border-sky-300/20 bg-sky-950/20 p-3">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <strong className="text-sm text-sky-100">本次实际生成提示词预览</strong>
                <button type="button" className="rounded-lg border border-sky-300/30 px-3 py-1.5 text-xs" onClick={() => setGeneratedPrompt(buildPrompt())}>生成 / 更新预览</button>
              </div>
              <textarea className={field} rows={8} readOnly value={generatedPrompt || "创建图片任务时会自动组合完整提示词；也可点击“生成 / 更新预览”提前查看。"} />
              {generatedPrompt && <button type="button" className="mt-2 rounded-lg border border-purple-300/30 px-3 py-1.5 text-xs" onClick={() => navigator.clipboard?.writeText(generatedPrompt)}>复制实际提示词</button>}
            </div>
            <label className="flex items-center gap-2 text-sm text-slate-300">
            </label>
            <div className="flex flex-wrap gap-2">
              <button className={button} disabled={busy} onClick={createTask}>创建图片任务</button>
              <label className="cursor-pointer rounded-xl border border-purple-300/30 px-4 py-2 text-sm hover:bg-purple-950/50">
                上传参考图片
                <input type="file" accept="image/*" className="hidden" onChange={(e) => uploadReference(e.target.files?.[0])} />
              </label>
            </div>
            <p className="text-xs leading-5 text-slate-400">
              当前图片生成入口只创建本地工作流任务；云端付费 API 保持禁用，不会自动提交或产生 API 费用。
            </p>
          </div>
        </div>
      </section>

      <section className={panel}>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-xl font-semibold">当前素材库</h2>
          <div className="flex flex-wrap gap-2">
            <select className="max-w-xs rounded-xl border border-purple-300/20 bg-slate-900 px-3 py-2 text-sm text-white" value={sourceNovelId} onChange={(e) => { setSourceNovelId(e.target.value); const url = new URL(window.location.href); if (e.target.value) url.searchParams.set("novelId", e.target.value); else url.searchParams.delete("novelId"); window.history.replaceState({}, "", url.toString()); }}>
              <option value="">选择小说，同步 AI 分析资产</option>
              {novels.map((novel) => <option key={novel.id} value={novel.id}>{novel.title || novel.name || `小说 #${novel.id}`}</option>)}
            </select>
            <button className={button} disabled={busy || !sourceNovelId} onClick={importAnalysisAssets}>同步 AI 识别结果</button>
          </div>
          <button className={button} onClick={loadAssets}>刷新素材</button>
        </div>
        {assets.length === 0 ? (
          <p className="text-sm text-slate-400">当前分类还没有素材。</p>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {assets.map((item) => (
              <article key={item.id} className="rounded-xl border border-purple-300/15 bg-slate-900/70 p-4">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="font-semibold">{item.name}</h3>
                  <span className="text-xs text-slate-400">{item.confirmed ? "已确认" : "待确认"}</span>
                </div>
                <p className="mt-2 line-clamp-3 text-sm text-slate-400">{item.description || item.appearance}</p>
                <p className="mt-2 text-xs text-purple-300">{item.style}</p>
                {item.image_path && (
                  <p className="mt-2 break-all text-xs text-slate-500">图片已生成：{item.image_path}</p>
                )}
                <button
                  className="mt-3 rounded-lg border border-purple-300/20 px-3 py-1.5 text-xs hover:bg-purple-900/50"
                  onClick={() => {
                    setSelectedAsset(item.id);
                    setName(item.name);
                    setDescription(item.description || "");
                    setAppearance(item.appearance || "");
                    setClothing(item.clothing || "");
                    setPersonality(item.personality || "");
                    setConsistency(item.consistency_prompt || "");
                    setPrompt("");
                    setGeneratedPrompt("");
                  }}
                >
                  使用此素材生成图片
                </button>
                <div className="mt-3 flex flex-wrap gap-2">
                  {!item.confirmed && <button className="rounded-lg border border-amber-300/30 px-3 py-1.5 text-xs text-amber-200 hover:bg-amber-900/30" disabled={busy} onClick={() => confirmAsset(item)}>审核通过并确认素材</button>}
                  <button className="rounded-lg border border-sky-300/20 px-3 py-1.5 text-xs hover:bg-sky-900/40" disabled={busy} onClick={() => generateProfessionalPrompt(item.id)}>生成专业提示词</button>
                  {item.confirmed && (
                    <>
                      <button type="button" disabled title="严格零费用模式已禁用云端 Tripo API，本地 3D 引擎尚未集成。" className="rounded-lg border border-emerald-300/15 px-3 py-1.5 text-xs text-emerald-100/50 cursor-not-allowed">云端 3D API 已禁用</button>
                    </>
                  )}
                  {item.data?.model3d_task?.task_id && <button className="rounded-lg border border-cyan-300/30 px-3 py-1.5 text-xs text-cyan-200 hover:bg-cyan-900/30" disabled={busy} onClick={() => refreshEditable3D(item)}>查询/下载3D模型</button>}
                  {item.data?.model3d?.path && <a className="rounded-lg border border-green-300/30 px-3 py-1.5 text-xs text-green-200 hover:bg-green-900/30" href={API + "/api/visual/models3d/" + encodeURIComponent(item.data.model3d.path.split(/[\\/]/).pop() || "")} target="_blank" rel="noreferrer">下载 GLB 模型</a>}
                  <button className="rounded-lg border border-purple-300/20 px-3 py-1.5 text-xs hover:bg-purple-900/50" disabled={busy} onClick={() => createViewBatch(item.id)}>创建多视角任务</button>
                  <button className="rounded-lg border border-amber-300/20 px-3 py-1.5 text-xs hover:bg-amber-900/30" disabled={busy} onClick={() => addEvolution(item)}>记录剧情变化</button>
                  {item.data?.evolution_history?.length ? <span className="self-center text-xs text-amber-200">剧情变化 {item.data.evolution_history.length} 条</span> : null}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className={panel}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-xl font-semibold">图片生成任务队列</h2>
          <button className={button} onClick={loadTasks}>刷新任务</button>
        </div>
        <div className="space-y-3">
          {tasks.length === 0 && <p className="text-sm text-slate-400">还没有图片生成任务。</p>}
          {tasks.map((task) => (
            <div key={task.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-purple-300/15 bg-slate-900/70 p-3">
              <div className="min-w-0 flex-1">
                <div className="font-medium">任务 #{task.id} · {task.task_type}</div>
                <p className="mt-1 line-clamp-2 text-xs text-slate-400">{task.prompt}</p>
                {task.error && <p className="mt-1 text-xs text-red-300">{task.error}</p>}
              </div>
              <span className="text-xs text-purple-300">{task.status}</span>
              {task.status === "pending" && (
                <>
                  <a href="/local-image-studio" className="rounded-lg border border-cyan-300/30 bg-cyan-950/30 px-3 py-2 text-xs text-cyan-100">前往本地 ComfyUI 图片工作台</a>
                  <button className={button} disabled={busy} onClick={() => manageTask(task.id, "pause")}>暂停</button>
                  <button className={button} disabled={busy} onClick={() => manageTask(task.id, "cancel")}>取消</button>
                </>
              )}
              {task.status === "paused" && (
                <>
                  <button className={button} disabled={busy} onClick={() => manageTask(task.id, "resume")}>继续</button>
                  <button className={button} disabled={busy} onClick={() => manageTask(task.id, "cancel")}>取消</button>
                </>
              )}
              {(task.status === "failed" || task.status === "cancelled") && (
                <button className={button} disabled={busy} onClick={() => manageTask(task.id, "retry")}>重新排队</button>
              )}
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
