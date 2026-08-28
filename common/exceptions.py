"""Excecoes de dominio."""


class DomainError(Exception):
    """
    Regra de negocio violada.

    Levantada pela camada de servico quando a operacao e invalida por motivo
    de negocio, e nao por erro tecnico. As views capturam e apresentam a
    mensagem ao administrador; a mensagem e escrita para ser lida por uma
    pessoa, entao nunca deve conter detalhe interno ou dado sensivel.
    """


def campos_alterados(instancia, dados):
    """
    Nomes dos campos cujo valor mudaria, comparando a instancia com os dados.

    Usado para auditar edicoes registrando apenas 'changed_fields', sem
    copiar valores antigos e novos de dados pessoais para a trilha
    (minimizacao de dados).
    """
    alterados = []
    for campo, novo_valor in dados.items():
        if getattr(instancia, campo, None) != novo_valor:
            alterados.append(campo)
    return alterados
