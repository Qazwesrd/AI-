"use client";

import { useEffect, useMemo, useState } from "react";

type Stage = "script" | "storyboard" | "image" | "video" | "voice";
type StyleGroup = "国漫" | "2D动漫" | "3D动漫" | "仿真人" | "漫画绘本" | "特殊题材";
type StyleTemplate = {
  id: string; name: string; group: StyleGroup; description: string;
  visual: string; character: string; clothing: string; scene: string;
  camera: string; lighting: string; color: string; quality: string;
  negative: string; custom: boolean;
};
type PromptSet = Record<Stage, string>;
type SavedState = { templates: StyleTemplate[]; prompts: PromptSet; activeId: string; history: Array<{ at: string; label: string; snapshot: string }> };

const STORAGE_KEY = "ai-comic-prompt-style-library-v1";
const DEFAULT_NEGATIVE = "低清晰度，模糊，噪点，过曝，欠曝，畸形肢体，多余手指，手部结构错误，五官错位，比例失真，透视错误，主体裁切，杂乱背景，重复人物，错误服装，文字，字幕，Logo，水印，边框，分格漫画";
const defaultPrompts: PromptSet = {
  script: "你是专业影视编剧。忠于原著核心设定与人物动机，将内容改编为可拍摄、节奏清晰的漫剧剧本。明确场景、人物、动作、对白和情绪，不擅自改变关键剧情。",
  storyboard: "将剧本拆分为连续且可执行的镜头。每镜只表现一个明确瞬间，写清景别、机位、镜头运动、人物位置、动作、表情、场景和转场；保持角色、服装、道具与空间连续性。",
  image: "生成一张完整、单一瞬间的高质量画面。明确主体数量与位置、人物外貌、服装材质、动作表情、环境道具、前中后景、景别视角、构图、光线与色彩。人物比例自然，空间透视正确，背景有层次且不抢主体。",
  video: "根据分镜生成连贯镜头，严格保持人物身份、服装、道具、场景和光线连续。动作自然，有明确起始状态、运动过程和结束状态；镜头运动平稳，不突然变形或跳切。",
  voice: "根据角色身份、年龄感、情绪和场景生成自然中文配音。区分角色音色与说话习惯，控制语速、停顿、重音和情绪，不添加原文没有的台词。"
};

