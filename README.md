# cpo-adbsum

**CPO Provas** — sistema de aplicação de provas por módulos, com correção,
cálculo de notas, aprovação/reprovação e emissão de certificados.

Aplicação Django monolítica e modular, com PostgreSQL, Django Templates e
Bootstrap 5. Produção prevista em uma instância AWS EC2 com Nginx, Gunicorn e
systemd — sem dependência de serviços proprietários da AWS na lógica de
negócio.

> **Estado atual: Etapa 3 — administração de provas.**
> Sobre a Etapa 1 (autenticação por e-mail, segregação de papéis, troca
> obrigatória de senha inicial, auditoria e health check) e a Etapa 2 (alunos,
> módulos, matrículas e importação de planilhas), o sistema agora monta provas:
> questões de cinco tipos, gabarito, pontuação em Decimal, publicação validada,
> fechamento, versionamento por duplicação, gabarito administrativo e preview
> sem resposta correta.
>
> **O aluno ainda não realiza provas.** Publicar uma prova não a expõe a
> ninguém: a tela do aluno continua mostrando apenas os módulos. Realização,
> cronômetro, correção, notas e certificados vêm nas etapas seguintes.

---

## 1. Pré-requisitos

| Item | Versão | Observação |
|---|---|---|
| Windows | 10 / 11 | ambiente de desenvolvimento |
| Python | **3.12** | mesma versão do Ubuntu 24.04 LTS, o SO previsto em produção |
| PostgreSQL | **16** | não usar SQLite em nenhum momento |
| Git | 2.x | — |

Confira o que já está instalado:

```powershell
py --list
py -3.12 --version
git --version
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" --version
```

Se faltar alguma coisa:

```powershell
winget install --id Python.Python.3.12 -e --scope user
winget install --id PostgreSQL.PostgreSQL.16 -e
winget install --id Git.Git -e
```

> O instalador do PostgreSQL pede uma senha para o superusuário `postgres`.
> Guarde-a: ela é necessária para criar o banco da aplicação no passo 3.

---

## 2. Virtualenv e dependências

```powershell
cd c:\Users\nickolas.sales\Projetos\cpo-adbsum

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

python --version          # precisa responder Python 3.12.x
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Se o PowerShell bloquear a ativação:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

---

## 3. PostgreSQL

O banco de desenvolvimento usa um usuário dedicado. **Nunca** use o
superusuário `postgres` como usuário da aplicação.

```powershell
$psql = "C:\Program Files\PostgreSQL\16\bin\psql.exe"
$env:PGPASSWORD = "<senha do superusuario postgres>"

& $psql -U postgres -h 127.0.0.1 -c "CREATE USER cpo_user WITH PASSWORD '<senha-forte>' CREATEDB;"
& $psql -U postgres -h 127.0.0.1 -c "CREATE DATABASE cpo_provas OWNER cpo_user;"
```

O atributo **`CREATEDB`** não é opcional: o pytest-django cria e destrói o
banco `test_cpo_provas` a cada execução da suíte, e sem essa permissão os
testes falham na criação do banco.

Confira a conexão:

```powershell
$env:PGPASSWORD = "<senha do cpo_user>"
& $psql -U cpo_user -h 127.0.0.1 -d cpo_provas -c "SELECT current_user, current_database();"
```

---

## 4. Configuração (.env)

```powershell
Copy-Item .env.example .env
```

Gere uma `SECRET_KEY` e cole no `.env`:

```powershell
python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"
```

Preencha no mínimo:

```dotenv
SECRET_KEY='<a chave gerada acima>'
DEBUG=True
DATABASE_URL=postgresql://cpo_user:<senha>@127.0.0.1:5432/cpo_provas
ALLOWED_HOSTS=localhost,127.0.0.1
SITE_URL=http://127.0.0.1:8000
INSTITUTION_NAME=CPO
```

> A `SECRET_KEY` do Django pode conter `#`, que o leitor de `.env` interpreta
> como início de comentário. Mantenha o valor entre aspas simples.

O `.env` **não é versionado** e nunca deve conter valores reais no
`.env.example`.

### Variáveis reconhecidas

