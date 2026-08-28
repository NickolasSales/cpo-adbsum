"""Excecoes de dominio."""


class DomainError(Exception):
    """
    Regra de negocio violada.

    Levantada pela camada de servico quando a operacao e invalida por motivo
    de negocio, e nao por erro tecnico. As views capturam e apresentam a
    mensagem ao administrador; a mensagem e escrita para ser lida por uma
    pessoa, entao nunca deve conter detalhe interno ou dado sensivel.

    Aceita tambem uma lista de mensagens. Validar uma prova inteira produz
    varios problemas de uma vez, e mostrar so o primeiro obrigaria o
    administrador a descobrir o resto por tentativa e erro. Quem captura le
    `.mensagens`; str() continua devolvendo o texto corrido, para nao quebrar
    quem ja trata a excecao como uma mensagem so.
    """

    def __init__(self, mensagem):
        if isinstance(mensagem, (list, tuple)):
            self.mensagens = [str(item) for item in mensagem]
        else:
            self.mensagens = [str(mensagem)]
        super().__init__(" ".join(self.mensagens))


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
