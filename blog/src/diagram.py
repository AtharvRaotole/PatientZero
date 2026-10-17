W,H=1400,900
BW,BH=170,46
N={ # name:(cx,cy)
 'Playbook':(150,130),'Incident':(480,250),'Finding':(770,250),'AgentRun':(770,130),
 'Session':(1060,130),'Turn':(1060,250),'WatchEvent':(1270,250),
 'Package':(150,500),'Maintainer':(480,500),'Version':(150,680),'Advisory':(150,800),
 'Person':(1180,500),'Team':(1180,680),'Service':(880,680)}
RED='#c8322f'; GREY='#55524c'
def edge_pt(a,b):
    (ax,ay),(bx,by)=N[a],N[b]
    import math
    dx,dy=bx-ax,by-ay
    def clip(cx,cy,dx,dy):
        tx=(BW/2)/abs(dx) if dx else 1e9; ty=(BH/2)/abs(dy) if dy else 1e9
        t=min(tx,ty); return cx+dx*t, cy+dy*t
    p1=clip(ax,ay,dx,dy); p2=clip(bx,by,-dx,-dy)
    return p1,p2
out=[]
def panel(x,y,w,h,title,sub):
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="#f6f5f1" stroke="#dcd8cf"/>')
    out.append(f'<text x="{x+18}" y="{y+28}" class="pt">{title}</text><text x="{x+18}" y="{y+47}" class="ps">{sub}</text>')
panel(20,30,1360,300,'MEMORY AND COORDINATION','written by the agents, one graph per tenant')
panel(20,380,560,480,'NPM SUPPLY CHAIN','real data: OSV, deps.dev, npm registry')
panel(640,380,740,480,'YOUR ORG','synthetic fleet: 22 services, 8 teams')
def e(a,b,label,color=GREY,lx=0,ly=0,anchor='middle'):
    (x1,y1),(x2,y2)=edge_pt(a,b)
    m='r' if color==RED else 'g'
    out.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{color}" stroke-width="{2.2 if color==RED else 1.5}" marker-end="url(#{m})"/>')
    mx,my=(x1+x2)/2+lx,(y1+y2)/2+ly
    cls='elr' if color==RED else 'el'
    out.append(f'<text x="{mx:.1f}" y="{my:.1f}" text-anchor="{anchor}" class="{cls}" paint-order="stroke" stroke="#f6f5f1" stroke-width="6">{label}</text>')
e('Playbook','Incident','APPROVED_FROM',lx=-18,ly=-10)
e('Playbook','Package','COVERS {pin}',lx=12,anchor='start')
e('Incident','Finding','HAS_FINDING',ly=-8)
e('AgentRun','Finding','PRODUCED',lx=10,anchor='start')
e('Session','Turn','HAS_TURN',lx=10,anchor='start')
e('Incident','Maintainer','ABOUT',lx=10,ly=-40,anchor='start')
e('Maintainer','Package','HAD_PUBLISH_RIGHTS',ly=-10)
e('Package','Version','HAS_VERSION',lx=10,anchor='start')
e('Advisory','Version','AFFECTS',lx=10,anchor='start')
e('Service','Version','DEPENDS_ON {requirement}',RED,ly=-10)
e('Incident','Service','EXPOSED {path}',RED,lx=14,ly=4,anchor='start')
e('Turn','Service','ABOUT',lx=-10,ly=0,anchor='end')
e('WatchEvent','Service','HITS',lx=10,ly=0,anchor='start')
e('Person','Team','ONCALL_FOR, MEMBER_OF',lx=10,anchor='start')
e('Team','Service','OWNS',ly=-10)
# self loops
vx,vy=N['Version']; x0=vx-BW/2
out.append(f'<path d="M{x0},{vy-12} C{x0-45},{vy-36} {x0-45},{vy+36} {x0},{vy+12}" fill="none" stroke="{RED}" stroke-width="2.2" marker-end="url(#r)"/>')
out.append(f'<text x="{x0+4}" y="{vy-34}" text-anchor="start" class="elr" paint-order="stroke" stroke="#f6f5f1" stroke-width="6">REQUIRES {{requirement}}</text>')
ax,ay=N['AgentRun']; x1=ax+BW/2
out.append(f'<path d="M{x1},{ay-12} C{x1+70},{ay-40} {x1+70},{ay+40} {x1},{ay+12}" fill="none" stroke="{GREY}" stroke-width="1.5" marker-end="url(#g)"/>')
out.append(f'<text x="{x1-30}" y="{ay-40}" text-anchor="start" class="el" paint-order="stroke" stroke="#f6f5f1" stroke-width="6">HANDED_OFF_TO {{reason}}</text>')
for n,(x,y) in N.items():
    out.append(f'<rect x="{x-BW/2}" y="{y-BH/2}" width="{BW}" height="{BH}" rx="7" fill="#fff" stroke="#1b1a18" stroke-width="1.4"/><text x="{x}" y="{y+5.5}" text-anchor="middle" class="nl">{n}</text>')
svg=f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
<defs><marker id="g" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{GREY}"/></marker>
<marker id="r" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{RED}"/></marker></defs>
<style>.nl{{font:600 16px "Inter Tight",sans-serif;fill:#111}}.el{{font:500 12.5px "IBM Plex Mono",monospace;fill:#55524c}}.elr{{font:600 12.5px "IBM Plex Mono",monospace;fill:{RED}}}.pt{{font:700 13px "Inter Tight",sans-serif;letter-spacing:1.4px;fill:#1b1a18}}.ps{{font:400 13px "Inter Tight",sans-serif;fill:#7a766d}}</style>
<rect width="100%" height="100%" fill="#fff"/>
{''.join(out)}
<text x="1380" y="888" text-anchor="end" class="ps">red: the exposure traversal (:Service)-[:DEPENDS_ON|REQUIRES*1..6]-&gt;(:Version) and the EXPOSED edges it writes back</text>
</svg>'''
open('diagram.html','w').write(f'<html><body style="margin:0">{svg}</body></html>')
