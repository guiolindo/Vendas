# Vendas

Sistema de vendas e controle financeiro: produtos, estoque, vendas, clientes, pagamentos (inclusive parciais), vencimentos, cobranças e relatórios. É **uma única aplicação** em Python (Flask + Jinja2 + SQLAlchemy), com PostgreSQL em produção. O negócio é tocado por **duas pessoas**: cada produto pertence a uma delas, e há um painel geral e um painel simples de cada pessoa. A interface é mobile-first: no celular tem navegação inferior, listas em cartões e uma tela de venda própria para o polegar.

## O que o sistema faz

- **Vendas** com pagamento total, parcial ou a prazo, estoque, clientes (extrato), cobranças por vencimento.
- **Margem de lucro**: a pessoa informa quanto o produto custou e por quanto vendeu; o sistema mostra a margem em R$ e em % por produto, venda, pessoa e no mês (ao cadastrar o produto e na própria tela de venda, ao vivo).
- **Duas pessoas**: o produto não tem dono; **na hora da venda você escolhe quem está vendendo** (a escolha fica lembrada no aparelho). Painel geral e painel simples de cada pessoa.
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
2. No serviço do app, em *Variables*, defina (atalho: **Raw Editor** e cole o conteúdo de [`.env.example`](.env.example), trocando os dois valores `troque-…`):
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (o app aceita o formato `postgres://…` do Railway)
   - `VENDAS_SECRET_KEY` = uma chave longa e aleatória (`python -c "import secrets; print(secrets.token_hex(32))"`).
   - `VENDAS_SETUP_TOKEN` = um código só seu (ex.: 20+ caracteres aleatórios). Sem ele, o cadastro do primeiro usuário fica **trancado**: a URL do Railway é pública, e sem esse código quem chegasse primeiro viraria o dono do sistema.

   Em produção o app **se recusa a iniciar** sem `VENDAS_SECRET_KEY` e sem `DATABASE_URL` (o disco do Railway é efêmero; sem chave fixa as sessões cairiam a cada deploy).
3. O `railway.json` já define o comando de início (gunicorn) e o healthcheck em `/saude`.
4. Abra a URL do app: o primeiro acesso (`/configurar`) pede o código de instalação, o seu usuário e senha, e o nome das duas pessoas. Depois disso essa tela deixa de existir. Para criar mais usuários: `flask --app run create-user NOME` (terminal do Railway).
5. Troque sua senha quando quiser em **Minha conta**.

### Banco de dados e migrações

- Em produção o esquema vem do **Alembic**: o `preDeployCommand` do `railway.json` roda `alembic upgrade head` antes de cada deploy (se falhar, o deploy não vai ao ar e a versão antiga continua servindo). O app se recusa a iniciar se o banco não estiver na última versão.
- Mudou um modelo? `alembic revision --autogenerate -m "o que mudou"`, revise o arquivo gerado e faça commit. `tests/test_migrations.py` falha se os modelos e as migrações divergirem.
- No computador (SQLite) as tabelas são criadas sozinhas. Se você já tinha um banco local de uma versão antiga, apague `instance/vendas.db`.
- **Backup**: ative os backups do serviço PostgreSQL no Railway (aba *Backups*) e, antes de mudanças grandes, faça `pg_dump "$DATABASE_URL" > backup.sql`. Restaurar: `psql "$DATABASE_URL" < backup.sql` em um banco vazio.

### Fuso horário

O servidor roda em UTC, mas "hoje", vencimentos e atraso seguem o horário do negócio: `America/Sao_Paulo` por padrão (mude com `VENDAS_TZ`). Uma venda feita às 22h de Brasília entra no dia certo.

### Fora do Railway

Defina `VENDAS_ENV=prod` para ligar as proteções de produção (cookie seguro, HTTPS, chaves obrigatórias), rode `alembic upgrade head` e depois o gunicorn por trás de um proxy com TLS.

## Margem, gráficos, Excel e comprovante: como funcionam

