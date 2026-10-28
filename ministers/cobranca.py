"""
Cobranca Pix das mensalidades (Fase 2D).

    Minister + MinisterMonthlyFee + MinisterBillingSettings
                         |
              gerar_cobranca()  (snapshot)
                         |
                 MinisterFeeCharge
                 /       |       \\
           Copia e Cola  QR    Guia PDF

A mensalidade ja existente e a fonte da cobranca. Este modulo so COPIA o que
ela e a configuracao dizem no momento da geracao; o que sai na tela e no PDF
vem dessa copia, nunca do cadastro atual.

Quem recebe cobranca
--------------------
Mensalidade PENDENTE (vencida ou nao) com valor maior que zero. Paga, isenta
e cancelada nao recebem: nao ha o que pagar. Uma que ja tenha cobranca
vigente devolve a existente — gerar duas vezes nao cria a segunda.

Quando a cobranca e gerada
--------------------------
    geracao em massa (2B)    junto das mensalidades, na mesma transacao,
                             se a configuracao estiver pronta
    cadastro manual          logo depois da criacao, se estiver pronta
    acao "Gerar cobranca"    POST do administrador, uma mensalidade
    "Gerar cobrancas         POST do administrador, todas as pendentes sem
     pendentes"              cobranca — o caminho das mensalidades criadas
                             antes desta fase

NUNCA por GET, e nunca pelo portal: o ministro so le.

Sem configuracao pronta a mensalidade continua sendo criada (ela existe sem
cobranca desde a 2A), mas nenhuma cobranca nasce pela metade.

Imutabilidade
-------------
A cobranca gerada nao muda. Configuracao nova vale para cobranca nova. A
unica excecao e o administrador alterar o valor ou o vencimento de uma
mensalidade pendente (update_fee): a cobranca vigente passa a substituida,
continua no banco com o mesmo TXID, e uma nova e gerada com o valor novo —
dentro da mesma transacao da alteracao, e registrada na trilha.
"""

import secrets
import string

from django.db import IntegrityError, connection, transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record
from common.exceptions import DomainError
from ministers import pix
from ministers.models import (
    Minister,
    MinisterBillingSettings,
    MinisterFeeCharge,
    MinisterMonthlyFee,
    MonthlyFeeStatus,
    PixKeyType,
)

SEM_CONFIGURACAO = "Configure os dados de pagamento antes de gerar as cobranças."
COBRANCA_DESATIVADA = (
    "A cobrança por Pix está desativada na configuração de pagamento. "
    "Ative-a antes de gerar as cobranças."
)
TAMANHO_DAS_ORIENTACOES = 300

# Advisory lock da geracao em lote, separado do da 2B (7301).
NAMESPACE_DO_LOCK = 7302

PREFIXO_DO_TXID = "CPO"
ALFABETO_DO_TXID = string.ascii_uppercase + string.digits
TAMANHO_ALEATORIO_DO_TXID = 10

MESES = (
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
)


def nome_do_mes(data):
    """Portugues fixo no codigo: nao depende do locale do servidor."""
    return MESES[data.month - 1]


def competencia_por_extenso(data):
    """date(2026, 10, 1) -> 'Outubro/2026'."""
    return "{}/{}".format(nome_do_mes(data), data.year)


# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------


def configuracao():
    return MinisterBillingSettings.objects.filter(pk=1).first()


