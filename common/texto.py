"""
Regras de texto que atravessam mais de uma app.

Por enquanto uma so: o motivo escrito que acompanha um ato administrativo
irreversivel. Tres operacoes exigem esse motivo — anular tentativa (Etapa 7),
arquivar prova e revogar matricula (Etapa 9) — e as tres precisam da mesma
regra: obrigatorio, nao aceita so espaco, tem teto.

O teto vive aqui e nao em cada modelo porque ele tambem e o max_length das
tres colunas. Tres numeros soltos combinariam ate o dia em que alguem
mudasse um deles, e ai o formulario passaria a aceitar um texto que o banco
recusa.
"""

from common.exceptions import DomainError

# Mil caracteres cabem em qualquer justificativa administrativa honesta e
# ainda assim impedem que alguem cole um documento inteiro dentro de um campo
# que sera lido numa tabela.
LIMITE_DO_MOTIVO = 1000


def validar_motivo(motivo, *, vazio, limite=LIMITE_DO_MOTIVO):
    """
    Devolve o motivo limpo, ou levanta DomainError.

    `vazio` e a mensagem especifica da operacao ("Informe o motivo da
    anulacao.", "Informe o motivo do arquivamento."). Ela e obrigatoria de
    proposito: um texto generico como "campo obrigatorio" nao diz ao
    administrador qual dos formularios da tela ele deixou em branco.

    O motivo existe para responder, meses depois, "por que isto foi feito?".
    A resposta precisa estar no proprio registro, e nao na memoria de quem
    clicou.
    """
    texto = (motivo or "").strip()
    if not texto:
        raise DomainError(vazio)
    if len(texto) > limite:
        raise DomainError(
            "O motivo pode ter no maximo {} caracteres.".format(limite)
        )
    return texto
