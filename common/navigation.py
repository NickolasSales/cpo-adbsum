"""Destino de navegacao por papel e estrutura do menu administrativo."""

from accounts.models import UserRole

DESTINO_POR_PAPEL = {
    UserRole.ADMIN: "admin_panel:dashboard",
    UserRole.STUDENT: "student:dashboard",
}


def url_do_painel(user):
    """
    Nome da rota do painel correspondente ao papel do usuario.

    Usuario anonimo ou com papel desconhecido cai no login: nao existe
    destino padrao permissivo.
    """
    if not getattr(user, "is_authenticated", False):
        return "accounts:login"
    return DESTINO_POR_PAPEL.get(user.role, "accounts:login")


# Itens ja implementados. A chave 'secao' casa com o atributo secao das views
# do painel e define qual item aparece marcado.
#
# A chave 'grupo' separa o menu em areas: o CPO academico, o Controle de
# Ministros e o que e da administracao do sistema como um todo. A lista
# continua plana — o template agrupa itens CONSECUTIVOS do mesmo grupo —,
# entao a ordem aqui e a ordem na tela, e um item novo entra junto dos
# outros da sua area.
GRUPO_CPO = "CPO"
GRUPO_MINISTROS = "Ministros"
GRUPO_ADMINISTRACAO = "Administração"

MENU_ADMIN = [
    {"nome": "Dashboard", "url": "admin_panel:dashboard", "secao": "dashboard", "grupo": GRUPO_CPO},
    {"nome": "Alunos", "url": "admin_panel:student_list", "secao": "alunos", "grupo": GRUPO_CPO},
    {"nome": "Modulos", "url": "admin_panel:module_list", "secao": "modulos", "grupo": GRUPO_CPO},
    {
        "nome": "Matriculas",
        "url": "admin_panel:enrollment_list",
        "secao": "matriculas",
        "grupo": GRUPO_CPO,
    },
    {"nome": "Provas", "url": "admin_panel:exam_list", "secao": "provas", "grupo": GRUPO_CPO},
    {
        "nome": "Tentativas",
        "url": "admin_panel:attempt_list",
        "secao": "tentativas",
        "grupo": GRUPO_CPO,
    },
    {
        "nome": "Correcoes",
        "url": "admin_panel:correction_list",
        "secao": "correcoes",
        "grupo": GRUPO_CPO,
    },
    {"nome": "Notas", "url": "admin_panel:grade_list", "secao": "notas", "grupo": GRUPO_CPO},
    {
        "nome": "Certificados",
        "url": "admin_panel:certificate_list",
        "secao": "certificados",
        "grupo": GRUPO_CPO,
    },
    # Entrada propria, e nao um submenu. Abrir um nivel so para dois itens
    # custaria mais na navegacao por teclado e no celular do que economiza em
    # espaco.
    {
        "nome": "Modelos de certificado",
        "url": "admin_panel:certificate_template_list",
        "secao": "modelos_certificado",
        "grupo": GRUPO_CPO,
    },
    # Controle de Ministros. Mensalidades, comprovantes e relatorios entram
    # aqui quando as telas existirem — nao antes, para nao haver link que
    # leva a lugar nenhum.
    {
        "nome": "Dashboard",
        "url": "admin_panel:minister_dashboard",
        "secao": "ministros_dashboard",
        "grupo": GRUPO_MINISTROS,
    },
    {
        "nome": "Ministros",
        "url": "admin_panel:minister_list",
        "secao": "ministros",
        "grupo": GRUPO_MINISTROS,
    },
    {
        "nome": "Administradores",
        "url": "admin_panel:admin_user_list",
        "secao": "administradores",
        "grupo": GRUPO_ADMINISTRACAO,
    },
    {"nome": "Logs", "url": "admin_panel:audit_log_list", "secao": "logs", "grupo": GRUPO_ADMINISTRACAO},
]

# Itens ainda nao implementados. Aparecem desabilitados e identificados pela
# etapa em que serao entregues, para que a interface nao prometa o que ainda
# nao existe. Cada item sai desta lista quando a sua tela entrar no ar, e
# nenhuma rota vazia e criada so para preencher o menu.
#
# Vazia desde a Etapa 7: todas as telas que o menu prometia existem. A lista
# permanece porque o mecanismo continua util na proxima vez que algo for
# anunciado antes de existir.
MENU_ADMIN_FUTURO = []
