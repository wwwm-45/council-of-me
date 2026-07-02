"""Render a ReportContext + ReportSummaries into a single self-contained HTML file.

Fixed visual style (Anthropic palette + typography). All dynamic values are
HTML-escaped. No external assets, so the file opens offline.
"""
from __future__ import annotations

from html import escape

from app.services.report.builder import ReportContext
from app.services.report.summarizer import ReportSummaries

_TITLE = "你的内心议会 · 对话回响"

_CSS = """
:root{
  --space:#020617; --ink:#EDEBE3; --muted:#A6ABC4;
  --accent:#B6A8FF; --cool:#9D8DF1;
  --line:rgba(255,255,255,.12); --card:rgba(18,24,46,.55);
  --halo:0 1px 10px rgba(2,6,23,.55);
}
*{box-sizing:border-box}
body{margin:0;color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;
  line-height:1.75;text-shadow:0 1px 6px rgba(2,6,23,.5);
  background:
    radial-gradient(circle at 26% 18%, rgba(49,46,129,.42), transparent 38%),
    radial-gradient(circle at 82% 72%, rgba(88,28,135,.32), transparent 42%),
    radial-gradient(circle at 50% 112%, rgba(37,99,235,.14), transparent 50%),
    linear-gradient(180deg,#020617 0%,#05070d 55%,#01030a 100%);
  background-attachment:fixed;}
/* Strengthened starfield: discrete bright stars + two tiled layers, gently twinkling. */
body::before{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
  background:
    radial-gradient(1.6px 1.6px at 20% 30%, #fff, transparent),
    radial-gradient(1.4px 1.4px at 70% 60%, rgba(255,255,255,.92), transparent),
    radial-gradient(1.2px 1.2px at 40% 80%, rgba(255,255,255,.82), transparent),
    radial-gradient(1.8px 1.8px at 85% 22%, #fff, transparent),
    radial-gradient(1.2px 1.2px at 12% 66%, rgba(255,255,255,.78), transparent),
    radial-gradient(1.5px 1.5px at 55% 14%, rgba(255,255,255,.88), transparent),
    radial-gradient(1.3px 1.3px at 33% 50%, rgba(255,255,255,.7), transparent),
    radial-gradient(circle, rgba(255,255,255,.30) 1px, transparent 1.5px) 0 0/150px 150px,
    radial-gradient(circle, rgba(255,255,255,.18) 1px, transparent 1.5px) 75px 95px/115px 115px;
  animation:twinkle 6s ease-in-out infinite;}
/* Drifting colored motes — the particle layer. */
body::after{content:"";position:fixed;inset:-10% 0 0 0;z-index:0;pointer-events:none;
  background:
    radial-gradient(2.4px 2.4px at 30% 22%, rgba(157,141,241,.55), transparent),
    radial-gradient(2.6px 2.6px at 66% 48%, rgba(182,168,255,.5), transparent),
    radial-gradient(2px 2px at 80% 76%, rgba(255,255,255,.6), transparent),
    radial-gradient(2.2px 2.2px at 16% 70%, rgba(96,165,250,.5), transparent),
    radial-gradient(2.4px 2.4px at 50% 90%, rgba(125,211,230,.45), transparent);
  animation:float 16s ease-in-out infinite;}
@keyframes twinkle{0%,100%{opacity:.55}50%{opacity:1}}
@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-16px)}}
.page{position:relative;z-index:1;max-width:720px;margin:0 auto;padding:64px 28px 80px;}
h1,h2,h3{font-family:Georgia,"Times New Roman","Songti SC",serif;font-weight:600;letter-spacing:.01em;
  color:#F7F5EE;text-shadow:var(--halo);}
.cover{position:relative;overflow:hidden;border-radius:18px;border:1px solid rgba(255,255,255,.14);
  background:
    radial-gradient(circle at 78% 14%, rgba(157,141,241,.26), transparent 44%),
    linear-gradient(160deg,#050811 0%,#141c33 58%,#2a2140 100%);
  color:#F0EEE6;margin:-32px -4px 8px;padding:48px 28px 40px;box-shadow:0 24px 60px rgba(2,6,23,.5);
  -webkit-print-color-adjust:exact;print-color-adjust:exact;}
.kicker{font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:var(--accent);margin:0 0 14px;}
.cover h1{font-size:34px;line-height:1.25;margin:0 0 14px;color:#FBFAF6;}
.cover .dilemma{font-size:17px;color:rgba(240,238,230,.82);margin:0 0 18px;}
.cover .meta{font-size:13px;color:rgba(214,218,235,.7);margin:0;}
section{margin-top:44px;}
section h2{font-size:22px;margin:0 0 16px;padding-left:12px;border-left:3px solid var(--accent);}
.prose{font-size:16px;margin:0 0 18px;white-space:pre-wrap;color:var(--ink);}
ul{margin:0;padding:0;list-style:none;}
.voices li{padding:10px 0;border-bottom:1px dashed var(--line);font-size:15px;}
.voices .vname{display:inline-block;min-width:84px;font-weight:600;color:var(--accent);margin-right:8px;}
.tension{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-bottom:16px;
  backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);
  -webkit-print-color-adjust:exact;print-color-adjust:exact;}
.tension h3{font-size:17px;margin:0 0 12px;color:#F7F5EE;}
.poles{display:flex;align-items:stretch;gap:12px;}
.pole{flex:1;}
.pole b{display:block;color:var(--accent);margin-bottom:4px;}
.pole p{margin:0;font-size:14px;color:var(--muted);}
.vs{align-self:center;color:var(--cool);font-size:18px;}
.bar{height:6px;border-radius:6px;background:rgba(255,255,255,.12);margin:14px 0 0;overflow:hidden;}
.bar span{display:block;height:100%;background:linear-gradient(90deg,#7C6BE6,#B6A8FF);}
.bar-label{margin:6px 0 0;font-size:12px;color:var(--muted);text-align:right;}
blockquote{margin:14px 0 0;padding:10px 14px;border-left:3px solid var(--accent);background:rgba(255,255,255,.06);
  font-size:14px;color:var(--ink);border-radius:0 8px 8px 0;}
blockquote cite{display:block;margin-top:6px;font-style:normal;color:var(--muted);font-size:13px;}
.consensus li,.intents li,.highlights li{position:relative;padding:8px 0 8px 20px;font-size:15px;border-bottom:1px dashed var(--line);}
.consensus li::before,.intents li::before,.highlights li::before{content:"·";position:absolute;left:4px;color:var(--cool);font-weight:700;}
.blessing .prose{font-size:17px;}
footer{margin-top:56px;padding-top:20px;border-top:1px solid var(--line);font-size:12px;color:var(--muted);text-align:center;}
footer p{margin:4px 0;}
@media (max-width:520px){
  .poles{flex-direction:column;}
  .vs{align-self:center;transform:rotate(90deg);}
}
@media print{
  *{-webkit-print-color-adjust:exact;print-color-adjust:exact;}
  body{background:#040816;text-shadow:none;}
  body::before,body::after{display:none;}
  .page{max-width:none;padding:24px;}
  section,.tension{break-inside:avoid;page-break-inside:avoid;}
  .cover{break-after:avoid;}
}
"""


