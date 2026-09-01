# cpo-adbsum

**CPO AD Brás Sumaré** — sistema de avaliação para o Curso de Preparação de
Obreiros: provas por módulo, correção, cálculo de notas,
aprovação/reprovação e emissão de certificados.

Aplicação Django monolítica e modular, com PostgreSQL, Django Templates e
Bootstrap 5. Produção em uma instância AWS EC2 com Nginx, Gunicorn e systemd,
atrás de Nginx Proxy Manager com HTTPS do Let's Encrypt — sem dependência de
serviços proprietários da AWS na lógica de negócio.

> **Estado atual: Etapa 8 — acabamento do MVP.**
>
> O ciclo está fechado de ponta a ponta: autenticação e papéis (Etapa 1),
> alunos, módulos e matrículas (Etapa 2), montagem e publicação de provas
> (Etapa 3), realização com cronômetro e autosave (Etapa 4), correção, notas e
> aprovação (Etapa 5), certificado com QR e validação pública sob domínio
> próprio com HTTPS (Etapa 6), contas administrativas, anulação de tentativa e
> trilha de auditoria consultável (Etapa 7).
>
> A Etapa 8 fecha o acabamento: **certificado no modelo oficial da AD Brás
> Sumaré**, com os dados da turma vindos do módulo, e **compartilhamento** do
> endereço público de validação por WhatsApp e pela folha nativa do celular.
>
> O nome anterior do projeto era "CPO Provas". Ele aparece no histórico do Git
> e em trechos deste README que contam a história de uma decisão; na interface,
> não existe mais em lugar nenhum — há teste varrendo os templates e o código
> de aplicação atrás dele.

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
| `/aluno/modulos/<id>/` | GET | módulo e suas provas; **404** sem matrícula liberada |
| `/aluno/provas/<id>/` | GET | instruções; **nunca** cria tentativa |
| `/aluno/provas/<id>/iniciar/` | **POST** | único ponto que cria uma tentativa |
| `/aluno/tentativas/<uuid>/` | GET | a prova, ou a página final se já encerrada |
| `/aluno/tentativas/<uuid>/autosave/` | **POST** | grava uma resposta; devolve JSON |
| `/aluno/tentativas/<uuid>/finalizar/` | **POST** | envia a prova |

A prova é identificada pelo id: ela é a mesma para a turma inteira, e saber
que existe não dá acesso a nada — o portão é a matrícula. A **tentativa** é de
uma pessoa só, então a URL usa UUID e nunca a PK.

Toda rota que altera estado é **POST com CSRF**. Um `GET` nessas rotas
responde `405`, e não existe link que dispare alteração.

| Situação | Resposta |
|---|---|
| prova de módulo sem matrícula liberada, tentativa de outro aluno, UUID inventado | **404** |
| papel errado, CSRF ausente | **403** |
| método errado numa rota de escrita | **405** |
| autosave em tentativa encerrada, envio com obrigatória em branco | **409** |

---

## 8. Testes

```powershell
pytest
```

A suíte roda contra PostgreSQL, nunca SQLite. O banco `test_cpo_provas` é
criado e destruído automaticamente.

### Um app novo precisa entrar no `pytest.ini`

`testpaths` lista os diretórios varridos, um a um. É explícito de propósito — e
por isso mesmo é uma armadilha: **`certificates` ficou de fora quando o app
nasceu na Etapa 6, e os testes dele passaram duas etapas inteiras sem nunca
serem executados por `pytest`.** Um deles já estava quebrado havia uma etapa e
ninguém soube, porque a suíte continuava verde.

Ao criar um app, acrescente-o ao `testpaths` na mesma alteração.

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
├── exams/               provas, questões, alternativas e tentativas
│   ├── models/
│   │   ├── exam.py              Exam, Question, QuestionOption
│   │   └── attempt.py           ExamAttempt, AttemptQuestion, AttemptOption,
│   │                            Answer, AnswerOption
│   ├── services/
│   │   ├── exam.py              criar, editar, publicar, fechar, duplicar, senha
│   │   ├── question.py          questões e alternativas
│   │   ├── validation.py        estrutura de questão e requisitos de publicação
│   │   └── attempt.py           iniciar, autosave, enviar, expirar
│   ├── selectors.py         leitura SEM gabarito, para o aluno e o preview
│   ├── forms.py             formulários administrativos
│   ├── views_admin.py       telas administrativas
│   ├── views_student.py     instruções, prova, autosave, envio
│   ├── management/commands/
│   │   └── expirar_tentativas.py   encerra tentativas órfãs
│   └── admin.py             Django Admin somente leitura
├── certificates/        certificado, PDF, validação pública e compartilhamento
│   ├── models.py            Certificate e os campos *_snapshot
│   ├── services.py          emitir, revogar, montar mensagem, auditar partilha
│   ├── pdf.py               desenho do documento e QR (ReportLab)
│   ├── ornamentos.py        molduras, ondas e fitas em vetor
│   ├── views_public.py      validação sem autenticação
│   ├── views_student.py     lista, download, compartilhamento
│   ├── views_admin.py       consulta e revogação
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

`parent_exam` e `root_exam` usam `on_delete=PROTECT` (migration
`exams/0003_linhagem_protect`). Apagar uma versão que tem descendentes
levanta `ProtectedError` **antes de tocar em qualquer linha**, e a exceção
carrega em `protected_objects` exatamente quais versões estão impedindo.

Antes era `SET_NULL`, e funcionava por acidente: o Django zerava as colunas na
referenciadora, isso produzia um dos estados proibidos pelas constraints
acima, e o `DELETE` falhava com `IntegrityError` — um erro obscuro, disparado
por um `UPDATE` que ninguém tinha escrito. Uma versão com descendentes faz
parte de histórico: apagá-la zeraria as referências de quem veio depois e
deixaria a linhagem sem começo.

A recusa vale inclusive para apagar a linhagem inteira num único queryset: o
collector do Django não isenta objetos que estão no próprio conjunto de
exclusão quando a relação é `PROTECT`.

O caminho suportado é apagar **da versão mais nova para a mais antiga**, uma
de cada vez, para que nenhuma versão seja removida enquanto alguém ainda
aponta para ela. Não há rota, service nem tela de exclusão de prova, e o
Django admin está somente leitura, então nada disso está exposto hoje; fica
registrado para quem for escrever essa rota.

### Gabarito e preview

| Tela | Mostra `is_correct` | Mostra `internal_explanation` |
|---|---|---|
| `/admin-panel/provas/<id>/gabarito/` | sim | sim |
| `/admin-panel/provas/<id>/preview/` | **não** | **não** |

O preview consome `exams.selectors.questoes_para_aluno`, a mesma função que a
tela do aluno usará. Ela devolve estruturas que **não possuem** os campos do
gabarito, então o vazamento deixa de depender de quem escreve o template.

---

## 9.3 Realização da prova

O que a Etapa 4 acrescenta é o caminho do aluno: da tela de instruções até a
página final, com o servidor decidindo tudo o que importa.

### Os cinco modelos

| Modelo | Guarda | Por quê |
|---|---|---|
| `ExamAttempt` | a tentativa: quem, qual prova, quando começa e acaba | registro histórico próprio |
| `AttemptQuestion` | uma questão **como foi apresentada** a este aluno | posição na tela e token público |
| `AttemptOption` | uma alternativa como foi apresentada | idem, por alternativa |
| `Answer` | o que o aluno respondeu numa questão | um-para-um com `AttemptQuestion` |
| `AnswerOption` | quais alternativas ele marcou | ligação, nunca cópia de texto |

A tentativa copia a **apresentação**, não o conteúdo. Enunciado, texto da
alternativa e valor da questão continuam em `Question` e `QuestionOption`,
alcançados por chave estrangeira — a prova publicada é imutável, então não há
o que duplicar. O que ela deliberadamente **não** copia é `is_correct`: a
referência aponta para a `QuestionOption` e a leitura do gabarito acontece no
servidor, na correção.

`Answer` nasce só no primeiro autosave. Questão sem `Answer` significa não
respondida, e essa é a leitura mais honesta que o banco consegue dar.

### O que o banco garante sozinho sobre a tentativa

A service layer mantém situação e carimbos coerentes, mas ela só protege
quem passa por ela. Um shell de produção para consertar um caso pontual, uma
migration de dados ou um comando escrito às pressas escrevem com
`QuerySet.update`, que não chama `save()`, não chama `full_clean()` e não sabe
que `choices` existe.

| Constraint | Exige |
|---|---|
| `tentativa_numero_unico_por_aluno_e_prova` | `UNIQUE(student, exam, attempt_number)` |
| `uniq_tentativa_em_andamento` | `UNIQUE(student, exam)` **apenas** onde `status = IN_PROGRESS` |
| `tentativa_numero_pelo_menos_um` | `attempt_number >= 1` |
| `tentativa_prazo_posterior_ao_inicio` | `expires_at > started_at` |
| `tentativa_total_de_pontos_nao_negativo` | `total_points_snapshot >= 0` |
| `tentativa_nota_minima_entre_0_e_10` | `0 <= passing_score_snapshot <= 10` |
| `tentativa_status_e_timestamps_coerentes` | a situação e os dois carimbos contam a mesma história |
| `tentativa_envio_nao_anterior_ao_inicio` | `submitted_at IS NULL OR submitted_at >= started_at` |
| `tentativa_expiracao_nao_anterior_ao_inicio` | `expired_at IS NULL OR expired_at >= started_at` |
| `tentativa_situacao_conhecida` | `status IN (IN_PROGRESS, SUBMITTED, EXPIRED, RESET)` |

