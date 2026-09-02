"""
Telas administrativas dos modelos de certificado.

    /admin-panel/certificados/modelos/                  lista
    /admin-panel/certificados/modelos/novo/             criar
    /admin-panel/certificados/modelos/<id>/editar/      editor
    /admin-panel/certificados/modelos/<id>/preview.pdf  preview, ADMIN
    /admin-panel/certificados/modelos/<id>/arte/        a imagem, ADMIN

Duas decisoes que valem ser ditas antes do codigo.

O preview e um PDF de verdade
-----------------------------
Nao existe preview em HTML tentando parecer com o documento. O preview passa
pelo MESMO renderizador, com o mesmo sistema de coordenadas e a mesma
tipografia — ele e o PDF, exibido dentro da pagina. Um preview desenhado em
HTML seria uma segunda implementacao do layout, e as duas divergiriam no
primeiro nome comprido.

A arte nao tem URL publica
--------------------------
O arquivo enviado NAO e servido pelo Nginx nem mora dentro de STATIC_ROOT.
Ele e entregue por uma view com @admin_required, que le do disco e responde.
Servir o diretorio de media daria a cada arte uma URL adivinhavel fora de
qualquer controle de acesso, e a primeira coisa a aparecer ali seria a
identidade visual da instituicao em alta resolucao.
"""

from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from django.views.generic import ListView, TemplateView

from certificates import services_templates as servicos
from certificates.models import (
    CertificateTemplate,
    CertificateTemplateField,
    FieldType,
    PageOrientation,
    TemplateStatus,
)
from certificates.models.template import (
    FAMILIAS_PERMITIDAS,
    TIPOS_DE_IMAGEM,
    TextAlign,
)
from certificates.render import render_from_snapshot
from certificates.snapshot import montar_snapshot, valores_de_preview
from common.exceptions import DomainError
from common.mixins import admin_required
from common.navigation import MENU_ADMIN, MENU_ADMIN_FUTURO
from common.views import PainelAdminMixin

SECAO = "modelos_certificado"

# Tipos que o formulario de posicoes edita. STATIC_IMAGE fica de fora: ela
# tem arquivo proprio e um fluxo de upload proprio.
TIPOS_EDITAVEIS = [
    tipo for tipo in FieldType.values if tipo != FieldType.STATIC_IMAGE
]

# Valores iniciais de um campo que ainda nao foi configurado. Espalhados pela
# pagina de proposito: se todos nascessem no mesmo lugar, ativar tres campos
# de uma vez empilharia os tres um sobre o outro.
PADRAO_DO_CAMPO = {
    "x": 15,
    "y": 45,
    "width": 70,
    "height": 10,
    "font_family": "Helvetica",
    "bold": False,
    "italic": False,
    "font_size": 16,
    "min_font_size": 10,
    "auto_fit": True,
    "line_height": 1.2,
    "text_align": TextAlign.CENTER,
    "text_color": "#000000",
    "rotation": 0,
    "is_visible": True,
    "z_index": 10,
}


def _modelo_ou_404(pk):
    template = (
        CertificateTemplate.objects.select_related("created_by", "parent_template")
        .filter(pk=pk)
        .first()
    )
    if template is None:
        # 404, e nao 403: a tela ja exige ADMIN, e distinguir "nao existe" de
        # "existe mas nao posso" nao acrescenta nada aqui.
        raise Http404("Modelo nao encontrado.")
    return template


def _mensagens(erro):
    return getattr(erro, "mensagens", None) or [str(erro)]


def _conflito(request, template, mensagens):
    return render(
        request,
        "admin_panel/certificate_templates/conflito.html",
        {
            "modelo": template,
            "mensagens": mensagens,
            "secao": SECAO,
            "itens_menu": MENU_ADMIN,
            "itens_futuros": MENU_ADMIN_FUTURO,
        },
        status=409,
    )


# ---------------------------------------------------------------------------
# Lista
# ---------------------------------------------------------------------------


