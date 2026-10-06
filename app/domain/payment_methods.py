METHODS = {
    "dinheiro": "Dinheiro",
    "pix": "Pix",
    "cartao_debito": "Cartão de débito",
    "cartao_credito": "Cartão de crédito",
    "transferencia": "Transferência",
    "boleto": "Boleto",
    "outro": "Outro",
}
DEFAULT_METHOD = "dinheiro"


def label(code: str) -> str:
    return METHODS.get(code, code)