def validar_configuracao(dados):
    """
    Normaliza e confere os dados de pagamento. Devolve o dicionario pronto
    para gravar, ou DomainError com TODOS os problemas de uma vez.

    O teste final e montar um payload de verdade com esses dados: a
    combinacao (chave longa + descricao, por exemplo) tambem precisa caber.
    """
    erros = []
    tipo = dados.get("pix_key_type") or ""
    chave = ""
    if tipo not in PixKeyType.values:
        erros.append("Escolha o tipo da chave Pix.")
    elif not (dados.get("pix_key") or "").strip():
        erros.append("Informe a chave Pix.")
    else:
        try:
            chave = pix.normalizar_chave(tipo, dados.get("pix_key"))
        except pix.PayloadInvalido as erro:
            erros.append(str(erro))

    nome = pix.texto_do_br_code(dados.get("pix_receiver_name"))
    if not nome:
        erros.append("Informe o favorecido.")
    elif len(nome) > pix.TAMANHO_MAXIMO_NOME:
        erros.append(
            "O favorecido pode ter no máximo {} caracteres (o Pix não aceita mais). "
            "Use uma forma abreviada.".format(pix.TAMANHO_MAXIMO_NOME)
        )

    cidade = pix.texto_do_br_code(dados.get("pix_receiver_city"))
    if not cidade:
        erros.append("Informe a cidade.")
    elif len(cidade) > pix.TAMANHO_MAXIMO_CIDADE:
        erros.append(
            "A cidade pode ter no máximo {} caracteres.".format(pix.TAMANHO_MAXIMO_CIDADE)
        )

    orientacoes = "\n".join(
        " ".join(linha.split()) for linha in (dados.get("payment_instructions") or "").strip().splitlines()
    ).strip()
    if len(orientacoes) > TAMANHO_DAS_ORIENTACOES:
        erros.append(
            "As orientações podem ter no máximo {} caracteres.".format(TAMANHO_DAS_ORIENTACOES)
        )

    if not erros:
        try:
            pix.montar_payload(
                chave=chave, nome=nome, cidade=cidade, valor="1.00", txid="TESTE"
            )
        except pix.PayloadInvalido as erro:
            erros.append(str(erro))

    if erros:
        raise DomainError(erros)
    return {
        "pix_key_type": tipo,
        "pix_key": chave,
        "pix_receiver_name": nome,
        "pix_receiver_city": cidade,
        "payment_instructions": orientacoes,
        "is_active": bool(dados.get("is_active")),
    }


CAMPOS_DA_CONFIGURACAO = (
    "pix_key_type",
    "pix_key",
    "pix_receiver_name",
    "pix_receiver_city",
    "payment_instructions",
    "is_active",
)


def salvar_configuracao(dados, *, actor=None, request=None):
    novos = validar_configuracao(dados)
    with transaction.atomic():
        atual = MinisterBillingSettings.objects.select_for_update().filter(pk=1).first()
        if atual is None:
            atual = MinisterBillingSettings(pk=1)
            alterados = list(CAMPOS_DA_CONFIGURACAO)
            antes = {}
        else:
            alterados = [c for c in CAMPOS_DA_CONFIGURACAO if getattr(atual, c) != novos[c]]
            if not alterados:
                return atual, False
            antes = {c: getattr(atual, c) for c in alterados}
        for campo in CAMPOS_DA_CONFIGURACAO:
            setattr(atual, campo, novos[campo])
        atual.updated_by = actor if getattr(actor, "pk", None) else None
        atual.save()
        record(
            AuditEvent.MINISTER_BILLING_SETTINGS_UPDATED,
            request=request,
            actor=actor,
            entity_type="MinisterBillingSettings",
            entity_id=atual.pk,
            metadata={
                "changed_fields": alterados,
                "antes": antes,
                "depois": {c: novos[c] for c in alterados},
            },
        )
    return atual, True


def configuracao_pronta():
    """
    A configuracao, se der para gerar cobranca com ela; senao None.
    Revalida os dados gravados: uma linha editada por fora da tela nao
    produz payload quebrado.
    """
    atual = configuracao()
    if atual is None or not atual.is_active:
        return None
    try:
        validar_configuracao({c: getattr(atual, c) for c in CAMPOS_DA_CONFIGURACAO})
    except DomainError:
        return None
    return atual


def motivo_sem_configuracao():
    atual = configuracao()
    if atual is not None and not atual.is_active:
        return COBRANCA_DESATIVADA
    return SEM_CONFIGURACAO


def _exigir_configuracao():
    atual = configuracao_pronta()
    if atual is None:
        raise DomainError(motivo_sem_configuracao())
    return atual


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------


def vigentes():
    return MinisterFeeCharge.objects.filter(superseded_at__isnull=True)


def cobranca_vigente(mensalidade):
    return vigentes().filter(fee=mensalidade).first()


def anotar_cobranca(consulta):
    """Acrescenta `tem_cobranca` (Exists) sem uma consulta por linha."""
    return consulta.annotate(
        tem_cobranca=Exists(vigentes().filter(fee=OuterRef("pk")))
    )


def pode_ter_cobranca(mensalidade):
    return mensalidade.status == MonthlyFeeStatus.PENDING and mensalidade.amount > 0


