import os
from pathlib import Path
import re
import subprocess
import unittest
from unittest.mock import patch
import runpy
import io
import contextlib
import types

import test_v25 as baseline


class InstallTests(baseline.Fixture, unittest.TestCase):
    def test_optimizer_exports_entware_path_for_cron(self):
        script=(baseline.SOURCES/'bin/vpn-optimize.sh').read_text()
        line='export PATH="${PATH:+$PATH:}/opt/bin:/opt/sbin:/usr/sbin:/usr/bin:/sbin:/bin"'
        self.assertEqual(script.splitlines()[1],line)
        probe='\n'.join(script.splitlines()[:2])+'\nprintf "%s\\n" "$PATH"\n'
        result=subprocess.run(['/bin/sh','-c',probe],env={'PATH':'/usr/bin:/bin'},
                              capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),
                         '/usr/bin:/bin:/opt/bin:/opt/sbin:/usr/sbin:/usr/bin:/sbin:/bin')

    def block(self, marker):
        blocks=re.findall(r'```sh\n(.*?)\n```', (baseline.SOURCES/'INSTALL_RU.md').read_text(), re.S)
        matches=[block for block in blocks if marker in block]
        self.assertEqual(len(matches),1,marker)
        return matches[0]

    def shell(self, block, **values):
        # Execute the documented commands with /opt rooted in this test directory.
        block=block.replace('/opt/',str(self.opt)+'/').replace('"/$ITEM"','"$TEST_ROOT/$ITEM"')
        block=block.replace('-C / ', '-C "$TEST_ROOT" ').replace('cd / || exit 1', 'cd "$TEST_ROOT" || exit 1')
        env=os.environ.copy()
        env.update(SOURCE=str(baseline.SOURCES),TEST_ROOT=str(self.base),**values)
        result=subprocess.run(['/bin/sh','-ec',block],env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        return result

    def test_documented_copy_repeat_backup_restore_and_cron(self):
        (self.opt/'etc/init.d').mkdir()
        for relative in ('var/spool/cron/crontabs','var/backups'):(self.opt/relative).mkdir(parents=True,exist_ok=True)
        cron=self.opt/'var/spool/cron/crontabs/root'
        foreign='15 4 * * * /opt/bin/unrelated-job\n# keep this comment\n'
        cron.write_text(foreign)
        original_config=self.conf.read_bytes();original_cache=self.cache.read_bytes()
        copy_block=self.block('cp "$SOURCE/lib/vpn-optimizer/common.py"')
        self.shell(copy_block);self.shell(copy_block)
        self.assertEqual(self.conf.read_bytes(),original_config)
        self.assertEqual(self.cache.read_bytes(),original_cache)
        self.assertEqual((self.opt/'etc/init.d/S99vpn-watchdog').stat().st_mode & 0o777,0o755)
        cron_block=self.block("if grep -Fq '/opt/bin/vpn-optimize.sh'")
        self.shell(cron_block);self.shell(cron_block)
        self.assertTrue(cron.read_text().startswith(foreign))
        self.assertEqual(cron.read_text().count('# vpn-optimizer'),1)
        backup=self.shell(self.block('BACKUP_DIR=$(mktemp -d'))
        folders=list((self.opt/'var/backups').glob('vpn-optimizer.*'))
        self.assertEqual(len(folders),1)
        saved=folders[0]
        self.assertEqual(saved.stat().st_mode & 0o777,0o700)
        executable=self.opt/'bin/vpn-optimize.sh';original_code=executable.read_bytes()
        executable.write_text('# changed script\n')
        self.conf.write_text('damaged config\n');self.cache.write_text('changed cache\n')
        self.shell('tar -xzf "$BACKUP_DIR/files.tar.gz" -C / ',BACKUP_DIR=str(saved))
        self.assertEqual(executable.read_bytes(),original_code)
        self.assertEqual(self.conf.read_bytes(),original_config)
        self.assertEqual(self.cache.read_bytes(),original_cache)
        self.shell(self.block('CRON_SAVED=$(mktemp'))
        self.assertTrue(cron.read_text().startswith(foreign))
        self.assertNotIn('# vpn-optimizer',cron.read_text())
        self.shell(cron_block)
        self.assertEqual(cron.read_text().count('# vpn-optimizer'),1)

    def test_initial_config_creation_does_not_overwrite(self):
        block=self.block('set -C; cat "$SOURCE/etc/vpn-optimizer.conf.example"')
        before=self.conf.read_bytes()
        self.shell(block)
        self.assertEqual(self.conf.read_bytes(),before)
        self.conf.unlink()
        self.shell(block)
        self.assertEqual(self.conf.stat().st_mode & 0o777,0o600)
        self.assertIn('REPLACE_',self.conf.read_text())

    def test_preflight_missing_components_and_malformed_api_are_safe(self):
        namespace=runpy.run_path(str(baseline.SOURCES/'tools/preflight.py'),run_name='preflight_test')
        config=dict(line.split('=',1) for line in self.conf.read_text().splitlines())
        config.update(NIGHT_HOUR='03',NIGHT_TZ='UTC0')
        calls=[]
        def api(*args,**kwargs):
            calls.append(args)
            raise RuntimeError('PRIVATE_API_SENTINEL')
        namespace['main'].__globals__['load_config']=lambda path:config
        namespace['main'].__globals__['api_data']=api
        fake_result=subprocess.CompletedProcess([],1,stdout=b'',stderr=b'PRIVATE_TRANSPORT_SENTINEL')
        output=io.StringIO()
        with patch('shutil.which',return_value=None), patch('subprocess.run',return_value=fake_result), contextlib.redirect_stdout(output):
            result=namespace['main'](['--config',str(self.conf)])
        self.assertEqual(result,1)
        self.assertIn('ERROR:',output.getvalue())
        self.assertIn('WARNING:',output.getvalue())
        self.assertNotIn('PRIVATE_',output.getvalue())
        self.assertTrue(all('/subscriptions' in args[1] or '/rulesets/list' in args[1] for args in calls))
        self.assertFalse(self.state().get('calls'))

    def test_init_start_repeat_check_stop_with_dummy_services(self):
        for name in ('S99vpn-watchdog','S99telegram-vpn-bot'):
            init=(baseline.SOURCES/'etc/init.d'/name).read_text().replace('/opt/',str(self.opt)+'/')
            init=init.replace(str(self.opt)+'/bin/python3',os.sys.executable)
            executable=self.base/name;executable.write_text(init);executable.chmod(0o755)
            bot=self.opt/'etc/telegram-vpn-bot/bot.py';bot.parent.mkdir(exist_ok=True)
            bot.write_text('import time\nwhile True: time.sleep(1)\n')
            loop=self.opt/'bin/vpn-watchdog-loop.sh'
            loop.write_text('#!/bin/sh\nwhile true; do sleep 1; done\n');loop.chmod(0o755)
            env=os.environ.copy()
            def invoke(action):
                return subprocess.run([str(executable),action],env=env,capture_output=True,text=True,timeout=10)
            try:
                self.assertEqual(invoke('start').returncode,0)
                self.assertEqual(invoke('check').returncode,0)
                again=invoke('start')
                self.assertEqual(again.returncode,0)
                self.assertIn('already running',again.stdout)
            finally:
                invoke('stop')
            self.assertNotEqual(invoke('check').returncode,0)

    def test_preflight_success_is_read_only_and_checks_timezone(self):
        namespace=runpy.run_path(str(baseline.SOURCES/'tools/preflight.py'),run_name='preflight_test')
        namespace=namespace['main'].__globals__
        config=dict(line.split('=',1) for line in self.conf.read_text().splitlines())
        config.update(NIGHT_HOUR='03',NIGHT_TZ='MSK-3')
        namespace['load_config']=lambda path:config
        endpoints=[]
        def data(opener,url):
            endpoints.append(url)
            if url.endswith('/subscriptions'):return [{'id':'testing','memberTags':['a','b']}]
            if url.endswith('/settings'):return {'enabled':True,'routingMode':'tproxy'}
            if url.endswith('/rulesets/list'):return [{'tag':'test','type':'inline','rules':[]}]
            raise AssertionError('Unexpected API request')
        namespace['api_data']=data
        opener=types.SimpleNamespace(open=lambda *args,**kw:contextlib.closing(io.BytesIO(b'{"now":"a","all":["a","b"]}')))
        offset='+0300'
        def command(args,**kwargs):
            if args[0]=='date':return subprocess.CompletedProcess(args,0,stdout='2026-01-02 03 '+offset+'\n')
            if args[0]=='/bin/sh':return subprocess.CompletedProcess(args,0,stdout=b'a\nb\n')
            if args[0]=='curl':return subprocess.CompletedProcess(args,0,stdout=b'gateway.discord.gg')
            if args[0]=='pidof':return subprocess.CompletedProcess(args,0)
            raise AssertionError('Unexpected process')
        with patch('urllib.request.build_opener',return_value=opener), patch('shutil.which',return_value='/mock/tool'), \
                patch('os.geteuid',return_value=0), patch.object(Path,'is_file',return_value=True), \
                patch.object(Path,'is_dir',return_value=True), patch.object(Path,'read_text',return_value=''), \
                patch.object(Path,'stat',return_value=types.SimpleNamespace(st_uid=0,st_mode=0o40755)), \
                patch('subprocess.run',side_effect=command):
            output=io.StringIO()
            with contextlib.redirect_stdout(output):result=namespace['main']([])
            self.assertEqual(result,0,output.getvalue())
            self.assertNotIn('ERROR:',output.getvalue())
            offset='+0000'
            with contextlib.redirect_stdout(io.StringIO()):result=namespace['main']([])
            self.assertEqual(result,1)
        self.assertTrue(all(url.endswith(('/subscriptions','/settings','/rulesets/list')) for url in endpoints))


if __name__=='__main__':unittest.main()