A coerência entre situação e carimbos, em detalhe:

```
IN_PROGRESS   submitted_at NULL        e  expired_at NULL
SUBMITTED     submitted_at PREENCHIDO  e  expired_at NULL
EXPIRED       submitted_at NULL        e  expired_at PREENCHIDO
RESET         sem exigência de formato
```

Uma tentativa `SUBMITTED` sem `submitted_at` não é só um registro feio: ela
**mente sobre quando o aluno entregou**, e toda pergunta posterior sobre prazo
fica sem resposta. Uma `IN_PROGRESS` com `submitted_at` aceitaria autosave
depois do envio, porque o serviço olha o status.

`RESET` fica de fora da exigência de formato **de propósito**. O reset
administrativo ainda não existe, e quando existir a decisão mais provável é
preservar o carimbo da tentativa anulada — apagá-lo destruiria justamente a
informação que explica por que a anulação foi necessária. Amarrar o formato
agora seria decidir, sem discussão, a regra de uma etapa futura.

`tentativa_situacao_conhecida` existe porque `choices` é validação de
formulário e o banco nunca ouviu falar dela. Um `UPDATE` direto gravaria
`HACKED` sem reclamar, e a partir daí a linha escaparia de toda regra escrita
em cima do enum: não estaria em andamento, não estaria encerrada, o comando de
expiração não a encontraria e ela ainda ocuparia uma das tentativas do aluno,
para sempre. A lista fica **literal** na migration, o que é desejado —
acrescentar uma situação passa a exigir migration nova, e essa migration é o
lugar certo para decidir o que a situação nova faz com os carimbos.

O que deliberadamente **não** virou constraint é `submitted_at <= expires_at`.
Uma requisição pode entrar dentro do prazo e só obter o lock da linha alguns
milissegundos depois dele; quem classifica esse caso é a service layer, com o
relógio do servidor, e ela já o transforma em `EXPIRED`. Uma check ali
recusaria uma linha que o próprio código produz e derrubaria o envio de um
aluno por causa de uma disputa de lock.

### Tokens públicos

`Question.id` e `QuestionOption.id` **nunca chegam ao navegador**. O aluno
recebe UUID4 gerados por tentativa:

```
Aluno A · Questão 10 → 550e8400-e29b-...
Aluno B · Questão 10 → 1f7c9d21-4a5e-...
```

Combinar "marque a alternativa X" não funciona: X não existe na tentativa do
colega, e o autosave o recusa como qualquer token inventado. Os tokens nascem
uma única vez, no início, e ficam gravados — gerar a cada request faria o F5
trocar todos eles e perder a ligação com as respostas já salvas.

A URL da tentativa usa o `public_id`, nunca a PK. Com PK sequencial, trocar o
número da URL seria o primeiro teste de qualquer aluno curioso.

### O servidor é a fonte da verdade do tempo

```python
expires_at = min(started_at + duração, exam.close_at)
```

Calculado **uma vez**, no início. Depois de gravado não é recalculado por
nada: nem se o administrador fechar a prova, nem se a duração mudar, nem no
primeiro request depois de uma pausa. Quem começa faltando vinte minutos tem
vinte minutos — a janela da prova vale para todos.

A tela recebe `remaining_seconds` e faz a contagem regressiva a partir dele.
Esse contador é **decorativo**: quem decide se ainda dá tempo de salvar é o
servidor, a cada requisição, comparando com o prazo que ele mesmo gravou.
Cada autosave bem-sucedido devolve `remaining_seconds` de novo, então o
contador se ressincroniza sozinho. Nada vindo do relógio do cliente é aceito.

### Autosave

`POST /aluno/tentativas/<public_id>/autosave/`, CSRF obrigatório — o endpoint
**não** é `csrf_exempt`. O corpo carrega apenas o token da questão, os tokens
das alternativas e o texto. Qualquer outro campo é ignorado porque não há
nada no código que o leia.

Cada gravação **substitui** a resposta inteira daquela questão. Apagar e
recriar, em vez de calcular diferença, é o que faz desmarcar funcionar: o
conjunto gravado passa a ser exatamente o que chegou, sem sobra.

| Tipo | Aceita |
|---|---|
| `SINGLE_CHOICE`, `TRUE_FALSE` | 0 ou 1 alternativa |
| `MULTIPLE_CHOICE` | lista, com duplicados normalizados |
| `SHORT_TEXT` | texto até 2.000 caracteres |
| `ESSAY` | texto até 20.000 caracteres |

O texto é gravado como foi digitado. A única normalização é `\r\n` → `\n`,
para que o limite não puna quem responde do Windows. Sem `strip` destrutivo:
numa dissertativa, o recuo de parágrafo é do autor.

### JavaScript é requisito funcional

**A prova não funciona com o JavaScript desativado.** Isso é uma limitação
conhecida e documentada, não um descuido.

O autosave é o **único** caminho pelo qual uma resposta chega ao servidor, e
ele é chamado por `fetch`. A rota de finalizar recebe o POST do formulário e
não lê nenhum campo de resposta dele — lê apenas o token CSRF. Um aluno sem
JavaScript preencheria a tela inteira e entregaria uma prova em branco.

Há ainda uma segunda barreira, independente da primeira: o botão visível é
`type="button"` e abre um modal de confirmação; o único `type="submit"` mora
dentro desse modal, que sem script nunca abre.

A tela exibe um `<noscript>` no topo, **antes da primeira questão**, dizendo
com todas as letras que as respostas não serão salvas e pedindo que o aluno
ative o JavaScript e recarregue antes de responder.

Ler os campos do formulário no envio criaria um segundo motor de resposta,
com sua própria validação de token, seu próprio tratamento de prazo e sua
própria chance de discordar do primeiro. A escolha foi assumir o requisito e
avisar, em vez de manter dois caminhos de escrita.

Nada disso afrouxa o servidor: prazo, matrícula, CSRF, validação de token e
recusa depois do envio continuam todos no Django, e **nenhum deles depende de
o script ter rodado**. O JavaScript entrega respostas; ele não autoriza nada.

**Requisito:** navegador atual com JavaScript habilitado.

### Envio e expiração

```
IN_PROGRESS ──envio voluntário──> SUBMITTED   submitted_at
            ──tempo acabou──────> EXPIRED     expired_at
```

Os dois campos nunca são preenchidos juntos. Marcar `submitted_at` numa
tentativa que expirou registraria um envio que não houve.

O envio voluntário **exige** as questões obrigatórias respondidas e responde
**409** com a lista do que falta, sem encerrar a tentativa — enquanto houver
tempo, o aluno volta e responde. A expiração **não** exige nada: o tempo
acabou, e barrar a expiração por falta de resposta deixaria a tentativa presa
em andamento para sempre.

Envio depois do prazo vira `EXPIRED`, não `SUBMITTED`. O envio voluntário não
vence o relógio.

Envio repetido é idempotente: não altera `submitted_at`, não mexe nas
respostas e não grava um segundo evento. O duplo clique e o F5 na página de
envio não podem ser punidos.

### Expiração preguiçosa e o comando

A expiração acontece sozinha no próximo request do aluno — abrir a tela ou
tentar salvar depois do prazo encerra a tentativa na hora. O que sobra são as
tentativas órfãs: a aba fechada, o notebook que dormiu, quem nunca mais
voltou.

```powershell
python manage.py expirar_tentativas [--lote 100] [--limite N] [--dry-run]
```

Ele **não tem regra própria**: chama `expire_attempt`, a mesma função que o
acesso web usa. Uma segunda implementação da expiração seria a forma mais
rápida de as duas discordarem sobre o que `EXPIRED` significa. Idempotente —
rodar duas vezes encerra 3 e depois 0.

### Concorrência

| Corrida | Proteção |
|---|---|
| dois cliques em "iniciar", dois aparelhos, duas abas | `select_for_update` na linha do **aluno** + `uniq_tentativa_em_andamento` |
| autosave em voo quando o aluno clica em finalizar | `select_for_update` na **tentativa**, nos dois caminhos |

No início, a trava é sobre o aluno e não sobre a prova: é o aluno que não pode
ter duas tentativas, e travar a prova poria a turma inteira em fila. A
constraint parcial é a rede embaixo disso — ela não depende de ninguém
lembrar de usar a transação certa.

Na corrida entre autosave e envio, os dois desfechos são aceitáveis: ou a
resposta entra e a prova é enviada com ela, ou o envio chega primeiro e o
autosave acorda com a tentativa encerrada e recusa com 409. O que não
acontece em desfecho nenhum é resposta gravada depois de `submitted_at`.