def _intensity_pct(intensity) -> int:
    v = float(intensity or 0)
    pct = v * 10 if v > 1 else v * 100
    return max(0, min(100, round(pct)))


def _evidence_block(ev) -> str:
    if ev and ev.get("content"):
        return (
            f'<blockquote>“{escape(ev["content"])}”'
            f'<cite>—— {escape(ev.get("agent", ""))}</cite></blockquote>'
        )
    return ""


def render_report_html(ctx: ReportContext, summaries: ReportSummaries) -> str:
    e = escape

    voices_html = "".join(
        f'<li><span class="vname">{e(v["name"])}</span>{e(v["stance"])}</li>'
        for v in ctx.voices
        if v.get("name") or v.get("stance")
    )

    tensions_html = ""
    for t in ctx.tensions:
        ev_html = _evidence_block(t.get("evidence_a")) + _evidence_block(t.get("evidence_b"))
        tensions_html += (
            '<div class="tension">'
            f'<h3>{e(t["name"])}</h3>'
            '<div class="poles">'
            f'<div class="pole"><b>{e(t["pole_a_label"])}</b><p>{e(t["pole_a_stance"])}</p></div>'
            '<div class="vs">↔</div>'
            f'<div class="pole"><b>{e(t["pole_b_label"])}</b><p>{e(t["pole_b_stance"])}</p></div>'
            "</div>"
            f'<div class="bar"><span style="width:{_intensity_pct(t["intensity"])}%"></span></div>'
            f'<p class="bar-label">张力强度 {_intensity_pct(t["intensity"])}%</p>'
            f"{ev_html}"
            "</div>"
        )

    consensus_html = "".join(
        f'<li>{e(c["description"])}</li>' for c in ctx.consensus if c.get("description")
    )
    intents_html = "".join(
        f'<li><b>{e(p["agent_name"])}</b> 守护着 {e(p["what_it_protects"])}'
        + (f"（{e(p['underlying_value'])}）" if p.get("underlying_value") else "")
        + "</li>"
        for p in ctx.protective_intents
        if p.get("agent_name") or p.get("what_it_protects")
    )
    highlights_html = "".join(f"<li>{e(h)}</li>" for h in ctx.highlights if h)
    highlights_section = (
        f'<section><h2>关键时刻</h2><ul class="highlights">{highlights_html}</ul></section>'
        if highlights_html
        else ""
    )

    rounds = e(str(ctx.meta.get("debate_rounds", 0)))
    agents = e(str(ctx.meta.get("agent_count", 0)))
    generated_at = e(ctx.meta.get("generated_at", ""))
    conv = ctx.meta.get("convergence_score")
    conv_html = (
        f" · 共识度 {round(float(conv) * 100)}%"
        if isinstance(conv, (int, float))
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(_TITLE)}</title>
<style>{_CSS}</style>
</head>
<body>
<main class="page">
  <header class="cover">
    <p class="kicker">内心议会 · 对话回响</p>
    <h1>{e(summaries.headline)}</h1>
    <p class="dilemma">{e(ctx.core_dilemma)}</p>
    <p class="meta">{rounds} 轮对话 · {agents} 个声音{conv_html} · {generated_at}</p>
  </header>

  <section>
    <h2>核心困境与内心声音</h2>
    <p class="prose">{e(summaries.dilemma_summary)}</p>
    <ul class="voices">{voices_html}</ul>
  </section>

  <section>
    <h2>核心张力与冲突</h2>
    <p class="prose">{e(summaries.tension_summary)}</p>
    {tensions_html}
  </section>

  <section>
    <h2>共识与保护性意图</h2>
    <p class="prose">{e(summaries.consensus_summary)}</p>
    <ul class="consensus">{consensus_html}</ul>
    <ul class="intents">{intents_html}</ul>
  </section>

  {highlights_section}

  <section class="blessing">
    <h2>写在最后</h2>
    <p class="prose">{e(summaries.closing_blessing)}</p>
  </section>

  <footer>
    <p>本报告由「内心议会」于 {generated_at}（UTC）为你生成</p>
    <p>仅供自我反思，不构成任何专业建议。</p>
  </footer>
</main>
</body>
</html>"""
