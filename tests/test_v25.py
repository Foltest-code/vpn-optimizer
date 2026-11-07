#!/usr/bin/env python3
"""Isolated integration tests: mocks local AWGM/Clash/HTTPS, never connects to a router."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

SOURCES = Path(__file__).resolve().parents[1]

MOCK_CURL = r'''#!/usr/bin/env python3
import fcntl, json, os, pathlib, sys
p=pathlib.Path(os.environ['VPN_OPTIMIZER_MOCK_STATE'])
lock=open(str(p)+'.lock','w')
fcntl.flock(lock, fcntl.LOCK_EX)
s=json.loads(p.read_text())
a=sys.argv[1:]
def arg(flag, default=''):
    try:return a[a.index(flag)+1]
    except (ValueError,IndexError):return default
out=arg('-o')
url=next((x for x in reversed(a) if x.startswith('http://') or x.startswith('https://')), '')
s.setdefault('calls',[]).append(url)
code='200'; body=''; result=None
if '/proxies/sub-test' in url:
    body=json.dumps({'now':s.get('current','a'), 'all':s.get('members',['a','b','c','d'])})
elif '/proxies/' in url:
    tag=url.split('/proxies/')[-1]; body=json.dumps({'type': s.get('types',{}).get(tag,'Vless')})
elif '/subscriptions/active-member?' in url:
    data=json.loads(arg('-d','{}')); target=data.get('memberTag')
    if target in s.get('select_fail',[]):
        code='500'
        if target in s.get('select_apply_fail',[]):s['current']=target
    else:s['current']=target
elif '/subscriptions/refresh?' in url:
    code='200'
elif '/router/settings' in url:
    mode=s.get('mode','tproxy')
    body=json.dumps({'enabled': mode!='off', 'routingMode': mode})
elif '/router/mode' in url:
    data=json.loads(arg('-d','{}')); target=data.get('mode')
    if target=='off' and s.get('mode_off_fail',False):
        s['mode']='off'; code='500'
    elif target in s.get('mode_restore_fail',[]):code='500'
    else:s['mode']=target
elif 'discord.com/api/v10/gateway' in url:
    if s.get('current','a') in s.get('https_fail',[]):code='503'
    else:body='{"url":"wss://gateway.discord.gg"}'
elif 'bytes=65536' in url:
    result='200 65536'
elif 'bytes=5242880' in url:
    speed=float(s.get('speeds',{}).get(s.get('current','a'),10))
    result=f'200 5242880 {speed*1000000/8:.1f}'
elif '/delay-check?' in url:
    body='{"delay":100}'
else:
    code='404'
if out and out != '/dev/null':
    pathlib.Path(out).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(out).write_text(body)
s.setdefault('counts',{})[url]=s.setdefault('counts',{}).get(url,0)+1
p.write_text(json.dumps(s))
if result is not None: print(result,end='')
elif arg('-w') == '%{http_code}': print(code,end='')
elif arg('-w'): print('200 65536',end='')
elif not out:print(body,end='')
'''

class Fixture:
    # Router root ownership is mapped only inside the disposable test copy.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='vpn-v25-fixture-')
        self.base=Path(self.tmp.name)
        self.opt=self.base/'opt'; self.vartmp=self.base/'temp'
        (self.opt/'etc').mkdir(parents=True)
        (self.opt/'bin').mkdir(parents=True)
        (self.opt/'var'/'lib'/'vpn-optimizer').mkdir(parents=True)
        (self.opt/'var'/'run').mkdir(parents=True)
        self.vartmp.mkdir()
        self.lib=self.base/'lib'; self.lib.mkdir()
        helper=(SOURCES/'lib/vpn-optimizer/common.py').read_text()
        helper=helper.replace('info.st_uid != 0', 'info.st_uid != os.geteuid()')
        (self.lib/'common.py').write_text(helper)
        self.original_sys_path=list(sys.path)
        self.original_common=sys.modules.pop('common', None)
        self.conf=self.opt/'etc'/'vpn-optimizer.conf'
        self.conf.write_text('SUB_ID=testing\nSELECTOR=sub-test\nRULESET_TAG=test\nAWGM_URL=http://127.0.0.1:2222\nCLASH_URL=http://127.0.0.1:9099\nPROXY_URL=http://127.0.0.1:11000\nTG_BOT_TOKEN=123:synthetic_test_token\nTG_ALLOWED_IDS=123,456\n')
        self.conf.chmod(0o600)
        self.mockdir=self.base/'mockbin'; self.mockdir.mkdir()
        curl=self.mockdir/'curl'; curl.write_text(MOCK_CURL); curl.chmod(0o755)
        sleep=self.mockdir/'sleep'; sleep.write_text('#!/bin/sh\nexit 0\n'); sleep.chmod(0o755)
        self.env=os.environ.copy()
        self.env['PYTHONDONTWRITEBYTECODE']='1'
        self.statefile=self.base/'state.json'; self.env['VPN_OPTIMIZER_MOCK_STATE']=str(self.statefile)
        self.env['PATH']=str(self.mockdir)+':'+os.environ['PATH']
        self.set_state(current='a', members=['a','b','c','d'], speeds={'a':4,'b':32,'c':12,'d':3}, https_fail=[])
        for name in ('vpn-watchdog.sh','vpn-optimize.sh','vpn-delay-scan.sh'):
            text=(SOURCES/'bin'/name).read_text()
            # Replace /tmp markers before inserting any temp path with that prefix.
            text=text.replace('/tmp/vpn-',str(self.vartmp/'vpn-'))
            text=text.replace('/opt/etc/vpn-optimizer.conf', str(self.conf))
            text=text.replace('/opt/etc/telegram-vpn-bot/bot.py', str(self.base/'nonexistent_bot'))
            text=text.replace('/opt/bin/vpn-delay-scan.sh', str(self.opt/'bin'/'vpn-delay-scan.sh'))
            text=text.replace('/opt/var/',str(self.opt/'var')+'/')
            text=text.replace('/opt/bin/python3',sys.executable).replace('/opt/lib/vpn-optimizer',str(self.lib))
            p=self.opt/'bin'/name; p.write_text(text); p.chmod(0o755)
        self.cache=self.opt/'var'/'lib'/'vpn-optimizer'/'speed-cache.tsv'
        self.fresh_cache()
        (self.opt/'var'/'run'/'vpn-delay.cache').write_text('a 100 1\nb 50 1\nc 30 1\nd 10 1\n')
    def tearDown(self):
        sys.path[:]=self.original_sys_path
        sys.modules.pop('common', None)
        if self.original_common is not None:
            sys.modules['common']=self.original_common
        self.tmp.cleanup()
    def bot_code(self):
        return (SOURCES/'etc/telegram-vpn-bot/bot.py').read_text().replace(
            '/opt/etc/vpn-optimizer.conf',str(self.conf)).replace('/opt/lib/vpn-optimizer',str(self.lib))
    def set_state(self, **kwargs):
        value = json.loads(self.statefile.read_text()) if self.statefile.exists() else {}
        value.update(kwargs)
        self.statefile.write_text(json.dumps(value))
    def state(self): return json.loads(self.statefile.read_text())
    def fresh_cache(self, stale_b=False):
        ts=int(time.time()); old=ts-200000 if stale_b else ts
        self.cache.write_text(f'a\t100\t40.0\tGOOD\t{ts}\t1\n'
                              f'b\t50\t32.0\tGOOD\t{old}\t0\n'
                              f'c\t30\t12.0\tRESERVE\t{ts}\t0\n'
                              f'd\t10\t3.0\tSLOW\t{ts}\t0\n')
    def run_wd(self, mode, timeout=40):
        return subprocess.run([str(self.opt/'bin'/'vpn-watchdog.sh'),mode],
              capture_output=True,text=True,env=self.env,timeout=timeout)
    def assert_no_speed(self):
        self.assertFalse(any('bytes=5242880' in x for x in self.state().get('calls',[])))
        self.assertFalse(any('bytes=65536' in x for x in self.state().get('calls',[])))
        self.assertFalse(any('refresh?' in x or 'delay-check?' in x for x in self.state().get('calls',[])))

class V25Tests(Fixture, unittest.TestCase):
    def test_switch_success_good_delay_and_cache(self):
        before=(self.opt/'var'/'run'/'vpn-delay.cache').read_bytes()
        result=self.run_wd('--switch-cached')
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        self.assertIn('SWITCH_CACHED_OK|a|b|32.0|GOOD',result.stdout)
        self.assertEqual(self.state()['current'],'b')
        rows=self.cache.read_text().splitlines()
        self.assertEqual(len(rows),3)
        self.assertFalse(any(r.startswith('a\t') for r in rows))
        self.assertTrue(any(r.startswith('b\t') and r.endswith('\t1') for r in rows))
        self.assertEqual((self.opt/'var'/'run'/'vpn-delay.cache').read_bytes(),before)
        self.assert_no_speed()

    def test_fallback_second_alternative(self):
        self.set_state(https_fail=['b'])
        result=self.run_wd('--switch-cached')
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        self.assertIn('|a|c|12.0|RESERVE',result.stdout)
        self.assertEqual(self.state()['current'],'c')
        self.assert_no_speed()

    def test_switch_no_candidates_cache_unchanged(self):
        self.set_state(members=['a'])
        before=self.cache.read_bytes()
        result=self.run_wd('--switch-cached')
        self.assertEqual(result.returncode,2)
        self.assertEqual(self.state()['current'],'a')
        self.assertEqual(self.cache.read_bytes(),before)
        self.assert_no_speed()

    def test_rollback_if_all_https_fail(self):
        self.set_state(https_fail=['b','c','d'])
        before=self.cache.read_bytes()
        result=self.run_wd('--switch-cached')
        self.assertEqual(result.returncode,2, result.stderr+result.stdout)
        self.assertIn('no-working-alternative',result.stdout)
        self.assertEqual(self.state()['current'],'a')
        self.assertEqual(self.cache.read_bytes(),before)
        self.assert_no_speed()

    def test_rollback_failure_reports_alert(self):
        self.set_state(https_fail=['b','c','d'],select_fail=['a'])
        before=self.cache.read_bytes()
        result=self.run_wd('--switch-cached')
        self.assertEqual(result.returncode,3, result.stdout+result.stderr)
        self.assertIn('rollback-failed',result.stdout)
        self.assertEqual(self.cache.read_bytes(),before)

    def test_switch_api_error_applied_then_rollback(self):
        self.set_state(select_fail=['b','c','d'], select_apply_fail=['b'])
        before=self.cache.read_bytes()
        result=self.run_wd('--switch-cached')
        self.assertEqual(result.returncode,2,result.stdout+result.stderr)
        self.assertEqual(self.state()['current'],'a')
        self.assertEqual(self.cache.read_bytes(),before)
        self.assert_no_speed()

    def test_expired_entries_not_used(self):
        self.fresh_cache(stale_b=True)
        result=self.run_wd('--switch-cached')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('|a|c|',result.stdout)
        self.assert_no_speed()

    def test_whole_cache_expired(self):
        old=int(time.time())-200000
        self.cache.write_text(f'a\t100\t40\tGOOD\t{old}\t1\nb\t40\t20\tGOOD\t{old}\t0\n')
        result=self.run_wd('--switch-cached')
        self.assertEqual(result.returncode,2)
        self.assertIn('missing-or-expired-cache',result.stdout)
        self.assertEqual(self.state()['current'],'a')
        self.assert_no_speed()

    def test_excluded_server_ignored(self):
        bad=self.opt/'var'/'run'/'vpn-watchdog.bad'
        bad.write_text(f'b {int(time.time())}\n')
        result=self.run_wd('--switch-cached')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('|a|c|',result.stdout)
        self.assert_no_speed()

    def test_trojan_not_selected(self):
        self.set_state(types={'b':'Trojan'})
        result=self.run_wd('--switch-cached')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('|a|c|',result.stdout)
        self.assert_no_speed()

    def test_two_low_speeds_triggers_existing_failover(self):
        monitor=self.opt/'var'/'run'/'vpn-speed-monitor.last'
        monitor.write_text(f'a {int(time.time())-3700}\n')
        result=self.run_wd('normal',timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(self.state()['current'],'b')
        events=self.state().get('calls',[])
        self.assertGreaterEqual(sum('bytes=5242880' in x for x in events),3)
        log=(self.opt/'var'/'log'/'vpn-watchdog.log').read_text()
        self.assertIn('confirmed low speed',log)
        self.assertIn('fresh-speed',log)

    def test_normal_check_skips_periodic_deep(self):
        result=self.run_wd('normal')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assert_no_speed()
        self.assertTrue(any('discord.com' in x for x in self.state()['calls']))

    def test_routing_restored_after_off_http_failure(self):
        # Use --recheck to reach routing-mode call without refresh or delay scanning.
        self.set_state(mode='tproxy', mode_off_fail=True)
        before=self.cache.read_bytes()
        result=subprocess.run([str(self.opt/'bin'/'vpn-optimize.sh'),'--recheck'],
             capture_output=True,text=True,env=self.env,timeout=30)
        self.assertEqual(result.returncode,1,result.stdout+result.stderr)
        self.assertEqual(self.state()['mode'],'tproxy')
        self.assertEqual(self.cache.read_bytes(),before)
        self.assertTrue(any('/router/mode' in x for x in self.state()['calls']))

    def test_telegram_switch_success_and_failure_messages(self):
        code=self.bot_code()
        code=code.replace('if __name__ == "__main__":\n    cli_main(sys.argv)','')
        mod=self.base/'bot-test.py'; mod.write_text(code)
        import runpy
        ns=runpy.run_path(str(mod))
        handler=ns['handle_switch_cached']
        sent=[]
        handler.__globals__['send_message']=lambda chat, message, **kwargs: sent.append(message)
        handler.__globals__['describe_tag']=lambda tag: tag
        handler.__globals__['run_switch_cached']=lambda: (0,'SWITCH_CACHED_OK|a|b|32.0|GOOD')
        handler(123)
        self.assertIn('Предыдущий сервер удалён', sent[-1])
        handler.__globals__['run_switch_cached']=lambda: (3,'SWITCH_CACHED_ERROR|rollback-failed')
        handler(123)
        self.assertIn('ручная проверка', sent[-1])

    def test_bot_config_rejects_weak_permissions(self):
        code=self.bot_code()
        code=code.replace('/opt/etc/telegram-vpn-bot',str(self.base/'telegram'))
        p=self.base/'bot-test.py';p.write_text(code)
        self.conf.chmod(0o644)
        proc=subprocess.run([sys.executable,str(p)],capture_output=True,text=True,timeout=10)
        self.assertNotEqual(proc.returncode,0)
        self.assertIn('chmod 600',proc.stderr)
        self.assertNotIn('123:synthetic_test_token',proc.stderr)

    def test_bot_config_parsed_without_legacy_files(self):
        code=self.bot_code()
        code=code.replace('/opt/etc/telegram-vpn-bot',str(self.base/'telegram'))
        code=code.replace('if __name__ == "__main__":\n    cli_main(sys.argv)', '')
        p=self.base/'bot-test.py';p.write_text(code)
        proc=subprocess.run([sys.executable,'-c',
            'import runpy,sys; a=runpy.run_path(sys.argv[1]); print(sorted(a["ALLOWED_USERS"]),a["RULESET_TAG"],a["TOKEN"]=="123:synthetic_test_token")',
            str(p)],capture_output=True,text=True,timeout=10)
        self.assertEqual(proc.returncode,0,proc.stderr)
        self.assertIn('[123, 456] test True',proc.stdout)

class StaticTests(unittest.TestCase):
    def test_no_router_values_in_sources(self):
        for name in ('vpn-optimize.sh','vpn-watchdog.sh','vpn-delay-scan.sh'):
            text=(SOURCES/'bin'/name).read_text()
            self.assertNotRegex(text,r'(?m)^SUB_ID=|^SELECTOR=|^AWGM=http|^CLASH=http|^PROXY=http')
        text=(SOURCES/'etc/telegram-vpn-bot/bot.py').read_text()
        self.assertNotIn('TOKEN_FILE',text)
        self.assertNotIn('USERS_FILE',text)
        self.assertNotRegex(text,r'(?m)^SUB_ID = "[0-9]|^RULESET_TAG = "custom-')
    def test_syntax(self):
        shell = ['busybox','sh'] if shutil.which('busybox') else ['/bin/sh']
        paths=list((SOURCES/'bin').glob('*.sh'))+list((SOURCES/'etc/init.d').iterdir())
        for path in paths:
            proc=subprocess.run(shell+['-n',str(path)],capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr)
        for path in SOURCES.rglob('*.py'):
            compile(path.read_text(),str(path),'exec')

if __name__ == '__main__':
    unittest.main(verbosity=2)