### Continuar em outro aparelho

A tentativa **não** é amarrada a IP, user-agent ou dispositivo. `ip_address` e
`user_agent` ficam gravados como evidência para auditoria, e nada no sistema
os usa para decidir se o aluno pode continuar — trocar de rede ou sair do
wi-fi para o 4G no meio da prova é legítimo e acontece o tempo todo no
celular. Fazer login em outro aparelho continua a mesma tentativa: mesmos
tokens, mesma ordem, mesmas respostas, mesmo `expires_at`.

Duas abas podem editar a mesma resposta, e o último autosave válido vence.
É aceitável para o MVP; depois do envio, nenhuma aba altera nada.

### Matrícula continua valendo durante a prova

Cada requisição reconfere matrícula ativa, acesso liberado e módulo ativo. Se
o acesso do aluno for bloqueado no meio da prova, o próximo request dele já
não encontra a tentativa — **404**, sem precisar derrubar a sessão.

### Fechar a prova durante uma tentativa

`CLOSED` bloqueia **novas** tentativas. Quem já está respondendo continua até
o `expires_at` gravado. Encerrar todo mundo no meio da prova seria uma decisão
administrativa grave demais para acontecer como efeito colateral de fechar a
prova; se um dia for preciso, será uma operação própria.

---

---

## 9.4 Correção, notas e aprovação

O que a Etapa 5 acrescenta é o outro lado da prova: o que acontece depois que
o aluno entrega.

### Duas dimensões que não se misturam

`ExamAttempt.status` responde **"o aluno ainda está fazendo?"** e continua com
os mesmos quatro valores da Etapa 4. `grading_status` responde **"já sabemos a
nota?"**. São perguntas independentes.

```
status           IN_PROGRESS · SUBMITTED · EXPIRED · RESET
grading_status   PENDING · AWAITING_REVIEW · GRADED
result           APPROVED · FAILED   (nulo enquanto não fecha)
```

Um campo único — `IN_PROGRESS/SUBMITTED/APPROVED` — obrigaria a perder uma das
duas informações no momento em que a prova fosse aprovada. Por isso `result`
nunca entra em `status`.

### Fluxo

```
SUBMITTED / EXPIRED
    │
    ▼
grade_objective_questions()      corrige tudo que a máquina sabe corrigir
    │
    ├── nenhuma pendência manual ──▶ GRADED · nota · APPROVED/FAILED
    │
    └── há dissertativa a ler ────▶ AWAITING_REVIEW
                                        │
                                 save_manual_grade()   (quantas vezes quiser)
                                        │
                                 finalize_grading()  ──▶ GRADED
```

A correção roda **fora** da transação do envio, de propósito. Se rodasse
dentro e falhasse, o rollback levaria junto a entrega do aluno — ele teria
clicado em finalizar e a prova voltaria a ficar aberta. A entrega é um fato
dele; a correção é trabalho do sistema, e `grade_objective_questions` é
idempotente, então uma falha ali é recuperável.

### Correção automática: tudo ou nada

| Tipo | Regra |
|---|---|
| `SINGLE_CHOICE` | conjunto exato → pontos completos; qualquer outro caso → zero |
| `TRUE_FALSE` | idem |
| `MULTIPLE_CHOICE` | **conjunto exato**: faltando uma → zero; com uma a mais → zero |
| `SHORT_TEXT`, `ESSAY` | manual |

Não há pontuação parcial, e isso é decisão de negócio. Meio ponto por acertar
metade de uma múltipla escolha premiaria quem marca tudo — que é exatamente a
estratégia que a regra de conjunto exato existe para desencorajar.

A comparação é de **conjuntos**, não de listas: com `randomize_options` ligado
cada aluno vê as alternativas em ordem diferente, e comparar sequências
reprovaria pela posição.

O gabarito é lido no servidor, de `QuestionOption.is_correct`. Ele nunca
esteve dentro da tentativa — `AttemptOption` guarda a referência e a posição,
nunca a resposta certa.

### Questão manual em branco vale zero automaticamente

Uma redação em branco não tem conteúdo para avaliar. Deixá-la pendente
obrigaria o administrador a abrir cada uma só para escrever `0`, e a fila de
correção viraria uma fila de cliques — pior, uma prova inteiramente em branco
ficaria eternamente `AWAITING_REVIEW` se ninguém lembrasse dela.

O que fica pendente é o que **tem texto**. Espaço e quebra de linha não contam
como conteúdo.

### Expirada também é corrigida

Uma tentativa `EXPIRED` passa pela correção como qualquer outra. O que ficou em
branco vale zero, e o resultado é um resultado. Tratar expirada como "sem
nota" deixaria o aluno num limbo permanente, sem explicação.

### A fórmula, e por que a precisão importa

```python
precise_score = obtained_points / total_points_snapshot * Decimal("10")
aprovado      = precise_score >= passing_score_snapshot
```

A comparação usa o valor **cheio**, antes de qualquer arredondamento visual:

```
nota matemática  7.996
nota exibida     8,00
nota mínima      8,00
resultado        REPROVADO
```

Se a comparação usasse o valor exibido, esse aluno seria aprovado e a tela
mostraria "8,00 · Aprovado" sem nada que revelasse o erro. Por isso
`final_score` é `DecimalField(decimal_places=6)` — guardar `8.00` destruiria
justamente o dígito que separa aprovado de reprovado.

O helper `nota_para_exibicao` existe para que o arredondamento more num lugar
só, longe de quem decide. Ele devolve **`str`**, e isso é deliberado: uma
string não pode ser comparada com a nota mínima por engano — a tentativa
levanta `TypeError` em vez de aprovar alguém em silêncio.

Tudo em `Decimal`, do primeiro ponto até a comparação. Com `float`, dez
questões de 0,1 ponto somariam `0.9999999999999999` e reprovariam quem acertou
tudo.

### O que o banco garante sobre a correção

| Constraint | Exige |
|---|---|
| `tentativa_correcao_situacao_conhecida` | `grading_status` dentro do enum |
| `tentativa_resultado_conhecido` | `result` nulo ou dentro do enum |
| `tentativa_nota_so_existe_se_corrigida` | nota, resultado e `graded_at` existem **se e somente se** `GRADED` |
| `tentativa_pontos_obtidos_nao_negativos` | as três somas ≥ 0 |
| `tentativa_nota_final_entre_0_e_10` | nota nula ou na escala |
| `tentativa_questao_pontos_ate_o_valor` | `awarded_points <= points_snapshot` |
| `tentativa_questao_pontos_coerentes_com_situacao` | questão corrigida tem nota; pendente não tem |

O teto por questão compara com `points_snapshot`, e não com `question.points`:
uma `CheckConstraint` enxerga apenas a própria linha, e essa é a razão prática
de o snapshot existir.

### `points_snapshot`

`AttemptQuestion.points_snapshot` copia o valor da questão no início. Hoje a
prova publicada é imutável e o valor coincidiria com `question.points`, mas a
nota de um aluno é registro histórico: "sobre quantos pontos esta questão foi
avaliada" não pode depender de nada que aconteça depois.

A migration `0007` preenche o campo nas tentativas que já existiam.
Deliberadamente **não** o torna obrigatório: um `NOT NULL` exigiria que toda
linha existente estivesse preenchida no instante do deploy, e um único
registro órfão faria a migration falhar em produção sem caminho óbvio de saída.

### Concorrência

`finalize_grading` e `save_manual_grade` fazem `select_for_update` na
`ExamAttempt`. Dois administradores clicando em "Finalizar" ao mesmo tempo
produzem **uma** finalização: o segundo encontra a tentativa já `GRADED` e
devolve o resultado existente, sem recalcular e sem gravar um segundo evento.

### Rotas

| URL | Método | Quem |
|---|---|---|
| `/admin-panel/correcoes/` | GET | ADMIN |
| `/admin-panel/correcoes/<uuid>/` | GET | ADMIN |
| `/admin-panel/correcoes/<uuid>/salvar/` | **POST** | ADMIN |
| `/admin-panel/correcoes/<uuid>/finalizar/` | **POST** | ADMIN |
| `/admin-panel/notas/` | GET | ADMIN |
| `/admin-panel/notas/<uuid>/` | GET | ADMIN |
| `/admin-panel/notas/exportar/` | GET | ADMIN |
| `/aluno/resultados/<uuid>/` | GET | dono da tentativa |

| Situação | Resposta |
|---|---|
| STUDENT numa tela administrativa | **403** — a área existe, falta permissão |
| resultado de outro aluno | **404** — a existência é que não se confirma |
| finalizar com questão manual sem nota | **409**, com a lista |
| GET numa rota de escrita | **405** |

### O que o aluno vê

| Situação | Tela |
|---|---|
| `PENDING` / `AWAITING_REVIEW` | "Sua avaliação está aguardando correção." Sem número nenhum |
| `GRADED` | Aprovado ou Reprovado |
| `GRADED` + `show_score_after_submission` | mais a nota e a nota mínima |
| `FAILED` | mais o `failure_message` da prova |
| `APPROVED` | mais "Certificado será disponibilizado em breve" |

