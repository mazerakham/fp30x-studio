import markdown, pathlib, re, html

DOCS = pathlib.Path("/Users/jake/workspace/audio/fp30x-studio/docs")
md = markdown.Markdown(extensions=[
    "extra", "toc", "sane_lists", "admonition",
    "pymdownx.superfences", "pymdownx.tilde",
], extension_configs={"toc": {"toc_depth": "2-3"}})

def render(path, prefix):
    md.reset()
    src = path.read_text()
    # keep mermaid blocks as plain, labelled pre — no CDN on a loopback server
    src = re.sub(r"```mermaid\n(.*?)```",
                 lambda m: "```\n[diagram — mermaid source]\n" + m.group(1) + "```",
                 src, flags=re.S)
    body = md.convert(src)
    # namespace the anchors so two docs can share one page
    body = re.sub(r'id="([^"]+)"', lambda m: f'id="{prefix}-{m.group(1)}"', body)
    body = re.sub(r'href="#([^"]+)"', lambda m: f'href="#{prefix}-{m.group(1)}"', body)
    toc = re.sub(r'href="#([^"]+)"', lambda m: f'href="#{prefix}-{m.group(1)}"', md.toc)
    return body, toc

prod_body, prod_toc = render(DOCS / "DESIGN-product.md", "p")
arch_body, arch_toc = render(DOCS / "DESIGN-architecture.md", "a")

CSS = """
:root{--bg:#0f1311;--panel:#161d1a;--fg:#e9efec;--dim:#8fa39a;--line:#232f2a;
--acc:#e0b64a;--cold:#4f8fe0;--hot:#e0563b;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
 font:16.5px/1.68 Charter,"Iowan Old Style",Georgia,serif}
#wrap{display:grid;grid-template-columns:280px minmax(0,1fr);gap:0;min-height:100vh}
nav{position:sticky;top:0;align-self:start;height:100vh;overflow-y:auto;
 border-right:1px solid var(--line);padding:1.4rem 1rem 3rem;background:var(--panel)}
nav h2{font-size:.74rem;letter-spacing:.1em;text-transform:uppercase;color:var(--dim);
 margin:1.6rem 0 .4rem;font-weight:600}
nav ul{list-style:none;margin:0;padding:0 0 0 .2rem;font-size:.86rem;line-height:1.45}
nav ul ul{padding-left:.75rem;border-left:1px solid var(--line);margin:.15rem 0 .35rem}
nav li{margin:.16rem 0}
nav a{color:var(--dim);text-decoration:none;display:block;padding:.1rem 0}
nav a:hover{color:var(--fg)}
main{padding:2.6rem 2.4rem 8rem;max-width:60rem;min-width:0}
h1{font-size:2.1rem;line-height:1.15;letter-spacing:-.02em;margin:0 0 .3rem}
h2{font-size:1.42rem;margin:2.9rem 0 .5rem;letter-spacing:-.01em;
 padding-top:.6rem;border-top:1px solid var(--line);scroll-margin-top:1rem}
h3{font-size:1.08rem;margin:1.9rem 0 .35rem;color:var(--acc);scroll-margin-top:1rem}
h4{font-size:.98rem;margin:1.3rem 0 .3rem;color:var(--dim)}
p,li{margin:.6rem 0}
a{color:var(--cold)}
code{background:#1d2723;padding:.11rem .34rem;border-radius:3px;
 font-family:ui-monospace,"SF Mono",Menlo,monospace;font-size:.85em}
pre{background:#1d2723;border:1px solid var(--line);border-radius:6px;padding:.85rem 1rem;
 overflow-x:auto;font-size:.82rem;line-height:1.5}
pre code{background:none;padding:0;font-size:1em}
blockquote{border-left:3px solid var(--acc);background:var(--panel);margin:1.2rem 0;
 padding:.7rem 1.1rem;border-radius:0 6px 6px 0;color:#dfe7e3}
blockquote p:first-child{margin-top:0}blockquote p:last-child{margin-bottom:0}
table{border-collapse:collapse;width:100%;margin:1rem 0;font-size:.92rem;display:block;
 overflow-x:auto}
th,td{text-align:left;padding:.42rem .7rem;border-bottom:1px solid var(--line);
 vertical-align:top}
th{color:var(--dim);font-size:.78rem;text-transform:uppercase;letter-spacing:.06em}
hr{border:0;border-top:1px solid var(--line);margin:2.4rem 0}
.doc{padding-top:.5rem}
.docmark{display:inline-block;font-size:.72rem;letter-spacing:.11em;text-transform:uppercase;
 color:#0f1311;background:var(--acc);padding:.15rem .55rem;border-radius:3px;margin-bottom:.9rem}
.docmark.b{background:var(--cold);color:#0f1311}
.tldr{border-left:3px solid var(--acc);background:var(--panel);padding:1.1rem 1.3rem;
 margin:1.6rem 0;border-radius:0 6px 6px 0}
.tldr p:first-child{margin-top:0}.tldr p:last-child{margin-bottom:0}
.k{color:var(--hot);font-weight:600}
@media(max-width:900px){#wrap{grid-template-columns:1fr}nav{position:static;height:auto}}
"""

