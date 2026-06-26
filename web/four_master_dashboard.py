#!/usr/bin/env python3
"""AI Berkshire — four-master analysis dashboard.

Read-only viewer over the repo's own data:
  - intrinsic value comes from the repo's morningstar_fair_value_*.csv (newest)
  - live fundamentals via yfinance (the numbers each master references)
  - four-master lenses follow the repo's investment-team.md mapping:
        Duan Yongping -> business model & moat
        Buffett       -> financials & valuation
        Munger        -> industry structure & competition
        Li Lu         -> risk & margin of safety
  - the AI card + summary synthesize the four.

The persona takes are heuristic interpretations of each investor's documented
principles applied to the live numbers — not the investors' actual opinions
and not a live LLM call. Clearly labeled as such in the UI.
"""
import csv
import glob
import io
import json
import os
import urllib.parse
import urllib.request

from flask import Flask, request, jsonify, Response
import yfinance as yf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # web/ -> repo root
app = Flask(__name__)


# --------------------------------------------------------------------------
# repo data: newest Morningstar fair-value export
# --------------------------------------------------------------------------
def morningstar_row(ticker):
    files = sorted(glob.glob(os.path.join(ROOT, "data", "morningstar_fair_value_*.csv")))
    if not files:
        return None, None
    newest = files[-1]
    with open(newest, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("ticker") or "").upper() == ticker:
                return row, os.path.basename(newest)
    return None, os.path.basename(newest)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def pct(x, nd=1):
    return None if x is None else round(x * 100, nd)


def num(label, value, suffix="", nd=2):
    if value is None:
        return None
    if isinstance(value, float):
        value = round(value, nd)
    return {"label": label, "value": f"{value:,}{suffix}" if isinstance(value, (int, float)) else f"{value}{suffix}"}


def stance(score):
    if score >= 2:
        return "Bullish"
    if score <= -2:
        return "Bearish"
    return "Neutral"


# --------------------------------------------------------------------------
# four-master persona engines (heuristic, grounded in real metrics)
# --------------------------------------------------------------------------
def duan(m, ms):
    """Duan Yongping — good business, moat, pricing power, differentiation."""
    moat = (ms or {}).get("moat") or "Unknown"
    gm, om, rg = m.get("gross"), m.get("oper"), m.get("rev_growth")
    s = 0
    s += {"Wide": 2, "Narrow": 1, "None": -1}.get(moat, 0)
    if gm is not None:
        s += 1 if gm > 40 else (-1 if gm < 25 else 0)
    if om is not None:
        s += 1 if om > 15 else (-1 if om < 5 else 0)
    if rg is not None:
        s += 1 if rg > 10 else (-1 if rg < 0 else 0)
    st = stance(s)
    why = (f"Morningstar tags a <b>{moat}</b> moat. "
           + (f"Gross margin {gm}% and operating margin {om}% " if gm is not None and om is not None else "")
           + ("signal real pricing power — the mark of a 'good business.'" if st == "Bullish"
              else "are thin for a durable franchise; differentiation looks shaky." if st == "Bearish"
              else "are decent but not a slam-dunk moat."))
    nums = [n for n in [
        num("Moat", moat), num("Gross margin", gm, "%"), num("Operating margin", om, "%"),
        num("Revenue growth", rg, "%")] if n]
    return {"name": "Duan Yongping", "lens": "Business model & moat", "stance": st,
            "why": why, "numbers": nums}