**Não existe nota provisória.** Mesmo quando as objetivas já foram corrigidas e
o sistema sabe que o aluno tem 6 dos 10 pontos, a tela não diz isso: metade de
uma nota não é informação, e o aluno calcularia a própria aprovação com dados
incompletos.

`show_score_after_submission=False` esconde o **número**, não o resultado.
Esconder também "aprovado ou reprovado" tornaria a tela inútil.

O resultado **nunca** mostra alternativa correta, `is_correct`, explicação
interna nem comentário do avaliador. O contexto é montado a partir de campos
escalares da tentativa, então não existe objeto de questão nessa página para
vazar nada por descuido de template.

### Contrato do certificado (Etapa 6)

Decidido agora, implementado depois:

```
Aprovado → Certificado emitido → Enrollment.status = COMPLETED
                                 Enrollment.access_enabled = False
```

Concluir **um** módulo não bloqueia o aluno globalmente: com MOD1 concluído e
MOD2 ativo, ele continua acessando MOD2. O encerramento do acesso global,
quando todos os módulos estiverem concluídos, também fica para a Etapa 6 — e
não acontece sem certificado.

---

## 9.5 Senha do aluno

**Mudança de negócio da Etapa 5: quem define a senha do aluno é o
administrador, e o aluno não a altera.**

| Momento | O que acontece |
|---|---|
| Criação individual | o administrador digita a senha; é obrigatória |
| Importação em lote | cai na senha padrão do ambiente |
| Esqueceu a senha | **Resetar senha** na ficha do aluno |
| Aluno tentando `/alterar-senha/` | **403** |

`must_change_password` nasce `False` em todos os casos. Obrigar a troca
prenderia o aluno num formulário que ele não tem permissão de enviar.

O campo continua no modelo e continua valendo para **ADMIN**, que troca a
própria senha normalmente. O que ele não faz mais é comandar o fluxo do
STUDENT — se continuasse, um aluno antigo criado com a flag ligada seria
mandado para uma tela que agora responde 403, e voltaria para lá a cada
request. Um loop sem saída, em produção, para quem já existia.

### Garantias

- A senha entra por `set_password`; nenhum caminho atribui `user.password`
- `PasswordInput` **sem** `render_value`: depois de um erro de validação o
  campo volta vazio, em vez de reimprimir a senha no HTML da resposta
- Os validadores do Django valem também para a senha que o admin define
- A senha nunca entra em `AuditLog`, log, mensagem, HTML ou query string.
  `STUDENT_PASSWORD_RESET` grava apenas `{"redefinida": true}` — nem o hash,
  nem o comprimento, que ajuda quem tenta adivinhar e não ajuda quem investiga.
  A chave se chama `redefinida`, e não `password_reset`, porque o sanitizador
  da auditoria descarta por **substring**: qualquer chave contendo `password`
  vira `[REMOVIDO]`. O evento perderia o próprio conteúdo. A escolha foi
  renomear a chave, não abrir exceção na regra que protege a trilha inteira
- **Resetar derruba as sessões abertas com a senha anterior.** O hash entra no
  cálculo da chave de sessão do Django, então o request seguinte do aluno já
  não está autenticado. Não há sistema paralelo de sessão; o comportamento vem
  do framework, e existe teste que o exercita de ponta a ponta

**A senha de importação precisa ser distribuída por canal seguro.** Ela é a
mesma para todos os alunos importados até que o administrador use "Resetar
senha".

---

## 9.6 Responsividade

### A causa real do overflow horizontal

O `<main>` do painel administrativo carregava, ao mesmo tempo:

```css
.container-fluid          { width: 100%; }
.cpo-conteudo--deslocado  { margin-left: 250px; }   /* ≥ 992px */
```

Margem fica **fora** da caixa mesmo com `box-sizing: border-box`. A caixa de
margem media `100% + 250px` — ou seja, a página inteira era 250px mais larga
que a viewport, em **toda** tela administrativa. Nenhum `.table-responsive`
resolvia, porque o estouro nascia acima dele.

### A correção

Lateral e conteúdo passaram a ser **irmãos num shell flex**:

```css
.cpo-shell--painel { display: flex; }
.cpo-lateral--fixa { flex: 0 0 250px; position: sticky; height: 100vh; }
.cpo-conteudo      { flex: 1 1 auto; min-width: 0; }
```

O `min-width: 0` é o detalhe que sustenta o resto: por padrão um item flex não
encolhe abaixo da largura natural do conteúdo, então uma tabela larga
empurraria a página em vez de rolar dentro do próprio cartão.

`body { overflow-x: clip }` existe como última linha de defesa, não como
correção. `clip` e não `hidden`: `hidden` cria contexto de rolagem e quebraria
o `position: sticky` do cronômetro da prova e da coluna de ações.

### Tabelas viram cartões abaixo de 768px

Um único markup para as duas formas. Cada `<td>` declara `data-rotulo` com o
nome da coluna; no celular esse rótulo vira o prefixo da linha, porque sem o
`<thead>` o valor sozinho não diz o que é.

Duplicar o template numa versão "tabela" e outra "cartões" duplicaria também as
condições de exibição — e um dia as duas discordariam.

A coluna de ações é `position: sticky; right: 0` no desktop e vira uma faixa de
botões no rodapé do cartão no celular. Em nenhuma largura o botão "Editar" fica
fora do alcance.

### Verificação

Os testes deste projeto garantem as **condições estruturais**: a regra que
causava o estouro não existe mais, `min-width: 0` está presente, toda tabela
tem versão de cartão, o hamburger existe em toda tela administrativa e o
offcanvas carrega os mesmos itens da lateral.

**Não há navegador na suíte**, então nenhum teste mede `document.body.scrollWidth`.
A confirmação visual em cada largura é trabalho de quem tem uma tela.

### Verdadeiro ou falso: o bug e a correção

O campo `resposta_verdadeira` usava `RadioSelect` com
`class="form-check-input"`. No Bootstrap 5 essa classe carrega
`margin-left: -1.5em`, pensada para cancelar o `padding-left: 1.5em` que o
container `.form-check` fornece. O `RadioSelect` do Django não gera esse
container — ele gera `<div><label><input> Texto</label></div>`.

Sem o pai, a margem negativa puxava cada radio 1,5em para **fora** da própria
caixa: círculo deslocado, rótulo escorregando por cima do anterior, texto de
ajuda atravessando os campos.

A correção não foi trocar a classe por outra: foi parar de usar a renderização
genérica. O template desenha as duas opções com `.cpo-vf`, feito para
exatamente duas escolhas de texto fixo, com `<label>` envolvendo o input — assim
clicar em qualquer ponto da linha marca a opção, o que importa no celular.

O backend já estava correto e continua sendo a fonte da verdade: os dois textos
são criados pelo serviço, e qualquer alternativa que venha no POST é ignorada
quando o tipo é `TRUE_FALSE`.

## 9.7 Certificados

**Um certificado é um documento, não uma consulta.** Essa frase decide quase
tudo neste app.

### Snapshots: por que o texto é copiado

`Certificate` guarda `student_name_snapshot`, `module_name_snapshot`,
`exam_title_snapshot` e `institution_name_snapshot`. São cópias, feitas no
momento da emissão.

O motivo: se o módulo "Módulo 1" for renomeado para "Formação Básica" no ano que
vem, um certificado emitido hoje **não pode mudar de texto**. Renderizar a
partir dos dados vivos faria o documento se reescrever sozinho depois de
assinado, que é exatamente o que um certificado não pode fazer. Existe teste
que renomeia o módulo e confirma que o certificado antigo continua igual.

`institution_name_snapshot` segue a mesma regra: certificados emitidos antes de
uma mudança de nome institucional preservam o nome que constava neles.

### Quem pode receber

Somente `grading_status=GRADED` **e** `result=APPROVED`. Tentativa pendente,
aguardando avaliador, reprovada ou anulada não gera certificado, e a validação
está em `issue_certificate` — não no template. O botão escondido é cortesia; o
controle é o serviço.

### Emissão é POST

Emitir muda estado acadêmico: conclui a matrícula e encerra o acesso ao módulo.
Isso não pode acontecer porque um pré-visualizador de link do WhatsApp, um
antivírus corporativo ou o próprio navegador resolveu buscar uma URL. `GET` na
rota de emissão responde **405**.

### Idempotência e concorrência

Dois cliques, dois toques no celular ou duas abas não podem produzir dois
documentos. Cada código extra seria um certificado autêntico e verificável a
mais circulando por engano.

A garantia vem em duas camadas:

| Camada | O que ela impede |
|---|---|
| `OneToOneField` | o banco recusa a segunda linha |
| `select_for_update()` | a segunda requisição espera, encontra o documento pronto e o devolve |

Sem o lock, duas requisições simultâneas leriam "não existe" ao mesmo tempo e a
segunda morreria com `IntegrityError` na cara do aluno. Há teste com duas
threads reais contra PostgreSQL confirmando: 1 certificado, 1 código, 1 evento
de auditoria.

O serviço devolve `(certificado, emitido_agora)`. O segundo valor é `False`
quando o documento já existia — quem chama escolhe a mensagem sem comparar
datas.

