"""`evals.run --model <connection>/<model>` through the app's own LLM client, against a
scripted local server (the fake LLM answers Tuppence's read prompts with the oracle)."""

import pytest
from evals.corpus import CASES
from evals.harness import run_case
from evals.run import real_model

from fakes.fake_llm import create_fake_app
from tuppence.app.services import build_services
from tuppence.desktop.launcher import ServerThread, bind_loopback_socket
from tuppence.paths import acquire_instance_lock
from tuppence.settings import RuntimeSettings


@pytest.fixture
def fake():
    sock = bind_loopback_socket()
    server = ServerThread(create_fake_app(), "127.0.0.1", sock.getsockname()[1], sock)
    server.start()
    try:
        server.wait_until_healthy()
        yield server
    finally:
        server.stop()


def test_a_real_model_runs_an_ai_case_through_the_app_client(tmp_path, fake):
    services = build_services(RuntimeSettings.for_mode("local", data_dir=tmp_path))
    connection = services.connections.create("ollama", base_url=f"{fake.url}v1")
    assert connection.is_local
    services.connections.test(connection.id)
    llm, window, label = real_model(f"{connection.name}/fake-small", str(tmp_path))
    assert label == f"{connection.name}/fake-small"
    assert window
    case = next(c for c in CASES if c.id == "pdf-card-text")
    result = run_case(case, llm=llm, context_window=window)
    assert result.ok, result.errors
    assert result.llm_calls >= 1
    assert result.cost_gbp == 0.0  # a local model costs nothing
    # the call went through the guarded client, so the privacy log recorded it
    assert services.privacy_log.list(limit=5)


def test_a_data_folder_in_use_is_refused(tmp_path):
    lock = acquire_instance_lock(tmp_path)
    try:
        with pytest.raises(SystemExit, match="Close Tuppence first"):
            real_model("Ollama/anything", str(tmp_path))
    finally:
        lock.release()


def test_a_cloud_connection_needs_allow_cloud(tmp_path, fake):
    """Task 12 minor: the eval sends the synthetic corpus to the model, so a cloud model is
    used only when asked for explicitly."""
    services = build_services(RuntimeSettings.for_mode("local", data_dir=tmp_path))
    connection = services.connections.create("openai", api_key="sk-x", base_url=f"{fake.url}v1")
    assert not connection.is_local
    services.connections.test(connection.id)
    with pytest.raises(SystemExit, match="--allow-cloud"):
        real_model(f"{connection.name}/fake-small", str(tmp_path))
    llm, _, label = real_model(f"{connection.name}/fake-small", str(tmp_path), allow_cloud=True)
    assert label == f"{connection.name}/fake-small"


def test_the_eval_spends_at_most_two_pounds_by_default():
    from evals.harness import DEFAULT_MAX_GBP, eval_budget
    from evals.run import build_parser

    assert DEFAULT_MAX_GBP == 2.0 and eval_budget().max_gbp == 2.0
    args = build_parser().parse_args(["--model", "x/y"])
    assert args.max_gbp == 2.0 and args.allow_cloud is False


def test_saved_results_hold_plain_messages_only():
    from evals.harness import plain_errors

    errors = [
        'L2: amount_text "ACME PAYROLL 900.00" not found on line',
        "P1L4: sign mismatch (line shows 42.18, amount is 42.18)",
        "statement period_start missing",
    ]
    plain = plain_errors(errors)
    assert not any("ACME" in e or "42.18" in e or "L2" in e for e in plain)
    assert plain  # still says what went wrong
