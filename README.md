# Vendas

Sistema de vendas e controle financeiro: produtos, estoque, vendas, clientes, pagamentos (inclusive parciais), vencimentos, cobranças e relatórios. É **uma única aplicação** em Python (Flask + Jinja2 + SQLAlchemy), com PostgreSQL em produção. A interface é mobile-first: no celular tem navegação inferior, listas em cartões e uma tela de venda própria para o polegar.

## Rodar no computador

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py                                      # http://127.0.0.1:5000
```

Sem `DATABASE_URL`, usa um SQLite em `instance/vendas.db` (só para desenvolvimento). No primeiro acesso o sistema pede para criar o usuário. Para conhecer com dados de exemplo: `flask --app run demo-data`.

## Publicar no Railway (PostgreSQL)

1. Crie um projeto no Railway a partir deste repositório e adicione o plugin **PostgreSQL**.
2. No serviço do app, em *Variables*, defina:
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (o app aceita o formato `postgres://…` do Railway)
   - `VENDAS_SECRET_KEY` = uma chave longa e aleatória (`python -c "import secrets; print(secrets.token_hex(32))"`).
     **Obrigatória em produção:** o disco do Railway é efêmero, e sem ela as sessões caem a cada deploy.
3. O `railway.json` já define o comando de início (gunicorn) e o healthcheck em `/saude`.
4. Abra a URL do app: o primeiro acesso cria o administrador (`/configurar`). Depois disso essa tela deixa de existir.

As tabelas são criadas automaticamente na primeira subida (com lock, então vários workers não colidem).

## Testes

```bash
pytest                                                     # SQLite
TEST_DATABASE_URL=postgresql://usuario@host/banco pytest   # PostgreSQL (apaga as tabelas desse banco!)
```

Cobrem total, desconto, estoque, pagamento integral/parcial/múltiplo, saldo, vencimento, status, cancelamento, edição, integridade (chaves, constraints), concorrência (threads disputando o mesmo saldo e o mesmo estoque) e as telas.

## Arquitetura

```
app/
  domain/        regras puras, sem banco: dinheiro (centavos), status da venda, formas de pagamento
  models/        tabelas (SQLAlchemy)
  services/      operações de negócio, cada uma em uma transação: vendas, pagamentos, estoque,
                 produtos, clientes + dashboard e relatórios
  repositories/  consultas de leitura (listas, filtros, saldos por cliente)
  schemas/       texto de formulário/JSON -> objetos dos serviços (com mensagens humanas)
  web/           rotas (blueprints), autenticação/CSRF, filtros dos templates
  templates/ static/   interface (Jinja + CSS + JS puro, sem etapa de build)
tests/
```

A tela só apresenta e facilita; **toda regra está em `services/` e `domain/`**.

## Regras que o sistema garante

- **Dinheiro é inteiro em centavos**, nunca `float`.
- **Status nunca é digitado**: é calculado de total, pagamentos válidos e vencimento (`pago` › `vencido` › `parcial` › `pendente`; `cancelada` à parte). Vence hoje ainda não é vencido. Parcial atrasado é *vencido*. Quitado nunca é vencido. Os filtros em SQL e a função em Python são testados para sempre concordarem.
- **Pagamentos só se acrescentam.** Para corrigir, *estorna-se* (o registro fica no histórico, marcado). Não passa do valor restante, não é futuro, não é anterior à venda.
- **Venda é tudo-ou-nada**: venda + itens + baixa de estoque + pagamento inicial na mesma transação. Estoque nunca fica negativo (atualização condicional, segura com vendas simultâneas). Envio duplicado (duplo clique) devolve a mesma venda.
- **O histórico não muda quando o cadastro muda**: o item da venda guarda nome, preço e custo da época.
- **Cancelar** devolve o estoque e estorna (sem apagar) os pagamentos. Vendas e clientes com histórico não são excluídos, só desativados.
- **Receber do cliente** (sem escolher venda) distribui o valor da venda mais antiga para a mais nova.
- Concorrência: SQLite usa `BEGIN IMMEDIATE`; PostgreSQL usa `SELECT … FOR UPDATE` nas vendas/pagamentos alterados.

## Decisões e limites conhecidos

- Cada venda tem **um** vencimento (sem parcelamento em várias datas). O extrato do cliente mostra *vendas* em aberto/vencidas.
- Quantidades são inteiras (a unidade — un, cx, kg… — é só um rótulo). Valores vão até R$ 20.000.000,00 por campo.
- Há um único nível de acesso (todo usuário logado pode tudo). Usuários extras: `flask --app run create-user NOME`.
- O bloqueio de tentativas de login é em memória por processo (basta contra tentativa simples; para mais, use um proxy/WAF).
- O esquema é criado por `create_all`. Ao evoluir as tabelas em produção, adote o Alembic.
- Itens e valores de uma venda não são editáveis (corrigir = cancelar e registrar de novo); cliente, vencimento e observações são.
- Atalhos no computador: `N` nova venda, `/` buscar, `Enter` adiciona produto, `Ctrl+Enter` conclui a venda.
