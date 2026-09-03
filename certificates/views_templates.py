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

import json

from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
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
    LIMITE_DA_FONTE,
    TIPOS_COM_REPETICAO,
    TIPOS_DA_PALETA,
    TIPOS_DE_IMAGEM,
    TextAlign,
)
from certificates.placeholders import (
    TAMANHO_MAXIMO_DO_TEXTO,
    opcoes_para_o_editor,
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
TIPOS_EDITAVEIS = list(TIPOS_DA_PALETA)

# Dados ficticios que o preview aceita pela query string, e o campo que cada
# um substitui. Lista fechada: o parametro escolhe DENTRO dela, e um nome de
# campo vindo do navegador nunca vira chave de dicionario por conta propria.
PARAMETROS_DO_PREVIEW = {
    "nome": FieldType.STUDENT_NAME,
    "curso": FieldType.COURSE_NAME,
    "modulo": FieldType.MODULE_NAME,
    "data": FieldType.COMPLETION_DATE,
    "datas": FieldType.COURSE_DATES,
    "local": FieldType.COURSE_LOCATION,
    "carga": FieldType.WORKLOAD,
    "ano": FieldType.YEAR,
}

# Teto de cada valor de preview. O texto entra num PDF, e o ReportLab escapa
# o que escreve — o teto e contra volume, e nao contra injecao: um parametro
# de 2 MB faria o servidor desenhar por minutos.
TAMANHO_DO_PARAMETRO = 120

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
    "wrap": True,
    "is_visible": True,
    "z_index": 10,
    "content": "",
}

# Ajustes por tipo no momento de soltar o elemento na arte. Nao sao posicoes
# — a posicao vem de onde o administrador soltou —, sao proporcoes que
# poupam o primeiro redimensionamento: um QR nasce quadrado, o ano nasce
# estreito e alto, um bloco de texto nasce largo.
PADRAO_POR_TIPO = {
    FieldType.STUDENT_NAME: {"width": 60, "height": 8, "font_size": 30, "bold": True},
    FieldType.QR_CODE: {"width": 9, "height": 12.7},
    FieldType.YEAR: {"width": 6, "height": 20, "rotation": 90, "font_size": 30},
    FieldType.VERIFICATION_CODE: {
        "width": 24,
        "height": 3,
        "font_size": 7,
        "min_font_size": 6,
        "font_family": "Courier",
    },
    FieldType.CUSTOM_TEXT: {
        "width": 60,
        "height": 10,
        "font_size": 13,
        "content": "Escreva aqui o texto do certificado.",
    },
}


def _rotulos(exemplos):
    """
    {tipo: rotulo} e {tipo__exemplo: valor de exemplo}.

    O exemplo e o que a caixa mostra no palco. Ver "Nome do aluno" escrito
    numa caixa nao ajuda a julgar tamanho nem quebra; ver "Joao da Silva de
    Oliveira" ajuda.
    """
    mapa = {}
    for tipo in FieldType.values:
        mapa[tipo] = FieldType(tipo).label
        if tipo not in TIPOS_DE_IMAGEM:
            mapa["{}__exemplo".format(tipo)] = exemplos.get(tipo, "")
    return mapa


