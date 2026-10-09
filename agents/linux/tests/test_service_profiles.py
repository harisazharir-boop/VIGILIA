from __future__ import annotations
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

class ServiceProfilesTests(unittest.TestCase):
    def test_backend_profile_recovers_and_shuts_down_gracefully(self):
        text = (ROOT / 'deploy/systemd/vigilia-backend.service').read_text()
        self.assertIn('Restart=on-failure', text); self.assertIn('KillSignal=SIGTERM', text); self.assertIn('TimeoutStopSec=20', text)
    def test_agent_profile_is_user_session_safe_for_wayland(self):
        text = (ROOT / 'deploy/systemd/vigilia-agent.service').read_text()
        self.assertIn('WantedBy=default.target', text); self.assertIn('Restart=on-failure', text); self.assertNotIn('User=root', text)
        self.assertIn('Environment=PYTHONPATH=%h/vigilia', text)
        self.assertIn('ExecStart=/usr/bin/env PYTHONPATH=%h/vigilia', text)
    def test_windows_profile_prevents_duplicates_and_restarts(self):
        text = (ROOT / 'deploy/windows/VIGILIA-Agent.xml').read_text()
        self.assertIn('IgnoreNew', text); self.assertIn('RestartOnFailure', text); self.assertIn('InteractiveToken', text)
    def test_windows_profile_is_valid_xml(self):
        import xml.etree.ElementTree as ET
        tree = ET.parse(ROOT / 'deploy/windows/VIGILIA-Agent.xml')
        self.assertEqual(tree.getroot().tag, '{http://schemas.microsoft.com/windows/2004/02/mit/task}Task')
    def test_uninstaller_removes_all_service_units(self):
        text = (ROOT / 'deploy/uninstall-linux.sh').read_text()
        self.assertIn('vigilia-backend.service', text)
        self.assertIn('vigilia-llama.service', text)
        self.assertIn('vigilia-agent.service', text)
        self.assertIn('retained intentionally', text)
    def test_all_deploy_shell_scripts_syntax(self):
        import subprocess
        scripts = [
            'deploy/install-linux.sh',
            'deploy/uninstall-linux.sh',
            'deploy/database-migrate.sh',
            'deploy/database-restore.sh',
            'scripts/llama-runtime.sh',
            'scripts/backend-start.sh',
            'scripts/agent-linux-start.sh',
        ]
        for script in scripts:
            res = subprocess.run(['bash', '-n', str(ROOT / script)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"syntax error in {script}: {res.stderr}")
    def test_llama_profile_is_nonroot_loopback_and_bounded(self):
        text = (ROOT / 'deploy/systemd/vigilia-llama.service').read_text()
        self.assertIn('User=vigilia', text); self.assertIn('Group=vigilia', text)
        self.assertIn('--host 127.0.0.1', text); self.assertIn('--parallel ${VIGILIA_LOCAL_AI_PARALLEL}', text)
        self.assertIn('ExecStartPre=/usr/bin/node', text); self.assertIn('Restart=on-failure', text)
    def test_installer_is_idempotent_and_never_runs_agent_as_root(self):
        text = (ROOT / 'deploy/install-linux.sh').read_text()
        self.assertIn('user_systemctl enable vigilia-agent.service', text)
        self.assertIn('if [[ ! -f "$BACKEND_ENV" ]]', text)
        self.assertIn('VIGILIA_BACKEND_USER', text)
        self.assertIn('SUDO_USER', text)
        self.assertIn('sudo -u "$SERVICE_USER"', text)
        self.assertIn('DBUS_SESSION_BUS_ADDRESS="unix:path=$USER_RUNTIME/bus"', text)
        self.assertIn('No active user systemd bus', text)
        self.assertIn('VIGILIA_AGENT_TOKEN_FILE=$SERVICE_HOME/.config/vigilia/agent.token', text)
        self.assertNotIn('VIGILIA_AGENT_TOKEN_FILE=%h/', text)
        self.assertIn('npm ci --workspaces --include-workspace-root', text)
        self.assertIn('npx --no-install prisma generate', text)
        self.assertIn('database-migrate.sh', text)
        self.assertIn('-m 0640', text)
        self.assertIn('sudo chown root:"$BACKEND_USER" "$BACKEND_ENV"', text)
        self.assertIn('VIGILIA_LLAMA_ENV', text)
        self.assertIn('vigilia-llama.service', text)
        self.assertIn('--exclude models --exclude bin', text)
        self.assertIn('setfacl -R -m u:"$SERVICE_USER":rX "$ROOT/agents"', text)
        self.assertIn('npm prune --omit=dev', text)
        self.assertIn('deploy/prisma-preflight.mjs', text)
        self.assertNotIn('sudo python3', text)