class TemplateListView(PainelAdminMixin, ListView):
    template_name = "admin_panel/certificate_templates/list.html"
    context_object_name = "modelos"
    paginate_by = 25
    secao = SECAO

    def get_queryset(self):
        consulta = CertificateTemplate.objects.select_related(
            "created_by"
        ).prefetch_related("modules")

        situacao = (self.request.GET.get("situacao") or "").strip()
        if situacao in TemplateStatus.values:
            consulta = consulta.filter(status=situacao)

        busca = (self.request.GET.get("q") or "").strip()
        if busca:
            consulta = consulta.filter(name__icontains=busca)

        return consulta.order_by("-status", "name", "-version")

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["situacoes"] = TemplateStatus.choices
        contexto["filtro_situacao"] = (self.request.GET.get("situacao") or "").strip()
        contexto["busca"] = (self.request.GET.get("q") or "").strip()
        contexto["total_geral"] = CertificateTemplate.objects.count()
        contexto["tem_padrao"] = CertificateTemplate.objects.filter(
            status=TemplateStatus.ACTIVE, is_global=True
        ).exists()
        return contexto


# ---------------------------------------------------------------------------
# Criacao e dados gerais
# ---------------------------------------------------------------------------


class TemplateCreateView(PainelAdminMixin, TemplateView):
    template_name = "admin_panel/certificate_templates/form.html"
    secao = SECAO

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["titulo"] = "Novo modelo de certificado"
        contexto["orientacoes"] = PageOrientation.choices
        contexto["valores"] = {
            "name": "",
            "description": "",
            "page_orientation": PageOrientation.LANDSCAPE,
            "page_width_mm": 297,
            "page_height_mm": 210,
            "is_global": False,
        }
        return contexto

    def post(self, request, *args, **kwargs):
        dados = {
            "name": request.POST.get("name") or "",
            "description": request.POST.get("description") or "",
            "page_orientation": request.POST.get("page_orientation") or "",
            "page_width_mm": request.POST.get("page_width_mm") or "",
            "page_height_mm": request.POST.get("page_height_mm") or "",
            "is_global": bool(request.POST.get("is_global")),
        }
        try:
            modelo = servicos.create_template(
                actor=request.user, request=request, **dados
            )
        except DomainError as erro:
            contexto = self.get_context_data(**kwargs)
            contexto["valores"] = dados
            contexto["erros"] = _mensagens(erro)
            return self.render_to_response(contexto, status=400)

        messages.success(
            request,
            "Modelo criado. Envie a arte de fundo e posicione os campos.",
        )
        return redirect("admin_panel:certificate_template_edit", pk=modelo.pk)


@require_POST
@admin_required
def template_update(request, pk):
    """Dados gerais: nome, descricao, pagina, padrao."""
    modelo = _modelo_ou_404(pk)
    try:
        servicos.update_template(
            modelo,
            name=request.POST.get("name") or "",
            description=request.POST.get("description") or "",
            page_orientation=request.POST.get("page_orientation") or "",
            page_width_mm=request.POST.get("page_width_mm") or "",
            page_height_mm=request.POST.get("page_height_mm") or "",
            is_global=bool(request.POST.get("is_global")),
            actor=request.user,
            request=request,
        )
    except servicos.ModeloNaoEditavel as erro:
        return _conflito(request, modelo, _mensagens(erro))
    except DomainError as erro:
        messages.error(request, str(erro))
    else:
        messages.success(request, "Dados do modelo atualizados.")
    return redirect("admin_panel:certificate_template_edit", pk=modelo.pk)


# ---------------------------------------------------------------------------
# Editor
# ---------------------------------------------------------------------------