def _elemento_para_a_tela(campo):
    """Um elemento do banco no formato que o editor consome."""
    return {
        "type": campo.field_type,
        "x": float(campo.x),
        "y": float(campo.y),
        "width": float(campo.width),
        "height": float(campo.height),
        "font_family": campo.font_family,
        "bold": campo.bold,
        "italic": campo.italic,
        "font_size": campo.font_size,
        "min_font_size": campo.min_font_size,
        "auto_fit": campo.auto_fit,
        "line_height": float(campo.line_height),
        "text_align": campo.text_align,
        "text_color": campo.text_color,
        "rotation": campo.rotation,
        "wrap": campo.wrap,
        "is_visible": campo.is_visible,
        "z_index": campo.z_index,
        "content": campo.content,
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
    """
    O editor visual.

    A pagina entrega tres coisas ao JavaScript, todas por `json_script`:
    os elementos ja gravados, os padroes de um elemento novo e a lista de
    variaveis. `json_script` escapa `<`, `>` e `&` — um texto personalizado
    que contenha `</script>` entra como dado, e nao fecha a tag.

    O que NAO e entregue: HTML. O editor monta os elementos no navegador a
    partir dos dados, e o servidor volta a receber dados na hora de salvar.
    Em nenhum momento uma marcacao produzida pela tela e gravada.
    """

    template_name = "admin_panel/certificate_templates/edit.html"
    secao = SECAO

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        modelo = _modelo_ou_404(self.kwargs["pk"])

        elementos = [
            _elemento_para_a_tela(campo)
            for campo in modelo.fields.all().order_by("z_index", "pk")
            if campo.field_type != FieldType.STATIC_IMAGE
        ]

        exemplos = valores_de_preview()
        paleta = []
        for tipo in TIPOS_DA_PALETA:
            padrao = dict(PADRAO_DO_CAMPO)
            padrao.update(PADRAO_POR_TIPO.get(tipo, {}))
            padrao["type"] = tipo
            paleta.append(
                {
                    "tipo": tipo,
                    "rotulo": FieldType(tipo).label,
                    "e_imagem": tipo in TIPOS_DE_IMAGEM,
                    "repetivel": tipo in TIPOS_COM_REPETICAO,
                    "exemplo": (
                        "" if tipo in TIPOS_DE_IMAGEM else exemplos.get(tipo, "")
                    ),
                    "padrao": padrao,
                }
            )

        contexto.update(
            {
                "modelo": modelo,
                "elementos": elementos,
                "paleta": paleta,
                "padroes": {item["tipo"]: item["padrao"] for item in paleta},
                "variaveis": [
                    {"chave": chave, "rotulo": rotulo, "exemplo": exemplo}
                    for chave, rotulo, exemplo in opcoes_para_o_editor(exemplos)
                ],
                "familias": list(FAMILIAS_PERMITIDAS),
                "alinhamentos": [list(par) for par in TextAlign.choices],
                "orientacoes": PageOrientation.choices,
                # Rotulos e exemplos num mapa so, para o editor nao precisar
                # procurar em duas listas para escrever o nome de uma caixa.
                "rotulos": _rotulos(exemplos),
                "tipos_de_imagem": sorted(TIPOS_DE_IMAGEM),
                "limite_da_fonte": {
                    "minimo": LIMITE_DA_FONTE[0],
                    "maximo": LIMITE_DA_FONTE[1],
                },
                "tamanho_maximo_do_texto": TAMANHO_MAXIMO_DO_TEXTO,
                "tipos_repetiveis": sorted(TIPOS_COM_REPETICAO),
                "editavel": modelo.editavel,
                "em_uso": modelo.esta_em_uso(),
                "pendencias": modelo.pendencias_para_ativar(),
                "dados_de_preview": [
                    {
                        "chave": chave,
                        "rotulo": FieldType(tipo).label,
                        "valor": exemplos.get(tipo, ""),
                    }
                    for chave, tipo in PARAMETROS_DO_PREVIEW.items()
                ],
                # Muda a cada gravacao e derruba o cache do navegador no
                # <embed> do preview. Sem isso o PDF antigo continuaria na
                # tela depois de salvar, e pareceria que nada mudou.
                "versao_do_preview": int(modelo.updated_at.timestamp()),
            }
        )
        return contexto


@require_POST
@admin_required
def template_save_elements(request, pk):
    """
    Grava a lista de elementos do editor visual.

    Recebe JSON, responde JSON — a tela nao recarrega ao salvar, e uma
    resposta HTML obrigaria o editor a interpretar pagina para descobrir se
    deu certo.

    O corpo e dado, e o servidor o trata como tal: cada elemento passa pela
    mesma validacao do formulario antigo. O que a tela mandou de HTML,
    estilo ou id nao e lido — nenhuma dessas chaves existe no que o servico
    aceita.
    """
    modelo = _modelo_ou_404(pk)

    try:
        corpo = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse(
            {"ok": False, "erros": ["Nao foi possivel ler os dados enviados."]},
            status=400,
        )

    elementos = corpo.get("elements") if isinstance(corpo, dict) else None
    if not isinstance(elementos, list):
        return JsonResponse(
            {"ok": False, "erros": ["Envie a lista de elementos."]}, status=400
        )
    if not all(isinstance(item, dict) for item in elementos):
        return JsonResponse(
            {"ok": False, "erros": ["Cada elemento precisa ser um objeto."]},
            status=400,
        )

    try:
        servicos.save_elements(
            modelo, elementos, actor=request.user, request=request
        )
    except servicos.ModeloNaoEditavel as erro:
        return JsonResponse(
            {
                "ok": False,
                "bloqueado": True,
                "erros": _mensagens(erro),
                "duplicar": reverse(
                    "admin_panel:certificate_template_duplicate",
                    kwargs={"pk": modelo.pk},
                ),
            },
            status=409,
        )
    except DomainError as erro:
        return JsonResponse({"ok": False, "erros": _mensagens(erro)}, status=400)

    modelo.refresh_from_db()
    return JsonResponse(
        {
            "ok": True,
            "elementos": len(elementos),
            "versao_do_preview": int(modelo.updated_at.timestamp()),
        }
    )


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
            "wrap": bool(request.POST.get("{}-wrap".format(tipo), "1")),
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

        pdf = render_from_snapshot(snapshot, self._valores(request))

        resposta = HttpResponse(pdf, content_type="application/pdf")
        baixar = request.GET.get("baixar") == "1"
        resposta["Content-Disposition"] = '{}; filename="preview-modelo.pdf"'.format(
            "attachment" if baixar else "inline"
        )
        resposta["Cache-Control"] = "private, max-age=0, no-store"
        resposta["X-Content-Type-Options"] = "nosniff"
        return resposta

    @staticmethod
    def _valores(request):
        """
        Os dados de exemplo, com o que o administrador digitou por cima.

        Serve para testar nome comprido antes de emitir de verdade: digitar
        "Maria Aparecida dos Santos de Oliveira Montenegro" no painel e ver
        se cabe e mais barato do que descobrir depois de imprimir.

        Cada parametro so alcanca o campo que a lista fechada permite. Os
        caracteres de controle saem: eles nao desenham nada e sujariam o
        fluxo do PDF.
        """
        valores = dict(valores_de_preview())
        for parametro, tipo in PARAMETROS_DO_PREVIEW.items():
            bruto = request.GET.get(parametro)
            if bruto is None:
                continue
            limpo = "".join(
                caractere
                for caractere in bruto[:TAMANHO_DO_PARAMETRO]
                if caractere == " " or caractere.isprintable()
            ).strip()
            if limpo:
                valores[tipo] = limpo
        return valores


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