def buffett(m, ms):
    """Buffett — financial quality + intrinsic value / margin of safety."""
    roe, fcf, up, de = m.get("roe"), m.get("fcf"), m.get("upside"), m.get("debt_eq")
    s = 0
    if roe is not None:
        s += 2 if roe > 20 else (1 if roe > 15 else (-1 if roe < 8 else 0))
    if fcf is not None:
        s += 1 if fcf > 0 else -1
    if up is not None:
        s += 2 if up > 15 else (1 if up > 0 else (-2 if up < -15 else -1))
    if de is not None and de > 150:
        s -= 1
    st = stance(s)
    why = ((f"ROE of {roe}% " if roe is not None else "")
           + ("clears his quality bar; " if roe and roe > 15 else "is light; " if roe is not None else "")
           + (f"trades {abs(up):.0f}% {'below' if up > 0 else 'above'} Morningstar fair value, "
              if up is not None else "")
           + ("a margin of safety he'd like." if st == "Bullish"
              else "no margin of safety here." if st == "Bearish"
              else "roughly fair — he'd wait for a better price."))
    nums = [n for n in [
        num("ROE", roe, "%"), num("Free cash flow", m.get("fcf_b"), "B"),
        num("Price vs fair value", up, "%"), num("PE (TTM)", m.get("pe")),
        num("Debt/Equity", de)] if n]
    return {"name": "Warren Buffett", "lens": "Financials & valuation", "stance": st,
            "why": why, "numbers": nums}


def munger(m, ms):
    """Munger — industry structure, mental models, invert (what kills it)."""
    moat = (ms or {}).get("moat") or "Unknown"
    sector = (ms or {}).get("sector") or m.get("sector") or "—"
    pe = m.get("pe")
    s = 0
    s += {"Wide": 2, "Narrow": 1, "None": -1}.get(moat, 0)
    if pe is not None:
        s += -1 if pe > 50 else (1 if 0 < pe < 25 else 0)
    if (m.get("eps") or 0) <= 0:
        s -= 1
    st = stance(s)
    kill = {"Consumer Cyclical": "demand cyclicality and price wars",
            "Technology": "disruption and multiple compression",
            "Consumer Defensive": "stagnant volumes and brand erosion",
            "Financial Services": "credit cycles and leverage"}.get(sector, "competitive erosion")
    why = (f"Inverting: what kills <b>{sector}</b> names is {kill}. "
           + (f"At {pe:.0f}× earnings " if pe and pe > 0 else "With no positive earnings " if (m.get('eps') or 0) <= 0 else "")
           + ("the price already bakes in perfection — easy way to be stupid." if st == "Bearish"
              else "the quality justifies a fair multiple." if st == "Bullish"
              else "it's a 'too hard' / wait pile for him."))
    nums = [n for n in [num("Moat", moat), num("Sector", sector), num("PE (TTM)", pe)] if n]
    return {"name": "Charlie Munger", "lens": "Industry & competition", "stance": st,
            "why": why, "numbers": nums}


def lilu(m, ms):
    """Li Lu — risk, downside, margin of safety, uncertainty."""
    up, beta, unc = m.get("upside"), m.get("beta"), (ms or {}).get("uncertainty")
    s = 0
    if up is not None:
        s += 2 if up > 25 else (1 if up > 0 else -1)
    if beta is not None and beta > 1.5:
        s -= 1
    if unc in ("High", "Very High"):
        s -= 1
    elif unc in ("Low",):
        s += 1
    if (m.get("eps") or 0) <= 0:
        s -= 1
    st = stance(s)
    why = ((f"Implied margin of safety is {up:.0f}% to fair value. " if up is not None else "")
           + (f"Beta {beta} means {'sharp' if beta and beta > 1.5 else 'moderate'} drawdown risk. " if beta is not None else "")
           + ("Downside looks protected enough to act." if st == "Bullish"
              else "Too little cushion for the risk." if st == "Bearish"
              else "Cushion is thin; he'd size small or pass."))
    nums = [n for n in [
        num("Margin of safety", up, "%"), num("Beta", beta),
        num("Uncertainty", unc or "—"), num("EPS (TTM)", m.get("eps"))] if n]
    return {"name": "Li Lu", "lens": "Risk & margin of safety", "stance": st,
            "why": why, "numbers": nums}


