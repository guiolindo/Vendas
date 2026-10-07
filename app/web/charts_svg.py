"""Gráficos em SVG gerados no servidor: sem biblioteca, sem script inline (CSP), imprimem bem.

Especificação (método de visualização): barras finas (≤ 24px) com topo arredondado e base reta; linhas de 2px;
vão de 2px na cor da superfície entre segmentos; grade em fio, recessiva; valores só nos pontos que importam,
o resto vai na dica e na tabela. Toda cor vem de variáveis CSS (validadas contra a superfície do sistema).
"""
from __future__ import annotations

import json
from math import ceil, floor, log10

from markupsafe import Markup, escape

from ..domain.money import format_brl
from ..services.charts import AGING, ChartData

W = 380
OTHER = "var(--c-other)"


def _tip(title: str, rows: list[tuple[str, str, str]]) -> str:
    """Conteúdo da dica, como JSON em atributo (o JS monta o balão com textContent)."""
    return escape(json.dumps({"t": title, "r": [list(r) for r in rows]}, ensure_ascii=False))


def nice_scale(vmax: int, ticks: int = 4) -> tuple[int, int]:
    """(máximo, passo) redondos para o eixo: 0 / 500 / 1.000 / 1.500 ..."""
    if vmax <= 0:
        return 100_00, 25_00
    raw = max(vmax / ticks, 100)          # os rótulos do eixo são em reais inteiros: passo mínimo de R$ 1
    mag = 10 ** floor(log10(raw))
    step = int(next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw))
    top = int(ceil(vmax / step) * step)
    return top, step


def tick_label(cents: int) -> str:
    reais = cents // 100
    return "R$ " + f"{reais:,}".replace(",", ".")