| Variável | Obrigatória | Padrão | Para que serve |
|---|---|---|---|
| `SECRET_KEY` | **sim** | — | assinatura de sessão e CSRF |
| `DATABASE_URL` | **sim** | — | conexão PostgreSQL; única forma de configurar o banco |
| `DEBUG` | não | `False` | modo de desenvolvimento |
| `ALLOWED_HOSTS` | não | `localhost,127.0.0.1` | hosts aceitos, separados por vírgula |
| `CSRF_TRUSTED_ORIGINS` | não | vazio | origens confiáveis, com esquema, em produção |
| `SITE_URL` | não | `http://127.0.0.1:8000` | URL pública; futuro QR Code dos certificados |
| `INSTITUTION_NAME` | não | `CPO` | nome exibido na interface e nos certificados |
| `DEFAULT_STUDENT_PASSWORD` | não | vazio | senha inicial de alunos importados (Etapa 2) |
| `TRUST_PROXY_HEADERS` | não | `False` | ligar **apenas** com Nginx na frente |
| `LOG_LEVEL` | não | `INFO` | nível de log |
| `SESSION_COOKIE_AGE` | não | `43200` | 12h; precisa exceder a maior prova |
| `DB_CONN_MAX_AGE` | não | `60` | reaproveitamento de conexão |
| `SECURE_SSL_REDIRECT` | não | `True` | somente quando `DEBUG=False` |
| `SECURE_HSTS_SECONDS` | não | `31536000` | somente quando `DEBUG=False` |

---

## 5. Banco de dados

```powershell
python manage.py migrate
```

Nunca resolva erro de migration apagando o banco, apagando migrations ou
usando `--fake`. Diagnostique e corrija preservando os dados.

---

## 6. Criar o primeiro administrador

```powershell
python manage.py createsuperuser
```

O comando pergunta, nessa ordem:

```
E-mail:
Nome completo:
Password:
Password (again):
```

Não existe username. O usuário criado sai com `role=ADMIN`, `is_staff=True`,
`is_superuser=True` e `must_change_password=False`.

---

## 7. Executar

```powershell
python manage.py runserver
```

### Acesso geral

| URL | Quem acessa | O que faz |
|---|---|---|
| `/` | todos | redireciona para o painel do papel, ou para o login |
| `/login/` | anônimo | autenticação por e-mail e senha |
| `/logout/` | autenticado | encerra a sessão (**POST**) |
| `/alterar-senha/` | autenticado | troca de senha, voluntária ou obrigatória |
| `/health/` | público | verificação de aplicação e banco |
| `/django-admin/` | `is_staff` | ferramenta técnica, **não** é a interface oficial |

### Painel administrativo — somente **ADMIN**

| URL | Método | O que faz |
|---|---|---|
| `/admin-panel/` | GET | dashboard com indicadores |
| `/admin-panel/alunos/` | GET | lista com busca, filtro e paginação |
| `/admin-panel/alunos/novo/` | GET/POST | cadastro manual |
| `/admin-panel/alunos/<id>/` | GET | ficha do aluno e suas matrículas |
| `/admin-panel/alunos/<id>/editar/` | GET/POST | edição de nome, e-mail e observações |
| `/admin-panel/alunos/<id>/bloquear/` | **POST** | `is_active = False` |
| `/admin-panel/alunos/<id>/desbloquear/` | **POST** | `is_active = True` |
| `/admin-panel/alunos/importar/` | GET/POST | envio da planilha |
| `/admin-panel/alunos/importar/preview/` | GET | conferência, **sem gravar nada** |
| `/admin-panel/alunos/importar/confirmar/` | **POST** | aplica o lote |
| `/admin-panel/alunos/importar/cancelar/` | **POST** | descarta o lote |
| `/admin-panel/modulos/` | GET | lista com busca e filtro |
| `/admin-panel/modulos/novo/` | GET/POST | criação |
| `/admin-panel/modulos/<id>/` | GET | detalhe e matriculados |
| `/admin-panel/modulos/<id>/editar/` | GET/POST | edição |
| `/admin-panel/modulos/<id>/ativar/` | **POST** | `is_active = True` |
| `/admin-panel/modulos/<id>/desativar/` | **POST** | `is_active = False` |
| `/admin-panel/matriculas/` | GET | lista com quatro filtros |
| `/admin-panel/matriculas/nova/` | GET/POST | matricular aluno em módulo |
| `/admin-panel/matriculas/<id>/bloquear/` | **POST** | suspende o acesso |
| `/admin-panel/matriculas/<id>/liberar/` | **POST** | devolve o acesso |
| `/admin-panel/matriculas/<id>/desativar/` | **POST** | `INACTIVE` + acesso bloqueado |
| `/admin-panel/matriculas/<id>/reativar/` | **POST** | volta a `ACTIVE` liberada |
| `/admin-panel/matriculas/<id>/concluir/` | **POST** | `COMPLETED` |