def ai_synthesis(masters, m, ms):
    score = sum({"Bullish": 1, "Neutral": 0, "Bearish": -1}[p["stance"]] for p in masters)
    up = m.get("upside")
    if up is not None:
        score += 1 if up > 15 else (-1 if up < -10 else 0)
    overall = "Bullish" if score >= 2 else "Bearish" if score <= -2 else "Mixed / Neutral"
    drivers, risks = [], []
    if (ms or {}).get("moat") in ("Wide", "Narrow"):
        drivers.append(f"{ms['moat']} economic moat")
    if m.get("roe") and m["roe"] > 15:
        drivers.append(f"high ROE ({m['roe']}%)")
    if up and up > 0:
        drivers.append(f"{up:.0f}% discount to fair value")
    if m.get("fcf") and m["fcf"] > 0:
        drivers.append("positive free cash flow")
    if up is not None and up < 0:
        risks.append("trades above fair value")
    if m.get("pe") and m["pe"] > 50:
        risks.append(f"rich {m['pe']:.0f}× earnings")
    if (m.get("eps") or 0) <= 0:
        risks.append("no positive trailing earnings")
    if m.get("beta") and m["beta"] > 1.5:
        risks.append(f"high volatility (beta {m['beta']})")
    why = (f"Weighing all four lenses plus the {up:.0f}% gap to Morningstar fair value, "
           if up is not None else "Weighing all four lenses, ")
    why += ("the signal leans constructive." if overall == "Bullish"
            else "the signal leans cautious." if overall == "Bearish"
            else "the picture is genuinely mixed — quality on one side, price/risk on the other.")
    return {"name": "AI synthesis", "lens": "Independent cross-read", "stance": overall,
            "why": why, "drivers": drivers or ["—"], "risks": risks or ["none flagged"]}


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------
@app.route("/api/search")
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"results": []})
    url = ("https://query2.finance.yahoo.com/v1/finance/search?q="
           + urllib.parse.quote(q) + "&quotesCount=8&newsCount=0")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=8).read())
    except Exception as e:
        return jsonify({"results": [], "error": str(e)})
    out = [{"symbol": x.get("symbol"),
            "name": x.get("shortname") or x.get("longname") or x.get("symbol"),
            "exch": x.get("exchDisp") or x.get("exchange") or ""}
           for x in data.get("quotes", []) if x.get("symbol") and x.get("quoteType") in ("EQUITY", "ETF")]
    return jsonify({"results": out})


@app.route("/api/stock")
def stock():
    t = (request.args.get("ticker") or "").strip().upper()
    if not t:
        return jsonify({"error": "no ticker"}), 400
    try:
        tk = yf.Ticker(t)
        info = tk.info
    except Exception as e:
        return jsonify({"error": f"lookup failed: {e}"}), 502
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if not price:
        return jsonify({"error": f"no data for '{t}'"}), 404

    ms, src = morningstar_row(t)
    fv = float(ms["fair_value"]) if ms and ms.get("fair_value") else None
    up = (fv - price) / price * 100 if fv else None

    fcf = info.get("freeCashflow")
    m = {
        "roe": pct(info.get("returnOnEquity")), "gross": pct(info.get("grossMargins")),
        "oper": pct(info.get("operatingMargins")), "rev_growth": pct(info.get("revenueGrowth")),
        "pe": round(info["trailingPE"], 1) if info.get("trailingPE") else None,
        "eps": info.get("trailingEps"), "beta": round(info["beta"], 2) if info.get("beta") else None,
        "debt_eq": round(info["debtToEquity"], 0) if info.get("debtToEquity") else None,
        "fcf": fcf, "fcf_b": round(fcf / 1e9, 1) if fcf else None,
        "upside": round(up, 1) if up is not None else None,
        "sector": info.get("sector"),
    }

    # price history for the chart (1y daily close)
    chart = {"labels": [], "close": []}
    try:
        hist = tk.history(period="1y", interval="1d")
        for ts, row in hist.iterrows():
            chart["labels"].append(ts.strftime("%Y-%m-%d"))
            chart["close"].append(round(float(row["Close"]), 2))
    except Exception:
        pass

    masters = [duan(m, ms), buffett(m, ms), munger(m, ms), lilu(m, ms)]
    ai = ai_synthesis(masters, m, ms)
    tally = {s: sum(1 for p in masters if p["stance"] == s) for s in ("Bullish", "Neutral", "Bearish")}

    return jsonify({"data": {
        "ticker": t, "name": info.get("shortName") or info.get("longName") or t,
        "price": round(price, 2), "currency": info.get("currency", "USD"),
        "fair_value": fv, "upside": m["upside"], "moat": (ms or {}).get("moat"),
        "star": (ms or {}).get("star_rating"), "sector": (ms or {}).get("sector") or info.get("sector"),
        "industry": (ms or {}).get("industry") or info.get("industry"),
        "analyst_target": info.get("targetMeanPrice"),
        "source": src, "in_repo": ms is not None,
        "chart": chart, "masters": masters, "ai": ai, "tally": tally,
    }})