class TemplateEditView(PainelAdminMixin, TemplateView):
    template_name = "admin_panel/certificate_templates/edit.html"
    secao = SECAO

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        modelo = _modelo_ou_404(self.kwargs["pk"])

        configurados = {
            campo.field_type: campo
            for campo in modelo.fields.all()
            if campo.field_type != FieldType.STATIC_IMAGE
        }

        # Uma linha por tipo, configurado ou nao. Mostrar a lista inteira e o
        # que permite ativar um campo sem procurar onde adiciona-lo.
        linhas = []
        for indice, tipo in enumerate(TIPOS_EDITAVEIS):
            campo = configurados.get(tipo)
            valores = dict(PADRAO_DO_CAMPO)
            if campo is not None:
                valores = {
                    "x": campo.x,
                    "y": campo.y,
                    "width": campo.width,
                    "height": campo.height,
                    "font_family": campo.font_family,
                    "bold": campo.bold,
                    "italic": campo.italic,
                    "font_size": campo.font_size,
                    "min_font_size": campo.min_font_size,
                    "auto_fit": campo.auto_fit,
                    "line_height": campo.line_height,
                    "text_align": campo.text_align,
                    "text_color": campo.text_color,
                    "rotation": campo.rotation,
                    "is_visible": campo.is_visible,
                    "z_index": campo.z_index,
                }
            else:
                # Desloca o padrao para baixo a cada tipo, para que ativar
                # varios de uma vez nao empilhe todos no mesmo ponto.
                valores["y"] = min(90, 8 + indice * 6)
            linhas.append(
                {
                    "tipo": tipo,
                    "rotulo": FieldType(tipo).label,
                    "configurado": campo is not None,
                    "e_imagem": tipo in TIPOS_DE_IMAGEM,
                    "valores": valores,
                }
            )

        contexto.update(
            {
                "modelo": modelo,
                "linhas": linhas,
                "familias": FAMILIAS_PERMITIDAS,
                "alinhamentos": TextAlign.choices,
                "orientacoes": PageOrientation.choices,
                "editavel": modelo.editavel,
                "em_uso": modelo.esta_em_uso(),
                "pendencias": modelo.pendencias_para_ativar(),
                # Muda a cada gravacao e derruba o cache do navegador no
                # <embed> do preview. Sem isso o PDF antigo continuaria na
                # tela depois de salvar, e pareceria que nada mudou.
                "versao_do_preview": int(modelo.updated_at.timestamp()),
            }
        )
        return contexto


@require_POST
@admin_required
def template_save_fields(request, pk):
    """
    Grava as posicoes de todos os campos de uma vez.

    O formulario manda a lista inteira; um tipo sem o marcador "ativo" e
    removido do modelo. Uma acao de exclusao separada por campo seria mais
    codigo para o mesmo efeito.
    """
    modelo = _modelo_ou_404(pk)

    campos = {}
    for tipo in TIPOS_EDITAVEIS:
        if not request.POST.get("{}-ativo".format(tipo)):
            continue
        campos[tipo] = {
            "x": request.POST.get("{}-x".format(tipo)),
            "y": request.POST.get("{}-y".format(tipo)),
            "width": request.POST.get("{}-width".format(tipo)),
            "height": request.POST.get("{}-height".format(tipo)),
            "font_family": request.POST.get("{}-font_family".format(tipo)),
            "bold": bool(request.POST.get("{}-bold".format(tipo))),
            "italic": bool(request.POST.get("{}-italic".format(tipo))),
            "font_size": request.POST.get("{}-font_size".format(tipo)),
            "min_font_size": request.POST.get("{}-min_font_size".format(tipo)),
            "auto_fit": bool(request.POST.get("{}-auto_fit".format(tipo))),
            "line_height": request.POST.get("{}-line_height".format(tipo)),
            "text_align": request.POST.get("{}-text_align".format(tipo)),
            "text_color": request.POST.get("{}-text_color".format(tipo)),
            "rotation": request.POST.get("{}-rotation".format(tipo)),
            "is_visible": bool(request.POST.get("{}-is_visible".format(tipo))),
            "z_index": request.POST.get("{}-z_index".format(tipo)),
        }

    try:
        servicos.save_fields(modelo, campos, actor=request.user, request=request)
    except servicos.ModeloNaoEditavel as erro:
        return _conflito(request, modelo, _mensagens(erro))
    except DomainError as erro:
        messages.error(request, str(erro))
    else:
        messages.success(request, "Campos salvos.")
    return redirect("admin_panel:certificate_template_edit", pk=modelo.pk)


@require_POST
@admin_required
def template_background(request, pk):
    modelo = _modelo_ou_404(pk)
    try:
        _, avisos = servicos.set_background(
            modelo,
            request.FILES.get("background"),
            actor=request.user,
            request=request,
        )
    except servicos.ModeloNaoEditavel as erro:
        return _conflito(request, modelo, _mensagens(erro))
    except DomainError as erro:
        messages.error(request, str(erro))
    else:
        messages.success(request, "Arte enviada.")
        for aviso in avisos:
            messages.warning(request, aviso)
    return redirect("admin_panel:certificate_template_edit", pk=modelo.pk)