- **Margem** = valor líquido do item (o desconto da venda é rateado entre os itens) − quantidade × custo. Produto **sem custo informado fica fora da conta** (contar custo zero inflaria o lucro) e a tela avisa quantos ficaram de fora. A margem do geral é exatamente a soma das margens das pessoas.
- **Gráficos** são SVG gerados no servidor (sem biblioteca, compatíveis com a CSP e com a impressão). As cores foram validadas por script (contraste, croma e separação para daltonismo): petróleo e terracota são as duas pessoas, verde é o dinheiro recebido, cinza é o vendido, tons de tijolo são o atraso. Os números vêm das mesmas regras dos painéis, e testes garantem que gráfico e painel nunca divergem.
- **Excel**: valores, datas e percentuais são tipos do Excel (somam, filtram, ordenam); texto que começa com `=`, `+`, `-` ou `@` fica como texto (sem injeção de fórmula).
- **Comprovante**: nunca mostra custo nem margem (testado). Mostra produtos, quem vendeu, pagamentos, saldo e vencimento, carimbo da situação e, em venda a prazo, a linha de assinatura do cliente. É um documento **sem valor fiscal**. Para PDF, use *Imprimir → Salvar como PDF* do navegador; a bobina declara a altura do papel conforme o conteúdo.

## Com ou sem controle de estoque

No primeiro acesso o sistema pergunta se você quer controlar o estoque. A resposta vira o padrão dos produtos novos (mude em **Configurações**), e cada produto ainda tem a sua caixa **Controlar o estoque deste produto**.

- **Sem controle:** vende qualquer quantidade, nada baixa nem "acaba", o produto não aparece em estoque baixo nem nos relatórios de estoque. Preço, custo, margem e lucro funcionam igual. Serve para serviços, encomendas e quem não conta quantidade.
- **Com controle:** cada venda baixa a quantidade (nunca fica negativa, nem com duas vendas ao mesmo tempo) e o sistema avisa quando chegar ao mínimo.
- Ligar o controle depois: o estoque começa com o que estiver registrado (zero); lance a quantidade em "Contei e o número é outro". Vendas antigas, feitas sem controle, não devolvem estoque se forem canceladas.

## Custo, compras e o que dá para editar ou excluir

**Mudar o custo (ou o preço) vale só daqui pra frente.** Cada item vendido guarda uma cópia do custo e do preço do momento da venda; painéis, gráficos, relatórios e a margem de cada venda leem essa cópia. Por isso mudar o custo **não altera nenhuma estatística do passado** (há testes que provam isso em painel, gráficos e relatórios). Cada mudança de custo fica no *Histórico de custo* do produto (cadastro, edição, compra, compra excluída).

**Compras de mercadoria**: ao registrar a chegada de mercadoria dá para informar quanto custou cada unidade e, se quiser, usar esse valor como custo do produto dali em diante. A compra pode ser **excluída** (o registro fica no histórico, marcado) desde que ainda existam em estoque as unidades dela; se já foram vendidas, o sistema explica. Se a compra tinha mudado o custo e ninguém mexeu nele depois, excluí-la devolve o custo anterior. O relatório "Compras de mercadoria" lista e soma o que foi gasto.

| Coisa | Editar | Excluir | Regra |
|---|---|---|---|
| Produto | tudo (custo e preço valem daqui pra frente) | só se nunca foi vendido | senão, desativar |
| Compra de mercadoria | (lançar outra) | sim, se as unidades ainda estão em estoque | fica no histórico, marcada |
| Venda | cliente, vencimento, observação; **itens e valores: "Corrigir e refazer"** | sim, **depois de cancelada** | o cancelamento devolve o estoque e estorna os pagamentos; a exclusão fica na auditoria |
| Pagamento | (estornar e lançar de novo) | estorno (fica no histórico) | nunca some |
| Cliente | tudo | só se não tem vendas | senão, desativar |
| Pessoa | nome | só se não tem produto nem venda | senão, desativar |

"Corrigir e refazer" cancela a venda e abre a tela de venda já preenchida (itens, preços combinados, cliente, desconto, observação); itens sem estoque ou inativos ficam de fora e são avisados. Os pagamentos da venda cancelada foram estornados e precisam ser lançados de novo.

