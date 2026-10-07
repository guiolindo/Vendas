# Vendas

Sistema de vendas e controle financeiro: produtos, estoque, vendas, clientes, pagamentos (inclusive parciais), vencimentos, cobranças e relatórios. É **uma única aplicação** em Python (Flask + Jinja2 + SQLAlchemy), com PostgreSQL em produção. O negócio é tocado por **duas pessoas**: cada produto pertence a uma delas, e há um painel geral e um painel simples de cada pessoa. A interface é mobile-first: no celular tem navegação inferior, listas em cartões e uma tela de venda própria para o polegar.

## O que o sistema faz

- **Vendas** com pagamento total, parcial ou a prazo, estoque, clientes (extrato), cobranças por vencimento.
- **Margem de lucro**: a pessoa informa quanto o produto custou e por quanto vendeu; o sistema mostra a margem em R$ e em % por produto, venda, pessoa e no mês (ao cadastrar o produto e na própria tela de venda, ao vivo).
- **Duas pessoas**: cada produto tem dono; painel geral e painel simples de cada uma.
- **Gráficos** (`/graficos`): vendido × recebido, vendido por pessoa, margem por produto, a receber por prazo e recebido por forma de pagamento. Cada gráfico tem tabela equivalente e dica ao tocar.
- **Excel de verdade** (`.xlsx`) de todos os relatórios, mais uma planilha única do mês com uma aba por relatório; CSV continua disponível.
- **Comprovante impresso** da venda (folha A4 ou bobina térmica de 80 mm) e **recibo** de cada pagamento, com valor por extenso. Os dados do negócio ficam em **Configurações**.

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
   - `VENDAS_SETUP_TOKEN` = um código só seu (ex.: 20+ caracteres aleatórios). Sem ele, o cadastro do primeiro usuário fica **trancado**: a URL do Railway é pública, e sem esse código quem chegasse primeiro viraria o dono do sistema.

   Em produção o app **se recusa a iniciar** sem `VENDAS_SECRET_KEY` e sem `DATABASE_URL` (o disco do Railway é efêmero; sem chave fixa as sessões cairiam a cada deploy).
3. O `railway.json` já define o comando de início (gunicorn) e o healthcheck em `/saude`.
4. Abra a URL do app: o primeiro acesso (`/configurar`) pede o código de instalação, o seu usuário e senha, e o nome das duas pessoas. Depois disso essa tela deixa de existir. Para criar mais usuários: `flask --app run create-user NOME` (terminal do Railway).
5. Troque sua senha quando quiser em **Minha conta**.

As tabelas são criadas automaticamente na primeira subida (com lock, então vários workers não colidem).

## Margem, gráficos, Excel e comprovante: como funcionam

- **Margem** = valor líquido do item (o desconto da venda é rateado entre os itens) − quantidade × custo. Produto **sem custo informado fica fora da conta** (contar custo zero inflaria o lucro) e a tela avisa quantos ficaram de fora. A margem do geral é exatamente a soma das margens das pessoas.
- **Gráficos** são SVG gerados no servidor (sem biblioteca, compatíveis com a CSP e com a impressão). As cores foram validadas por script (contraste, croma e separação para daltonismo): petróleo e terracota são as duas pessoas, verde é o dinheiro recebido, cinza é o vendido, tons de tijolo são o atraso. Os números vêm das mesmas regras dos painéis, e testes garantem que gráfico e painel nunca divergem.
- **Excel**: valores, datas e percentuais são tipos do Excel (somam, filtram, ordenam); texto que começa com `=`, `+`, `-` ou `@` fica como texto (sem injeção de fórmula).
- **Comprovante**: nunca mostra custo nem margem (testado). Mostra produtos, quem vendeu, pagamentos, saldo e vencimento, carimbo da situação e, em venda a prazo, a linha de assinatura do cliente. É um documento **sem valor fiscal**. Para PDF, use *Imprimir → Salvar como PDF* do navegador; a bobina declara a altura do papel conforme o conteúdo.

## Segurança

