"""In-memory keychains for tests (never the developer's real OS keychain)."""

import keyring
import keyring.errors
from keyring.backend import KeyringBackend


class RefusingKeyring(KeyringBackend):
    """A keychain that can refuse reads, writes or deletes, as a re-locked or denied one does."""

    priority = 5

    def __init__(self, refuse=(), error=None):
        super().__init__()
        self.data = {}
        self.refuse = set(refuse)
        self.error = error or (lambda: keyring.errors.KeyringLocked("locked: backend detail"))

    def get_password(self, service, username):
        if "get" in self.refuse:
            raise self.error()
        return self.data.get((service, username))

    def set_password(self, service, username, password):
        if "set" in self.refuse:
            raise self.error()
        self.data[(service, username)] = password

    def delete_password(self, service, username):
        if "delete" in self.refuse:
            raise self.error()
        if (service, username) not in self.data:
            raise keyring.errors.PasswordDeleteError("not found")
        del self.data[(service, username)]
