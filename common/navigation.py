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
    {"nome": "Modulos", "url": "admin_panel:module_list", "secao": "modulos"},
    {"nome": "Matriculas", "url": "admin_panel:enrollment_list", "secao": "matriculas"},
    {"nome": "Provas", "url": "admin_panel:exam_list", "secao": "provas"},
]

# Itens ainda nao implementados. Aparecem desabilitados e identificados pela
# etapa em que serao entregues, para que a interface nao prometa o que ainda
# nao existe. Cada item sai desta lista quando a sua tela entrar no ar, e
# nenhuma rota vazia e criada so para preencher o menu.
MENU_ADMIN_FUTURO = [
    {"nome": "Correcoes", "etapa": "Etapa 5"},
    {"nome": "Notas", "etapa": "Etapa 5"},
    {"nome": "Certificados", "etapa": "Etapa 6"},
    {"nome": "Logs", "etapa": "Etapa 8"},
]