### Efeito na matrícula

Na mesma transação da emissão:

```
Enrollment.status        = COMPLETED
Enrollment.access_enabled = False
```

`complete_enrollment` ganhou o parâmetro `encerrar_acesso`, com padrão `False`
para não mudar o comportamento de quem já a chamava. A emissão passa `True`.

Consequências, todas com teste:

- o módulo concluído sai de `Enrollment.objects.liberadas()` e deixa de dar acesso
- **outro módulo ativo continua funcionando** — concluir um não encerra o curso
- o `User` **não** é desativado: o aluno precisa continuar entrando para baixar
  o certificado de novo, consultar resultados e cursar outros módulos
- a lista de certificados não depende da matrícula — perder o acesso ao módulo
  não pode significar perder o documento que comprova tê-lo concluído

### Código de verificação

`UUIDField(default=uuid.uuid4, unique=True, editable=False)`.

UUID4 porque o código entra em QR Code impresso e em URL pública sem
autenticação. Um id sequencial, ou qualquer valor derivado do aluno, deixaria a
coleção inteira enumerável para quem tivesse um único certificado. Há teste
verificando versão 4, variante RFC 4122 e distância astronômica entre dois
códigos — um contador disfarçado de UUID daria distância 1.

### PDF

`A4 landscape`, uma página, gerado sob demanda. **Nada é guardado em disco nem
no banco.** O documento é determinístico e os dados de origem são imutáveis;
guardar milhares de PDFs custaria espaço sem acrescentar nada.

**ReportLab, e não WeasyPrint.** WeasyPrint produz um resultado bonito a partir
de CSS, mas exige Pango, Cairo e GDK-Pixbuf — bibliotecas de sistema, não
wheels. Numa t3.small que não tem nada disso e num ambiente de desenvolvimento
Windows, seriam dois caminhos de instalação diferentes e frágeis para um
documento de uma página com dez linhas de texto.

As fontes são as Type 1 padrão embutidas no próprio formato PDF (Helvetica,
Times, Courier). **Nenhum arquivo de fonte precisa existir no servidor**, e há
teste confirmando que o PDF não carrega `/FontFile`.

**A nota não aparece no certificado.** O documento atesta conclusão; a nota
pertence ao resultado acadêmico.

Nome longo não quebra o layout: a fonte é reduzida até caber, com piso. Um
certificado com o nome cortado não serve.

### Nome do arquivo

Montado por **lista branca** (`[A-Za-z0-9]`), não por filtro de proibidos.

`Content-Disposition` é delimitado por CRLF: um nome de aluno contendo quebra de
linha permitiria injetar cabeçalhos na resposta. Filtrar caracteres proibidos é
uma corrida que se perde; aqui só passa o que está explicitamente permitido. Há
teste com `Joao"\r\nSet-Cookie: admin=1`.

### QR Code

Aponta para `SITE_URL + reverse("certificates:validate", ...)`. Nunca uma string
escrita à mão, nunca IP, nunca `localhost`, nunca `http`.

Este é o único erro desta etapa **sem conserto**: um certificado impresso com QR
apontando para `127.0.0.1` nasce inútil, e refazer significa reimprimir e
redistribuir todos os documentos já entregues. Há teste dedicado.

### Validação pública

`/certificados/validar/<uuid>/` — sem autenticação, somente leitura.

| Situação | Resposta |
|---|---|
| existe e ativo | 200, "Certificado válido" + dados |
| existe e revogado | 200, "Certificado revogado" + os mesmos dados |
| não existe | 404 |

Revogado continua mostrando nome, módulo e data de propósito: quem está com o
papel na mão precisa saber que **aquele** documento caiu. Uma página genérica de
"inválido" deixaria a dúvida de ter digitado errado.

**O que a página nunca mostra**, com teste item a item: e-mail, nota, respostas,
gabarito, número da tentativa, IP, user-agent, id interno, e **o motivo da
revogação** — que é nota administrativa e viraria acusação exposta a qualquer
pessoa com o código.

A view monta o contexto com campos escalares, um a um. Passar o objeto inteiro
daria ao template acesso a `certificado.attempt` e, por ali, ao aluno, às
respostas e à nota — numa página sem autenticação.

### Revogação

`POST` com CSRF, motivo obrigatório, somente ADMIN.

**Nunca apaga.** O código antigo continua consultável e passa a responder
"revogado" — quem recebeu o documento em papel precisa conseguir descobrir que
ele deixou de valer, e isso é impossível se o código simplesmente desaparecer.

**Revogar não reativa a matrícula.** Uma revogação pode vir de erro
administrativo, fraude ou correção documental, e cada uma pede um encaminhamento
acadêmico diferente.

Certificado revogado **não gera PDF**, nem para o aluno (409) nem para o
administrador. Entregar o arquivo de um documento sem validade seria entregar
algo que parece válido: a pessoa imprime e apresenta sem nunca conferir o QR. Os
dados históricos continuam na tela de detalhe administrativa.

### Auditoria

`CERTIFICATE_ISSUED` e `CERTIFICATE_REVOKED` são obrigatórios: os dois mudam o
que a instituição afirma sobre uma pessoa.

**Download não gera evento**, de propósito. Um certificado carrega QR Code e
pode ser aberto por leitor, robô ou pré-visualizador de link; auditar cada
acesso encheria a trilha de ruído e esconderia os eventos que importam.

A metadata guarda `module_id`, `module_code`, `attempt_number` e
`certificate_status`. Nunca o PDF, a imagem do QR, respostas, gabarito, e-mail
ou o próprio código de verificação.

### Django Admin

`Certificate` é somente leitura ali: sem adicionar, sem alterar, sem excluir,
todos os campos em `readonly`.

Um certificado criado pelo Django Admin nasceria sem passar por
`issue_certificate` — sem validar aprovação, sem concluir a matrícula, sem
registro na auditoria. E um status alterado à mão produziria o estado que a
página pública lê como verdade, sem que ninguém tenha decidido isso. A interface
oficial é `/admin-panel/certificados/`.

### Rotas

| Rota | Quem | Método |
|---|---|---|
| `/certificados/validar/<uuid>/` | qualquer pessoa | GET |
| `/aluno/certificados/` | dono | GET |
| `/aluno/certificados/emitir/<public_id>/` | dono | POST |
| `/aluno/certificados/<uuid>/baixar/` | dono | GET |
| `/admin-panel/certificados/` | ADMIN | GET |
| `/admin-panel/certificados/<id>/` | ADMIN | GET |
| `/admin-panel/certificados/<id>/baixar/` | ADMIN | GET |
| `/admin-panel/certificados/<id>/revogar/` | ADMIN | POST |

Certificado ou tentativa de outro aluno responde **404**, não 403: 403
confirmaria que aquele código existe.

### `template_version`

Nasce em `1`. Quando o desenho mudar, os certificados já emitidos continuam
apontando para a versão com que foram gerados — em vez de reimprimir um
documento antigo com a cara nova.

---

## 9.8 Domínio, HTTPS e a camada de borda

**Domínio oficial:** <https://cpoadsum.nexeeo.com>

### Arquitetura

```
Internet :443 (TLS)
      ↓
Nginx Proxy Manager        container Docker, versão fixada
      ↓ HTTP, rede cpo-edge (172.30.0.0/24)
Nginx nativo :8080         ACL: só 127.0.0.1 e 172.30.0.0/24
      ↓
Gunicorn                   unix socket
      ↓
PostgreSQL                 127.0.0.1:5432
```

**Docker existe só para o NPM.** Django, Gunicorn, PostgreSQL e o Nginx nativo
continuam nativos no host. Não há Dockerfile da aplicação, e não deve haver: a
arquitetura que já estava estável não foi reescrita para ganhar HTTPS.

A versão do NPM é **fixada** no Compose, não `latest`. Com `latest`, um
`docker compose pull` de rotina viraria uma atualização não planejada da borda
inteira.

### O laço de redirecionamento, e por que ele não acontece

Este é o detalhe que quebra instalações de dois proxies.

O `/etc/nginx/proxy_params` da distribuição faz:

```nginx
proxy_set_header X-Forwarded-Proto $scheme;
```

Entre o NPM e o nginx nativo o esquema real é `http`. Repassar `$scheme`
**apagaria** o `https` que o NPM enviou. Com `SECURE_SSL_REDIRECT=True`, o
Django responderia 301 para HTTPS em toda requisição, e o NPM entregaria de
volta como HTTP: laço infinito.

A correção não foi editar o `proxy_params` compartilhado. O site da aplicação
**deixou de incluí-lo** e escreve os quatro cabeçalhos à mão, com um `map`:

```nginx
map $http_x_forwarded_proto $cpo_forwarded_proto {
    default  $scheme;    # não veio, ou veio lixo: use a conexão real
    "https"  "https";    # veio do proxy da frente: preserve
    "http"   "http";
}
```

Repetir `proxy_set_header` com o mesmo nome no mesmo nível **não substitui** — o
nginx enviaria o cabeçalho duas vezes.

