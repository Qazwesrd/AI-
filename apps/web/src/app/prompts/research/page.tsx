"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type Prompt = { id: number; name: string; current_version: number; content: string };
type Evidence = { id: number; platform: string; work_title: string; genre:string; work_url: string; observed_at: string; metric_summary: Record<string, unknown>; reliability: string; is_verified: boolean; verification_note: string };
type Candidate = { id: number; prompt_id: number; base_version: number; proposed_name: string; proposed_content: string; change_reason: string; expected_impact: string; evidence_ids: number[]; status: string; decision_note: string };
type TrendRank = { rank:number; work_title:string; platforms:string[]; platform_count:number; observed_days:string[]; sample_count:number; latest_observed_at:string; trend_score:number; evidence_types:string[]; contains_public_prompt:boolean; evidence_ids:number[]; score_method:string };
type ResearchSource = { id:number; platform:string; name:string; feed_url:string; enabled:boolean; refresh_interval_minutes:number; last_collected_at:string|null; last_status:string; last_error:string };
type CollectionRun = { id:number; source_id:number; status:string; discovered_count:number; inserted_count:number; duplicate_count:number; error_message:string; started_at:string|null };
type CategoryRankItem = {rank:number;title:string;platforms?:string[];observed_days?:string[];latest_observed_at?:string;growth_rate_pct?:number|null;sample_count?:number;score:number;average_score?:number;test_count?:number;version?:number;score_basis?:string};
type RankingFreshness = {mode:string;ui_poll_seconds:number;collector_scheduler_seconds:number;latest_verified_evidence_created_at:string|null;latest_observed_at:string|null;latest_source_collection_at:string|null;latest_snapshot_at:string|null};
type CategoryRankings = {drama:{title:string;items:CategoryRankItem[];genre_items:{rank:number;title:string;work_count:number;sample_count:number;score:number;platforms:string[]}[];verified_evidence_count:number};prompt:{title:string;items:CategoryRankItem[];public_share_items:CategoryRankItem[];verified_evidence_count:number};screenplay:{title:string;items:CategoryRankItem[];verified_evidence_count:number}};
type RankingHistoryItem = {id:number;category:string;created_at:string|null;ranking:CategoryRankItem[]};

