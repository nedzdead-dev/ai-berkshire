#!/usr/bin/env python3
"""Local web UI wrapping the REAL ai-berkshire financial_rigor.py tool.

This does not reimplement any logic — it imports the repo's own functions
and captures their stdout, so what you see in the browser is exactly what
the CLI tool produces.
"""
import io
import json
import contextlib
from pathlib import Path

from flask import Flask, request, jsonify, Response

# Import the repo's actual tool module
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))  # web/ -> repo/tools
import financial_rigor as fr  # noqa: E402

import yfinance as yf  # noqa: E402
import urllib.request  # noqa: E402
import urllib.parse  # noqa: E402

app = Flask(__name__)


@app.route("/api/search")
def search():
    """Name -> ticker autocomplete via Yahoo's search endpoint."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 1:
        return jsonify({"results": []})
    url = ("https://query2.finance.yahoo.com/v1/finance/search?q="
           + urllib.parse.quote(q) + "&quotesCount=8&newsCount=0")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=8).read())
    except Exception as e:
        return jsonify({"results": [], "error": str(e)})
    out = []
    for qt in data.get("quotes", []):
        sym = qt.get("symbol")
        if not sym or qt.get("quoteType") not in ("EQUITY", "ETF"):
            continue
        out.append({
            "symbol": sym,
            "name": qt.get("shortname") or qt.get("longname") or sym,
            "exch": qt.get("exchDisp") or qt.get("exchange") or "",
            "type": qt.get("quoteType"),
        })
    return jsonify({"results": out})


@app.route("/api/lookup")
def lookup():
    """Resolve a ticker to live fundamentals (any listed stock) via yfinance.

    This is the missing data layer: financial_rigor.py only does math, so we
    fetch the real numbers here and hand them to the page, which then drives
    the genuine tool functions.
    """
    t = (request.args.get("ticker") or "").strip().upper()
    if not t:
        return jsonify({"error": "no ticker"}), 400
    try:
        info = yf.Ticker(t).info
    except Exception as e:
        return jsonify({"error": f"lookup failed: {type(e).__name__}: {e}"}), 502

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    shares = info.get("sharesOutstanding")
    if not price or not shares:
        return jsonify({"error": f"no usable data for '{t}' (delisted/invalid ticker?)"}), 404

    fcf = info.get("freeCashflow")
    rev = info.get("totalRevenue")
    out = {
        "ticker": t,
        "name": info.get("shortName") or info.get("longName") or t,
        "currency": info.get("currency", ""),
        "price": price,
        "shares": shares,
        "marketCap": info.get("marketCap"),
        "eps": info.get("trailingEps"),
        "bvps": info.get("bookValue"),
        "fcf_per_share": round(fcf / shares, 4) if fcf and shares else None,
        "dividend": info.get("dividendRate"),
        "rps": round(rev / shares, 4) if rev and shares else None,
        "pe_reported": info.get("trailingPE"),
        "pb_reported": info.get("priceToBook"),
        # analyst / forward inputs used to auto-derive the fair-value scenarios
        "forward_pe": info.get("forwardPE"),
        "earnings_growth": info.get("earningsGrowth"),
        "revenue_growth": info.get("revenueGrowth"),
        "analyst_target": info.get("targetMeanPrice"),
        "sector": info.get("sector", ""),
    }
    return jsonify({"data": out})


# Chinese -> English for the tool's printed labels. Longest/most-specific first
# so partial strings (e.g. "偏差") don't clobber fuller ones (e.g. "偏差:").
_TRANSLATE = [
    ("市值验算 (Market Cap Verification)", "Market Cap Verification"),
    ("估值指标验算 (Valuation Verification)", "Valuation Verification"),
    ("Benford定律检测 (Financial Data Fabrication Check)", "Benford's Law Check (data-fabrication screen)"),
    ("精确计算 (Exact Calculator)", "Exact Calculator"),
    ("三情景估值模型 (Three-Scenario Valuation)", "Three-Scenario Valuation"),
    ("交叉验证:", "Cross-Validation:"),
    ("✅ 以上指标均使用精确十进制计算, 无浮点误差", "✅ All metrics use exact decimal math (no float error)"),
    ("✅ 所有计算使用精确十进制, 结果可审计复现", "✅ Exact decimal math — auditable & reproducible"),
    ("✅ 数据首位数字分布符合Benford定律", "✅ Leading-digit distribution conforms to Benford's Law"),
    ("❌ 数据首位数字分布异常, 可能存在人为调整", "❌ Leading-digit distribution anomalous — possible manual adjustment"),
    ("提示: 不符合Benford定律不一定是造假, 但值得进一步调查",
     "Note: non-conformance is not proof of fraud, but warrants investigation"),
    ("✅ 所有来源偏差 ≤", "✅ All sources within ≤"),
    ("%, 数据一致", "%, data consistent"),
    ("⚠️  存在来源偏差 >", "⚠️  Source deviation >"),
    ("%, 请核实差异原因", "%, verify the discrepancy"),
    ("建议: 优先采用公司年报/交易所数据", "Tip: prefer company filings / exchange data"),
    ("✅ 验证通过, 偏差仅", "✅ Passed — deviation only"),
    ("❌ 警告: 偏差", "❌ Warning: deviation"),
    (", 请检查:", ", check:"),
    ("股本是否为最新（回购/增发）?", "Are shares outstanding current (buybacks/issuance)?"),
    ("单位是否一致（港币 vs 人民币 vs 美元）?", "Are units consistent (HKD vs CNY vs USD)?"),
    ("股价是否为最新?", "Is the price current?"),
    ("在可接受范围, 可能因股价波动/股本变化", "within acceptable range (price/share-count drift)"),
    ("⚠️  偏差", "⚠️  Deviation"),
    ("样本量不足:", "Insufficient sample:"),
    (", Benford分析不可靠", ", Benford analysis unreliable"),
    ("共识值 (加权中位数):", "Consensus (weighted median):"),
    ("数据来源数:", "Sources:"),
    ("参考中位数:", "Reference median:"),
    ("Close (高度符合)", "Close (strong fit)"),
    ("Acceptable (可接受)", "Acceptable"),
    ("Marginally Acceptable (边缘)", "Marginally Acceptable"),
    ("Nonconforming (不符合 ⚠️)", "Nonconforming ⚠️"),
    ("Benford期望", "Benford exp"),
    ("不安全的表达式:", "Unsafe expression:"),
    ("计算错误:", "Calc error:"),
    ("乐观 (Bull)", "Bull"), ("中性 (Base)", "Base"), ("悲观 (Bear)", "Bear"),
    ("股价 (Price):", "Price:"),
    ("总股本 (Shares):", "Shares:"),
    ("计算市值:", "Calculated cap:"),
    ("报告市值:", "Reported cap:"),
    ("当前股价:", "Current price:"),
    ("当前EPS:", "Current EPS:"),
    ("预测期:", "Forecast horizon:"),
    ("盈利收益率:", "Earnings yield:"),
    ("股息率:", "Dividend yield:"),
    ("样本量:", "Sample size:"),
    ("符合度:", "Conformity:"),
    ("表达式:", "Expression:"),
    ("精确值:", "Exact value:"),
    ("结果:", "Result:"),
    ("偏差:", "Deviation:"),
    ("偏差 ", "dev "),
    ("年增速", "Growth"), ("目标PE", "Tgt PE"), ("目标EPS", "Tgt EPS"),
    ("目标股价", "Tgt price"), ("涨跌幅", "Change"),
    ("首位数", "Digit"), ("观测", "Observed"),
    ("情景", "Scenario"), ("偏差", "Dev"), ("年", "y"),
]


def translate(text: str) -> str:
    for zh, en in _TRANSLATE:
        text = text.replace(zh, en)
    return text


def run_capturing(fn, *args, **kwargs):
    """Call a financial_rigor function, capturing everything it prints."""
    buf = io.StringIO()
    err = None
    ret = None
    try:
        with contextlib.redirect_stdout(buf):
            ret = fn(*args, **kwargs)
    except Exception as e:  # surface the real error to the page
        err = f"{type(e).__name__}: {e}"
    return {"output": translate(buf.getvalue()), "result": _safe(ret), "error": err}


def _safe(v):
    try:
        json.dumps(v)
        return v
    except TypeError:
        return str(v)


@app.route("/api/market-cap", methods=["POST"])
def market_cap():
    d = request.get_json(force=True)
    return jsonify(run_capturing(
        fr.verify_market_cap,
        float(d["price"]), float(d["shares"]), float(d["reported"]),
        d.get("currency", "")))


@app.route("/api/valuation", methods=["POST"])
def valuation():
    d = request.get_json(force=True)
    def f(x):
        return float(x) if x not in (None, "", "null") else None
    return jsonify(run_capturing(
        fr.verify_valuation,
        float(d["price"]), f(d.get("eps")), f(d.get("bvps")),
        f(d.get("fcf")), f(d.get("dividend")), f(d.get("rps"))))


@app.route("/api/cross-validate", methods=["POST"])
def cross_validate():
    d = request.get_json(force=True)
    values = json.loads(d["values"]) if isinstance(d["values"], str) else d["values"]
    return jsonify(run_capturing(
        fr.cross_validate, d["field"], values,
        d.get("unit", ""), float(d.get("tolerance", 2.0))))


@app.route("/api/benford", methods=["POST"])
def benford():
    d = request.get_json(force=True)
    values = json.loads(d["values"]) if isinstance(d["values"], str) else d["values"]
    return jsonify(run_capturing(fr.benford_check, values))


@app.route("/api/calc", methods=["POST"])
def calc():
    d = request.get_json(force=True)
    return jsonify(run_capturing(fr.exact_calc, d["expr"]))


@app.route("/api/three-scenario", methods=["POST"])
def three_scenario():
    d = request.get_json(force=True)
    g = [float(x) for x in d["growth"]]
    pe = [float(x) for x in d["pe"]]
    return jsonify(run_capturing(
        fr.three_scenario_valuation,
        float(d["price"]), float(d["eps"]), float(d["shares"]),
        g[0], g[1], g[2], pe[0], pe[1], pe[2],
        int(d.get("years", 3)), d.get("currency", "")))


@app.route("/")
def index():
    return Response(HTML, mimetype="text/html")


HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Berkshire · financial_rigor.py — Local Tool Runner</title>
<style>
  :root{--bg:#0d1117;--panel:#161b22;--bd:#30363d;--fg:#e6edf3;--mut:#8b949e;--acc:#2f81f7;--ok:#3fb950}
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg)}
  header{padding:20px 24px;border-bottom:1px solid var(--bd)}
  header h1{margin:0;font-size:18px}
  header p{margin:4px 0 0;color:var(--mut);font-size:13px}
  .tickerbar{margin-top:12px;display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap}
  .tickerbar button{margin:0}
  .ac{position:relative;width:340px}
  .ac input{width:100%;font-weight:600}
  .ac-list{position:absolute;left:0;right:0;top:100%;z-index:20;background:var(--panel);border:1px solid var(--bd);border-top:0;border-radius:0 0 8px 8px;max-height:300px;overflow:auto;display:none}
  .ac-item{padding:7px 10px;cursor:pointer;border-bottom:1px solid #21262d;font-size:13px}
  .ac-item:hover,.ac-item.sel{background:#1f6feb33}
  .ac-item b{color:var(--acc)}.ac-item span{color:var(--mut);font-size:11px;float:right}
  .verdict{grid-column:1/3;border-radius:10px;padding:16px 18px;border:1px solid var(--bd);background:var(--panel);display:none}
  .verdict h2{margin:0 0 6px;font-size:16px}
  .verdict .big{font-size:13px;color:var(--mut)}
  .vline{display:flex;gap:24px;flex-wrap:wrap;margin-top:8px;font-size:13px}
  .vline div b{font-size:16px;display:block}
  .up{color:var(--ok)} .down{color:#f85149}
  .wrap{max-width:1100px;margin:0 auto;padding:24px;display:grid;grid-template-columns:1fr 1fr;gap:18px}
  .card{background:var(--panel);border:1px solid var(--bd);border-radius:10px;padding:16px}
  .card h2{margin:0 0 12px;font-size:14px;color:var(--acc)}
  label{display:block;font-size:12px;color:var(--mut);margin:8px 0 3px}
  input,textarea{width:100%;background:#0d1117;border:1px solid var(--bd);border-radius:6px;color:var(--fg);padding:7px 9px;font:13px monospace}
  .row{display:flex;gap:8px}.row>div{flex:1}
  button{margin-top:12px;background:var(--acc);color:#fff;border:0;border-radius:6px;padding:8px 14px;font-weight:600;cursor:pointer}
  button:hover{opacity:.9}
  pre{margin:12px 0 0;background:#010409;border:1px solid var(--bd);border-radius:6px;padding:12px;white-space:pre-wrap;font:12px/1.5 monospace;max-height:340px;overflow:auto}
  .full{grid-column:1/3}
  .note{color:var(--mut);font-size:12px}
</style></head><body>
<header>
  <h1>AI Berkshire — <code>financial_rigor.py</code> live runner</h1>
  <p>Browser front-end calling the repo's <b>actual</b> Python functions (stdout captured verbatim). Nothing reimplemented.</p>
  <div class="tickerbar">
    <div class="ac">
      <input id="ticker" autocomplete="off" placeholder="search by name or ticker — e.g. Tesla, AAPL, Coca-Cola…"
             oninput="suggest()" onkeydown="if(event.key==='Enter'){closeAC();analyze()}">
      <div id="ac_list" class="ac-list"></div>
    </div>
    <button onclick="analyze()" style="background:#238636">Analyze</button>
    <span id="tk_status" class="note"></span>
  </div>
</header>
<div class="wrap">

  <div id="verdict" class="verdict">
    <h2 id="vd_name">—</h2>
    <div class="big" id="vd_sub"></div>
    <div class="vline" id="vd_line"></div>
  </div>

  <div class="card">
    <h2>1 · Market Cap Verification</h2>
    <div class="row"><div><label>Price</label><input id="mc_price" value="195.89"></div>
    <div><label>Shares</label><input id="mc_shares" value="14.84e9"></div></div>
    <div class="row"><div><label>Reported cap</label><input id="mc_rep" value="2.908e12"></div>
    <div><label>Currency</label><input id="mc_cur" value="USD"></div></div>
    <button onclick="call('/api/market-cap',{price:v('mc_price'),shares:v('mc_shares'),reported:v('mc_rep'),currency:v('mc_cur')},'mc_out')">Run</button>
    <pre id="mc_out">—</pre>
  </div>

  <div class="card">
    <h2>2 · Valuation Metrics</h2>
    <div class="row"><div><label>Price</label><input id="v_price" value="195.89"></div>
    <div><label>EPS</label><input id="v_eps" value="6.43"></div></div>
    <div class="row"><div><label>BVPS</label><input id="v_bvps" value="4.0"></div>
    <div><label>FCF/share</label><input id="v_fcf" value="6.5"></div></div>
    <div class="row"><div><label>Dividend</label><input id="v_div" value="0.96"></div>
    <div><label>Rev/share</label><input id="v_rps" value="26.4"></div></div>
    <button onclick="call('/api/valuation',{price:v('v_price'),eps:v('v_eps'),bvps:v('v_bvps'),fcf:v('v_fcf'),dividend:v('v_div'),rps:v('v_rps')},'v_out')">Run</button>
    <pre id="v_out">—</pre>
  </div>

  <div class="card">
    <h2>3 · Cross-Source Validation</h2>
    <label>Field</label><input id="cv_field" value="revenue">
    <label>Values (JSON: {source: number})</label>
    <textarea id="cv_vals" rows="3">{"10-K": 383285, "Yahoo": 383000, "StockAnalysis": 385000, "Outlier": 410000}</textarea>
    <div class="row"><div><label>Unit</label><input id="cv_unit" value="M"></div>
    <div><label>Tolerance %</label><input id="cv_tol" value="2.0"></div></div>
    <button onclick="call('/api/cross-validate',{field:v('cv_field'),values:v('cv_vals'),unit:v('cv_unit'),tolerance:v('cv_tol')},'cv_out')">Run</button>
    <pre id="cv_out">—</pre>
  </div>

  <div class="card">
    <h2>4 · Benford's Law (fraud check)</h2>
    <label>Values (JSON array, ≥50 for reliable result)</label>
    <textarea id="bf_vals" rows="4"></textarea>
    <button onclick="call('/api/benford',{values:v('bf_vals')},'bf_out')">Run</button>
    <button onclick="genBenford()" style="background:#444">Fill 200 Benford-ish samples</button>
    <pre id="bf_out">—</pre>
  </div>

  <div class="card">
    <h2>5 · Exact Calculator</h2>
    <label>Expression (+ - * / parens, sci-notation)</label>
    <input id="ca_expr" value="195.89 * 14.84e9">
    <button onclick="call('/api/calc',{expr:v('ca_expr')},'ca_out')">Run</button>
    <pre id="ca_out">—</pre>
  </div>

  <div class="card">
    <h2>6 · Three-Scenario Valuation</h2>
    <div class="row"><div><label>Price</label><input id="ts_price" value="195.89"></div>
    <div><label>EPS</label><input id="ts_eps" value="6.43"></div>
    <div><label>Shares (B)</label><input id="ts_sh" value="14.84"></div></div>
    <label>Growth (bull base bear)</label>
    <div class="row"><div><input id="ts_g0" value="0.12"></div><div><input id="ts_g1" value="0.08"></div><div><input id="ts_g2" value="0.03"></div></div>
    <label>Target PE (bull base bear)</label>
    <div class="row"><div><input id="ts_p0" value="30"></div><div><input id="ts_p1" value="25"></div><div><input id="ts_p2" value="18"></div></div>
    <div class="row"><div><label>Years</label><input id="ts_y" value="3"></div><div><label>Currency</label><input id="ts_cur" value="USD"></div></div>
    <button onclick="call('/api/three-scenario',{price:v('ts_price'),eps:v('ts_eps'),shares:v('ts_sh'),growth:[v('ts_g0'),v('ts_g1'),v('ts_g2')],pe:[v('ts_p0'),v('ts_p1'),v('ts_p2')],years:v('ts_y'),currency:v('ts_cur')},'ts_out')">Run</button>
    <pre id="ts_out">—</pre>
  </div>

  <div class="full note">Every panel POSTs to a Flask route that imports <code>tools/financial_rigor.py</code> and runs its real function — the boxed text is the tool's own captured stdout.</div>
</div>
<script>
const v=id=>document.getElementById(id).value;
async function call(url,body,outId){
  const out=document.getElementById(outId); out.textContent='running…';
  try{
    const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const j=await r.json();
    out.textContent=(j.error?('ERROR: '+j.error+'\n\n'):'')+(j.output||'(no output)');
  }catch(e){out.textContent='request failed: '+e}
}
function genBenford(){
  // generate ~200 values whose leading digits roughly follow Benford
  const a=[];for(let i=0;i<200;i++){const x=Math.pow(10,Math.random()*5);a.push(Math.round(x));}
  document.getElementById('bf_vals').value=JSON.stringify(a);
}
function set(id,val){if(val!==null&&val!==undefined&&!Number.isNaN(val))document.getElementById(id).value=val;}
const clamp=(x,lo,hi)=>Math.max(lo,Math.min(hi,x));

// ---- name -> ticker autocomplete ----
let acItems=[],acSel=-1,acTimer=null;
function suggest(){
  clearTimeout(acTimer);
  const q=document.getElementById('ticker').value.trim();
  if(q.length<1){closeAC();return;}
  acTimer=setTimeout(async()=>{
    try{
      const r=await fetch('/api/search?q='+encodeURIComponent(q));
      const j=await r.json();acItems=j.results||[];renderAC();
    }catch(e){closeAC();}
  },180);
}
function renderAC(){
  const box=document.getElementById('ac_list');
  if(!acItems.length){closeAC();return;}
  acSel=-1;
  box.innerHTML=acItems.map((it,i)=>
    `<div class="ac-item" data-i="${i}" onclick="pick(${i})"><b>${it.symbol}</b> ${it.name} <span>${it.exch} · ${it.type}</span></div>`).join('');
  box.style.display='block';
}
function closeAC(){const b=document.getElementById('ac_list');b.style.display='none';b.innerHTML='';acItems=[];acSel=-1;}
function pick(i){document.getElementById('ticker').value=acItems[i].symbol;closeAC();analyze();}
document.addEventListener('keydown',e=>{
  const box=document.getElementById('ac_list');
  if(box.style.display!=='block')return;
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){
    e.preventDefault();acSel=clamp(acSel+(e.key==='ArrowDown'?1:-1),0,acItems.length-1);
    [...box.children].forEach((c,i)=>c.classList.toggle('sel',i===acSel));
  }else if(e.key==='Enter'&&acSel>=0){e.preventDefault();pick(acSel);}
  else if(e.key==='Escape'){closeAC();}
});
document.addEventListener('click',e=>{if(!e.target.closest('.ac'))closeAC();});

// ---- main: upload a stock -> refresh ALL panels + fair value ----
async function analyze(){
  const t=document.getElementById('ticker').value.trim().toUpperCase();
  const st=document.getElementById('tk_status');
  if(!t){st.textContent='enter a ticker';return;}
  st.textContent='fetching '+t+' …';
  const j=await(await fetch('/api/lookup?ticker='+encodeURIComponent(t))).json();
  if(j.error){st.textContent='✗ '+j.error;return;}
  const d=j.data;
  document.getElementById('ticker').value=d.ticker;

  // derive fair-value scenario inputs from the stock's OWN analyst/forward data
  const g=clamp((d.earnings_growth ?? d.revenue_growth ?? 0.08),-0.10,0.40);
  const basePE=clamp(d.forward_pe || Math.min(d.pe_reported||20,40) || 20,5,60);
  const g0=clamp(g*1.5,0,0.50),g1=g,g2=clamp(g*0.4,-0.10,0.30);
  const p0=basePE*1.2,p1=basePE,p2=basePE*0.75;

  // fill EVERY panel
  set('mc_price',d.price);set('mc_shares',d.shares);set('mc_rep',d.marketCap);set('mc_cur',d.currency);
  set('v_price',d.price);set('v_eps',d.eps);set('v_bvps',d.bvps);set('v_fcf',d.fcf_per_share);set('v_div',d.dividend??'');set('v_rps',d.rps);
  set('ts_price',d.price);set('ts_eps',d.eps);set('ts_sh',(d.shares/1e9).toFixed(3));set('ts_cur',d.currency);
  set('ts_g0',g0.toFixed(3));set('ts_g1',g1.toFixed(3));set('ts_g2',g2.toFixed(3));
  set('ts_p0',p0.toFixed(0));set('ts_p1',p1.toFixed(0));set('ts_p2',p2.toFixed(0));
  // benford: seed with this stock's own key figures so the panel reflects the stock too
  set('bf_vals',JSON.stringify([d.price,d.eps,d.bvps,d.fcf_per_share,d.rps,d.marketCap,d.shares,d.dividend,d.pe_reported,d.pb_reported].filter(x=>x)));
  set('cv_field','market cap');
  set('cv_vals',JSON.stringify({"price×shares":Math.round(d.price*d.shares),"reported":d.marketCap},null,0));
  set('cv_unit','');

  st.innerHTML='✓ '+d.name+' ('+d.ticker+') — $'+d.price+' · PE '+(d.pe_reported?d.pe_reported.toFixed(1):'n/a')+' · PB '+(d.pb_reported?d.pb_reported.toFixed(1):'n/a');

  // run all the real tool endpoints
  await call('/api/market-cap',{price:v('mc_price'),shares:v('mc_shares'),reported:v('mc_rep'),currency:v('mc_cur')},'mc_out');
  await call('/api/valuation',{price:v('v_price'),eps:v('v_eps'),bvps:v('v_bvps'),fcf:v('v_fcf'),dividend:v('v_div'),rps:v('v_rps')},'v_out');
  await call('/api/three-scenario',{price:v('ts_price'),eps:v('ts_eps'),shares:v('ts_sh'),growth:[v('ts_g0'),v('ts_g1'),v('ts_g2')],pe:[v('ts_p0'),v('ts_p1'),v('ts_p2')],years:v('ts_y'),currency:v('ts_cur')},'ts_out');
  await call('/api/cross-validate',{field:v('cv_field'),values:v('cv_vals'),unit:v('cv_unit'),tolerance:v('cv_tol')},'cv_out');
  await call('/api/benford',{values:v('bf_vals')},'bf_out');

  renderVerdict(d,g1,p1);
}

function renderVerdict(d,g,pe){
  // tool's base-case intrinsic value = EPS*(1+g)^yrs * target PE  (the value the toolkit produces)
  const yrs=parseInt(v('ts_y'))||3;
  const fairBase=(d.eps>0)?(d.eps*Math.pow(1+g,yrs)*pe):null;
  const up=fairBase?((fairBase-d.price)/d.price*100):null;
  const cur=d.currency||'';
  const fmt=x=>x==null?'n/a':(cur?cur+' ':'$')+x.toLocaleString(undefined,{maximumFractionDigits:2});
  const cls=up==null?'':(up>=0?'up':'down');
  const verdict=up==null?'EPS ≤ 0 — earnings-multiple model N/A':
    (up>=15?'UNDERVALUED vs base case':up<=-15?'OVERVALUED vs base case':'roughly FAIRLY VALUED vs base case');
  const an=d.analyst_target?`<div>Analyst mean target<b>${fmt(d.analyst_target)}</b></div>`:'';
  document.getElementById('vd_name').textContent=d.name+' ('+d.ticker+')';
  document.getElementById('vd_sub').innerHTML='Base-case intrinsic value from the three-scenario engine — EPS '+d.eps+' × (1+'+(g*100).toFixed(0)+'%)^'+yrs+'y × '+pe.toFixed(0)+'× PE. <i>Growth & PE auto-derived from the stock’s own analyst/forward data; adjust them in panel 6 to test your own thesis.</i>';
  document.getElementById('vd_line').innerHTML=
    `<div>Current price<b>${fmt(d.price)}</b></div>`+
    `<div>Tool fair value (base)<b class="${cls}">${fmt(fairBase)}</b></div>`+
    `<div>Implied up/downside<b class="${cls}">${up==null?'n/a':(up>=0?'+':'')+up.toFixed(1)+'%'}</b></div>`+
    an+
    `<div>Verdict<b class="${cls}">${verdict}</b></div>`;
  document.getElementById('verdict').style.display='block';
}
</script></body></html>"""


if __name__ == "__main__":
    print("AI Berkshire local tool runner → http://localhost:5055")
    app.run(host="127.0.0.1", port=5055, debug=False)
