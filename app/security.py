"""Segurança: hash de senhas com Bcrypt e tokens de sessão assinados (HMAC).

* Senhas são armazenadas exclusivamente como hash Bcrypt com fator de custo 12
  e salt aleatório. Texto limpo, MD5 ou SHA-1 nunca são usados (OWASP A02).
* Tokens de sessão são ``payload.assinatura`` com HMAC-SHA256 e expiração,
  verificados em tempo constante (OWASP A07).
"""

import base64
import hashlib
import hmac
import os
import time

import bcrypt

BCRYPT_ROUNDS = 12
_BCRYPT_MAX_BYTES = 72
_PREFIXOS_BCRYPT = ("$2a$", "$2b$", "$2y$")

SECRET_KEY = os.getenv("SECRET_KEY", "novaaba-chave-de-desenvolvimento-troque-em-producao")
TOKEN_VALIDADE_SEGUNDOS = 8 * 60 * 60  # um turno de atendimento
COOKIE_SESSAO = "novaaba_sessao"


# --------------------------------------------------------------------------- #
# Senhas
# --------------------------------------------------------------------------- #
def _codificar(senha: str) -> bytes:
    # Bcrypt considera no máximo 72 bytes; truncamos explicitamente para evitar
    # ValueError em versões recentes da biblioteca com senhas multibyte longas.
    return senha.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_senha(senha: str) -> str:
    """Gera o hash Bcrypt (custo 12, salt aleatório) de uma senha."""
    if not senha:
        raise ValueError("A senha não pode ser vazia.")
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(_codificar(senha), salt).decode("utf-8")


def eh_hash_bcrypt(valor: str) -> bool:
    return bool(valor) and valor.startswith(_PREFIXOS_BCRYPT) and len(valor) == 60


def verificar_senha(senha: str, senha_hash: str) -> bool:
    """Confere a senha contra o hash; qualquer formato que não seja Bcrypt é recusado."""
    if not senha or not eh_hash_bcrypt(senha_hash):
        return False
    try:
        return bcrypt.checkpw(_codificar(senha), senha_hash.encode("utf-8"))
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# Tokens de sessão
# --------------------------------------------------------------------------- #
def _b64(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode("ascii")


def _b64_decode(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def _assinar(payload: str, chave: str) -> str:
    return _b64(hmac.new(chave.encode(), payload.encode(), hashlib.sha256).digest())


def criar_token_sessao(
    usuario_id: int,
    validade_segundos: int = TOKEN_VALIDADE_SEGUNDOS,
    chave: str | None = None,
    agora: float | None = None,
) -> str:
    """Cria um token ``<payload>.<assinatura>`` com o id do usuário e a expiração."""
    expira = int((agora if agora is not None else time.time()) + validade_segundos)
    payload = _b64(f"{usuario_id}:{expira}".encode())
    return f"{payload}.{_assinar(payload, chave or SECRET_KEY)}"


def ler_token_sessao(
    token: str | None, chave: str | None = None, agora: float | None = None
) -> int | None:
    """Retorna o id do usuário se o token for íntegro e não expirado; senão ``None``."""
    if not token or token.count(".") != 1:
        return None
    payload, assinatura = token.split(".")
    if not hmac.compare_digest(assinatura, _assinar(payload, chave or SECRET_KEY)):
        return None
    try:
        usuario_id, expira = _b64_decode(payload).decode().split(":")
        usuario_id_int, expira_int = int(usuario_id), int(expira)
    except (ValueError, UnicodeDecodeError):
        return None
    if expira_int < (agora if agora is not None else time.time()):
        return None
    return usuario_id_int
