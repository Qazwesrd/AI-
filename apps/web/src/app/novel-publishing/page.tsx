"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";
type Novel = {id:number;title:string;genre:string;chapter_count:number;word_count:number};
type Platform = {id:string;name:string;region:string;language:string[];portal:string;mode:string;api_publish:boolean;revenue_sync:boolean;payout:string};
type Package = {id:number;novel_id:number;platform_id:string;platform_name:string;pen_name:string;language:string;status:string;word_count:number;content_hash:string;created_at:string};
type Chapter = {id:number;chapter_number:number;title:string;word_count:number};
type Submission = {id:number;package_id:number;status:string;external_work_url:string;note:string;submitted_at:string|null};
type LocalModel = {name:string};
type Translation = {id:number;novel_id:number;target_language:string;target_language_name:string;model_name:string;status:string;total_chunks:number;completed_chunks:number;progress_percent:number;last_error:string;chapters?:{chapter_number:number;title:string;translated_text:string;status:string}[]};

export default function NovelPublishingPage(){
  const [novels,setNovels]=useState<Novel[]>([]);
  const [platforms,setPlatforms]=useState<Platform[]>([]);
  const [packages,setPackages]=useState<Package[]>([]);
  const [submissions,setSubmissions]=useState<Submission[]>([]);
  const [chapters,setChapters]=useState<Chapter[]>([]);
  const [localModels,setLocalModels]=useState<LocalModel[]>([]);
  const [translationLanguage,setTranslationLanguage]=useState("en");
  const [translationModel,setTranslationModel]=useState("");
  const [translationGlossary,setTranslationGlossary]=useState("{}");
  const [translations,setTranslations]=useState<Translation[]>([]);
  const [activeTranslation,setActiveTranslation]=useState<Translation|null>(null);
  const [uploading,setUploading]=useState(false);
  const [aiTask,setAiTask]=useState("outline");
  const [aiChapterId,setAiChapterId]=useState("");
  const [aiExtra,setAiExtra]=useState("");
  const [aiPrompt,setAiPrompt]=useState("");
  const [novelId,setNovelId]=useState("");
  const [platformId,setPlatformId]=useState("fanqie");
  const [penName,setPenName]=useState("");
  const [language,setLanguage]=useState("zh-CN");
  const [synopsis,setSynopsis]=useState("");
  const [tags,setTags]=useState("");
  const [rights,setRights]=useState(false);
  const [aiDisclosure,setAiDisclosure]=useState(false);
  const [message,setMessage]=useState("");
  const [busy,setBusy]=useState(false);

  async function api<T>(path:string,init?:RequestInit):Promise<T>{
    const response=await fetch(API+path,init);
    const data=await response.json().catch(()=>({}));
    if(!response.ok) throw new Error(data.detail||"请求失败："+response.status);
    return data as T;
  }
  async function load(){
    try{
      const [n,p,k,s,models]=await Promise.all([
        api<{items:Novel[]}>("/api/novel-publishing/novels"),
        api<{items:Platform[]}>("/api/novel-publishing/platforms"),
        api<{items:Package[]}>("/api/novel-publishing/packages"),
        api<{items:Submission[]}>("/api/novel-publishing/submissions"),
        api<{models:LocalModel[]}>("/api/local-ai/runtime").catch(()=>({models:[]})),
      ]);
      setNovels(n.items||[]);setPlatforms(p.items||[]);setPackages(k.items||[]);setSubmissions(s.items||[]);
      setLocalModels(models.models||[]);
      if(!translationModel&&models.models?.length)setTranslationModel(models.models[0].name);
      if(!novelId&&n.items?.length)setNovelId(String(n.items[0].id));
    }catch(e){setMessage(e instanceof Error?e.message:"读取小说发行数据失败");}
  }
  useEffect(()=>{void load();},[]);
  useEffect(()=>{
    if(!novelId){setChapters([]);return;}
    void api<{items:Chapter[]}>(`/api/novels/${novelId}/chapters`).then(x=>setChapters(x.items||[])).catch(()=>setChapters([]));
  },[novelId]);

  useEffect(()=>{
    if(!novelId){setTranslations([]);setActiveTranslation(null);return;}
    void api<{items:Translation[]}>(`/api/novel-publishing/translations?novel_id=${novelId}`).then(x=>setTranslations(x.items||[])).catch(()=>setTranslations([]));
  },[novelId]);

  async function uploadNovel(file:File){
    setUploading(true);setBusy(true);
    try{
      const created=await api<{id:number}>("/api/novels",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({title:file.name.replace(/\.[^.]+$/,""),genre:"待分类",description:"从小说发行中心上传导入"})});
      const form=new FormData();form.append("file",file);
      const response=await fetch(`${API}/api/novels/${created.id}/import-txt`,{method:"POST",body:form});
      const result=await response.json().catch(()=>({}));
      if(!response.ok)throw new Error(result.detail||"小说导入失败");
      await load();setNovelId(String(created.id));
      setMessage(`已导入 ${file.name}，识别 ${result.chapter_count} 章、${result.word_count} 字。原有小说项目未被覆盖。`);
    }catch(e){setMessage(e instanceof Error?e.message:"上传小说失败");}
    finally{setUploading(false);setBusy(false);}
  }

  async function createTranslation(){
    if(!novelId)return setMessage("请先选择或上传小说。");
    if(!translationModel)return setMessage("没有检测到本机已安装的 Ollama 模型，请先完成本地模型配置。");
    let glossary:Record<string,string>={};
    try{glossary=JSON.parse(translationGlossary||"{}");if(!glossary||Array.isArray(glossary)||typeof glossary!=="object")throw new Error("术语表必须是 JSON 对象");}
    catch{return setMessage("术语表 JSON 格式不正确，例如：{\"主角\":\"Hero\"}");}
    setBusy(true);
    try{
      const result=await api<{item:Translation;message:string}>(`/api/novel-publishing/novels/${novelId}/translations`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({target_language:translationLanguage,model_name:translationModel,glossary})});
      setActiveTranslation(result.item);setMessage(result.message);
      const list=await api<{items:Translation[]}>(`/api/novel-publishing/translations?novel_id=${novelId}`);setTranslations(list.items||[]);
    }catch(e){setMessage(e instanceof Error?e.message:"创建译本失败");}
    finally{setBusy(false);}
  }

  async function refreshTranslation(id:number){
    const result=await api<Translation>(`/api/novel-publishing/translations/${id}`);
    setActiveTranslation(result);
    const list=await api<{items:Translation[]}>(`/api/novel-publishing/translations?novel_id=${novelId}`);
    setTranslations(list.items||[]);
    return result;
  }

  async function translateOne(id:number){
    setBusy(true);
    try{
      const result=await api<{item:Translation;message:string}>(`/api/novel-publishing/translations/${id}/translate-next`,{method:"POST"});
      await refreshTranslation(id);setMessage(result.message);
    }catch(e){setMessage(e instanceof Error?e.message:"本段翻译失败");}
    finally{setBusy(false);}
  }

  async function translateAll(id:number){
    setBusy(true);
    try{
      let current=await refreshTranslation(id);
      while(current.status!=="completed"&&current.status!=="source_changed"){
        const result=await api<{item:Translation;message:string}>(`/api/novel-publishing/translations/${id}/translate-next`,{method:"POST"});
        current=await refreshTranslation(id);
        setMessage(`翻译进度：${current.completed_chunks}/${current.total_chunks} 段。`);
      }
      setMessage(current.status==="completed"?"全文翻译完成。可下载译本并制作目标平台发行稿件。":"原文已变化，翻译已暂停，请重新创建译本。");
    }catch(e){setMessage(e instanceof Error?e.message:"全文翻译中断；已完成部分会保留，可稍后继续。");}
    finally{setBusy(false);}
  }

  async function exportTranslation(id:number,format:"md"|"txt"|"json"="md"){
    try{
      const response=await fetch(`${API}/api/novel-publishing/translations/${id}/export?format=${format}`);
      if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(data.detail||"导出译本失败");}
      const blob=await response.blob();const url=URL.createObjectURL(blob);const a=document.createElement("a");a.href=url;a.download=`novel-translation-${id}.${format}`;a.click();URL.revokeObjectURL(url);
    }catch(e){setMessage(e instanceof Error?e.message:"导出译本失败");}
  }

  async function generateAIPrompt(){
    if(!novelId)return setMessage("请先选择小说。");
    setBusy(true);
    try{
      const result=await api<{prompt:string;message:string}>(
        `/api/novel-publishing/novels/${novelId}/ai-assist-prompt`,
        {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({task:aiTask,chapter_id:aiChapterId?Number(aiChapterId):null,target_language:language,platform_id:platformId,extra_requirements:aiExtra})}
      );
      setAiPrompt(result.prompt);setMessage(result.message);
    }catch(e){setMessage(e instanceof Error?e.message:"生成 AI 创作提示词失败");}
    finally{setBusy(false);}
  }
  async function createPackage(){
    if(!novelId)return setMessage("请先在小说创作工作室创建或导入小说。");
    setBusy(true);
    try{
      const result=await api<{item:Package;message:string}>(`/api/novel-publishing/novels/${novelId}/packages`,{
        method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({platform_id:platformId,pen_name:penName,language,synopsis,tags:tags.split(/[,，、]/).map(x=>x.trim()).filter(Boolean),rights_confirmed:rights,ai_assistance_disclosed:aiDisclosure}),
      });
      await api(`/api/novel-publishing/packages/${result.item.id}/submissions`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({status:"prepared",note:"已在本机生成发行稿件包，尚未向平台提交。"})});
      setMessage(result.message);await load();
    }catch(e){setMessage(e instanceof Error?e.message:"生成发行稿件失败");}
    finally{setBusy(false);}
  }
  async function exportPackage(id:number,format:"md"|"txt"|"json"){
    try{
      const response=await fetch(`${API}/api/novel-publishing/packages/${id}/export?format=${format}`);
      if(!response.ok){const d=await response.json().catch(()=>({}));throw new Error(d.detail||"导出失败");}
      const blob=await response.blob();const url=URL.createObjectURL(blob);const a=document.createElement("a");a.href=url;a.download=`novel-distribution-${id}.${format}`;a.click();URL.revokeObjectURL(url);
    }catch(e){setMessage(e instanceof Error?e.message:"导出失败");}
  }
  async function openSubmission(packageId:number){
    const pkg=packages.find(x=>x.id===packageId);const platform=platforms.find(x=>x.id===pkg?.platform_id);
    if(!pkg||!platform)return;
    window.open(platform.portal,"_blank","noopener,noreferrer");
    const existing=submissions.find(x=>x.package_id===packageId);
    if(!existing){
      try{await api(`/api/novel-publishing/packages/${packageId}/submissions`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({status:"prepared",note:"已打开官方作者入口；实际投稿需在平台内完成。"})});await load();}
      catch(e){setMessage(e instanceof Error?e.message:"投稿记录创建失败");}
    }
  }
  async function updateSubmission(id:number,status:string){
    const url=status==="published"?window.prompt("请填写平台实际发布后的作品链接（必须来自平台页面）")||"": "";
    if(status==="published"&&!url)return;
    const note=window.prompt("补充说明（可留空）")||"";
    try{
      await api(`/api/novel-publishing/submissions/${id}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({status,external_work_url:url,note})});
      setMessage("投稿记录已更新。");await load();
    }catch(e){setMessage(e instanceof Error?e.message:"更新失败");}
  }
  const field="w-full rounded-lg border border-purple-400/25 bg-slate-950 px-3 py-2 text-sm text-white";
  const statusLabel:Record<string,string>={prepared:"已备稿",submitted:"已提交",reviewing:"审核中",published:"已发布",rejected:"被拒绝",paused:"已暂停",withdrawn:"已撤稿"};
  return <main className="cosmic min-h-screen p-5 text-white md:p-8">
    <div className="mx-auto max-w-6xl space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3"><div><a href="/" className="text-sm text-purple-300">← 返回首页</a><h1 className="mt-3 text-3xl font-black">🌍 AI小说创作与国内外发行</h1><p className="mt-2 text-sm text-purple-200">从本地小说项目生成平台适配稿件，准备国内外投稿并跟踪审核、发布和收益。</p></div><a href="/novels" className="rounded-lg border border-purple-400/30 px-4 py-2 text-sm">进入小说创作工作室</a></header>
      {message&&<div role="status" className="rounded-xl border border-cyan-400/20 bg-cyan-950/30 p-3 text-sm">{message}</div>}


      <section className="rounded-2xl border border-emerald-400/20 bg-[#0d201dcc] p-5">
        <h2 className="text-xl font-bold">小说上传与多语言全文翻译</h2>
        <p className="mt-1 text-xs text-emerald-100/70">支持 TXT、MD、DOCX、EPUB、PDF、RTF。上传会新建独立小说项目，不覆盖现有作品。翻译使用本机已安装模型，逐段保存，可中断续做；原文和章节永不被翻译结果覆盖。</p>
        <label className="mt-3 block text-sm">上传小说文件<input type="file" accept=".txt,.md,.markdown,.docx,.epub,.pdf,.rtf" disabled={busy} onChange={e=>{const file=e.target.files?.[0];if(file)void uploadNovel(file);e.currentTarget.value="";}} className="mt-2 block w-full rounded-lg border border-emerald-300/20 bg-black/20 p-3 text-sm"/></label>
        {uploading&&<p className="mt-2 text-xs text-cyan-200">正在导入文件并识别章节…</p>}
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          <label className="text-sm">要翻译的小说<select className={field+" mt-1"} value={novelId} onChange={e=>setNovelId(e.target.value)}><option value="">请选择小说</option>{novels.map(n=><option key={n.id} value={n.id}>{n.title} · {n.chapter_count}章</option>)}</select></label>
          <label className="text-sm">目标语言<select className={field+" mt-1"} value={translationLanguage} onChange={e=>setTranslationLanguage(e.target.value)}>{[["en","英语"],["ja","日语"],["ko","韩语"],["es","西班牙语"],["fr","法语"],["de","德语"],["it","意大利语"],["pt","葡萄牙语"],["ru","俄语"],["ar","阿拉伯语"],["th","泰语"],["vi","越南语"],["id","印尼语"],["tr","土耳其语"],["zh-TW","繁体中文"]].map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label>
          <label className="text-sm">本机翻译模型<select className={field+" mt-1"} value={translationModel} onChange={e=>setTranslationModel(e.target.value)}><option value="">请选择已安装模型</option>{localModels.map(m=><option key={m.name} value={m.name}>{m.name}</option>)}</select></label>
        </div>
        <label className="mt-3 block text-sm">术语表（JSON，可选）<textarea className={field+" mt-1 min-h-20 font-mono text-xs"} value={translationGlossary} onChange={e=>setTranslationGlossary(e.target.value)} placeholder='{"人物名":"Character Name","圣衣":"Cloth"}'/></label>
        <div className="mt-3 flex flex-wrap gap-2"><button disabled={busy||!novelId||!translationModel} onClick={()=>void createTranslation()} className="rounded-lg bg-emerald-700 px-4 py-2 text-sm font-semibold disabled:opacity-40">创建独立译本</button></div>
        <div className="mt-4 space-y-2">{translations.map(item=><div key={item.id} className="rounded-xl border border-white/10 bg-black/20 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2"><strong className="text-sm">{item.target_language_name}译本 · #{item.id}</strong><span className="text-xs text-cyan-200">{item.completed_chunks}/{item.total_chunks} 段 · {item.progress_percent}%</span></div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/10"><div className="h-full bg-emerald-500" style={{width:item.progress_percent+"%"}}/></div>
          <p className="mt-2 text-xs text-white/50">模型：{item.model_name} · 状态：{item.status}{item.last_error&&" · "+item.last_error}</p>
          <div className="mt-3 flex flex-wrap gap-2"><button disabled={busy||item.status==="completed"} onClick={()=>void translateOne(item.id)} className="rounded-lg border border-emerald-300/30 px-3 py-2 text-xs">翻译下一段</button><button disabled={busy||item.status==="completed"} onClick={()=>void translateAll(item.id)} className="rounded-lg bg-emerald-800 px-3 py-2 text-xs">连续翻译全文</button><button onClick={()=>void refreshTranslation(item.id)} className="rounded-lg border border-white/15 px-3 py-2 text-xs">查看进度</button><button onClick={()=>void exportTranslation(item.id,"md")} className="rounded-lg border border-cyan-300/30 px-3 py-2 text-xs">导出译本</button><button onClick={()=>void exportTranslation(item.id,"txt")} className="rounded-lg border border-cyan-300/30 px-3 py-2 text-xs">导出 TXT</button></div>
        </div>)}
        {!translations.length&&<p className="py-4 text-center text-xs text-white/40">上传小说或选择已有小说，创建第一个独立译本。</p>}</div>
        {activeTranslation?.chapters&&<details className="mt-4 rounded-xl border border-white/10 p-3"><summary className="cursor-pointer text-sm">预览已翻译章节（{activeTranslation.chapters.length}章）</summary><div className="mt-3 max-h-96 space-y-3 overflow-y-auto">{activeTranslation.chapters.map(ch=><article key={ch.chapter_number} className="rounded-lg bg-black/20 p-3"><strong className="text-sm">{ch.title}</strong><pre className="mt-2 whitespace-pre-wrap text-xs text-white/70">{ch.translated_text||"尚未翻译"}</pre></article>)}</div></details>}
      </section>
      <section className="rounded-2xl border border-cyan-400/20 bg-[#101c2acc] p-5">
        <h2 className="text-xl font-bold">AI辅助小说创作与翻译</h2>
        <p className="mt-1 text-xs text-cyan-100/70">可生成构思、大纲、人物、续写、润色、翻译、简介和市场本地化任务。默认只生成提示词，不会自动调用付费 API；你可以交给本地模型或你选定的免费 AI 工具，审阅后再写回小说。</p>
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          <label className="text-sm">创作任务<select className={field+" mt-1"} value={aiTask} onChange={e=>setAiTask(e.target.value)}>{[["concept","原创创意构思"],["outline","长篇大纲与分卷"],["characters","人物档案与关系网"],["chapter","续写下一章"],["polish","章节润色与逻辑校对"],["translate","多语言翻译"],["synopsis","平台简介与标签"],["localize","海外市场本地化"]].map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label>
          <label className="text-sm">参考章节（可选）<select className={field+" mt-1"} value={aiChapterId} onChange={e=>setAiChapterId(e.target.value)}><option value="">不指定章节</option>{chapters.map(ch=><option key={ch.id} value={ch.id}>第{ch.chapter_number}章 · {ch.title}</option>)}</select></label>
          <label className="text-sm">目标语言<select className={field+" mt-1"} value={language} onChange={e=>setLanguage(e.target.value)}>{[["zh-CN","简体中文"],["en","英语"],["ja","日语"],["es","西班牙语"],["fr","法语"],["de","德语"],["it","意大利语"],["pt","葡萄牙语"]].map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label>
        </div>
        <label className="mt-3 block text-sm">额外创作要求<textarea className={field+" mt-1 min-h-20"} value={aiExtra} onChange={e=>setAiExtra(e.target.value)} placeholder="例如：加强主角成长线、每章结尾增加悬念、保留原有世界观设定"/></label>
        <button disabled={busy||!novelId} onClick={()=>void generateAIPrompt()} className="mt-3 rounded-lg bg-cyan-700 px-4 py-2 text-sm font-semibold disabled:opacity-40">生成 AI 创作任务</button>
        {aiPrompt&&<div className="mt-4"><div className="mb-2 flex items-center justify-between gap-2"><strong className="text-sm">AI创作任务提示词</strong><button onClick={()=>void navigator.clipboard.writeText(aiPrompt).then(()=>setMessage("提示词已复制。"))} className="rounded-lg border border-cyan-300/30 px-3 py-1.5 text-xs">复制提示词</button></div><textarea readOnly value={aiPrompt} className={field+" min-h-64 font-mono text-xs leading-5"}/></div>}
      </section>
      <section className="rounded-2xl border border-amber-300/20 bg-amber-950/20 p-4 text-sm text-amber-100/90">
        当前先提供本地稿件生成、格式导出、官方作者入口和投稿记录。目录中的平台尚无经确认可供本软件使用的个人作者发布 API 或收益 API，因此不会自动登录、自动上传或假报发布成功。实际签约、收益、结算和提现以你在平台官方作者账户看到的结果为准。
      </section>
      <section className="rounded-2xl border border-purple-400/20 bg-[#160d2acc] p-5">
        <h2 className="text-xl font-bold">生成国内外平台发行稿件</h2>
        <p className="mt-1 text-xs text-purple-300">从现有小说项目读取章节，生成可下载的 Markdown / TXT / JSON 稿件包。</p>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <label className="text-sm">选择小说
            <select className={field+" mt-1"} value={novelId} onChange={e=>setNovelId(e.target.value)}><option value="">请选择</option>{novels.map(n=><option key={n.id} value={n.id}>{n.title} · {n.chapter_count}章 · {n.word_count}字</option>)}</select>
          </label>
          <label className="text-sm">目标平台
            <select className={field+" mt-1"} value={platformId} onChange={e=>setPlatformId(e.target.value)}>{platforms.map(p=><option key={p.id} value={p.id}>{p.region} · {p.name}</option>)}</select>
          </label>
          <label className="text-sm">作者笔名<input className={field+" mt-1"} value={penName} onChange={e=>setPenName(e.target.value)} placeholder="填写你的作者笔名"/></label>
          <label className="text-sm">发行语言
            <select className={field+" mt-1"} value={language} onChange={e=>setLanguage(e.target.value)}>{[["zh-CN","简体中文"],["en","英语"],["ja","日语"],["es","西班牙语"],["fr","法语"],["de","德语"],["it","意大利语"],["pt","葡萄牙语"]].map(([v,l])=><option key={v} value={v}>{l}</option>)}</select>
          </label>
          <label className="text-sm md:col-span-2">作品简介 / 投稿简介<textarea className={field+" mt-1 min-h-24"} value={synopsis} onChange={e=>setSynopsis(e.target.value)} placeholder="填写或粘贴适合目标平台的作品简介"/></label>
          <label className="text-sm md:col-span-2">标签（逗号分隔）<input className={field+" mt-1"} value={tags} onChange={e=>setTags(e.target.value)} placeholder="奇幻、冒险、成长、异世界"/></label>
        </div>
        <div className="mt-4 space-y-2 text-sm">
          <label className="flex items-start gap-2"><input type="checkbox" checked={rights} onChange={e=>setRights(e.target.checked)} className="mt-1"/>我确认拥有该作品的著作权或已取得充分授权，并会逐个平台检查独家、首发、改编权及版权合同要求。</label>
          <label className="flex items-start gap-2"><input type="checkbox" checked={aiDisclosure} onChange={e=>setAiDisclosure(e.target.checked)} className="mt-1"/>我会遵守目标平台关于 AI 辅助创作、翻译和内容披露的规则，不隐瞒平台要求披露的信息。</label>
        </div>
        <button disabled={busy||!novels.length} onClick={()=>void createPackage()} className="mt-4 rounded-xl bg-gradient-to-r from-purple-600 to-fuchsia-600 px-5 py-3 text-sm font-bold disabled:opacity-40">生成平台发行稿件包</button>
      </section>
      <section className="rounded-2xl border border-purple-400/20 bg-[#160d2acc] p-5">
        <h2 className="text-xl font-bold">国内外官方作者平台</h2>
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">{platforms.map(p=><article key={p.id} className="rounded-xl border border-purple-400/15 bg-black/20 p-4">
          <div className="flex items-center justify-between"><strong>{p.name}</strong><span className="text-xs text-cyan-200">{p.region}</span></div>
          <p className="mt-2 text-xs text-purple-200">支持语言：{p.language.join("、")}</p>
          <p className="mt-2 text-xs text-purple-300">{p.payout}</p>
          <p className="mt-2 text-xs text-amber-300">自动发布 API：未确认 · 自动收益同步：未确认</p>
          <a href={p.portal} target="_blank" rel="noopener noreferrer" className="mt-3 inline-block rounded-lg border border-cyan-400/30 px-3 py-2 text-xs text-cyan-100">打开官方作者入口 ↗</a>
        </article>)}</div>
      </section>
      <section className="rounded-2xl border border-purple-400/20 bg-[#160d2acc] p-5">
        <h2 className="text-xl font-bold">发行稿件包与投稿跟踪</h2>
        <div className="mt-4 space-y-3">{packages.map(pkg=>{
          const record=submissions.find(x=>x.package_id===pkg.id);
          const platform=platforms.find(x=>x.id===pkg.platform_id);
          return <article key={pkg.id} className="rounded-xl border border-purple-400/15 bg-black/20 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2"><div><strong>{pkg.platform_name}</strong><p className="mt-1 text-xs text-purple-300">小说 #{pkg.novel_id} · {pkg.language} · {pkg.word_count} 字 · {statusLabel[record?.status||pkg.status]||pkg.status}</p></div><span className="text-xs text-slate-500">SHA256 {pkg.content_hash.slice(0,12)}…</span></div>
            <div className="mt-3 flex flex-wrap gap-2"><button onClick={()=>void exportPackage(pkg.id,"md")} className="rounded-lg border border-cyan-400/30 px-3 py-2 text-xs">导出 Markdown</button><button onClick={()=>void exportPackage(pkg.id,"txt")} className="rounded-lg border border-cyan-400/30 px-3 py-2 text-xs">导出 TXT</button><button onClick={()=>void exportPackage(pkg.id,"json")} className="rounded-lg border border-cyan-400/30 px-3 py-2 text-xs">导出 JSON</button><button onClick={()=>void openSubmission(pkg.id)} className="rounded-lg bg-cyan-800 px-3 py-2 text-xs">打开官方投稿入口</button></div>
            {record&&<div className="mt-3 flex flex-wrap items-center gap-2 text-xs"><span className="text-purple-200">投稿记录：{statusLabel[record.status]||record.status}</span>{record.external_work_url&&<a href={record.external_work_url} target="_blank" rel="noopener noreferrer" className="text-cyan-200">查看已发布作品 ↗</a>}
              <select className="rounded-lg bg-slate-900 p-2" value={record.status} onChange={e=>void updateSubmission(record.id,e.target.value)}><option value="prepared">已备稿</option><option value="submitted">已提交</option><option value="reviewing">审核中</option><option value="published">已发布（需提供作品链接）</option><option value="rejected">被拒绝</option><option value="paused">已暂停</option><option value="withdrawn">已撤稿</option></select>
            </div>}
          </article>;
        })}
        {!packages.length&&<p className="py-5 text-center text-sm text-purple-300">还没有发行稿件包。选择小说和目标平台后即可生成。</p>}</div>
      </section>
      <section className="rounded-2xl border border-purple-400/20 bg-[#160d2acc] p-5">
        <h2 className="text-lg font-bold">真实稿费与提现处理</h2>
        <p className="mt-2 text-sm text-purple-200">小说平台的稿费、签约收入、订阅分成、打赏、版权收入、结算和提现记录统一进入收益中心。平台暂未开放官方 API 时，只能按你在作者后台看到的真实数据登记，不能把预估阅读数据当作现金收入。</p>
        <a href="/revenue" className="mt-3 inline-block rounded-lg border border-fuchsia-400/30 px-4 py-2 text-sm text-fuchsia-100">打开真实收益与提现中心 →</a>
      </section>
    </div>
  </main>;
}
