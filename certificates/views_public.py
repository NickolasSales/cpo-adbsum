"""
Validacao publica de certificado.

Uma unica tela, sem autenticacao, alcancada pelo QR Code impresso no
documento. Quem abre e alguem conferindo se um papel na mao dele e verdadeiro:
pode ser uma secretaria, um pastor de outra igreja, um empregador.

O que a pagina responde
-----------------------
    codigo existe e ativo      "Certificado valido" + os dados do documento
    codigo existe e revogado   "Certificado revogado" + os mesmos dados
    codigo nao existe          404

Revogado continua mostrando nome, modulo e data de proposito. Quem esta com o
papel na mao precisa saber que AQUELE documento perdeu a validade — uma
pagina generica de "invalido" deixaria a duvida de ter digitado errado.

O que ela nunca mostra
----------------------
E-mail, nota, respostas, gabarito, numero da tentativa, IP, user-agent ou
qualquer id interno. A pagina e publica: tudo que aparece aqui e visivel para
quem tiver o codigo. Por isso o contexto e montado apenas com os campos
*_snapshot do certificado, e nao com o objeto da tentativa — nao ha como vazar
o que nao esta no contexto.
"""

from django.http import Http404
from django.views.generic import TemplateView

from certificates.models import Certificate


class CertificateValidateView(TemplateView):
    """Pagina publica de conferencia. Somente leitura, sem sessao."""

    template_name = "certificates/validate.html"

    def get(self, request, *args, **kwargs):
        self.certificado = self._certificado_ou_404(kwargs["verification_code"])
        return super().get(request, *args, **kwargs)

    def _certificado_ou_404(self, codigo):
        """
        404 para codigo inexistente, sem distinguir de codigo malformado.

        Uma mensagem diferente para "formato invalido" e "nao encontrado"
        ajudaria alguem a calibrar uma tentativa de adivinhacao. Como o codigo
        e UUID4, adivinhar ja e inviavel; nao ha motivo para facilitar.
        """
        certificado = Certificate.objects.filter(verification_code=codigo).first()
        if certificado is None:
            raise Http404("Certificado nao encontrado.")
        return certificado

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        certificado = self.certificado

        # Campos escalares, um a um. Passar o objeto inteiro daria ao template
        # acesso a certificado.attempt e, por ali, ao aluno, as respostas e a
        # nota — numa pagina sem autenticacao.
        contexto["valido"] = certificado.esta_valido
        contexto["codigo"] = str(certificado.verification_code)
        contexto["nome"] = certificado.student_name_snapshot
        contexto["modulo"] = certificado.module_name_snapshot
        contexto["prova"] = certificado.exam_title_snapshot
        contexto["instituicao"] = certificado.institution_name_snapshot
        contexto["emitido_em"] = certificado.issued_at
        contexto["revogado_em"] = certificado.revoked_at
        return contexto