Confiar no cabeçalho só é seguro porque a porta 8080 não aceita cliente fora do
loopback e da rede `cpo-edge`.

### Por que o backend escuta em `:8080` e não em `172.30.0.1:8080`

Amarrar o `listen` ao IP do gateway parece mais seguro, mas aquele endereço só
passa a existir depois que o Docker cria a rede. No boot, o nginx pode subir
antes do Docker: o bind falharia e o serviço inteiro não iniciaria.

A restrição fica na ACL do próprio nginx:

```nginx
allow 127.0.0.1;
allow 172.30.0.0/24;
deny  all;
```

O Security Group não expõe 8080, mas confiar só nele deixaria o backend aberto
para qualquer outro processo ou container desta máquina. Origem fora da faixa
recebe **403** — verificado.

### HSTS

`SECURE_HSTS_INCLUDE_SUBDOMAINS` e `SECURE_HSTS_PRELOAD` passaram a ser
parametrizáveis com padrão **False**.

`includeSubDomains` a partir de `cpoadsum.nexeeo.com` alcançaria qualquer
subdomínio abaixo dele, e `preload` é porta de sentido único: uma vez na lista
embutida dos navegadores, sair leva meses e depende de terceiro. O domínio
pertence a uma zona que não é inteiramente nossa.

O HSTS fica sob controle do Django, e **não** do NPM. Duas camadas emitindo o
mesmo cabeçalho com valores diferentes é uma divergência que ninguém percebe até
o dia em que percebe.

### Painel do NPM

Publicado em `127.0.0.1:81`, nunca em `0.0.0.0:81`. O acesso é por túnel SSH:

```powershell
ssh -L 8181:127.0.0.1:81 cpo-aws
```

O painel controla os certificados de toda a borda; expô-lo à Internet seria pôr
a chave junto da fechadura.

### Renovação

Quem renova é o próprio NPM, por timer interno. **Não instalar Certbot no host:**
dois clientes ACME disputando o mesmo domínio levam a rate limit da Let's
Encrypt.

### Rollback

`/usr/local/sbin/cpo-rollback-borda` para o NPM, devolve o nginx nativo para a
porta 80, testa a configuração e recarrega. A aplicação volta em HTTP direto.
Nada do NPM é apagado.

O detalhe operacional completo — comandos, backup do NPM, conferência do
certificado — está em `/etc/cpo-provas/OPERACAO.md` no servidor.

---

## 9.9 Identidade da aplicação

**Nome:** CPO AD Brás Sumaré
**Subtítulo:** Sistema de avaliação para o Curso de Preparação de Obreiros

### Um lugar só

Antes da Etapa 7 o nome estava escrito à mão em **43 templates**, no formato
`{{ INSTITUTION_NAME }} Provas`. Trocar a identidade exigia editar arquivo por
arquivo, e bastava esquecer um para a aplicação aparecer com dois nomes ao mesmo
tempo — provavelmente na tela que ninguém abre com frequência.

Agora são três variáveis de ambiente, expostas pelo context processor:

| Variável | Onde aparece |
|---|---|
| `APP_NAME` | login, laterais, `<title>` de toda página |
| `APP_SUBTITLE` | login |
| `INSTITUTION_NAME` | **impresso no certificado** |

`INSTITUTION_NAME` é separado de `APP_NAME` de propósito. Um dia a interface pode
se chamar de um jeito e o documento oficial de outro; hoje coincidem, mas são
perguntas diferentes.

Há teste varrendo `templates/` atrás de `"CPO Provas"` e do padrão antigo, e
outro varrendo o código de aplicação atrás do nome literal. A centralização não
depende de ninguém lembrar dela.

### Certificados já emitidos não mudam

`institution_name_snapshot` guarda o nome que constava **naquele** documento.
Trocar `INSTITUTION_NAME` não reescreve nada, e **nenhuma migration toca nesse
campo** — há um teste varrendo `certificates/migrations/` para garantir.

---

## 9.10 Contas administrativas

`/admin-panel/administradores/`

### O que é um administrador

`User.role = ADMIN`. Nada mais.

Isso **não** significa `is_staff` nem `is_superuser`. Os dois pertencem ao Django
Admin, que aqui é ferramenta técnica de emergência. Uma conta criada pelo painel
nasce assim:

```
role                  ADMIN
is_active             True
is_staff              False
is_superuser          False
must_change_password  False
```

Os cinco valores são literais no serviço, e **nenhum deles existe no
formulário**. Não é questão de esconder: um POST forjado com `is_superuser=true`
chega a uma view que nunca lê esse nome. Um formulário que declarasse o campo e o
removesse no `clean()` seria uma defesa que depende de ninguém mexer no
`clean()`; não declarar é uma defesa que depende de ninguém *adicionar* o campo.

Por que importa tanto: `is_superuser=True` ignora todo o sistema de permissões do
Django — inclusive as verificações que ainda não foram escritas.

### Duas proteções que só podem viver no serviço

**Não bloquear a si mesmo.** O administrador se trancaria para fora no mesmo
instante.

**Não bloquear o último ativo.** O sistema ficaria sem ninguém para desbloquear
qualquer um, e a única saída seria linha de comando no servidor.

A contagem do último ativo roda dentro da transação, com `select_for_update`
sobre as contas administrativas ativas: sem o lock, dois administradores
bloqueando um ao outro ao mesmo tempo poderiam passar os dois pela verificação.

A interface esconde os botões correspondentes, mas isso é cortesia — há teste
enviando o POST direto para provar que a recusa está no serviço.

### Não existe excluir

Uma conta administrativa é **ativa** ou **bloqueada**. Apagar a linha quebraria a
leitura da trilha de auditoria, onde o autor de cada ação aponta para este
usuário. Há teste confirmando que a rota de exclusão não existe.

### Superusuário técnico

Contas com `is_superuser` ou `is_staff` aparecem na lista com a etiqueta
**Técnico**. A interface não concede nem remove esses atributos: eles foram
dados fora dela, por `createsuperuser`, e continuam sendo assunto de linha de
comando. Editar uma conta técnica pelo painel muda nome e e-mail, e mais nada —
com teste.

### Senha

`reset_admin_password` não pede a senha atual: quem executa é outro
administrador, que não a conhece e não deve conhecer. Para trocar a **própria**
senha existe `/alterar-senha/`, que exige a senha vigente.

Redefinir derruba as sessões abertas com a senha anterior. O hash entra no cálculo
da chave de sessão do Django, então o request seguinte já não está autenticado —
comportamento do framework, sem sistema paralelo de sessão.

Redefinir a **própria** senha derruba a própria sessão. A view avisa e manda para
o login, em vez de deixar o próximo clique cair lá parecendo erro.

### Auditoria

`ADMIN_USER_CREATED`, `ADMIN_USER_UPDATED`, `ADMIN_USER_BLOCKED`,
`ADMIN_USER_UNBLOCKED`, `ADMIN_PASSWORD_RESET`.

A edição registra apenas os **nomes** dos campos alterados
(`{"changed_fields": ["full_name"]}`), nunca os valores: copiar antigo e novo
levaria dado pessoal para a trilha sem necessidade.

O reset grava `{"redefinida": true}` — e não `{"password_reset": true}`. O
sanitizador de `audit.services` descarta por **substring** qualquer chave contendo
`password`, e ele está certo: é essa regra grosseira que garante que nenhuma chave
futura carregue segredo por descuido. Renomear a chave é mais barato, e mais
seguro, do que abrir exceção na única barreira que protege a trilha inteira.

---

## 9.11 Tentativas e anulação administrativa

`/admin-panel/tentativas/`

### A tela

Lista com filtro por aluno, módulo, prova, situação da tentativa, situação da
correção, resultado e período.

As três dimensões aparecem em **colunas separadas**, e não fundidas numa só: uma
tentativa pode estar enviada e sem correção; corrigida e reprovada; anulada
guardando a nota que tirou. Espremer as três perderia justamente o caso que o
administrador foi investigar.

O detalhe é a **única tela do sistema** que mostra gabarito, resposta do aluno,
IP e user-agent na mesma página. Há teste confirmando que nada disso aparece na
tela de resultado do aluno.

### O que "resetar" significa

Retirar a **validade** de uma tentativa, sem apagar o registro dela.

| Muda | Permanece |
|---|---|
| `status` → `RESET` | respostas e alternativas marcadas |
| `reset_at`, `reset_by`, `reset_reason` | pontos por questão, `final_score`, `result` |
| | `grading_status`, `submitted_at`, `expired_at` |

A tentativa anulada continua respondendo "o que este aluno escreveu, quanto tirou
e quando entregou" — que é a informação que justifica a anulação.

`reset_by` usa `SET_NULL`: o registro de que houve anulação precisa sobreviver ao
dia em que aquela conta administrativa deixar de existir. Com `PROTECT` o
histórico impediria a limpeza da conta; com `CASCADE` sumiria junto.

### Motivo obrigatório

Não aceita string vazia nem só espaços, limite de 1000 caracteres. Resetar anula
o trabalho de um aluno e pode revogar um certificado: seis meses depois, "por que
esta tentativa foi anulada?" precisa ter resposta no próprio registro, e não na
memória de quem clicou.

