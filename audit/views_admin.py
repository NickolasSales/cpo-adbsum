"""
Trilha de auditoria: leitura, e so isso.

    /admin-panel/logs/        listagem com filtros
    /admin-panel/logs/<id>/   um evento, com a metadata formatada

Nao existe rota de editar, apagar ou limpar, e a ausencia e a funcionalidade.
Uma trilha que pode ser alterada pela mesma interface que ela audita nao serve
para investigar nada: quem quisesse esconder uma acao apagaria a linha logo
depois de executa-la.

O modelo ja bloqueia UPDATE e DELETE na camada de aplicacao. Aqui nao ha nem
formulario que os tente.

Metadata e DADO, nunca marcacao
-------------------------------
O JSON e renderizado com o escape padrao do Django, item a item. Nada de
`|safe`: parte da metadata vem de campo que uma pessoa preencheu, e um
`<script>` gravado seis meses atras executaria na tela de quem esta
investigando exatamente aquele evento.
"""

from django.db.models import Q
from django.http import Http404
from django.views.generic import ListView, TemplateView

from audit.models import AuditEvent, AuditLog
from common.views import PainelAdminMixin

POR_PAGINA = 50


def _data_ou_none(texto):
    from datetime import date

    if not texto:
        return None
    try:
        ano, mes, dia = (int(p) for p in texto.split("-"))
        return date(ano, mes, dia)
    except (ValueError, TypeError):
        return None


def _achatar(valor, prefixo=""):
    """
    Transforma a metadata aninhada numa lista de (caminho, valor).

    O template renderiza pares de texto, e nao um objeto: assim nao ha
    recursao no template, e nao ha como um dia alguem marcar o bloco inteiro
    como seguro para "melhorar a formatacao".
    """
    if isinstance(valor, dict):
        linhas = []
        for chave, item in valor.items():
            caminho = "{}.{}".format(prefixo, chave) if prefixo else str(chave)
            linhas.extend(_achatar(item, caminho))
        return linhas
    if isinstance(valor, (list, tuple)):
        linhas = []
        for indice, item in enumerate(valor):
            linhas.extend(_achatar(item, "{}[{}]".format(prefixo, indice)))
        return linhas
    return [(prefixo or "valor", valor)]


class AuditLogListView(PainelAdminMixin, ListView):
    """
    Listagem paginada, do mais recente para o mais antigo.

    select_related no ator e no aluno: sem isso, uma pagina de 50 eventos
    faria ate 100 consultas extras so para escrever nomes.
    """

    template_name = "admin_panel/logs/list.html"
    context_object_name = "pagina"
    paginate_by = POR_PAGINA
    secao = "logs"

    def get_queryset(self):
        consulta = AuditLog.objects.select_related("actor", "student").order_by(
            "-timestamp", "-id"
        )

        g = self.request.GET
        evento = (g.get("evento") or "").strip()
        ator = (g.get("ator") or "").strip()
        entidade = (g.get("entidade") or "").strip()

        if evento in AuditEvent.values:
            consulta = consulta.filter(event=evento)
        if ator:
            # Busca so em campos seguros e indexaveis. Varrer a metadata em
            # texto livre custaria uma leitura completa da tabela a cada
            # digitacao, e a trilha e a tabela que mais cresce no sistema.
            consulta = consulta.filter(
                Q(actor__full_name__icontains=ator)
                | Q(actor__email__icontains=ator)
            )
        if entidade:
            consulta = consulta.filter(entity_type__iexact=entidade)

        inicio = _data_ou_none((g.get("de") or "").strip())
        fim = _data_ou_none((g.get("ate") or "").strip())
        if inicio:
            consulta = consulta.filter(timestamp__date__gte=inicio)
        if fim:
            consulta = consulta.filter(timestamp__date__lte=fim)

        return consulta

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        g = self.request.GET
        contexto["eventos"] = AuditEvent.choices
        contexto["entidades"] = (
            AuditLog.objects.exclude(entity_type="")
            .values_list("entity_type", flat=True)
            .distinct()
            .order_by("entity_type")
        )
        contexto["filtro_evento"] = (g.get("evento") or "").strip()
        contexto["filtro_ator"] = (g.get("ator") or "").strip()
        contexto["filtro_entidade"] = (g.get("entidade") or "").strip()
        contexto["filtro_de"] = (g.get("de") or "").strip()
        contexto["filtro_ate"] = (g.get("ate") or "").strip()
        contexto["tem_filtro"] = any(
            contexto[c]
            for c in (
                "filtro_evento",
                "filtro_ator",
                "filtro_entidade",
                "filtro_de",
                "filtro_ate",
            )
        )
        return contexto


class AuditLogDetailView(PainelAdminMixin, TemplateView):
    """Um evento, com a metadata achatada em pares de texto."""

    template_name = "admin_panel/logs/detail.html"
    secao = "logs"

    def get(self, request, *args, **kwargs):
        self.evento = (
            AuditLog.objects.select_related("actor", "student")
            .filter(pk=kwargs["log_id"])
            .first()
        )
        if self.evento is None:
            raise Http404("Registro nao encontrado.")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["evento"] = self.evento
        contexto["metadata"] = _achatar(self.evento.metadata or {})
        return contexto
