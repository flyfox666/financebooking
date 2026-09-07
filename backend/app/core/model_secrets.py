"""Encrypt provider credentials with an installation-specific persistent key."""
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import TypeDecorator, Text
from app.core.config import get_settings

PREFIX = 'enc:v1:'


def key_path():
    return Path(get_settings().ATTACHMENTS_DIR).parent / '.model_key'


def cipher():
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as handle:
            handle.write(Fernet.generate_key())
        path.chmod(0o600)
    except FileExistsError:
        pass
    return Fernet(path.read_bytes())


def encrypt(value):
    if not value or value.startswith(PREFIX):
        return value
    return PREFIX + cipher().encrypt(value.encode()).decode()


def decrypt(value):
    if not value or not value.startswith(PREFIX):
        return value
    try:
        return cipher().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        raise RuntimeError('模型密钥无法解密，请恢复配套密钥文件或重新配置模型') from None


class EncryptedCredential(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt(value)

    def process_result_value(self, value, dialect):
        return decrypt(value)


def migrate_credentials(db):
    from sqlalchemy import text
    rows = db.execute(text('SELECT id, api_key FROM llm_provider')).all()
    for ident, value in rows:
        if value and not value.startswith(PREFIX):
            db.execute(text('UPDATE llm_provider SET api_key=:value WHERE id=:id'),
                       {'value':encrypt(value), 'id':ident})
    db.commit()
