// Executa certificate-editor.js num DOM minimo, so para ver se ele CARREGA e
// se o painel de propriedades MONTA sem lancar. Nao valida aparencia — isso
// depende de navegador de verdade.
//
// Existe porque o defeito que quase foi commitado (um `var atual` sombreando a
// funcao atual() em todo o montarPainel) nao aparece em nenhum teste Python:
// quebraria so na tela, ao selecionar um elemento.

const fs = require('fs');
const path = require('path');

const arquivo = process.argv[2];
const dadosJson = process.argv[3];

function no(tag) {
  const alvo = {
    tagName: (tag || 'div').toUpperCase(),
    children: [],
    style: new Proxy({}, { get: (o, k) => o[k] ?? '', set: (o, k, v) => (o[k] = v, true) }),
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    options: [],
    value: '',
    _texto: '',
    // textContent = '' esvazia o no de verdade no DOM. O editor limpa o
    // painel exatamente assim, e sem isto o stub acumularia os controles de
    // todas as montagens.
    get textContent() { return alvo._texto; },
    set textContent(valor) {
      alvo._texto = String(valor);
      if (valor === '') { alvo.children.length = 0; alvo.options.length = 0; }
    },
    className: '',
    id: '',
    disabled: false,
    checked: false,
    hidden: false,
    ouvintes: {},
    appendChild(filho) {
      alvo.children.push(filho);
      if (alvo.tagName === 'SELECT') { alvo.options.push(filho); }
      return filho;
    },
    removeChild(filho) {
      const i = alvo.children.indexOf(filho);
      if (i >= 0) { alvo.children.splice(i, 1); }
      return filho;
    },
    insertBefore(filho) { alvo.children.unshift(filho); return filho; },
    addEventListener(nome, fn) { (alvo.ouvintes[nome] ||= []).push(fn); },
    removeEventListener() {},
    setAttribute(nome, valor) { alvo[nome] = valor; },
    getAttribute(nome) { return alvo[nome]; },
    remove() {},
    focus() {},
    querySelector: () => null,
    querySelectorAll: () => [],
    // O bastante para o `evento.target.closest('.cpo-el')` do editor.
    closest(seletor) {
      const classe = seletor.replace('.', '');
      return (alvo.className || '').split(/\s+/).includes(classe) ? alvo : null;
    },
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 1000, height: 707 }),
    setPointerCapture() {},
    releasePointerCapture() {},
    disparar(nome, evento) {
      (alvo.ouvintes[nome] || []).forEach((fn) => fn(evento || { preventDefault() {}, stopPropagation() {} }));
    },
    get firstChild() { return alvo.children[0] || null; },
  };
  return alvo;
}

// Os dados que o json_script entregaria. Sao os MESMOS que a view produz —
// gerados por manage.py e gravados ao lado deste arquivo.
const dados = JSON.parse(fs.readFileSync(dadosJson, 'utf8'));

const registrados = {};
function registrar(id, extras) {
  const alvo = no('div');
  alvo.id = id;
  Object.assign(alvo, extras || {});
  registrados[id] = alvo;
  return alvo;
}

registrar('editor-certificado').dataset = {
  urlSalvar: '/salvar/', urlPreview: '/preview.pdf', editavel: '1',
};
registrar('palco').dataset = { larguraMm: '297', alturaMm: '210' };
['palco-camada', 'painel-propriedades', 'painel-vazio', 'paleta', 'btn-salvar',
 'editor-aviso', 'editor-contador', 'preview-pdf', 'guia-h', 'guia-v',
 'zoom', 'mostrar-grade', 'snap-grade', 'editor-erros', 'form-preview',
].forEach((id) => registrar(id));

for (const [chave, valor] of Object.entries(dados)) {
  registrar(chave, { textContent: JSON.stringify(valor) });
}

global.document = {
  getElementById: (id) => registrados[id] || null,
  createElement: (tag) => no(tag),
  addEventListener() {},
  body: no('body'),
  documentElement: no('html'),
  cookie: 'csrftoken=teste',
};
global.window = { addEventListener() {}, devicePixelRatio: 1 };
global.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
global.confirm = () => true;
global.FormData = class { constructor() {} append() {} };
global.URLSearchParams = URLSearchParams;

const fonte = fs.readFileSync(arquivo, 'utf8');
new Function(fonte)();

// --- as verificacoes -------------------------------------------------------

const painel = registrados['painel-propriedades'];

function textos(alvo, saida = []) {
  if (alvo.textContent) { saida.push(alvo.textContent); }
  (alvo.children || []).forEach((f) => textos(f, saida));
  return saida;
}

function seletor(id, alvo = painel) {
  if (alvo.id === id) { return alvo; }
  for (const filho of alvo.children || []) {
    const achado = seletor(id, filho);
    if (achado) { return achado; }
  }
  return null;
}

let falhas = 0;
function conferir(descricao, condicao) {
  if (condicao) { console.log('  ok   ' + descricao); }
  else { console.log('  FALHA ' + descricao); falhas += 1; }
}

// A paleta e HTML do servidor; o editor so escuta cliques nela. O que ele
// desenha e o palco.
const camada = registrados['palco-camada'];
const caixas = camada.children;
conferir('o palco desenhou os elementos', caixas.length === dados['dados-elementos'].length);

// Selecionar o primeiro elemento monta o painel. E aqui que o sombreamento de
// `atual` estourava — a razao inteira de este arquivo existir.
camada.disparar('pointerdown', {
  button: 0, pointerId: 1, clientX: 100, clientY: 100,
  target: caixas[0], preventDefault() {}, stopPropagation() {},
});

const conteudo = textos(painel).join(' | ');
conferir('o painel montou alguma coisa', painel.children.length > 0);
conferir('tem seletor de fonte', !!seletor('prop-font'));
conferir('tem seletor de peso para Montserrat', !!seletor('prop-weight'));
conferir('nao existe mais caixa de negrito', !seletor('prop-bold'));
conferir('as familias novas aparecem', /Bodoni Moda/.test(conteudo) && /Great Vibes/.test(conteudo));

// Trocar para uma caligrafica: o seletor de peso some e aparece o aviso.
const selFonte = seletor('prop-font');
selFonte.value = 'GREAT_VIBES';
selFonte.disparar('change');

const depois = textos(painel).join(' | ');
conferir('caligrafica perde o seletor de peso', !seletor('prop-weight'));
conferir('caligrafica explica que tem um desenho so', /um desenho so/.test(depois));
conferir('caligrafica perde o italico', !seletor('prop-italic'));

// E voltar para Montserrat traz os dois de volta.
seletor('prop-font').value = 'MONTSERRAT';
seletor('prop-font').disparar('change');
conferir('voltando, o peso reaparece', !!seletor('prop-weight'));
conferir('voltando, o italico reaparece', !!seletor('prop-italic'));

console.log(falhas ? '\nFALHOU: ' + falhas : '\nTUDO OK');
process.exit(falhas ? 1 : 0);