O motivo fica em `ExamAttempt.reset_reason` e **não** é duplicado no `AuditLog` —
duas versões do mesmo texto livre só existem para divergir depois. E **não**
aparece para o aluno: é nota administrativa, pode conter juízo sobre a conduta
dele, e não foi escrita para ele ler.

### O que o reset NÃO faz

**Não abre a prova.** Se a janela encerrou, se o módulo está inativo ou se a
matrícula não dá acesso, o aluno continua sem conseguir começar. Resetar libera o
**slot** da tentativa, e nada mais — um atalho que ignorasse a janela seria um
bypass escondido dentro de uma função chamada "reset".

A tela avisa explicitamente quando a janela já fechou:

> A tentativa foi resetada, porém a janela da prova está encerrada. O aluno não
> poderá iniciar novamente até que exista uma prova/janela válida.

### Numeração e limite

Já valia desde a Etapa 4, e a Etapa 7 confirmou com teste:

- `attempt_number` vem de `MAX + 1` sobre **todas** as tentativas — anular a 1 e
  refazer produz 1 `RESET` e 1 nova de número 2, nunca duas de número 1
- `que_contam_para_o_limite()` exclui `RESET` — a anulada não consome uma das
  `max_attempts`

### Cascata

**Certificado.** Um certificado `ACTIVE` daquela tentativa é revogado na mesma
transação, com motivo `"Tentativa resetada administrativamente."`. O código antigo
continua consultável e a página pública passa a informar a revogação.

**Matrícula.** Se a emissão havia deixado a matrícula `COMPLETED` com acesso
encerrado, o reset avalia a reativação. Ela acontece **só** quando as três
condições valem:

1. a matrícula está `COMPLETED` (foi a emissão que a fechou)
2. **não** resta outro certificado `ACTIVE` do mesmo aluno no mesmo módulo
3. o módulo está ativo

A condição 2 é a que importa: se restar outro certificado válido, o aluno ainda
tem comprovação de conclusão, e reabrir o módulo contradiria o documento que ele
tem na mão. Há teste montando exatamente esse cenário — duas provas do mesmo
módulo, dois certificados, anulação de um só.

A condição 3 impede que reativar uma matrícula ligue um módulo que a
administração desligou de propósito.

### Idempotência e concorrência

Resetar uma tentativa já anulada levanta `TentativaJaAnulada`, e a view responde
**409** — não um redirect silencioso. Quem clicou duas vezes precisa saber que a
segunda não fez nada; uma confirmação de sucesso o levaria a acreditar que anulou
de novo.

`select_for_update` sobre a tentativa serializa dois administradores
simultâneos. Há teste com duas threads reais contra PostgreSQL: 1 reset efetivo,
1 recusa, 1 evento `ATTEMPT_RESET`.

### O aluno

A tela de resultado de uma tentativa anulada mostra:

> Esta tentativa foi anulada administrativamente.
> Consulte a área do módulo para verificar se há uma nova tentativa disponível.

Nunca "Aprovado" nem "Reprovado", mesmo quando a nota existe — e ela existe, por
preservação. Dizer "Aprovado" numa tentativa anulada faria o aluno acreditar que
concluiu o módulo.

A URL da tentativa também deixa de aceitar escrita: `RESET` já está em
`ESTADOS_ENCERRADOS`, então autosave responde 409.

---

## 9.12 Trilha de auditoria

`/admin-panel/logs/`

### Somente leitura, e a ausência é a funcionalidade

Não existe rota de editar, apagar ou limpar. Uma trilha alterável pela mesma
interface que ela audita não serve para investigar nada: quem quisesse esconder
uma ação apagaria a linha logo depois de executá-la.

O modelo já bloqueia UPDATE e DELETE na camada de aplicação. A tela não tem nem
formulário que os tente, e há teste confirmando que `audit_log_delete`,
`audit_log_update` e `audit_log_clear` **não resolvem**.

### Filtros

Evento, ator, tipo de entidade e período. A busca por ator olha apenas nome e
e-mail — campos indexáveis. Varrer a metadata em texto livre custaria uma leitura
completa da tabela a cada digitação, e a trilha é a tabela que mais cresce no
sistema.

### Metadata é dado, nunca marcação

O JSON chega ao template já **achatado em pares** `(caminho, valor)` pela view, e
é renderizado com o escape padrão do Django.

Não existe `|safe` nessa página, e não pode passar a existir: parte da metadata
vem de campo que uma pessoa preencheu, e um `<script>` gravado meses atrás
executaria justamente na tela de quem está investigando aquele evento. Há teste.

Achatar na view, e não no template, também evita recursão no template — e evita
que alguém um dia marque o bloco inteiro como seguro "para melhorar a
formatação".

### Desempenho

Paginação de 50, ordenação por `-timestamp`, `select_related` no ator e no aluno.
Sem o `select_related`, uma página de 50 eventos faria até 100 consultas extras só
para escrever nomes. Há teste com `django_assert_max_num_queries`.

---

## 9.13 O modelo oficial do certificado

### O que a imagem de referência é, e o que ela não é

A identidade visual chegou como uma imagem: um certificado já preenchido, com um
módulo, uma data e um ano específicos.

Usá-la como fundo e escrever por cima produziria dois módulos, duas datas e duas
assinaturas no mesmo papel. E, no que sobrasse, um fundo rasterizado que imprime
borrado e pesa alguns megabytes por documento — num certificado que existe
justamente para ser impresso.

Então o desenho foi **refeito em vetor**, em `certificates/ornamentos.py`: as
ondas escuras nos cantos opostos, as fitas douradas com degradê, a trama de
linhas finas em verde acinzentado, a moldura dupla. Curvas de Bézier e
sombreamento nativo do PDF. Resultado: arquivo de ~19 KB, nítido em qualquer
ampliação, e nenhum asset binário para versionar.

Não é cópia pixel a pixel, e não tenta ser. A intenção é que o documento seja
reconhecível como o mesmo modelo.

### O logo

Não existe arquivo oficial da AD Brás Sumaré no repositório, e **recortar um
logo de baixa resolução da imagem de referência seria pior do que não ter**: um
logo pixelado num documento oficial chama mais atenção do que a ausência dele.

O lugar recebe uma composição tipográfica com as fontes padrão. Se um arquivo
aparecer em `static/img/certificado-logo.png`, ele passa a ser usado sem
alteração de código — o renderizador já procura por ele.

### Fontes

Helvetica, Times e Courier: as Type 1 embutidas no próprio formato PDF. Nenhum
arquivo de fonte precisa existir no servidor, e há teste confirmando que o PDF
não contém `/FontFile`. A rubrica da assinatura usa Times-BoldItalic — é uma
aproximação tipográfica declarada, e não uma assinatura digitalizada; nenhum
arquivo de assinatura foi inventado.

### Duas versões de desenho, e por que as duas continuam existindo

```
versão 1   layout provisório da Etapa 6
versão 2   modelo oficial (Etapa 8)
```

Cada certificado guarda a versão com que foi emitido e continua sendo desenhado
por ela. Não é apego: **um certificado da versão 1 não tem os campos que a versão
2 imprime.** Ele foi gravado antes de data do curso, local, carga horária e ano
existirem. Redesenhá-lo com o modelo novo produziria um documento oficial com
buracos no lugar da data — ou, pior, com valores inventados na hora da
renderização.

### O fluxo vertical é dinâmico

Nome próprio de quatro sobrenomes é comum. Quando o nome ocupa duas linhas, a
régua decorativa desce e o texto de conclusão desce junto.

Isso não foi previsto: a **primeira amostra gerada mostrou a segunda linha do
nome escrevendo por cima do divisor**. Posições fixas funcionavam para "Ana
Silva" e quebravam para "Maria Aparecida dos Santos de Oliveira Montenegro".

A quebra em duas linhas também é equilibrada — o corte procura o ponto que deixa
as duas mais parecidas. A quebra gulosa enchia a primeira linha até o limite e
jogava o resto na segunda, o que num nome em destaque parece erro.

---

## 9.14 Dados de certificado no módulo

Cinco campos administrativos em `Module`:

| Campo | Exemplo |
|---|---|
| `certificate_display_name` | Módulo I - Cooperadores e Diáconos |
| `certificate_course_dates_text` | 10 e 17 de outubro de 2026 |
| `certificate_location` | Igreja Sede |
| `certificate_workload_hours` | 8 |
| `certificate_year` | 2026 |

Ficam no módulo, e não em `settings`, porque **variam por turma**: o Módulo I
aconteceu numa data, na Sede, com oito horas; o Módulo II teve outras.

O que é institucional e fixo fica em `settings`, e vai para o `.env`:
`CERTIFICATE_COURSE_NAME`, `CERTIFICATE_SIGNATORY_NAME`,
`CERTIFICATE_SIGNATORY_TITLE`. Trocar de presidente não reescreve documentos já
assinados pelo anterior: cada certificado guarda a própria cópia dos três.

### Emissão sem os dados: recusa, não documento quebrado

