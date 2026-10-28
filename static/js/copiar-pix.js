/*
 * Botao "Copiar Pix" do Portal do Ministro.
 *
 * So copia texto que ja esta na pagina: nao chama o servidor, nao registra
 * nada, nao muda a situacao da mensalidade.
 *
 *   <button data-copiar="id-do-elemento" data-copiado="Pix copiado!">
 *
 * Clipboard API quando existe (HTTPS ou localhost). Sem ela, tenta o
 * execCommand antigo; se nada funcionar, seleciona o codigo para o ministro
 * copiar pelo menu do proprio aparelho.
 */
(function () {
  "use strict";

  function selecionar(elemento) {
    var faixa = document.createRange();
    faixa.selectNodeContents(elemento);
    var selecao = window.getSelection();
    selecao.removeAllRanges();
    selecao.addRange(faixa);
  }

  function copiarAntigo(elemento) {
    selecionar(elemento);
    try {
      return document.execCommand("copy");
    } catch (erro) {
      return false;
    }
  }

  function avisar(botao, texto, deuCerto) {
    var retorno = document.getElementById("pix-retorno");
    if (retorno) {
      retorno.textContent = texto;
      // Com sucesso, o botao ja diz "Pix copiado!": a linha fica so para
      // leitor de tela. No fallback ela aparece, porque explica o que fazer.
      retorno.classList.toggle("visually-hidden", deuCerto);
    }
    if (deuCerto) {
      var original = botao.getAttribute("data-original") || botao.textContent;
      botao.setAttribute("data-original", original);
      botao.textContent = botao.getAttribute("data-copiado") || "Copiado!";
      window.setTimeout(function () {
        botao.textContent = original;
      }, 3000);
    }
  }

  document.addEventListener("click", function (evento) {
    var botao = evento.target.closest("[data-copiar]");
    if (!botao) {
      return;
    }
    var alvo = document.getElementById(botao.getAttribute("data-copiar"));
    if (!alvo) {
      return;
    }
    var texto = alvo.textContent.trim();
    var falhou = function () {
      if (copiarAntigo(alvo)) {
        avisar(botao, "Pix copiado!", true);
      } else {
        selecionar(alvo);
        avisar(botao, "Não foi possível copiar automaticamente. O código está selecionado: copie pelo menu do aparelho.", false);
      }
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(texto).then(function () {
        avisar(botao, "Pix copiado!", true);
      }, falhou);
    } else {
      falhou();
    }
  });
})();