#### Provas (Etapa 3)

| URL | Método | O que faz |
|---|---|---|
| `/admin-panel/provas/` | GET | lista com busca, filtro por módulo e situação |
| `/admin-panel/provas/nova/` | GET/POST | criação; nasce **rascunho**, versão 1 |
| `/admin-panel/provas/<id>/` | GET | detalhe, pendências de publicação e linhagem |
| `/admin-panel/provas/<id>/editar/` | GET/POST | edição; só em rascunho |
| `/admin-panel/provas/<id>/publicar/` | **POST** | valida tudo e congela o total de pontos |
| `/admin-panel/provas/<id>/fechar/` | **POST** | encerra; nada é excluído |
| `/admin-panel/provas/<id>/duplicar/` | **POST** | nova versão independente, em rascunho |
| `/admin-panel/provas/<id>/senha/` | GET/POST | define ou troca a senha de acesso |
| `/admin-panel/provas/<id>/senha/remover/` | **POST** | remove a senha |
| `/admin-panel/provas/<id>/gabarito/` | GET | respostas corretas e explicações internas |
| `/admin-panel/provas/<id>/preview/` | GET | a prova como o aluno a verá, **sem gabarito** |
| `/admin-panel/provas/<id>/questoes/` | GET | questões e total de pontos |
| `/admin-panel/provas/<id>/questoes/nova/` | GET/POST | criação, com alternativas na mesma tela |
| `/admin-panel/provas/<id>/questoes/<qid>/editar/` | GET/POST | edição; **404** se a questão for de outra prova |
| `/admin-panel/provas/<id>/questoes/<qid>/excluir/` | **POST** | exclusão; só em rascunho |

Tentativa de escrita bloqueada pelo estado da prova responde **409 Conflict**
com a lista de motivos. Um `GET` numa tela de edição de prova não editável
redireciona para o detalhe com mensagem — é navegação, não escrita.

### Painel do aluno — somente **STUDENT**

| URL | Método | O que faz |
|---|---|---|
| `/aluno/` | GET | módulos liberados para o aluno |
| `/aluno/modulos/<id>/` | GET | detalhe do módulo; **404** sem matrícula liberada |

Toda rota que altera estado é **POST com CSRF**. Um `GET` nessas rotas
responde `405`, e não existe link que dispare alteração.

---

## 8. Testes

```powershell
pytest
```

A suíte roda contra PostgreSQL, nunca SQLite. O banco `test_cpo_provas` é
criado e destruído automaticamente.

Antes de considerar qualquer etapa concluída:

```powershell
python manage.py check
python manage.py makemigrations --check
python manage.py showmigrations
pytest
```

---

## 9. Estrutura

```
cpo-adbsum/
├── config/              settings, URLconf raiz, WSGI
├── common/              infraestrutura transversal
│   ├── mixins.py            autorização por papel (único lugar que lê user.role)
│   ├── http.py              IP e user-agent da requisição
│   ├── navigation.py        destino por papel e menu administrativo
│   ├── views.py             health check, raiz, painéis
│   └── context_processors.py
├── accounts/            identidade e autenticação
│   ├── models.py            User customizado, login por e-mail
│   ├── managers.py          criação e normalização de e-mail
│   ├── forms.py             login e troca de senha
│   ├── middleware.py        imposição de must_change_password
│   ├── signals.py           auditoria de login
│   └── admin.py             registro no Django Admin
├── audit/               trilha de auditoria
│   ├── models.py            AuditLog, somente inserção
│   └── services.py          gravação com sanitização de segredos
├── students/            alunos
│   ├── models.py            StudentProfile (1:1 com User)
│   ├── services.py          criar, editar, bloquear, desbloquear
│   ├── importers.py         leitura, análise e confirmação de CSV/XLSX
│   ├── forms.py             formulários de aluno e de upload
│   └── views.py             telas administrativas de aluno
├── courses/             módulos e matrículas
│   ├── models.py            Module, Enrollment
│   ├── services.py          regras de módulo e matrícula
│   ├── forms.py             formulários de módulo e matrícula
│   └── views.py             telas administrativas e área do aluno
├── exams/               provas, questões e alternativas
│   ├── models.py            Exam, Question, QuestionOption
│   ├── services/
│   │   ├── exam.py              criar, editar, publicar, fechar, duplicar, senha
│   │   ├── question.py          questões e alternativas
│   │   └── validation.py        estrutura de questão e requisitos de publicação
│   ├── selectors.py         leitura SEM gabarito, para o aluno e o preview
│   ├── forms.py             formulários administrativos
│   ├── views_admin.py       telas administrativas
│   └── admin.py             Django Admin somente leitura
├── templates/
├── static/
│   ├── css/app.css
│   └── vendor/bootstrap/    Bootstrap 5.3.3 servido localmente
├── requirements.txt
├── pytest.ini
├── .env.example
└── manage.py
```

