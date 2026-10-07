import httpx

from tuppence.core.errors import InputError, UserFacing, safe_error_text
from tuppence.core.secrets import SecretStoreUnavailable
from tuppence.llm.types import AllModelsFailed, LLMConnectionError, LLMHTTPError, LLMTimeout
from tuppence.net.client import LocalOnlyBlocked


def test_library_error_becomes_its_class_name_only():
    exc = httpx.LocalProtocolError("Illegal header value b'Bearer sk-LEAKED\\x0b'")
    assert safe_error_text(exc) == "LocalProtocolError"


def test_http_error_keeps_its_status_but_not_its_body():
    exc = LLMHTTPError(400, "Invalid key sk-ABCDEFGHIJKLMNOP for user")
    assert safe_error_text(exc) == "LLMHTTPError (HTTP 400)"


def test_httpx_status_error_keeps_its_status():
    request = httpx.Request("GET", "https://api.example.com/v1/models")
    response = httpx.Response(503, request=request, text="secret echo")
    exc = httpx.HTTPStatusError("Server error 'secret echo'", request=request, response=response)
    assert safe_error_text(exc) == "HTTPStatusError (HTTP 503)"


def test_connection_errors_are_worded_from_safe_facts_only():
    down = LLMConnectionError("box.lan", "Ollama")
    assert safe_error_text(down) == "Couldn't reach Ollama. Is it running?"
    assert (
        safe_error_text(LLMConnectionError("box.lan")) == "Couldn't reach box.lan. Is it running?"
    )
    assert safe_error_text(LLMTimeout("box.lan", "Ollama")) == "Ollama took too long to answer."


def test_messages_tuppence_wrote_are_kept():
    assert safe_error_text(InputError("Enter a name.")) == "Enter a name."
    unlock = SecretStoreUnavailable("Unlock your keychain.")
    assert safe_error_text(unlock) == "Unlock your keychain."
    assert "Local only is on" in safe_error_text(LocalOnlyBlocked("api.example.com"))
    assert safe_error_text(AllModelsFailed(["A / m: LLMTimeout"])).endswith("A / m: LLMTimeout")
    assert isinstance(InputError("x"), UserFacing) and isinstance(InputError("x"), ValueError)
