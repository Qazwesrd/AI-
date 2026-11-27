"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";
type Revenue = { id:number; platform:string; gross_amount:number; currency:string; fee_amount:number; tax_amount:number; net_amount:number; status:string; period_start:string; period_end:string };
type Withdrawal = { id:number; platform:string; amount:number; currency:string; fee_amount:number; tax_amount:number; net_amount:number; status:string; requested_at:string; arrived_at:string; receipt_reference:string };
type Totals = Record<string,{gross:number;fees:number;taxes:number;net:number;settled_net:number;payable_net:number;paid_net:number;withdrawn:number;settled_balance_estimate:number}>;
type PlatformCapability = { platform:string; official_portal:string; revenue_api:string; payout_api:boolean; payout_label:string; note:string };
type OAuthConnection = {id:number;provider:string;account_label:string;status:string;last_sync_at:string|null;last_sync_status:string;last_sync_error:string};
type OAuthStatus = {google_oauth_configured:boolean;token_encryption_configured:boolean;read_only:boolean;payout_execution_enabled:boolean;required_environment:string[];note:string};

export default function RevenueCenterPage() {
  const [totals,setTotals]=useState<Totals>({});
  const [revenues,setRevenues]=useState<Revenue[]>([]);
  const [withdrawals,setWithdrawals]=useState<Withdrawal[]>([]);
  const [platforms,setPlatforms]=useState<PlatformCapability[]>([]);
  const [connections,setConnections]=useState<OAuthConnection[]>([]);
  const [oauthStatus,setOauthStatus]=useState<OAuthStatus|null>(null);
  const [googleClientId,setGoogleClientId]=useState("");
  const [message,setMessage]=useState("");
  const [busy,setBusy]=useState(false);
  const [rev,setRev]=useState({platform:"",gross_amount:"",currency:"CNY",fee_amount:"0",tax_amount:"0",status:"estimated",period_start:"",period_end:"",notes:""});
  const [wd,setWd]=useState({platform:"",amount:"",currency:"CNY",fee_amount:"0",tax_amount:"0",status:"planned",requested_at:"",notes:""});

  async function api<T>(path:string,init?:RequestInit):Promise<T>{
    const response=await fetch(API+path,init);
    const data=await response.json().catch(()=>({}));
    if(!response.ok) throw new Error(data.detail||"请求失败："+response.status);
    return data as T;
  }
  async function load(){
    try{
      const [s,r,w,p,o,os]=await Promise.all([
        api<{currency_totals:Totals}>("/api/revenue/summary"),
        api<{items:Revenue[]}>("/api/revenue/entries"),
        api<{items:Withdrawal[]}>("/api/revenue/withdrawals"),
        api<{items:PlatformCapability[]}>("/api/revenue/platforms"),
        api<{items:OAuthConnection[]}>("/api/revenue/oauth/connections"),
        api<OAuthStatus>("/api/revenue/oauth/status")
      ]);
      setTotals(s.currency_totals||{});setRevenues(r.items||[]);setWithdrawals(w.items||[]);setPlatforms(p.items||[]);setConnections(o.items||[]);setOauthStatus(os);
    }catch(e){setMessage(e instanceof Error?e.message:"读取收益数据失败");}
  }
  useEffect(()=>{void load();},[]);


  async function saveGoogleOAuthConfig(){
    setBusy(true);
    try{
      await api("/api/revenue/oauth/config",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({client_id:googleClientId})});
      setMessage("Google OAuth Client ID 已保存在本机。现在可以连接 Google 账户。");
      await load();
    }catch(e){setMessage(e instanceof Error?e.message:"保存 Google OAuth 配置失败");}
    finally{setBusy(false);}
  }
  async function syncConnection(id:number){
    setBusy(true);
    try{
      const result=await api<{message:string;inserted:number;updated:number;analytics_days:number;payment_records:number}>(`/api/revenue/oauth/connections/${id}/sync`,{method:"POST"});
      setMessage(`${result.message} 新增 ${result.inserted} 条、更新 ${result.updated} 条；YouTube分析 ${result.analytics_days} 天，AdSense付款记录 ${result.payment_records} 条。`);
      await load();
    }catch(e){setMessage(e instanceof Error?e.message:"真实收入同步失败");}
    finally{setBusy(false);}
  }
  async function disconnectConnection(id:number){
    if(!window.confirm("确定断开此 Google 授权？软件会删除本地加密令牌，并尝试向 Google 撤销令牌。")) return;
    setBusy(true);
    try{await api(`/api/revenue/oauth/connections/${id}`,{method:"DELETE"});setMessage("Google 授权已断开。");await load();}
    catch(e){setMessage(e instanceof Error?e.message:"断开授权失败");}
    finally{setBusy(false);}
  }
  async function saveRevenue(e:React.FormEvent){
    e.preventDefault();setBusy(true);
    try{
      await api("/api/revenue/entries",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...rev,gross_amount:Number(rev.gross_amount),fee_amount:Number(rev.fee_amount),tax_amount:Number(rev.tax_amount)})});
      setRev({...rev,platform:"",gross_amount:"",fee_amount:"0",tax_amount:"0",notes:""});setMessage("收入记录已保存。");await load();
    }catch(err){setMessage(err instanceof Error?err.message:"保存失败");}finally{setBusy(false);}
  }
  async function saveWithdrawal(e:React.FormEvent){
    e.preventDefault();setBusy(true);
    try{
      await api("/api/revenue/withdrawals",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...wd,amount:Number(wd.amount),fee_amount:Number(wd.fee_amount),tax_amount:Number(wd.tax_amount)})});
      setWd({...wd,platform:"",amount:"",fee_amount:"0",tax_amount:"0",notes:""});setMessage("提现计划已登记。请自行到平台官方账户提交。");await load();
    }catch(err){setMessage(err instanceof Error?err.message:"保存失败");}finally{setBusy(false);}
  }
  async function updateStatus(id:number,status:string){
    const arrived_at=status==="paid"?new Date().toISOString().slice(0,10):"";
    const receipt_reference=status==="paid"?window.prompt("填写到账凭证编号或说明（可留空）")||"": "";
    try{await api("/api/revenue/withdrawals/"+id,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({status,arrived_at,receipt_reference})});setMessage("提现记录已更新。");await load();}
    catch(e){setMessage(e instanceof Error?e.message:"更新失败");}
  }
  const field="w-full rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-white";
  return <main className="min-h-screen bg-slate-950 p-5 text-slate-100 md:p-8">
    <div className="mx-auto max-w-6xl space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-sm text-cyan-300">CREATOR FINANCE</p><h1 className="text-3xl font-bold">收益与提现管理</h1><p className="mt-2 text-sm text-slate-400">手动登记各平台收入、结算、费用与到账情况。</p></div><a href="/" className="rounded-lg border border-slate-700 px-4 py-2 text-sm">返回首页</a></header>
      {message&&<div role="status" className="rounded-lg border border-cyan-800 bg-cyan-950/40 p-3 text-sm">{message}</div>}
      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{Object.entries(totals).map(([currency,t])=><article key={currency} className="rounded-xl border border-slate-800 bg-slate-900 p-4"><div className="text-sm text-slate-400">{currency} 汇总</div><div className="mt-2 text-xl font-bold">{t.net.toFixed(2)}</div><div className="mt-1 text-xs text-slate-400">登记净收入</div><div className="mt-3 text-sm">待付款：{(t.payable_net||0).toFixed(2)} {currency}</div><div className="mt-1 text-sm">已结算：{t.settled_net.toFixed(2)} · 已付款：{(t.paid_net||0).toFixed(2)}</div><div className="mt-1 text-xs text-slate-500">估算可结算余额：{t.settled_balance_estimate.toFixed(2)} · 费用 {t.fees.toFixed(2)} · 税费 {t.taxes.toFixed(2)}</div></article>)}</section>
      <section className="rounded-xl border border-cyan-900 bg-slate-900 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-xl font-semibold">真实平台账户 OAuth 授权</h2><p className="mt-1 text-sm text-slate-400">首批接入 Google：YouTube Analytics 预估收入 + AdSense 真实付款记录，只读同步。</p></div><button disabled={busy||!oauthStatus?.google_oauth_configured} onClick={()=>{window.location.href=API+"/api/revenue/oauth/google/start";}} className="rounded-lg bg-cyan-700 px-4 py-2 text-sm font-semibold disabled:opacity-40">连接 Google / YouTube</button></div>
        {oauthStatus&&!oauthStatus.google_oauth_configured&&<div className="mt-3 rounded-lg border border-amber-800 bg-amber-950/30 p-3"><p className="text-sm text-amber-200">首次连接需要你在 Google Cloud 创建自己的 Desktop app OAuth Client ID。只需填写 Client ID，不要填写密码或银行卡信息。详细步骤见 docs/GOOGLE_CREATOR_REVENUE_OAUTH.md。</p><div className="mt-3 flex flex-wrap gap-2"><input value={googleClientId} onChange={e=>setGoogleClientId(e.target.value)} placeholder="xxxx.apps.googleusercontent.com" className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"/><button disabled={busy||!googleClientId.trim()} onClick={()=>void saveGoogleOAuthConfig()} className="rounded-lg border border-cyan-300/30 px-4 py-2 text-sm text-cyan-100 disabled:opacity-40">保存本机配置</button></div></div>}
        {oauthStatus?.google_oauth_configured&&<p className="mt-3 text-xs text-emerald-300">Google OAuth Client ID 已配置；本机加密密钥由软件生成并保护。</p>}
        <div className="mt-4 space-y-2">{connections.map(item=><div key={item.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-700 p-3"><div><div className="font-medium">{item.account_label}</div><div className="text-xs text-slate-400">Google 授权 · {item.status} · 最近同步 {item.last_sync_at||"尚未同步"} · {item.last_sync_status}</div>{item.last_sync_error&&<p className="mt-1 text-xs text-amber-300">{item.last_sync_error}</p>}</div><div className="flex gap-2"><button disabled={busy||item.status!=="connected"} onClick={()=>void syncConnection(item.id)} className="rounded-lg bg-emerald-700 px-3 py-2 text-xs disabled:opacity-40">同步真实收入</button><button disabled={busy} onClick={()=>void disconnectConnection(item.id)} className="rounded-lg border border-rose-800 px-3 py-2 text-xs">断开授权</button></div></div>)}
          {!connections.length&&<p className="text-sm text-slate-500">尚未连接 Google 账户。其他平台会按官方 API 能力逐个接入，不会使用模拟登录。</p>}</div>
        <p className="mt-3 text-xs text-slate-500">OAuth 配置、授权范围、数据分类和本机密钥生成方法：docs/GOOGLE_CREATOR_REVENUE_OAUTH.md。Google Analytics 是预估收入；AdSense Payments 是平台付款记录，二者分开存储。</p>
      </section>
      <section className="rounded-xl border border-slate-800 bg-slate-900 p-5">
        <h2 className="text-xl font-semibold">其他平台官方收益 / 提现入口</h2>
        <p className="mt-2 text-sm text-slate-400">这里仅跳转平台官方页面。当前版本尚未完成平台 OAuth 收入同步或提现执行 API，因此不会伪造“软件内提现”按钮，也不会保存银行卡号、密码或验证码。</p>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          {platforms.map(item=><article key={item.platform} className="rounded-lg border border-slate-700 p-3">
            <div className="flex items-center justify-between gap-2"><strong>{item.platform}</strong><span className="text-xs text-amber-300">官方操作</span></div>
            <p className="mt-2 text-xs text-slate-400">{item.revenue_api}</p>
            <p className="mt-1 text-xs text-slate-500">{item.note}</p>
            {item.official_portal ? <a href={item.official_portal} target="_blank" rel="noopener noreferrer" className="mt-3 inline-block rounded-lg border border-cyan-700 px-3 py-2 text-sm text-cyan-200">{item.payout_label} ↗</a> : <p className="mt-3 text-sm text-amber-200">{item.payout_label}</p>}
          </article>)}
        </div>
      </section>
      <div className="grid gap-5 lg:grid-cols-2">
        <form onSubmit={saveRevenue} className="space-y-3 rounded-xl border border-slate-800 bg-slate-900 p-5"><h2 className="text-xl font-semibold">登记平台收入</h2>
          <input required className={field} placeholder="平台名称，如 YouTube / 红果" value={rev.platform} onChange={e=>setRev({...rev,platform:e.target.value})}/>
          <div className="grid grid-cols-2 gap-2"><input required type="number" min="0" step="0.01" className={field} placeholder="收入金额" value={rev.gross_amount} onChange={e=>setRev({...rev,gross_amount:e.target.value})}/><select className={field} value={rev.currency} onChange={e=>setRev({...rev,currency:e.target.value})}>{["CNY","USD","EUR","JPY","GBP","HKD","KRW","TWD","CAD","AUD","INR","SGD","CHF","NZD"].map(x=><option key={x}>{x}</option>)}</select></div>
          <div className="grid grid-cols-2 gap-2"><input type="number" min="0" step="0.01" className={field} placeholder="手续费" value={rev.fee_amount} onChange={e=>setRev({...rev,fee_amount:e.target.value})}/><input type="number" min="0" step="0.01" className={field} placeholder="税费" value={rev.tax_amount} onChange={e=>setRev({...rev,tax_amount:e.target.value})}/></div>
          <div className="grid grid-cols-2 gap-2"><select className={field} value={rev.status} onChange={e=>setRev({...rev,status:e.target.value})}><option value="estimated">预估</option><option value="settled">已结算</option><option value="payable">可付款/待付款</option><option value="paid">已支付</option></select><input className={field} placeholder="账期开始 YYYY-MM-DD" value={rev.period_start} onChange={e=>setRev({...rev,period_start:e.target.value})}/></div>
          <input className={field} placeholder="账期结束 YYYY-MM-DD" value={rev.period_end} onChange={e=>setRev({...rev,period_end:e.target.value})}/>
          <textarea className={field} placeholder="备注、平台账单编号" value={rev.notes} onChange={e=>setRev({...rev,notes:e.target.value})}/>
          <button disabled={busy} className="rounded-lg bg-cyan-700 px-4 py-2 font-medium disabled:opacity-50">保存收入记录</button>
        </form>
        <form onSubmit={saveWithdrawal} className="space-y-3 rounded-xl border border-slate-800 bg-slate-900 p-5"><h2 className="text-xl font-semibold">登记提现计划</h2>
          <input required className={field} placeholder="平台名称" value={wd.platform} onChange={e=>setWd({...wd,platform:e.target.value})}/>
          <div className="grid grid-cols-2 gap-2"><input required type="number" min="0.01" step="0.01" className={field} placeholder="计划提现金额" value={wd.amount} onChange={e=>setWd({...wd,amount:e.target.value})}/><select className={field} value={wd.currency} onChange={e=>setWd({...wd,currency:e.target.value})}>{["CNY","USD","EUR","JPY","GBP","HKD","KRW","TWD","CAD","AUD","INR","SGD","CHF","NZD"].map(x=><option key={x}>{x}</option>)}</select></div>
          <div className="grid grid-cols-2 gap-2"><input type="number" min="0" step="0.01" className={field} placeholder="手续费" value={wd.fee_amount} onChange={e=>setWd({...wd,fee_amount:e.target.value})}/><input type="number" min="0" step="0.01" className={field} placeholder="税费" value={wd.tax_amount} onChange={e=>setWd({...wd,tax_amount:e.target.value})}/></div>
          <input className={field} placeholder="计划提交日期" value={wd.requested_at} onChange={e=>setWd({...wd,requested_at:e.target.value})}/>
          <textarea className={field} placeholder="备注" value={wd.notes} onChange={e=>setWd({...wd,notes:e.target.value})}/>
          <button disabled={busy} className="rounded-lg bg-fuchsia-700 px-4 py-2 font-medium disabled:opacity-50">保存提现计划</button>
          <p className="text-xs leading-5 text-amber-300">此处只登记计划；是否可提现、最低金额与手续费请以对应平台官方账户为准。</p>
        </form>
      </div>
      <section className="rounded-xl border border-slate-800 bg-slate-900 p-5"><h2 className="mb-3 text-xl font-semibold">收入明细</h2><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="text-slate-400"><tr><th className="p-2">平台</th><th className="p-2">金额</th><th className="p-2">净额</th><th className="p-2">状态</th><th className="p-2">账期</th></tr></thead><tbody>{revenues.map(x=><tr key={x.id} className="border-t border-slate-800"><td className="p-2">{x.platform}</td><td className="p-2">{x.gross_amount.toFixed(2)} {x.currency}</td><td className="p-2">{x.net_amount.toFixed(2)}</td><td className="p-2">{({estimated:"预估",settled:"已结算",payable:"待付款",paid:"已支付"} as Record<string,string>)[x.status]||x.status}</td><td className="p-2">{x.period_start} - {x.period_end}</td></tr>)}</tbody></table>{revenues.length===0&&<p className="p-3 text-sm text-slate-500">暂无收入记录。</p>}</div></section>
      <section className="rounded-xl border border-slate-800 bg-slate-900 p-5"><h2 className="mb-3 text-xl font-semibold">提现记录与到账跟踪</h2><div className="space-y-3">{withdrawals.map(x=><div key={x.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-800 p-3"><div><div className="font-medium">{x.platform} · {x.amount.toFixed(2)} {x.currency}</div><div className="text-xs text-slate-400">净到账预估 {x.net_amount.toFixed(2)} · 状态 {x.status} {x.arrived_at&&"· 到账 "+x.arrived_at}</div>{x.receipt_reference&&<div className="text-xs text-slate-500">凭证：{x.receipt_reference}</div>}</div><div className="flex flex-wrap gap-2"><button onClick={()=>void updateStatus(x.id,"submitted")} className="rounded border border-slate-600 px-2 py-1 text-xs">已提交</button><button onClick={()=>void updateStatus(x.id,"processing")} className="rounded border border-slate-600 px-2 py-1 text-xs">处理中</button><button onClick={()=>void updateStatus(x.id,"paid")} className="rounded border border-emerald-700 px-2 py-1 text-xs">已到账</button><button onClick={()=>void updateStatus(x.id,"rejected")} className="rounded border border-rose-800 px-2 py-1 text-xs">被拒绝</button></div></div>)}{withdrawals.length===0&&<p className="text-sm text-slate-500">暂无提现记录。</p>}</div></section>
      <p className="text-xs leading-5 text-slate-500">重要说明：汇总仅基于你手动录入的数据，按币种分别计算，不做未经核实的汇率换算，也不代表平台实时余额、税务意见或官方可提现金额。</p>
    </div>
  </main>;
}