A app `certificates` será criada quando o domínio for implementado. O
projeto não carrega apps vazias.

---

## 9.1 Importação de alunos

Acesse **`/admin-panel/alunos/importar/`**. O fluxo tem três passos e nada é
gravado antes da confirmação:

```
Upload  →  Análise  →  Preview  →  Confirmação  →  Importação
                       (somente             (uma única
                        leitura)             transação)
```

**Formato do arquivo** — `.csv` ou `.xlsx`, até 5 MB, no máximo 2000 linhas:

```csv
nome,email,modulo
João da Silva,joao@exemplo.com,MOD1
Maria Oliveira,maria@exemplo.com,MOD1
Pedro Souza,pedro@exemplo.com,MOD2
```

- Os cabeçalhos são reconhecidos sem diferenciar maiúsculas nem acentos:
  `Nome`, `EMAIL`, `E-mail`, `Modulo` e `Módulo` funcionam.
- Espaços em volta dos valores são removidos.
- CSV aceita vírgula ou ponto e vírgula como separador — o Excel em português
  grava com ponto e vírgula.
- **CSV precisa estar em UTF-8** (com ou sem BOM). Outra codificação é
  recusada com mensagem clara, em vez de gravar nomes corrompidos. Se o Excel
  dificultar, use `.xlsx`, que não tem esse problema.
- A coluna `modulo` deve conter o **código** do módulo (`MOD1`). O nome exato
  também é aceito, mas só quando for inequívoco: se dois módulos tiverem o
  mesmo nome, a linha é recusada em vez de escolher um deles.

**O que o preview informa por linha:**

| Status | Efeito na confirmação |
|---|---|
| `NOVO ALUNO` | cria conta, perfil e matrícula |
| `ALUNO EXISTENTE` | cria apenas a matrícula |
| `MATRICULA EXISTENTE` | ignorada, já está do jeito desejado |
| `MATRICULA INATIVA EXISTENTE` | ignorada; reative pela tela de matrículas |
| `LINHA DUPLICADA` | ignorada, a primeira ocorrência já vale |
| `MODULO NAO ENCONTRADO` | inválida |
| `E-MAIL INVALIDO` | inválida |
| `NOME AUSENTE` | inválida |
| `MODULO AUSENTE` | inválida |
| `E-MAIL DE ADMINISTRADOR` | inválida — um ADMIN nunca vira aluno |

**Regras que a importação respeita:**

- O mesmo e-mail em dois módulos gera **uma** conta e **duas** matrículas.
- Aluno já cadastrado **não** tem o nome sobrescrito. Se o arquivo trouxer
  nome diferente, o preview avisa e o cadastro atual é mantido.
- Alunos criados recebem `DEFAULT_STUDENT_PASSWORD` com hash e nascem com
  `must_change_password = True`.
- Somente as linhas válidas são importadas, e todas em uma única transação.

---

## 9.2 Provas

### Os três modelos

**`Exam`** — uma prova, em uma versão específica. Pertence obrigatoriamente a
um `courses.Module`. Guarda título, descrição, instruções, janela
(`open_at`/`close_at`), `duration_minutes`, `passing_score` (0 a 10, Decimal,
padrão 8,00), `total_points` (snapshot), `failure_message`, `max_attempts`,
`randomize_questions`, `randomize_options`, `show_score_after_submission`,
`access_password_hash`, `version`, `parent_exam`, `root_exam` e `created_by`.

**`Question`** — uma questão de uma prova. `type`, `text`, `points` (Decimal,
sempre maior que zero), `required`, `order`, `internal_explanation`
(**administrativa**, nunca chega ao aluno) e `active`.

**`QuestionOption`** — uma alternativa. `text`, `is_correct` (**o gabarito**) e
`order`.

### Estados

```
DRAFT ──publicar──> PUBLISHED ──fechar──> CLOSED
```

