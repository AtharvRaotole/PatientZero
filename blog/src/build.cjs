const fs=require('fs');
const {marked}=require('/home/box/.npm/_npx/668c188756b835f3/node_modules/marked');
const src=fs.readFileSync('/workspace/falkor-hack/blog/post.md','utf8');
const m=src.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/);
const fm=Object.fromEntries(m[1].split('\n').map(l=>{const i=l.indexOf(':');return [l.slice(0,i).trim(),l.slice(i+1).trim().replace(/^"|"$/g,'')]}));
let body=m[2].replaceAll('https://raw.githubusercontent.com/AtharvRaotole/PatientZero/main/blog/images/','images/');
body=body.replace(/\n#FalkorDB #GraphDB #AI #hackathon\s*$/,'\n');
const KW=/\b(MATCH|OPTIONAL|WHERE|WITH|RETURN|ORDER BY|DISTINCT|AND|OR|IN|AS|CREATE|MERGE|SET)\b/g;
const esc=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const renderer=new marked.Renderer();
renderer.code=function(tok){const code=typeof tok==='object'?tok.text:tok;const lang=typeof tok==='object'?tok.lang:arguments[1];
  let h=esc(code).split('\n').map(line=>{const c=line.indexOf('//');let a=c>=0?line.slice(0,c):line,b=c>=0?line.slice(c):'';
    a=a.replace(/('[^']*')/g,'<span class="s">$1</span>').replace(KW,'<span class="k">$1</span>').replace(/(\$[a-z_]+)/g,'<span class="p">$1</span>');
    return a+(b?'<span class="c">'+b+'</span>':'');}).join('\n');
  return `<pre><code class="lang-${lang||''}">${h}</code></pre>`;};
marked.use({renderer});
let html=marked.parse(body);
// image + following italic caption -> figure
html=html.replace(/<p><img src="([^"]+)" alt="([^"]*)">\s*<em>([\s\S]*?)<\/em><\/p>/g,'<figure><img src="$1" alt="$2"><figcaption>$3</figcaption></figure>');
const tags=fm.tags.split(',').map(t=>'<span>#'+t.trim()+'</span>').join(' ');
const page=`<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${fm.title}</title><meta name="description" content="${fm.description}">
<style>
:root{--ink:#1b1a18;--muted:#6d695f;--rule:#e6e2d9;--red:#c8322f;--paper:#fffefb}
*{box-sizing:border-box}
html{background:var(--paper)}
body{margin:0;color:var(--ink);font:19px/1.68 "Source Serif Pro","Source Serif 4",Charter,Georgia,serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:680px;margin:0 auto;padding:56px 20px 96px}
.cover{display:block;width:100%;max-width:1000px;margin:0 auto;border-radius:0}
header{margin:44px 0 36px}
.kicker{font:600 12px/1 "Inter Tight",Inter,-apple-system,"Segoe UI",sans-serif;letter-spacing:1.4px;text-transform:uppercase;color:var(--muted)}
h1{font:700 40px/1.14 "Inter Tight",Inter,-apple-system,"Segoe UI",sans-serif;letter-spacing:-.8px;margin:14px 0 16px}
.dek{font-size:20px;color:#4a4740;margin:0 0 18px}
.byline{font:500 14px/1.4 "Inter Tight",Inter,sans-serif;color:var(--muted)}
.byline b{color:var(--ink)}
h2{font:700 27px/1.25 "Inter Tight",Inter,-apple-system,"Segoe UI",sans-serif;letter-spacing:-.4px;margin:56px 0 14px}
h3{font:700 21px/1.3 "Inter Tight",Inter,sans-serif;margin:36px 0 10px}
p{margin:0 0 20px}
a{color:var(--ink);text-decoration-color:var(--red);text-underline-offset:3px}
strong{font-weight:700}
ul,ol{padding-left:24px;margin:0 0 22px}li{margin:0 0 10px}
blockquote{margin:28px 0;padding:20px 24px;background:#f6f4ee;border-left:3px solid var(--ink);font-size:17.5px;line-height:1.6}
blockquote p{margin:0 0 10px}blockquote ul{margin:8px 0 0}blockquote li{margin:0 0 8px}
code{font:500 .8em/1 "IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;background:#f2efe8;padding:.12em .36em;border-radius:4px}
pre{background:#141413;color:#e9e6df;padding:20px 22px;border-radius:8px;overflow-x:auto;margin:26px 0;font-size:14px;line-height:1.6}
pre code{background:none;padding:0;font:500 14px/1.6 "IBM Plex Mono",ui-monospace,Menlo,monospace;color:inherit;white-space:pre}
pre .k{color:#f0a37f;font-weight:600}pre .p{color:#9ccfd8}pre .s{color:#c9d89c}pre .c{color:#7d796f;font-style:italic}
figure{margin:34px -40px}figure img{display:block;width:100%;border:1px solid var(--rule);border-radius:8px;background:#fff}
figcaption{font:400 14.5px/1.5 "Inter Tight",Inter,sans-serif;color:var(--muted);margin:10px 40px 0}
figcaption code{font-size:.85em}
table{width:100%;border-collapse:collapse;margin:24px 0 28px;font:400 15px/1.45 "Inter Tight",Inter,sans-serif}
th,td{text-align:left;vertical-align:top;padding:10px 10px 10px 0;border-bottom:1px solid var(--rule)}
th{font-weight:700;border-bottom:1.5px solid var(--ink)}
hr{border:0;border-top:1px solid var(--rule);margin:40px 0}
.tags{margin-top:36px;font:600 14px "Inter Tight",Inter,sans-serif;color:var(--muted)}.tags span{margin-right:10px}
@media (max-width:760px){figure{margin:28px 0}figcaption{margin:10px 0 0}h1{font-size:32px}}
@media print{body{font-size:12pt}.wrap{padding-top:0}figure{break-inside:avoid;margin:24px 0}figcaption{margin:8px 0 0}pre,table,blockquote{break-inside:avoid}h2{break-after:avoid}a{text-decoration:none}}
</style></head><body>
<img class="cover" src="images/cover@2x.png" alt="One phished maintainer. 13 of 22 services exposed.">
<div class="wrap">
<header><div class="kicker">Graph Hacks: Context for AI Agents &nbsp;/&nbsp; FalkorDB</div>
<h1>${fm.title}</h1><p class="dek">${fm.description}</p>
<div class="byline"><b>Atharv Raotole</b> &nbsp;/&nbsp; October 2026</div></header>
${html}
<div class="tags">${tags}</div>
</div></body></html>`;
fs.writeFileSync('/workspace/falkor-hack/blog/post.html',page);
console.log('ok', html.length);