const styleSeeds = ([
  { id:"guoman-traditional",name:"传统国风",group:"国漫",description:"东方审美、传统服饰与含蓄典雅的画面",visual:"高品质中国风动画美术，东方审美，细腻线描与精致造型",character:"符合东方人物审美，五官自然耐看，神态含蓄生动",clothing:"传统服饰结构准确，织物纹理、刺绣与配饰细节清晰",scene:"亭台楼阁、山水庭院、古典器物，空间布局有秩序",camera:"电影构图，层次分明，主体突出",lighting:"柔和自然光，适度轮廓光",color:"雅致低饱和配色，朱红、黛青、暖金协调",quality:"精致国风动画质感，细节清楚，画面统一",negative:DEFAULT_NEGATIVE },
  { id:"guoman-modern",name:"现代国漫",group:"国漫",description:"现代商业国漫，鲜明角色设计与电影感",visual:"高完成度现代中国动画电影美术风格",character:"角色设计鲜明，面部结构稳定，表情自然",clothing:"服装设计有辨识度，材质与褶皱真实",scene:"具有空间纵深的现代或幻想场景",camera:"电影级镜头语言，动态构图",lighting:"体积光与环境反射自然",color:"层次丰富、对比明确的电影调色",quality:"高精度动画电影画面，主体清晰",negative:DEFAULT_NEGATIVE },
  { id:"xianxia",name:"仙侠玄幻",group:"国漫",description:"仙山、法术、灵气与东方幻想",visual:"东方仙侠幻想动画风格，宏大而精致",character:"角色气质鲜明，神态坚定或空灵",clothing:"飘逸多层衣袍，纹饰精细，布料动态自然",scene:"仙山云海、古殿、灵脉与奇幻地貌",camera:"大场面远景结合有张力的近景",lighting:"云隙光、灵性辉光，控制特效强度",color:"青蓝、银白、暖金等协调搭配",quality:"电影级东方奇幻动画质感",negative:DEFAULT_NEGATIVE },
  { id:"wuxia",name:"武侠热血",group:"国漫",description:"写实武侠动作与江湖氛围",visual:"热血武侠国漫风格，动作设计清晰有力量",character:"骨骼比例自然，姿态有力量感",clothing:"便于动作的武侠服装，衣摆随动作合理摆动",scene:"古镇、山道、竹林、庭院等江湖场景",camera:"动作镜头清楚，方向关系连续",lighting:"自然硬光与戏剧性侧光",color:"沉稳大地色搭配重点色",quality:"高质量动作动画，动态清晰",negative:DEFAULT_NEGATIVE },
  { id:"ink",name:"水墨国风",group:"国漫",description:"水墨晕染、留白与诗意意境",visual:"中国水墨动画美学，墨色层次与笔触自然",character:"人物造型简洁优雅，轮廓清楚",clothing:"衣纹以流畅笔触表现，结构不含糊",scene:"山水留白、雾气、古树与简洁建筑",camera:"构图讲究留白与虚实关系",lighting:"柔和漫射光，墨色浓淡自然",color:"黑白灰为主，少量朱砂或青绿点缀",quality:"细腻水墨肌理，画面干净",negative:"照片写实，塑料3D质感，杂乱纹理，模糊，文字，水印，畸形结构" },
  { id:"anime-cel",name:"日系赛璐璐",group:"2D动漫",description:"清晰线稿、平涂色块与经典动画质感",visual:"高质量日系2D赛璐璐动画风格，干净线稿",character:"日系动画人物设计，五官统一，表情易读",clothing:"简洁明确的色块与褶皱线条",scene:"手绘动画背景，前中后景清晰",camera:"动画电影构图，轮廓易辨识",lighting:"清晰明暗分区与柔和高光",color:"鲜明但协调的动画配色",quality:"干净线条，稳定造型，精致上色",negative:DEFAULT_NEGATIVE },
  { id:"anime-film",name:"日系电影动画",group:"2D动漫",description:"电影级手绘背景与细腻光影",visual:"高品质日本动画电影美术，手绘质感",character:"角色造型自然，表演细腻",clothing:"材质细节丰富但不过度写实",scene:"精细手绘环境，空气透视明显",camera:"电影化景别与空间构图",lighting:"自然光影，柔和反射与空气感",color:"清透电影调色，色彩层次丰富",quality:"精细手绘动画电影画面",negative:DEFAULT_NEGATIVE },
  { id:"anime-action",name:"热血番",group:"2D动漫",description:"强烈动作张力与高对比色彩",visual:"热血动作番动画风格，线条有力度",character:"角色表情强烈，动作姿态清楚",clothing:"服装轮廓明确，动作褶皱合理",scene:"有速度感但不遮挡主体的背景",camera:"低角度、动态透视与冲击构图",lighting:"高反差戏剧光",color:"高饱和重点色与清晰明暗",quality:"动作清晰、张力强、主体不变形",negative:DEFAULT_NEGATIVE },
  { id:"anime-dark",name:"暗黑番",group:"2D动漫",description:"克制色彩、阴影与悬疑氛围",visual:"成熟暗黑系2D动画风格",character:"人物轮廓明确，神情克制而有戏",clothing:"深色服装层次清楚，材质可信",scene:"压迫感空间、雾气与环境细节",camera:"偏电影化构图，适当留白",lighting:"低照度环境光与局部轮廓光",color:"低饱和冷色，少量强调色",quality:"精致暗黑动画质感，细节可辨",negative:DEFAULT_NEGATIVE },
  { id:"3d-film",name:"电影级3D",group:"3D动漫",description:"高品质动画电影渲染与真实材质",visual:"电影级3D动画渲染，精细模型与高质量材质",character:"角色面部结构稳定，比例自然，表情生动",clothing:"PBR材质，织物、金属、皮革区分明确",scene:"完整三维空间，物体尺度与透视准确",camera:"电影摄影机语言，景深适度",lighting:"全局光照、柔和反弹光与轮廓光",color:"电影级色彩管理，层次自然",quality:"高精度3D动画电影画质",negative:DEFAULT_NEGATIVE },
  { id:"3d-real",name:"写实3D",group:"3D动漫",description:"接近真实摄影的三维动画",visual:"高写实3D动画渲染，真实物理材质",character:"自然人体结构与可信面部比例",clothing:"真实织物、皮革、金属物理属性",scene:"符合现实尺度与透视的三维环境",camera:"真实镜头焦段与景深",lighting:"物理正确的环境光与阴影",color:"自然电影调色，不塑料化",quality:"细节丰富，真实但保留动画审美",negative:DEFAULT_NEGATIVE },
  { id:"3d-cartoon",name:"卡通3D",group:"3D动漫",description:"亲和造型、圆润比例与明快色彩",visual:"高品质卡通3D动画风格，造型圆润",character:"可爱而统一的角色比例，表情清楚",clothing:"简洁材质，结构清晰",scene:"色彩明快、层次清楚的卡通环境",camera:"活泼稳定的动画构图",lighting:"柔和均匀的棚拍式光照",color:"明快、温暖、协调的配色",quality:"精致卡通3D渲染",negative:DEFAULT_NEGATIVE },
  { id:"game-cg",name:"游戏CG",group:"3D动漫",description:"高规格游戏过场动画与角色资产感",visual:"高规格游戏CG过场动画美术",character:"角色设计具有辨识度，结构精确",clothing:"精细盔甲、织物与装备材质",scene:"宏大可探索的三维场景",camera:"游戏电影过场镜头",lighting:"戏剧化环境光与体积光",color:"高对比电影级调色",quality:"高端实时渲染与电影CG质感",negative:DEFAULT_NEGATIVE },
  { id:"live-action",name:"电影写实",group:"仿真人",description:"真人电影摄影与自然皮肤质感",visual:"真人电影摄影风格，非插画非动漫",character:"真实人体比例、自然五官与皮肤纹理",clothing:"真实服装剪裁、布料纹理与自然褶皱",scene:"实景电影布景，空间尺度准确",camera:"真实摄影机焦段与景深",lighting:"电影布光，肤色自然",color:"专业电影调色，保留自然肤色",quality:"高分辨率电影剧照质感",negative:"动漫，卡通，插画，塑料皮肤，蜡像脸，过度磨皮，畸形肢体，文字，水印" },
  { id:"period-live",name:"古装真人",group:"仿真人",description:"古装影视剧质感与考究服化道",visual:"高品质古装真人影视剧画面",character:"真人演员外貌，表演自然",clothing:"符合时代设定的服饰、发饰与妆造",scene:"考究古代建筑、器物与陈设",camera:"古装电影镜头语言",lighting:"自然日光与室内实景布光",color:"沉稳雅致的古装影视调色",quality:"真实影视剧摄影质感",negative:"现代物件，动漫脸，错误朝代服饰，塑料质感，文字，水印" },
  { id:"modern-live",name:"现代都市",group:"仿真人",description:"现代都市剧与生活化摄影",visual:"现代都市真人影视摄影风格",character:"自然真实的人物与生活化表演",clothing:"符合人物身份的现代服装",scene:"真实城市街道、室内空间与生活细节",camera:"自然电影构图与真实焦段",lighting:"真实环境光与柔和补光",color:"清爽自然的现代影视调色",quality:"可信的高清真人影视画面",negative:"动漫，奇幻盔甲，错误时代道具，塑料皮肤，文字，水印" },
  { id:"webtoon",name:"韩系条漫",group:"漫画绘本",description:"清爽线条、精致人物与竖屏条漫色彩",visual:"高品质韩系彩色网络漫画风格",character:"精致人物造型，表情细腻",clothing:"时尚服装设计，线条与材质明确",scene:"干净的漫画背景与氛围细节",camera:"适合条漫叙事的清晰构图",lighting:"柔和高光与干净阴影",color:"清透柔和的彩色漫画配色",quality:"线条干净，上色细腻",negative:DEFAULT_NEGATIVE },
  { id:"comic-western",name:"欧美美漫",group:"漫画绘本",description:"粗细变化线稿与强烈明暗",visual:"高品质欧美超级英雄漫画美术",character:"轮廓鲜明，肌肉与人体结构合理",clothing:"设计明确的服装与装备线条",scene:"戏剧化城市或幻想背景",camera:"强透视与有力构图",lighting:"高反差明暗塑造体积",color:"大胆色块与高对比配色",quality:"精细墨线与成熟漫画上色",negative:DEFAULT_NEGATIVE },
  { id:"watercolor",name:"水彩绘本",group:"漫画绘本",description:"透明水彩、纸张肌理与温柔叙事",visual:"精致水彩绘本插画风格",character:"造型亲切，表情温暖",clothing:"水彩笔触表现布料与纹理",scene:"纸张肌理、自然景物与柔和背景",camera:"绘本式平衡构图",lighting:"柔和自然光",color:"透明水彩晕染，温暖低饱和",quality:"细腻纸感与自然水彩边缘",negative:"照片写实，硬质3D，过度锐化，文字，水印" },
  { id:"cyberpunk",name:"赛博朋克",group:"特殊题材",description:"未来都市、霓虹与科技细节",visual:"高品质赛博朋克科幻视觉设计",character:"角色造型有科技感，人体结构自然",clothing:"功能性未来服装与机械细节",scene:"未来都市、全息界面、雨夜街道",camera:"电影级广角与纵深构图",lighting:"霓虹边缘光与潮湿反射",color:"青蓝与洋红霓虹对比",quality:"精细科幻电影美术",negative:DEFAULT_NEGATIVE },
  { id:"post-apoc",name:"末日废土",group:"特殊题材",description:"荒芜环境、磨损材质与生存氛围",visual:"末日废土电影概念美术",character:"坚韧真实的人物形象",clothing:"磨损、尘土与实用生存装备",scene:"荒废城市、残骸、沙尘与植被侵蚀",camera:"广阔环境镜头与紧张近景结合",lighting:"强烈自然光与尘雾散射",color:"土黄、灰褐与低饱和冷色",quality:"高细节电影概念画面",negative:DEFAULT_NEGATIVE },
  { id:"oriental-myth",name:"东方神话",group:"特殊题材",description:"神祇、古老神殿与东方神秘感",visual:"东方神话史诗视觉风格",character:"神性气质与清晰人物轮廓",clothing:"神话礼服、甲胄与象征性纹样",scene:"神殿、天界、古老遗迹与奇观",camera:"史诗尺度与人物情绪兼顾",lighting:"神圣体积光与层次阴影",color:"玉青、鎏金、朱红协调",quality:"宏大精致的神话电影美术",negative:DEFAULT_NEGATIVE }
] satisfies Array<Omit<StyleTemplate, "custom">>).map((item): StyleTemplate => ({ ...item, custom: false }));

