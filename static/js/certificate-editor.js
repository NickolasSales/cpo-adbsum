/*
  Editor visual do modelo de certificado.

  Por que sem biblioteca
  ----------------------
  O pedido admitia algo pequeno no estilo do interact.js para arrastar e
  redimensionar. Nao foi preciso: Pointer Events resolvem os dois em cerca de
  cem linhas, com o mesmo codigo para mouse, toque e caneta, e sem nenhum
  arquivo novo para versionar, servir e manter atualizado. Uma dependencia
  que economiza cem linhas e custa um pacote no repositorio nao se paga.

  O que ele nao faz
  -----------------
  Nao desenha o certificado. O palco e uma APROXIMACAO fiel o bastante para
  posicionar — mesma pagina, mesmas porcentagens, mesmo tamanho de fonte em
  pontos — mas quem decide onde a linha quebra e o ReportLab, no servidor.
  Por isso o PDF continua ao lado, e e ele que manda.

  Divisao do arquivo
  ------------------
      estado          a lista de elementos, e nada mais
      geometria       porcentagem <-> pixel, e a rotacao
      palco           criar, posicionar e pintar as caixas
      selecao         quem esta selecionado, e as alcas
      arraste         mover e redimensionar por ponteiro
      teclado         setas para ajuste fino
      guias           linhas de alinhamento durante o arraste
      propriedades    o painel lateral
      serializacao    o que vai para o servidor
*/
(function () {
  'use strict';

  var raiz = document.getElementById('editor-certificado');
  if (!raiz) { return; }

  function ler(id) {
    var no = document.getElementById(id);
    return no ? JSON.parse(no.textContent) : null;
  }

  var CONFIG = {
    elementos: ler('dados-elementos') || [],
    padroes: ler('dados-padroes') || {},
    variaveis: ler('dados-variaveis') || [],
    familias: ler('dados-familias') || [],
    alinhamentos: ler('dados-alinhamentos') || [],
    repetiveis: ler('dados-repetiveis') || [],
    rotulos: ler('dados-rotulos') || {},
    imagens: ler('dados-imagens') || [],
    limiteFonte: ler('dados-limite-fonte') || { minimo: 6, maximo: 120 },
    tamanhoMaximoDoTexto: ler('dados-tamanho-texto') || 2000,
    urlSalvar: raiz.dataset.urlSalvar,
    urlPreview: raiz.dataset.urlPreview,
    editavel: raiz.dataset.editavel === '1'
  };

  var palco = document.getElementById('palco');
  var camada = document.getElementById('palco-camada');
  var painel = document.getElementById('painel-propriedades');
  var vazio = document.getElementById('painel-vazio');
  var listaPaleta = document.getElementById('paleta');
  var botaoSalvar = document.getElementById('btn-salvar');
  var aviso = document.getElementById('editor-aviso');
  var contador = document.getElementById('editor-contador');
  var previewPdf = document.getElementById('preview-pdf');

  // A pagina em pontos PostScript. E o que permite mostrar um texto de 30pt
  // com o tamanho que ele tera no papel: a razao entre a largura do palco em
  // pixels e a largura da pagina em pontos e o fator de escala.
  var LARGURA_EM_PONTOS = parseFloat(palco.dataset.larguraMm) * 72 / 25.4;

  var estado = {
    elementos: CONFIG.elementos.map(function (item) { return Object.assign({}, item); }),
    selecionado: -1,
    sujo: false,
    grade: false,
    snap: false
  };

  // -----------------------------------------------------------------------
  // Geometria
  // -----------------------------------------------------------------------

  function limitar(valor, minimo, maximo) {
    return Math.min(maximo, Math.max(minimo, valor));
  }

  function arredondar(valor) {
    // Quatro casas: e a precisao que o banco guarda em duas, com folga para
    // o arredondamento nao andar sozinho entre um arraste e outro.
    return Math.round(valor * 100) / 100;
  }

  function encaixar(valor) {
    return estado.snap ? Math.round(valor) : valor;
  }

  function area() {
    return palco.getBoundingClientRect();
  }

  function ehImagem(tipo) {
    return CONFIG.imagens.indexOf(tipo) !== -1;
  }

  // O CSS gira no sentido horario; o ReportLab, no anti-horario. Sem o sinal
  // trocado, um ano configurado com rotation=90 apareceria de cabeca para
  // baixo aqui em relacao ao PDF — e o administrador corrigiria na tela um
  // defeito que so existia na tela.
  function grausCss(rotacao) {
    return -(rotacao || 0);
  }

  // -----------------------------------------------------------------------
  // Palco
  // -----------------------------------------------------------------------

  var caixas = [];

  function criarCaixa(indice) {
    var caixa = document.createElement('div');
    caixa.className = 'cpo-el';
    caixa.dataset.indice = String(indice);
    caixa.tabIndex = 0;

    var corpo = document.createElement('span');
    corpo.className = 'cpo-el__texto';
    caixa.appendChild(corpo);

    ['nw', 'ne', 'se', 'sw'].forEach(function (canto) {
      var alca = document.createElement('span');
      alca.className = 'cpo-el__alca cpo-el__alca--' + canto;
      alca.dataset.canto = canto;
      caixa.appendChild(alca);
    });

    camada.appendChild(caixa);
    return caixa;
  }

  function textoDoElemento(elemento) {
    if (elemento.type === 'QR_CODE') { return 'QR'; }
    if (elemento.type === 'CUSTOM_TEXT') {
      return elemento.content || CONFIG.rotulos[elemento.type] || '';
    }
    var exemplo = CONFIG.rotulos[elemento.type + '__exemplo'];
    return exemplo || CONFIG.rotulos[elemento.type] || elemento.type;
  }

  function pintar(caixa, elemento, indice) {
    caixa.style.left = elemento.x + '%';
    caixa.style.top = elemento.y + '%';
    caixa.style.width = elemento.width + '%';
    caixa.style.height = elemento.height + '%';
    caixa.style.transform = 'rotate(' + grausCss(elemento.rotation) + 'deg)';
    caixa.style.zIndex = String(10 + (elemento.z_index || 0));
    caixa.classList.toggle('cpo-el--oculto', !elemento.is_visible);
    caixa.classList.toggle('cpo-el--imagem', ehImagem(elemento.type));
    caixa.classList.toggle('cpo-el--selecionado', indice === estado.selecionado);
    caixa.setAttribute(
      'aria-label',
      (CONFIG.rotulos[elemento.type] || elemento.type) +
        ', ' + Math.round(elemento.x) + '% por ' + Math.round(elemento.y) + '%'
    );

    var corpo = caixa.firstChild;
    // textContent, e nunca innerHTML: o texto vem do administrador, e um
    // <img onerror> gravado no bloco personalizado nao pode virar marcacao
    // dentro do editor de quem abrir o modelo depois.
    corpo.textContent = textoDoElemento(elemento);

    if (ehImagem(elemento.type)) {
      corpo.style.font = '';
      corpo.style.color = '';
      corpo.style.textAlign = '';
      return;
    }

    var escala = area().width / LARGURA_EM_PONTOS;
    var familia = { Times: '"Times New Roman", Times, serif',
                    Courier: '"Courier New", Courier, monospace' }[elemento.font_family]
                    || 'Helvetica, Arial, sans-serif';
    corpo.style.fontFamily = familia;
    corpo.style.fontSize = (elemento.font_size * escala) + 'px';
    corpo.style.fontWeight = elemento.bold ? '700' : '400';
    corpo.style.fontStyle = elemento.italic ? 'italic' : 'normal';
    corpo.style.lineHeight = String(elemento.line_height);
    corpo.style.color = elemento.text_color;
    corpo.style.textAlign = { LEFT: 'left', RIGHT: 'right' }[elemento.text_align] || 'center';
    corpo.style.whiteSpace = elemento.wrap ? 'pre-wrap' : 'pre';
  }

  function desenhar() {
    while (caixas.length > estado.elementos.length) {
      camada.removeChild(caixas.pop());
    }
    while (caixas.length < estado.elementos.length) {
      caixas.push(criarCaixa(caixas.length));
    }
    estado.elementos.forEach(function (elemento, indice) {
      caixas[indice].dataset.indice = String(indice);
      pintar(caixas[indice], elemento, indice);
    });
    if (contador) {
      contador.textContent = estado.elementos.length === 1
        ? '1 elemento'
        : estado.elementos.length + ' elementos';
    }
  }

  // -----------------------------------------------------------------------
  // Selecao e propriedades
  // -----------------------------------------------------------------------

  function selecionar(indice) {
    estado.selecionado = indice;
    desenhar();
    montarPainel();
    if (indice >= 0 && caixas[indice]) { caixas[indice].focus({ preventScroll: true }); }
  }

  function atual() {
    return estado.selecionado >= 0 ? estado.elementos[estado.selecionado] : null;
  }

  function marcarSujo() {
    estado.sujo = true;
    if (botaoSalvar) { botaoSalvar.classList.add('btn-primary'); }
    if (aviso) { aviso.hidden = false; }
  }

  function alterar(propriedade, valor) {
    var elemento = atual();
    if (!elemento) { return; }
    elemento[propriedade] = valor;
    marcarSujo();
    desenhar();
  }

  function campo(rotulo, controle, dica) {
    var envolve = document.createElement('div');
    envolve.className = 'cpo-prop';
    var etiqueta = document.createElement('label');
    etiqueta.className = 'form-label small mb-1';
    etiqueta.textContent = rotulo;
    etiqueta.setAttribute('for', controle.id);
    envolve.appendChild(etiqueta);
    envolve.appendChild(controle);
    if (dica) {
      var texto = document.createElement('div');
      texto.className = 'form-text small';
      texto.textContent = dica;
      envolve.appendChild(texto);
    }
    return envolve;
  }

  function numero(id, valor, minimo, maximo, passo, aoMudar) {
    var controle = document.createElement('input');
    controle.type = 'number';
    controle.className = 'form-control form-control-sm';
    controle.id = id;
    controle.value = valor;
    controle.min = minimo;
    controle.max = maximo;
    controle.step = passo;
    controle.disabled = !CONFIG.editavel;
    controle.addEventListener('input', function () {
      var lido = parseFloat(controle.value);
      if (isNaN(lido)) { return; }
      aoMudar(limitar(lido, minimo, maximo));
    });
    return controle;
  }

  function selecao(id, opcoes, valor, aoMudar) {
    var controle = document.createElement('select');
    controle.className = 'form-select form-select-sm';
    controle.id = id;
    controle.disabled = !CONFIG.editavel;
    opcoes.forEach(function (par) {
      var opcao = document.createElement('option');
      opcao.value = par[0];
      opcao.textContent = par[1];
      if (par[0] === valor) { opcao.selected = true; }
      controle.appendChild(opcao);
    });
    controle.addEventListener('change', function () { aoMudar(controle.value); });
    return controle;
  }

  function marcador(id, rotulo, marcado, aoMudar) {
    var envolve = document.createElement('div');
    envolve.className = 'form-check';
    var controle = document.createElement('input');
    controle.type = 'checkbox';
    controle.className = 'form-check-input';
    controle.id = id;
    controle.checked = !!marcado;
    controle.disabled = !CONFIG.editavel;
    controle.addEventListener('change', function () { aoMudar(controle.checked); });
    var etiqueta = document.createElement('label');
    etiqueta.className = 'form-check-label small';
    etiqueta.setAttribute('for', id);
    etiqueta.textContent = rotulo;
    envolve.appendChild(controle);
    envolve.appendChild(etiqueta);
    return envolve;
  }

  function botao(rotulo, classe, aoClicar) {
    var controle = document.createElement('button');
    controle.type = 'button';
    controle.className = 'btn btn-sm ' + classe;
    controle.textContent = rotulo;
    controle.disabled = !CONFIG.editavel;
    controle.addEventListener('click', aoClicar);
    return controle;
  }

  function linha() {
    var grupo = document.createElement('div');
    grupo.className = 'd-flex flex-wrap gap-2 mb-2';
    return grupo;
  }

  function montarPainel() {
    painel.textContent = '';
    var elemento = atual();
    if (!elemento) {
      vazio.hidden = false;
      painel.hidden = true;
      return;
    }
    vazio.hidden = true;
    painel.hidden = false;

    var titulo = document.createElement('h3');
    titulo.className = 'h6 mb-2';
    titulo.textContent = CONFIG.rotulos[elemento.type] || elemento.type;
    painel.appendChild(titulo);

    // --- texto personalizado ---------------------------------------------
    if (elemento.type === 'CUSTOM_TEXT') {
      var area_ = document.createElement('textarea');
      area_.className = 'form-control form-control-sm';
      area_.id = 'prop-content';
      area_.rows = 5;
      area_.maxLength = CONFIG.tamanhoMaximoDoTexto;
      area_.value = elemento.content || '';
      area_.disabled = !CONFIG.editavel;
      area_.addEventListener('input', function () { alterar('content', area_.value); });
      painel.appendChild(campo('Texto', area_,
        'Use as variaveis abaixo. Elas viram o dado do aluno no PDF.'));

      var inserir = document.createElement('select');
      inserir.className = 'form-select form-select-sm';
      inserir.id = 'prop-variavel';
      inserir.disabled = !CONFIG.editavel;
      var vazia = document.createElement('option');
      vazia.value = '';
      vazia.textContent = 'Inserir variavel...';
      inserir.appendChild(vazia);
      CONFIG.variaveis.forEach(function (item) {
        var opcao = document.createElement('option');
        opcao.value = item.chave;
        opcao.textContent = item.rotulo + (item.exemplo ? '  ->  ' + item.exemplo : '');
        inserir.appendChild(opcao);
      });
      inserir.addEventListener('change', function () {
        if (!inserir.value) { return; }
        var inicio = area_.selectionStart || 0;
        var fim = area_.selectionEnd || 0;
        area_.value = area_.value.slice(0, inicio) + inserir.value + area_.value.slice(fim);
        var cursor = inicio + inserir.value.length;
        area_.focus();
        area_.setSelectionRange(cursor, cursor);
        alterar('content', area_.value);
        inserir.value = '';
      });
      painel.appendChild(campo('Inserir variavel', inserir));
    }

    // --- posicao ----------------------------------------------------------
    var grade = document.createElement('div');
    grade.className = 'row g-2 mb-2';
    [
      ['X (%)', 'x', 0, 100, 0.1],
      ['Y (%)', 'y', 0, 100, 0.1],
      ['Largura (%)', 'width', 0.1, 100, 0.1],
      ['Altura (%)', 'height', 0.1, 100, 0.1]
    ].forEach(function (item) {
      var coluna = document.createElement('div');
      coluna.className = 'col-6';
      coluna.appendChild(campo(item[0], numero(
        'prop-' + item[1], arredondar(elemento[item[1]]), item[2], item[3], item[4],
        function (valor) { alterar(item[1], arredondar(valor)); }
      )));
      grade.appendChild(coluna);
    });
    painel.appendChild(grade);

    var acoes = linha();
    acoes.appendChild(botao('Centralizar na horizontal', 'btn-outline-secondary', function () {
      alterar('x', arredondar((100 - elemento.width) / 2));
      montarPainel();
    }));
    acoes.appendChild(botao('Centralizar na vertical', 'btn-outline-secondary', function () {
      alterar('y', arredondar((100 - elemento.height) / 2));
      montarPainel();
    }));
    painel.appendChild(acoes);

    // --- tipografia -------------------------------------------------------
    if (!ehImagem(elemento.type)) {
      painel.appendChild(campo('Fonte', selecao('prop-font', CONFIG.familias.map(function (f) {
        return [f, f];
      }), elemento.font_family, function (valor) { alterar('font_family', valor); })));

      var estilos = linha();
      estilos.appendChild(marcador('prop-bold', 'Negrito', elemento.bold, function (v) {
        alterar('bold', v);
      }));
      estilos.appendChild(marcador('prop-italic', 'Italico', elemento.italic, function (v) {
        alterar('italic', v);
      }));
      painel.appendChild(estilos);

      var tamanhos = document.createElement('div');
      tamanhos.className = 'row g-2 mb-2';
      [
        ['Tamanho', 'font_size'],
        ['Tamanho minimo', 'min_font_size']
      ].forEach(function (item) {
        var coluna = document.createElement('div');
        coluna.className = 'col-6';
        coluna.appendChild(campo(item[0], numero(
          'prop-' + item[1], elemento[item[1]],
          CONFIG.limiteFonte.minimo, CONFIG.limiteFonte.maximo, 1,
          function (valor) { alterar(item[1], Math.round(valor)); }
        )));
        tamanhos.appendChild(coluna);
      });
      painel.appendChild(tamanhos);

      painel.appendChild(campo('Alinhamento', selecao(
        'prop-align', CONFIG.alinhamentos, elemento.text_align,
        function (valor) { alterar('text_align', valor); }
      )));

      var cor = document.createElement('input');
      cor.type = 'color';
      cor.className = 'form-control form-control-sm form-control-color';
      cor.id = 'prop-color';
      cor.value = elemento.text_color;
      cor.disabled = !CONFIG.editavel;
      cor.addEventListener('input', function () { alterar('text_color', cor.value.toUpperCase()); });
      painel.appendChild(campo('Cor', cor));

      painel.appendChild(campo('Entrelinha', numero(
        'prop-line', elemento.line_height, 0.8, 3, 0.05,
        function (valor) { alterar('line_height', Math.round(valor * 100) / 100); }
      )));

      var opcoes = linha();
      opcoes.appendChild(marcador('prop-autofit', 'Auto ajustar', elemento.auto_fit, function (v) {
        alterar('auto_fit', v);
      }));
      opcoes.appendChild(marcador('prop-wrap', 'Quebrar linha', elemento.wrap, function (v) {
        alterar('wrap', v);
      }));
      painel.appendChild(opcoes);
    }

    painel.appendChild(campo('Rotacao', numero(
      'prop-rotation', elemento.rotation, -360, 360, 1,
      function (valor) { alterar('rotation', Math.round(valor)); }
    ), 'Use 90 ou -90 para texto na vertical.'));

    painel.appendChild(campo('Ordem de desenho', numero(
      'prop-z', elemento.z_index, 0, 999, 1,
      function (valor) { alterar('z_index', Math.round(valor)); }
    ), 'Maior desenha por cima.'));

    painel.appendChild(marcador('prop-visible', 'Visivel', elemento.is_visible, function (v) {
      alterar('is_visible', v);
    }));

    var rodape = linha();
    rodape.className = 'd-flex flex-wrap gap-2 pt-3';
    if (elemento.type === 'CUSTOM_TEXT') {
      rodape.appendChild(botao('Duplicar', 'btn-outline-secondary', function () {
        var copia = Object.assign({}, elemento);
        copia.x = arredondar(limitar(copia.x + 2, 0, 100 - copia.width));
        copia.y = arredondar(limitar(copia.y + 2, 0, 100 - copia.height));
        estado.elementos.push(copia);
        marcarSujo();
        selecionar(estado.elementos.length - 1);
      }));
    }
    rodape.appendChild(botao('Remover', 'btn-outline-danger', function () {
      var rotulo = CONFIG.rotulos[elemento.type] || elemento.type;
      if (!window.confirm('Remover "' + rotulo + '" deste modelo?')) { return; }
      estado.elementos.splice(estado.selecionado, 1);
      marcarSujo();
      selecionar(-1);
    }));
    painel.appendChild(rodape);
  }

  // -----------------------------------------------------------------------
  // Guias de alinhamento
  // -----------------------------------------------------------------------

  var guiaH = document.getElementById('guia-h');
  var guiaV = document.getElementById('guia-v');
  var TOLERANCIA = 0.6;

  function candidatos(eixo, ignorar) {
    // Centro da pagina, mais bordas e centros dos outros elementos.
    var lista = [50];
    estado.elementos.forEach(function (outro, indice) {
      if (indice === ignorar) { return; }
      var inicio = eixo === 'x' ? outro.x : outro.y;
      var tamanho = eixo === 'x' ? outro.width : outro.height;
      lista.push(inicio, inicio + tamanho / 2, inicio + tamanho);
    });
    return lista;
  }

  function guiar(elemento, indice) {
    var centroX = elemento.x + elemento.width / 2;
    var centroY = elemento.y + elemento.height / 2;
    var achouV = candidatos('x', indice).find(function (v) {
      return Math.abs(v - centroX) < TOLERANCIA;
    });
    var achouH = candidatos('y', indice).find(function (v) {
      return Math.abs(v - centroY) < TOLERANCIA;
    });
    guiaV.hidden = achouV === undefined;
    if (achouV !== undefined) { guiaV.style.left = achouV + '%'; }
    guiaH.hidden = achouH === undefined;
    if (achouH !== undefined) { guiaH.style.top = achouH + '%'; }
  }

  function esconderGuias() {
    guiaV.hidden = true;
    guiaH.hidden = true;
  }

  // -----------------------------------------------------------------------
  // Arraste e redimensionamento
  // -----------------------------------------------------------------------

  var gesto = null;

  camada.addEventListener('pointerdown', function (evento) {
    var caixa = evento.target.closest('.cpo-el');
    if (!caixa) { return; }
    var indice = parseInt(caixa.dataset.indice, 10);
    selecionar(indice);
    if (!CONFIG.editavel) { return; }

    var elemento = estado.elementos[indice];
    var retangulo = area();
    var alca = evento.target.closest('.cpo-el__alca');

    gesto = {
      indice: indice,
      canto: alca ? alca.dataset.canto : null,
      retangulo: retangulo,
      inicioX: evento.clientX,
      inicioY: evento.clientY,
      original: Object.assign({}, elemento)
    };
    caixa.setPointerCapture(evento.pointerId);
    caixa.classList.add('cpo-el--movendo');
    evento.preventDefault();
    evento.stopPropagation();
  });

  camada.addEventListener('pointermove', function (evento) {
    if (!gesto) { return; }
    var elemento = estado.elementos[gesto.indice];
    var original = gesto.original;
    var deltaX = (evento.clientX - gesto.inicioX) / gesto.retangulo.width * 100;
    var deltaY = (evento.clientY - gesto.inicioY) / gesto.retangulo.height * 100;

    if (!gesto.canto) {
      elemento.x = arredondar(encaixar(limitar(original.x + deltaX, 0, 100 - original.width)));
      elemento.y = arredondar(encaixar(limitar(original.y + deltaY, 0, 100 - original.height)));
    } else {
      var esquerda = original.x;
      var topo = original.y;
      var largura = original.width;
      var altura = original.height;

      if (gesto.canto.indexOf('w') !== -1) {
        esquerda = limitar(original.x + deltaX, 0, original.x + original.width - 0.5);
        largura = original.width + (original.x - esquerda);
      } else {
        largura = limitar(original.width + deltaX, 0.5, 100 - original.x);
      }
      if (gesto.canto.indexOf('n') !== -1) {
        topo = limitar(original.y + deltaY, 0, original.y + original.height - 0.5);
        altura = original.height + (original.y - topo);
      } else {
        altura = limitar(original.height + deltaY, 0.5, 100 - original.y);
      }

      elemento.x = arredondar(encaixar(esquerda));
      elemento.y = arredondar(encaixar(topo));
      elemento.width = arredondar(encaixar(largura));
      elemento.height = arredondar(encaixar(altura));
    }

    guiar(elemento, gesto.indice);
    pintar(caixas[gesto.indice], elemento, gesto.indice);
  });

  function terminarGesto() {
    if (!gesto) { return; }
    caixas[gesto.indice].classList.remove('cpo-el--movendo');
    var antes = gesto.original;
    var depois = estado.elementos[gesto.indice];
    if (antes.x !== depois.x || antes.y !== depois.y ||
        antes.width !== depois.width || antes.height !== depois.height) {
      marcarSujo();
    }
    gesto = null;
    esconderGuias();
    montarPainel();
  }

  camada.addEventListener('pointerup', terminarGesto);
  camada.addEventListener('pointercancel', terminarGesto);

  palco.addEventListener('pointerdown', function (evento) {
    if (!evento.target.closest('.cpo-el')) { selecionar(-1); }
  });

  // -----------------------------------------------------------------------
  // Teclado
  // -----------------------------------------------------------------------

  var SETAS = {
    ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]
  };

  document.addEventListener('keydown', function (evento) {
    if (!CONFIG.editavel) { return; }
    var elemento = atual();
    if (!elemento) { return; }
    var alvo = evento.target;
    // Setas dentro de um campo do painel movem o cursor, e nao o elemento.
    if (alvo && /^(INPUT|TEXTAREA|SELECT)$/.test(alvo.tagName)) { return; }

    if (evento.key === 'Delete' || evento.key === 'Backspace') {
      evento.preventDefault();
      var rotulo = CONFIG.rotulos[elemento.type] || elemento.type;
      if (window.confirm('Remover "' + rotulo + '" deste modelo?')) {
        estado.elementos.splice(estado.selecionado, 1);
        marcarSujo();
        selecionar(-1);
      }
      return;
    }

    var direcao = SETAS[evento.key];
    if (!direcao) { return; }
    evento.preventDefault();
    var passo = evento.shiftKey ? 1 : 0.1;
    elemento.x = arredondar(limitar(elemento.x + direcao[0] * passo, 0, 100 - elemento.width));
    elemento.y = arredondar(limitar(elemento.y + direcao[1] * passo, 0, 100 - elemento.height));
    marcarSujo();
    desenhar();
    montarPainel();
  });

  // -----------------------------------------------------------------------
  // Paleta
  // -----------------------------------------------------------------------

  function adicionar(tipo, posicao) {
    var padrao = CONFIG.padroes[tipo];
    if (!padrao) { return; }
    if (CONFIG.repetiveis.indexOf(tipo) === -1) {
      var existente = estado.elementos.findIndex(function (item) { return item.type === tipo; });
      if (existente !== -1) {
        selecionar(existente);
        window.alert('"' + (CONFIG.rotulos[tipo] || tipo) +
          '" ja esta no modelo. Arraste o que ja existe.');
        return;
      }
    }
    var novo = Object.assign({}, padrao);
    if (posicao) {
      novo.x = arredondar(limitar(posicao.x - novo.width / 2, 0, 100 - novo.width));
      novo.y = arredondar(limitar(posicao.y - novo.height / 2, 0, 100 - novo.height));
    }
    estado.elementos.push(novo);
    marcarSujo();
    selecionar(estado.elementos.length - 1);
  }

  if (listaPaleta) {
    listaPaleta.addEventListener('click', function (evento) {
      var item = evento.target.closest('[data-tipo]');
      if (!item || !CONFIG.editavel) { return; }
      adicionar(item.dataset.tipo, null);
    });
    listaPaleta.querySelectorAll('[data-tipo]').forEach(function (item) {
      item.addEventListener('dragstart', function (evento) {
        evento.dataTransfer.setData('text/plain', item.dataset.tipo);
        evento.dataTransfer.effectAllowed = 'copy';
      });
    });
  }

  palco.addEventListener('dragover', function (evento) {
    if (!CONFIG.editavel) { return; }
    evento.preventDefault();
    evento.dataTransfer.dropEffect = 'copy';
  });
  palco.addEventListener('drop', function (evento) {
    if (!CONFIG.editavel) { return; }
    evento.preventDefault();
    var tipo = evento.dataTransfer.getData('text/plain');
    if (!CONFIG.padroes[tipo]) { return; }
    var retangulo = area();
    adicionar(tipo, {
      x: (evento.clientX - retangulo.left) / retangulo.width * 100,
      y: (evento.clientY - retangulo.top) / retangulo.height * 100
    });
  });

  // -----------------------------------------------------------------------
  // Zoom, grade e snap
  // -----------------------------------------------------------------------

  var seletorZoom = document.getElementById('zoom');
  if (seletorZoom) {
    seletorZoom.addEventListener('change', function () {
      // A largura do PALCO, e nao um transform: as coordenadas do ponteiro
      // continuam batendo com getBoundingClientRect sem nenhuma conta a
      // mais, e o arraste nao precisa saber que existe zoom. Acima de 100%
      // o palco fica maior que a coluna, e o contêiner de rolagem cuida do
      // resto.
      palco.style.width = seletorZoom.value + '%';
      desenhar();
    });
  }
  var caixaGrade = document.getElementById('mostrar-grade');
  if (caixaGrade) {
    caixaGrade.addEventListener('change', function () {
      estado.grade = caixaGrade.checked;
      palco.classList.toggle('cpo-palco--grade', estado.grade);
    });
  }
  var caixaSnap = document.getElementById('snap-grade');
  if (caixaSnap) {
    caixaSnap.addEventListener('change', function () { estado.snap = caixaSnap.checked; });
  }

  // -----------------------------------------------------------------------
  // Serializacao
  // -----------------------------------------------------------------------

  function cookie(nome) {
    var achado = document.cookie.split(';').map(function (parte) {
      return parte.trim();
    }).find(function (parte) { return parte.indexOf(nome + '=') === 0; });
    return achado ? decodeURIComponent(achado.slice(nome.length + 1)) : '';
  }

  function mostrarErros(mensagens) {
    var caixa = document.getElementById('editor-erros');
    caixa.textContent = '';
    if (!mensagens || !mensagens.length) { caixa.hidden = true; return; }
    var lista = document.createElement('ul');
    lista.className = 'mb-0 ps-3';
    mensagens.forEach(function (mensagem) {
      var item = document.createElement('li');
      item.textContent = mensagem;
      lista.appendChild(item);
    });
    caixa.appendChild(lista);
    caixa.hidden = false;
  }

  function salvar() {
    if (!CONFIG.editavel) { return; }
    botaoSalvar.disabled = true;
    botaoSalvar.textContent = 'Salvando...';
    mostrarErros(null);

    fetch(CONFIG.urlSalvar, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': cookie('csrftoken'),
        'X-Requested-With': 'XMLHttpRequest'
      },
      credentials: 'same-origin',
      body: JSON.stringify({ elements: estado.elementos })
    }).then(function (resposta) {
      return resposta.json().then(function (dados) {
        return { status: resposta.status, dados: dados };
      });
    }).then(function (resultado) {
      botaoSalvar.disabled = false;
      botaoSalvar.textContent = 'Salvar modelo';
      if (resultado.dados.ok) {
        estado.sujo = false;
        if (aviso) { aviso.hidden = true; }
        mostrarErros(null);
        if (previewPdf) {
          previewPdf.src = CONFIG.urlPreview + '?v=' + resultado.dados.versao_do_preview;
        }
        return;
      }
      mostrarErros(resultado.dados.erros || ['Nao foi possivel salvar.']);
      if (resultado.dados.bloqueado && resultado.dados.duplicar) {
        window.location.reload();
      }
    }).catch(function () {
      botaoSalvar.disabled = false;
      botaoSalvar.textContent = 'Salvar modelo';
      mostrarErros(['Sem resposta do servidor. Confira a conexao e tente de novo.']);
    });
  }

  if (botaoSalvar) { botaoSalvar.addEventListener('click', salvar); }

  window.addEventListener('beforeunload', function (evento) {
    if (!estado.sujo) { return; }
    evento.preventDefault();
    evento.returnValue = '';
  });

  // -----------------------------------------------------------------------
  // Preview com dados de teste
  // -----------------------------------------------------------------------

  var formPreview = document.getElementById('form-preview');
  if (formPreview && previewPdf) {
    formPreview.addEventListener('submit', function (evento) {
      evento.preventDefault();
      var parametros = new URLSearchParams(new FormData(formPreview));
      parametros.set('v', String(Date.now()));
      previewPdf.src = CONFIG.urlPreview + '?' + parametros.toString();
    });
  }

  window.addEventListener('resize', desenhar);
  desenhar();
  montarPainel();
})();
