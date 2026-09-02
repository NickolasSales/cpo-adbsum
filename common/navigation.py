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
MENU_ADMIN = [
    {"nome": "Dashboard", "url": "admin_panel:dashboard", "secao": "dashboard"},
    {"nome": "Alunos", "url": "admin_panel:student_list", "secao": "alunos"},
    {
        "nome": "Administradores",
        "url": "admin_panel:admin_user_list",
        "secao": "administradores",
    },
    {"nome": "Modulos", "url": "admin_panel:module_list", "secao": "modulos"},
    {"nome": "Matriculas", "url": "admin_panel:enrollment_list", "secao": "matriculas"},
    {"nome": "Provas", "url": "admin_panel:exam_list", "secao": "provas"},
    {
        "nome": "Tentativas",
        "url": "admin_panel:attempt_list",
        "secao": "tentativas",
    },
    {"nome": "Correcoes", "url": "admin_panel:correction_list", "secao": "correcoes"},
    {"nome": "Notas", "url": "admin_panel:grade_list", "secao": "notas"},
    {
        "nome": "Certificados",
        "url": "admin_panel:certificate_list",
        "secao": "certificados",
    },
    # Entrada propria, e nao um submenu. O menu administrativo e uma lista
    # plana desde a Etapa 1, e abrir um nivel so para dois itens custaria
    # mais na navegacao por teclado e no celular do que economiza em espaco.
    {
        "nome": "Modelos de certificado",
        "url": "admin_panel:certificate_template_list",
        "secao": "modelos_certificado",
    },
    {"nome": "Logs", "url": "admin_panel:audit_log_list", "secao": "logs"},
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
