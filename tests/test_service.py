import importlib.util, pathlib, unittest, multiprocessing, tempfile, socket, json, time, os
from unittest.mock import patch, Mock
spec = importlib.util.spec_from_file_location('vpn', pathlib.Path(__file__).resolve().parents[1] / 'controller' / 'service.py')
vpn = importlib.util.module_from_spec(spec); spec.loader.exec_module(vpn)

class ControllerTests(unittest.TestCase):
    def setUp(self):
        with patch.object(vpn, 'profile', return_value={'host':'192.0.2.1','port':'443'}):
            self.c = vpn.Controller()
    def test_prompt_and_auth_failure(self):
        self.assertEqual(vpn.classify_output('Two-factor authentication token: ')[0], 'token')
        self.assertEqual(vpn.classify_output('ERROR: Could not authenticate to gateway.')[0], 'error')
        self.assertEqual(vpn.classify_output('ERROR: Gateway certificate validation failed.')[0], 'error')
    def test_saml_command_and_safe_browser_url(self):
        self.assertIn('--saml-login', vpn.COMMAND)
        self.assertTrue(vpn.matches_profile(vpn.COMMAND[1:]))
        gateway = {'host': 'vpn.example.org', 'port': '10443'}
        url = 'https://vpn.example.org:10443/remote/saml/start?redirect=1'
        self.assertEqual(vpn.saml_url("INFO: Authenticate at '%s'" % url, gateway), url)
        for bad in [url.replace('https:', 'http:'), url.replace('vpn.example.org', 'evil.example'),
                    url.replace('10443', '443'), url + '&id=secret',
                    url.replace('/remote/saml/start', '/remote/saml/auth_id'),
                    url.replace('vpn.example.org', 'user@vpn.example.org')]:
            self.assertEqual(vpn.saml_url("Authenticate at '%s'" % bad, gateway), '')
    def test_saml_failures(self):
        self.assertIn('8020', vpn.classify_output('Failed to bind socket to port 8020')[1])
        self.assertEqual(vpn.classify_output('Failed to retrieve SAML cookie from HTTP')[0], 'error')
    def test_status_never_reuses_stale_login_snapshot(self):
        self.c.snapshot = {'browserUrl': 'old-url', 'attemptId': 'old-attempt'}
        self.c.browser_url = ''
        self.c.attempt_id = 'new-attempt'
        result = self.c.handle({'action': 'status'})
        self.assertEqual(result['browserUrl'], '')
        self.assertEqual(result['attemptId'], 'new-attempt')
    def test_reconnect_gets_unique_attempt(self):
        with patch.object(vpn, 'profile', return_value={'host': 'vpn.example.org'}), \
             patch.object(vpn, 'processes', return_value=[]), \
             patch.object(vpn, 'ip_json', return_value=[]), \
             patch.object(vpn.subprocess, 'Popen') as launch, \
             patch.object(vpn.threading, 'Thread'):
            self.c.connect()
            first = self.c.attempt_id
            launch.return_value.poll.return_value = 0
            self.c.connect()
            self.assertTrue(first)
            self.assertNotEqual(first, self.c.attempt_id)
    def test_token_never_in_process_arguments(self):
        self.c.state='token'; self.c.proc=Mock(); self.c.proc.poll.return_value=None
        self.c.token('654321')
        self.c.proc.stdin.write.assert_called_once_with(b'654321\n')
        self.assertEqual(self.c.state, 'connecting')
    def test_stale_or_multiline_token_rejected(self):
        for value in ['654321', '654321\ncommand', None]:
            with self.assertRaises(ValueError): self.c.token(value)
    @patch.object(vpn, 'processes', return_value=[123])
    @patch.object(vpn.subprocess, 'Popen')
    def test_duplicate_connection_not_started(self, popen, processes):
        self.c.connect(); popen.assert_not_called()
    @patch.object(vpn, 'ip_json', return_value=[])
    @patch.object(vpn, 'processes', return_value=[])
    def test_dropped_connection_is_reported(self, *args):
        self.c.state='connected'; self.c.refresh()
        self.assertEqual(self.c.state,'error')
        self.assertEqual(self.c.snapshot['rxRate'],0)
    @patch.object(vpn, 'ip_json', return_value=[])
    @patch.object(vpn, 'processes', return_value=[])
    def test_disconnect_finishes(self, *args):
        self.c.disconnect(); self.c.refresh()
        self.assertEqual(self.c.state,'disconnected')
    def test_unknown_action_rejected(self):
        with self.assertRaises(ValueError): self.c.handle({'action':'shell','command':'true'})
    @patch.object(vpn, 'processes', return_value=[123])
    def test_unrelated_ppp_interface_not_claimed(self, *args):
        iface={'ifname':'ppp1','link_type':'ppp','flags':['UP'],'addr_info':[{'family':'inet','address':'198.51.100.1','local':'198.51.100.2'}]}
        with patch.object(vpn,'ip_json',return_value=[iface]): self.c.refresh()
        self.assertNotEqual(self.c.state,'connected')

