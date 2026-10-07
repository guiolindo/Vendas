import pytest

from app.domain.extenso import reais


@pytest.mark.parametrize("cents,text", [
    (0, "zero reais"), (1, "um centavo"), (50, "cinquenta centavos"), (100, "um real"), (101, "um real e um centavo"),
    (200, "dois reais"), (1500, "quinze reais"), (2100, "vinte e um reais"), (10000, "cem reais"),
    (10100, "cento e um reais"), (25000, "duzentos e cinquenta reais"), (100000, "mil reais"),
    (110000, "mil e cem reais"), (100100, "mil e um reais"), (123456, "mil duzentos e trinta e quatro reais e cinquenta e seis centavos"),
    (200000, "dois mil reais"), (1234567, "doze mil trezentos e quarenta e cinco reais e sessenta e sete centavos"),
    (10000000, "cem mil reais"), (100000000, "um milhão de reais"), (250000000, "dois milhões e quinhentos mil reais"),
    (100000100, "um milhão e um reais"), (200000000, "dois milhões de reais"), (199999, "mil novecentos e noventa e nove reais e noventa e nove centavos"),
    (1999, "dezenove reais e noventa e nove centavos"), (1_000_001_00, "um milhão e um reais"),
])
def test_extenso(cents, text):
    assert reais(cents) == text


def test_negative_is_rejected():
    with pytest.raises(ValueError):
        reais(-1)
