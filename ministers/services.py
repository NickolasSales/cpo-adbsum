"""
Regras do cadastro de ministros e do acesso ao portal.

Toda escrita passa por aqui: o cadastro manual, a edicao, a importacao e as
acoes de bloqueio. As views nao tocam no modelo diretamente, e nenhum campo
privilegiado (hash da senha, ultimo acesso, origem) vem do navegador.

Dois interruptores independentes
--------------------------------
    Minister.is_active          o ministro faz parte da relacao ativa
    MinisterAccount.is_active   o acesso ao portal esta liberado

Um ministro ativo pode estar com o acesso bloqueado (perdeu o celular,
suspeita de senha vazada), e um ministro inativado mantem a conta como
estava, para que reativa-lo nao exija recriar o acesso. Para entrar no
portal os DOIS precisam estar ligados.
"""

import datetime

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record
from common.exceptions import DomainError
from ministers.models import (
    Minister,
    MinisterAccount,
    MinisterialTitle,
    MinisterSex,
    MinisterSource,
)
from ministers.normalizacao import (
    TAMANHO_MAXIMO_REGISTRO,
    normalizar_nome,
    normalizar_registro,
    registro_valido,
)

MENSAGEM_SENHA_PADRAO_NAO_CONFIGURADA = (
    "A senha inicial padrão dos ministros não está configurada. Defina "
    "DEFAULT_MINISTER_PASSWORD nas variáveis de ambiente para criar acessos "
    "na importação."
)

ANO_MINIMO = 1900

# Campos que a edicao administrativa pode alterar. Hash, ultimo acesso,
# origem e situacao ficam de fora: tem caminho proprio, auditado.
CAMPOS_EDITAVEIS = (
    "full_name",
    "conamad_registration",
    "field_registration",
    "ministerial_title",
    "position",
    "congregation",
    "consecration_year",
    "birth_date",
    "sex",
    "notes",
)


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------


def ministros_queryset():
    """Base das listagens. O acesso vem na mesma consulta."""
    return Minister.objects.select_related("account")


def congregacoes_cadastradas():
    """Valores distintos, para o filtro e para a sugestao do formulario."""
    return list(
        Minister.objects.exclude(congregation="")
        .values_list("congregation", flat=True)
        .distinct()
        .order_by("congregation")
    )


# ---------------------------------------------------------------------------
# Senha
# ---------------------------------------------------------------------------


def senha_padrao_configurada():
    return bool((getattr(settings, "DEFAULT_MINISTER_PASSWORD", "") or "").strip())


def obter_senha_padrao():
    """
    A senha inicial das contas criadas pela importacao.

    Vem do ambiente e nunca do codigo. O valor devolvido nao pode aparecer em
    log, auditoria, template ou mensagem de erro.
    """
    senha = (getattr(settings, "DEFAULT_MINISTER_PASSWORD", "") or "").strip()
    if not senha:
        raise DomainError(MENSAGEM_SENHA_PADRAO_NAO_CONFIGURADA)
    return senha


def validar_senha(senha, *, identificador=""):
    """
    Validadores do Django, mais uma regra propria: a senha nao pode conter o
    login. A inscricao CONAMAD nao e segredo — aparece na guia de mensalidade
    —, entao uma senha que a contenha e adivinhavel por quem viu a guia.
    """
    if not senha:
        raise DomainError("Informe a senha.")
    mensagens = []
    try:
        validate_password(senha)
    except ValidationError as erro:
        mensagens.extend(erro.messages)
    if identificador and identificador.lower() in senha.lower():
        mensagens.append("A senha não pode conter a matrícula / inscrição.")
    if mensagens:
        raise DomainError(mensagens)
    return senha


# ---------------------------------------------------------------------------
# Validacao dos dados ministeriais
# ---------------------------------------------------------------------------


def _ano_atual():
    return timezone.localdate().year