# ---------------------------------------------------------------------------
# Arte e preview
# ---------------------------------------------------------------------------


class TemplateBackgroundView(PainelAdminMixin, TemplateView):
    """
    A imagem da arte, para o editor exibir.

    E uma view, e nao um arquivo servido pelo Nginx, porque assim ela herda o
    mesmo controle de acesso de todas as telas do painel. Ver
    PainelAdminMixin: aluno recebe 403, anonimo vai para o login.
    """

    secao = SECAO

    def get(self, request, *args, **kwargs):
        modelo = _modelo_ou_404(self.kwargs["pk"])
        if not modelo.background:
            raise Http404("Este modelo ainda nao tem arte.")
        try:
            arquivo = modelo.background.open("rb")
        except (FileNotFoundError, OSError):
            raise Http404("Arquivo da arte nao encontrado.")
        resposta = FileResponse(arquivo)
        # inline: a imagem e para ser exibida no editor, nao baixada.
        resposta["Content-Disposition"] = "inline"
        # A arte e material administrativo. Nenhum intermediario deve
        # guardar copia.
        resposta["Cache-Control"] = "private, max-age=0, no-store"
        resposta["X-Content-Type-Options"] = "nosniff"
        return resposta


class TemplatePreviewView(PainelAdminMixin, TemplateView):
    """
    PDF de teste, com dados ficticios.

    Nao cria Certificate, nao altera matricula, nao registra conclusao e nao
    grava nada. E uma renderizacao e nada mais — o mesmo renderizador do
    documento final, alimentado por valores de exemplo.
    """

    secao = SECAO

    def get(self, request, *args, **kwargs):
        modelo = _modelo_ou_404(self.kwargs["pk"])

        snapshot = montar_snapshot(modelo)
        snapshot["title"] = "Preview - {}".format(modelo.name)
        snapshot["author"] = "Preview"
        snapshot["creator"] = "Preview"

        pdf = render_from_snapshot(snapshot, valores_de_preview())

        resposta = HttpResponse(pdf, content_type="application/pdf")
        baixar = request.GET.get("baixar") == "1"
        resposta["Content-Disposition"] = '{}; filename="preview-modelo.pdf"'.format(
            "attachment" if baixar else "inline"
        )
        resposta["Cache-Control"] = "private, max-age=0, no-store"
        resposta["X-Content-Type-Options"] = "nosniff"
        return resposta


# ---------------------------------------------------------------------------
# Ciclo de vida
# ---------------------------------------------------------------------------


@require_POST
@admin_required
def template_activate(request, pk):
    modelo = _modelo_ou_404(pk)
    try:
        _, substituido = servicos.activate_template(
            modelo, actor=request.user, request=request
        )
    except DomainError as erro:
        return _conflito(request, modelo, _mensagens(erro))

    messages.success(request, "Modelo '{}' ativado.".format(modelo.name))
    if substituido is not None:
        messages.info(
            request,
            "O modelo padrao anterior '{}' foi arquivado.".format(substituido.name),
        )
    return redirect("admin_panel:certificate_template_edit", pk=modelo.pk)


@require_POST
@admin_required
def template_archive(request, pk):
    modelo = _modelo_ou_404(pk)
    try:
        servicos.archive_template(modelo, actor=request.user, request=request)
    except DomainError as erro:
        return _conflito(request, modelo, _mensagens(erro))

    messages.success(
        request,
        "Modelo '{}' arquivado. Os certificados ja emitidos continuam "
        "usando a configuracao com que foram gerados.".format(modelo.name),
    )
    return redirect("admin_panel:certificate_template_list")


@require_POST
@admin_required
def template_duplicate(request, pk):
    modelo = _modelo_ou_404(pk)
    copia = servicos.duplicate_template(modelo, actor=request.user, request=request)
    messages.success(
        request,
        "Criada a versao {} de '{}', em rascunho.".format(copia.version, copia.name),
    )
    return redirect("admin_panel:certificate_template_edit", pk=copia.pk)