@app.route("/")
def index():
    return Response(HTML, mimetype="text/html")


HTML = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Berkshire — Four-Master Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root{--bg:#0b0e14;--panel:#151b26;--bd:#263041;--fg:#e6edf3;--mut:#8b97a8;--acc:#4f9cf9;
        --bull:#2ea043;--bear:#f85149;--neu:#d29922}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 -apple-system,Segoe UI,Roboto,sans-serif}
  header{padding:18px 24px;border-bottom:1px solid var(--bd);position:sticky;top:0;background:var(--bg);z-index:30}
  h1{margin:0;font-size:17px}.sub{color:var(--mut);font-size:12px;margin-top:3px}
  .bar{margin-top:12px;display:flex;gap:8px;align-items:flex-start}
  .ac{position:relative;width:360px}
  input{width:100%;background:#0b0e14;border:1px solid var(--bd);border-radius:7px;color:var(--fg);padding:9px 11px;font-size:14px;font-weight:600}
  button{background:var(--acc);color:#fff;border:0;border-radius:7px;padding:9px 16px;font-weight:600;cursor:pointer}
  .ac-list{position:absolute;left:0;right:0;top:100%;background:var(--panel);border:1px solid var(--bd);border-top:0;border-radius:0 0 8px 8px;max-height:300px;overflow:auto;display:none;z-index:40}
  .ac-item{padding:8px 11px;cursor:pointer;border-bottom:1px solid #1d2533;font-size:13px}
  .ac-item:hover,.ac-item.sel{background:#1f6feb33}.ac-item b{color:var(--acc)}.ac-item span{float:right;color:var(--mut);font-size:11px}
  .wrap{max-width:1180px;margin:0 auto;padding:22px}
  .hero{display:flex;flex-wrap:wrap;align-items:center;gap:18px;background:var(--panel);border:1px solid var(--bd);border-radius:12px;padding:18px;margin-bottom:18px}
  .hero .px{font-size:30px;font-weight:700}
  .pill{padding:3px 10px;border-radius:20px;font-size:12px;font-weight:700}
  .Bullish{background:#2ea04322;color:var(--bull)} .Bearish{background:#f8514922;color:var(--bear)}
  .Neutral,.Mixed{background:#d2992222;color:var(--neu)} .Mixed\/\ Neutral{background:#d2992222;color:var(--neu)}
  .hero .kv{color:var(--mut);font-size:12px}.hero .kv b{display:block;color:var(--fg);font-size:15px}
  .chartcard{background:var(--panel);border:1px solid var(--bd);border-radius:12px;padding:14px 16px;margin-bottom:18px}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
  .card{background:var(--panel);border:1px solid var(--bd);border-radius:12px;padding:16px;border-top:3px solid var(--bd)}
  .card.b{border-top-color:var(--bull)} .card.r{border-top-color:var(--bear)} .card.n{border-top-color:var(--neu)}
  .card h3{margin:0;font-size:15px;display:flex;justify-content:space-between;align-items:center}
  .card .role{color:var(--mut);font-size:12px;margin:2px 0 10px}
  .card .why{font-size:13.5px}.card .why b{color:var(--fg)}
  .nums{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
  .nums div{background:#0b0e14;border:1px solid var(--bd);border-radius:7px;padding:6px 9px;font-size:12px}
  .nums div b{display:block;color:var(--mut);font-weight:500;font-size:11px}
  .full{grid-column:1/3}
  .lists{display:flex;gap:24px;flex-wrap:wrap;margin-top:10px}
  .lists ul{margin:4px 0;padding-left:18px}.lists h4{margin:0;font-size:12px;color:var(--mut)}
  .disclaim{color:var(--mut);font-size:11.5px;margin-top:16px;line-height:1.5}
  @media(max-width:760px){.grid{grid-template-columns:1fr}.full{grid-column:auto}}
</style></head><body>
<header>
  <h1>AI Berkshire — Four-Master Dashboard</h1>
  <div class="sub">Intrinsic value from the repo's Morningstar export · live fundamentals via yfinance · four-master lenses per <code>investment-team.md</code></div>
  <div class="bar">
    <div class="ac">
      <input id="q" autocomplete="off" placeholder="Search a company or ticker — Tesla, AAPL, Coca-Cola…"
             oninput="suggest()" onkeydown="if(event.key==='Enter'){closeAC();go()}">
      <div id="ac" class="ac-list"></div>
    </div>
    <button onclick="go()">Analyze</button>
  </div>
</header>
<div class="wrap" id="wrap">
  <div class="disclaim">Enter a stock above. The four-master takes are heuristic readings of each investor's documented principles applied to the live numbers — not their actual opinions, and not a live LLM call.</div>
</div>

<script>
const $=id=>document.getElementById(id);
let chart=null,acItems=[],acSel=-1,t=null;
function suggest(){
  clearTimeout(t);const q=$('q').value.trim();if(!q){closeAC();return;}
  t=setTimeout(async()=>{try{const j=await(await fetch('/api/search?q='+encodeURIComponent(q))).json();
    acItems=j.results||[];const b=$('ac');if(!acItems.length){closeAC();return;}
    b.innerHTML=acItems.map((it,i)=>`<div class="ac-item" onclick="pick(${i})"><b>${it.symbol}</b> ${it.name}<span>${it.exch}</span></div>`).join('');
    b.style.display='block';acSel=-1;}catch(e){closeAC();}},170);
}
function closeAC(){$('ac').style.display='none';acItems=[];acSel=-1;}
function pick(i){$('q').value=acItems[i].symbol;closeAC();go();}
document.addEventListener('keydown',e=>{const b=$('ac');if(b.style.display!=='block')return;
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();acSel=Math.max(0,Math.min(acItems.length-1,acSel+(e.key==='ArrowDown'?1:-1)));
    [...b.children].forEach((c,i)=>c.classList.toggle('sel',i===acSel));}
  else if(e.key==='Enter'&&acSel>=0){e.preventDefault();pick(acSel);}else if(e.key==='Escape')closeAC();});
document.addEventListener('click',e=>{if(!e.target.closest('.ac'))closeAC();});

const cls=s=>s==='Bullish'?'b':(s==='Bearish'?'r':'n');
const fmt=(v,c)=>v==null?'n/a':(c||'$')+Number(v).toLocaleString(undefined,{maximumFractionDigits:2});

async function go(){
  const tk=$('q').value.trim().toUpperCase();if(!tk)return;
  $('wrap').innerHTML='<div class="disclaim">Analyzing '+tk+' …</div>';
  const j=await(await fetch('/api/stock?ticker='+encodeURIComponent(tk))).json();
  if(j.error){$('wrap').innerHTML='<div class="disclaim">✗ '+j.error+'</div>';return;}
  render(j.data);
}
function masterCard(p){
  return `<div class="card ${cls(p.stance)}">
    <h3>${p.name}<span class="pill ${p.stance}">${p.stance}</span></h3>
    <div class="role">${p.lens}</div>
    <div class="why">${p.why}</div>
    <div class="nums">${(p.numbers||[]).map(n=>`<div><b>${n.label}</b>${n.value}</div>`).join('')}</div>
  </div>`;
}
function render(d){
  const c=d.currency==='USD'?'$':d.currency+' ';
  const up=d.upside;
  const hero=`<div class="hero">
    <div><div style="font-size:13px;color:var(--mut)">${d.ticker}</div><div class="px">${d.name}</div></div>
    <div class="kv">Price<b>${fmt(d.price,c)}</b></div>
    <div class="kv">Fair value (repo)<b>${d.fair_value?fmt(d.fair_value,c):'n/a'}</b></div>
    <div class="kv">Upside<b style="color:${up>=0?'var(--bull)':'var(--bear)'}">${up==null?'n/a':(up>=0?'+':'')+up+'%'}</b></div>
    <div class="kv">Moat<b>${d.moat||'—'}</b></div>
    <div class="kv">Sector<b>${d.sector||'—'}</b></div>
    <div class="kv">Analyst target<b>${d.analyst_target?fmt(d.analyst_target,c):'—'}</b></div>
    <div style="margin-left:auto"><span class="pill ${d.ai.stance.replace('/','\\/').replace(' ','\\ ')}">${d.ai.stance}</span></div>
  </div>`;
  const chartCard=`<div class="chartcard"><div style="font-size:13px;color:var(--mut);margin-bottom:6px">Price — last 12 months</div><canvas id="cv" height="90"></canvas></div>`;
  const ai=d.ai;
  const aiCard=`<div class="card full ${cls(ai.stance==='Bullish'?'Bullish':ai.stance==='Bearish'?'Bearish':'Neutral')}">
    <h3>🤖 ${ai.name}<span class="pill ${ai.stance.replace('/','\\/').replace(' ','\\ ')}">${ai.stance}</span></h3>
    <div class="role">${ai.lens}</div><div class="why">${ai.why}</div>
    <div class="lists">
      <div><h4>Key drivers</h4><ul>${ai.drivers.map(x=>'<li>'+x+'</li>').join('')}</ul></div>
      <div><h4>Risks</h4><ul>${ai.risks.map(x=>'<li>'+x+'</li>').join('')}</ul></div>
    </div></div>`;
  const ty=d.tally;
  const verdict=ty.Bullish>ty.Bearish?'Net constructive':ty.Bearish>ty.Bullish?'Net cautious':'Split';
  const summary=`<div class="card full n">
    <h3>📋 Summary — all five<span class="pill ${ai.stance.replace('/','\\/').replace(' ','\\ ')}">${verdict}</span></h3>
    <div class="role">Consensus across the four masters + AI</div>
    <div class="why">Tally: <b style="color:var(--bull)">${ty.Bullish} bullish</b> · <b style="color:var(--neu)">${ty.Neutral} neutral</b> · <b style="color:var(--bear)">${ty.Bearish} bearish</b>.
      ${d.fair_value?`The repo's Morningstar fair value is ${fmt(d.fair_value,c)} vs ${fmt(d.price,c)} today (${up>=0?'+':''}${up}%).`:''}
      ${verdict==='Net constructive'?' Quality + valuation tilt favorable, but check the dissenting card before acting.':verdict==='Net cautious'?' Price/risk outweigh quality on balance.':' Genuinely split — the decision hinges on whether you trust the moat enough to pay today\'s price.'}</div>
  </div>`;
  $('wrap').innerHTML=hero+chartCard+'<div class="grid">'+
    d.masters.map(masterCard).join('')+aiCard+summary+'</div>'+
    `<div class="disclaim">Fair value & moat: repo file <code>${d.source}</code>${d.in_repo?'':' (ticker not in repo file — value shown as n/a)'}. Live metrics: yfinance. Persona verdicts are rule-based interpretations of each investor's principles applied to these numbers — educational, not investment advice.</div>`;
  if(d.chart&&d.chart.labels.length){
    if(chart)chart.destroy();
    chart=new Chart($('cv'),{type:'line',data:{labels:d.chart.labels,
      datasets:[{data:d.chart.close,borderColor:'#4f9cf9',backgroundColor:'#4f9cf922',fill:true,pointRadius:0,borderWidth:1.5,tension:.1}]},
      options:{plugins:{legend:{display:false}},scales:{x:{ticks:{maxTicksLimit:8,color:'#8b97a8'},grid:{display:false}},
        y:{ticks:{color:'#8b97a8'},grid:{color:'#1d2533'}}}}});
  }
}
</script></body></html>"""


if __name__ == "__main__":
    print("AI Berkshire dashboard → http://localhost:5056")
    app.run(host="127.0.0.1", port=5056, debug=False)