def limpar_dados(dados):
    """
    Normaliza e valida um dicionario de dados ministeriais.

    Devolve um dicionario novo com exatamente CAMPOS_EDITAVEIS. Erros sao
    acumulados e levantados juntos, para o administrador nao descobrir um de
    cada vez.
    """
    erros = []
    limpo = {}

    nome = normalizar_nome(dados.get("full_name"))
    if not nome:
        erros.append("Informe o nome completo.")
    elif len(nome) > 200:
        erros.append("O nome pode ter no máximo 200 caracteres.")
    limpo["full_name"] = nome

    conamad = normalizar_registro(dados.get("conamad_registration"))
    if not conamad:
        erros.append("Informe a inscrição CONAMAD.")
    elif not registro_valido(conamad):
        erros.append(
            "Inscrição CONAMAD inválida. Use apenas números, letras, ponto, "
            "barra ou hífen (até {} caracteres).".format(TAMANHO_MAXIMO_REGISTRO)
        )
    limpo["conamad_registration"] = conamad

    campo = normalizar_registro(dados.get("field_registration"))
    if campo and not registro_valido(campo):
        erros.append(
            "Matrícula do campo inválida. Use apenas números, letras, ponto, "
            "barra ou hífen (até {} caracteres).".format(TAMANHO_MAXIMO_REGISTRO)
        )
    limpo["field_registration"] = campo

    titulo = (dados.get("ministerial_title") or "").strip()
    if not titulo:
        erros.append("Informe o título ministerial.")
    elif titulo not in MinisterialTitle.values:
        erros.append("Título ministerial inválido.")
    limpo["ministerial_title"] = titulo

    cargo = normalizar_nome(dados.get("position"))
    if len(cargo) > 100:
        erros.append("O cargo / função pode ter no máximo 100 caracteres.")
    limpo["position"] = cargo

    congregacao = normalizar_nome(dados.get("congregation"))
    if len(congregacao) > 120:
        erros.append("A congregação pode ter no máximo 120 caracteres.")
    limpo["congregation"] = congregacao

    ano = dados.get("consecration_year")
    if ano in ("", None):
        ano = None
    else:
        try:
            ano = int(ano)
        except (TypeError, ValueError):
            ano = -1
        if not (ANO_MINIMO <= ano <= _ano_atual()):
            erros.append(
                "Ano de consagração inválido (entre {} e {}).".format(
                    ANO_MINIMO, _ano_atual()
                )
            )
    limpo["consecration_year"] = ano

    nascimento = dados.get("birth_date") or None
    if nascimento is not None:
        if isinstance(nascimento, datetime.datetime):
            nascimento = nascimento.date()
        if not isinstance(nascimento, datetime.date):
            erros.append("Data de nascimento inválida.")
        elif nascimento.year < ANO_MINIMO or nascimento > timezone.localdate():
            erros.append("Data de nascimento fora do intervalo aceito.")
    limpo["birth_date"] = nascimento

    sexo = (dados.get("sex") or "").strip().upper()
    if sexo and sexo not in MinisterSex.values:
        erros.append("Sexo inválido.")
    limpo["sex"] = sexo

    limpo["notes"] = (dados.get("notes") or "").strip()

    if erros:
        raise DomainError(erros)
    return limpo


def _verificar_unicidade(dados, *, ignorando=None):
    erros = []
    outros = Minister.objects.all()
    if ignorando is not None:
        outros = outros.exclude(pk=ignorando.pk)

    if outros.filter(conamad_registration=dados["conamad_registration"]).exists():
        erros.append("Já existe um ministro com esta inscrição CONAMAD.")

    if (
        dados["field_registration"]
        and outros.filter(field_registration=dados["field_registration"]).exists()
    ):
        erros.append("Já existe um ministro com esta matrícula do campo.")

    # O login e a inscricao, e um acesso orfao com o mesmo identificador nao
    # pode existir — mas um acesso e sempre de um ministro, entao a checagem
    # acima ja cobre. Esta fica como defesa caso os dois divirjam um dia.
    acessos = MinisterAccount.objects.filter(
        login_identifier=dados["conamad_registration"]
    )
    if ignorando is not None:
        acessos = acessos.exclude(minister=ignorando)
    if acessos.exists():
        erros.append("Esta inscrição já é usada como login por outro ministro.")

    if erros:
        raise DomainError(erros)


