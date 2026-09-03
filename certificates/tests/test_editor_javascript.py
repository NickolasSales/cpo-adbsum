"""
O JavaScript do editor roda? O painel monta?

Todo o resto da suite testa Python. O editor visual sao novecentas linhas de
JavaScript que nenhum teste tocava, e um erro ali nao aparece em lugar nenhum
do servidor: a pagina carrega, o palco desenha, e o painel de propriedades
simplesmente nao abre.

Foi assim que quase passou um `var atual` sombreando a funcao `atual()` — em
JavaScript o `var` sobe para o topo da funcao, entao a declaracao no meio do
`montarPainel` apagava a funcao no comeco dele. Nenhum teste Python veria.

O que este arquivo NAO faz
--------------------------
Nao valida aparencia, nao mede pixel, nao testa arraste. Um DOM de mentira
nao tem layout. A conferencia visual continua sendo no navegador.

Node
----
Pula se `node` nao estiver na maquina. O servidor nao tem, e nao vai ter por
causa disto — mas quem desenvolve tem, e e onde o editor e alterado.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from django.conf import settings

from certificates.fonts import catalogo_para_o_editor
from certificates.models.template import (
    LIMITE_DA_FONTE,
    TIPOS_COM_REPETICAO,
    TIPOS_DA_PALETA,
    TIPOS_DE_IMAGEM,
    FieldType,
    TextAlign,
)
from certificates.views_templates import (
    PADRAO_DO_CAMPO,
    PADRAO_POR_TIPO,
    _rotulos,
)

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node nao esta instalado nesta maquina"
)

AQUI = Path(__file__).resolve().parent
HARNESS = AQUI / "editor_smoke.js"
EDITOR = Path(settings.BASE_DIR) / "static" / "js" / "certificate-editor.js"


def _padrao(tipo):
    dados = dict(PADRAO_DO_CAMPO)
    dados.update(PADRAO_POR_TIPO.get(tipo, {}))
    dados["type"] = tipo
    return dados


def _contexto():
    """
    Os mesmos dados que a view entrega por json_script.

    Montados a partir das constantes de producao, e nao escritos a mao: um
    dado novo no editor chega aqui sozinho, e se ele quebrar o JavaScript o
    teste diz.
    """
    exemplos = {tipo: "Exemplo {}".format(tipo) for tipo in FieldType.values}
    padroes = {tipo: _padrao(tipo) for tipo in TIPOS_DA_PALETA}
    return {
        "dados-elementos": [
            _padrao(FieldType.STUDENT_NAME),
            _padrao(FieldType.CUSTOM_TEXT),
        ],
        "dados-padroes": padroes,
        "dados-variaveis": [
            {"chave": "{{nome_aluno}}", "rotulo": "Nome do aluno", "exemplo": "Joao"}
        ],
        "dados-familias": catalogo_para_o_editor(),
        "dados-alinhamentos": [list(par) for par in TextAlign.choices],
        "dados-repetiveis": sorted(TIPOS_COM_REPETICAO),
        "dados-rotulos": _rotulos(exemplos),
        "dados-imagens": sorted(TIPOS_DE_IMAGEM),
        "dados-limite-fonte": {
            "minimo": LIMITE_DA_FONTE[0],
            "maximo": LIMITE_DA_FONTE[1],
        },
        "dados-tamanho-texto": 2000,
    }


def test_o_editor_carrega_e_monta_o_painel(tmp_path):
    dados = tmp_path / "dados-editor.json"
    dados.write_text(json.dumps(_contexto()), encoding="utf-8")

    resultado = subprocess.run(
        [NODE, str(HARNESS), str(EDITOR), str(dados)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert "TUDO OK" in resultado.stdout