| Estado | Pode |
|---|---|
| **Rascunho** | editar tudo: prova, questões, alternativas, gabarito, pontos |
| **Publicada** | somente trocar ou remover a senha de acesso; duplicar |
| **Fechada** | somente leitura; duplicar |

Precisa mudar uma prova publicada? Duplique-a e edite a nova versão.

### Tipos de questão

| Tipo | Interface | Regra de estrutura |
|---|---|---|
| `SINGLE_CHOICE` | Escolha única | ≥ 2 alternativas, **exatamente 1** correta |
| `MULTIPLE_CHOICE` | Múltiplas respostas | ≥ 2 alternativas, ≥ 1 correta e ≥ 1 incorreta |
| `TRUE_FALSE` | Verdadeiro ou falso | exatamente "Verdadeiro" e "Falso", 1 correta |
| `SHORT_TEXT` | Resposta curta | **sem** alternativas; correção manual |
| `ESSAY` | Dissertativa | **sem** alternativas; correção manual |

Em Verdadeiro ou Falso o administrador escolhe apenas qual é a correta; as
duas alternativas são criadas pelo backend com texto fixo.

### Escala da nota

A nota final é sempre de **0 a 10**, independentemente de a soma dos pontos
das questões dar 10, 20, 50 ou qualquer outro valor:

```
nota final = pontos obtidos / total_points * 10
```

A comparação com `passing_score` usará o valor preciso, antes de qualquer
arredondamento de exibição. `total_points` é congelado na publicação, de modo
que a prova carrega a própria escala histórica.

### Publicação

A publicação é transacional e só acontece com tudo válido. É recusada quando:

- o módulo está inativo
- falta abertura, encerramento ou duração
- o encerramento não é posterior à abertura
- a nota mínima está fora de 0–10, ou as tentativas são menos de 1
- não há nenhuma questão ativa, ou a soma dos pontos não é maior que zero
- qualquer questão ativa tem estrutura inválida, pelas regras da tabela acima

A recusa devolve **409** com a lista completa dos problemas — não o primeiro
deles. Ao publicar, na mesma transação: valida, calcula e grava
`total_points`, muda o estado, marca `published_at` e registra
`EXAM_PUBLISHED`.

### Versionamento

```
Avaliação Módulo 1
├── v1  root_exam=null   parent=null   CLOSED
├── v2  root_exam=v1     parent=v1     PUBLISHED
└── v3  root_exam=v1     parent=v2     DRAFT
```

Duplicar copia configuração, questões, alternativas, gabarito e pontuações
para objetos **novos**, com PKs próprias. A cópia nasce em rascunho, com
`published_at` e `closed_at` vazios e `total_points` zerado. A versão é a
maior da linhagem mais um — duplicar a v1 acima produziria a **v4**.

#### O que o banco garante sozinho

Três constraints, que valem para qualquer caminho de escrita — inclusive
`objects.create`, `QuerySet.update`, shell e migrations de dados:

| Constraint | Exige |
|---|---|
| `exam_versao_unica_na_linhagem` | `UNIQUE(root_exam, version)` |
| `exam_raiz_e_versao_coerentes` | `(root IS NULL AND version = 1)` ou `(root IS NOT NULL AND version >= 2)` |
| `exam_linhagem_parent_coerente` | `root` e `parent` existem ou faltam **juntos** |

Ser raiz e ter raiz são estados exclusivos, e as duas referências têm papéis
diferentes mas nunca aparecem sozinhas. O que fica **fora** do alcance de uma
`CheckConstraint` é a relação entre linhas — que `parent.root_exam` seja esta
mesma raiz —, porque uma check enxerga apenas a própria linha. Essa
consistência continua sendo responsabilidade de `duplicate_exam`.

#### Consequência ao excluir uma prova

`parent_exam` e `root_exam` usam `on_delete=SET_NULL`. Apagar uma prova que
outra referencia faz o Django zerar essas colunas na referenciadora, o que
produz exatamente um dos estados agora proibidos — então o `DELETE` falha com
`IntegrityError`.

Isso vale inclusive para apagar a linhagem inteira num único queryset: o
collector emite `UPDATE ... SET parent_exam_id = NULL` **antes** dos
`DELETE`s, e esse update atinge também as linhas que sairiam em seguida.

