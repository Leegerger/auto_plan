"""Build a self-contained replay of recorded prototype runs, without a server."""
from pathlib import Path
import gzip
import json

root=Path(__file__).parent
bundle=json.loads(gzip.decompress((root/'decomposition-results.json.gz').read_bytes()))
runs=[]
for version,files in bundle.items():
    for name,value in files.items():
        if isinstance(value,dict) and 'history' in value and 'reference_vector' in value:
            runs.append({'id':version+'/'+name,'version':version,**value})
data=json.dumps(runs,ensure_ascii=False,separators=(',',':')).replace('</','<'+chr(92)+'/')
page='''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>构造与分解修复：实验回放</title>
<style>
body{font:16px/1.6 system-ui,sans-serif;margin:0;color:#20342d;background:#f4f7f4}main{max-width:1040px;margin:auto;padding:32px 20px}section{background:white;border:1px solid #d4dfd8;border-radius:10px;padding:22px;margin:20px 0}h1{font-size:28px}h2{font-size:21px}.muted{color:#52675b}button,select{font:inherit;border:1px solid #91aaa0;border-radius:6px;background:white;padding:9px 12px;margin:4px}select{max-width:100%}button{cursor:pointer}button:hover{background:#e5f1e8}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{text-align:left;padding:9px;border-bottom:1px solid #e2e9e4}.good{color:#196940}.bad{color:#a43d26}.status{font-size:20px;font-weight:600}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}.cards div{background:#f4f7f4;padding:12px;border-radius:6px}.cards strong{font-size:24px;display:block}code{overflow-wrap:anywhere;font-size:12px}details{margin:14px 0}@media(max-width:600px){.cards{grid-template-columns:1fr}th,td{padding:6px;font-size:14px}}
</style>
<main><h1>先减少切机，再调整数量</h1>
<p>可抛弃原型。本页回放实际实验，观察每次提案为什么被保留，以及为什么“数量变动更少”仍可能不合格。按钮只切换记录，不运行求解器。</p>
<p class="muted">范围限定为已有日期、线体与正投入段。确认充足，无执行、在制品、锁定或禁令。此页不表示完整业务规则或生产性能已验收。</p>
<section><label>实验记录 <select id="run"></select></label><p id="overview"></p><div class="cards" id="cards"></div></section>
<section><h2>候选变化</h2><p id="status" class="status"></p><p id="step"></p><table><thead><tr><th>按先后优先级比较</th><th>当前提案</th><th>参考方案</th></tr></thead><tbody id="scores"></tbody></table>
<p><button id="reset">回到构造起点</button><button id="previous">上一步</button><button id="next">下一步提案</button><button id="last">查看最终结果</button></p>
<label>跳到阶段 <select id="stage"></select></label><p id="decision" class="muted"></p>
<details><summary>展开前 30 层逐周缺口</summary><table><thead><tr><th>周</th><th>提案按时缺口</th><th>参考按时缺口</th><th>提案最终缺口</th><th>参考最终缺口</th></tr></thead><tbody id="weeks"></tbody></table></details></section>
<section><h2>引导演练</h2><button id="failure">先看数量优先的失败</button><button id="fixed">再看切机优先的修复</button><button id="uneven">查看不均匀需求</button><p id="guide">先看失败案例的最终结果：切机次数比参考多，数量变动代价更低仍不能抵消它。再切到修复版，逐步查看初始顺序修复与后续数量修复。</p></section>
<p class="muted">候选选择始终比较完整 33 层向量。分块求解的最优状态只适用于该块；本轮没有全局最优证明。首候选时间与总耗时是本地实验，不含浏览器、排队和 Oracle。</p><p>输入摘要 <code id="hash"></code></p></main>
<script>
const runs=__DATA__;
const variants={uniform5:'统一增加 5%',uniform7:'统一增加 7%',uneven:'机种与周别增加 2% 至 9%',scrambled5:'增加 5% 且打乱顺序'};
const versions={initial:'初版',quantity_first:'先数量后顺序',switches_first_full:'先切机后数量，整窗重复',switches_first_local:'先切机后数量，局部重复'};
const title=r=>`${versions[r.version]||r.version} · ${r.local?'局部':'整窗'} · ${variants[r.variant]||r.variant} · ${r.id.split('/').pop()}`;
const stageName=s=>s==='constructed_and_trimmed'?'构造起点并删除可移除富余':s==='order_initial'?'先修复切机与日内顺序':s.startsWith('quantity_')?`第 ${Number(s.split('_')[1])+1} 轮，Recipe ${s.split('_')[2]} 数量修复`:s.startsWith('order_')?`第 ${Number(s.split('_')[1])+1} 轮，日内顺序修复`:s;
const compare=(a,b)=>{for(let i=0;i<a.length;i++)if(a[i]!==b[i])return a[i]<b[i]?-1:1;return 0};
let selected=0,index=0;
const el=id=>document.getElementById(id);
function render(){const r=runs[selected],h=r.history[index],v=h.vector,b=r.reference_vector,ok=compare(v,b)<=0;
 el('overview').textContent=`${r.local?'局部':'整窗'}范围。总耗时 ${r.seconds.toFixed(3)} 秒；共记录 ${r.history.length} 次提案。当前回放第 ${index+1} 次。`;
 el('cards').innerHTML=`<div>首个候选已保存<strong>${r.first_candidate_saved_seconds.toFixed(3)} 秒</strong></div><div>最终参考质量<strong class="${r.reference_quality_met?'good':'bad'}">${r.reference_quality_met?'达到':'未达到'}</strong></div><div>本地计算时间<strong>${r.seconds<=r.budget_seconds?'在预算内':'超过预算'}</strong></div>`;
 el('status').textContent=ok?'当前提案不劣于参考':'当前提案仍劣于参考';el('status').className='status '+(ok?'good':'bad');
 el('step').textContent=stageName(h.stage)+`，实验进行到 ${h.seconds.toFixed(3)} 秒。`;
 el('scores').innerHTML=['切机次数','计划变动代价','日内顺序变动数'].map((name,i)=>`<tr><td>${31+i}. ${name}</td><td>${v[30+i].toLocaleString()}</td><td>${b[30+i].toLocaleString()}</td></tr>`).join('');
 el('weeks').innerHTML=Array.from({length:15},(_,i)=>`<tr><td>${i+1}</td><td>${v[i]}</td><td>${b[i]}</td><td>${v[15+i]}</td><td>${b[15+i]}</td></tr>`).join('');
 el('decision').textContent=h.accepted?'本次提案已保留：完整目标向量优于此前候选，或它是首个候选。':'本次提案未替换此前候选：完整目标向量没有严格改善。';
 el('hash').textContent=r.input_sha256;el('stage').value=index;el('run').value=selected;}
function select(n){selected=n;index=0;el('stage').innerHTML=runs[n].history.map((h,i)=>`<option value="${i}">${i+1}. ${stageName(h.stage)}</option>`).join('');render();}
el('run').innerHTML=runs.map((r,i)=>`<option value="${i}">${title(r)}</option>`).join('');el('run').onchange=e=>select(Number(e.target.value));
el('stage').onchange=e=>{index=Number(e.target.value);render()};el('reset').onclick=()=>{index=0;render()};el('previous').onclick=()=>{index=Math.max(0,index-1);render()};el('next').onclick=()=>{index=Math.min(runs[selected].history.length-1,index+1);render()};el('last').onclick=()=>{index=runs[selected].history.length-1;render()};
function choose(predicate,last=false){let n=runs.findIndex(predicate);if(n>=0){select(n);if(last){index=runs[n].history.length-1;render()}}}
el('failure').onclick=()=>choose(r=>r.version==='quantity_first'&&r.variant==='scrambled5',true);el('fixed').onclick=()=>choose(r=>r.version==='switches_first_full');el('uneven').onclick=()=>choose(r=>r.variant==='uneven');
choose(r=>r.version==='switches_first_full');if(!el('stage').options.length)select(0);
</script></html>'''
(root/'decomposition-review.html').write_text(page.replace('__DATA__',data))
print('wrote decomposition-review.html with',len(runs),'runs')
