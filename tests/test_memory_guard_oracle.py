"""Which Firstmate task state the installed memory guard reads as its oracle.

The real oracle-selection and policy tasks from tasks/install-memory-guard.yml
render through Ansible into a temporary home as the current user. The rendered
policy then drives the real guard decision pipeline against a synthetic
inventory, so no process is read or signalled.
"""

import importlib.util
import json
import os
from pathlib import Path
import pwd
import site
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
TASKS = ("Inspect the legacy Firstmate task state",
         "Select the Firstmate task state that owns the guarded worktree pool",
         "Install the reviewed memory guard policy")
ORACLE_KEYS = ("axi_state_dir", "firstmate_state_dir", "worktree_root")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fixtures = _load("memory_guard_test_fixtures", ROOT / "tests/test_memory_guard.py")
guard = fixtures.guard


class MemoryGuardOracleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix=".test-guard-oracle-", dir=ROOT)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.user = pwd.getpwuid(os.getuid()).pw_name
        self.legacy = self.home / "firstmate/state"
        self.instance = self.home / ".local/share/firstmate/instances/main/state"

    def render(self, agent_user=None):
        tasks = {task["name"]: task
                 for task in yaml.safe_load((ROOT / "tasks/install-memory-guard.yml").read_text())}
        selected = [tasks[name] for name in TASKS]
        policy = selected[-1]["ansible.builtin.template"]
        policy.update(dest=str(self.root / "bibi-memory-guard.conf"),
                      owner=self.user, group=self.user)
        play = self.root / "render.yml"
        play.write_text(yaml.safe_dump([{
            "name": "Isolated memory guard oracle render",
            "hosts": "all", "connection": "local", "become": False,
            "gather_facts": False,
            "vars_files": [str(ROOT / "group_vars/all.yml")],
            "vars": {"ansible_python_interpreter": "/usr/bin/python3"},
            "tasks": selected,
        }]))
        # Extra vars outrank group_vars, as the real reconcile's would.
        overrides = {"bibi_agent_home": str(self.home),
                     "bibi_agent_user": agent_user or self.user,
                     "memory_guard_config_template": str(ROOT / "templates/bibi-memory-guard.conf.j2")}
        env = {**os.environ, "ANSIBLE_NOCOLOR": "1", "PYTHONUSERBASE": site.getuserbase(),
               "ANSIBLE_LOCAL_TEMP": str(self.root / "ansible-local"),
               "ANSIBLE_REMOTE_TEMP": str(self.root / "ansible-remote")}
        result = subprocess.run(
            ["ansible-playbook", "-i", "localhost,", "-e", json.dumps(overrides), str(play)],
            cwd=ROOT, env=env, text=True, capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return guard.load_config(str(self.root / "bibi-memory-guard.conf"))

    def test_existing_legacy_home_is_the_oracle(self):
        self.legacy.mkdir(parents=True)
        config = self.render()
        self.assertEqual(config["firstmate_state_dir"], str(self.legacy))
        # The guarded pool and its other oracles are unchanged by the selection.
        self.assertEqual(config["worktree_root"], str(self.home / ".treehouse"))
        self.assertEqual(config["axi_state_dir"], str(self.home / ".chrome-devtools-axi"))

    def test_isolated_instance_default_is_kept_otherwise(self):
        cases = {
            "absent": lambda: None,
            "symlink": lambda: (self.instance.mkdir(parents=True),
                                self.legacy.parent.mkdir(parents=True),
                                self.legacy.symlink_to(self.instance)),
            "regular file": lambda: (self.legacy.parent.mkdir(parents=True),
                                     self.legacy.write_text("")),
        }
        for name, prepare in cases.items():
            with self.subTest(name):
                self.setUp()
                prepare()
                self.assertEqual(self.render()["firstmate_state_dir"], str(self.instance))
        with self.subTest("owned by another account"):
            self.setUp()
            self.legacy.mkdir(parents=True)
            other = next(entry.pw_name for entry in pwd.getpwall() if entry.pw_uid != os.getuid())
            self.assertEqual(self.render(agent_user=other)["firstmate_state_dir"], str(self.instance))

    def guard_verdict(self, config, status):
        """A finished task's leaked browser bridge, judged under `config`."""
        harness = fixtures.GuardHarness(self.root / "guard")
        harness.worktree_root = Path(config["worktree_root"])
        harness.axi_state = Path(config["axi_state_dir"])
        harness.firstmate_state = self.legacy
        (harness.axi_state / "sessions").mkdir(parents=True, exist_ok=True)
        worktree = harness.worktree("money-monk", slot=2)
        harness.bind_session("mm-a1", 1000, 10069)
        harness.task("money-monk-a1", worktree, status=status)
        outcome, signaller, _, _ = harness.run(
            fixtures.bridge_tree(1000, worktree), mode="dry-run",
            tcp=[{"port": 10069, "state": "LISTEN"}],
            **{key: config[key] for key in ORACLE_KEYS})
        self.assertEqual(signaller.sent, [])
        return fixtures.decisions(outcome)[1000]

    def test_selection_restores_cleanup_without_widening_it(self):
        self.legacy.mkdir(parents=True)
        config = self.render()
        self.assertEqual(self.guard_verdict(config, "done: shipped"),
                         ("eligible", "owning-task-completed"))
        # Live work in the legacy home is still protected exactly as before.
        for status in ("working: busy", "paused: waiting", "needs-decision: two options"):
            self.assertEqual(self.guard_verdict(config, status),
                             ("refused", "owning-task-not-terminal"))

        # The policy as rendered before this selection: the instance state, which
        # does not exist on such a host, so every finished task's tree is
        # unclaimed and the reaper silently stops cleaning.
        unselected = dict(config, firstmate_state_dir=str(self.instance))
        self.assertFalse(self.instance.exists())
        self.assertEqual(self.guard_verdict(unselected, "done: shipped"),
                         ("refused", "unclaimed-worktree"))


if __name__ == "__main__":
    unittest.main()
