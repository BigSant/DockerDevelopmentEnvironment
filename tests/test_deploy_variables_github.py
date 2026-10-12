"""make deploy-variables for GitHub Actions against a fake GitHub API and a fake `gh` (no network).

The fake API keeps environments, environment secrets (names only are listed) and variables in memory and
pages them by its own small page size (secrets 10, variables 1) whatever per_page asks. Tokens:
good-token-* may do everything, readonly-token-* gets 403 on writes, any other 401. The fake `gh secret
set` reads the value from stdin and stores it through the fake API, logging its arguments. No output may
show a value (they contain VALUE) or a token (T0KEN), and no `gh` command line may hold either.
"""

import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker")]
from project_ci import ci_provider, deploy_variables
from project_github import repository

REPOSITORY = "owner/demo"
BASE = f"/repos/{REPOSITORY}"
KEYS = " ".join(f"KEY_{number:02}" for number in range(1, 9))
COMMON = f"""\
PROJECT_NAME=demo
CI_PROVIDER=github
DEPLOY_GITHUB_REPOSITORY={REPOSITORY}
SERVICES=metrics
DEPLOY_REQUIRED_VARIABLES="SEARCH_KEY HTPASSWD_WEB"
DEPLOY_PLAIN_VARIABLES="GRAFANA_BASE_URL ERP_EMPLOYEE_ID"
APP_ENVIRONMENT="ERP_API_KEY ERP_EMPLOYEE_ID GRAFANA_BASE_URL GRAFANA_TOKEN {KEYS}"
"""
FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys, urllib.request
args = sys.argv[1:]
with open(os.environ['GH_LOG'], 'a') as log:
    log.write(json.dumps({'args': args, 'token': bool(os.environ.get('GH_TOKEN')),
                          'other_tokens': [k for k in ('GITHUB_TOKEN',) if k in os.environ]}) + '\n')
if os.environ.get('GH_FAIL') == args[2]:
    print('failed to set secret: HTTP 422', file=sys.stderr)
    sys.exit(1)