const emptyTemplate = (): StyleTemplate => ({
  id: "custom-" + Date.now(), name: "我的自定义风格", group: "国漫", description: "自定义视觉风格",
  visual: "", character: "", clothing: "", scene: "", camera: "", lighting: "", color: "", quality: "", negative: DEFAULT_NEGATIVE, custom: true
});

const stageNames: Record<Stage, string> = { script:"剧本", storyboard:"分镜", image:"图片", video:"视频", voice:"配音" };
const groups: StyleGroup[] = ["国漫","2D动漫","3D动漫","仿真人","漫画绘本","特殊题材"];

function seedState(): SavedState {
  return { templates: styleSeeds, prompts: defaultPrompts, activeId: styleSeeds[0].id, history: [] };
}

export default function PromptStudioPage() {
  const [state, setState] = useState<SavedState>(seedState);
  const [ready, setReady] = useState(false);
  const [group, setGroup] = useState<StyleGroup | "全部">("全部");
  const [selectedId, setSelectedId] = useState(styleSeeds[0].id);
  const [stage, setStage] = useState<Stage>("image");
  const [draft, setDraft] = useState<StyleTemplate>(styleSeeds[0]);
  const [promptDraft, setPromptDraft] = useState(defaultPrompts.image);
  const [message, setMessage] = useState("");
  const [preview, setPreview] = useState("");

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const saved = JSON.parse(raw) as SavedState;
        if (saved && Array.isArray(saved.templates) && saved.prompts) {
          setState({ ...seedState(), ...saved });
          setSelectedId(saved.activeId || saved.templates[0]?.id || styleSeeds[0].id);
        }
      }
    } catch { setMessage("读取本地模板失败，已载入内置默认模板。"); }
    setReady(true);
  }, []);

  useEffect(() => {
    if (ready) localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }, [state, ready]);

  const selected = state.templates.find(item => item.id === selectedId) || state.templates[0];
  const visibleTemplates = useMemo(() => state.templates.filter(item => group === "全部" || item.group === group), [state.templates, group]);

  useEffect(() => {
    if (!selected) return;
    setDraft({ ...selected });
    setState(current => ({ ...current, activeId: selected.id }));
  }, [selectedId]);

  useEffect(() => setPromptDraft(state.prompts[stage] || ""), [stage, state.prompts]);

  function updateDraft(key: keyof StyleTemplate, value: string) {
    setDraft(current => ({ ...current, [key]: value }));
  }

  function saveTemplate() {
    if (!draft.name.trim()) { setMessage("请先填写模板名称。"); return; }
    const snapshot = JSON.stringify({ templates: state.templates, prompts: state.prompts, activeId: state.activeId });
    const exists = state.templates.some(item => item.id === draft.id);
    const templates = exists ? state.templates.map(item => item.id === draft.id ? { ...draft, name: draft.name.trim() } : item) : [...state.templates, draft];
    setState(current => ({ ...current, templates, activeId: draft.id, history: [...current.history, { at: new Date().toISOString(), label: "保存风格：" + draft.name, snapshot }].slice(-30) }));
    setSelectedId(draft.id);
    setMessage("风格模板已保存到本机。");
  }

  function createTemplate() {
    const item = emptyTemplate();
    setState(current => ({ ...current, templates: [...current.templates, item] }));
    setSelectedId(item.id);
    setGroup("全部");
    setDraft(item);
    setMessage("已创建自定义模板，编辑后点击保存即可。");
  }

  function duplicateTemplate() {
    const item = { ...draft, id: "custom-" + Date.now(), name: draft.name + "（副本）", custom: true };
    setState(current => ({ ...current, templates: [...current.templates, item] }));
    setSelectedId(item.id);
    setDraft(item);
    setMessage("已复制为自定义模板。");
  }

  function deleteTemplate() {
    if (!draft.custom) { setMessage("内置模板不能删除，可以复制后自定义。"); return; }
    const templates = state.templates.filter(item => item.id !== draft.id);
    const next = templates[0] || styleSeeds[0];
    setState(current => ({ ...current, templates: templates.length ? templates : [styleSeeds[0]], activeId: next.id }));
    setSelectedId(next.id);
    setMessage("自定义模板已删除。");
  }

  function restoreDefaults() {
    if (!window.confirm("恢复内置默认风格和提示词？自定义模板将保留。")) return;
    const custom = state.templates.filter(item => item.custom);
    setState(current => ({ ...current, templates: [...styleSeeds, ...custom], prompts: defaultPrompts, activeId: styleSeeds[0].id }));
    setSelectedId(styleSeeds[0].id);
    setMessage("默认风格与提示词已恢复，自定义模板已保留。");
  }

  function savePrompt() {
    setState(current => ({ ...current, prompts: { ...current.prompts, [stage]: promptDraft }, history: [...current.history, { at: new Date().toISOString(), label: "保存" + stageNames[stage] + "提示词", snapshot: JSON.stringify(current.prompts) }].slice(-30) }));
    setMessage(stageNames[stage] + "提示词已保存在本机。");
  }

  function buildStyleText(item: StyleTemplate) {
    return [
      "视觉风格：" + item.name + "。", item.visual, "人物表现：" + item.character,
      "服装材质：" + item.clothing, "场景环境：" + item.scene, "镜头构图：" + item.camera,
      "光线：" + item.lighting, "色彩：" + item.color, "画质要求：" + item.quality,
      "负面提示词：" + item.negative
    ].filter(Boolean).join("\n");
  }

  function buildPreview() {
    setPreview((state.prompts[stage] || promptDraft) + "\n\n" + buildStyleText(draft));
    setMessage("已组合当前任务提示词与风格模板。可复制后粘贴到对应创作流程。");
  }

  async function copyPreview() {
    const text = preview || ((state.prompts[stage] || promptDraft) + "\n\n" + buildStyleText(draft));
    try { await navigator.clipboard.writeText(text); setMessage("提示词已复制。"); }
    catch { setMessage("浏览器未开放剪贴板权限，请手动选择下方预览内容复制。"); }
  }

  function exportLibrary() {
    const blob = new Blob([JSON.stringify(state, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = "AI漫剧大师-提示词与风格库.json"; anchor.click();
    URL.revokeObjectURL(url);
    setMessage("已导出本地模板备份文件。");
  }

  async function importLibrary(file?: File) {
    if (!file) return;
    try {
      const imported = JSON.parse(await file.text()) as SavedState;
      if (!Array.isArray(imported.templates) || !imported.prompts) throw new Error("文件格式不正确");
      setState({ ...seedState(), ...imported });
      setSelectedId(imported.activeId || imported.templates[0]?.id || styleSeeds[0].id);
      setMessage("模板库导入成功。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "导入失败"); }
  }

  return (
    <main className="min-h-screen bg-[#070511] px-4 py-8 pb-28 text-slate-100 md:px-8">
      <div className="mx-auto max-w-7xl">
        <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="mb-2 text-xs tracking-[0.3em] text-fuchsia-300">AI COMIC CREATIVE CONTROL</p>
            <h1 className="text-3xl font-black md:text-4xl">提示词管理中心 · AI视觉风格库</h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-300">管理剧本、分镜、图片、视频和配音提示词。风格模板与自定义内容保存在当前电脑本地，不包含或重新下载任何模型资源。</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button onClick={exportLibrary} className="rounded-xl border border-cyan-400/30 px-4 py-2 text-sm text-cyan-100 hover:bg-cyan-950/50">导出备份</button>
            <label className="cursor-pointer rounded-xl border border-cyan-400/30 px-4 py-2 text-sm text-cyan-100 hover:bg-cyan-950/50">导入模板<input type="file" accept="application/json,.json" className="hidden" onChange={event => { void importLibrary(event.target.files?.[0]); event.currentTarget.value = ""; }} /></label>
            <button onClick={restoreDefaults} className="rounded-xl border border-amber-400/30 px-4 py-2 text-sm text-amber-100 hover:bg-amber-950/40">恢复默认</button>
          </div>
        </header>

        {message && <div role="status" className="mb-5 rounded-xl border border-purple-400/30 bg-purple-950/40 px-4 py-3 text-sm text-purple-100">{message}</div>}

        <div className="grid gap-6 xl:grid-cols-[310px_minmax(0,1fr)]">
          <aside className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
            <div className="mb-4 flex items-center justify-between"><h2 className="font-bold">视觉风格模板</h2><button onClick={createTemplate} className="rounded-lg bg-fuchsia-600 px-3 py-2 text-xs font-semibold hover:bg-fuchsia-500">＋ 新建</button></div>
            <select value={group} onChange={event => setGroup(event.target.value as StyleGroup | "全部")} className="mb-3 w-full rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-sm">
              <option value="全部">全部风格分类</option>{groups.map(item => <option key={item} value={item}>{item}</option>)}
            </select>
            <div className="max-h-[680px] space-y-2 overflow-y-auto pr-1">
              {visibleTemplates.map(item => <button key={item.id} onClick={() => setSelectedId(item.id)} className={"w-full rounded-xl border p-3 text-left transition " + (selectedId === item.id ? "border-fuchsia-400/70 bg-fuchsia-950/50" : "border-white/10 bg-black/20 hover:border-white/30")}>
                <span className="flex items-center justify-between gap-2"><span className="font-semibold">{item.name}</span><span className="text-[10px] text-slate-400">{item.group}</span></span>
                <span className="mt-1 block text-xs leading-5 text-slate-400">{item.description}</span>
              </button>)}
            </div>
            <p className="mt-4 text-xs leading-5 text-slate-500">内置风格可直接使用或复制后修改；自定义模板可编辑、删除和备份。</p>
          </aside>

          <section className="space-y-6">
            <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
              <div className="mb-5 flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-xl font-bold">编辑风格模板</h2><p className="mt-1 text-xs text-slate-400">细化人物、服装、场景、镜头、光影和画质规则</p></div><div className="flex flex-wrap gap-2"><button onClick={duplicateTemplate} className="rounded-lg border border-white/15 px-3 py-2 text-xs hover:bg-white/10">复制模板</button><button onClick={deleteTemplate} className="rounded-lg border border-rose-400/30 px-3 py-2 text-xs text-rose-200 hover:bg-rose-950/40">删除自定义</button><button onClick={saveTemplate} className="rounded-lg bg-fuchsia-600 px-4 py-2 text-xs font-bold hover:bg-fuchsia-500">保存模板</button></div></div>
              <div className="grid gap-4 md:grid-cols-2">
                <label className="text-sm">模板名称<input value={draft.name} onChange={event => updateDraft("name",event.target.value)} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-2" /></label>
                <label className="text-sm">风格分类<select value={draft.group} onChange={event => updateDraft("group",event.target.value as StyleGroup)} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-2">{groups.map(item => <option key={item}>{item}</option>)}</select></label>
                <label className="text-sm md:col-span-2">风格说明<input value={draft.description} onChange={event => updateDraft("description",event.target.value)} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-2" /></label>
                {([
                  ["visual","整体视觉风格"],["character","人物外观与表演"],["clothing","服装与材质"],["scene","场景与环境"],["camera","镜头与构图"],["lighting","光线与氛围"],["color","色彩体系"],["quality","画质与细节要求"],["negative","负面提示词"]
                ] as Array<[keyof StyleTemplate,string]>).map(([key,label]) => <label key={key} className={"text-sm " + (key === "negative" || key === "visual" ? "md:col-span-2" : "")}>{label}<textarea rows={key === "negative" ? 3 : 2} value={String(draft[key] || "")} onChange={event => updateDraft(key,event.target.value)} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-950 px-3 py-2 text-sm leading-6" /></label>)}
              </div>
            </div>

            <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
              <div className="mb-4"><h2 className="text-xl font-bold">分阶段提示词</h2><p className="mt-1 text-xs text-slate-400">每个阶段独立编辑并保存，避免图片提示词规则误用于剧本或配音</p></div>
              <div className="mb-4 flex flex-wrap gap-2">{(Object.keys(stageNames) as Stage[]).map(key => <button key={key} onClick={() => setStage(key)} className={"rounded-lg px-4 py-2 text-sm " + (stage === key ? "bg-cyan-600 text-white" : "border border-white/10 text-slate-300 hover:bg-white/10")}>{stageNames[key]}</button>)}</div>
              <textarea value={promptDraft} onChange={event => setPromptDraft(event.target.value)} rows={6} className="w-full rounded-xl border border-white/10 bg-slate-950 p-4 text-sm leading-7" />
              <div className="mt-3 flex flex-wrap gap-2"><button onClick={savePrompt} className="rounded-lg bg-cyan-600 px-4 py-2 text-sm font-semibold hover:bg-cyan-500">保存{stageNames[stage]}提示词</button><button onClick={() => setPromptDraft(defaultPrompts[stage])} className="rounded-lg border border-white/15 px-4 py-2 text-sm hover:bg-white/10">恢复此阶段默认</button></div>
            </div>

            <div className="rounded-2xl border border-fuchsia-400/20 bg-fuchsia-950/20 p-5">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-xl font-bold">组合提示词预览</h2><p className="mt-1 text-xs text-slate-400">将当前阶段指令与所选风格组合，检查后复制使用</p></div><div className="flex gap-2"><button onClick={buildPreview} className="rounded-lg border border-fuchsia-300/30 px-4 py-2 text-sm hover:bg-fuchsia-900/40">生成预览</button><button onClick={copyPreview} className="rounded-lg bg-fuchsia-600 px-4 py-2 text-sm font-bold hover:bg-fuchsia-500">复制提示词</button></div></div>
              <textarea readOnly value={preview || "点击“生成预览”，查看阶段提示词与当前风格模板的组合结果。"} rows={12} className="w-full rounded-xl border border-white/10 bg-slate-950/80 p-4 text-sm leading-7 text-slate-200" />
              <p className="mt-3 text-xs leading-5 text-amber-200/80">当前版本已提供本地管理、组合、预览和复制。将模板自动注入剧本/分镜/ComfyUI实际生成链路属于下一步接口集成，不能把复制预览误认为已自动应用。</p>
            </div>
          </section>
        </div>
        <footer className="mt-8 text-center text-xs text-slate-500">本地保存 · 支持 JSON 备份 · 不调用付费云端 API · 不触碰离线模型资源</footer>
      </div>
    </main>
  );
}
