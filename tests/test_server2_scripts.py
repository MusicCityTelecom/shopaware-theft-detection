"""Exercise Linux operator scripts with isolated state and a fake Docker CLI."""
import os
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Linux operator scripts')
class Server2Scripts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='shopaware-script-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / 'state'
        self.repo = self.root / 'repo'
        self.bin = self.root / 'bin'
        for directory in ['data', 'models', 'training', 'runs', 'alerts', 'incidents']:
            (self.state / directory).mkdir(parents=True)
        self.repo.mkdir()
        self.bin.mkdir()
        (self.repo / '.env').write_text('SYNTHETIC_CONFIG=test\n')
        (self.state / 'data/.shopaware.key').write_text('synthetic matching key')
        with sqlite3.connect(self.state / 'data/shopaware.db') as conn:
            conn.execute('CREATE TABLE preserved(value TEXT)')
            conn.execute("INSERT INTO preserved VALUES('preserved')")
        self.log = self.root / 'docker.log'
        self.executable('docker', '''#!/usr/bin/env bash
case " $* " in
  *" ps "*) echo shopaware ;;
  *" stop "*) echo stop >> "$DOCKER_TEST_LOG" ;;
  *" start "*) echo start >> "$DOCKER_TEST_LOG" ;;
esac
''')
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        SHOPAWARE_REPO=str(self.repo), SHOPAWARE_BACKUP_ROOT=str(self.root / 'backups'),
                        DOCKER_TEST_LOG=str(self.log), INCLUDE_MEDIA='1')
        # Redirect fixed appliance paths only in this temporary test copy.
        source = (ROOT / 'deploy/server2/backup.sh').read_text()
        source = source.replace('STATE="/var/lib/shopaware"', 'STATE=' + shlex.quote(str(self.state)))
        source = source.replace('if [[ ${EUID} -ne 0 ]]; then', 'if false; then')
        self.script = self.root / 'backup.sh'
        self.script.write_text(source)

    def executable(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def run_backup(self):
        return subprocess.run(['bash', str(self.script)], env=self.env, capture_output=True, text=True, timeout=30)

    def test_complete_backup_checksums_and_database_survive(self):
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        backup, = [p for p in (self.root / 'backups').iterdir() if p.is_dir()]
        sums = (backup / 'SHA256SUMS').read_text()
        self.assertNotIn('SHA256SUMS', sums)
        self.assertIn('.shopaware.key', sums)
        self.assertIn('evidence.tar.gz', sums)
        subprocess.run(['sha256sum', '--check', 'SHA256SUMS'], cwd=backup, check=True, capture_output=True)
        with sqlite3.connect(backup / 'shopaware.db') as conn:
            self.assertEqual(conn.execute('SELECT value FROM preserved').fetchone()[0], 'preserved')
        self.assertEqual(self.log.read_text().splitlines(), ['stop', 'start'])
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)

    def test_missing_key_fails_before_stopping_analysis(self):
        (self.state / 'data/.shopaware.key').unlink()
        result = self.run_backup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing required backup input', result.stderr)
        self.assertFalse(self.log.exists())

    def test_archive_failure_restarts_backend_and_reports_incomplete(self):
        self.executable('tar', '#!/usr/bin/env bash\nexit 9\n')
        result = self.run_backup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Backup failed', result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ['stop', 'start'])

    def test_verifier_rejects_unprotected_readiness(self):
        self.executable('curl', '''#!/usr/bin/env bash
case " $* " in *" -w "*) printf '%s' "$TEST_HTTP_STATUS" ;; esac
''')
        script = ROOT / 'deploy/server2/verify.sh'
        for status, expected in [('401', 0), ('200', 1), ('503', 1)]:
            result = subprocess.run(['bash', str(script)], env=dict(self.env, TEST_HTTP_STATUS=status),
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

    def test_shell_syntax(self):
        for script in (ROOT / 'deploy/server2').glob('*.sh'):
            subprocess.run(['bash', '-n', str(script)], check=True, capture_output=True)