Faltando data, local, carga horária ou ano, `issue_certificate` levanta
`DadosDoCertificadoIncompletos` e **nomeia apenas o que falta**.

A alternativa seria gerar "realizado em , em , com carga horária de horas" — e
essa versão quebrada só seria descoberta depois de o aluno baixar e imprimir. A
recusa acontece antes, e a tela do módulo mostra a mesma lista.

`certificate_display_name` é o único dos cinco que não bloqueia: ele tem
substituto natural no nome do módulo. Certificado com nome curto é melhor do que
nenhum certificado; certificado sem data não é.

### Validação em quatro camadas

Carga horária `> 0` e ano entre 2000 e 2100 são verificados no formulário, no
serviço, nos validators do modelo e em `CheckConstraint`. Não é desconfiança
redundante: cada camada cobre um caminho de escrita diferente — formulário,
serviço, `full_clean()`, `update()` em queryset ou SQL direto. E o valor errado
só aparece **depois de o certificado estar impresso**, quando não há mais
correção possível no papel entregue.

Os atributos `min` e `max` no HTML ajudam quem digita. Há teste que envia POST
direto com ano 3050 e carga horária 0 para provar que eles não são a defesa.

---

## 9.15 Compartilhamento do certificado

### O que é compartilhado

O **endereço público de validação**, nunca o PDF.

O arquivo, uma vez enviado, não volta atrás: se o certificado for revogado
amanhã, quem recebeu o PDF continua com um documento de aparência válida. O
endereço continua respondendo a verdade — e por isso é ele que vai no link.

### Dois canais, duas rotas

```
POST /aluno/certificados/<código>/compartilhar/whatsapp/  → 302 wa.me
POST /aluno/certificados/<código>/compartilhar/nativo/    → 204
```

Um caminho por canal, e não um parâmetro que o navegador preencha. O valor vai
para a trilha de auditoria: com rota fixa, o conjunto de canais possíveis é o
conjunto de rotas que existem.

`wa.me` sem número abre o seletor de contato do próprio aplicativo — o sistema
não precisa saber, nem guardar, para quem o aluno vai mandar.

### POST, e não link direto

O botão do WhatsApp é um formulário com CSRF e `target="_blank"`. Um link direto
para `wa.me` seria mais simples, mas o servidor precisa registrar o
compartilhamento, e **escrita não acontece por GET**: um GET que grava enche a
trilha toda vez que alguém passa o mouse sobre o link numa conversa ou que um
antivírus corporativo resolve buscar a URL.

### O gesto do usuário, e por que a mensagem já vem no HTML

`navigator.share()` só é aceito dentro do gesto que o disparou. Se o JavaScript
fosse buscar o texto no servidor primeiro, o Safari do iOS já teria considerado o
gesto encerrado e recusaria a folha de compartilhamento.

Então o servidor entrega o texto pronto num `data-` do botão, e o registro na
trilha vai **depois**, por `fetch` com `keepalive` — sem segurar nada. O
`keepalive` importa: a folha nativa tira a página de foco, e sem ele o navegador
cancelaria o pedido.

A regra não muda: **o texto é do servidor**. O navegador só repassa o que
recebeu. Há teste enviando `text=`, `mensagem=` e `message=` forjados no POST e
confirmando que nada disso aparece na mensagem.

### O evento diz "iniciado", e é só isso que ele pode dizer

`CERTIFICATE_SHARE_INITIATED`, metadata `{"channel": "whatsapp"}` ou
`{"channel": "native"}`.

Leia o nome com cuidado. O evento é gravado quando o aluno aperta o botão. A
partir dali o sistema **perde a visão**: quem entrega é o WhatsApp ou a folha de
compartilhamento do celular, nenhum dos dois devolve confirmação, e o aluno pode
fechar tudo sem enviar para ninguém.

Ou seja: **não** significa mensagem enviada, entregue nem lida. Significa que a
intenção existiu. Interpretar como entrega — num relatório, numa conversa, numa
cobrança — seria afirmar algo que este sistema não tem como saber. Há teste
confirmando que a metadata não carrega nenhuma chave sugerindo entrega.

Dois cliques produzem dois eventos, de propósito: compartilhar duas vezes com
pessoas diferentes é comportamento normal, e agrupá-los esconderia o segundo.

### As fronteiras

| Situação | Resposta |
|---|---|
| certificado de outro aluno | **404**, nunca 403 — 403 confirmaria que o código existe |
| anônimo | redirect para o login |
| administrador | 403 (não é dono de certificado) |
| GET | 405 |
| POST sem CSRF | 403 |
| certificado revogado | **409** |
| `url=`, `next=`, `redirect=` no POST | ignorados; o destino é montado no servidor |
| `verification_code` no POST | ignorado; o documento vem do caminho + dono da sessão |

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

### Decisões da Etapa 4

**Tokens por tentativa, e não por questão.** Um token de questão seria mais
simples e resolveria o vazamento de PK. Não resolveria a combinação entre
alunos: bastaria um passar "marque a X" para o colega. Custou uma tabela a
mais de cada lado (`AttemptQuestion`, `AttemptOption`) e fechou os dois
buracos de uma vez.

**A tentativa referencia, não copia.** Guardar o enunciado e o texto das
alternativas dentro da tentativa protegeria o histórico contra mudanças na
prova — mas a prova publicada já é imutável desde a Etapa 3, então não há o
que proteger. Copiar só criaria uma segunda cópia do conteúdo, incluindo uma
segunda cópia do gabarito na tabela que a tela do aluno mais consulta.

**Verdadeiro/Falso não é sorteado.** São duas alternativas de significado
fixo. Inverter a posição delas não esconde nada de ninguém — quem sabe a
resposta continua sabendo — e só torna a leitura mais lenta no celular. O
sorteio existe para dificultar a cola entre vizinhos, e isso não se aplica a
um par fixo de duas opções.

**A expiração acontece fora da transação que a descobriu.** Foi um bug real,
encontrado por teste: quando o autosave ou o início detectavam o prazo
vencido, expiravam a tentativa e em seguida levantavam a exceção de recusa —
e a exceção fazia rollback da própria expiração. A tentativa voltava a
`IN_PROGRESS` e ficava presa, bloqueada pela constraint parcial, até o comando
rodar. Agora a passagem do tempo é gravada em transação própria: ela é um
fato, e não uma consequência de a operação dar certo.

**Envio bloqueado responde 409, e não 200.** A prova volta inteira, com as
respostas já salvas no lugar e a lista do que falta. Um 200 faria a tela
parecer um envio bem-sucedido.

**A página final usa a mesma URL da prova.** Se fosse outra rota, voltar pelo
histórico do navegador devolveria a tela editável antiga e um POST dali
seguiria válido. Com uma rota só, qualquer requisição nova passa pela mesma
verificação.

**`RESET` já existe no enum, sem implementação.** O reset administrativo entra
em etapa futura, mas o valor nasce junto com a tabela — acrescentá-lo depois
exigiria uma migration mexendo em histórico já gravado. A regra combinada já
está documentada: para `max_attempts` contam todos os estados **exceto**
`RESET`; `attempt_number` **nunca** é reaproveitado.

**Django Admin somente leitura também para tentativas.** A resposta de um
aluno é o registro do que ele fez numa prova. Poder editá-la pelo Admin seria
uma porta lateral para alterar prova alheia sem passar por serviço nenhum, sem
trava de concorrência e sem entrar na trilha.

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

Acrescentado pelo motor de realização da prova:

- **IDs internos de `Question` e `QuestionOption` não são enviados ao
  navegador.** Todo identificador de resposta na tela do aluno é um UUID4
  gerado para aquela tentativa
- Tokens diferentes por tentativa: o token de um aluno não existe na tentativa
  do outro, então combinar "marque a alternativa X" não funciona
- A URL da tentativa usa `public_id`, nunca a PK — não há sequência para
  enumerar
- **O servidor é a fonte da verdade do tempo.** `expires_at` é calculado uma
  vez, no início, e nunca recalculado; `client_started_at`,
  `client_remaining_seconds` e afins não são aceitos para regra nenhuma
- Autosave com CSRF obrigatório — o endpoint não é `csrf_exempt`
- Token de outra questão, de outra tentativa ou inexistente recebe a mesma
  recusa: distinguir os casos transformaria o endpoint num oráculo
- Limites de texto aplicados no servidor; `maxlength` do HTML é conforto de
  digitação, não validação
- Tentativa de outro aluno responde **404**, nunca 403
- Matrícula reconferida a cada requisição: bloquear o acesso no meio da prova
  tem efeito no request seguinte
- Concorrência protegida por trava de banco em duas frentes: `select_for_update`
  na linha do aluno no início, e na tentativa no autosave e no envio
- A trilha de auditoria não guarda respostas, texto de redação, alternativas
  marcadas, tokens públicos nem senha da prova
- Autosave não gera evento de auditoria: seriam milhares de linhas duplicando
  o que a tabela de respostas já guarda melhor
- IP e user-agent são evidência, nunca autenticação: a tentativa não é
  amarrada a dispositivo, e trocar de rede no meio da prova é legítimo

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
