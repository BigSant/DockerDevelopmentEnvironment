"""make deploy-variables against a fake Bitbucket API (an HTTP server on localhost, no network).

The fake keeps its state (environments, variables) in memory and logs each request with its body. It
pages the environments one per page and the variables ten per page. Tokens: good-token-* may do
everything, readonly-token-* gets 403 on writes, any other token 401. No output may show a value
(they contain VALUE) or a token (T0KEN).
"""

import contextlib
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
from project_bitbucket import deploy_variables, repository

BASE = "/2.0/repositories/rsmaizys/asana_tracker"
# asana.local's list: setup's own (metrics on prod), its required and optional names, its APP_ENVIRONMENT.
COMMON = """\
PROJECT_NAME=asana
SERVICES=cron
DEPLOY_BITBUCKET_REPOSITORY=rsmaizys/asana_tracker
DEPLOY_REQUIRED_VARIABLES="MEILISEARCH_MASTER_KEY HTPASSWD_WEB HTPASSWD_API"
DEPLOY_OPTIONAL_VARIABLES=HTPASSWD_BUILDER
DEPLOY_PLAIN_VARIABLES="GRAFANA_BASE_URL CLOUDFLARE_ACCOUNT_ID ERP_EMPLOYEE_ID"
APP_ENVIRONMENT="
  ASANA_TOKEN ASANA_WRITE_TOKEN ASANA_WRITE_ENABLED ASANA_WORKSPACE
  INTAKE_API_KEY OPENSPEC_API_KEY BUILDER_API_KEY MCP_SERVER_TOKEN
  FRONTAPP_TOKEN TOGGL_API_TOKEN ANTHROPIC_API_KEY LLM_MODEL OPENAI_API_KEY VOYAGE_API_KEY
  BITBUCKET_CLIENT_ID BITBUCKET_CLIENT_SECRET GRAFANA_BASE_URL GRAFANA_TOKEN
  CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID ERP_API_KEY ERP_EMPLOYEE_ID
"
ASANA_WORKSPACE=14409416458195
LLM_MODEL=claude-haiku
"""
PROD = 'SERVICES="cron metrics"\nASANA_WRITE_ENABLED=0\n'