def resumo():
    """
    O retrato de antes de uma geracao em lote, numa consulta: quantas
    mensalidades existem em cada situacao, quantas ja tem cobranca e
    quantas a geracao criaria. Somente leitura.
    """
    hoje = timezone.localdate()
    consulta = anotar_cobranca(MinisterMonthlyFee.objects.all())
    pendente = Q(status=MonthlyFeeStatus.PENDING)
    sem = Q(tem_cobranca=False)
    return consulta.aggregate(
        total=Count("id"),
        com_cobranca=Count("id", filter=Q(tem_cobranca=True)),
        elegiveis=Count("id", filter=pendente & sem),
        pendentes=Count("id", filter=pendente & Q(due_date__gte=hoje)),
        vencidas=Count("id", filter=pendente & Q(due_date__lt=hoje)),
        pagas=Count("id", filter=Q(status=MonthlyFeeStatus.PAID)),
        isentas=Count("id", filter=Q(status=MonthlyFeeStatus.EXEMPT)),
        canceladas=Count("id", filter=Q(status=MonthlyFeeStatus.CANCELLED)),
        pagas_sem_cobranca=Count("id", filter=Q(status=MonthlyFeeStatus.PAID) & sem),
    )


# ---------------------------------------------------------------------------
# Montagem do snapshot
# ---------------------------------------------------------------------------


def novo_txid(competencia):
    """
    CPO + AAAAMM + 10 caracteres aleatorios: CPO202610K7Q2M9XW4B.

    Aleatorio de proposito: nem sequencial (que revelaria quantas cobrancas
    existem) nem derivado de id, nome, CPF ou e-mail. 19 caracteres, dentro
    dos 25 do Pix, so A-Z e 0-9. A unicidade e garantida pelo banco.
    """
    sorteio = "".join(secrets.choice(ALFABETO_DO_TXID) for _ in range(TAMANHO_ALEATORIO_DO_TXID))
    return "{}{}{}".format(PREFIXO_DO_TXID, competencia.strftime("%Y%m"), sorteio)


def descricao_do_pix(competencia):
    """Texto curto que alguns bancos mostram ao pagador."""
    return "Mensalidade {}".format(competencia.strftime("%m %Y"))


def _snapshot(mensalidade, ministro, config, actor):
    """Uma cobranca NAO gravada, com todos os dados congelados."""
    txid = novo_txid(mensalidade.reference_month)
    payload = pix.montar_payload(
        chave=config.pix_key,
        nome=config.pix_receiver_name,
        cidade=config.pix_receiver_city,
        valor=mensalidade.amount,
        txid=txid,
        descricao=descricao_do_pix(mensalidade.reference_month),
    )
    return MinisterFeeCharge(
        fee_id=mensalidade.pk,
        txid=txid,
        reference_month_snapshot=mensalidade.reference_month,
        amount_snapshot=mensalidade.amount,
        due_date_snapshot=mensalidade.due_date,
        pix_key_snapshot=config.pix_key,
        pix_receiver_name_snapshot=config.pix_receiver_name,
        pix_receiver_city_snapshot=config.pix_receiver_city,
        payment_instructions_snapshot=config.payment_instructions,
        minister_name_snapshot=ministro.full_name,
        conamad_registration_snapshot=ministro.conamad_registration,
        field_registration_snapshot=ministro.field_registration,
        congregation_snapshot=ministro.congregation,
        position_snapshot=ministro.cargo_exibido,
        pix_payload=payload,
        generated_by=actor if getattr(actor, "pk", None) else None,
    )


def _auditar_geracao(cobranca, mensalidade, *, actor, request, **extra):
    record(
        AuditEvent.MINISTER_BILLING_GENERATED,
        request=request,
        actor=actor,
        entity_type="MinisterMonthlyFee",
        entity_id=mensalidade.pk,
        metadata={
            "minister_id": mensalidade.minister_id,
            "competencia": mensalidade.competencia_chave,
            "txid": cobranca.txid,
            "valor": str(cobranca.amount_snapshot),
            "vencimento": cobranca.due_date_snapshot.isoformat(),
            **extra,
        },
    )


# ---------------------------------------------------------------------------
# Geracao
# ---------------------------------------------------------------------------


def gerar_cobranca(mensalidade, *, actor=None, request=None):
    """
    Gera a cobranca de UMA mensalidade e devolve (cobranca, criada).

    Idempotente: com cobranca vigente, devolve a existente e criada=False.
    A linha da mensalidade fica travada durante a geracao, entao duas
    chamadas simultaneas produzem uma cobranca e uma leitura dela.
    Nao muda a situacao da mensalidade.
    """
    try:
        with transaction.atomic():
            atual = (
                MinisterMonthlyFee.objects.select_for_update(of=("self",))
                .select_related("minister")
                .get(pk=mensalidade.pk)
            )
            existente = cobranca_vigente(atual)
            if existente is not None:
                return existente, False
            if not pode_ter_cobranca(atual):
                raise DomainError(
                    "Só mensalidades pendentes recebem cobrança. Esta está {}.".format(
                        atual.get_status_display().lower()
                    )
                )
            config = _exigir_configuracao()
            cobranca = _snapshot(atual, atual.minister, config, actor)
            cobranca.save()
            _auditar_geracao(cobranca, atual, actor=actor, request=request)
    except IntegrityError:
        # So o TXID unico chega aqui (a mensalidade estava travada): uma
        # colisao de 10 caracteres aleatorios. Nada foi gravado.
        raise DomainError("Não foi possível gerar a cobrança agora. Tente novamente.")
    return cobranca, True


