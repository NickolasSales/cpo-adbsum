"""Manager do modelo de usuario."""

from django.contrib.auth.base_user import BaseUserManager


def normalizar_email(email):
    """
    Normaliza um e-mail para a forma canonica usada no banco.

    O normalize_email do Django coloca em minusculas apenas a parte do
    dominio, preservando o case da parte local. Para este sistema isso nao
    basta: "Aluno@Email.com" e "aluno@email.com" precisam ser a mesma conta,
    entao normalizamos o endereco inteiro para minusculas.
    """
    if not email:
        return email
    return BaseUserManager.normalize_email(email).strip().lower()


class UserManager(BaseUserManager):
    """Cria usuarios identificados por e-mail, sem username."""

    use_in_migrations = True

    def get_by_natural_key(self, username):
        """
        Busca pelo e-mail normalizado.

        O ModelBackend do Django chama este metodo durante a autenticacao.
        Normalizando aqui, o login passa a ser insensivel a maiusculas sem
        precisar de um backend de autenticacao customizado.
        """
        return self.get(**{self.model.USERNAME_FIELD: normalizar_email(username)})

    def _create_user(self, email, full_name, password, **extra_fields):
        if not email:
            raise ValueError("O e-mail e obrigatorio.")
        if not full_name:
            raise ValueError("O nome completo e obrigatorio.")

        user = self.model(
            email=normalizar_email(email),
            full_name=full_name.strip(),
            **extra_fields,
        )
        # set_password aplica o hashing do Django. A senha em texto puro nunca
        # e atribuida ao campo diretamente e nunca e persistida.
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, full_name, password=None, **extra_fields):
        from accounts.models import UserRole

        extra_fields.setdefault("role", UserRole.STUDENT)
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, full_name, password, **extra_fields)

    def create_superuser(self, email, full_name, password=None, **extra_fields):
        """
        Cria um administrador.

        O superusuario e sempre ADMIN e nunca nasce obrigado a trocar a senha:
        ele acabou de escolhe-la no proprio comando.
        """
        from accounts.models import UserRole

        extra_fields.setdefault("role", UserRole.ADMIN)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("must_change_password", False)

        if extra_fields["role"] != UserRole.ADMIN:
            raise ValueError("Um superusuario precisa ter o papel ADMIN.")
        if extra_fields["is_staff"] is not True:
            raise ValueError("Um superusuario precisa ter is_staff=True.")
        if extra_fields["is_superuser"] is not True:
            raise ValueError("Um superusuario precisa ter is_superuser=True.")

        return self._create_user(email, full_name, password, **extra_fields)