class FakeBitbucket(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def answer(self, status, data):
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def paged(self, items, size, query):
        number = int(query.get("page", ["1"])[0])
        data = {"values": items[(number - 1) * size:number * size], "page": number}
        if number * size < len(items):
            data["next"] = self.server.next_base + f'{self.path.split("?")[0]}?page={number + 1}'
        return data

    def handle_any(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        url = urllib.parse.urlsplit(self.path)
        path, query = urllib.parse.unquote(url.path), urllib.parse.parse_qs(url.query)
        token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
        self.server.requests.append({"method": method, "path": path, "query": url.query, "body": body})
        if not token.startswith(("good-token-", "readonly-token-")):
            return self.answer(401, {"type": "error", "error": {"message": "Token is invalid, expired, or not supported."}})
        if method != "GET" and not token.startswith("good-token-"):
            return self.answer(403, {"type": "error", "error": {"message": "Your credentials lack one or more required privilege scopes."}})
        state = self.server.state
        if path == f"{BASE}/environments" and method == "GET":
            return self.answer(200, self.paged(state["environments"], 1, query))
        match = re.fullmatch(f"{BASE}/deployments_config/environments/([^/]+)/variables(?:/([^/]+))?", path)
        if not match or match[1] not in state["variables"]:
            return self.answer(404, {"type": "error", "error": {"message": "Not found"}})
        variables = state["variables"][match[1]]
        if method == "GET" and not match[2]:
            shown = [dict(v, value="" if v["secured"] else v["value"]) for v in variables]
            return self.answer(200, self.paged(shown, 10, query))
        if method == "POST" and not match[2]:
            if any(v["key"] == body["key"] for v in variables):
                return self.answer(409, {"type": "error", "error": {"message": f'Variable {body["key"]} exists'}})
            variable = dict(body, uuid="{new-%s}" % body["key"], type="deployment_variable")
            variables.append(variable)
            status = 201
        elif method == "PUT" and match[2]:
            found = [v for v in variables if v["uuid"] == match[2]]
            if not found:
                return self.answer(404, {"type": "error", "error": {"message": "No such variable"}})
            found[0].update(body)
            variable, status = found[0], 200
        else:
            return self.answer(405, {"type": "error", "error": {"message": "Method not allowed"}})
        self.answer(status, dict(variable, value="" if variable["secured"] else variable["value"]))

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_PUT(self):
        self.handle_any("PUT")


class DeployVariablesTest(unittest.TestCase):
    token = "good-token-T0KEN-SECRET"

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeBitbucket)
        cls.api = f"http://127.0.0.1:{cls.server.server_port}/2.0"
        cls.server.next_base = f"http://127.0.0.1:{cls.server.server_port}"
        thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="deploy-variables-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        (self.work / "env").mkdir()
        (self.work / "env/common.env").write_text(COMMON)
        (self.work / "env/prod.env.example").write_text(PROD)
        self.server.next_base = f"http://127.0.0.1:{self.server.server_port}"
        self.names = self.listed()
        self.state()

    def listed(self):
        self.run_tool(0, names_only=True)
        return [line.split() for line in self.output.splitlines()]

    def state(self, missing=(), plain=(), secured=(), extra=()):
        """Production holds every listed variable (its listed secured flag) except missing; Staging none."""
        variables = []
        for name, kind, *_ in self.names:
            if name in missing:
                continue
            flag = False if name in plain else True if name in secured else kind == "secured"
            variables.append({"key": name, "value": "stored-" + name, "secured": flag, "uuid": "{var-%s}" % name})
        for name in sorted(extra):
            variables.append({"key": name, "value": "x", "secured": True, "uuid": "{var-%s}" % name})
        self.server.state = {"environments": [
            {"uuid": "{env-staging}", "name": "Staging", "environment_type": {"name": "Staging"}},
            {"uuid": "{env-production}", "name": "Production", "environment_type": {"name": "Production"}},
        ], "variables": {"{env-staging}": [], "{env-production}": variables}}

    def stored(self, name):
        found = [v for v in self.server.state["variables"]["{env-production}"] if v["key"] == name]
        return (("secured " if found[0]["secured"] else "plain ") + found[0]["value"]) if found else "absent"

    def run_tool(self, expected, environment="local", token=None, **options):
        self.server.requests = []
        environ = {"BITBUCKET_ACCESS_TOKEN": self.token if token is None else token, "BITBUCKET_API_URL": self.api}
        out, err = io.StringIO(), io.StringIO()
        status = deploy_variables(self.work, environment, environ=environ, out=out, err=err, **options)
        self.output = out.getvalue() + err.getvalue()
        self.assertEqual(status, expected, self.output)
        self.assertNotIn("VALUE", self.output)
        self.assertNotIn("T0KEN", self.output)

    def requests(self, method):
        return [r for r in self.server.requests if r["method"] == method]

    def private_file(self, name, text, mode=0o600):
        path = self.work / name
        path.write_text(text)
        path.chmod(mode)
        return path

    def test_the_list_comes_from_setup_and_the_project_settings(self):
        self.assertEqual(len(self.names), 27)
        required = [name for name, _, need, _ in self.names if need == "required"]
        self.assertEqual(required, ["DATABASE_PASSWORD", "DATABASE_ROOT_PASSWORD", "METRICS_TOKEN",
                                    "MEILISEARCH_MASTER_KEY", "HTPASSWD_WEB", "HTPASSWD_API"])
        self.assertEqual([name for name, kind, *_ in self.names if kind == "plain"],
                         ["GRAFANA_BASE_URL", "CLOUDFLARE_ACCOUNT_ID", "ERP_EMPLOYEE_ID"])
        # Names an env file sets are not Deployment variables.
        listed = {name for name, *_ in self.names}
        self.assertFalse(listed & {"ASANA_WORKSPACE", "LLM_MODEL", "ASANA_WRITE_ENABLED"})
        self.assertEqual(self.requests("GET"), [])

    def test_check_reads_every_page(self):
        self.run_tool(0)
        self.assertIn("Bitbucket rsmaizys/asana_tracker, environment production: 27 listed variables", self.output)
        self.assertIn("27 present, 0 missing, 0 secured/plain mismatches", self.output)
        self.assertIn("  present   ERP_EMPLOYEE_ID", self.output)
        gets = [f'{r["path"]}?{r["query"]}' for r in self.requests("GET")]
        self.assertEqual(len(gets), 5, gets)
        self.assertIn(f"{BASE}/environments?page=2", gets)
        self.assertIn(f"{BASE}/deployments_config/environments/{{env-production}}/variables?page=3", gets)

    def test_check_reports_missing_extra_and_mismatched(self):
        self.state(missing=("ERP_API_KEY", "HTPASSWD_WEB"), extra=("OLD_TOKEN",))
        self.run_tool(1)
        self.assertIn("  MISSING   ERP_API_KEY (secured)\n", self.output)
        self.assertIn("  MISSING   HTPASSWD_WEB (secured, required)", self.output)
        self.assertIn("  extra     OLD_TOKEN (not in the list)", self.output)
        self.assertIn("25 present, 2 missing", self.output)
        self.state(plain=("GRAFANA_TOKEN",), secured=("CLOUDFLARE_ACCOUNT_ID",))
        self.run_tool(0)
        self.assertIn("  present   GRAFANA_TOKEN: plain in Bitbucket, the list says secured", self.output)
        self.assertIn("  present   CLOUDFLARE_ACCOUNT_ID: secured in Bitbucket, the list says plain", self.output)
        self.assertIn("27 present, 0 missing, 2 secured/plain mismatches", self.output)
        self.assertNotIn("stored-", self.output)

    def test_stage_reads_staging_with_its_own_list(self):
        (self.work / "env/stage.env.example").write_text("")
        self.run_tool(1, environment="stage")
        self.assertIn("environment staging: 26 listed variables", self.output)  # no metrics, ASANA_WRITE_ENABLED unset
        self.assertIn("0 present, 26 missing", self.output)
        self.assertTrue(any("{env-staging}/variables" in r["path"] for r in self.requests("GET")))
        self.run_tool(64, environment="test")
        self.assertIn("Deployment variables belong to ENV=stage or ENV=prod", self.output)

    def test_apply_creates_only_the_missing_ones_with_the_listed_flag(self):
        self.state(missing=("DATABASE_PASSWORD", "HTPASSWD_WEB", "GRAFANA_BASE_URL", "ERP_EMPLOYEE_ID", "VOYAGE_API_KEY"))
        values = self.private_file("values.env", """\
            # values for the test
            DATABASE_PASSWORD=db-VALUE-1   # inline comment
            HTPASSWD_WEB='admin:$apr1$VALUE2$hash other:$2y$05$VALUE3'
            export GRAFANA_BASE_URL="https://grafana.example/VALUE-4"
            ASANA_TOKEN='asana-VALUE-5'
            ERP_EMPLOYEE_ID=
            NOT_LISTED=nl-VALUE-6
            """.replace("            ", ""))
        self.run_tool(1, source=values)
        self.assertEqual((len(self.requests("POST")), len(self.requests("PUT"))), (3, 0))
        self.assertEqual(self.stored("DATABASE_PASSWORD"), "secured db-VALUE-1")
        self.assertEqual(self.stored("HTPASSWD_WEB"), "secured admin:$apr1$VALUE2$hash other:$2y$05$VALUE3")
        self.assertEqual(self.stored("GRAFANA_BASE_URL"), "plain https://grafana.example/VALUE-4")
        self.assertEqual(self.stored("ASANA_TOKEN"), "secured stored-ASANA_TOKEN")
        for line in ("  created   DATABASE_PASSWORD (secured)", "  created   GRAFANA_BASE_URL (plain)",
                     "  MISSING   ERP_EMPLOYEE_ID (empty in", "  MISSING   VOYAGE_API_KEY (not in",
                     "  skipped   NOT_LISTED (in", "Now: 25 present, 2 missing"):
            self.assertIn(line, self.output)
        # Again: nothing left to create from the file.
        self.run_tool(1, source=values)
        self.assertEqual(self.requests("POST"), [])

        more = self.private_file("more.env", "ERP_EMPLOYEE_ID=42-VALUE-7\nVOYAGE_API_KEY=\"voyage-VALUE-8\"\nASANA_TOKEN='asana-VALUE-5'\n")
        self.run_tool(0, source=more, replace=True)
        self.assertEqual((len(self.requests("POST")), len(self.requests("PUT"))), (2, 1))
        put = self.requests("PUT")[0]
        self.assertEqual(put["path"], f"{BASE}/deployments_config/environments/{{env-production}}/variables/{{var-ASANA_TOKEN}}")
        self.assertEqual(put["body"], {"key": "ASANA_TOKEN", "value": "asana-VALUE-5", "secured": True})
        self.assertEqual(self.stored("ERP_EMPLOYEE_ID"), "plain 42-VALUE-7")
        self.assertIn("  replaced  ASANA_TOKEN (secured)", self.output)
        self.assertIn("Now: 27 present, 0 missing", self.output)

    def test_apply_refuses_unsafe_files_before_any_request(self):
        values = self.private_file("values.env", "ERP_API_KEY=erp-VALUE\n", mode=0o644)
        self.run_tool(64, source=values)
        self.assertIn("is readable by others (mode 0644): chmod 600", self.output)
        self.assertEqual(self.server.requests, [])
        dollar = self.private_file("dollar.env", "HTPASSWD_API=admin:$apr1$VALUE9\n")
        self.run_tool(64, source=dollar)
        self.assertIn("HTPASSWD_API holds $ outside single quotes", self.output)
        self.assertEqual(self.server.requests, [])
        self.run_tool(64, replace=True)
        self.assertIn("replace=1 belongs to from=FILE", self.output)

    def test_token_file_is_used_only_when_private(self):
        token = self.private_file("token", self.token + "\n")
        self.run_tool(0, token="", token_file=token)
        token.chmod(0o640)
        self.run_tool(64, token="", token_file=token)
        self.assertIn("is readable by others (mode 0640)", self.output)
        self.run_tool(64, token="")
        self.assertIn("no token: set BITBUCKET_ACCESS_TOKEN or pass token=PATH", self.output)

    def test_refusals_name_the_token_scopes(self):
        self.run_tool(2, token="bad-T0KEN-SECRET")
        self.assertIn("Bitbucket answered 401 to GET environments: the token is wrong, expired or revoked", self.output)
        self.assertIn("Pipelines: Read (scope pipeline)", self.output)
        self.state(missing=("ERP_API_KEY",))
        values = self.private_file("erp.env", "ERP_API_KEY=erp-VALUE-10\n")
        self.run_tool(2, token="readonly-token-T0KEN-SECRET", source=values)
        self.assertIn("Bitbucket answered 403 to create ERP_API_KEY: the token lacks a scope", self.output)
        self.assertIn("Pipelines: Edit variables (scope pipeline:variable)", self.output)
        self.assertIn("Bitbucket: Your credentials lack", self.output)

    def test_a_next_page_elsewhere_is_not_followed(self):
        self.server.next_base = "http://127.0.0.2:9"
        self.run_tool(2)
        self.assertIn(f"Bitbucket returned a next page outside {self.api}", self.output)
        self.assertEqual(len(self.server.requests), 1)

    def test_the_repository_comes_from_the_setting_or_the_origin_remote(self):
        self.assertEqual(repository(self.work, {"DEPLOY_BITBUCKET_REPOSITORY": "ws/repo"}), "ws/repo")
        subprocess.run(["git", "init", "-q", str(self.work)], check=True)
        for url in ("git@bitbucket.org:rsmaizys/forsena_docker.git", "https://user@bitbucket.org/rsmaizys/forsena_docker.git",
                    "ssh://git@bitbucket.org/rsmaizys/forsena_docker"):
            subprocess.run(["git", "-C", str(self.work), "remote", "remove", "origin"], capture_output=True)
            subprocess.run(["git", "-C", str(self.work), "remote", "add", "origin", url], check=True)
            self.assertEqual(repository(self.work, {}), "rsmaizys/forsena_docker")
        subprocess.run(["git", "-C", str(self.work), "remote", "set-url", "origin", "git@github.com:a/b.git"], check=True)
        with self.assertRaisesRegex(Exception, "set DEPLOY_BITBUCKET_REPOSITORY"):
            repository(self.work, {})

    def test_the_make_target_passes_its_arguments(self):
        (self.work / "Makefile").write_text(f"PROJECT_DOCKER_DIRECTORY := $(CURDIR)\nSETUP_DIRECTORY := {ROOT}\n"
                                            f"include $(SETUP_DIRECTORY)/docker/project.mk\n")
        result = subprocess.run(["make", "-s", "-C", str(self.work), "deploy-variables", "names=1"],
                                capture_output=True, text=True, env={**os.environ, "BITBUCKET_ACCESS_TOKEN": ""})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 27)
        result = subprocess.run(["make", "-s", "-C", str(self.work), "deploy-variables", "ENV=stage", "replace=1"],
                                capture_output=True, text=True)
        self.assertIn("replace=1 belongs to from=FILE", result.stderr)


if __name__ == "__main__":
    unittest.main()
