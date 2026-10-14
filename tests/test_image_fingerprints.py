"""Per-image fingerprints and the image-prune plan, without building or removing images."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker")]
from project import Project
from project_images import LEGACY_NAME, build_images, plan_prune, prune_images
from setup_release import build_fingerprint, dockerfile_sources, image_suffixes, suffix_variable


class FingerprintTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.context = Path(self.temporary.name)
        for name in ("alpha", "beta"):
            (self.context / name / "conf").mkdir(parents=True)
            (self.context / name / "Dockerfile").write_text(
                f"ARG VERSION=1\nFROM scratch\nCOPY /{name}/conf/${{VERSION}}.ini /etc/\n")
            (self.context / name / "conf/1.ini").write_text("value=1\n")
        (self.context / "shared.txt").write_text("not copied by anyone\n")

    def services(self):
        return {name: {"build": {"context": str(self.context), "dockerfile": f"{name}/Dockerfile",
                                 "args": {"VERSION": "1"}}} for name in ("alpha", "beta")}

    def test_changing_one_image_input_changes_only_that_fingerprint(self):
        before = image_suffixes("prestashop", self.services())
        (self.context / "alpha/conf/1.ini").write_text("value=2\n")
        after = image_suffixes("prestashop", self.services())
        self.assertNotEqual(before["alpha"], after["alpha"])
        self.assertEqual(before["beta"], after["beta"])

    def test_files_outside_the_copied_sources_do_not_matter(self):
        before = image_suffixes("", self.services())
        (self.context / "shared.txt").write_text("changed\n")
        (self.context / "alpha/conf/2.ini").write_text("unused version\n")
        self.assertEqual(before, image_suffixes("", self.services()))

    def test_arguments_profile_and_dependency_are_inputs(self):
        build = self.services()["alpha"]["build"]
        base = build_fingerprint("alpha", build, "")
        self.assertNotEqual(base, build_fingerprint("alpha", {**build, "args": {"VERSION": "2"}}, ""))
        self.assertNotEqual(base, build_fingerprint("alpha", build, "prestashop"))
        self.assertNotEqual(base, build_fingerprint("alpha", build, "", dependency="-sabc"))

    def test_dockerfile_sources_skip_stage_copies_and_expand_arguments(self):
        text = "ARG V=8\nCOPY --from=composer /usr/bin/composer /x\nCOPY /php/${V}/php.ini \\\n  /y\nADD --chown=1 a b /dst\n"
        self.assertEqual(dockerfile_sources(text, {"V": "8.4"}), ["/php/8.4/php.ini", "a", "b"])

    def test_suffix_variable_names(self):
        self.assertEqual(suffix_variable("php-fpm-base"), "SETUP_IMAGE_SUFFIX_PHP_FPM_BASE")
        self.assertEqual(suffix_variable("nginx-proxy"), "SETUP_IMAGE_SUFFIX_NGINX_PROXY")


class ProjectFingerprintTest(unittest.TestCase):
    def test_each_built_service_gets_its_own_suffix_and_php_chain_is_linked(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            directory = Path(temporary) / "example/app"
            (directory / "env").mkdir(parents=True)
            (directory / "compose").mkdir()
            (directory / "env/common.env").write_text("PROJECT_NAME=example\nPROFILE=prestashop\n")
            (directory / "env/local.env").write_text("DOMAIN=example.local\nLOCALHOST_PORT=3301\nLOCALHOST_PORT_SSL=3302\n"
                                                     "DATABASE_USER=t\nDATABASE_NAME=t\nDATABASE_ROOT_PASSWORD=root-test-only\nDATABASE_PASSWORD=t\n")
            project = Project(directory)
            suffixes = project.image_suffixes
            self.assertEqual(set(suffixes), {"php-fpm-base", "php-fpm-core", "php-fpm", "webserver", "nginx-proxy", "database"})
            self.assertEqual(len(set(suffixes.values())), len(suffixes))
            services = project.model()["services"]
            self.assertTrue(services["php-fpm"]["image"].endswith(suffixes["php-fpm"]))
            self.assertTrue(services["database"]["image"].endswith(suffixes["database"]))
            self.assertEqual(services["php-fpm-core"]["build"]["args"]["SETUP_IMAGE_SUFFIX"], suffixes["php-fpm-base"])
            self.assertTrue(services["php-fpm"]["build"]["args"]["SETUP_PHP_IMAGE"].endswith(suffixes["php-fpm-core"]))
            labels = services["webserver"]["build"]["labels"]
            self.assertEqual(labels["setup.fingerprint"], suffixes["webserver"])
            self.assertEqual(services["php-fpm"]["build"]["labels"]["setup.project"], "example")


class PrunePlanTest(unittest.TestCase):
    def test_plan_keeps_used_and_expected_images(self):
        images = {
            "sha256:used": {"names": ["mysql-local-8.4.0-sold"], "size": "1GB"},
            "sha256:expected": {"names": ["forsena-php-fpm-local-snew"], "size": "1GB"},
            "sha256:stale": {"names": ["php-fpm-base-local-7.1-2.9.8-2.2-sold"], "size": "1GB"},
        }
        plan = plan_prune(images, {"sha256:used"}, {"forsena-php-fpm-local-snew"})
        self.assertEqual(set(plan), {"sha256:stale"})

    def test_images_of_a_release_lock_are_kept_for_deploy_and_rollback(self):
        from project_images import released_image_ids
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "shop"
            lock = root / "app/.generated/release/prod/images.lock.json"
            lock.parent.mkdir(parents=True)
            lock.write_text(json.dumps({"images": {"shop-php-fpm-prod-s1": "sha256:released"}}))
            other = Path(temporary) / "other/app/.generated/release/stage/images.lock.json"
            other.parent.mkdir(parents=True)
            other.write_text(json.dumps({"images": {"other-php-fpm-stage-s2": "sha256:other"}}))
            project = SimpleNamespace(root=root, directory=root / "app")
            self.assertEqual(released_image_ids(project), {"sha256:released", "sha256:other"})

    def test_legacy_names_match_setup_images_only(self):
        for name in ("php-fpm-base-local-7.3-3.1.5-2.7.6", "mysql-local-8.4.0-s1bd8429c96c7",
                     "forsena-php-fpm-local-s98210b6d2a04", "webserver-apache-local-2.4.59"):
            self.assertTrue(LEGACY_NAME.match(name), name)
        for name in ("mysql", "redis", "php", "mcr.microsoft.com/playwright"):
            self.assertFalse(LEGACY_NAME.match(name), name)

    def test_preview_never_removes_and_apply_removes_only_the_plan(self):
        project = object()
        with patch("project_images.setup_images", return_value={"sha256:stale": {"names": ["pma-local-5-sold"], "size": "1MB"}}), \
             patch("project_images.used_image_ids", return_value=set()), \
             patch("project_images.expected_image_names", return_value=set()), \
             patch("project_images.released_image_ids", return_value=set()), \
             patch("project_images.subprocess.run") as run:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                prune_images(project)
            run.assert_not_called()
            self.assertIn("apply=1", output.getvalue())
            with contextlib.redirect_stdout(io.StringIO()):
                prune_images(project, apply=True)
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0], ["docker", "rmi", "sha256:stale"])


class BuildOrderTest(unittest.TestCase):
    def test_locally_built_parents_come_first(self):
        calls = []
        build_images(SimpleNamespace(run=lambda args, profiles=None: calls.append((args, profiles))))
        self.assertEqual(calls, [(["build", "php-fpm-base"], "build_only"), (["build", "php-fpm-core"], "build_only"),
                                 (["build"], None)])


if __name__ == "__main__":
    unittest.main()
