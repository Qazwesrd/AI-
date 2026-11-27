"use client";

import { useEffect, useState } from "react";

type UpdateStatus = {
  port: number; address: string; localOnly: boolean; softwareVersion: string;
  softwareUpdateSource: string; modelServicePort: number | null; installedModels: string[];
};
type SoftwareCheck = {
  currentVersion: string; latestVersion: string; updateAvailable: boolean;
  name: string; notes: string; publishedAt: string | null; pageUrl: string;
  assets: {name:string;size:number;downloadUrl:string}[]; notice: string;
};
type ModelProgress = {model:string;status:string;completed:number;total:number;percent:number|null};
declare global {
  interface Window {
    updatesDesktop?: {
      getStatus: () => Promise<UpdateStatus>;
      checkSoftware: () => Promise<SoftwareCheck>;
      updateModel: (modelName:string, confirm:boolean) => Promise<{model:string;status:string;port:number}>;
      onModelProgress: (callback:(progress:ModelProgress)=>void) => () => void;
    };
  }
}

export default function UpdatesPage() {
  const [status,setStatus] = useState<UpdateStatus|null>(null);
  const [software,setSoftware] = useState<SoftwareCheck|null>(null);
  const [selectedModel,setSelectedModel] = useState("");
  const [progress,setProgress] = useState<ModelProgress|null>(null);
  const [message,setMessage] = useState("");
  const [busy,setBusy] = useState(false);

  async function refresh() {
    if (!window.updatesDesktop) {
      setMessage("更新中心仅在 Windows 桌面版运行。请在正式桌面程序内打开此页面。");
      return;
    }
    try {
      const value=await window.updatesDesktop.getStatus();
      setStatus(value);
      if (!selectedModel && value.installedModels.length) setSelectedModel(value.installedModels[0]);
    } catch(e) { setMessage(e instanceof Error?e.message:"读取更新中心状态失败"); }
  }
  useEffect(()=>{
    void refresh();
    const off=window.updatesDesktop?.onModelProgress(setProgress);
    return ()=>off?.();
  },[]);

  async function checkSoftware() {
    if(!window.updatesDesktop) return setMessage("请在 Windows 桌面版中使用更新中心。");
    setBusy(true);setMessage("");
    try { setSoftware(await window.updatesDesktop.checkSoftware()); }
    catch(e){setMessage(e instanceof Error?e.message:"检查软件更新失败");}
    finally{setBusy(false);}
  }

  async function updateModel() {
    if(!window.updatesDesktop || !selectedModel) return;
    if(!window.confirm("确认通过本机更新端口更新模型："+selectedModel+"？这会调用本机 Ollama 拉取该模型标签的最新内容，可能产生网络流量和磁盘占用；不会删除项目文件。")) return;
    setBusy(true);setProgress({model:selectedModel,status:"准备更新",completed:0,total:0,percent:0});setMessage("");
    try {
      await window.updatesDesktop.updateModel(selectedModel,true);
      setMessage("模型更新完成。建议重新检查模型状态后再开始生成。");
      await refresh();
    } catch(e){setMessage(e instanceof Error?e.message:"模型更新失败");}
    finally{setBusy(false);}
  }

  const formatBytes=(n:number)=>n<1024**2?(n/1024).toFixed(1)+" KB":(n/1024**2).toFixed(1)+" MB";
  return <main className="min-h-screen p-5 md:p-9 text-white">
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <a href="/" className="text-sm text-cyan-200 hover:underline">← 返回首页</a>
        <p className="mt-4 text-xs tracking-[4px] text-cyan-300">LOCAL UPDATE CHANNEL</p>
        <h1 className="mt-2 text-3xl font-black">软件与本地 AI 模型更新中心</h1>
        <p className="mt-2 text-sm text-purple-200/70">独立本地更新端口：检查软件版本、维护本地模型。更新服务仅绑定 127.0.0.1，不向局域网开放。</p>
      </header>
      {message&&<div role="status" className="rounded-xl border border-cyan-300/20 bg-cyan-950/30 p-3 text-sm text-cyan-100">{message}</div>}
      <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-bold">更新端口状态</h2>
          <button onClick={()=>void refresh()} className="rounded-lg border border-white/20 px-3 py-2 text-sm">刷新状态</button>
        </div>
        {status?<div className="mt-4 grid gap-3 sm:grid-cols-2">
          <div className="rounded-xl bg-black/20 p-3"><p className="text-xs text-white/50">本地更新地址</p><p className="mt-1 font-mono">http://{status.address}:{status.port}</p></div>
          <div className="rounded-xl bg-black/20 p-3"><p className="text-xs text-white/50">当前软件版本</p><p className="mt-1">{status.softwareVersion}</p></div>
          <div className="rounded-xl bg-black/20 p-3"><p className="text-xs text-white/50">本地模型服务</p><p className="mt-1">{status.modelServicePort?("Ollama · 端口 "+status.modelServicePort):"未检测到运行中的 Ollama"}</p></div>
          <div className="rounded-xl bg-black/20 p-3"><p className="text-xs text-white/50">本地已安装模型</p><p className="mt-1">{status.installedModels.length} 个</p></div>
        </div>:<p className="mt-4 text-sm text-white/50">正在读取更新端口状态……</p>}
        <p className="mt-3 text-xs text-white/40">端口仅监听本机回环地址。其他电脑不能通过局域网访问；如端口被占用，可设置 AICOMICS_UPDATE_PORT 更换。</p>
      </section>

      <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
        <h2 className="text-lg font-bold">软件版本更新</h2>
        <p className="mt-1 text-sm text-white/50">从项目 GitHub 正式 Release 检查版本和更新说明，不会静默下载安装程序。</p>
        <button disabled={busy} onClick={()=>void checkSoftware()} className="mt-4 rounded-xl bg-cyan-700 px-4 py-2 text-sm font-semibold disabled:opacity-50">检查软件更新</button>
        {software&&<div className="mt-4 rounded-xl border border-white/10 bg-black/20 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2"><strong>{software.name}</strong><span className={software.updateAvailable?"text-amber-200":"text-emerald-300"}>{software.updateAvailable?"发现新版本":"当前已是最新版本"}</span></div>
          <p className="mt-2 text-sm text-white/70">当前版本 {software.currentVersion} → 最新版本 {software.latestVersion}</p>
          {software.publishedAt&&<p className="mt-1 text-xs text-white/40">发布时间：{new Date(software.publishedAt).toLocaleString()}</p>}
          {software.notes&&<pre className="mt-3 whitespace-pre-wrap text-xs text-white/60">{software.notes}</pre>}
          {software.assets.map(asset=><p key={asset.name} className="mt-2 text-xs text-white/50">{asset.name} · {formatBytes(asset.size)}</p>)}
          {software.pageUrl&&<a href={software.pageUrl} target="_blank" rel="noreferrer" className="mt-3 inline-block text-sm text-cyan-200 underline">查看官方版本发布页</a>}
          <p className="mt-3 text-xs text-amber-200/80">{software.notice}</p>
        </div>}
      </section>

      <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
        <h2 className="text-lg font-bold">本地模型原位更新</h2>
        <p className="mt-1 text-sm text-white/50">只允许更新已经安装的 Ollama 模型。确认后由本机 Ollama 拉取同一模型标签的最新版本；不移动模型目录、不删除项目文件。</p>
        {status?.installedModels.length?<div className="mt-4 flex flex-wrap gap-3">
          <select value={selectedModel} onChange={e=>setSelectedModel(e.target.value)} className="min-w-0 flex-1 rounded-lg bg-[#171326] p-3 text-sm">
            {status.installedModels.map(name=><option key={name} value={name}>{name}</option>)}
          </select>
          <button disabled={busy||!selectedModel} onClick={()=>void updateModel()} className="rounded-xl bg-fuchsia-700 px-4 py-2 text-sm font-semibold disabled:opacity-50">确认更新所选模型</button>
        </div>:<p className="mt-4 text-sm text-amber-200">没有检测到已安装的 Ollama 模型，或本地 Ollama 服务尚未启动。</p>}
        {progress&&<div className="mt-4 rounded-xl bg-black/20 p-3">
          <div className="flex justify-between gap-3 text-sm"><span>{progress.model} · {progress.status}</span><span>{progress.percent===null?"处理中":progress.percent+"%"}</span></div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/10"><div className="h-full bg-fuchsia-500 transition-all" style={{width:(progress.percent??5)+"%"}} /></div>
          {progress.total>0&&<p className="mt-1 text-xs text-white/40">{formatBytes(progress.completed)} / {formatBytes(progress.total)}</p>}
        </div>}
      </section>
    </div>
  </main>;
}