O caminho suportado é apagar **da versão mais nova para a mais antiga**, uma
de cada vez, para que nenhuma versão seja removida enquanto alguém ainda
aponta para ela. A Etapa 3 não criou rota, service nem tela de exclusão de
prova, e o Django admin está somente leitura, então nada disso está exposto
hoje; fica registrado para quem for escrever essa rota. Se ela vier a
existir, vale reavaliar `SET_NULL` — `PROTECT` daria um erro de domínio claro
em vez de um `IntegrityError`.

### Gabarito e preview

| Tela | Mostra `is_correct` | Mostra `internal_explanation` |
|---|---|---|
| `/admin-panel/provas/<id>/gabarito/` | sim | sim |
| `/admin-panel/provas/<id>/preview/` | **não** | **não** |

O preview consome `exams.selectors.questoes_para_aluno`, a mesma função que a
tela do aluno usará. Ela devolve estruturas que **não possuem** os campos do
gabarito, então o vazamento deixa de depender de quem escreve o template.

---

## 10. Decisões de arquitetura desta etapa

**Um único `config/settings.py`, parametrizado por ambiente.**
Um pacote `settings/base|development|production` duplicaria quase todo o
conteúdo e criaria a dúvida recorrente de qual arquivo editar. Como as
diferenças entre ambientes já são expressas por variáveis de ambiente,
acrescentar um segundo eixo de variação seria redundante e propenso a
divergir. O endurecimento de produção fica sob `if not DEBUG`.

**Sem WhiteNoise.**
Em produção o Nginx serve `STATIC_ROOT` direto do disco. WhiteNoise seria uma
camada Python desnecessária no caminho de cada arquivo estático.

**Bootstrap servido localmente, não por CDN.**
No dia da prova, uma CDN indisponível ou bloqueada pela rede da instituição
quebraria o layout da tela de avaliação. O custo é 300 KB versionados.

**`must_change_password` imposto por middleware.**
Um decorator precisa ser lembrado em cada view nova; uma view esquecida vira
um furo na regra. O middleware cobre por construção toda rota presente e
futura, liberando apenas `/alterar-senha/`, `/logout/`, `/health/` e estáticos.

**A troca de senha exige a senha atual.**
Sem isso, uma sessão deixada aberta em um computador compartilhado permitiria
a qualquer pessoa assumir a conta. O aluno acabou de digitar a senha atual no
login, então o custo é próximo de zero.

**AuditLog é *append-only* na camada de aplicação.**
`save()` recusa alteração de registro existente e `delete()` recusa exclusão.
A sanitização da metadata acontece dentro de `audit.services.record()`, de
modo que nenhum chamador consegue gravar senha, token ou segredo por descuido.

**Comentários de código sem acentuação.**
Convenção adotada para eliminar qualquer risco de codificação entre editores
e terminais no Windows. A documentação e a interface usam português pleno.

### Decisões da Etapa 2

**`StudentProfile` criado por serviço, nunca por signal.**
Um signal criaria o perfil em um momento invisível, tornando importações e
testes imprevisíveis. `students.services.create_student` monta `User` e
`StudentProfile` juntos, e é o mesmo caminho usado pela tela e pela
importação — as duas não podem divergir.

**`status` e `access_enabled` são independentes na matrícula.**
`status` é a situação acadêmica; `access_enabled` é uma chave operacional.
Bloquear um aluno na véspera da prova não pode alterar o histórico acadêmico
dele. O aluno só vê o módulo quando `status=ACTIVE`, `access_enabled=True`
e `module.is_active=True` — critério que existe num único lugar,
`courses.services.modulos_do_aluno`.

**"Remover matrícula" desativa, não apaga.**
Um `DELETE` destruiria o registro de que o aluno esteve matriculado, o que
importa quando existirem tentativas e notas.

**Código do módulo protegido por índice funcional.**
`save()` normaliza para maiúsculas e `unique=True` cobre o caminho normal,
mas `bulk_create` e SQL direto não passam por `save()`. Um
`UniqueConstraint(Upper("code"))` no PostgreSQL é a garantia real de que
`MOD1` e `mod1` não coexistem.

**A confirmação da importação refaz a análise.**
As linhas cruas ficam na sessão do servidor; o preview renderizado não decide
nada. Entre ver o preview e confirmar, o banco pode ter mudado — um módulo
pode ter sido desativado, um aluno criado por outra via. Nenhum identificador
vindo do navegador participa da decisão; o formulário carrega apenas um token
que amarra a confirmação ao lote que o administrador viu.

