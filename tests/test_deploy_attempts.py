"""The deploy attempt contract: DEPLOY_ATTEMPT, DEPLOY_EXPECTED_LIVE, make deploy-health and make deploy-status.

The pipeline tests reuse test_pipeline_deploy's git fixtures with a fake `make` that keeps setup's deploy history and
records the attempt it is given; its deploy-health runs setup's real command against a local HTTP server standing in
for the target, which serves the release of the environment's last history entry at /_health and fails / for the
commits a test breaks. A project that sets none of the new settings must deploy exactly as before.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker"), str(Path(__file__).resolve().parent)]
import test_pipeline_deploy as pipeline
from project_attempts import Attempt, status_document
from project_deploy_health import Endpoint, deploy_health

git = pipeline.git
U = "6f1c2d3e-0000-4000-8000-{:012d}".format

FAKE_MAKE = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
options = dict(arg.split('=', 1) for arg in args[1:] if '=' in arg)
environment = options['ENV']
git = lambda *command: subprocess.run(['git', *command], capture_output=True, text=True).stdout.strip()
with open(os.environ['MAKE_LOG'], 'a') as log:
    log.write(f"make {' '.join(args)} | head={git('log', '-1', '--format=%s')}\n")
target, fail = args[0], os.environ.get('FAIL', '')
if target == 'deploy-health':
    command = [sys.executable, os.path.join(os.environ['SETUP_DIRECTORY'], 'docker/project.py'), '--docker-directory',
               os.getcwd(), '--env', environment, 'deploy-health']
    if options.get('attempt'):
        command += ['--attempt', options['attempt']]
    if options.get('rollback') == '1':
        command.append('--rollback')
    sys.exit(subprocess.run(command).returncode)
if fail == target:
    print(f'fake make: {target} failed', file=sys.stderr)
    sys.exit(2)
if target != 'deploy':
    sys.exit(0)
history = Path(f'.generated/release/{environment}/deployments.json')
entries = json.loads(history.read_text()) if history.is_file() else []
if options.get('rollback') == '1':
    done = [e for e in entries[:-1] if not e.get('failed') and e['commit'] != entries[-1]['commit']]
    entry = {'ref': done[-1]['ref'], 'commit': done[-1]['commit'], 'rollback': True}
else:
    entry = {'ref': options['ref'], 'commit': git('rev-parse', 'HEAD'), 'rollback': False}
if options.get('attempt'):
    entry['attempt'] = options['attempt']
if fail == 'after-rsync' and options.get('rollback') != '1':
    entry['failed'] = 'after-rsync'
history.parent.mkdir(parents=True, exist_ok=True)
history.write_text(json.dumps(entries + [entry]))
sys.exit(3 if fail == 'after-rsync' and options.get('rollback') != '1' else 0)
'''

HEALTH = 'DEPLOY_HEALTH_PATHS="/"\nDEPLOY_HEALTH_RELEASE_PATH=/_health\n'
EXAMPLE = "MARK={name}\nDOMAIN={domain}\nLOCALHOST_PORT={port}\nDEPLOY_CONTEXT=default\nDEPLOY_SSH=\nDEPLOY_PATH=/srv/fixture-{env}\n"


class Target:
    """The site of both environments on one port: the Host header names the environment (stage.fixture.test)."""

    def __init__(self, test):
        self.app, self.broken, self.requests = None, set(), []
        target = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                target.requests.append((self.path, dict(self.headers)))
                environment = 'stage' if self.headers.get('Host', '').startswith('stage.') else 'prod'
                history = target.app / f'.generated/release/{environment}/deployments.json'
                entries = json.loads(history.read_text()) if history.is_file() else []
                release = entries[-1]['commit'] if entries else 'none'
                if self.path == '/_health':
                    body, status = json.dumps({'release': release, 'database': 'ok'}).encode(), 200
                else:
                    body, status = b'page body SECRET-BODY', 404 if release in target.broken else 200
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)


class AttemptFixture(pipeline.Fixture):
    common = pipeline.COMMON

    def setUp(self):
        super().setUp()
        (self.work / "bin/make").write_text(FAKE_MAKE)
        self.site = Target(self)

        def files(name, extra=None):
            return {"env/common.env": self.common, "config/mark": f"{name}\n", "public/index.php": f"<?php // {name}\n",
                    "env/prod.env.example": EXAMPLE.format(name=name, domain="fixture.test", port=self.site.port, env="prod"),
                    "env/stage.env.example": EXAMPLE.format(name=name, domain="stage.fixture.test", port=self.site.port,
                                                            env="stage"), **(extra or {})}
        # D changes what the containers are made of (IMAGES=auto rebuilds for it).
        commits = [(name, files(name)) for name in "ABC"] + [("D", files("D", {"config/nginx/site.conf": "server {}\n"}))]
        origin, self.commits = self.repository("environment", commits)
        self.app = self.site.app = self.work / "root/app"
        git(self.work, "clone", "-q", "--no-hardlinks", str(origin), str(self.app))
        git(self.app, "checkout", "-q", "--detach", self.commits["A"])
        # As the first deploy by hand leaves it.
        (self.app / "env/prod.env").write_text((self.app / "env/prod.env.example").read_text())
        self.pipeline_commit = self.commits["C"]
        self.secrets = dict(pipeline.REQUIRED)

    def attempt(self, attempt, environment="prod"):
        path = self.app / f".generated/release/{environment}/attempts/{attempt}.json"
        return json.loads(path.read_text()) if path.is_file() else None

    def history(self, environment="prod"):
        path = self.app / f".generated/release/{environment}/deployments.json"
        return json.loads(path.read_text()) if path.is_file() else []

    def stages(self, attempt, environment="prod"):
        return [(event["stage"], event["state"]) for event in self.attempt(attempt, environment)["events"]]

    def project(self, *arguments, expected=0):
        """setup's own command in the build root, as a workflow step runs it."""
        result = subprocess.run([sys.executable, str(ROOT / "docker/project.py"), "--docker-directory", str(self.app),
                                 *arguments], capture_output=True, text=True,
                                env={"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", str(self.work))})
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertNotIn("VALUE", result.stdout + result.stderr)
        return result.stdout

    def heads(self):
        return [line.split(" |")[0] for line in self.make_log().splitlines()]


