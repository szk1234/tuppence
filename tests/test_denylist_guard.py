import subprocess

import denylist_guard as guard


def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def test_matches_whole_words_case_insensitively():
    patterns = guard.compile_terms(["Alex Example"])
    assert guard.scan_text("Paid ALEX EXAMPLE £5", patterns) == [(1, 0)]


def test_ignores_substrings_inside_words():
    patterns = guard.compile_terms(["r exa", "mple"])
    assert guard.scan_text("for example\nsimple plan", patterns) == []


def test_load_terms_merges_env_file_and_skips_comments(tmp_path):
    f = tmp_path / "deny.txt"
    f.write_text("# comment\nsecretword\n\n", encoding="utf-8")
    terms = guard.load_terms(
        {"TUPPENCE_DENYLIST": "alpha\nbeta", "TUPPENCE_DENYLIST_FILE": str(f)}, None
    )
    assert terms == ["alpha", "beta", "secretword"]


def test_main_reports_term_index_not_term(tmp_path, capsys, monkeypatch):
    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("hello\nwe paid Secretword today\n", encoding="utf-8")
    monkeypatch.setenv("TUPPENCE_DENYLIST", "secretword")
    monkeypatch.delenv("TUPPENCE_DENYLIST_FILE", raising=False)
    rc = guard.main(["--root", str(repo)])
    out = capsys.readouterr()
    assert rc == 1
    assert "notes.md:2" in out.out + out.err
    assert "term #1" in out.out + out.err
    assert "secretword" not in (out.out + out.err).lower()


def test_main_checks_file_paths_too(tmp_path, capsys, monkeypatch):
    repo = _git_repo(tmp_path)
    (repo / "secretword-notes.md").write_text("clean\n", encoding="utf-8")
    monkeypatch.setenv("TUPPENCE_DENYLIST", "secretword")
    monkeypatch.delenv("TUPPENCE_DENYLIST_FILE", raising=False)
    assert guard.main(["--root", str(repo)]) == 1
    out = capsys.readouterr()
    combined = out.out + out.err
    assert "secretword" not in combined.lower()
    assert "[redacted]-notes.md" in combined
    assert "term #1" in combined


def test_redact_replaces_every_match_case_insensitively():
    patterns = guard.compile_terms(["Alex Example"])
    assert guard.redact("alex example/ALEX EXAMPLE.md", patterns) == "[redacted]/[redacted].md"


def test_no_terms_passes_unless_required(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    monkeypatch.delenv("TUPPENCE_DENYLIST", raising=False)
    monkeypatch.delenv("TUPPENCE_DENYLIST_FILE", raising=False)
    assert guard.main(["--root", str(repo), "--no-git-config"]) == 0
    assert guard.main(["--root", str(repo), "--no-git-config", "--require"]) == 2


def test_clean_repo_passes(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    (repo / "a.txt").write_text("nothing to see\n", encoding="utf-8")
    monkeypatch.setenv("TUPPENCE_DENYLIST", "secretword")
    assert guard.main(["--root", str(repo)]) == 0


def _commit(repo, message="msg"):
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@example.invalid",
            "commit",
            "-q",
            "-m",
            message,
        ],
        cwd=repo,
        check=True,
    )


def _head(repo):
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def _env(monkeypatch, terms="secretword"):
    monkeypatch.setenv("TUPPENCE_DENYLIST", terms)
    monkeypatch.delenv("TUPPENCE_DENYLIST_FILE", raising=False)


def test_missing_env_file_fails_closed(tmp_path, capsys, monkeypatch):
    repo = _git_repo(tmp_path)
    monkeypatch.delenv("TUPPENCE_DENYLIST", raising=False)
    monkeypatch.setenv("TUPPENCE_DENYLIST_FILE", str(tmp_path / "nope.txt"))
    assert guard.main(["--root", str(repo)]) == 2
    out = capsys.readouterr()
    assert "denylist: configured denylist file not found" in out.err
    assert "nope.txt" not in out.out + out.err