def _bar_path(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    """Barra com topo arredondado e base reta (cresce de uma linha de base única)."""
    r = max(0, min(r, w / 2, h))
    return (f"M{x:.1f},{y + h:.1f} V{y + r:.1f} a{r},{r} 0 0 1 {r},{-r} H{x + w - r:.1f} a{r},{r} 0 0 1 {r},{r} V{y + h:.1f} Z")


def _hbar_path(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    """Barra horizontal: ponta arredondada, base (esquerda) reta."""
    r = max(0, min(r, h / 2, w))
    return f"M{x:.1f},{y:.1f} H{x + w - r:.1f} a{r},{r} 0 0 1 {r},{r} V{y + h - r:.1f} a{r},{r} 0 0 1 {-r},{r} H{x:.1f} Z"


def _svg(h: float, title: str, desc: str, body: str) -> Markup:
    return Markup(f'<svg class="chart-svg" viewBox="0 0 {W} {h:.0f}" role="img" aria-label="{escape(title)}. {escape(desc)}" '
                  f'preserveAspectRatio="xMidYMid meet"><title>{escape(title)}</title><desc>{escape(desc)}</desc>{body}</svg>')


def _grid(top: int, step: int, left: float, plot_h: float, t: float, right: float) -> str:
    out = []
    for k in range(0, top // step + 1):
        v = k * step
        y = t + plot_h - v / top * plot_h
        out.append(f'<line class="ch-grid{" base" if k == 0 else ""}" x1="{left}" x2="{W - right}" y1="{y:.1f}" y2="{y:.1f}"/>'
                   f'<text class="ch-axis" x="{left - 6}" y="{y + 3.5:.1f}" text-anchor="end">{tick_label(v)}</text>')
    return "".join(out)


def _xlabels(buckets, slot: float, left: float, base_y: float) -> str:
    n = len(buckets)
    step = max(1, ceil(n / 6))
    out = []
    for i in range(0, n, step):
        out.append(f'<text class="ch-axis" x="{left + slot * (i + .5):.1f}" y="{base_y + 15:.1f}" text-anchor="middle">{escape(buckets[i].label)}</text>')
    return "".join(out)


def sold_received(data: ChartData) -> Markup:
    """Vendido (colunas cinza, contexto) e Recebido (linha verde) no MESMO eixo."""
    n = len(data.buckets)
    left, right, top, plot_h, bottom = 50.0, 8.0, 10.0, 170.0, 24.0
    vmax = max(max(data.sold), max(data.received), 1)
    scale_top, step = nice_scale(vmax)
    slot = (W - left - right) / n
    bw = min(24.0, slot * 0.62)
    y_of = lambda v: top + plot_h - v / scale_top * plot_h
    body = [_grid(scale_top, step, left, plot_h, top, right)]
    for i, v in enumerate(data.sold):
        if v > 0:
            h = v / scale_top * plot_h
            body.append(f'<path class="ch-mark" fill="var(--c-sold)" d="{_bar_path(left + slot * i + (slot - bw) / 2, top + plot_h - h, bw, h)}"/>')
    pts = [(left + slot * (i + .5), y_of(v)) for i, v in enumerate(data.received)]
    if any(data.received):
        body.append('<path class="ch-line" stroke="var(--c-recv)" fill="none" d="M' + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + '"/>')
        x, y = pts[-1]
        body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="var(--c-recv)" stroke="var(--surface)" stroke-width="2"/>')
    body.append(_xlabels(data.buckets, slot, left, top + plot_h))
    for i, b in enumerate(data.buckets):
        tip = _tip(b.long, [("Vendido", format_brl(data.sold[i]), "var(--c-sold)"), ("Recebido", format_brl(data.received[i]), "var(--c-recv)")])
        body.append(f'<rect class="hit" tabindex="0" x="{left + slot * i:.1f}" y="{top}" width="{slot:.1f}" height="{plot_h}" '
                    f'aria-label="{escape(b.long)}: vendido {format_brl(data.sold[i])}, recebido {format_brl(data.received[i])}" data-tip="{tip}"/>')
    desc = f"Total vendido {format_brl(sum(data.sold))} e recebido {format_brl(sum(data.received))} no período."
    return _svg(top + plot_h + bottom, "Vendido e recebido", desc, "".join(body))


def owner_color(data: ChartData, owner_id) -> str:
    """A cor segue a PESSOA (pela ordem de cadastro), nunca a posição no gráfico."""
    for i, o in enumerate(data.owners):
        if o.id == owner_id:
            return ("var(--c1)", "var(--c2)")[i] if i < 2 else OTHER
    return OTHER


def stacked_by_owner(data: ChartData) -> Markup:
    n = len(data.buckets)
    left, right, top, plot_h, bottom = 50.0, 8.0, 10.0, 170.0, 24.0
    keys = [o.id for o in data.owners[:2]]
    other = [sum(v) for v in zip(*[vals for k, vals in data.sold_by_owner.items() if k not in keys])] if any(k not in keys for k in data.sold_by_owner) else [0] * n
    series = [(next(o.name for o in data.owners if o.id == k), owner_color(data, k), data.sold_by_owner.get(k, [0] * n)) for k in keys]
    if any(other):
        series.append(("Outros", OTHER, other))
    totals = [sum(s[2][i] for s in series) for i in range(n)]
    scale_top, step = nice_scale(max(max(totals), 1))
    slot = (W - left - right) / n
    bw = min(24.0, slot * 0.62)
    body = [_grid(scale_top, step, left, plot_h, top, right)]
    for i in range(n):
        y = top + plot_h
        drawn = [(c, vals[i]) for _, c, vals in series if vals[i] > 0]
        for j, (color, v) in enumerate(drawn):
            h = v / scale_top * plot_h
            seg_h = h - (2 if j < len(drawn) - 1 else 0)          # vão de 2px (cor da superfície) entre segmentos
            if seg_h > 0.5:
                x = left + slot * i + (slot - bw) / 2
                if j == len(drawn) - 1:                              # só o topo da pilha é arredondado
                    body.append(f'<path class="ch-mark" fill="{color}" d="{_bar_path(x, y - seg_h, bw, seg_h)}"/>')
                else:
                    body.append(f'<rect class="ch-mark" fill="{color}" x="{x:.1f}" y="{y - seg_h:.1f}" width="{bw:.1f}" height="{seg_h:.1f}"/>')
            y -= h
    body.append(_xlabels(data.buckets, slot, left, top + plot_h))
    for i, b in enumerate(data.buckets):
        rows = [(name, format_brl(vals[i]), color) for name, color, vals in series]
        tip = _tip(b.long, rows + [("Total", format_brl(totals[i]), "transparent")])
        body.append(f'<rect class="hit" tabindex="0" x="{left + slot * i:.1f}" y="{top}" width="{slot:.1f}" height="{plot_h}" '
                    f'aria-label="{escape(b.long)}: ' + escape("; ".join(f"{r[0]} {r[1]}" for r in rows)) + f'" data-tip="{tip}"/>')
    return _svg(top + plot_h + bottom, "Vendido por pessoa", f"Total vendido {format_brl(sum(totals))}.", "".join(body))


def hbars(items: list[tuple[str, int, str, str]], title: str, desc: str, value_fmt=format_brl) -> Markup:
    """Barras horizontais. item = (rótulo, valor, cor, texto_extra). O valor fica na ponta, fora da barra."""
    if not items:
        return Markup("")
    row_h, label_w, val_w, left = 40.0, 0.0, 124.0, 0.0
    vmax = max(max(v for _, v, _, _ in items), 1)
    track = W - left - val_w
    body = []
    for i, (label, value, color, extra) in enumerate(items):
        y = i * row_h
        w = max(value / vmax * track, 0) if value > 0 else 0
        text = label if len(label) <= 34 else label[:33] + "…"
        body.append(f'<text class="ch-label" x="0" y="{y + 13:.1f}">{escape(text)}</text>')
        if w > 0:
            body.append(f'<path class="ch-mark" fill="{color}" d="{_hbar_path(left, y + 19, w, 14)}"/>')
        else:
            body.append(f'<line class="ch-grid base" x1="{left}" x2="{left + 1}" y1="{y + 19}" y2="{y + 33}"/>')
        body.append(f'<text class="ch-value" x="{left + w + 8:.1f}" y="{y + 30:.1f}">{escape(value_fmt(value))}{(" · " + escape(extra)) if extra else ""}</text>')
        tip = _tip(label, [(extra or "Valor", value_fmt(value), color)])
        body.append(f'<rect class="hit" tabindex="0" x="0" y="{y:.1f}" width="{W}" height="{row_h:.1f}" aria-label="{escape(label)}: {escape(value_fmt(value))}" data-tip="{tip}"/>')
    return _svg(len(items) * row_h, title, desc, "".join(body))


def aging_chart(data: ChartData) -> Markup:
    colors = ["var(--c-sold)", "var(--age1)", "var(--age2)", "var(--age3)"]
    items = [(name, v, colors[i], "") for i, (name, v) in enumerate(zip(AGING, data.aging))]
    return hbars(items, "A receber por prazo", f"Total a receber {format_brl(sum(data.aging))}.")


def margin_chart(data: ChartData) -> Markup:
    items = []
    for p in data.margin_products:
        color = owner_color(data, p.owner_id) if not data.chosen else owner_color(data, data.chosen.id)
        pct = f"{p.percent:.1f}".replace(".", ",") + "%" if p.percent is not None else ""
        items.append((p.name, p.amount, color, pct))
    return hbars(items, "Margem por produto", "Os produtos que mais deram lucro no período.")


def methods_chart(data: ChartData) -> Markup:
    items = [(name, v, "var(--c-recv)", "") for name, v in data.methods]
    return hbars(items, "Recebido por forma de pagamento", f"Total recebido {format_brl(sum(v for _, v in data.methods))}.")
