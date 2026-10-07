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


def test_main_checks_file_paths_too(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    (repo / "secretword-notes.md").write_text("clean\n", encoding="utf-8")
    monkeypatch.setenv("TUPPENCE_DENYLIST", "secretword")
    assert guard.main(["--root", str(repo)]) == 1


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
