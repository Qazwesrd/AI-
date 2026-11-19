"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type BackupItem = { name: string; size_bytes: number; modified_at: string };
type WorkspaceBackupItem = { name: string; size_bytes: number; modified_at: string };

function formatSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export default function BackupCenterPage() {
  const [items, setItems] = useState<BackupItem[]>([]);
  const [workspaceItems, setWorkspaceItems] = useState<WorkspaceBackupItem[]>([]);
  const [workspaceFile, setWorkspaceFile] = useState<File | null>(null);
  const [workspaceConfirmation, setWorkspaceConfirmation] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try {
      const response = await fetch(`${API}/api/system/backups`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "读取备份列表失败");
      setItems(data.items || []);
      const workspaceResponse = await fetch(API + "/api/system/workspace-backups");
      const workspaceData = await workspaceResponse.json();
      if (workspaceResponse.ok) setWorkspaceItems(workspaceData.items || []);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "读取备份列表失败");
    }
  }

  useEffect(() => { void refresh(); }, []);

  async function createBackup() {
    setBusy(true);
    setMessage("正在创建数据库备份并检查完整性……");
    try {
      const response = await fetch(`${API}/api/system/backups`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "备份失败");
      setMessage(`备份完成：${data.name}（${formatSize(data.size_bytes)}），已检查 ${data.tables} 张数据表。`);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "备份失败");
    } finally { setBusy(false); }
  }

  async function restoreBackup() {
    if (!file || confirmation !== "RESTORE") return;
    if (!window.confirm("恢复操作会用所选备份替换当前数据库。当前数据库会先自动备份。确定继续吗？")) return;
    setBusy(true);
    setMessage("正在验证备份文件并准备恢复……");
    try {
      const form = new FormData();
      form.append("backup_file", file);
      form.append("confirmation", confirmation);
      const response = await fetch(`${API}/api/system/backups/restore`, { method: "POST", body: form });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "恢复失败");
      setMessage(`${data.message} 恢复前备份：${data.safety_backup || "无（原数据库不存在）"}`);
      setFile(null);
      setConfirmation("");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "恢复失败");
    } finally { setBusy(false); }
  }

  const panel = "rounded-2xl border border-purple-300/20 bg-slate-950/65 p-6";
  return (
    <main className="cosmic min-h-screen p-5 text-white md:p-8">
      <div className="mx-auto max-w-5xl space-y-7">
        <header>
          <h1 className="text-3xl font-bold">项目数据备份与恢复中心</h1>
          <p className="mt-2 text-white/60">备份包含小说、章节、剧情、人物素材、分镜、任务记录和软件配置。API Key 仍以加密形式存储。</p>
          <a href="/" className="mt-3 inline-flex rounded-lg border border-purple-300/30 px-3 py-2 text-sm">返回工作台</a>
        </header>

        {message && <div className="rounded-xl border border-purple-400/30 bg-purple-500/10 p-4">{message}</div>}

        <section className={panel}>
          <h2 className="mb-2 text-xl font-semibold">一键创建完整备份</h2>
          <p className="mb-4 text-sm text-white/60">使用 SQLite 在线备份机制，尽量保证备份时数据库内容一致；备份完成后会自动执行完整性检查。</p>
          <button disabled={busy} onClick={() => void createBackup()} className="rounded-xl bg-purple-600 px-5 py-3 font-semibold disabled:opacity-40">立即备份整个工作空间</button>
        </section>

        <section className={panel}>
          <div className="mb-4 flex items-center justify-between gap-3">
            <h2 className="text-xl font-semibold">本地备份记录</h2>
            <button disabled={busy} onClick={() => void refresh()} className="rounded-lg border border-white/20 px-3 py-2 text-sm">刷新</button>
          </div>
          {items.length === 0 ? <p className="text-sm text-white/50">暂时没有备份文件。</p> : (
            <div className="space-y-3">
              {items.map(item => (
                <div key={item.name} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/10 p-4">
                  <div className="min-w-0">
                    <div className="break-all text-sm font-medium">{item.name}</div>
                    <div className="mt-1 text-xs text-white/45">{formatSize(item.size_bytes)} · {new Date(item.modified_at).toLocaleString()}</div>
                  </div>
                  <a href={`${API}/api/system/backups/${encodeURIComponent(item.name)}/download`} className="rounded-lg border border-cyan-300/30 px-3 py-2 text-sm text-cyan-100">下载备份</a>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className={panel}>
          <h2 className="mb-2 text-xl font-semibold">完整工作空间归档</h2>
          <p className="mb-4 text-sm text-white/60">除数据库外，还会打包 storage 中的人物/场景/道具图片、视频、音频、字幕和本地配置文件。归档不包含旧备份目录，避免备份套备份。</p>
          <button disabled={busy} onClick={async()=>{setBusy(true);setMessage("正在打包数据库和素材文件，文件较多时可能需要一些时间……");try{const r=await fetch(API+"/api/system/workspace-backups",{method:"POST"});const d=await r.json();if(!r.ok)throw new Error(d.detail||"完整归档失败");setMessage("完整归档已完成："+d.name+"（"+formatSize(d.size_bytes)+"），收录素材文件 "+d.storage_file_count+" 个。");await refresh();}catch(e){setMessage(e instanceof Error?e.message:"完整归档失败");}finally{setBusy(false);}}} className="rounded-xl bg-cyan-700 px-5 py-3 font-semibold disabled:opacity-40">创建完整工作空间归档</button>
          <p className="mt-3 text-xs text-amber-200/80">安全提醒：完整归档可能包含本地 API 密钥加密密钥文件。请只保存在可信设备，不要公开分享或上传到不可信网盘。</p>
          <div className="mt-5 space-y-3">
            {workspaceItems.map(item=><div key={item.name} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/10 p-3"><div className="min-w-0"><div className="break-all text-sm">{item.name}</div><div className="mt-1 text-xs text-white/45">{formatSize(item.size_bytes)} · {new Date(item.modified_at).toLocaleString()}</div></div><a href={API+"/api/system/workspace-backups/"+encodeURIComponent(item.name)+"/download"} className="rounded-lg border border-cyan-300/30 px-3 py-2 text-sm text-cyan-100">下载完整归档</a></div>)}
          </div>
          <div className="mt-6 border-t border-white/10 pt-5">
            <h3 className="font-semibold">从完整归档恢复</h3>
            <p className="mt-2 text-sm text-amber-200/80">此操作会替换数据库和素材文件。系统先自动创建当前工作空间安全归档；有执行中任务时会拒绝恢复。请勿在恢复过程中关闭软件。</p>
            <input type="file" accept=".zip" onChange={e=>setWorkspaceFile(e.target.files?.[0]||null)} className="mt-3 block w-full rounded-lg border border-white/15 bg-black/30 p-3 text-sm"/>
            <label className="mt-3 block text-sm">输入 RESTORE WORKSPACE
              <input value={workspaceConfirmation} onChange={e=>setWorkspaceConfirmation(e.target.value)} placeholder="RESTORE WORKSPACE" className="mt-2 w-full max-w-sm rounded-lg border border-white/15 bg-black/30 p-3"/>
            </label>
            <button disabled={busy||!workspaceFile||workspaceConfirmation!=="RESTORE WORKSPACE"} onClick={async()=>{if(!workspaceFile)return;const selectedWorkspaceFile=workspaceFile;if(!window.confirm("这会替换当前数据库和整个素材目录。系统会先创建当前工作空间安全归档。确定继续吗？"))return;setBusy(true);setMessage("正在验证并恢复完整工作空间，请勿关闭软件……");try{const form=new FormData();form.append("archive_file",selectedWorkspaceFile);form.append("confirmation",workspaceConfirmation);const r=await fetch(API+"/api/system/workspace-backups/restore",{method:"POST",body:form});const d=await r.json();if(!r.ok)throw new Error(d.detail||"恢复失败");setMessage(d.message+" 恢复前安全归档："+(d.safety_archive||"无"));setWorkspaceFile(null);setWorkspaceConfirmation("");await refresh();}catch(e){setMessage(e instanceof Error?e.message:"完整工作空间恢复失败");}finally{setBusy(false);}}} className="mt-3 rounded-xl bg-rose-700 px-5 py-3 font-semibold disabled:opacity-40">恢复完整工作空间</button>
          </div>
        </section>

        <section className={panel}>
          <h2 className="mb-2 text-xl font-semibold">从备份恢复</h2>
          <p className="mb-4 text-sm text-amber-200/80">恢复会替换当前数据库。系统会先自动备份当前数据库；如果正在执行任务，将拒绝恢复。请勿关闭软件，直到操作完成。</p>
          <label className="block text-sm">选择此前下载的 SQLite 备份文件
            <input type="file" accept=".db,.sqlite,.sqlite3" onChange={e => setFile(e.target.files?.[0] || null)} className="mt-2 block w-full rounded-lg border border-white/15 bg-black/30 p-3 text-sm" />
          </label>
          <label className="mt-4 block text-sm">为防止误操作，请输入 RESTORE
            <input value={confirmation} onChange={e => setConfirmation(e.target.value)} placeholder="输入 RESTORE" className="mt-2 w-full max-w-sm rounded-lg border border-white/15 bg-black/30 p-3" />
          </label>
          <button disabled={busy || !file || confirmation !== "RESTORE"} onClick={() => void restoreBackup()} className="mt-4 rounded-xl bg-rose-700 px-5 py-3 font-semibold disabled:opacity-40">确认恢复数据库</button>
        </section>
      </div>
    </main>
  );
}