def gerar_para_novas(mensalidades, *, actor=None):
    """
    Cobrancas das mensalidades RECEM-CRIADAS pela geracao em massa, dentro
    da transacao dela. Duas consultas (configuracao e ministros) e um
    INSERT, qualquer que seja o tamanho do lote. Devolve quantas criou —
    zero quando a configuracao nao esta pronta.

    Sem auditoria aqui: quem chama (geracao.gerar) registra o numero no
    evento do lote.
    """
    config = configuracao_pronta()
    if config is None or not mensalidades:
        return 0
    ministros = Minister.objects.in_bulk({m.minister_id for m in mensalidades})
    novas = [
        _snapshot(m, ministros[m.minister_id], config, actor)
        for m in mensalidades
        if pode_ter_cobranca(m)
    ]
    MinisterFeeCharge.objects.bulk_create(novas)
    return len(novas)


def _travar_lote():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [NAMESPACE_DO_LOCK, 0])


def gerar_cobrancas_pendentes(*, actor=None, request=None):
    """
    Gera a cobranca de TODA mensalidade pendente que ainda nao tem uma.
    Devolve quantas criou.

    Tudo ou nada: uma transacao so. Se qualquer cobranca falhar, nenhuma
    fica gravada e o administrador recebe a mensagem — nao existe lote pela
    metade para conciliar depois. A configuracao e validada antes de tocar
    em qualquer linha.

    Concorrencia: o advisory lock poe duas geracoes em fila; as mensalidades
    escolhidas ficam travadas contra edicao, pagamento e cancelamento ate o
    fim; a constraint de uma vigente por mensalidade e a ultima barreira.
    Um evento consolidado na trilha, nao um por cobranca.
    """
    try:
        with transaction.atomic():
            _travar_lote()
            config = _exigir_configuracao()
            mensalidades = list(
                anotar_cobranca(
                    MinisterMonthlyFee.objects.select_for_update(of=("self",))
                    .select_related("minister")
                    .filter(status=MonthlyFeeStatus.PENDING, amount__gt=0)
                )
                .filter(tem_cobranca=False)
                .order_by("reference_month", "minister__full_name", "id")
            )
            novas = [_snapshot(m, m.minister, config, actor) for m in mensalidades]
            MinisterFeeCharge.objects.bulk_create(novas)
            if novas:
                por_competencia = {}
                for m in mensalidades:
                    chave = m.competencia_chave
                    por_competencia[chave] = por_competencia.get(chave, 0) + 1
                record(
                    AuditEvent.MINISTER_BILLING_BATCH_GENERATED,
                    request=request,
                    actor=actor,
                    entity_type="MinisterFeeCharge",
                    metadata={
                        "criadas": len(novas),
                        "por_competencia": por_competencia,
                        "valor_total": str(sum(m.amount for m in mensalidades)),
                    },
                )
    except IntegrityError:
        raise DomainError(
            "Outra operação gerou cobranças ao mesmo tempo. Nada foi gravado. "
            "Confira o resumo e tente novamente."
        )
    return len(novas)


def substituir_por_alteracao(mensalidade, *, actor=None):
    """
    Chamada por update_fee, DENTRO da transacao dela, depois de gravar o
    valor ou o vencimento novo. A cobranca vigente (se houver) passa a
    substituida; se a configuracao estiver pronta, nasce uma nova com os
    dados atuais. Devolve (antiga, nova) — qualquer um pode ser None.
    """
    antiga = cobranca_vigente(mensalidade)
    if antiga is None:
        return None, None
    MinisterFeeCharge.objects.filter(pk=antiga.pk).update(superseded_at=timezone.now())
    nova = None
    config = configuracao_pronta()
    if config is not None and pode_ter_cobranca(mensalidade):
        ministro = Minister.objects.get(pk=mensalidade.minister_id)
        nova = _snapshot(mensalidade, ministro, config, actor)
        nova.save()
    return antiga, nova