value = sys.stdin.read()
environment, repository = args[args.index('--env') + 1], args[args.index('--repo') + 1]
request = urllib.request.Request(
    f"{os.environ['GITHUB_API_URL']}/repos/{repository}/environments/{environment}/secrets/{args[2]}",
    data=json.dumps({'encrypted_value': 'sealed:' + value.rstrip('\r\n'), 'key_id': 'k1'}).encode(), method='PUT',
    headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Content-Type': 'application/json'})
try:
    urllib.request.urlopen(request)
except urllib.error.HTTPError as error:
    print(f'failed to set secret: HTTP {error.code}', file=sys.stderr)
    sys.exit(1)
print(f'Set Actions secret {args[2]} for {repository}', file=sys.stderr)
'''


class FakeGitHub(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def answer(self, status, data=None):
        payload = json.dumps(data).encode() if data is not None else b""
        self.send_response(status)
        if payload:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def handle_any(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        url = urllib.parse.urlsplit(self.path)
        path, query = urllib.parse.unquote(url.path), urllib.parse.parse_qs(url.query)
        token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
        self.server.requests.append({"method": method, "path": path, "query": url.query, "body": body,
                                     "version": self.headers.get("X-GitHub-Api-Version")})
        if not token.startswith(("good-token-", "readonly-token-")):
            return self.answer(401, {"message": "Bad credentials", "status": "401"})
        if method != "GET" and not token.startswith("good-token-"):
            return self.answer(403, {"message": "Resource not accessible by personal access token", "status": "403"})
        state = self.server.state
        if path == f"{BASE}/environments" and method == "GET":
            return self.answer(200, {"total_count": len(state), "environments": [{"name": name} for name in state]})
        match = re.fullmatch(f"{BASE}/environments/([^/]+)/(secrets|variables)(?:/([^/]+))?", path)
        if not match or match[1] not in state:
            return self.answer(404, {"message": "Not Found", "status": "404"})
        environment, kind, name = match[1], match[2], match[3]
        store = state[environment][kind]
        if method == "GET" and not name:
            per_page = int(query.get("per_page", ["30"])[0])
            if per_page > {"secrets": 100, "variables": 30}[kind]:
                return self.answer(422, {"message": "per_page too large"})
            size, page = min(per_page, self.server.page_size[kind]), int(query.get("page", ["1"])[0])
            names = sorted(store)[(page - 1) * size:page * size]
            items = [{"name": n, "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}
                     | ({"value": store[n]} if kind == "variables" else {}) for n in names]
            return self.answer(200, {"total_count": len(store), kind: items})
        if kind == "secrets" and method == "PUT" and name:
            created = name not in store
            store[name] = body["encrypted_value"]
            return self.answer(201 if created else 204)
        if kind == "variables" and method == "POST" and not name:
            if body["name"] in store:
                return self.answer(409, {"message": "Already exists"})
            store[body["name"]] = body["value"]
            return self.answer(201, {})
        if kind == "variables" and method == "PATCH" and name:
            if name not in store:
                return self.answer(404, {"message": "Not Found"})
            store[name] = body["value"]
            return self.answer(204)
        return self.answer(405, {"message": "Method not allowed"})

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_PUT(self):
        self.handle_any("PUT")

    def do_PATCH(self):
        self.handle_any("PATCH")


class GitHubDeployVariablesTest(unittest.TestCase):
    token = "good-token-T0KEN-SECRET"

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
        cls.api = f"http://127.0.0.1:{cls.server.server_port}"
        cls.server.page_size = {"secrets": 10, "variables": 1}
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="deploy-variables-github-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        (self.work / "env").mkdir()
        (self.work / "env/common.env").write_text(COMMON)
        (self.work / "env/prod.env.example").write_text("")
        (self.work / "bin").mkdir()
        (self.work / "bin/gh").write_text(FAKE_GH)
        (self.work / "bin/gh").chmod(0o755)
        self.gh_log = self.work / "gh.log"
        self.names = self.listed()
        self.state()

    def listed(self):
        self.run_tool(0, names_only=True)
        return [line.split() for line in self.output.splitlines()]

    def state(self, missing=(), as_variable=(), as_secret=(), extra=()):
        """production holds every listed name in its listed kind except missing; staging nothing."""
        secrets, variables = {}, {}
        for name, kind, *_ in self.names:
            if name in missing:
                continue
            secret = name in as_secret or (kind == "secret" and name not in as_variable)
            (secrets if secret else variables)[name] = "stored-" + name
        for name in extra:
            secrets[name] = "x"
        self.server.state = {"staging": {"secrets": {}, "variables": {}},
                             "production": {"secrets": secrets, "variables": variables}}

    def stored(self, name):
        production = self.server.state["production"]
        if name in production["secrets"]:
            return "secret " + production["secrets"][name]
        if name in production["variables"]:
            return "variable " + production["variables"][name]
        return "absent"

    def run_tool(self, expected, environment="local", token=None, path=None, **options):
        self.server.requests = []
        self.gh_log.write_text("")
        environ = {"GH_TOKEN": self.token if token is None else token, "GITHUB_TOKEN": "",
                   "GITHUB_API_URL": self.api, "GH_LOG": str(self.gh_log),
                   "PATH": path if path is not None else f"{self.work / 'bin'}:{os.environ['PATH']}"}
        environ.update(options.pop("environ", {}))
        out, err = io.StringIO(), io.StringIO()
        status = deploy_variables(self.work, environment, environ=environ, out=out, err=err, **options)
        self.output = out.getvalue() + err.getvalue()
        self.assertEqual(status, expected, self.output)
        self.assertNotIn("VALUE", self.output)
        self.assertNotIn("T0KEN", self.output)
        self.assertNotIn("VALUE", self.gh_log.read_text())
        self.assertNotIn("T0KEN", self.gh_log.read_text())

    def requests(self, method):
        return [r for r in self.server.requests if r["method"] == method]

    def gh_calls(self):
        return [json.loads(line) for line in self.gh_log.read_text().splitlines()]

    def private_file(self, name, text, mode=0o600):
        path = self.work / name
        path.write_text(text)
        path.chmod(mode)
        return path

    def test_plain_names_are_variables_and_the_rest_secrets(self):
        self.assertEqual(len(self.names), 18)
        self.assertEqual([name for name, kind, *_ in self.names if kind == "variable"], ["ERP_EMPLOYEE_ID", "GRAFANA_BASE_URL"])
        self.assertEqual([name for name, _, need, _ in self.names if need == "required"],
                         ["DATABASE_PASSWORD", "DATABASE_ROOT_PASSWORD", "SEARCH_KEY", "HTPASSWD_WEB"])
        self.assertEqual(self.server.requests, [])

    def test_check_reads_every_page_of_both_lists(self):
        self.run_tool(0)
        self.assertIn(f"GitHub {REPOSITORY}, environment production: 18 listed variables", self.output)
        self.assertIn("18 present, 0 missing, 0 secret/variable mismatches", self.output)
        gets = [f'{r["path"]}?{r["query"]}' for r in self.requests("GET")]
        self.assertEqual(gets, [f"{BASE}/environments/production/secrets?per_page=100&page=1",
                                f"{BASE}/environments/production/secrets?per_page=100&page=2",
                                f"{BASE}/environments/production/variables?per_page=30&page=1",
                                f"{BASE}/environments/production/variables?per_page=30&page=2"])
        self.assertEqual({r["version"] for r in self.server.requests}, {"2022-11-28"})

    def test_check_reports_missing_extra_and_the_wrong_kind(self):
        self.state(missing=("SEARCH_KEY", "ERP_EMPLOYEE_ID"), as_variable=("GRAFANA_TOKEN",), extra=("OLD_TOKEN",))
        self.run_tool(1)
        self.assertIn("  MISSING   SEARCH_KEY (secret, required)", self.output)
        self.assertIn("  MISSING   ERP_EMPLOYEE_ID (variable)", self.output)
        self.assertIn("  present   GRAFANA_TOKEN: variable in GitHub, the list says secret", self.output)
        self.assertIn("  extra     OLD_TOKEN (not in the list)", self.output)
        self.assertIn("16 present, 2 missing, 1 secret/variable mismatch\n", self.output)
        self.server.state["production"]["variables"]["DATABASE_PASSWORD"] = "plain"
        self.run_tool(1)
        self.assertIn("  present   DATABASE_PASSWORD: both a secret and a variable in GitHub (the secret is used)",
                      self.output)
        self.assertNotIn("stored-", self.output)

    def test_stage_reads_staging(self):
        (self.work / "env/stage.env.example").write_text('SERVICES=""\n')
        self.run_tool(1, environment="stage")
        self.assertIn("environment staging: 16 listed variables", self.output)  # no metrics: no METRICS_TOKEN, monitor
        self.assertIn("0 present, 16 missing", self.output)
        self.assertTrue(all("/environments/staging/" in r["path"] for r in self.requests("GET")))

    def test_apply_writes_secrets_through_gh_and_variables_through_the_api(self):
        self.state(missing=("DATABASE_PASSWORD", "HTPASSWD_WEB", "GRAFANA_BASE_URL", "ERP_EMPLOYEE_ID", "KEY_01", "KEY_02"))
        values = self.private_file("values.env", """\
            DATABASE_PASSWORD=db-VALUE-1
            HTPASSWD_WEB='admin:$apr1$VALUE2$hash'
            GRAFANA_BASE_URL="https://grafana.example/VALUE-3"
            KEY_01="multi
            line-VALUE-4
            "
            KEY_02=
            ERP_API_KEY='erp-VALUE-5'
            NOT_LISTED=nl-VALUE-6
            """.replace("            ", ""))
        self.run_tool(1, source=values)
        self.assertEqual(self.stored("DATABASE_PASSWORD"), "secret sealed:db-VALUE-1")
        self.assertEqual(self.stored("HTPASSWD_WEB"), "secret sealed:admin:$apr1$VALUE2$hash")
        self.assertEqual(self.stored("GRAFANA_BASE_URL"), "variable https://grafana.example/VALUE-3")
        self.assertEqual(self.stored("ERP_API_KEY"), "secret stored-ERP_API_KEY")
        self.assertEqual(self.stored("KEY_01"), "absent")
        calls = self.gh_calls()
        self.assertEqual([call["args"] for call in calls],
                         [["secret", "set", name, "--env", "production", "--repo", REPOSITORY]
                          for name in ("DATABASE_PASSWORD", "HTPASSWD_WEB")])
        self.assertEqual({(call["token"], tuple(call["other_tokens"])) for call in calls}, {(True, ())})
        self.assertEqual([(r["path"], r["body"]) for r in self.requests("POST")],
                         [(f"{BASE}/environments/production/variables",
                           {"name": "GRAFANA_BASE_URL", "value": "https://grafana.example/VALUE-3"})])
        for line in ("  created   DATABASE_PASSWORD (secret)", "  created   GRAFANA_BASE_URL (variable)",
                     "  MISSING   KEY_01 (its value ends with a line break, which gh would drop",
                     "  MISSING   KEY_02 (empty in", "  MISSING   ERP_EMPLOYEE_ID (not in",
                     "  skipped   NOT_LISTED (in", "  12 existing variables kept",
                     "Now: 15 present, 3 missing, 0 secret/variable mismatches"):
            self.assertIn(line, self.output)

        more = self.private_file("more.env", "ERP_EMPLOYEE_ID=42-VALUE-7\nGRAFANA_BASE_URL=https://new-VALUE-8\n"
                                             "ERP_API_KEY='erp-VALUE-9'\nKEY_01=one-VALUE\nKEY_02=two-VALUE\n")
        self.run_tool(0, source=more, replace=True)
        self.assertEqual(self.stored("ERP_API_KEY"), "secret sealed:erp-VALUE-9")
        self.assertEqual(self.stored("GRAFANA_BASE_URL"), "variable https://new-VALUE-8")
        self.assertEqual([r["path"] for r in self.requests("PATCH")],
                         [f"{BASE}/environments/production/variables/GRAFANA_BASE_URL"])
        self.assertIn("  replaced  ERP_API_KEY (secret)", self.output)
        self.assertIn("  replaced  GRAFANA_BASE_URL (variable)", self.output)
        self.assertIn("Now: 18 present, 0 missing", self.output)

    def test_apply_needs_gh_for_secrets_before_any_write(self):
        self.state(missing=("DATABASE_PASSWORD", "GRAFANA_BASE_URL"))
        values = self.private_file("values.env", "DATABASE_PASSWORD=db-VALUE\nGRAFANA_BASE_URL=https://g-VALUE\n")
        empty = self.work / "no-gh"
        empty.mkdir()
        self.run_tool(64, source=values, path=str(empty))
        self.assertIn("writing environment secrets needs the GitHub CLI gh (https://cli.github.com)", self.output)
        self.assertIn("Nothing was written.", self.output)
        self.assertEqual((self.requests("POST"), self.requests("PUT")), ([], []))
        # Variables alone need no gh.
        plain = self.private_file("plain.env", "GRAFANA_BASE_URL=https://g-VALUE\n")
        self.run_tool(1, source=plain, path=str(empty))
        self.assertEqual(self.stored("GRAFANA_BASE_URL"), "variable https://g-VALUE")

    def test_a_failed_gh_is_reported_and_the_rest_written(self):
        self.state(missing=("DATABASE_PASSWORD", "SEARCH_KEY"))
        values = self.private_file("values.env", "DATABASE_PASSWORD=db-VALUE\nSEARCH_KEY=search-VALUE\n")
        self.run_tool(2, source=values, environ={"GH_FAIL": "DATABASE_PASSWORD"})
        self.assertIn("  FAILED    DATABASE_PASSWORD: gh exited 1: failed to set secret: HTTP 422", self.output)
        self.assertIn("  created   SEARCH_KEY (secret)", self.output)
        self.assertIn("deploy-variables: 1 writes failed", self.output)

    def test_tokens_and_refusals_name_the_permissions(self):
        self.run_tool(0, token="", environ={"GITHUB_TOKEN": self.token})
        token = self.private_file("token", self.token + "\n")
        self.run_tool(0, token="", token_file=token)
        token.chmod(0o640)
        self.run_tool(64, token="", token_file=token)
        self.assertIn("is readable by others (mode 0640)", self.output)
        self.run_tool(64, token="")
        self.assertIn("no token: set GH_TOKEN (or GITHUB_TOKEN) or pass token=PATH", self.output)
        self.assertIn("Environments: Read-only to check, Environments: Read and write to apply", self.output)
        self.run_tool(2, token="bad-T0KEN")
        self.assertIn("GitHub answered 401 to GET secrets: the token is wrong, expired or revoked", self.output)
        self.state(missing=("GRAFANA_BASE_URL",))
        values = self.private_file("values.env", "GRAFANA_BASE_URL=https://g-VALUE\n")
        self.run_tool(2, token="readonly-token-T0KEN", source=values)
        self.assertIn("GitHub answered 403 to create GRAFANA_BASE_URL: the token lacks a permission", self.output)
        self.assertIn("GitHub: Resource not accessible by personal access token", self.output)
        del self.server.state["production"]
        self.run_tool(2)
        self.assertIn("GitHub answered 404 to GET secrets: no repository owner/demo or no environment production", self.output)
        self.assertIn("The repository has the environments: staging.", self.output)

    def test_unsafe_values_files_are_refused_before_any_request(self):
        values = self.private_file("values.env", "ERP_API_KEY=erp-VALUE\n", mode=0o644)
        self.run_tool(64, source=values)
        self.assertIn("is readable by others (mode 0644): chmod 600", self.output)
        self.assertEqual(self.server.requests, [])


class ProviderTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="ci-provider-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        subprocess.run(["git", "init", "-q", str(self.work)], check=True)

    def remote(self, url):
        subprocess.run(["git", "-C", str(self.work), "remote", "remove", "origin"], capture_output=True)
        subprocess.run(["git", "-C", str(self.work), "remote", "add", "origin", url], check=True)

    def test_the_setting_then_the_repository_settings_then_the_origin_remote(self):
        self.assertEqual(ci_provider(self.work, {}), "bitbucket")
        self.remote("git@github.com:owner/demo.git")
        self.assertEqual(ci_provider(self.work, {}), "github")
        self.assertEqual(ci_provider(self.work, {"DEPLOY_BITBUCKET_REPOSITORY": "ws/demo"}), "bitbucket")
        self.remote("git@bitbucket.org:ws/demo.git")
        self.assertEqual(ci_provider(self.work, {}), "bitbucket")
        self.assertEqual(ci_provider(self.work, {"DEPLOY_GITHUB_REPOSITORY": "owner/demo"}), "github")
        self.assertEqual(ci_provider(self.work, {"CI_PROVIDER": "github", "DEPLOY_BITBUCKET_REPOSITORY": "ws/demo"}), "github")
        with self.assertRaisesRegex(ValueError, "CI_PROVIDER must be bitbucket or github, not 'gitlab'"):
            ci_provider(self.work, {"CI_PROVIDER": "gitlab"})

    def test_the_github_repository_comes_from_the_setting_or_the_origin_remote(self):
        self.assertEqual(repository(self.work, {"DEPLOY_GITHUB_REPOSITORY": "owner/repo"}), "owner/repo")
        for url in ("git@github.com:BigSant/DockerDevelopmentEnvironment.git",
                    "https://github.com/BigSant/DockerDevelopmentEnvironment", "ssh://git@github.com/BigSant/DockerDevelopmentEnvironment.git"):
            self.remote(url)
            self.assertEqual(repository(self.work, {}), "BigSant/DockerDevelopmentEnvironment")
        self.remote("git@bitbucket.org:ws/demo.git")
        with self.assertRaisesRegex(Exception, "set DEPLOY_GITHUB_REPOSITORY=owner/repository"):
            repository(self.work, {})
        with self.assertRaisesRegex(Exception, "must be owner/repository"):
            repository(self.work, {"DEPLOY_GITHUB_REPOSITORY": "just-a-name"})

    def test_the_make_target_reaches_the_github_list(self):
        (self.work / "env").mkdir()
        (self.work / "env/common.env").write_text(COMMON)
        (self.work / "Makefile").write_text(f"PROJECT_DOCKER_DIRECTORY := $(CURDIR)\nSETUP_DIRECTORY := {ROOT}\n"
                                            f"include $(SETUP_DIRECTORY)/docker/project.mk\n")
        result = subprocess.run(["make", "-s", "-C", str(self.work), "deploy-variables", "names=1"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("GRAFANA_BASE_URL variable optional (APP_ENVIRONMENT)", result.stdout)
        self.assertIn("DATABASE_PASSWORD secret required (setup)", result.stdout)
        (self.work / "env/common.env").write_text(COMMON.replace("CI_PROVIDER=github", "CI_PROVIDER=gitlab"))
        result = subprocess.run(["make", "-s", "-C", str(self.work), "deploy-variables", "names=1"],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)  # make reports the command's 64 as its own 2
        self.assertIn("deploy-variables: CI_PROVIDER must be bitbucket or github", result.stderr)


if __name__ == "__main__":
    unittest.main()