**Sem `pandas` para ler planilha.**
`openpyxl` custa 250 KB e resolve o problema. `pandas` custaria mais de 50 MB
e segundos de import numa EC2 pequena, para uma tarefa que roda algumas vezes
por semestre.

**Bloqueio de aluno não precisou de middleware novo.**
`ModelBackend.get_user()` do Django já devolve `None` para usuário inativo, o
que transforma a sessão em anônima na requisição seguinte. Em vez de duplicar
essa lógica, o comportamento é fixado por teste; os mixins de papel checam
`is_active` apenas como defesa em profundidade.

**Django Admin não é atalho para contornar as regras.**
`Enrollment` e `StudentProfile` ficam somente leitura ali: criar matrícula
envolve validar papel, estado do módulo e ausência de matrícula anterior, e
uma linha inserida pelo Admin passaria por cima de tudo isso. `Module` é
editável, mas a interface oficial continua sendo `/admin-panel/modulos/`,
porque só ela gera auditoria.

### Decisões da Etapa 3

**A prova tem três estados e a transição nunca vem do formulário.**
`DRAFT → PUBLISHED → CLOSED`, sempre nessa direção. `status` não existe como
campo editável em lugar nenhum: publicar e fechar são operações próprias, que
validam antes. Um `status` no formulário deixaria a transição a um POST
forjado.

**Publicada significa congelada — a prova inteira, não só a estrutura.**
Questões, alternativas, gabarito, pontuação, módulo e a própria configuração
param de aceitar alteração. Uma exceção "operacional" hoje vira precedente
amanhã, e a saída certa quando algo precisa mudar é duplicar a prova. A única
coisa que continua alterável é a **senha de acesso**, e por um motivo
concreto: se ela vazar na véspera, trocar precisa ser possível sem invalidar
a prova. Trocar a senha não toca em questão, gabarito nem pontuação.

**Versionamento com duas referências: `parent_exam` e `root_exam`.**
`parent_exam` guarda a procedência ("a v4 saiu da v1"). `root_exam` guarda a
identidade da linhagem, e é ele que permite duas coisas que subir a cadeia de
pais não permitiria: listar todas as versões com uma consulta, e deixar o
**banco** impedir versões repetidas, por `UniqueConstraint(root_exam, version)`.
A raiz tem `root_exam` nulo e versão 1; toda cópia recebe a maior versão da
linhagem mais um. Duplicar a v1 existindo v2 e v3 produz a **v4**.

**Duas duplicações simultâneas não colidem.**
A raiz da linhagem é travada com `select_for_update` antes de a próxima versão
ser calculada, então a segunda operação espera e lê o número que a primeira
acabou de gravar. A constraint única é a rede embaixo disso, para o caso de
alguém escrever um caminho novo e esquecer da transação. Há teste com duas
threads reais: ele falha de forma consistente quando a trava é removida.

**Nada é compartilhado entre versões.**
Duplicar cria `Question` e `QuestionOption` novos, com PKs próprias. Se as
questões fossem compartilhadas, editar a v2 reescreveria a v1, e uma prova já
aplicada deixaria de descrever o que o aluno respondeu.

**`total_points` é snapshot, calculado pelo servidor.**
Enquanto a prova é rascunho, a tela mostra a soma corrente das questões
ativas. Na publicação o valor é calculado e gravado. É isso que dá à prova uma
escala histórica própria: uma correção feita daqui a um ano divide pelo total
que valia no dia da aplicação. O campo nunca é aceito do navegador.

**Pontuação em `Decimal`, do campo ao snapshot.**
`2.50 + 3.25 + 1.25 + 1.50 + 1.50` precisa dar exatamente `10.00`. Em float
daria `9.999999999999998`. Há teste travando isso.

**A senha da prova existe apenas como hash.**
`make_password` na gravação, `check_password` na futura validação. A tela
mostra "Configurada" e oferece alterar ou remover — nunca exibe a senha, e o
campo de formulário não é preenchido com o hash. A auditoria registra que
houve troca, jamais o que foi trocado.

**O gabarito é separado por estrutura de dados, não por disciplina.**
`exams.selectors` devolve *dataclasses* com exatamente os campos visíveis ao
aluno. Não são objetos do ORM com `.only()` ou `.defer()`: nesses, o atributo
continua existindo, e ler `opcao.is_correct` num template dispara outra
consulta e devolve a resposta certa sem aviso nenhum. Numa dataclass sem o
campo, a mesma tentativa vira `AttributeError`.

