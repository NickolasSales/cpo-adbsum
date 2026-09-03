"""
O CSS das fontes, escrito a partir do registro.

    certificates/fonts.py  ->  static/css/fontes-certificado.css

O arquivo e gerado, e nao mantido a mao. Escrever `@font-face` a mao criaria
uma segunda lista de arquivos ao lado da que o Python ja tem, e as duas
divergiriam no primeiro peso acrescentado — a tela mostrando um Semibold que
o PDF nao desenha, ou o contrario.

Um teste refaz a geracao e compara com o arquivo em disco. Acrescentar uma
face ao registro e esquecer de rodar o comando faz a suite recusar.

Por que arquivo gerado, e nao uma view
--------------------------------------
Porque e um arquivo estatico e deve ser servido como tal: pelo nginx, com
hash no nome e cache longo, sem acordar o Python. Uma view Django devolvendo
CSS gastaria um worker por request para entregar bytes que nunca mudam entre
deploys.
"""

from certificates.fonts import (
    CERTIFICATE_FONTS,
    RAIZ_DAS_FONTES,
    ROTULOS_DOS_PESOS,
    caminho_relativo,
    pesos_suportados,
)

CABECALHO = """/*
 * Fontes do certificado — ARQUIVO GERADO. Nao edite a mao.
 *
 *     python manage.py gerar_css_das_fontes
 *
 * A fonte da verdade e certificates/fonts.py. Um teste refaz esta geracao e
 * compara com o que esta aqui, entao editar este arquivo direto e recusado
 * pela suite.
 *
 * Tudo local. Nenhuma linha aponta para fonts.googleapis.com nem para
 * fonts.gstatic.com: o editor precisa abrir com a internet da instituicao
 * fora do ar, e uma fonte que so existe num CDN e uma fonte que um dia nao
 * carrega.
 *
 * Os caminhos sao relativos a este arquivo. O ManifestStaticFilesStorage
 * reescreve cada url() para o nome com hash no collectstatic — e por isso
 * que eles precisam continuar relativos.
 *
 * font-display: swap — o texto aparece na hora com a fonte generica e troca
 * quando o arquivo chega. Num editor de posicionamento, texto invisivel por
 * meio segundo e pior que texto que troca de forma: o administrador esta
 * arrastando caixas, e caixa vazia nao se arrasta.
 */
"""

REGRA = """
@font-face {{
  font-family: "{familia}";
  src: url("{caminho}") format("truetype");
  font-weight: {peso};
  font-style: {estilo};
  font-display: swap;
}}
"""


def gerar():
    """O conteudo completo do arquivo, como string."""
    partes = [CABECALHO]
    for chave, dados in CERTIFICATE_FONTS.items():
        pesos = pesos_suportados(chave)
        partes.append(
            "\n/* {} — {} */\n".format(
                dados["rotulo"],
                ", ".join(ROTULOS_DOS_PESOS[peso] for peso in pesos),
            )
        )
        for (peso, italico), _face in sorted(dados["faces"].items()):
            relativo = caminho_relativo(chave, peso, italico)
            partes.append(
                REGRA.format(
                    familia=dados["css_familia"],
                    # `../` porque este CSS mora em static/css/ e as fontes
                    # em static/fonts/certificates/.
                    caminho="../{}".format(relativo),
                    peso=peso,
                    estilo="italic" if italico else "normal",
                )
            )
    return "".join(partes)


def caminho_do_css():
    """Onde o arquivo gerado mora, resolvido pela configuracao."""
    from certificates.fonts import raiz_no_disco

    # raiz_no_disco() aponta para static/fonts/certificates; o CSS fica dois
    # niveis acima, em static/css.
    return raiz_no_disco().parent.parent / "css" / "fontes-certificado.css"


# Reexportado so para o comando e o teste nao precisarem importar de dois
# lugares.
__all__ = ["gerar", "caminho_do_css", "RAIZ_DAS_FONTES"]
