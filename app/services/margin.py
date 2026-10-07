"""Margem de lucro: quanto a pessoa recebeu pelo produto menos quanto ele custou pra ela.

Regras:
  * O desconto da venda é rateado entre os itens na proporção do valor de cada um; a margem de um
    item é o valor líquido dele (já com desconto) menos quantidade × custo.
  * Item cujo produto está SEM CUSTO informado (custo 0) fica fora da conta: contar o custo como zero
    inflaria o lucro em 100%. Quantos ficaram de fora é informado para a tela avisar.
  * Tudo em centavos inteiros e aditivo: a margem do geral é exatamente a soma das margens das pessoas.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..domain.allocation import allocate
from ..models import Sale, SaleItem


@dataclass
class Margin:
    revenue: int = 0       # valor líquido dos itens COM custo informado
    cost: int = 0
    uncosted: int = 0      # itens sem custo, fora da conta

    @property
    def amount(self) -> int:
        return self.revenue - self.cost

    @property
    def percent(self) -> float | None:
        """Margem sobre o preço de venda (lucro ÷ vendido), em %. None quando não há base."""
        return round(self.amount * 100 / self.revenue, 1) if self.revenue > 0 else None

    def add(self, other: "Margin") -> None:
        self.revenue += other.revenue
        self.cost += other.cost
        self.uncosted += other.uncosted


def item_nets(sale: Sale) -> dict[int, int]:
    """Valor líquido de cada item (id do item -> centavos): total da venda rateado pelos itens."""
    return allocate(sale.total_cents, {i.id: i.total_cents for i in sale.items})


def item_margin(item: SaleItem, net: int) -> Margin:
    if item.unit_cost_cents <= 0:
        return Margin(uncosted=1)
    return Margin(revenue=net, cost=item.quantity * item.unit_cost_cents)


def sale_margin(sale: Sale) -> Margin:
    nets = item_nets(sale)
    total = Margin()
    for item in sale.items:
        total.add(item_margin(item, nets[item.id]))
    return total


def margins_by_owner(sale: Sale) -> dict[int | None, Margin]:
    nets = item_nets(sale)
    out: dict[int | None, Margin] = {}
    for item in sale.items:
        out.setdefault(item.owner_id, Margin()).add(item_margin(item, nets[item.id]))
    return out
