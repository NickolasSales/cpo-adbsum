/*
 * Envio de comprovante no Portal do Ministro.
 *
 *   <form data-envio-comprovante>
 *     <input type="file" accept="..." data-limite="5242880">
 *     <span data-nome-do-arquivo></span>
 *     <p data-erro-do-arquivo></p>
 *     <button data-enviar>
 *
 * Tres coisas, todas de conforto: mostra o nome do arquivo escolhido, avisa
 * ANTES de enviar quando o formato ou o tamanho nao servem (poupa o 4G de
 * mandar 8 MB para ouvir "nao"), e troca o botao por "Enviando..." no
 * envio, bloqueando o segundo clique. Quem decide e o servidor: ele confere
 * extensao, tipo, conteudo e tamanho de novo, e recusa um clique duplo que
 * passe daqui.
 */
(function () {
  "use strict";

  var EXTENSOES = /\.(pdf|jpe?g|png)$/i;

  function megabytes(bytes) {
    return (bytes / (1024 * 1024)).toFixed(1).replace(".", ",");
  }

  function problema(arquivo, limite) {
    if (!EXTENSOES.test(arquivo.name)) {
      return "Formato não aceito. Envie o comprovante em PDF, JPG ou PNG.";
    }
    if (arquivo.size === 0) {
      return "O arquivo escolhido está vazio.";
    }
    if (limite && arquivo.size > limite) {
      return "O arquivo tem " + megabytes(arquivo.size) + " MB. O limite é " +
        Math.round(limite / (1024 * 1024)) + " MB.";
    }
    return "";
  }

  document.querySelectorAll("form[data-envio-comprovante]").forEach(function (form) {
    var entrada = form.querySelector("input[type=file]");
    var nome = form.querySelector("[data-nome-do-arquivo]");
    var erro = form.querySelector("[data-erro-do-arquivo]");
    var botao = form.querySelector("[data-enviar]");
    var limite = parseInt(entrada.getAttribute("data-limite"), 10) || 0;
    var enviando = false;

    function avisar(texto) {
      erro.textContent = texto;
    }

    entrada.addEventListener("change", function () {
      var arquivo = entrada.files && entrada.files[0];
      nome.textContent = arquivo ? arquivo.name : "";
      avisar(arquivo ? problema(arquivo, limite) : "");
    });

    form.addEventListener("submit", function (evento) {
      if (enviando) {
        evento.preventDefault();
        return;
      }
      var arquivo = entrada.files && entrada.files[0];
      var texto = arquivo ? problema(arquivo, limite) : "Selecione o comprovante.";
      if (texto) {
        evento.preventDefault();
        avisar(texto);
        entrada.focus();
        return;
      }
      enviando = true;
      botao.disabled = true;
      botao.textContent = "Enviando...";
    });

    // Voltar pelo historico (bfcache) traz a pagina como foi deixada: com o
    // botao travado em "Enviando...". Destrava.
    window.addEventListener("pageshow", function () {
      enviando = false;
      botao.disabled = false;
      botao.textContent = "Enviar comprovante";
    });
  });
})();
