"use client";

import { useCallback, useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type LicenseReview =
  | "unverified"
  | "checked_by_user"
  | "commercial_permission_confirmed"
  | "noncommercial_only"
  | "restricted";

type ModelItem = {
  name: string;
  format: string;
  size_bytes: number;
  license_review: LicenseReview;
  license_name: string;
  license_url?: string | null;
  notes: string;
  license_notice: string;
  compatibility: {
    status: string;
    label: string;
    file_size_bytes: number;
    ram_bytes?: number | null;
    vram_bytes?: number | null;
    note: string;
  };
};

type Catalog = {
  items: ModelItem[];
  total: number;
  directory: string;
  network_used: boolean;
  downloaded: boolean;
  note: string;
};

const licenseLabels: Record<LicenseReview, string> = {
  unverified: "未核验",
  checked_by_user: "用户已人工查看",
  commercial_permission_confirmed: "用户确认允许商用",
  noncommercial_only: "仅限非商业用途",
  restricted: "限制使用 / 暂不可用",
};

function size(value?: number | null) {
  if (value === undefined || value === null) return "未知";
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(2)} GB`;
}

export default function LocalModelsPage() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [selected, setSelected] = useState("");
  const [licenseReview, setLicenseReview] = useState<LicenseReview>("unverified");
  const [licenseName, setLicenseName] = useState("");
  const [licenseUrl, setLicenseUrl] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const refresh = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${API}/api/local-models`, { cache: "no-store" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "无法读取本地模型库");
      setCatalog(result);
      setSelected((current) => current || result.items[0]?.name || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取模型库失败");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const chooseModel = (item: ModelItem) => {
    setSelected(item.name);
    setLicenseReview(item.license_review);
    setLicenseName(item.license_name || "");
    setLicenseUrl(item.license_url || "");
    setNotes(item.notes || "");
    setMessage("");
  };

  const saveReview = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selected) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch(`${API}/api/local-models/review`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: selected,
          license_review: licenseReview,
          license_name: licenseName,
          license_url: licenseUrl.trim() || null,
          notes,
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "保存许可证备注失败");
      setMessage("模型资料已保存。许可证内容由你填写，软件没有替你核验授权。");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  const selectedItem = catalog?.items.find((item) => item.name === selected);

  return (
    <main className="cosmic min-h-screen p-5 text-white md:p-8">
      <div className="mx-auto max-w-6xl space-y-6">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold">本地 AI 模型管理</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-white/60">
              管理已经放入本机模型目录的文件，查看粗略硬件适配建议，并记录你自己核对过的许可证信息。
            </p>
            <a href="/system-diagnostics" className="mt-3 inline-flex rounded-lg border border-purple-300/30 px-3 py-2 text-sm">返回系统诊断中心</a>
          </div>
          <button disabled={busy} onClick={() => void refresh()} className="rounded-xl bg-purple-600 px-4 py-3 text-sm font-semibold disabled:opacity-40">
            {busy ? "处理中…" : "刷新模型库"}
          </button>
        </header>

        <section className="rounded-2xl border border-emerald-300/20 bg-emerald-400/5 p-4">
          <div className="font-semibold text-emerald-100">严格本地管理模式</div>
          <p className="mt-2 text-sm leading-6 text-white/65">
            本页面不会联网、下载、启动、删除或修改模型文件，也不会调用云端 API。不会产生模型服务费或下载流量费用。
            本地运行仍会使用电脑硬件和电力。许可证信息需要你自行查阅官方来源，软件不会把用户填写的状态当作法律核验结果。
          </p>
        </section>

        {error && <div className="rounded-xl border border-rose-400/30 bg-rose-500/10 p-4 text-rose-200">{error}</div>}
        {message && <div className="rounded-xl border border-emerald-400/30 bg-emerald-500/10 p-4 text-emerald-100">{message}</div>}

        {catalog && (
          <>
            <section className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="font-semibold">模型文件概览</h2>
                  <p className="mt-1 break-all text-xs text-white/45">{catalog.directory}</p>
                </div>
                <div className="rounded-full bg-white/10 px-3 py-1 text-xs text-white/70">{catalog.total} 个文件</div>
              </div>
              <p className="mt-3 text-xs text-white/50">{catalog.note}</p>
            </section>

            <div className="grid gap-5 lg:grid-cols-[1.1fr_0.9fr]">
              <section className="space-y-3">
                <h2 className="font-semibold">已发现的模型文件</h2>
                {catalog.items.length === 0 ? (
                  <div className="rounded-2xl border border-white/10 bg-slate-950/60 p-6 text-sm leading-6 text-white/60">
                    暂时没有发现模型文件。将模型文件放入上方模型目录后，点击“刷新模型库”。请先确认模型来源、许可证、文件大小和电脑配置。
                  </div>
                ) : catalog.items.map((item) => (
                  <button
                    type="button"
                    key={item.name}
                    onClick={() => chooseModel(item)}
                    className={`w-full rounded-2xl border p-4 text-left transition ${selected === item.name ? "border-cyan-300/60 bg-cyan-400/10" : "border-white/10 bg-slate-950/60 hover:border-white/25"}`}
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="break-all font-semibold">{item.name}</div>
                        <div className="mt-1 text-xs text-white/45">{item.format.toUpperCase()} · {size(item.size_bytes)}</div>
                      </div>
                      <span className="rounded-full bg-white/10 px-3 py-1 text-xs text-white/70">{licenseLabels[item.license_review]}</span>
                    </div>
                    <div className="mt-3 text-sm text-cyan-100">{item.compatibility.label}</div>
                    <div className="mt-1 text-xs leading-5 text-white/45">{item.compatibility.note}</div>
                  </button>
                ))}
              </section>

              <section className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-5">
                <h2 className="font-semibold">许可证与使用记录</h2>
                {!selectedItem ? (
                  <p className="mt-4 text-sm text-white/50">请先从左侧选择一个模型文件。</p>
                ) : (
                  <form onSubmit={saveReview} className="mt-4 space-y-4">
                    <div className="rounded-xl bg-white/[0.04] p-3">
                      <div className="text-xs text-white/45">当前模型</div>
                      <div className="mt-1 break-all text-sm font-semibold">{selectedItem.name}</div>
                      <div className="mt-2 text-xs text-cyan-100">{selectedItem.compatibility.label}</div>
                      <div className="mt-1 text-xs text-white/45">内存：{size(selectedItem.compatibility.ram_bytes)} · 显存：{size(selectedItem.compatibility.vram_bytes)}</div>
                    </div>

                    <label className="block space-y-2 text-sm">
                      <span className="text-white/70">许可证核对状态</span>
                      <select value={licenseReview} onChange={(event) => setLicenseReview(event.target.value as LicenseReview)} className="w-full rounded-lg border border-white/15 bg-slate-900 px-3 py-3 text-white">
                        <option value="unverified">未核验</option>
                        <option value="checked_by_user">我已人工查看许可证</option>
                        <option value="commercial_permission_confirmed">我已确认允许商用</option>
                        <option value="noncommercial_only">仅限非商业用途</option>
                        <option value="restricted">限制使用 / 暂不可用</option>
                      </select>
                    </label>

                    <label className="block space-y-2 text-sm">
                      <span className="text-white/70">许可证名称</span>
                      <input value={licenseName} onChange={(event) => setLicenseName(event.target.value)} maxLength={160} placeholder="例如：模型页面列出的许可证名称" className="w-full rounded-lg border border-white/15 bg-slate-900 px-3 py-3 text-white placeholder:text-white/30" />
                    </label>

                    <label className="block space-y-2 text-sm">
                      <span className="text-white/70">官方许可证页面（可选）</span>
                      <input type="url" value={licenseUrl} onChange={(event) => setLicenseUrl(event.target.value)} placeholder="https://…" className="w-full rounded-lg border border-white/15 bg-slate-900 px-3 py-3 text-white placeholder:text-white/30" />
                    </label>

                    <label className="block space-y-2 text-sm">
                      <span className="text-white/70">个人核对备注</span>
                      <textarea value={notes} onChange={(event) => setNotes(event.target.value)} maxLength={2000} rows={4} placeholder="记录商用限制、署名要求、再分发限制等…" className="w-full rounded-lg border border-white/15 bg-slate-900 px-3 py-3 text-white placeholder:text-white/30" />
                    </label>

                    <div className="rounded-xl border border-amber-300/20 bg-amber-400/5 p-3 text-xs leading-5 text-amber-100/80">
                      重要：这里的状态是你自己的记录，不代表软件已经验证许可证，也不构成法律意见。请以模型作者和官方发布页面的完整条款为准。
                    </div>
                    <button disabled={busy} type="submit" className="w-full rounded-xl bg-purple-600 px-4 py-3 text-sm font-semibold disabled:opacity-40">
                      {busy ? "正在保存…" : "保存许可证核对记录"}
                    </button>
                  </form>
                )}
              </section>
            </div>
          </>
        )}
      </div>
    </main>
  );
}
