import ast
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

import test_v25 as baseline


class ReleaseTests(baseline.Fixture, unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location('audit_common', baseline.SOURCES/'lib/vpn-optimizer/common.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def config(self, module=None):
        module = module or self.module()
        real_fstat = os.fstat
        def root_stat(fd):
            original = real_fstat(fd)
            return types.SimpleNamespace(st_uid=0, st_mode=original.st_mode)
        with patch.object(module.os, 'fstat', side_effect=root_stat):
            return module.load_config(self.conf)

    def set_config(self, **values):
        rows=dict(line.split('=',1) for line in self.conf.read_text().splitlines())
        rows.update(values)
        self.conf.write_text(''.join(k+'='+v+'\n' for k,v in rows.items()))

    def bot(self):
        namespace={'__name__':'isolated_bot'}
        exec(compile(self.bot_code(), '<isolated_bot>', 'exec'), namespace)
        return namespace

    def run_optimizer(self, mode='normal'):
        return subprocess.run([str(self.opt/'bin/vpn-optimize.sh'),mode],
                              env=self.env,capture_output=True,text=True,timeout=45)

    def clock(self, epoch):
        self.env['VPN_TEST_EPOCH']=str(epoch)
        path=self.mockdir/'date'
        path.write_text('''#!/usr/bin/env python3
import os, sys, time
time.tzset()
epoch=int(os.environ['VPN_TEST_EPOCH'])
print(epoch if sys.argv[1]=='+%s' else time.strftime(sys.argv[1][1:],time.localtime(epoch)))
''')
        path.chmod(0o755)

    def last(self, value):
        (self.cache.parent/'nightly-last-date').write_text(value+'\n')

    def test_config_rejects_commands_without_execution_or_leaks(self):
        original=self.conf.read_text()
        marker=self.base/'should-not-exist'
        for value in ('$(touch '+str(marker)+')', '`touch '+str(marker)+'`', 'x;touch '+str(marker), '"quoted"'):
            self.conf.write_text(original)
            self.set_config(SUB_ID=value)
            for script in ('vpn-optimize.sh','vpn-watchdog.sh','vpn-delay-scan.sh'):
                result=subprocess.run([str(self.opt/'bin'/script)],env=self.env,capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn(value,result.stdout+result.stderr)
            self.assertFalse(marker.exists())
        self.assertFalse(self.state().get('calls'))

    def test_config_requires_exact_mode_owner_regular_file_and_no_symlink(self):
        module=self.module()
        for mode in (0o400,0o644,0o700,0o1600):
            self.conf.chmod(mode)
            with self.assertRaisesRegex(ValueError,'chmod 600'):
                self.config(module)
        self.conf.chmod(0o600)
        with patch.object(module.os,'fstat',return_value=types.SimpleNamespace(st_uid=1234,st_mode=0o100600)):
            with self.assertRaisesRegex(ValueError,'owned by root'):
                module.load_config(self.conf)
        target=self.base/'target';self.conf.rename(target);self.conf.symlink_to(target)
        with self.assertRaises(ValueError):self.config(module)
        self.conf.unlink();os.mkfifo(self.conf,0o600)
        with self.assertRaisesRegex(ValueError,'regular file'):self.config(module)

    def test_config_rejects_duplicates_missing_empty_unknown_and_bad_formats(self):
        original=self.conf.read_text()
        cases=[original+'SUB_ID=duplicate\n', original+'UNKNOWN_KEY=abc\n',
               original.replace('SUB_ID=testing\n',''),original.replace('SUB_ID=testing','SUB_ID='),
               original.replace('SUB_ID=testing','SUB_ID=REPLACE_VALUE')]
        for content in cases:
            self.conf.write_text(content)
            with self.assertRaises(ValueError):self.config()
        for key,value in [('AWGM_URL','http://user:private@localhost:2222'),
                          ('PROXY_URL','http://localhost:123/path'),('CLASH_URL','http://localhost:99999'),
                          ('SELECTOR','tag with spaces'),('TG_ALLOWED_IDS','0'),('TG_ALLOWED_IDS','1,,2'),
                          ('TG_BOT_TOKEN','not-a-token'),('NIGHT_HOUR','24'),('NIGHT_HOUR','3'),
                          ('NIGHT_TZ','Europe/Unknown'),('NIGHT_TZ',''),('NIGHT_TZ','UTC-3;echo')]:
            self.conf.write_text(original);self.set_config(**{key:value})
            with self.assertRaises(ValueError):self.config()

    def test_config_time_defaults_partial_and_explicit(self):
        self.assertEqual((self.config()['NIGHT_HOUR'],self.config()['NIGHT_TZ']),('03','MSK-3'))
        self.set_config(NIGHT_HOUR='07')
        self.assertEqual(self.config()['NIGHT_TZ'],'LOCAL')
        for zone in ('LOCAL','UTC0','MSK-3','UTC-3','UTC+5:30'):
            self.set_config(NIGHT_TZ=zone)
            self.assertEqual(self.config()['NIGHT_TZ'],zone)
        self.assertIn('NIGHT_HOUR=03\nNIGHT_TZ=LOCAL', (baseline.SOURCES/'etc/vpn-optimizer.conf.example').read_text())

    def test_shell_output_never_contains_token_or_allowed_ids(self):
        result=subprocess.run([sys.executable,str(self.lib/'common.py'),'--shell',str(self.conf)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn('TG_',result.stdout)
        self.assertNotIn('synthetic_test_token',result.stdout+result.stderr)
        self.assertNotIn('123,456',result.stdout+result.stderr)

    def test_night_legacy_gate_and_existing_date(self):
        # 2026-01-02 00:00 UTC = 03:00 Moscow.
        self.clock(1767312000)
        self.last('2026-01-02')
        result=self.run_optimizer()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(self.state().get('calls'))
        self.last('2026-01-01')
        self.clock(1767308400)  # 02:00 Moscow: not due.
        self.assertEqual(self.run_optimizer().returncode,0)
        self.assertFalse(self.state().get('calls'))

    def test_night_zone_change_and_calendar_rollback_block_repeat(self):
        self.clock(1767312000)
        self.set_config(NIGHT_HOUR='03',NIGHT_TZ='UTC-3')
        self.last('2026-01-01 1767308400 UTC0')
        self.assertEqual(self.run_optimizer().returncode,0)
        self.assertFalse(self.state().get('calls'))
        self.last('2026-01-03 1767308400 UTC-3')
        self.assertEqual(self.run_optimizer().returncode,0)
        self.assertFalse(self.state().get('calls'))

    def test_retry_delay_remains_600_seconds(self):
        self.clock(1767312000)
        (self.cache.parent/'nightly-retry').write_text(str(1767312000-599))
        self.assertEqual(self.run_optimizer().returncode,0)
        self.assertFalse(self.state().get('calls'))

    def test_night_success_once_next_day_and_metadata(self):
        self.clock(1767312000)
        self.set_config(NIGHT_HOUR='00',NIGHT_TZ='UTC0')
        result=self.run_optimizer()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(self.state()['mode'],'tproxy')
        self.assertEqual(self.state()['current'],'b')
        self.assertEqual((self.cache.parent/'nightly-last-date').read_text().split(),['2026-01-02','1767312000','UTC0'])
        self.assertIn('+0000', (self.cache.parent/'speed-cache.meta').read_text())
        calls=list(self.state()['calls'])
        self.assertEqual(self.run_optimizer().returncode,0)
        self.assertEqual(self.state()['calls'],calls)
        self.clock(1767398400)
        result=self.run_optimizer()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertGreater(len(self.state()['calls']),len(calls))
        for row in self.cache.read_text().splitlines():self.assertEqual(len(row.split('\t')),6)

    def test_explicit_and_local_hours_are_used(self):
        self.clock(1767312000)
        self.env['TZ']='UTC-7'
        self.set_config(NIGHT_HOUR='03',NIGHT_TZ='LOCAL')
        self.assertEqual(self.run_optimizer().returncode,0)
        self.assertFalse(self.state().get('calls'))
        self.set_config(NIGHT_HOUR='07')
        # A known date prevents the benchmark but confirms date/local-hour handling separately.
        self.last('2026-01-02')
        self.assertEqual(self.run_optimizer().returncode,0)
        self.last('2026-01-01')
        result=self.run_optimizer()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue(self.state().get('calls'))

    def test_failed_night_keeps_retry_and_does_not_mark_success(self):
        self.clock(1767312000)
        self.set_state(mode_off_fail=True)
        result=self.run_optimizer()
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.cache.parent/'nightly-last-date').exists())
        self.assertEqual((self.cache.parent/'nightly-retry').read_text().strip(),'1767312000')
        self.assertEqual(self.state()['mode'],'tproxy')

    def test_ruleset_rejects_unknown_structure_without_writes(self):
        bot=self.bot()
        valid={'tag':'test','type':'inline','rules':[{'domain_suffix':['example.org']}]}
        bad=[[],[valid,valid],[dict(valid,type='remote')],
             [dict(valid,rules=[{'ip_cidr':['127.0.0.1/32']}])],
             [dict(valid,rules=[{'domain_suffix':['example.org'],'network':'tcp'}])],
             [dict(valid,rules=[{'domain_suffix':'example.org'}])],
             [dict(valid,rules=[{'domain_suffix':[123]}])], {'rules':[]}]
        for response in bad:
            calls=[]
            def api(path,payload=None):
                calls.append((path,payload));return response
            bot['awg_request']=api
            with self.assertRaises(ValueError):bot['set_domains'](['example.net'])
            self.assertEqual([path for path,_ in calls],['/rulesets/list'])
        self.assertEqual(self.module().validate_ruleset([dict(valid,rules=[])],'test'),[])
        self.assertEqual(self.module().validate_ruleset([dict(valid,materialized_srs={'generated':True},future_metadata=42)],'test'),['example.org'])

    def test_ruleset_add_delete_and_changed_snapshot(self):
        bot=self.bot();calls=[]
        current={'tag':'test','type':'inline','rules':[{'domain_suffix':['example.org']}]}
        def api(path,payload=None):
            calls.append((path,payload))
            if payload is None:return [copy.deepcopy(current),{'tag':'other','rules':[{'ip_cidr':['192.0.2.0/24']}]}]
            current.update(payload['ruleSet'])
        bot['awg_request']=api
        bot['set_domains'](['example.org','example.net'],expected=['example.org'])
        self.assertEqual(calls[-1][1]['tag'],'test')
        self.assertEqual(bot['get_domains'](),['example.net','example.org'])
        bot['set_domains']([],expected=['example.net','example.org'])
        self.assertEqual(current['rules'],[])
        calls.clear()
        with self.assertRaises(ValueError):bot['set_domains'](['example.com'],expected=['stale.example'])
        self.assertFalse(any(payload is not None for _,payload in calls))

    def test_telegram_auth_and_dynamic_help(self):
        self.set_config(NIGHT_HOUR='07',NIGHT_TZ='LOCAL')
        bot=self.bot();sent=[]
        bot['send_message']=lambda *args,**kwargs:sent.append(args)
        for sender,kind in ((999,'private'),(123,'group')):
            bot['process_message']({'from':{'id':sender},'chat':{'id':123,'type':kind},'text':'/help'})
        self.assertEqual(sent,[])
        bot['process_message']({'from':{'id':123},'chat':{'id':123,'type':'private'},'text':'/help'})
        self.assertIn('07:00 (LOCAL)',sent[-1][1])

    def test_nightly_report_retries_transient_telegram_failure(self):
        bot=self.bot();attempts=[];delays=[]
        bot['speedcache_report_html']=lambda title:'Synthetic report'
        def sender(chat_id,report,parse_mode=None):
            attempts.append((chat_id,parse_mode))
            if chat_id==123 and len([x for x in attempts if x[0]==123])<3:
                raise RuntimeError('temporary network failure')
        bot['send_message']=sender
        with patch.object(bot['time'],'sleep',side_effect=lambda seconds:delays.append(seconds)):
            self.assertTrue(bot['notify_nightly_report']())
        self.assertEqual([x[0] for x in attempts],[123,123,123,456])
        self.assertTrue(all(mode=='HTML' for _,mode in attempts))
        self.assertEqual(delays,[5,5])

    def test_nightly_report_stops_after_three_failures_without_leaking_data(self):
        bot=self.bot();calls=[];delays=[]
        bot['speedcache_report_html']=lambda title:'Synthetic report'
        def sender(chat_id,*args,**kwargs):
            calls.append(chat_id)
            raise RuntimeError('SECRET_NETWORK_RESPONSE')
        bot['send_message']=sender
        output=io.StringIO()
        with patch.object(bot['time'],'sleep',side_effect=lambda seconds:delays.append(seconds)),contextlib.redirect_stdout(output):
            self.assertFalse(bot['notify_nightly_report']())
        self.assertEqual(calls,[123,123,123,456,456,456])
        self.assertEqual(delays,[5,5,5,5])
        self.assertEqual(output.getvalue().count('NOTIFY ERROR'),2)
        self.assertNotIn('SECRET_NETWORK_RESPONSE',output.getvalue())

    def test_api_and_notification_errors_hide_response_url_and_user_id(self):
        bot=self.bot();secret='PRIVATE_RESPONSE_SENTINEL'
        with patch.object(bot['urllib'].request,'urlopen',side_effect=RuntimeError(secret)):
            for name in ('awg_request','awgm_request'):
                with self.assertRaises(RuntimeError) as caught:bot[name]('/unused')
                self.assertNotIn(secret,str(caught.exception))
        bot['send_message']=lambda *a,**kw:(_ for _ in ()).throw(RuntimeError(secret))
        output=io.StringIO()
        with contextlib.redirect_stdout(output):bot['notify_allowed_users']('test')
        self.assertNotIn(secret,output.getvalue())
        self.assertNotIn('chat_id',output.getvalue())
        self.assertNotIn('123',output.getvalue())

    def test_mktemp_failure_releases_lock_without_network(self):
        path=self.mockdir/'mktemp';path.write_text('#!/bin/sh\nexit 1\n');path.chmod(0o755)
        for name in ('vpn-watchdog.sh','vpn-optimize.sh','vpn-delay-scan.sh'):
            result=subprocess.run([str(self.opt/'bin'/name),'--force'],env=self.env,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
        self.assertFalse(list(self.vartmp.iterdir()))
        self.assertFalse(self.state().get('calls'))

    def test_preflight_selector_validation(self):
        namespace=runpy.run_path(str(baseline.SOURCES/'tools/preflight.py'),run_name='preflight_test')
        validate=namespace['check_selector']
        validate({'now':'a','all':['a']},[{'id':'testing','memberTags':['a','b']}],'testing')
        for selected in ({'now':'a','all':['wrong']},{'now':'a','all':['a bad']},{'now':'missing','all':['a']}):
            with self.assertRaises(ValueError):validate(selected,[{'id':'testing','memberTags':['a']}],'testing')


if __name__=='__main__':
    unittest.main()
