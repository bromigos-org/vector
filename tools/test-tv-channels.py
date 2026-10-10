#!/usr/bin/env python3
"""VECTOR's TV tools with a sandbox config and the actual shared bromigOS library.

python tools/test-tv-channels.py --bromigos /path/to/bromigOS
No real config, live socket, media server or transcripts are used.
"""
import argparse
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

p=argparse.ArgumentParser();p.add_argument('--bromigos',required=True)
a=p.parse_args();sys.path.insert(0,os.path.join(a.bromigos,'lib'))
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME=tempfile.TemporaryDirectory(prefix='vector-tv-test-')
os.environ.update(HOME=HOME.name,XDG_CONFIG_HOME=HOME.name+'/.config',XDG_STATE_HOME=HOME.name+'/.state',
                  XDG_CACHE_HOME=HOME.name+'/.cache',XDG_RUNTIME_DIR=HOME.name,
                  BROMIGOS_LIVE_CONFIG=HOME.name+'/config.toml')
# Always an empty private overlay, regardless of the machine's installed libraries.
sys.path.insert(0,HOME.name)
with open(HOME.name+'/bromigos_private.py','w') as f:
    f.write("def data(): return {}\ndef present(): return False\ndef get(k, default=None): return default\n"
            "def url(n, path='', default=''): return default\ndef vault(n, default=''): return default\n"
            "def vault_root(default=''): return default\ndef lan_domain(default=''): return default\n"
            "def path(n, default=''): return default\nPATH=''\n")
import bromigos_tvchannels as C
from holo.vector import tools,tv,guard


class Tools(unittest.TestCase):
    def setUp(self):
        with open(os.environ['BROMIGOS_LIVE_CONFIG'],'w') as f:
            f.write('# sandbox\n[[tv.channel]]\nname="Night Block" # keep\nshows=["A"]\n')
        tools.LAST_USER_TEXT['text']=''
        for patch in (mock.patch.object(C,'control',return_value=None),mock.patch.object(C.TV,'state',return_value={}),
                      mock.patch.object(C.TV,'cached_items',return_value=None),
                      mock.patch.object(tools,'_switched_off',return_value=None)):
            patch.start();self.addCleanup(patch.stop)

    def call(self,tool_name,**args):
        text,_=tools.call(tool_name,args)
        doc=json.loads(text)
        self.assertNotIn("error",doc,doc);self.assertNotIn("refused",doc,doc)
        return doc

    def test_shared_library_mutations_and_read_are_audited(self):
        self.call('tv_channel_add_item',channel='Night Block',title='B')
        self.call('tv_channel_remove_item',channel='Night Block',title='A')
        self.call('tv_channel_create',name='Films',library='Movies')
        doc=self.call('tv_channels')
        self.assertEqual(doc['channels'][0]['table']['shows'],['B'])
        self.assertEqual(doc['channels'][1]['kind'],'library')
        with open(tools.AUDIT) as f: records=[json.loads(line) for line in f]
        self.assertTrue(all(any(r['tool']==name and r['ok'] for r in records) for name in
            ('tv_channel_add_item','tv_channel_remove_item','tv_channel_create','tv_channels')))
        self.assertIn('# keep',C.Store().load().text)

    def test_delete_requires_latest_request_naming_this_channel(self):
        for text in ('', 'Delete Films', 'Do not delete Night Block','What is in Night Block?',
                     'Remove A from Night Block', 'Delete Films but keep Night Block',
                     "Delete Night Block's pick"):
            tools.LAST_USER_TEXT['text']=text
            with self.assertRaises(PermissionError): tv.tv_channel_delete('Night Block',confirm=True)
        tools.LAST_USER_TEXT['text']='Delete channel Night Block'
        self.assertIn('Removed',tv.tv_channel_delete('Night Block',confirm=True)['message'])
        self.assertEqual(C.Store().load().channels(),[])

    def test_legacy_library_name_can_be_used_in_an_explicit_delete(self):
        store=C.Store();store.edit(lambda doc:doc.add({'library':'Albums'}))
        tools.LAST_USER_TEXT['text']='Delete channel Albums'
        self.assertEqual(tv.tv_channel_delete('Albums',confirm=True)['name'],'Albums')
        self.assertEqual(len(store.load().channels()),1)

    def test_tune_uses_shared_socket_and_does_not_restart(self):
        with mock.patch.object(C,'control',return_value='tuned') as ctl:
            res=tv.tv_tune('Night Block','scope')
            self.assertEqual(res['screen'],'wave')
            self.assertIn(mock.call('tv set 2 --screen wave'),ctl.call_args_list)

    def test_phone_exports_only_read_and_picks(self):
        import ast
        src=os.path.join(os.path.dirname(os.path.dirname(__file__)),'holo/vector/mcp_server.py')
        with open(src) as f: tree=ast.parse(f.read())
        values={n.targets[0].id:ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
                and isinstance(n.targets[0],ast.Name) and n.targets[0].id in ('READ_ONLY','ACT','DESKTOP_ONLY')}
        self.assertIn('tv_channels',values['READ_ONLY'])
        for name in ('tv_channel_add_item','tv_channel_remove_item'): self.assertIn(name,values['ACT'])
        for name in ('tv_channel_create','tv_tune','tv_channel_delete','tv_play','tv_remote'):
            self.assertIn(name,values['DESKTOP_ONLY']);self.assertNotIn(name,values['ACT'])

    def test_play_and_remote_are_audited_and_not_on_phone(self):
        import bromigos_tvplay as P
        import bromigos_tvremote as R
        with (mock.patch.object(P,'resolve',return_value=dict(id='episode5',title='Episode 5',server='library',kind='episode')),
              mock.patch.object(P,'control',return_value=json.dumps(dict(ok=True,message='Playing'))) as ctl):
            self.call('tv_play',screen='big',title='Space Radio S01E05',resume=True)
            payload=json.loads(ctl.call_args.args[0].partition('play-json ')[2])
            self.assertEqual(payload['item']['id'],'episode5');self.assertTrue(payload['resume'])
        with mock.patch.object(R,'control',return_value='big: volume 65') as ctl:
            self.call('tv_remote',action='volume-up',screen='scope')
            ctl.assert_called_with('tv remote volume-up --screen wave')
        self.assertNotIn('tv_play',tools.WHILE_OFF);self.assertNotIn('tv_remote',tools.WHILE_OFF)
        with open(tools.AUDIT) as f: records=[json.loads(line) for line in f]
        self.assertTrue(all(any(r['tool']==name and r['ok'] for r in records) for name in ('tv_play','tv_remote')))

    def test_terminal_does_not_bypass_whole_channel_delete(self):
        self.assertTrue(guard._lab_switch(['bromigos-live','tv','channel','remove','Night Block','--yes']))
        self.assertIsNone(guard._lab_switch(['bromigos','lab','media','channel','add','A','--to','Night Block']))


if __name__=='__main__':
    unittest.main(argv=[sys.argv[0]])
