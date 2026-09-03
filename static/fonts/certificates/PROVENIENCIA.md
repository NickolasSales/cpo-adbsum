# Fontes dos certificados — origem, licenca e integridade

Baixado em 2026-09-03. Nenhum arquivo foi modificado: sao os binarios
oficiais, byte a byte. O `SHA256SUMS` ao lado deste arquivo permite conferir
isso a qualquer momento.


## Por que estatica, e nao variavel

O repositorio `google/fonts` distribui **Montserrat** e **Bodoni Moda**
apenas como fontes VARIAVEIS:

    ofl/montserrat/Montserrat[wght].ttf
    ofl/bodonimoda/BodoniModa[opsz,wght].ttf

Isso nao serve aqui. O navegador entende o eixo de peso e desenharia o
Bold; o ReportLab le a tabela `glyf` direto e desenharia a instancia
padrao — Regular — em qualquer peso. O resultado seria exatamente a
divergencia que o preview existe para eliminar:

    tela = Montserrat Bold        PDF = Montserrat Regular

Entao as instancias estaticas vem do REPOSITORIO DO PROPRIO AUTOR, no
MESMO COMMIT que o Google Fonts usou para compilar a variavel. E a mesma
arvore de codigo-fonte, sem intermediario e sem conversao nossa.

O commit de cada um esta registrado pelo Google em
`ofl/<familia>/upstream_info.md` e no bloco `source` do `METADATA.pb`.


## Origem por familia

### Montserrat
    autoria    Julieta Ulanovsky, Sol Matas, Juan Pablo del Peral,
               Jacques Le Bailly
    repo       https://github.com/JulietaUla/Montserrat
    commit     cc8daf2e7085006b9c112542fc82b58afc13521d
    caminho    fonts/ttf/Montserrat-*.ttf
    licenca    OFL 1.1  (OFL.txt de google/fonts/ofl/montserrat)

### Bodoni Moda
    autoria    Owen Earl (Indestructible Type)
    repo       https://github.com/indestructible-type/Bodoni
    commit     30ce6cdc354ef179a3b72ba0f0e71826e599348c
    caminho    fonts/ttf/BodoniModa-*.ttf
    licenca    OFL 1.1  (OFL.txt de google/fonts/ofl/bodonimoda)

### Great Vibes
    repo       https://github.com/google/fonts  (branch main)
    caminho    ofl/greatvibes/GreatVibes-Regular.ttf
    licenca    OFL 1.1

### Allura
    repo       https://github.com/google/fonts  (branch main)
    caminho    ofl/allura/Allura-Regular.ttf
    licenca    OFL 1.1

Great Vibes e Allura ja sao estaticas no proprio google/fonts — nao ha
versao variavel delas, e por isso vieram direto de la.


## Licenca

As quatro sao SIL Open Font License 1.1. O `OFL.txt` de cada familia esta
na pasta dela, que e o que a licenca exige para redistribuicao.

Nenhuma das quatro declara Reserved Font Name na linha de copyright — e
de qualquer forma nao renomeamos nem alteramos arquivo nenhum, entao a
clausula nao chega a ser tocada.

A OFL permite embutir a fonte em documento. Os certificados em PDF
carregam subconjuntos das fontes usadas, que e o que faz o documento abrir
igual em qualquer computador.


## Integridade

    sha256sum -c SHA256SUMS

Os mesmos valores estao no teste `test_fontes_instaladas.py`, que falha se
um arquivo sumir ou mudar.