Baseada na auditoria e nos pentests do projeto [Notas-despesas](https://github.com/guiolindo/Notas-despesas). O sistema **não abre nada sem login**.

- **Sessão no servidor**: o cookie só guarda um identificador aleatório (no banco fica só o hash). Sair ou trocar a senha apaga a sessão na hora, então um cookie roubado deixa de valer. Expira após 12h sem uso e, no máximo, 7 dias. Cookie `HttpOnly`, `SameSite=Lax` e, em produção, `Secure` com prefixo `__Host-`.
- **Força bruta**: 5 erros por usuário ou 30 por origem em 10 minutos bloqueiam novas tentativas. O contador fica **no banco**, então vale entre os workers (em memória, 2 workers dobrariam o limite). Usuário inexistente gasta o mesmo tempo de uma senha errada (não dá para descobrir quem existe pelo tempo de resposta). Senha de até 128 caracteres.
- **Senha**: 8+ caracteres com letra e número, hash com salt (scrypt); a troca exige a senha atual, recusa repetir a mesma e derruba os outros aparelhos.
- **Primeiro acesso** protegido por `VENDAS_SETUP_TOKEN` (com limite de tentativas).
- **CSRF** em todo POST (comparação em tempo constante), redirecionamento só para caminhos do próprio sistema, nenhum `GET` altera dados.
- **Cabeçalhos**: CSP sem script inline (`script-src 'self'`), `frame-ancestors 'none'`, HSTS, `nosniff`, `Permissions-Policy`, `Cache-Control: no-store` (telas financeiras não ficam em cache), `noindex`.
- **Entradas**: limite de corpo (256 KB), de itens por venda, de tamanho de cada texto e de valores/ids (o PostgreSQL recusa o que o SQLite aceitava, e isso virava erro 500); consultas sempre parametrizadas; saída escapada; células de CSV que começam com `= + - @` são neutralizadas (injeção de fórmula).
- **Idempotência**: reenviar uma venda ou um pagamento (duplo clique, botão Voltar, duas requisições simultâneas) não duplica nada.
- **Auditoria**: login, falhas, bloqueios, vendas, cancelamentos, pagamentos, estornos, exclusões e trocas de senha vão para `audit_logs`, com o IP pseudonimizado (HMAC) e sem nenhuma senha.
- **Concorrência**: `SELECT … FOR UPDATE` (PostgreSQL) / `BEGIN IMMEDIATE` (SQLite); testado com threads.

Limite conhecido: não há 2FA nem papéis diferentes (todo usuário logado pode tudo).

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
- **Duas pessoas**: o total da venda (já com desconto) é dividido entre as pessoas na proporção dos itens de cada uma, e **cada pagamento** é repartido pelo que cada pessoa ainda tem a receber daquela venda. Em centavos inteiros: a soma das pessoas é sempre exatamente o total real, nada fica negativo e, quitada a venda, cada pessoa recebeu a sua parte. O item da venda guarda o dono **da época**, então trocar o dono de um produto só vale daí em diante.
- **Receber do cliente** (sem escolher venda) distribui o valor da venda mais antiga para a mais nova.
- Concorrência: SQLite usa `BEGIN IMMEDIATE`; PostgreSQL usa `SELECT … FOR UPDATE` nas vendas/pagamentos alterados.

## Decisões e limites conhecidos

- Cada venda tem **um** vencimento (sem parcelamento em várias datas). O extrato do cliente mostra *vendas* em aberto/vencidas.
- Quantidades são inteiras (a unidade — un, cx, kg… — é só um rótulo). Valores vão até R$ 20.000.000,00 por campo.
- Gráficos e painéis por pessoa são calculados na hora; com dezenas de milhares de vendas em aberto vale mover os cálculos para SQL.
- Os gráficos não têm tema escuro (o sistema inteiro é claro).
- Há um único nível de acesso (todo usuário logado pode tudo). Usuários extras: `flask --app run create-user NOME`.
- O bloqueio de tentativas de login é em memória por processo (basta contra tentativa simples; para mais, use um proxy/WAF).
- O esquema é criado por `create_all`, que **só cria tabelas novas**: ele não altera colunas de um banco já existente. Antes de evoluir as tabelas em produção, adote o Alembic (um banco criado por uma versão anterior deste código não ganharia as colunas novas).
- Os painéis por pessoa somam as vendas em Python (uma passada pelas vendas do mês e das que estão em aberto): ótimo para um negócio pequeno; com dezenas de milhares de vendas em aberto, vale mover para SQL.
- Itens e valores de uma venda não são editáveis (corrigir = cancelar e registrar de novo); cliente, vencimento e observações são.
- Atalhos no computador: `N` nova venda, `/` buscar, `Enter` adiciona produto, `Ctrl+Enter` conclui a venda.