class AttemptTest(AttemptFixture):
    """A project without health settings: attempts, the live-commit guard, the history's live commit, the repair."""

    def test_a_project_that_sets_nothing_new_deploys_as_before(self):
        c = self.commits
        self.run_step(0, REF=c["A"])
        self.run_step(0, REF=c["B"])
        self.assertEqual(self.heads(), [f"make deploy ENV=prod ref={c['B']} backup=0 cache=auto"])
        self.run_step(0, ROLLBACK="yes")
        self.run_step(3, FAIL="after-rsync")
        self.assertEqual(self.heads(), [f"make deploy ENV=prod ref={c['C']} backup=0 cache=auto"])
        release = self.app / ".generated/release/prod"
        self.assertEqual(sorted(path.name for path in release.iterdir()), ["deployments.json"],
                         "no attempts, no lock, no run marker left behind")
        self.assertEqual([sorted(entry) for entry in self.history()], [["commit", "ref", "rollback"]] * 3 +
                         [["commit", "failed", "ref", "rollback"]])
        self.assertNotIn("health", self.output)

    def test_an_attempt_is_recorded_and_a_repeated_one_changes_nothing(self):
        c = self.commits
        self.run_step(0, REF=c["B"], DEPLOY_ATTEMPT=U(1), GITHUB_RUN_ID="")
        self.assert_log(f"make deploy ENV=prod ref={c['B']} backup=0 cache=auto attempt={U(1)} | head=B")
        record = self.attempt(U(1))
        self.assertEqual((record["outcome"], record["reason"], record["sha"]), ("live", None, c["B"]))
        self.assertEqual(self.stages(U(1)), [("accepted", "ok"), ("settings", "ok"), ("images", "skipped")])
        self.assertEqual(self.history()[-1]["attempt"], U(1))
        for variables in ({"REF": c["C"]}, {"ROLLBACK": "yes"}):
            self.run_step(64, DEPLOY_ATTEMPT=U(1), **variables)
            self.assert_out(f"Attempt {U(1)} is known in ENV=prod already (outcome: live)")
            self.assertEqual(self.make_log(), "")
            self.assert_root("B")
        self.assertEqual(self.attempt(U(1))["outcome"], "live")
        self.assertEqual(self.stages(U(1))[-2:], [("repeated", "refused")] * 2)
        self.run_step(64, DEPLOY_ATTEMPT="../../etc/x")
        self.assert_out("DEPLOY_ATTEMPT must be 8-64 letters, digits or dashes")
        self.assertEqual(len(list((self.app / ".generated/release/prod/attempts").glob("*.json"))), 1)

    def test_a_refused_new_attempt_records_why(self):
        self.run_step(64, DEPLOY_ATTEMPT=U(2), HTPASSWD_API="")
        self.assertEqual((self.attempt(U(2))["outcome"], self.attempt(U(2))["reason"]), ("refused", "missing_settings"))
        self.assertEqual(self.stages(U(2)), [("refused", "refused")])
        self.assertIn("HTPASSWD_API", self.attempt(U(2))["events"][0]["message"])
        self.run_step(64, DEPLOY_ATTEMPT=U(2))
        self.assert_out("is known in ENV=prod already (outcome: refused)")
        self.run_step(64, DEPLOY_ATTEMPT=U(3), IMAGES="maybe")
        self.assertEqual(self.attempt(U(3))["reason"], "bad_input")
        self.run_step(64, DEPLOY_ATTEMPT=U(4), REF="no-such-ref")
        self.assertEqual((self.attempt(U(4))["outcome"], self.attempt(U(4))["reason"]), ("refused", "bad_input"))
        self.assertEqual(self.make_log(), "")
        self.assert_root("A")

    def test_expected_live_refuses_a_changed_environment_before_anything_changes(self):
        c = self.commits
        self.run_step(0, REF=c["A"], DEPLOY_ATTEMPT=U(5), DEPLOY_EXPECTED_LIVE="none")
        self.run_step(64, REF=c["B"], DEPLOY_ATTEMPT=U(6), DEPLOY_EXPECTED_LIVE=c["C"])
        self.assert_out(f"ENV=prod changed outside this deploy: its live commit is {c['A']}, not {c['C']}")
        self.assertEqual((self.attempt(U(6))["outcome"], self.attempt(U(6))["reason"],
                          self.attempt(U(6))["expected_live"]), ("refused", "live_changed", c["C"]))
        self.assertEqual(self.make_log(), "")
        self.run_step(64, REF=c["B"], DEPLOY_EXPECTED_LIVE="none")  # without an attempt: refused, nothing recorded
        self.run_step(64, REF=c["B"], DEPLOY_EXPECTED_LIVE="A")
        self.assert_out("DEPLOY_EXPECTED_LIVE must be a full lower-case commit or none")
        self.assertEqual(len(list((self.app / ".generated/release/prod/attempts").glob("*.json"))), 2)
        self.run_step(0, REF=c["B"], DEPLOY_ATTEMPT=U(7), DEPLOY_EXPECTED_LIVE=c["A"])
        self.assert_root("B")
        # A failed deploy is not live: the guard still expects the last completed one.
        self.run_step(3, REF=c["C"], DEPLOY_ATTEMPT=U(8), DEPLOY_EXPECTED_LIVE=c["B"], FAIL="after-rsync")
        self.assertEqual((self.attempt(U(8))["outcome"], self.attempt(U(8))["reason"]), ("failed_after_code", "hook"))
        self.run_step(0, REF=c["C"], DEPLOY_ATTEMPT=U(9), DEPLOY_EXPECTED_LIVE=c["B"])

    def test_the_live_commit_of_each_environment_comes_from_its_history(self):
        """One build root deploys stage and prod: prod's IMAGES=auto compares with prod's live commit, not stage's."""
        c = self.commits
        for environment in ("prod", "stage"):
            lock = self.app / f".generated/release/{environment}/images.lock.json"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(json.dumps({"setup": git(ROOT, "describe", "--tags", "--always", "--dirty"), "images": {}}))
        self.run_step(0, REF=c["A"], IMAGES="auto")
        self.run_step(0, environment="stage", REF=c["D"], IMAGES="auto", BITBUCKET_DEPLOYMENT_ENVIRONMENT="staging")
        self.assert_log("make release ENV=stage | head=D")
        self.assertEqual(git(self.app, "log", "-1", "--format=%s"), "D", "stage deployed last")
        self.run_step(0, REF=c["D"], IMAGES="auto")
        decision = next(line for line in self.output.splitlines() if line.startswith("==> IMAGES=auto"))
        self.assertTrue(decision.startswith(f"==> IMAGES=auto: yes (changed since {c['A'][:12]}: "), decision)
        self.assertIn("config/nginx/site.conf", decision, "the build root's own commit D alone would say no")
        self.assert_log("make release ENV=prod | head=D")

    def test_a_killed_run_is_repaired_before_the_next_one(self):
        c = self.commits
        self.run_step(0, REF=c["A"], DEPLOY_ATTEMPT=U(10))
        # U(11) was killed after its checkout of C and before make deploy recorded anything.
        killed = Attempt(self.app, "prod", U(11))
        killed.create(sha=c["C"])
        killed.event("accepted", "ok")
        git(self.app, "checkout", "-q", "--detach", c["C"])
        marker = self.app / ".generated/release/prod/pipeline-run.json"
        marker.write_text(json.dumps({"from": c["A"], "target": c["C"], "attempt": U(11), "images": False, "entries": 1}))
        self.run_step(0, REF=c["B"], DEPLOY_ATTEMPT=U(12), CRON="auto")
        self.assert_out(f"A killed run left the build root at {c['C'][:12]}; it is back at {c['A'][:12]}")
        self.assert_out(f"CRON=auto: no (the crontab did not change since {c['A'][:12]})")
        self.assertEqual((self.attempt(U(11))["outcome"], self.attempt(U(11))["reason"]), ("not_deployed", "interrupted"))
        repair = next(event for event in self.attempt(U(12))["events"] if event["stage"] == "repair")
        self.assertEqual((repair["checkout_repaired"], repair["interrupted_attempt"]), (True, U(11)))
        self.assertFalse(marker.exists())
        self.assert_root("B")
        # A marker its run still holds is no leftover: the second run waits for nothing and changes nothing.
        import fcntl
        marker.write_text(json.dumps({"from": c["A"], "target": c["C"], "attempt": None, "images": False, "entries": 9}))
        with open(marker) as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.run_step(64, REF=c["C"])
            self.assert_out("Another make pipeline-deploy of ENV=prod is running in this build root")
            self.assertEqual(self.make_log(), "")
        self.assert_root("B")
        # Killed after make deploy recorded its code: the build root follows the deployed commit.
        marker.write_text(json.dumps({"from": c["A"], "target": c["B"], "attempt": None, "images": False, "entries": 0}))
        git(self.app, "checkout", "-q", "--detach", c["A"])
        self.run_step(0, REF=c["C"])
        self.assert_out(f"A killed run deployed {c['B'][:12]} to ENV=prod but did not finish; the build root follows it.")
        self.assert_root("C")

    def test_a_run_restarted_on_a_newer_setup_goes_on_with_its_attempt(self):
        c = self.commits
        # What update_setup_and_restart leaves before it starts the step again on the newer setup.
        restarted = Attempt(self.app, "prod", U(15))
        restarted.create(sha=c["B"])
        restarted.event("accepted", "ok")
        restarted.event("setup", "updated", to="v9.9.9")
        self.run_step(64, REF=c["B"], DEPLOY_ATTEMPT=U(15))
        self.assert_out("is known in ENV=prod already")
        self.run_step(0, REF=c["B"], DEPLOY_ATTEMPT=U(15), SETUP_SELF_UPDATED="v9.9.9")
        self.assertEqual(self.attempt(U(15))["outcome"], "live")
        self.assertEqual([stage for stage, _ in self.stages(U(15))].count("accepted"), 1)

    def test_the_status_document_reads_the_attempt_and_the_history(self):
        c = self.commits
        self.run_step(0, REF=c["A"], DEPLOY_ATTEMPT=U(13), ERP_API_KEY="")
        backups = self.app / ".generated/backups"
        backups.mkdir(parents=True)
        for name in ("worker-prod-20261104T013000Z.sql.gz", "worker-prod-20261105T013000Z.sql.gz",
                     "worker-stage-20261106T013000Z.sql.gz"):
            (backups / name).write_text("")
        history = self.history()
        history[-1]["backup"] = str(backups / "fixture-prod-20261105T104012123456.sql.gz")
        (self.app / ".generated/release/prod/deployments.json").write_text(json.dumps(history))
        document = json.loads(self.project("--env", "prod", "deploy-status", "--attempt", U(13), "--run-id", "123",
                                           "--run-attempt", "1", "--request-id", U(13), "--operation", "deploy"))
        self.assertEqual({key: document[key] for key in ("format", "project", "environment", "run")},
                         {"format": "dde-deploy-status/v1", "project": "fixture", "environment": "prod",
                          "run": {"operation": "deploy", "request_id": U(13), "run_id": 123, "run_attempt": 1}})
        self.assertEqual((document["attempt"]["known"], document["attempt"]["outcome"], document["attempt"]["sha"]),
                         (True, "live", c["A"]))
        self.assertEqual(document["live"]["commit"], c["A"])
        self.assertEqual(document["history"][-1]["backup"], "fixture-prod-20261105T104012123456.sql.gz", "names, not paths")
        self.assertIn("HTPASSWD_API", document["settings"]["required"])
        self.assertIn("ERP_API_KEY", document["settings"]["empty_at_last_deploy"])
        self.assertEqual(document["backups"]["latest_nightly"]["file"], "worker-prod-20261105T013000Z.sql.gz")
        self.assertEqual(document["build_root"]["layout"], "one-repository")
        unknown = json.loads(self.project("--env", "prod", "deploy-status", "--attempt", U(14)))
        self.assertEqual((unknown["attempt"]["known"], unknown["attempt"]["outcome"]), (False, "unknown_attempt"))
        self.assertIsNone(json.loads(self.project("--env", "prod", "deploy-status"))["attempt"])
        self.assertEqual(self.project("--env", "prod", "deploy-status", "--field", "live").strip(), c["A"])
        self.assertEqual(self.project("--env", "stage", "deploy-status", "--field", "live").strip(), "none")
        self.assertEqual(self.project("--env", "prod", "deploy-status", "--attempt", U(13), "--field", "code_reached").strip(),
                         "true")
        self.assertEqual(self.project("--env", "prod", "deploy-status", "--attempt", U(14), "--field", "code_reached").strip(),
                         "false")
        self.project("--env", "prod", "deploy-status", "--attempt", "x/../y", expected=64)
        self.project("--env", "prod", "deploy-status", "--field", "code_reached", expected=64)
        # Read only: the files are as they were.
        self.assertEqual(self.history(), history)
        self.assertFalse((self.app / f".generated/release/prod/attempts/{U(14)}.json").exists())