# ---------------------------------------------------------------------------
# Cadastro
# ---------------------------------------------------------------------------


def create_minister(
    *,
    dados,
    source=MinisterSource.MANUAL,
    import_sequence=None,
    password=None,
    actor=None,
    request=None,
):
    """
    Cadastra um ministro e, opcionalmente, o acesso ao portal.

    `password` presente cria o acesso com aquela senha, na mesma transacao:
    ou os dois existem, ou nenhum.
    """
    limpo = limpar_dados(dados)
    if password is not None:
        validar_senha(password, identificador=limpo["conamad_registration"])

    with transaction.atomic():
        _verificar_unicidade(limpo)
        ministro = Minister.objects.create(
            **limpo, source=source, import_sequence=import_sequence
        )
        record(
            AuditEvent.MINISTER_CREATED,
            request=request,
            actor=actor,
            entity_type="Minister",
            entity_id=ministro.pk,
            metadata={"source": source},
        )
        if password is not None:
            _criar_acesso(ministro, senha=password, actor=actor, request=request)
    return ministro


def update_minister(ministro, *, dados, actor=None, request=None):
    """
    Edita os dados ministeriais.

    A trilha recebe so os NOMES dos campos alterados, como no restante do
    sistema: copiar valor antigo e novo levaria dado pessoal para uma tabela
    que so cresce.
    """
    limpo = limpar_dados(dados)

    with transaction.atomic():
        ministro = Minister.objects.select_for_update().get(pk=ministro.pk)
        _verificar_unicidade(limpo, ignorando=ministro)

        alterados = [
            campo for campo in CAMPOS_EDITAVEIS if getattr(ministro, campo) != limpo[campo]
        ]
        if not alterados:
            return ministro

        for campo in alterados:
            setattr(ministro, campo, limpo[campo])
        ministro.save(update_fields=alterados + ["updated_at"])

        metadata = {"changed_fields": alterados}

        # O login acompanha a inscricao. Sem isto o ministro continuaria
        # entrando com o numero antigo, que pode vir a ser de outra pessoa.
        acesso = MinisterAccount.objects.filter(minister=ministro).first()
        if acesso is not None and acesso.login_identifier != ministro.conamad_registration:
            acesso.login_identifier = ministro.conamad_registration
            acesso.save(update_fields=["login_identifier", "updated_at"])
            metadata["login_identifier_alterado"] = True

        record(
            AuditEvent.MINISTER_UPDATED,
            request=request,
            actor=actor,
            entity_type="Minister",
            entity_id=ministro.pk,
            metadata=metadata,
        )
    return ministro


def deactivate_minister(ministro, *, actor=None, request=None):
    """Inativa o cadastro. Nada e apagado; o acesso fica como estava."""
    if not ministro.is_active:
        return ministro
    ministro.is_active = False
    ministro.save(update_fields=["is_active", "updated_at"])
    record(
        AuditEvent.MINISTER_BLOCKED,
        request=request,
        actor=actor,
        entity_type="Minister",
        entity_id=ministro.pk,
    )
    return ministro


def reactivate_minister(ministro, *, actor=None, request=None):
    if ministro.is_active:
        return ministro
    ministro.is_active = True
    ministro.save(update_fields=["is_active", "updated_at"])
    record(
        AuditEvent.MINISTER_UNBLOCKED,
        request=request,
        actor=actor,
        entity_type="Minister",
        entity_id=ministro.pk,
    )
    return ministro


# ---------------------------------------------------------------------------
# Acesso ao portal
# ---------------------------------------------------------------------------


def _criar_acesso(
    ministro,
    *,
    senha=None,
    hash_pronto=None,
    must_change_password=False,
    origem=None,
    actor=None,
    request=None,
):
    acesso = MinisterAccount(
        minister=ministro,
        login_identifier=ministro.conamad_registration,
        is_active=True,
        must_change_password=must_change_password,
    )
    if hash_pronto is not None:
        acesso.set_password_hash(hash_pronto)
    else:
        acesso.set_password(senha)
    acesso.save()
    record(
        AuditEvent.MINISTER_ACCOUNT_CREATED,
        request=request,
        actor=actor,
        entity_type="Minister",
        entity_id=ministro.pk,
        # `origem` diz por onde o acesso nasceu ("provisionamento", Fase 2F).
        # Nunca a senha: so se ela era a padrao compartilhada ou nao.
        metadata=dict(
            {"senha_padrao": hash_pronto is not None},
            **({"origem": origem} if origem else {}),
        ),
    )
    return acesso