HTML = f"""<!doctype html>
<meta charset="utf-8">
<title>FP-30X Studio — the desktop app, designed</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{CSS}</style>
<div id="wrap">
<nav>
<h2>Read this first</h2>
<ul><li><a href="#top">Where this came from</a></li>
<li><a href="#act">Three things to act on</a></li></ul>
<h2>Product design</h2>
{prod_toc}
<h2>Architecture</h2>
{arch_toc}
</nav>
<main>

<h1 id="top">FP-30X Studio, as a desktop app</h1>
<p style="color:var(--dim);margin:0 0 1.4rem">Two independent design passes, 2026-08-22.
Both were derived from the record — 83 Claude Code transcripts on this machine, the
<code>fp30x-recording</code> skill, <code>PROJECT_LOG.md</code>, the takes directory and the
Linear board — not invented. Committed as <code>87da9ad</code> and <code>ab1c299</code>.</p>

<div class="tldr">
<p><strong>The brief.</strong> A native macOS app with the same features as the Python
toolkit, that survives sleep, and that you operate yourself with no LLM in the loop.</p>
<p><strong>The architecture recommendation:</strong> SwiftUI shell, the existing C capture as a
supervised child process, the existing Python stack as cancellable subprocesses. Rewrite
nothing that works. Verified today that SwiftUI/CoreMIDI/AVFAudio build with the Command Line
Tools SDK — no Xcode required.</p>
<p><strong>The product finding:</strong> every indicator the current tool shows is about the
software, not about the take. The connect line, the note feed, the packet counter and the live
process have each reported healthy while nothing was being recorded.</p>
</div>

<h2 id="act">Three things the designs turned up that are already true</h2>
<p><span class="k">1. No inactivity timeout can be honest on this piano.</span> The FP-30X sends
no Active Sensing over BLE-MIDI — a byte census of tonight's 27-minute take is
<code>0x80</code>, <code>0x90</code> and <code>0xB0</code> and nothing else — so silence and a
dead radio are the same on the wire. The longest genuine silent gap inside that known-good take
is <strong>147.6 s</strong>, past <code>watch.py</code>'s 120 s red threshold. The existing alarm
would have fired inside a perfect performance.</p>
<p><span class="k">2. The green light I wrote this afternoon could lie.</span> It watched file
size, and a <code>.fp30</code> grows by nine comment lines at open and one more on a reconnect —
so it flipped GREEN with zero MIDI received. Reproduced and fixed: it counts note-ons now.
Verified against tonight's take, 6037.</p>
<p><span class="k">3. The reconnect fix existed but had never been committed or rebuilt.</span>
<code>MIDINotifyProc</code> on <code>kMIDIMsgSetupChanged</code> plus the app holding its own
<code>IOPMAssertion</code> — written, sitting dirty in the tree, and <code>make</code> was
reporting nothing to do while the built binary predated it. Committed as <code>ab1c299</code>.
The venv was also missing <code>soundfile</code> and <code>scipy</code>, which is why ten tests
failed and why tonight's gain probe silently returned an empty string. <strong>319 tests pass
now.</strong></p>

<hr>
<div class="doc"><span class="docmark">Product design</span>
{prod_body}
</div>
<hr>
<div class="doc"><span class="docmark b">Architecture</span>
{arch_body}
</div>
</main>
</div>
"""
out = pathlib.Path("/Users/jake/workspace/audio/fp30x-studio/docs/design-review.html")
out.write_text(HTML)
print(out, f"{len(HTML)/1024:.0f} KB")
