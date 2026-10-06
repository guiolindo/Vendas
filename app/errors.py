"""Erros de negócio: sempre carregam uma mensagem pronta para o usuário."""


class BusinessError(Exception):
    """Operação recusada por uma regra de negócio. A mensagem é exibida ao usuário."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


class NotFound(BusinessError):
    """Registro inexistente."""