class ProfileTests(unittest.TestCase):
    def test_only_fixed_profile_commands_are_managed(self):
        for args in [[], ['-c', vpn.CONFIG], ['--config', vpn.CONFIG], ['--config='+vpn.CONFIG], ['-v', '-c', vpn.CONFIG, '--pppd-ifname=fortivpn0']]:
            self.assertTrue(vpn.matches_profile(args), args)
        for args in [['-c', '/etc/other-profile'], ['--config=/etc/other-profile'], ['--config', '/etc/other-profile'], ['-c'], ['192.0.2.55'], ['--password=example'], ['--pppd-call=other']]:
            self.assertFalse(vpn.matches_profile(args), args)

    def test_profile_never_exports_saved_secrets(self):
        metadata=Mock(st_uid=0, st_mode=0o100600)
        body='host = vpn.example.org\nport = 443\nusername = example\npassword = example-only\ntrusted-cert = example-only\n'
        with patch.object(pathlib.Path, 'stat', return_value=metadata), patch.object(pathlib.Path, 'read_text', return_value=body):
            self.assertEqual(vpn.profile(), {'host':'vpn.example.org','port':'443'})

    def test_world_readable_or_user_owned_profiles_rejected(self):
        for uid, mode in [(0,0o100644),(1,0o100600),(0,0o040600)]:
            with patch.object(pathlib.Path,'stat',return_value=Mock(st_uid=uid,st_mode=mode)), patch.object(pathlib.Path,'read_text') as read:
                self.assertEqual(vpn.profile(), {})
                read.assert_not_called()

    def test_missing_profile_is_handled(self):
        with patch.object(pathlib.Path,'stat',side_effect=FileNotFoundError):
            self.assertEqual(vpn.profile(), {})

    def test_missing_profile_cannot_start_vpn(self):
        with patch.object(vpn,'profile',return_value={}), patch.object(vpn,'processes',return_value=[]), patch.object(vpn.subprocess,'Popen') as launch:
            controller=vpn.Controller()
            controller.connect()
            launch.assert_not_called()
            self.assertEqual(controller.state,'error')

    def test_interface_collision_cannot_start_vpn(self):
        with patch.object(vpn,'profile',return_value={'host':'vpn.example.org'}), patch.object(vpn,'processes',return_value=[]), patch.object(vpn,'ip_json',return_value=[{'ifname':'fortivpn0'}]), patch.object(vpn.subprocess,'Popen') as launch:
            controller=vpn.Controller()
            controller.connect()
            launch.assert_not_called()
            self.assertIn('already in use',controller.message)

class SocketTests(unittest.TestCase):
    def test_service_starts_and_status_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            def child():
                vpn.SOCKET = tmp + '/control.sock'
                vpn.ALLOWED_UID = os.getuid()
                with patch.object(vpn, 'profile', return_value={'host': 'test.invalid'}):
                    vpn.serve()
            proc = multiprocessing.get_context('fork').Process(target=child)
            proc.start()
            try:
                for _ in range(100):
                    if pathlib.Path(tmp + '/control.sock').exists(): break
                    if not proc.is_alive(): self.fail('Controller exited during startup')
                    time.sleep(0.02)
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(2)
                    client.connect(tmp + '/control.sock')
                    client.sendall(b'{"action":"status"}\n')
                    response=json.loads(client.makefile('rb').readline())
                    self.assertIn('state', response)
                    self.assertEqual(response['gateway'], 'test.invalid')
                    client.sendall(b'{"action":"unknown"}\n')
                    self.assertIn('error', json.loads(client.makefile('rb').readline()))
            finally:
                proc.terminate(); proc.join(3)

if __name__=='__main__': unittest.main()