class HealthPipelineTest(AttemptFixture):
    """A project that configures the health check: the deploy checks it, and prod rolls back when it fails."""
    common = pipeline.COMMON + HEALTH

    def test_a_healthy_deploy_is_live(self):
        c = self.commits
        self.run_step(0, REF=c["B"], DEPLOY_ATTEMPT=U(20))
        self.assertEqual(self.heads(), [f"make deploy ENV=prod ref={c['B']} backup=0 cache=auto attempt={U(20)}",
                                        f"make deploy-health ENV=prod attempt={U(20)}"])
        self.assertEqual(self.attempt(U(20))["outcome"], "live")
        health = self.attempt(U(20))["events"][-1]
        self.assertEqual((health["stage"], health["state"], health["release_seen"]), ("health", "ok", c["B"]))
        self.assertEqual([(r["path"], r["status"], r["tries"]) for r in health["results"]], [("/", 200, 1), ("/_health", 200, 1)])
        self.assertEqual(self.history()[-1]["health"], "ok")
        path, headers = self.site.requests[-1]
        self.assertEqual((headers["Host"], headers["X-Forwarded-Proto"]), ("fixture.test", "https"))
        self.assertNotIn("SECRET-BODY", json.dumps(self.attempt(U(20))) + self.output)

    def test_a_failed_check_on_prod_rolls_back_and_checks_again(self):
        c = self.commits
        self.run_step(0, REF=c["A"], DEPLOY_ATTEMPT=U(21))
        self.site.broken.add(c["B"])
        self.run_step(1, REF=c["B"], DEPLOY_ATTEMPT=U(22), CRON="yes")
        self.assertEqual(self.heads(), [f"make deploy ENV=prod ref={c['B']} backup=0 cache=auto attempt={U(22)}",
                                        f"make deploy-health ENV=prod attempt={U(22)}",
                                        f"make deploy ENV=prod rollback=1 cache=auto attempt={U(22)}",
                                        f"make deploy-health ENV=prod attempt={U(22)} rollback=1",
                                        "make cron-reload ENV=prod"])
        self.assert_log(f"make deploy ENV=prod rollback=1 cache=auto attempt={U(22)} | head=A")
        record = self.attempt(U(22))
        self.assertEqual((record["outcome"], record["reason"]), ("rolled_back", "health"))
        self.assertEqual(self.stages(U(22))[-3:], [("health", "failed"), ("rollback", "ok"), ("health_after_rollback", "ok")])
        failed = next(event for event in record["events"] if event["stage"] == "health")
        self.assertEqual([(r["path"], r["status"]) for r in failed["results"]], [("/", 404), ("/_health", 200)])
        self.assertEqual([(e["commit"], e.get("failed"), e.get("health"), e["rollback"]) for e in self.history()],
                         [(c["A"], None, "ok", False), (c["B"], "health", "failed", False), (c["A"], None, "ok", True)])
        self.assert_root("A")
        document = json.loads(self.project("--env", "prod", "deploy-status", "--attempt", U(22)))
        self.assertEqual((document["attempt"]["outcome"], document["live"]["commit"], document["live"]["rollback"]),
                         ("rolled_back", c["A"], True))
        # The failed release is never a rollback target, and the guard expects the release that serves.
        self.run_step(64, REF=c["C"], DEPLOY_ATTEMPT=U(23), DEPLOY_EXPECTED_LIVE=c["B"])
        self.assertEqual(self.attempt(U(23))["reason"], "live_changed")

    def test_a_failed_hook_on_prod_rolls_back(self):
        c = self.commits
        self.run_step(0, REF=c["A"])
        self.run_step(3, REF=c["B"], DEPLOY_ATTEMPT=U(24), FAIL="after-rsync")
        self.assertEqual(self.heads()[1:], [f"make deploy ENV=prod rollback=1 cache=auto attempt={U(24)}",
                                            f"make deploy-health ENV=prod attempt={U(24)} rollback=1"])
        self.assertEqual((self.attempt(U(24))["outcome"], self.attempt(U(24))["reason"]), ("rolled_back", "hook"))
        self.assert_root("A")

    def test_stage_keeps_the_failed_code_and_says_so(self):
        c = self.commits
        stage = {"environment": "stage", "BITBUCKET_DEPLOYMENT_ENVIRONMENT": "staging"}
        self.run_step(0, REF=c["A"], DEPLOY_ATTEMPT=U(25), **stage)
        self.site.broken.add(c["B"])
        self.run_step(1, REF=c["B"], DEPLOY_ATTEMPT=U(26), **stage)
        self.assertNotIn("rollback", self.make_log())
        self.assertEqual((self.attempt(U(26), "stage")["outcome"], self.attempt(U(26), "stage")["reason"]),
                         ("failed_after_code", "health"))
        self.assertEqual(git(self.app, "log", "-1", "--format=%s"), "B", "the code on the target and its config agree")

    def test_health_no_leaves_the_check_and_the_rollback_to_the_workflow(self):
        c = self.commits
        self.run_step(0, REF=c["A"], DEPLOY_ATTEMPT=U(27), HEALTH="no")
        self.assertEqual(self.attempt(U(27))["outcome"], "in_progress", "the workflow's own step checks it")
        self.assertNotIn("deploy-health", self.make_log())
        self.project("--env", "prod", "deploy-health", "--attempt", U(27))
        self.assertEqual(self.attempt(U(27))["outcome"], "live")
        self.project("--env", "prod", "deploy-health", "--attempt", U(28), expected=64)  # not the last deploy
        self.run_step(3, REF=c["B"], DEPLOY_ATTEMPT=U(28), HEALTH="no", FAIL="after-rsync")
        self.assertEqual((self.attempt(U(28))["outcome"], self.attempt(U(28))["reason"]), ("failed_after_code", "hook"))
        self.assertEqual(self.project("--env", "prod", "deploy-status", "--attempt", U(28), "--field", "code_reached").strip(),
                         "true")
        # The workflow's Roll back step: the same attempt follows its own failed deploy.
        self.run_step(0, ROLLBACK="yes", DEPLOY_ATTEMPT=U(28), HEALTH="no")
        self.assertEqual(self.attempt(U(28))["outcome"], "rolled_back")
        self.project("--env", "prod", "deploy-health", "--attempt", U(28), "--rollback")
        self.assertEqual(self.stages(U(28))[-2:], [("rollback", "ok"), ("health_after_rollback", "ok")])
        self.run_step(64, ROLLBACK="yes", DEPLOY_ATTEMPT=U(28))
        self.assert_out("is known in ENV=prod already (outcome: rolled_back)")

    def test_without_an_attempt_the_check_still_guards_production(self):
        c = self.commits
        self.run_step(0, REF=c["A"])
        self.site.broken.add(c["B"])
        self.run_step(1, REF=c["B"])
        self.assertEqual(self.heads()[-2:], ["make deploy ENV=prod rollback=1 cache=auto", "make deploy-health ENV=prod rollback=1"])
        self.assertEqual(self.history()[1].get("failed"), "health")
        self.assertFalse(list((self.app / ".generated/release/prod").glob("attempts/*.json")))