def create_account(ministro, *, password, actor=None, request=None):
    """Cria o acesso de um ministro ja cadastrado, com senha definida pelo ADMIN."""
    validar_senha(password, identificador=ministro.conamad_registration)
    with transaction.atomic():
        ministro = Minister.objects.select_for_update().get(pk=ministro.pk)
        if MinisterAccount.objects.filter(minister=ministro).exists():
            raise DomainError("Este ministro já possui acesso ao portal.")
        if MinisterAccount.objects.filter(
            login_identifier=ministro.conamad_registration
        ).exists():
            raise DomainError("Esta inscrição já é usada como login por outro ministro.")
        return _criar_acesso(ministro, senha=password, actor=actor, request=request)


def _exigir_acesso(ministro):
    acesso = MinisterAccount.objects.filter(minister=ministro).first()
    if acesso is None:
        raise DomainError("Este ministro ainda não possui acesso ao portal.")
    return acesso


def block_account(ministro, *, actor=None, request=None):
    acesso = _exigir_acesso(ministro)
    if not acesso.is_active:
        return acesso
    acesso.is_active = False
    acesso.save(update_fields=["is_active", "updated_at"])
    record(
        AuditEvent.MINISTER_ACCOUNT_BLOCKED,
        request=request,
        actor=actor,
        entity_type="Minister",
        entity_id=ministro.pk,
    )
    return acesso


def unblock_account(ministro, *, actor=None, request=None):
    acesso = _exigir_acesso(ministro)
    if acesso.is_active:
        return acesso
    acesso.is_active = True
    acesso.save(update_fields=["is_active", "updated_at"])
    record(
        AuditEvent.MINISTER_ACCOUNT_UNBLOCKED,
        request=request,
        actor=actor,
        entity_type="Minister",
        entity_id=ministro.pk,
    )
    return acesso


def reset_password(ministro, *, new_password, actor=None, request=None):
    """
    Redefinicao pelo ADMIN. Nao pede a senha atual.

    As sessoes abertas com a senha anterior caem sozinhas: a assinatura
    guardada na sessao e derivada do hash, e o hash mudou.
    """
    acesso = _exigir_acesso(ministro)
    validar_senha(new_password, identificador=acesso.login_identifier)
    acesso.set_password(new_password)
    acesso.must_change_password = False
    acesso.save(
        update_fields=[
            "password_hash",
            "password_changed_at",
            "must_change_password",
            "updated_at",
        ]
    )
    record(
        AuditEvent.MINISTER_PASSWORD_RESET,
        request=request,
        actor=actor,
        entity_type="Minister",
        entity_id=ministro.pk,
        # A chave nao contem "password": o sanitizador da trilha descartaria.
        metadata={"redefinida": True},
    )
    return acesso


def change_own_password(acesso, *, current_password, new_password, request=None):
    """Troca feita pelo proprio ministro, no portal. Exige a senha atual."""
    if not acesso.check_password(current_password or ""):
        raise DomainError("A senha atual está incorreta.")
    if current_password == new_password:
        raise DomainError("A nova senha precisa ser diferente da atual.")
    validar_senha(new_password, identificador=acesso.login_identifier)

    acesso.set_password(new_password)
    acesso.must_change_password = False
    acesso.save(
        update_fields=[
            "password_hash",
            "password_changed_at",
            "must_change_password",
            "updated_at",
        ]
    )
    record(
        AuditEvent.MINISTER_PASSWORD_CHANGED,
        request=request,
        entity_type="Minister",
        entity_id=acesso.minister_id,
    )
    return acesso


def hash_da_senha_padrao():
    """Um hash da senha padrao, calculado uma vez por importacao."""
    return make_password(obter_senha_padrao())