**Limite conhecido do custo:** cada produto tem um custo atual (não há custo por lote, como PEPS ou custo médio). O valor do estoque ("estoque ao custo atual") usa o custo atual para todas as unidades; por isso o rótulo diz "atual".

## Texto e uso para qualquer pessoa

- **Linguagem do dia a dia**: nada de "estorno", "auditoria" ou "sessão". Pagamento lançado errado é **desfeito**; a mensagem de erro diz o que fazer ("A página ficou aberta por muito tempo. Atualize a página e tente de novo"), e o texto da tela de venda guarda os itens enquanto isso.
- **Sem assumir gênero**: o sistema não sabe quem são as pessoas. Frases usam o nome ou construções neutras ("Pessoa cadastrada: Nome", "o que pertence a Nome"), nunca "dela", "cadastrada" depois de um nome etc.
- **Sem frases genéricas**: não há "bem-vindo", "insights", emojis nem texto de marketing. Um teste (`tests/test_ux.py`) varre todo o texto da interface e das mensagens procurando jargão, tom de marketing e gênero assumido, para isso não voltar.
- **Primeiro uso**: um passo a passo no início ("Para começar") mostra o próximo passo em destaque e some sozinho depois da primeira venda. O primeiro acesso já pergunta o nome do negócio e das duas pessoas.
- **Legibilidade**: texto-base de 16 px; **todo texto tem contraste mínimo de 4,5:1** (padrão WCAG AA), medido direto nas cores do CSS por teste. Alvos de toque de 44 px no celular, atalho "Pular para o conteúdo" para teclado, campos sempre com rótulo e botões de ícone com nome.
- Um mesmo conceito tem um só nome: **"Falta pagar"** na venda e no recibo, **"A receber"** no painel (visto de quem vende).

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
- **Duas pessoas**: cada venda é de quem vendeu (escolhido na venda; dá para corrigir depois, e a venda inteira muda de pessoa). O que cada pessoa vendeu, custou, rendeu, recebeu e tem a receber vem dessas vendas; a soma das pessoas é sempre exatamente o total real, em centavos inteiros. Vendas antigas, de antes dessa escolha, que misturavam itens de pessoas diferentes continuam divididas item a item, e cada pagamento é repartido pelo que cada pessoa ainda tem a receber. Cada item guarda quem vendeu **na época**.
- **Receber do cliente** (sem escolher venda) distribui o valor da venda mais antiga para a mais nova.
- Concorrência: SQLite usa `BEGIN IMMEDIATE`; PostgreSQL usa `SELECT … FOR UPDATE` nas vendas/pagamentos alterados.

## Decisões e limites conhecidos

- Cada venda tem **um** vencimento (sem parcelamento em várias datas). O extrato do cliente mostra *vendas* em aberto/vencidas.
- Quantidades são inteiras (a unidade — un, cx, kg… — é só um rótulo). Valores vão até R$ 20.000.000,00 por campo.
- Gráficos e painéis por pessoa são calculados na hora; com dezenas de milhares de vendas em aberto vale mover os cálculos para SQL.
- Os gráficos não têm tema escuro (o sistema inteiro é claro).
- Há um único nível de acesso (todo usuário logado pode tudo). Usuários extras: `flask --app run create-user NOME`.
- O bloqueio de tentativas de login é em memória por processo (basta contra tentativa simples; para mais, use um proxy/WAF).
- O esquema evolui por migrações Alembic (`migrations/`). O banco local de desenvolvimento é criado direto pelos modelos e não é migrado: apague-o ao atualizar.
- Cliente com vendas não pode ser apagado nem anonimizado (o histórico financeiro precisa dele); não há 2FA nem níveis de acesso: todos os usuários veem tudo.
- Os painéis por pessoa somam as vendas em Python (uma passada pelas vendas do mês e das que estão em aberto): ótimo para um negócio pequeno; com dezenas de milhares de vendas em aberto, vale mover para SQL.
- Itens e valores de uma venda não são editáveis (corrigir = cancelar e registrar de novo); cliente, vencimento e observações são.
- Atalhos no computador: `N` nova venda, `/` buscar, `Enter` adiciona produto, `Ctrl+Enter` conclui a venda.
