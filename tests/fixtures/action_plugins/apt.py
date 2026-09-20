"""Hermetic Chrome fixture adapter. Loaded ONLY by test-browser-install.sh.

No apt/dpkg mutation: model the documented apt deb version/idempotency contract
on a synthetic JSON artifact. All get_url/checksum/stat/assert tasks remain real.
"""
import json
from pathlib import Path

from ansible.plugins.action import ActionBase
from ansible.errors import AnsibleActionFail


class ActionModule(ActionBase):
    def run(self, tmp=None, task_vars=None):
        args = self._task.args
        if set(args) != {'deb', 'allow_downgrade', 'install_recommends'} or args['allow_downgrade'] or args['install_recommends']:
            raise AnsibleActionFail('Unexpected Chrome apt contract')
        package = json.loads(Path(args['deb']).read_text())
        root = Path(task_vars['chrome_executable']).parent
        root.mkdir(parents=True, exist_ok=True)
        installed = root / 'version'
        previous = installed.read_text() if installed.exists() else None
        wanted = package['version']
        if previous and tuple(map(int, previous.replace('-', '.').split('.'))) > tuple(map(int, wanted.replace('-', '.').split('.'))):
            raise AnsibleActionFail('Refusing downgrade')
        installed.write_text(wanted)
        (root / 'chrome-sandbox').touch()
        (root / 'chrome-sandbox').chmod(0o4755)
        return {'changed': previous != wanted, 'fixture_only': True}
