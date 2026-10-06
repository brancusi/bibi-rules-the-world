"""Run the real installer task locally, never the provisioning play.

Only template.src is made absolute; destination/owner/group use synthetic home
and the current user's identity. No become, real Pi, gh, or credential helper.
"""

import grp
import json
import os
from pathlib import Path
import pwd
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
TASK = "Install the generic authenticated private capability command"


class PrivateCapabilitiesRenderTests(unittest.TestCase):
    def test_real_task_and_rendered_command(self):
        with tempfile.TemporaryDirectory(prefix=".test-private-", dir=ROOT) as tmp:
            root = Path(tmp)
            home = root / "home"
            binary = home / ".local/bin/bibi-private-capabilities-update"
            binary.parent.mkdir(parents=True)
            pi_home = home / "synthetic pi home"
            tasks = yaml.safe_load((ROOT / "site.yml").read_text())[0]["tasks"]
            matches = [task for task in tasks if task.get("name") == TASK]
            self.assertEqual(len(matches), 1)
            task = matches[0]
            module = task["ansible.builtin.template"]
            module["src"] = str(ROOT / module["src"])
            # Preserve the real task's owner/group expressions, without privilege.
            user = pwd.getpwuid(os.getuid()).pw_name
            self.assertEqual(grp.getgrnam(user).gr_gid, os.getegid())
            play = root / "render.yml"
            play.write_text(yaml.safe_dump([{
                "name": "Isolated private installer render",
                "hosts": "all", "connection": "local", "become": False,
                "gather_facts": False,
                "vars": {"bibi_agent_home": str(home), "bibi_agent_user": user,
                         "bibi_pi_home": str(pi_home),
                         "ansible_python_interpreter": "/usr/bin/python3"},
                "tasks": [task],
            }]))
            env = {**os.environ, "HOME": str(home), "ANSIBLE_NOCOLOR": "1",
                   "ANSIBLE_LOCAL_TEMP": str(root / "ansible-local"),
                   "ANSIBLE_REMOTE_TEMP": str(root / "ansible-remote")}
            command = ["ansible-playbook", "-i", "localhost,", str(play)]
            for iteration in range(2):
                result = subprocess.run(command, cwd=ROOT, env=env, text=True,
                                        capture_output=True, timeout=90)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if iteration:
                    self.assertIn("changed=0", result.stdout)
            rendered = binary.read_text()
            self.assertNotIn("{{", rendered)
            self.assertEqual(rendered.count(str(pi_home)), 2)
            self.assertIn("((${#sources[@]} < 32))", rendered)
            self.assertIn("((${#sources[@]} > 0))", rendered)
            subprocess.run(["bash", "-n", str(binary)], check=True)
            self.assertEqual(binary.stat().st_mode & 0o777, 0o755)

            fake = root / "fake-bin"
            fake.mkdir()
            log = root / "external-calls.jsonl"
            for name in ("gh", "pi"):
                stub = fake / name
                stub.write_text('''#!/usr/bin/python3
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
with open(os.environ['FIXTURE_LOG'], 'a') as output:
    output.write(json.dumps([name, sys.argv[1:], os.environ.get('PI_CODING_AGENT_DIR'),
                             os.environ.get('GIT_TERMINAL_PROMPT')]) + '\\n')
if name == 'gh' and sys.argv[1:] == ['auth', 'status']:
    sys.exit(int(os.environ.get('FIXTURE_AUTH_FAIL', '0')))
if name == 'gh':
    assert sys.argv[1:] == ['auth', 'setup-git']
else:
    assert sys.argv[1] == 'install'
''')
                stub.chmod(0o755)
            for key in ("PI_CODING_AGENT_DIR", "GIT_TERMINAL_PROMPT"):
                env.pop(key, None)
            env.update(PATH=str(fake) + os.pathsep + os.environ["PATH"],
                       FIXTURE_LOG=str(log))
            manifest = root / "manifest"
            schema = "schema=bibi-private-capabilities.v1\n"
            sources = [f"git:https://github.com/fixture/repo{i}.git@{'a' * 40}"
                       for i in range(33)]

            def run(content, mode="--plan", error=None, auth_fail=False):
                manifest.write_text(content)
                result = subprocess.run([str(binary), mode, str(manifest)], env={
                    **env, "FIXTURE_AUTH_FAIL": str(int(auth_fail))}, text=True,
                    capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0 if error is None else 1,
                                 result.stdout + result.stderr)
                if error:
                    self.assertIn(error, result.stderr)
                return result.stdout

            def rows(count):
                return schema + "".join(f"source={s}\n" for s in sources[:count])

            for count in (1, 32):
                output = run(rows(count))
                self.assertIn(f"Private Pi home: {pi_home}\n", output)
                self.assertEqual(output.count("  git:https://"), count)
                self.assertIn("plan only", output)
            invalid = [
                (schema, "contains no sources"),
                (rows(33), "exceeds 32 sources"),
                (rows(1) + f"source={sources[0]}\n", "duplicate private manifest source"),
                (schema + rows(1), "duplicate manifest schema"),
                (f"source={sources[0]}\n", "missing schema"),
                (schema + "source=git:https://github.com/fixture/repo.git@main\n", "exact source="),
                (schema + "source=git:https://token@github.com/fixture/repo.git@" + "a" * 40, "exact source="),
                ("#" * 65537, "must not exceed 65536 bytes"),
            ]
            for content, error in invalid:
                for mode in ("--plan", "--apply"):
                    run(content, mode, error)
            # Boundary-sized comments are accepted, not silently truncated.
            content = rows(1)
            run(content + "#" * (65536 - len(content)))
            link = root / "manifest-link"
            link.symlink_to(manifest)
            for path in (link, root / "missing"):
                result = subprocess.run([str(binary), "--apply", str(path)], env=env,
                                        text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 1)
                self.assertIn("regular non-symlink file", result.stderr)
            self.assertFalse(log.exists(), "plan/validation must not invoke gh or pi")
            run(rows(1), "--apply", "run 'gh auth login'", auth_fail=True)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(calls, [["gh", ["auth", "status"], None, None]])
            log.unlink()
            run(rows(2), "--apply")
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(calls, [
                ["gh", ["auth", "status"], None, None],
                ["gh", ["auth", "setup-git"], None, None],
                *[["pi", ["install", source], str(pi_home), "0"] for source in sources[:2]],
            ])
            self.assertFalse(pi_home.exists(), "fake installs must not touch Pi home")


if __name__ == "__main__":
    unittest.main()