**O preview usa exatamente o caminho de dados do aluno.**
Não existe uma "versão de preview" dos dados. Se existisse, testar o preview
não provaria nada sobre a tela real da Etapa 4. O teste central renderiza a
mesma prova duas vezes, movendo a resposta certa de uma alternativa para
outra entre as duas, e exige HTML idêntico — o que cobre vazamento por
classe, atributo, ordenação ou espaço em branco, e não só por palavra.

**Verdadeiro ou Falso usa alternativas de texto fixo.**
"Verdadeiro" e "Falso" são criados pelo backend e não podem ser renomeados
para "Opção A" e "Opção B". A tela do aluno precisa poder contar com a forma
sempre igual.

**Resposta curta e dissertativa não têm resposta esperada cadastrável.**
Nesta versão a correção é manual. Um campo de "resposta correta" em texto
livre daria a falsa impressão de correção automática confiável.

**Escrita bloqueada pelo estado responde 409, navegação responde redirect.**
Um `POST` recusado precisa dizer com precisão que o recurso está num estado
incompatível. Um `GET` numa tela de edição costuma vir de link antigo ou aba
esquecida aberta, e devolver 409 a quem só navegou seria hostil sem ganho.

**Django Admin somente leitura para prova, questão e alternativa.**
Publicar exige validar a estrutura inteira e congelar pontos; duplicar exige
calcular a versão sob trava; alterar questão exige rascunho. Nada disso passa
pelo Django Admin. Registrar somente leitura mantém a utilidade de inspecionar
e pesquisar sem abrir o `psql`, sem criar um caminho paralelo de escrita. O
`access_password_hash` é excluído de todas as telas.

---

## 11. Segurança já ativa

- CSRF em todas as rotas mutáveis, inclusive no logout
- Escaping automático dos templates Django
- Senhas exclusivamente com PBKDF2 do Django; nenhuma em texto puro
- Validadores de força de senha ativos
- Mensagem de erro de login genérica — não distingue e-mail inexistente,
  senha errada e conta bloqueada, evitando enumeração de contas
- Autorização validada no servidor; esconder o menu não é controle de acesso
- Sessão com `HttpOnly` e `SameSite=Lax`; `Secure` quando `DEBUG=False`
- HSTS, `X-Frame-Options: DENY`, `nosniff` e `SECURE_PROXY_SSL_HEADER`
  aplicados apenas fora de desenvolvimento
- Gabarito (`is_correct`) e explicação interna nunca deixam a camada
  administrativa: a leitura para o aluno usa estruturas que não possuem os
  campos
- Formulários administrativos declaram campos um a um; `status`,
  `total_points`, `version`, `parent_exam`, `root_exam`, `created_by`,
  `published_at`, `closed_at` e `access_password_hash` não são aceitos do
  navegador
- Questão de outra prova responde **404**, não edita — IDOR administrativo
  também importa
- Senha da prova apenas como hash PBKDF2; nunca exibida, nunca auditada
- Nenhum segredo no repositório; `.env` e credenciais locais no `.gitignore`
- `X-Forwarded-For` ignorado até existir um proxy reverso confiável
- Formulários com campos declarados um a um — nunca `fields = "__all__"` —
  de modo que `role`, `is_staff` e `is_superuser` não são editáveis pela web
- Ações que alteram estado só por `POST`; `GET` nessas rotas responde `405`
- Acesso a objeto resolvido por matrícula, não pelo id da URL: sem matrícula
  liberada a resposta é `404`, que nem confirma a existência do módulo
- `DEFAULT_STUDENT_PASSWORD` nunca aparece em tela, log, preview de
  importação ou trilha de auditoria
- Auditoria com minimização de dados: edições gravam `changed_fields`, e não
  os valores antigos e novos dos dados pessoais

---

## 12. Solução de problemas

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `ImproperlyConfigured: SECRET_KEY` | `.env` ausente ou vazio | copie `.env.example` e gere a chave |
| `connection refused` na porta 5432 | serviço parado | `Get-Service postgresql*` e inicie |
| `permission denied to create database` | falta `CREATEDB` | `ALTER ROLE cpo_user CREATEDB;` |
| `password authentication failed` | senha divergente na `DATABASE_URL` | confira o `.env` |
| Editor acusa `Cannot find module django` | interpretador errado no VS Code | selecione `.venv\Scripts\python.exe` |
| `/health/` responde 503 | banco inacessível | veja o log do console |