export default function PromptResearchPage() {
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [trendRanking, setTrendRanking] = useState<TrendRank[]>([]);
  const [categoryRankings,setCategoryRankings]=useState<CategoryRankings|null>(null);
  const [rankingsUpdatedAt,setRankingsUpdatedAt]=useState<string>("");
  const [rankingFreshness,setRankingFreshness]=useState<RankingFreshness|null>(null);
  const [rankTab,setRankTab]=useState<"drama"|"prompt"|"screenplay">("drama");
  const [rankingHistory,setRankingHistory]=useState<RankingHistoryItem[]>([]);
  const [historyLoading,setHistoryLoading]=useState(false);
  const [sources, setSources] = useState<ResearchSource[]>([]);
  const [collectionRuns, setCollectionRuns] = useState<CollectionRun[]>([]);
  const [sourcePlatform, setSourcePlatform] = useState("YouTube");
  const [sourceName, setSourceName] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceInterval, setSourceInterval] = useState("360");
  const [evidenceType, setEvidenceType] = useState("作品趋势证据");
  const [selectedPrompt, setSelectedPrompt] = useState("");
  const [platform, setPlatform] = useState("红果");
  const [workTitle, setWorkTitle] = useState("");
  const [workUrl, setWorkUrl] = useState("");
  const [observedAt, setObservedAt] = useState("");
  const [metrics, setMetrics] = useState("");
  const [verifyNote, setVerifyNote] = useState("已人工核对公开页面、榜单位置及采样日期。");
  const [verifyMetrics,setVerifyMetrics]=useState("{}");
  const [evidenceClassifications,setEvidenceClassifications]=useState<Record<number,string>>({});
  const [evidenceGenres,setEvidenceGenres]=useState<Record<number,string>>({});
  const [genre,setGenre]=useState("未分类");
  const [candidateName, setCandidateName] = useState("");
  const [candidateContent, setCandidateContent] = useState("");
  const [reason, setReason] = useState("");
  const [impact, setImpact] = useState("");
  const [selectedEvidence, setSelectedEvidence] = useState<number[]>([]);
  const [decisionNote, setDecisionNote] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function api<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(API + path, init);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "请求失败：" + response.status);
    return data as T;
  }

  async function load() {
    try {
      const [p, e, c, t, s, r, k] = await Promise.all([
        api<{items: Prompt[]}>("/api/prompts"),
        api<{items: Evidence[]}>("/api/prompts/research/evidence"),
        api<{items: Candidate[]}>("/api/prompts/research/candidates"),
        api<{items: TrendRank[]}>("/api/prompts/research/trends/ranking"),
        api<{items: ResearchSource[]}>("/api/prompts/research/sources"),
        api<{items: CollectionRun[]}>("/api/prompts/research/sources/runs"),
        api<{categories:CategoryRankings;generated_at?:string;freshness?:RankingFreshness}>("/api/prompts/research/rankings"),
      ]);
      setPrompts(p.items || []);
      setEvidence(e.items || []);
      setCandidates(c.items || []);
      setTrendRanking(t.items || []);
      setCategoryRankings(k.categories || null);
      setRankingsUpdatedAt(k.generated_at || new Date().toISOString());
      setRankingFreshness(k.freshness||null);
      setSources(s.items || []);
      setCollectionRuns(r.items || []);
      if (!selectedPrompt && p.items?.length) setSelectedPrompt(String(p.items[0].id));
    } catch (error) { setMessage(error instanceof Error ? error.message : "加载研究数据失败"); }
  }

  async function refreshLiveRankings(){
    try{
      const [ranking,trend]=await Promise.all([
        api<{categories:CategoryRankings;generated_at?:string;freshness?:RankingFreshness}>("/api/prompts/research/rankings"),
        api<{items:TrendRank[]}>("/api/prompts/research/trends/ranking"),
      ]);
      setCategoryRankings(ranking.categories||null);
      setRankingsUpdatedAt(ranking.generated_at||new Date().toISOString());
      setRankingFreshness(ranking.freshness||null);
      setTrendRanking(trend.items||[]);
    }catch(error){
      setMessage(error instanceof Error?error.message:"实时排名刷新失败；保留上次成功数据。");
    }
  }

  useEffect(() => {
    setObservedAt(new Date().toISOString().slice(0, 10));
    void load();
    const timer=window.setInterval(()=>{if(document.visibilityState==="visible")void refreshLiveRankings();},15000);
    const onVisible=()=>{if(document.visibilityState==="visible")void refreshLiveRankings();};
    document.addEventListener("visibilitychange",onVisible);
    return ()=>{window.clearInterval(timer);document.removeEventListener("visibilitychange",onVisible);};
  }, []);

  const currentPrompt = prompts.find(p => String(p.id) === selectedPrompt) || null;

  function suggestPublicFeedAdapter() {
    const raw = sourceUrl.trim();
    if (!raw) { setMessage("请先粘贴公开频道、社区或 RSS 页面地址。"); return; }
    try {
      const url = new URL(raw);
      if (url.protocol !== "https:") { setMessage("只接受 HTTPS 公开来源。"); return; }
      let feedUrl = url.toString();
      let suggestedName = sourceName.trim();
      const host = url.hostname.toLowerCase().replace(/^www\./, "");
      if (host === "reddit.com") {
        const subreddit = url.pathname.match(/^\/r\/([A-Za-z0-9_]+)\/?$/);
        const user = url.pathname.match(/^\/user\/([A-Za-z0-9_-]+)\/?$/);
        if (subreddit) {
          feedUrl = `https://www.reddit.com/r/${subreddit[1]}/new/.rss`;
          suggestedName ||= `Reddit · r/${subreddit[1]} 最新公开内容`;
        } else if (user) {
          feedUrl = `https://www.reddit.com/user/${user[1]}/submitted/.rss`;
          suggestedName ||= `Reddit · ${user[1]} 公开投稿`;
        } else if (/\.(rss|xml)$/i.test(url.pathname)) {
          suggestedName ||= `Reddit · ${url.pathname.split("/").filter(Boolean).slice(-1)[0] || "公开订阅"}`;
        } else {
          setMessage("已识别 Reddit，但请粘贴具体 subreddit 或用户主页地址，才能生成对应 RSS。");
          return;
        }
      } else if (host === "youtube.com" || host === "m.youtube.com") {
        const channel = url.pathname.match(/^\/channel\/(UC[A-Za-z0-9_-]{20,})\/?$/);
        if (channel) {
          feedUrl = `https://www.youtube.com/feeds/videos.xml?channel_id=${channel[1]}`;
          suggestedName ||= `YouTube · ${channel[1]}`;
        } else if (url.pathname === "/feeds/videos.xml" && url.searchParams.get("channel_id")) {
          suggestedName ||= `YouTube · ${url.searchParams.get("channel_id")}`;
        } else {
          setMessage("YouTube 自动适配需要频道 ID（/channel/UC…）；@用户名链接暂不能可靠反查频道 ID，请粘贴频道 RSS 地址。");
          return;
        }
      } else {
        suggestedName ||= `${sourcePlatform} · ${url.hostname}${url.pathname === "/" ? "" : url.pathname}`;
        if (!/\.(rss|xml)$/i.test(url.pathname) && !/feed|atom/i.test(url.pathname + url.search)) {
          setSourceName(suggestedName);
          setMessage("已自动生成来源名称。该平台地址未发现可安全推导的官方 RSS/Atom 规则，请粘贴平台实际公开订阅地址后再添加；不会猜造地址。");
          return;
        }
      }
      setSourceName(suggestedName);
      setSourceUrl(feedUrl);
      setMessage("已生成来源名称和 RSS/Atom 地址。点击“添加公开来源”时还会进行 HTTPS、域名和订阅地址校验；首次采集成功后才能确认内容可用。");
    } catch {
      setMessage("地址格式无效，请粘贴完整的 HTTPS 公开页面或 RSS/Atom 地址。");
    }
  }

  async function addPublicSource() {
    if (!sourceUrl.trim()) return setMessage("请先生成或填写公开 RSS/Atom HTTPS 地址；来源名称可以留空，由系统自动生成。");
    setBusy(true);
    try {
      const result = await api<{verified?:boolean;verified_item_count?:number;item?:ResearchSource}>("/api/prompts/research/sources", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({platform:sourcePlatform, name:sourceName, feed_url:sourceUrl, refresh_interval_minutes:Number(sourceInterval)}),
      });
      setSourceName(""); setSourceUrl("");
      setMessage(result.verified
        ? `公开来源已添加并完成真实订阅验证，发现 ${result.verified_item_count ?? 0} 条目。来源名称可由系统自动生成。`
        : "公开来源已添加，请在列表中查看验证状态。");
      await load();
    } catch(error) { setMessage(error instanceof Error ? error.message : "添加来源失败"); }
    finally { setBusy(false); }
  }

  async function loadRankingHistory(category:"drama"|"prompt"|"screenplay"){
    setHistoryLoading(true);
    try{
      const result=await api<{items:RankingHistoryItem[]}>(`/api/prompts/research/rankings/history?category=${category}`);
      setRankingHistory(result.items||[]);
    }catch(e){setMessage(e instanceof Error?e.message:"读取排行榜历史失败");}
    finally{setHistoryLoading(false);}
  }

  async function refreshAllRankings(){
    setBusy(true);
    try{
      const result=await api<{categories:CategoryRankings}>("/api/prompts/research/rankings/refresh",{method:"POST"});
      setCategoryRankings(result.categories);
      setRankingsUpdatedAt(new Date().toISOString());
      await refreshLiveRankings();
      setMessage("漫剧、提示词、剧本三类排名已重新计算并保存历史快照。");
    }catch(error){setMessage(error instanceof Error?error.message:"刷新分类排名失败");}
    finally{setBusy(false);}
  }

  async function collectDueSources() {
    setBusy(true);
    try {
      const result = await api<{processed_count:number; due_count:number}>("/api/prompts/research/sources/collect-due", {method:"POST"});
      setMessage(`到期来源处理完成：待采集 ${result.due_count} 个，本次处理 ${result.processed_count} 个。`);
      await load();
    } catch(error) { setMessage(error instanceof Error ? error.message : "批量采集失败"); }
    finally { setBusy(false); }
  }

  async function collectSource(id:number) {
    setBusy(true);
    try {
      const result = await api<{message:string}>(`/api/prompts/research/sources/${id}/collect`, {method:"POST"});
      setMessage(result.message);
      await load();
    } catch(error) { setMessage(error instanceof Error ? error.message : "采集失败"); }
    finally { setBusy(false); }
  }

  async function toggleSource(source:ResearchSource) {
    setBusy(true);
    try {
      await api(`/api/prompts/research/sources/${source.id}`, {
        method:"PATCH", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({enabled:!source.enabled}),
      });
      await load();
    } catch(error) { setMessage(error instanceof Error ? error.message : "更新来源失败"); }
    finally { setBusy(false); }
  }

  async function addEvidence() {
    if (!platform.trim() || !workTitle.trim() || !observedAt) return setMessage("请填写平台、作品名称和采样日期。");
    setBusy(true);
    try {
      let metric_summary: Record<string, unknown> = {};
      if (metrics.trim()) metric_summary = JSON.parse(metrics);
      await api("/api/prompts/research/evidence", {
        method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({platform, work_title:workTitle, genre, work_url:workUrl, observed_at:observedAt, metric_summary, evidence_type:evidenceType}),
      });
      setWorkTitle(""); setWorkUrl(""); setMetrics(""); setGenre("未分类");
      setMessage("证据已保存为待核验；系统不会仅凭一次榜单记录认定爆款。");
      await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "保存证据失败；指标请使用合法 JSON"); }
    finally { setBusy(false); }
  }

  async function verifyEvidence(id: number) {
    setBusy(true);
    try {
      const metric_summary:Record<string,unknown>=verifyMetrics.trim()?JSON.parse(verifyMetrics):{};
      await api(`/api/prompts/research/evidence/${id}/verify`, {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({verification_note:verifyNote,evidence_type:evidenceClassifications[id]||"漫剧作品趋势证据",genre:evidenceGenres[id]||evidence.find(item=>item.id===id)?.genre||"未分类",metric_summary}),
      });
      setMessage("证据已标记为人工核验。请确保来源、日期和指标真实可追溯。");
      await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "核验证据失败"); }
    finally { setBusy(false); }
  }

  async function createCandidate() {
    if (!currentPrompt) return setMessage("请先选择正式提示词。");
    if (!candidateName.trim() || !candidateContent.trim() || !reason.trim()) return setMessage("请填写候选名称、正文和修改理由。");
    if (selectedEvidence.length < 2) return setMessage("至少选择两条已核验证据。");
    setBusy(true);
    try {
      await api("/api/prompts/research/candidates", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
          prompt_id:currentPrompt.id, base_version:currentPrompt.current_version,
          proposed_name:candidateName, proposed_content:candidateContent,
          change_reason:reason, expected_impact:impact, evidence_ids:selectedEvidence,
        }),
      });
      setCandidateName(""); setCandidateContent(""); setReason(""); setImpact(""); setSelectedEvidence([]);
      setMessage("升级候选已创建，正式提示词没有变化；需你在下方明确批准后才会生效。");
      await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "创建候选失败"); }
    finally { setBusy(false); }
  }

  async function decideCandidate(id: number, decision: "approve" | "reject") {
    const prompt = decision === "approve"
      ? "确认批准该候选并更新正式提示词？系统将创建新版本并保留旧版本。"
      : "确认拒绝该候选？正式提示词不会变化。";
    if (!window.confirm(prompt)) return;
    setBusy(true);
    try {
      const result = await api<{message:string}>(`/api/prompts/research/candidates/${id}/${decision}`, {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({decision_note:decisionNote}),
      });
      setMessage(result.message);
      await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "处理候选失败"); }
    finally { setBusy(false); }
  }

  return (
    <main className="min-h-screen p-5 md:p-9 text-white">
      <div className="mx-auto max-w-7xl space-y-6">
        <header>
          <a href="/prompts" className="text-sm text-cyan-200 hover:underline">← 返回提示词中心</a>
          <p className="mt-4 text-xs tracking-[4px] text-fuchsia-300">TREND RESEARCH</p>
          <h1 className="mt-2 text-3xl font-black">爆款研究证据库与提示词升级审批</h1>
          <p className="mt-2 text-sm text-purple-200/70">公开来源或人工录入 → 人工核验 → 多平台、多日期交叉验证 → 生成候选 → 你明确批准后更新正式版本。</p>
        </header>
        {message && <div role="status" className="rounded-xl border border-cyan-300/20 bg-cyan-950/30 p-3 text-sm text-cyan-100">{message}</div>}

        <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h2 className="text-lg font-bold">公开来源自动采集适配器</h2><p className="mt-1 text-xs text-white/50">添加平台允许访问的公开 RSS/Atom 地址；后台每 15 分钟检查到期任务，按每个来源设置的间隔采集。</p></div>
            <div className="flex gap-2"><button disabled={busy} onClick={()=>void collectDueSources()} className="rounded-xl bg-cyan-700 px-4 py-2 text-sm text-white">采集全部到期来源</button><button disabled={busy} onClick={()=>void load()} className="rounded-xl border border-cyan-300/30 px-4 py-2 text-sm text-cyan-100">刷新来源状态</button></div>
          </div>
          <div className="mt-3 rounded-lg border border-amber-300/20 bg-amber-950/20 p-3 text-xs text-amber-100/80">
            安全边界：仅 HTTPS；仅允许 YouTube、B站、抖音、快手、TikTok、Facebook、红果等平台域名；只读取 RSS/Atom 标题、链接、日期和短摘要；不模拟登录、不绕过访问限制。当前不是这些平台全部都提供 RSS，必须填写真实可用的公开订阅地址。自动发现的数据一律待人工核验。
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-4">
            <label className="text-xs">平台
              <select value={sourcePlatform} onChange={e=>setSourcePlatform(e.target.value)} className="mt-1 w-full rounded-lg bg-[#171326] p-2 text-sm">
                {["YouTube","B站","抖音","快手","TikTok","Facebook","红果短剧","Reddit","Royal Road","Wattpad","Tapas","Inkitt","Pixiv"].map(x=><option key={x}>{x}</option>)}
              </select>
            </label>
            <label className="text-xs">来源名称（可留空自动生成）<input value={sourceName} onChange={e=>setSourceName(e.target.value)} placeholder="留空则按平台和域名自动生成" className="mt-1 w-full rounded-lg bg-black/20 p-2 text-sm"/></label>
            <label className="text-xs">刷新间隔
              <select value={sourceInterval} onChange={e=>setSourceInterval(e.target.value)} className="mt-1 w-full rounded-lg bg-[#171326] p-2 text-sm">
                <option value="15">每 15 分钟</option><option value="30">每 30 分钟</option><option value="60">每 1 小时</option><option value="180">每 3 小时</option><option value="360">每 6 小时</option><option value="720">每 12 小时</option><option value="1440">每天</option><option value="10080">每周</option>
              </select>
            </label>
            <div className="flex items-end"><button disabled={busy} onClick={()=>void addPublicSource()} className="w-full rounded-lg bg-fuchsia-600 px-3 py-2 text-sm font-semibold disabled:opacity-50">添加公开来源</button></div>
          </div>
          <label className="mt-3 block text-xs">公开频道 / 社区 / RSS 页面地址
            <input value={sourceUrl} onChange={e=>setSourceUrl(e.target.value)} placeholder="粘贴 YouTube 频道 ID 页面、Reddit 社区页或实际 RSS/Atom 地址" className="mt-1 w-full rounded-lg bg-black/20 p-2 text-sm"/>
          </label>
          <button disabled={busy} onClick={suggestPublicFeedAdapter} className="mt-2 rounded-lg border border-cyan-300/30 px-3 py-2 text-xs text-cyan-100">自动识别来源名称并生成 RSS/Atom 地址</button>
          <div className="mt-4 space-y-2">
            {sources.map(source=><div key={source.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/10 bg-black/20 p-3">
              <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><strong className="text-sm">{source.name}</strong><span className="text-xs text-cyan-200">{source.platform}</span><span className={source.enabled?"text-xs text-emerald-300":"text-xs text-white/40"}>{source.enabled?"自动采集已启用":"已暂停"}</span></div>
                <p className="mt-1 break-all text-[11px] text-white/40">{source.feed_url}</p><p className="mt-1 text-xs text-white/50">间隔 {source.refresh_interval_minutes} 分钟 · 状态 {source.last_status} · 最近采集 {source.last_collected_at||"尚未采集"}</p>
                {source.last_error&&<p className="mt-1 text-xs text-amber-200">{source.last_error}</p>}
              </div>
              <div className="flex gap-2"><button disabled={busy} onClick={()=>void collectSource(source.id)} className="rounded-lg border border-cyan-300/30 px-3 py-2 text-xs text-cyan-100">立即采集</button><button disabled={busy} onClick={()=>void toggleSource(source)} className="rounded-lg border border-white/15 px-3 py-2 text-xs">{source.enabled?"暂停":"启用"}</button></div>
            </div>)}
            {!sources.length&&<p className="py-5 text-center text-sm text-white/40">还没有配置公开订阅源。添加一个真实可访问的 RSS/Atom 地址即可开始。</p>}
          </div>
          <details className="mt-4"><summary className="cursor-pointer text-xs text-cyan-200">查看最近采集记录（{collectionRuns.length}）</summary>
            <div className="mt-2 space-y-2">{collectionRuns.slice(0,20).map(run=><div key={run.id} className="rounded-lg border border-white/10 p-2 text-xs text-white/60">来源 #{run.source_id} · {run.status} · 发现 {run.discovered_count} · 新增 {run.inserted_count} · 重复 {run.duplicate_count} {run.error_message&&"· "+run.error_message}</div>)}</div>
          </details>
        </section>


        <section className="rounded-2xl border border-fuchsia-300/20 bg-fuchsia-950/10 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h2 className="text-xl font-bold">爆款分类排行榜 · 自动刷新</h2><p className="mt-1 text-xs text-white/50">榜单每 15 秒自动重新读取并计算；切回页面时立即刷新。来源数据按各自采集间隔更新，不把延迟数据伪装成实时数据。</p><p className="mt-1 text-xs text-emerald-200">最近刷新：{rankingsUpdatedAt?new Date(rankingsUpdatedAt).toLocaleString():"等待首次更新"} · 最新已核验证据记录创建：{rankingFreshness?.latest_verified_evidence_created_at?new Date(rankingFreshness.latest_verified_evidence_created_at).toLocaleString():"暂无"} · 最新样本观察日：{rankingFreshness?.latest_observed_at||"暂无"} · 最近公开来源采集：{rankingFreshness?.latest_source_collection_at?new Date(rankingFreshness.latest_source_collection_at).toLocaleString():"暂无"} · 后台采集检查：每 {Math.round((rankingFreshness?.collector_scheduler_seconds||900)/60)} 分钟</p></div>
            <button disabled={busy} onClick={()=>void refreshAllRankings()} className="rounded-xl bg-fuchsia-700 px-4 py-2 text-sm font-semibold disabled:opacity-50">刷新并保存三类榜单</button>
            <button disabled={historyLoading} onClick={()=>void loadRankingHistory(rankTab)} className="rounded-xl border border-white/15 px-4 py-2 text-sm disabled:opacity-50">{historyLoading?"读取历史中…":"查看当前分类历史快照"}</button>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {(["drama","prompt","screenplay"] as const).map(key=><button key={key} onClick={()=>{setRankTab(key);setRankingHistory([]);}} className={rankTab===key?"rounded-lg bg-cyan-700 px-4 py-2 text-sm":"rounded-lg border border-white/15 px-4 py-2 text-sm text-white/70"}>{key==="drama"?"漫剧作品排名":key==="prompt"?"提示词排名":"剧本排名"}</button>)}
          </div>
          {rankingHistory.length>0&&<section className="mt-3 rounded-xl border border-white/10 bg-black/20 p-3"><h3 className="text-sm font-semibold">历史排名快照 · {rankTab==="drama"?"漫剧":rankTab==="prompt"?"提示词":"剧本"}</h3><div className="mt-2 space-y-2">{rankingHistory.map(snapshot=><details key={snapshot.id} className="rounded-lg border border-white/10 p-2"><summary className="cursor-pointer text-xs">{snapshot.created_at?new Date(snapshot.created_at).toLocaleString():"时间未知"} · {snapshot.ranking.length} 个排名项目</summary><ol className="mt-2 space-y-1 text-xs text-white/70">{snapshot.ranking.slice(0,20).map((item,index)=><li key={item.title||index}>{item.rank||index+1}. {item.title} · {item.score??item.average_score??"—"}</li>)}</ol></details>)}</div></section>}
          {categoryRankings&&(()=>{
            const current=categoryRankings[rankTab];
            return <div className="mt-4 overflow-x-auto">
              <div className="mb-2 text-xs text-white/50">{current.title} · 已核验证据 {current.verified_evidence_count||0} 条</div>
              {rankTab==="drama"&&<div className="mb-4"><h3 className="mb-2 text-sm font-semibold">漫剧题材类别排名</h3><table className="w-full min-w-[520px] text-left text-sm"><thead className="text-xs text-white/50"><tr><th className="p-2">排名</th><th className="p-2">题材类别</th><th className="p-2">作品数</th><th className="p-2">样本数</th><th className="p-2">平均趋势分</th><th className="p-2">覆盖平台</th></tr></thead><tbody>{categoryRankings.drama.genre_items.map(item=><tr key={item.title} className="border-t border-white/10"><td className="p-2 font-bold">{item.rank}</td><td className="p-2">{item.title}</td><td className="p-2">{item.work_count}</td><td className="p-2">{item.sample_count}</td><td className="p-2 text-cyan-200">{item.score}</td><td className="p-2 text-xs">{item.platforms.join("、")}</td></tr>)}</tbody></table>{!categoryRankings.drama.genre_items.length&&<p className="py-3 text-xs text-white/40">尚无足够的已核验题材样本。</p>}</div>}
              <h3 className="mb-2 text-sm font-semibold">{rankTab==="drama"?"漫剧作品热度排名":current.title}</h3>
              <table className="w-full min-w-[620px] text-left text-sm">
                <thead className="text-xs text-white/50"><tr><th className="p-2">排名</th><th className="p-2">名称</th><th className="p-2">平台/版本</th><th className="p-2">样本</th>{rankTab!=="prompt"&&<><th className="p-2">最近样本日期</th><th className="p-2">互动变化</th></>}<th className="p-2">评分</th><th className="p-2">评分口径</th></tr></thead>
                <tbody>{current.items.map(item=><tr key={item.title+"-"+(item.version||0)} className="border-t border-white/10">
                  <td className="p-2 font-bold">{item.rank}</td><td className="p-2">{item.title}</td><td className="p-2 text-xs">{item.platforms?.join("、")||("V"+(item.version||1))}</td><td className="p-2">{item.sample_count||item.test_count||0}</td>{rankTab!=="prompt"&&<><td className="p-2 text-xs">{item.latest_observed_at||"未知"}</td><td className="p-2 text-xs">{item.growth_rate_pct==null?"样本不足":((item.growth_rate_pct>0?"+":"")+item.growth_rate_pct+"%")}</td></>}<td className="p-2 text-cyan-200">{item.score}</td><td className="p-2 text-xs text-white/50">{item.score_basis||"实测评分"}</td>
                </tr>)}</tbody>
              </table>
              {!current.items.length&&<p className="py-6 text-center text-sm text-white/40">该类别暂时没有满足条件的已核验证据或实测评分。可在下方录入公开数据或到提示词中心记录实测结果。</p>}
              {rankTab==="prompt"&&<div className="mt-4 rounded-xl border border-white/10 p-3"><strong className="text-sm">公开分享提示词热度（独立参考）</strong><p className="mt-1 text-xs text-white/50">此处反映公开分享内容的互动表现，不代表提示词实际生成质量。</p>{categoryRankings.prompt.public_share_items.slice(0,10).map(item=><div key={item.title} className="mt-2 flex justify-between gap-3 text-xs"><span>{item.rank}. {item.title}</span><span className="text-cyan-200">{item.score}</span></div>)}</div>}
            </div>;
          })()}
        </section>
        <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h2 className="text-lg font-bold">跨平台公开趋势榜</h2><p className="mt-1 text-xs text-white/50">只统计已人工核验、至少两个平台且跨两个日期的证据；这是研究排序，不是平台官方榜单。</p></div>
            <button disabled={busy} onClick={()=>void load()} className="rounded-xl border border-cyan-300/30 px-4 py-2 text-sm text-cyan-100 disabled:opacity-50">↻ 刷新趋势排名</button>
          </div>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[680px] text-left text-sm">
              <thead className="text-xs text-white/50"><tr><th className="p-2">排名</th><th className="p-2">作品</th><th className="p-2">平台</th><th className="p-2">采样日</th><th className="p-2">证据数</th><th className="p-2">趋势分</th><th className="p-2">证据类型</th></tr></thead>
              <tbody>{trendRanking.map(item=><tr key={item.work_title} className="border-t border-white/10">
                <td className="p-2 font-bold">{item.rank}</td><td className="p-2">{item.work_title}</td><td className="p-2">{item.platforms.join("、")}</td><td className="p-2">{item.observed_days.join("、")}</td><td className="p-2">{item.sample_count}</td><td className="p-2 text-cyan-200">{item.trend_score}</td><td className="p-2 text-xs text-white/50">{item.contains_public_prompt?"含公开提示词证据":"作品/案例趋势证据"}</td>
              </tr>)}</tbody>
            </table>
            {!trendRanking.length&&<p className="py-5 text-center text-sm text-white/40">暂无满足跨平台、跨日期且已核验条件的作品。</p>}
          </div>
          <p className="mt-2 text-[11px] text-white/40">评分采用公开互动指标对数加权、14天热度衰减和跨平台/跨日期加权。RSS/Atom 自动采集只发现公开条目，互动数值须在人工核验时补录或通过手工证据登记；不能从作品热度推断其内部提示词。</p>
        </section>

        <section className="grid gap-5 lg:grid-cols-2">
          <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
            <h2 className="text-lg font-bold">录入公开作品证据</h2>
            <p className="mt-1 text-xs text-white/50">支持手工登记，也支持已配置的公开 RSS/Atom 来源按采集间隔自动发现条目；新发现内容先进入待核验区，核验后才参与正式排名。平台没有公开接口或订阅源时，不会伪造自动采集结果。</p>
            <label className="mt-3 block text-sm">证据类型
              <select value={evidenceType} onChange={e=>setEvidenceType(e.target.value)} className="mt-1 w-full rounded-lg bg-[#171326] p-2">
                <option value="漫剧作品趋势证据">漫剧作品热度/榜单数据</option>
                <option value="作品趋势证据">其他作品趋势证据</option>
                <option value="剧本作品证据">公开剧本/剧本案例排名证据</option>
                <option value="公开提示词分享">公开分享的提示词原文/教程</option>
                <option value="创作案例证据">公开创作教程/案例</option>
              </select>
            </label>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <label className="text-sm">平台 / 来源<input value={platform} onChange={e=>setPlatform(e.target.value)} className="mt-1 w-full rounded-lg bg-black/20 p-2" /></label>
              <label className="text-sm">作品名称<input value={workTitle} onChange={e=>setWorkTitle(e.target.value)} className="mt-1 w-full rounded-lg bg-black/20 p-2" /></label>
              <label className="text-sm">漫剧/剧本题材<select value={genre} onChange={e=>setGenre(e.target.value)} className="mt-1 w-full rounded-lg bg-black/20 p-2">{["未分类","玄幻","仙侠","都市","言情","悬疑","科幻","历史","武侠","末日","校园","其他"].map(x=><option key={x}>{x}</option>)}</select></label>
              <label className="text-sm">采样日期<input type="date" value={observedAt} onChange={e=>setObservedAt(e.target.value)} className="mt-1 w-full rounded-lg bg-black/20 p-2" /></label>
              <label className="text-sm">公开页面链接<input value={workUrl} onChange={e=>setWorkUrl(e.target.value)} placeholder="https://" className="mt-1 w-full rounded-lg bg-black/20 p-2" /></label>
            </div>
            <label className="mt-3 block text-sm">榜单、播放、互动等指标（JSON，可选）
              <textarea value={metrics} onChange={e=>setMetrics(e.target.value)} rows={3} placeholder='例如：{"views":12000,"plays":8000,"likes":900,"comments":120,"shares":80,"saves":40}' className="mt-1 w-full rounded-lg bg-black/20 p-3 font-mono text-xs" />
            </label>
            <button disabled={busy} onClick={()=>void addEvidence()} className="mt-3 rounded-xl bg-fuchsia-600 px-4 py-2 text-sm font-semibold disabled:opacity-50">保存待核验证据</button>
          </div>

          <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
            <h2 className="text-lg font-bold">研究证据清单</h2>
            <p className="mt-1 text-xs text-white/50">核验操作代表人工确认记录真实，不是平台官方认证。</p>
            <label className="mt-3 block text-xs">核验备注<input value={verifyNote} onChange={e=>setVerifyNote(e.target.value)} className="mt-1 w-full rounded-lg bg-black/20 p-2 text-sm" /></label>
            <label className="mt-3 block text-xs">核验时补录公开互动指标（JSON，可选）<textarea value={verifyMetrics} onChange={e=>setVerifyMetrics(e.target.value)} rows={2} placeholder='{"views":12000,"likes":900,"comments":120,"shares":80}' className="mt-1 w-full rounded-lg bg-black/20 p-2 font-mono text-xs" /></label>
            <div className="mt-3 max-h-[390px] space-y-2 overflow-y-auto">
              {evidence.map(item=><div key={item.id} className="rounded-xl border border-white/10 bg-black/20 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2"><strong className="text-sm">{item.work_title}</strong><span className={item.is_verified?"text-xs text-emerald-300":"text-xs text-amber-200"}>{item.is_verified?"已人工核验":"待核验"}</span></div>
                <p className="mt-1 text-xs text-white/60">{item.platform} · {item.genre||"未分类"} · {item.observed_at} · {item.reliability}</p>
                {item.work_url && <a href={item.work_url} target="_blank" rel="noreferrer" className="mt-1 block break-all text-xs text-cyan-200 underline">查看公开来源</a>}
                <pre className="mt-2 whitespace-pre-wrap break-words text-[11px] text-white/50">{JSON.stringify(item.metric_summary)}</pre>
                {!item.is_verified && <div className="mt-2 flex flex-wrap items-center gap-2"><select value={evidenceClassifications[item.id]||"漫剧作品趋势证据"} onChange={event=>setEvidenceClassifications(old=>({...old,[item.id]:event.target.value}))} className="rounded-lg bg-slate-900 p-2 text-xs"><option value="漫剧作品趋势证据">归入漫剧作品榜</option><option value="剧本作品证据">归入剧本榜</option><option value="公开提示词分享">归入提示词公开分享热度</option><option value="创作案例证据">归入创作案例</option></select><select value={evidenceGenres[item.id]||item.genre||"未分类"} onChange={event=>setEvidenceGenres(old=>({...old,[item.id]:event.target.value}))} className="rounded-lg bg-slate-900 p-2 text-xs">{["未分类","玄幻","仙侠","都市","言情","悬疑","科幻","历史","武侠","末日","校园","其他"].map(x=><option key={x}>{x}</option>)}</select><button disabled={busy} onClick={()=>void verifyEvidence(item.id)} className="rounded-lg border border-amber-200/30 px-3 py-1 text-xs text-amber-100">核验并分类</button></div>}
              </div>)}
              {!evidence.length && <p className="py-8 text-center text-sm text-white/40">还没有研究证据。</p>}
            </div>
          </div>
        </section>

        <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
          <h2 className="text-lg font-bold">建立提示词升级候选</h2>
          <p className="mt-1 text-xs text-white/50">系统强制要求至少两个不同平台、两个不同日期的已核验证据。创建候选不会改动正式提示词。</p>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <label className="text-sm">要升级的正式提示词
              <select value={selectedPrompt} onChange={e=>setSelectedPrompt(e.target.value)} className="mt-1 w-full rounded-lg bg-[#171326] p-2">
                {prompts.map(p=><option key={p.id} value={p.id}>{p.name} · v{p.current_version}</option>)}
              </select>
            </label>
            <label className="text-sm">候选名称<input value={candidateName} onChange={e=>setCandidateName(e.target.value)} className="mt-1 w-full rounded-lg bg-black/20 p-2" /></label>
          </div>
          {currentPrompt && <p className="mt-2 text-xs text-white/50">当前正式版本 v{currentPrompt.current_version}；候选基于此版本创建，若正式版本先发生变化，候选将自动失效。</p>}
          <label className="mt-3 block text-sm">候选提示词正文<textarea value={candidateContent} onChange={e=>setCandidateContent(e.target.value)} rows={7} className="mt-1 w-full rounded-lg bg-black/20 p-3 font-mono text-sm" /></label>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <label className="text-sm">修改理由<textarea value={reason} onChange={e=>setReason(e.target.value)} rows={3} className="mt-1 w-full rounded-lg bg-black/20 p-2" /></label>
            <label className="text-sm">预期改善点<textarea value={impact} onChange={e=>setImpact(e.target.value)} rows={3} className="mt-1 w-full rounded-lg bg-black/20 p-2" /></label>
          </div>
          <div className="mt-4">
            <h3 className="text-sm font-semibold">选择已核验证据</h3>
            <div className="mt-2 grid gap-2 md:grid-cols-2">
              {evidence.filter(e=>e.is_verified).map(e=><label key={e.id} className="flex items-start gap-2 rounded-lg border border-white/10 p-3 text-xs">
                <input type="checkbox" checked={selectedEvidence.includes(e.id)} onChange={event=>setSelectedEvidence(old=>event.target.checked?[...old,e.id]:old.filter(id=>id!==e.id))} />
                <span>{e.work_title} · {e.platform} · {e.observed_at}</span>
              </label>)}
            </div>
          </div>
          <button disabled={busy} onClick={()=>void createCandidate()} className="mt-4 rounded-xl bg-fuchsia-600 px-5 py-2 text-sm font-semibold disabled:opacity-50">生成待审批候选</button>
        </section>

        <section className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
          <h2 className="text-lg font-bold">升级候选审批</h2>
          <label className="mt-3 block max-w-xl text-xs">审批备注<input value={decisionNote} onChange={e=>setDecisionNote(e.target.value)} className="mt-1 w-full rounded-lg bg-black/20 p-2 text-sm" /></label>
          <div className="mt-3 space-y-3">
            {candidates.map(c=><article key={c.id} className="rounded-xl border border-white/10 bg-black/20 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2"><strong>{c.proposed_name}</strong><span className="text-xs text-white/60">{c.status==="pending"?"待你确认":c.status==="approved"?"已批准":"已拒绝"}</span></div>
              <p className="mt-1 text-xs text-white/50">提示词 #{c.prompt_id} · 基准 v{c.base_version} · 证据 {c.evidence_ids.length} 条</p>
              <p className="mt-2 text-sm">{c.change_reason}</p>
              {c.expected_impact && <p className="mt-1 text-xs text-cyan-100/70">预期改善：{c.expected_impact}</p>}
              <details className="mt-3"><summary className="cursor-pointer text-xs text-cyan-200">查看候选完整正文</summary><pre className="mt-2 whitespace-pre-wrap rounded-lg bg-black/30 p-3 text-xs">{c.proposed_content}</pre></details>
              {c.status==="pending" && <div className="mt-3 flex gap-2"><button disabled={busy} onClick={()=>void decideCandidate(c.id,"approve")} className="rounded-lg bg-emerald-700 px-4 py-2 text-xs font-semibold">确认更新正式提示词</button><button disabled={busy} onClick={()=>void decideCandidate(c.id,"reject")} className="rounded-lg border border-red-300/30 px-4 py-2 text-xs text-red-200">拒绝候选</button></div>}
              {c.decision_note && <p className="mt-2 text-xs text-white/40">处理备注：{c.decision_note}</p>}
            </article>)}
            {!candidates.length && <p className="py-6 text-center text-sm text-white/40">暂无升级候选。</p>}
          </div>
        </section>
      </div>
    </main>
  );
}