def test_missing_git_config_file_fails_closed(tmp_path, capsys, monkeypatch):
    repo = _git_repo(tmp_path)
    _env(monkeypatch, "")
    subprocess.run(
        ["git", "config", "tuppence.denylistFile", str(tmp_path / "gone.txt")],
        cwd=repo,
        check=True,
    )
    assert guard.main(["--root", str(repo)]) == 2
    assert "configured denylist file not found" in capsys.readouterr().err


def test_redact_is_single_pass_for_overlapping_terms():
    patterns = guard.compile_terms(["Alex", "Alex Example"])
    assert guard.redact("Alex Example and alex", patterns) == "[redacted] and [redacted]"
    patterns = guard.compile_terms(["Alex Example", "Alex"])
    assert guard.redact("Alex Example", patterns) == "[redacted]"


def test_staged_scans_index_not_worktree(tmp_path, capsys, monkeypatch):
    repo = _git_repo(tmp_path)
    _env(monkeypatch)
    f = repo / "a.txt"
    f.write_text("we paid secretword\\n".replace("\\n", "\n"), encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    f.write_text("clean now\n", encoding="utf-8")
    assert guard.main(["--root", str(repo), "--staged"]) == 1
    out = capsys.readouterr()
    assert "a.txt:1" in out.out and "term #1" in out.out
    f.unlink()
    assert guard.main(["--root", str(repo), "--staged"]) == 1


def test_staged_checks_paths_and_passes_when_clean(tmp_path, capsys, monkeypatch):
    repo = _git_repo(tmp_path)
    _env(monkeypatch)
    (repo / "ok.txt").write_text("fine\n", encoding="utf-8")
    subprocess.run(["git", "add", "ok.txt"], cwd=repo, check=True)
    assert guard.main(["--root", str(repo), "--staged"]) == 0
    (repo / "secretword.txt").write_text("fine\n", encoding="utf-8")
    subprocess.run(["git", "add", "secretword.txt"], cwd=repo, check=True)
    assert guard.main(["--root", str(repo), "--staged"]) == 1
    combined = "".join(capsys.readouterr())
    assert "secretword" not in combined.lower()
    assert "[redacted].txt" in combined


def test_message_file_mode(tmp_path, capsys, monkeypatch):
    repo = _git_repo(tmp_path)
    _env(monkeypatch)
    msg = tmp_path / "MSG"
    msg.write_text("Add thing\n\nfor Secretword\n", encoding="utf-8")
    assert guard.main(["--root", str(repo), "--message-file", str(msg)]) == 1
    out = capsys.readouterr()
    assert "commit message:3" in out.out and "term #1" in out.out
    assert "secretword" not in (out.out + out.err).lower()
    msg.write_text("Add thing\n", encoding="utf-8")
    assert guard.main(["--root", str(repo), "--message-file", str(msg)]) == 0


def test_range_mode_scans_patches_and_messages(tmp_path, capsys, monkeypatch):
    repo = _git_repo(tmp_path)
    _env(monkeypatch)
    (repo / "a.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    _commit(repo, "base")
    base = _head(repo)
    # patch leak, later scrubbed from the tip: history still matters
    (repo / "a.txt").write_text("secretword\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    _commit(repo, "second")
    leaky = _head(repo)
    (repo / "a.txt").write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    _commit(repo, "third")
    tip = _head(repo)
    assert guard.main(["--root", str(repo), "--range", f"{base}..{tip}"]) == 1
    out = capsys.readouterr()
    assert f"commit {leaky[:10]}: matches denylisted term #1" in out.out
    assert "secretword" not in (out.out + out.err).lower()
    # the scrubbing commit's patch still shows the removed line, so it is flagged too
    assert guard.main(["--root", str(repo), "--range", f"{leaky}..{tip}"]) == 1
    assert guard.main(["--root", str(repo), "--range", f"{base}..{base}"]) == 0
    # message leak
    (repo / "b.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    _commit(repo, "mentions Secretword")
    assert guard.main(["--root", str(repo), "--range", f"{tip}..HEAD"]) == 1
    # new-branch style range
    assert guard.main(["--root", str(repo), "--range", "HEAD --not --remotes"]) == 1


def test_range_rejects_option_injection(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    _env(monkeypatch)
    assert guard.main(["--root", str(repo), "--range", "--output=x HEAD"]) == 2
