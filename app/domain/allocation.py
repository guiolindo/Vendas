"""Divisão exata de centavos: nada some, nada aparece."""
from __future__ import annotations


def allocate(total: int, weights: dict) -> dict:
    """Reparte `total` centavos conforme os pesos, sem perder nem criar centavos.
    O resto da divisão vai para quem tem o maior peso (empate: menor chave)."""
    wsum = sum(weights.values())
    if wsum <= 0 or total <= 0:
        return {k: 0 for k in weights}
    out = {k: total * w // wsum for k, w in weights.items()}
    leftover = total - sum(out.values())
    if leftover:
        top = sorted(weights, key=lambda k: (-weights[k], -1 if k is None else k))[0]
        out[top] += leftover
    return out
