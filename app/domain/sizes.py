"""Tamanhos que um produto pode ter. A ordem daqui é a ordem em que aparecem na tela."""
from __future__ import annotations

SIZES = ("P", "M", "G", "GG")


def clean_sizes(values) -> list[str]:
    """Só tamanhos conhecidos, sem repetir, na ordem de SIZES. Qualquer outro valor é erro de quem chamou."""
    wanted = {str(v).strip().upper() for v in (values or []) if str(v).strip()}
    unknown = wanted - set(SIZES)
    if unknown:
        raise ValueError(", ".join(sorted(unknown)))
    return [s for s in SIZES if s in wanted]