class HealthCheckTest(unittest.TestCase):
    """make deploy-health itself against a local site: retries, redirects, the release and the refusals."""

    commit = "1" * 40

    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="health-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.answers, self.requests = {}, []
        test = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                test.requests.append(self.path)
                answers = test.answers.get(self.path, [(200, b"ok")])
                status, body = answers.pop(0) if len(answers) > 1 else answers[0]
                self.send_response(status)
                if status in (301, 302):
                    self.send_header("Location", "/elsewhere")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        (self.directory / "env").mkdir()
        self.settings({"DEPLOY_HEALTH_PATHS": '"/ /old /flaky"', "DEPLOY_HEALTH_RELEASE_PATH": "/_health"})
        (self.directory / "env/prod.env.example").write_text(f"DOMAIN=\"shop.test www.shop.test\"\nLOCALHOST_PORT={server.server_address[1]}\n")
        self.history = self.directory / ".generated/release/prod/deployments.json"
        self.history.parent.mkdir(parents=True)
        self.history.write_text(json.dumps([{"ref": "v1", "commit": self.commit, "rollback": False, "attempt": U(1)}]))
        self.answers["/_health"] = [(200, json.dumps({"release": self.commit}).encode())]

    def settings(self, values):
        (self.directory / "env/common.env").write_text("PROJECT_NAME=shop\n" + "".join(f"{k}={v}\n" for k, v in values.items()))

    def check(self, expected, **options):
        out, err = io.StringIO(), io.StringIO()
        environ = {"PATH": os.environ["PATH"], **options.pop("environ", {})}
        status = deploy_health(self.directory, "prod", environ=environ, out=out, err=err, sleep=lambda seconds: None,
                               retry_delay=0, release_wait=0.2, **options)
        self.output = out.getvalue() + err.getvalue()
        self.assertEqual(status, expected, self.output)
        return json.loads(self.history.read_text())[-1]

    def test_retries_once_follows_no_redirect_and_keeps_no_body(self):
        self.answers["/flaky"] = [(502, b"bad gateway SECRET-BODY"), (200, b"ok")]
        self.answers["/old"] = [(302, b"")]
        entry = self.check(0, attempt=U(1))
        self.assertEqual(entry["health"], "ok")
        self.assertNotIn("/elsewhere", self.requests, "a redirect passes and is not followed")
        event = json.loads((self.directory / f".generated/release/prod/attempts/{U(1)}.json").read_text())["events"][-1]
        self.assertEqual([(r["path"], r["status"], r["tries"]) for r in event["results"]],
                         [("/", 200, 1), ("/old", 302, 1), ("/flaky", 200, 2), ("/_health", 200, 1)])
        self.assertNotIn("SECRET-BODY", self.output + self.history.read_text() + json.dumps(event))

    def test_a_client_error_fails_at_once_and_marks_the_release(self):
        self.answers["/old"] = [(404, b"")]
        entry = self.check(1, environ={"DEPLOY_HEALTH_EXTRA": "/extra /old"})
        self.assertEqual((entry["health"], entry["failed"]), ("failed", "health"))
        self.assertEqual(self.requests.count("/old"), 1)
        self.assertIn("/extra", self.requests, "the caller's pages are checked too")
        self.assertIn("FAILED 404 /old", self.output)

    def test_the_release_page_must_report_the_deployed_commit(self):
        self.answers["/_health"] = [(200, json.dumps({"release": "2" * 40}).encode())]
        entry = self.check(1, attempt=U(1))
        self.assertEqual(entry["failed"], "health")
        record = json.loads((self.directory / f".generated/release/prod/attempts/{U(1)}.json").read_text())
        self.assertEqual((record["outcome"], record["reason"], record["events"][-1]["release_seen"]),
                         ("failed_after_code", "health", "2" * 40))
        self.answers["/_health"] = [(200, b'{"release": "<script>"}')]
        self.check(1)
        self.assertIn("release /_health: unreadable", self.output)

    def test_refusals_change_nothing(self):
        before = self.history.read_text()
        for options, message in (({"attempt": U(2)}, f"The last deploy of this environment is not attempt {U(2)}"),
                                 ({"rollback": True}, "the last deploy of this environment is no rollback"),
                                 ({"environ": {"DEPLOY_HEALTH_EXTRA": "/a b"}}, "Health check path refused: 'b'"),
                                 ({"environ": {"DEPLOY_HEALTH_EXTRA": " ".join(f"/p{n}" for n in range(30))}},
                                  "At most 30 health check paths"),
                                 ({"attempt": "../x"}, "attempt must be 8-64")):
            with self.subTest(message=message):
                self.check(64, **options)
                self.assertIn(message, self.output)
                self.assertIn("Nothing was checked.", self.output)
        self.settings({})
        self.check(64)
        self.assertIn("No health check pages", self.output)
        self.assertEqual(self.history.read_text(), before)
        self.assertEqual(self.requests, [])

    def test_another_server_is_asked_over_https_at_its_address(self):
        endpoint = Endpoint({"DOMAIN": "shop.test www.shop.test", "DEPLOY_SSH": "user@host", "DEPLOY_HEALTH_ADDRESS": "10.0.0.5"})
        self.assertEqual((endpoint.local, endpoint.domain, endpoint.address, endpoint.port), (False, "shop.test", "10.0.0.5", 443))
        connection = endpoint.connection()
        self.assertEqual((connection.host, connection.port), ("shop.test", 443), "SNI, Host and the certificate check")
        from project_deploy_health import Refusal
        with self.assertRaisesRegex(Refusal, "DEPLOY_HEALTH_ADDRESS must be an IPv4 or IPv6 address"):
            Endpoint({"DOMAIN": "shop.test", "DEPLOY_SSH": "user@host", "DEPLOY_HEALTH_ADDRESS": "shop.test"})
        local = Endpoint({"DOMAIN": "shop.test", "LOCALHOST_PORT": "18080", "PUBLISH_ADDRESS": "10.8.0.14"})
        self.assertEqual((local.local, local.address, local.port), (True, "10.8.0.14", 18080))
        with self.assertRaisesRegex(Refusal, "needs LOCALHOST_PORT"):
            Endpoint({"DOMAIN": "shop.test"})


class StatusDocumentTest(unittest.TestCase):
    def test_the_document_stays_within_64_kib(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="status-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        (directory / "env").mkdir()
        (directory / "env/common.env").write_text("PROJECT_NAME=shop\n")
        (directory / "env/prod.env.example").write_text("DOMAIN=shop.test\n")
        history = directory / ".generated/release/prod/deployments.json"
        history.parent.mkdir(parents=True)
        entry = {"ref": "v1", "commit": "1" * 40, "rollback": False, "migrations": ["m" * 200] * 30}
        history.write_text(json.dumps([entry] * 40))
        document = status_document(directory, "prod")
        self.assertLessEqual(len(json.dumps(document)), 64 * 1024)
        self.assertLess(len(document["history"]), 20)
        self.assertEqual(document["live"]["commit"], "1" * 40)


if __name__ == "__main__":
    unittest.main()
